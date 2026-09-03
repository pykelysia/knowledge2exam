"""规划 agent（planner）。"""

from __future__ import annotations

import json
import logging
import math
import uuid
from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.compressor import compress_text
from app.agents.llm import llm_client
from app.agents.prompts import load_prompt
from app.config import settings
from app.core.debug_log import log_step
from app.models.plan import PlanItem
from app.orchestration.events import EventBus
from app.orchestration.state_machine import Stage

logger = logging.getLogger(__name__)


# 单位耗时基准（分钟/题）
_UNIT_TIME = {
    "choice": 1.5,
    "blank": 1.5,
    "short_answer": 6.0,
}

_DEFAULT_PROPORTIONS = {
    "choice": 0.20,
    "blank": 0.20,
    "short_answer": 0.60,
}

# 中文 -> 英文枚举映射（兼容 LLM 输出中文的情况）
_QUESTION_TYPE_ALIASES: dict[str, str] = {
    "选择题": "choice",
    "单选题": "choice",
    "多选题": "choice",
    "填空题": "blank",
    "简答题": "short_answer",
    "论述题": "short_answer",
    "计算题": "short_answer",
}


def _normalize_question_type(raw: str) -> str:
    """将 LLM 返回的 question_type 标准化为英文枚举值。"""
    key = raw.strip().lower()
    if key in {"choice", "blank", "short_answer"}:
        return key
    # 中文映射
    return _QUESTION_TYPE_ALIASES.get(raw.strip(), "choice")

# 相对变动容忍度
_RELATIVE_TOLERANCE = 0.10

# 时间区间下限比例
_TIME_LOWER_RATIO = 0.85


class PlannerResult:
    def __init__(self, plan_items: list[dict[str, Any]]) -> None:
        self.plan_items = plan_items


def _largest_remainder_allocation(
    total: int,
    proportions: dict[str, float],
    question_types: list[str],
) -> dict[str, int]:
    """最大余额法分配整数题数，保证 sum == total。

    1. 取 floor(total * p_i) 作为基础题数
    2. 按小数部分从大到小排序，依次 +1 直到总数等于 total
    3. 保护规则：p_i > 0 的题型至少 1 题
    """
    if total <= 0:
        return {qt: 0 for qt in question_types}

    # 基础题数
    floors: dict[str, int] = {}
    remainders: dict[str, float] = {}
    for qt in question_types:
        p = proportions.get(qt, 0.0)
        floors[qt] = max(1 if p > 0 else 0, math.floor(total * p))
        remainders[qt] = total * p - floors[qt]

    allocated = sum(floors.values())

    # 补齐差额
    remainder_slots = total - allocated
    if remainder_slots > 0:
        # 按小数部分降序排序
        sorted_qts = sorted(
            question_types,
            key=lambda qt: remainders.get(qt, 0),
            reverse=True,
        )
        for i in range(remainder_slots):
            qt = sorted_qts[i % len(sorted_qts)]
            floors[qt] += 1

    # 确保总和正确
    current_total = sum(floors.values())
    if current_total != total:
        # 微调：从最多题型借/还
        diff = total - current_total
        sorted_by_count = sorted(question_types, key=lambda qt: floors[qt], reverse=True)
        for i in range(abs(diff)):
            qt = sorted_by_count[i % len(sorted_by_count)]
            floors[qt] += 1 if diff > 0 else -1

    return floors


def _validate_time_range(
    counts: dict[str, int],
    duration_minutes: int,
) -> tuple[bool, float]:
    """校验总耗时是否落在 [0.85×duration, duration] 区间。"""
    total_time = sum(
        counts.get(qt, 0) * _UNIT_TIME.get(qt, 0) for qt in counts
    )
    lower = _TIME_LOWER_RATIO * duration_minutes
    upper = duration_minutes
    return lower <= total_time <= upper, total_time


def _adjust_counts_to_time_range(
    counts: dict[str, int],
    duration_minutes: int,
    question_types: list[str],
) -> dict[str, int]:
    """调整题数使总耗时落在时间区间内。"""
    lower = _TIME_LOWER_RATIO * duration_minutes
    upper = duration_minutes

    for _ in range(100):  # 最多迭代 100 次
        in_range, total_time = _validate_time_range(counts, duration_minutes)
        if in_range:
            break

        if total_time > upper:
            # 减少耗时最多的题型（优先减少简答题）
            for qt in sorted(question_types, key=lambda q: _UNIT_TIME.get(q, 0), reverse=True):
                if counts.get(qt, 0) > 1:
                    counts[qt] -= 1
                    break
            else:
                break
        else:
            # 增加耗时最少的题型
            for qt in sorted(question_types, key=lambda q: _UNIT_TIME.get(q, 0)):
                counts[qt] += 1
                break

    return counts


def _validate_relative_constraints(
    counts: dict[str, int],
    proportions: dict[str, float],
    question_types: list[str],
) -> tuple[bool, dict[str, Any]]:
    """校验 ±10% 相对变动约束。"""
    total = sum(counts.values())
    if total == 0:
        return True, {}

    violations = []
    details = {}
    for qt in question_types:
        actual_ratio = counts.get(qt, 0) / total
        ref_p = proportions.get(qt, 0.0)
        lower_bound = ref_p * (1 - _RELATIVE_TOLERANCE)
        upper_bound = ref_p * (1 + _RELATIVE_TOLERANCE)

        details[qt] = {
            "count": counts.get(qt, 0),
            "actual_ratio": actual_ratio,
            "reference_proportion": ref_p,
            "lower_bound": lower_bound,
            "upper_bound": upper_bound,
            "in_range": lower_bound <= actual_ratio <= upper_bound,
        }

        if not (lower_bound <= actual_ratio <= upper_bound):
            violations.append(qt)

    return len(violations) == 0, details


def _compute_final_distribution(
    duration_minutes: int,
    proportions: dict[str, float],
    question_types: list[str],
) -> tuple[dict[str, int], dict[str, Any]]:
    """计算最终题型分布（题量推导 + 最大余额法 + 约束校验）。"""
    # 1. 先按比例估算总题数 N
    # 设总题数为 N，解方程：sum(N * p_i * t_i) = duration
    # 其中 t_i 是单位耗时
    total_time_per_n = sum(
        proportions.get(qt, 0.0) * _UNIT_TIME.get(qt, 0) for qt in question_types
    )
    if total_time_per_n > 0:
        estimated_n = duration_minutes / total_time_per_n
    else:
        estimated_n = 10.0

    # 限制在时间区间对应的 N 范围
    lower_time = _TIME_LOWER_RATIO * duration_minutes
    upper_time = duration_minutes

    def time_for_n(n: float) -> float:
        return sum(
            (n * proportions.get(qt, 0.0)) * _UNIT_TIME.get(qt, 0)
            for qt in question_types
        )

    # 二分查找合适的 N
    low_n = max(1, math.floor(lower_time / total_time_per_n)) if total_time_per_n > 0 else 1
    high_n = max(low_n, math.ceil(upper_time / total_time_per_n)) if total_time_per_n > 0 else 50

    # 在 [low_n, high_n] 中找最优 N
    best_n = low_n
    best_diff = float("inf")
    for n in range(max(1, low_n), high_n + 1):
        t = time_for_n(n)
        if lower_time <= t <= upper_time:
            diff = abs(t - duration_minutes * 0.925)  # 目标：区间中值
            if diff < best_diff:
                best_diff = diff
                best_n = n

    # 用最大余额法分配
    counts = _largest_remainder_allocation(best_n, proportions, question_types)

    # 校验并调整时间区间
    counts = _adjust_counts_to_time_range(counts, duration_minutes, question_types)

    # 校验相对变动约束
    in_range, details = _validate_relative_constraints(counts, proportions, question_types)

    if not in_range:
        # 微调：从偏差最大的题型借题给偏差方向相反的题型
        total = sum(counts.values())
        for qt in question_types:
            actual_ratio = counts.get(qt, 0) / total if total > 0 else 0
            ref_p = proportions.get(qt, 0.0)
            lower = ref_p * (1 - _RELATIVE_TOLERANCE)
            upper = ref_p * (1 + _RELATIVE_TOLERANCE)
            if actual_ratio > upper and counts[qt] > 1:
                # 找到偏差最小的题型（低于下限）
                for qt2 in question_types:
                    if qt2 != qt:
                        actual2 = counts.get(qt2, 0) / total if total > 0 else 0
                        if actual2 < lower:
                            counts[qt] -= 1
                            counts[qt2] += 1
                            break

    return counts, details


def _normalize_proportions(
    raw_proportions: dict[str, float],
    question_types: list[str],
) -> dict[str, float]:
    """归一化占比，确保总和为 1。"""
    total = sum(raw_proportions.get(qt, 0.0) for qt in question_types)
    if total == 0:
        return _DEFAULT_PROPORTIONS.copy()
    return {qt: raw_proportions.get(qt, 0.0) / total for qt in question_types}


def _derive_proportions_from_past_papers(
    past_papers: list[dict[str, Any]],
    question_types: list[str],
) -> dict[str, float]:
    """从多份往期试卷推导题型占比（取平均值）。"""
    if not past_papers:
        return _DEFAULT_PROPORTIONS.copy()

    type_sums: dict[str, float] = {qt: 0.0 for qt in question_types}
    paper_count = 0

    for paper in past_papers:
        distribution = paper.get("distribution", {})
        paper_total = sum(distribution.get(qt, 0) for qt in question_types)
        if paper_total > 0:
            paper_count += 1
            for qt in question_types:
                type_sums[qt] += distribution.get(qt, 0) / paper_total

    if paper_count == 0:
        return _DEFAULT_PROPORTIONS.copy()

    return {qt: type_sums[qt] / paper_count for qt in question_types}


def _check_direction_overlap(
    plan_items_raw: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """检查是否存在三道及以上考察同一知识点相同/相似方向的题目。

    返回重叠警告列表，每个元素包含：
    - knowledge_point: 知识点
    - directions: 考察方向列表
    - count: 命中数量
    """
    # 按知识点分组
    kp_directions: dict[str, list[str]] = {}
    for item in plan_items_raw:
        kp = item.get("knowledge_point", "").strip()
        direction = item.get("exam_direction", "").strip()
        if kp:
            kp_directions.setdefault(kp, []).append(direction)

    warnings = []
    for kp, directions in kp_directions.items():
        if len(directions) >= 3:
            # 检查是否存在相同/相似方向
            direction_counts = Counter(d.lower().strip() for d in directions)
            for direction, count in direction_counts.items():
                if count >= 3:
                    warnings.append({
                        "knowledge_point": kp,
                        "direction": direction,
                        "count": count,
                        "message": f"知识点「{kp}」有 {count} 道题考察相同/相似方向「{direction}」",
                    })

    return warnings


async def run_planner(
    db: AsyncSession,
    job_id: uuid.UUID,
    bus: EventBus,
    context: dict[str, Any],
) -> list[PlanItem]:
    """执行规划 agent，返回 PlanItem ORM 对象列表。"""

    # 1. 从 context 中提取多份往期试卷信息
    past_papers_data = context.get("past_papers_data", [])
    past_papers_full_text = context.get("past_papers_full_text_or_none") or "无"
    keypoint_list = context.get("keypoint_list_or_none") or "无"
    extra_requirement = context.get("extra_requirement_or_none") or "无"
    shared_library_summary = context.get("shared_library_summary") or "无共享库内容"

    # 1.1 对关键点和额外要求做长度检查与压缩
    compressor_model = getattr(settings, "compressor_model", "gpt-4o-mini")

    if len(keypoint_list) > settings.max_keypoint_list_chars:
        logger.info(
            "keypoint_list 超长（%d 字符，阈值 %d），触发压缩",
            len(keypoint_list),
            settings.max_keypoint_list_chars,
        )
        try:
            keypoint_list = await compress_text(
                raw_text=keypoint_list,
                target_tokens=max(settings.max_keypoint_list_chars // 4, 500),
                model=compressor_model,
            )
        except Exception as exc:
            logger.warning("压缩 keypoint_list 失败，回退到硬截断: %s", exc)
            keypoint_list = keypoint_list[: settings.max_keypoint_list_chars]

    if len(extra_requirement) > settings.max_extra_requirement_chars:
        logger.info(
            "extra_requirement 超长（%d 字符，阈值 %d），触发压缩",
            len(extra_requirement),
            settings.max_extra_requirement_chars,
        )
        try:
            extra_requirement = await compress_text(
                raw_text=extra_requirement,
                target_tokens=max(settings.max_extra_requirement_chars // 4, 500),
                model=compressor_model,
            )
        except Exception as exc:
            logger.warning("压缩 extra_requirement 失败，回退到硬截断: %s", exc)
            extra_requirement = extra_requirement[: settings.max_extra_requirement_chars]

    # 2. 推导题型占比
    question_types = ["choice", "blank", "short_answer"]

    if past_papers_data:
        # 从多份往期试卷推导占比
        proportions = _derive_proportions_from_past_papers(
            past_papers_data, question_types
        )
    else:
        # 使用默认模板
        proportions = _DEFAULT_PROPORTIONS.copy()

    proportions = _normalize_proportions(proportions, question_types)

    # 3. 计算最终题型分布
    duration_minutes = context.get("duration_minutes", 100)
    counts, validation_details = _compute_final_distribution(
        duration_minutes, proportions, question_types
    )

    # 4. 构造提示词，加入推导后的题型分布约束
    template = load_prompt("planner")

    # 在提示词中告知 LLM 推荐的题型分布
    distribution_hint = (
        f"\n\n【推荐题型分布】\n"
        f"选择题: {counts.get('choice', 0)} 题\n"
        f"填空题: {counts.get('blank', 0)} 题\n"
        f"简答题: {counts.get('short_answer', 0)} 题\n"
        f"总题数: {sum(counts.values())} 题\n"
        f"预计总耗时: {sum(counts.get(qt, 0) * _UNIT_TIME.get(qt, 0) for qt in question_types):.1f} 分钟 "
        f"（区间 [{_TIME_LOWER_RATIO * duration_minutes:.1f}, {duration_minutes}]）\n"
        f"请尽量遵循此分布，若有充分理由可微调，但总耗时必须落在上述区间内。"
    )

    # 换题模式：在 prompt 中追加换题请求信息
    replacement_hint = ""
    if context.get("mode") == "replacement" and context.get("replacement_requests"):
        replacement_hint = "\n\n【换题请求】\n"
        for req in context["replacement_requests"]:
            replacement_hint += (
                f"- 第 {req['seq']} 题：{req['question_type']}，"
                f"知识点「{req['knowledge_point']}」，考察方向「{req['exam_direction']}」\n"
                f"  失败原因：{req['failure_reason']}，已重试 {req['retry_count']} 次\n"
            )
        replacement_hint += "\n当前试卷已包含的题目（请避免重复）：\n"
        for item in context.get("current_plan_items", []):
            replacement_hint += (
                f"- 第 {item['seq']} 题：{item['question_type']}，"
                f"知识点「{item['knowledge_point']}」，考察方向「{item['exam_direction']}」\n"
            )
        replacement_hint += (
            "\n要求：\n"
            "1. 为每个换题请求生成新的 plan_item，保持相同的 seq 和 question_type\n"
            "2. 建议更换知识点或考察方向，避免与当前试卷中的题目重复\n"
            "3. 保持总题数和题型分布不变\n"
            "4. 输出仅包含需要更换的 plan_item 列表\n"
        )

    prompt = template.format(
        duration_minutes=duration_minutes,
        past_papers_full_text_or_none=past_papers_full_text,
        keypoint_list_or_none=keypoint_list,
        extra_requirement_or_none=extra_requirement,
        shared_library_summary=shared_library_summary,
    ) + distribution_hint + replacement_hint

    # 5. 调用 LLM
    import time

    max_retries = 3
    plan_items_raw: list[dict[str, Any]] = []

    for attempt in range(max_retries):
        llm_t0 = time.perf_counter()
        response = await llm_client.chat(
            model=context.get("planner_model", "gpt-4o"),
            messages=[
                {"role": "system", "content": "你是一位专业的试卷规划专家，擅长根据教学材料设计合理的试卷蓝图。请始终输出 JSON 数组格式。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
        )
        llm_elapsed = (time.perf_counter() - llm_t0) * 1000

        await log_step(
            job_id=str(job_id),
            name="llm_planner",
            step="llm",
            stage=Stage.planning.value,
            input={
                "model": context.get("planner_model", "gpt-4o"),
                "prompt_chars": len(prompt),
                "attempt": attempt + 1,
            },
            output={
                "plan_items_count": len(plan_items_raw),
                "elapsed_ms": round(llm_elapsed, 1),
            },
            elapsed_ms=llm_elapsed,
        )

        # 解析 LLM 返回的 plan_item 列表
        choice = response.choices[0]
        plan_items_raw = []

        # 优先从文本解析 JSON 数组
        content = choice.message.content or ""
        try:
            start = content.find("[")
            end = content.rfind("]") + 1
            if start >= 0 and end > start:
                plan_items_raw = json.loads(content[start:end])
        except (json.JSONDecodeError, ValueError):
            pass

        # 如果 LLM 使用了 tool_calls，也尝试解析
        if not plan_items_raw and choice.message.tool_calls:
            for tool_call in choice.message.tool_calls:
                try:
                    args = json.loads(tool_call.function.arguments)
                    if isinstance(args, list):
                        plan_items_raw = args
                        break
                except (json.JSONDecodeError, ValueError):
                    continue

        if not plan_items_raw:
            if attempt < max_retries - 1:
                continue
            raise RuntimeError("规划 agent 未产出任何 plan_item")

        # 6. 考察方向去重自查
        overlap_warnings = _check_direction_overlap(plan_items_raw)
        if overlap_warnings:
            # 构造修正提示
            overlap_msg = "\n\n【考察方向去重警告】\n"
            for w in overlap_warnings:
                overlap_msg += (
                    f"- {w['message']}。"
                    f"请保留考察角度最典型的两道，其余替换为该知识点的其他考察方向，"
                    f"或替换为其他知识点。\n"
                )
            overlap_msg += "请根据以上警告修正 plan_item[] 并重新输出。"
            prompt = prompt + overlap_msg

            if attempt < max_retries - 1:
                continue

        # 通过校验，跳出重试循环
        break

    if not plan_items_raw:
        raise RuntimeError("规划 agent 经多次重试仍未产出有效 plan_item")

    # 7. 补全字段并落盘
    plan_items: list[PlanItem] = []
    seq = 0
    for item in plan_items_raw:
        seq += 1
        plan = PlanItem(
            job_id=job_id,
            seq=seq,
            question_type=_normalize_question_type(item.get("question_type", "choice")),
            knowledge_point=item.get("knowledge_point", ""),
            exam_direction=item.get("exam_direction", ""),
            difficulty=item.get("difficulty", "medium"),
            reference_source=item.get("reference_source"),
        )
        db.add(plan)
        plan_items.append(plan)

    await db.flush()

    # 8. 发送 plan_ready 事件
    distribution: dict[str, int] = {}
    for p in plan_items:
        distribution[p.question_type] = distribution.get(p.question_type, 0) + 1

    # 计算实际总耗时
    actual_total_time = sum(
        distribution.get(qt, 0) * _UNIT_TIME.get(qt, 0) for qt in question_types
    )

    await log_step(
        job_id=str(job_id),
        name="run_planner",
        step="result",
        stage=Stage.planning.value,
        input={"plan_items_count": len(plan_items_raw)},
        output={
            "plan_items": len(plan_items),
            "distribution": distribution,
            "estimated_time": round(actual_total_time, 1),
        },
    )

    await bus.emit(
        job_id,
        "plan_ready",
        {
            "total": len(plan_items),
            "distribution": distribution,
            "reference_used": context.get("reference_used", "default_template"),
            "duration_minutes": duration_minutes,
            "estimated_time": actual_total_time,
            "time_range": {
                "lower": _TIME_LOWER_RATIO * duration_minutes,
                "upper": duration_minutes,
            },
            "proportions": proportions,
            "validation": validation_details,
        },
        stage=Stage.planning.value,
    )

    return plan_items
