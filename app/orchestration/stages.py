"""编排层入口。

真实实现：文件式 ReAct agent（见 app/agents/agent.py）。
解析已前移到上传阶段（app/ingestion/pipeline.py）：预处理只把现成的
解析产物整理进 agent 工作区（agent/materials/），agent 规划蓝图并把
整卷 Markdown 直接书写到 output/paper.md，最后经 render_paper 工具渲染 PDF
（失败自动降级交付 md）；编排层在 agent 返回后仅负责入库蓝图
（persist_exam_result）与提交渲染产物（md_key/pdf_key/状态）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import AgentHooks, run_exam_agent
from app.agents.workspace import Workspace
from app.config import settings
from app.core.db import AsyncSessionLocal
from app.core.debug_log import log_error, log_step
from app.core.enums import FILE_SOURCE_TYPES, TEXT_SOURCE_TYPES, SourceType
from app.core.exceptions import ErrorCode
from app.core.storage import storage
from app.models.job import Job, JobUpload
from app.models.plan import PlanItem
from app.models.upload import Upload
from app.orchestration.events import EventBus
from app.orchestration.integration import persist_exam_result
from app.orchestration.preference import list_course_preferences
from app.orchestration.state_machine import TERMINAL_STATUSES, JobStatus, Stage

logger = logging.getLogger(__name__)


async def _get_job_upload_ids(db: AsyncSession, job_id: uuid.UUID) -> list[uuid.UUID]:
    """从 job_upload 中间表获取某个 job 关联的 upload_id 列表。"""
    result = await db.scalars(select(JobUpload.upload_id).where(JobUpload.job_id == job_id))
    return list(result.all())


async def _check_cancelled(db: AsyncSession, job_id: uuid.UUID, bus: EventBus) -> bool:
    """检查 job 是否已被取消，如果是则发送 done 事件并返回 True。"""
    job = await db.get(Job, job_id)
    if job and job.status == JobStatus.cancelled.value:
        await bus.emit_and_close(
            job_id,
            "done",
            {"status": JobStatus.cancelled.value},
        )
        await db.commit()
        return True
    return False


async def run_pipeline(job_id: uuid.UUID) -> None:
    """执行生成 pipeline。"""
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is None:
            return

        bus = EventBus(db)

        has_llm = bool(
            getattr(settings, "llm_api_key", None) and getattr(settings, "llm_base_url", None)
        )

        if not has_llm:
            raise RuntimeError(
                "LLM 配置缺失（llm_api_key / llm_base_url），无法执行真实 pipeline。"
            )

        try:
            await _run_real_pipeline(db, job, bus)
        except Exception as exc:
            await log_error(
                job_id=str(job.id),
                exc=exc,
                stage=Stage.preprocessing.value,
                context={"has_llm": has_llm},
            )
            raise


class JobStateConflict(RuntimeError):
    """任务状态已被并发修改（如取消/失败兜底），管线停止推进。"""


async def _advance_status(db: AsyncSession, job: Job, target: JobStatus) -> None:
    """条件推进任务状态：仅当 DB 中当前状态与 ORM 快照一致时更新。

    防止 cancel/fail 兜底在窗口期写入终态后，被管线用过期对象无条件覆盖。
    """
    result = await db.execute(
        update(Job)
        .where(Job.id == job.id, Job.status == job.status)
        .values(status=target.value)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        raise JobStateConflict(f"job 状态已被并发修改（期望 {job.status}）")
    job.status = target.value


async def _run_pipeline_with_cancellation(job_id: uuid.UUID) -> None:
    """包装 run_pipeline，处理任务注册、取消与意外失败的终态收敛。"""
    try:
        await run_pipeline(job_id)
    except JobStateConflict:
        # 状态已被并发推进到终态（取消等）：安静收尾，不覆盖、不告警
        logger.info("job=%s 状态已被并发修改，管线停止推进", job_id)
        return
    except asyncio.CancelledError:
        # 任务被取消：条件更新数据库状态并通知订阅者（终态已被写入则跳过）
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.status.not_in([s.value for s in TERMINAL_STATUSES]),
                )
                .values(status=JobStatus.cancelled.value, finished_at=datetime.now(UTC))
                .execution_options(synchronize_session=False)
            )
            await db.commit()
            if result.rowcount:
                bus = EventBus(db)
                await bus.emit_and_close(
                    job_id,
                    "done",
                    {"status": JobStatus.cancelled.value},
                )
                await db.commit()
        raise
    except Exception as exc:
        # 意外异常兜底：任务定格为 failed 并推送终态事件，
        # 避免任务永远停留在非终态、前端无限轮询。
        await log_error(
            job_id=str(job_id),
            exc=exc,
            stage=Stage.generating.value,
            context={},
        )
        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    update(Job)
                    .where(
                        Job.id == job_id,
                        Job.status.not_in([s.value for s in TERMINAL_STATUSES]),
                    )
                    .values(
                        status=JobStatus.failed.value,
                        error_code=ErrorCode.PIPELINE_FAILED.value,
                        finished_at=datetime.now(UTC),
                    )
                    .execution_options(synchronize_session=False)
                )
                await db.commit()
                if result.rowcount:
                    bus = EventBus(db)
                    await bus.emit(
                        job_id,
                        "error",
                        {
                            "error_code": ErrorCode.PIPELINE_FAILED.value,
                            "message": str(exc) or type(exc).__name__,
                        },
                    )
                    await bus.emit_and_close(
                        job_id,
                        "done",
                        {"status": JobStatus.failed.value},
                    )
                    await db.commit()
        except Exception:
            logger.exception("失败兜底处理时再次出错（job=%s）", job_id)
        raise


async def _run_real_pipeline(db: AsyncSession, job: Job, bus: EventBus) -> None:
    """真实 pipeline：preprocessing -> generating(agent) -> rendering。"""
    import time

    if await _check_cancelled(db, job.id, bus):
        return

    await log_step(
        job_id=str(job.id),
        name="_run_real_pipeline",
        stage=Stage.preprocessing.value,
        input={"upload_count": len(await _get_job_upload_ids(db, job.id))},
    )

    # ---------- preprocessing ----------
    if await _check_cancelled(db, job.id, bus):
        return
    await _advance_status(db, job, JobStatus.preprocessing)
    await bus.emit(
        job.id,
        "stage_changed",
        {"stage": Stage.preprocessing.value, "previous": JobStatus.pending.value},
        stage=Stage.preprocessing.value,
    )
    await db.commit()

    # 真实解析、切块、嵌入
    pre_t0 = time.perf_counter()
    context = await _preprocess(db, job, bus)
    pre_elapsed = (time.perf_counter() - pre_t0) * 1000

    await log_step(
        job_id=str(job.id),
        name="_preprocess",
        stage=Stage.preprocessing.value,
        input={"upload_count": len(await _get_job_upload_ids(db, job.id))},
        output={"materials": context.get("materials", [])},
        elapsed_ms=pre_elapsed,
    )

    # ---------- generating（agent 工作：进入 agent 调用即切换） ----------
    if await _check_cancelled(db, job.id, bus):
        return
    await _advance_status(db, job, JobStatus.generating)
    await bus.emit(
        job.id,
        "stage_changed",
        {"stage": Stage.generating.value, "previous": Stage.preprocessing.value},
        stage=Stage.generating.value,
    )
    await db.commit()

    main_agent_context = {
        "duration_minutes": job.duration_minutes,
        "need_explanation": job.need_explanation,
        "user_id": job.user_id,
        "school_id": job.school_id,
        "course_id": job.course_id,
        "upload_ids": await _get_job_upload_ids(db, job.id),
    }

    # 本校本课程沉淀的出题偏好：聚合为伪技能注入技能索引（【必读】）
    if job.school_id is not None and job.course_id is not None:
        preference_rows = await list_course_preferences(db, job.school_id, job.course_id)
        main_agent_context["course_preferences"] = [row.content for row in preference_rows]

    async def on_plan_ready(todos: list) -> None:  # noqa: ANN001
        distribution: dict[str, int] = {}
        for t in todos:
            distribution[t.question_type] = distribution.get(t.question_type, 0) + 1
        await bus.emit(
            job.id,
            "plan_ready",
            {
                "total": len(todos),
                "distribution": distribution,
                "reference_used": "agent_planning",
                "duration_minutes": job.duration_minutes,
            },
            stage=Stage.generating.value,
        )

    async def on_progress(completed: int, total: int) -> None:
        await bus.emit(
            job.id,
            "progress",
            {
                "completed": completed,
                "total": total,
            },
            stage=Stage.generating.value,
        )

    async def on_warning(message: str) -> None:
        await bus.emit(
            job.id,
            "warning",
            {"code": ErrorCode.AGENT_WARNING, "message": message},
            stage=Stage.generating.value,
        )

    # 渲染阶段的 stage_changed 事件：agent 的 render_paper 首次执行时发出，
    # 重试不重复发；agent 未调用渲染工具时由渲染段补发。
    render_stage_emitted = False

    async def on_render_start() -> None:
        nonlocal render_stage_emitted
        if not render_stage_emitted:
            render_stage_emitted = True
            await bus.emit(
                job.id,
                "stage_changed",
                {"stage": Stage.rendering.value, "previous": Stage.generating.value},
                stage=Stage.rendering.value,
            )

    hooks = AgentHooks(
        on_plan_ready=on_plan_ready,
        on_progress=on_progress,
        on_warning=on_warning,
        on_render_start=on_render_start,
    )

    main_result = await run_exam_agent(job.id, main_agent_context, hooks)

    # agent 产物入库
    integration_result = await persist_exam_result(db, job.id, main_result)

    # 从数据库加载 plan_items 以计算分布
    all_plan_items = (
        await db.scalars(
            select(PlanItem).where(
                PlanItem.job_id == job.id,
                PlanItem.superseded_by.is_(None),
            )
        )
    ).all()
    distribution: dict[str, int] = {}
    for p in all_plan_items:
        distribution[p.question_type] = distribution.get(p.question_type, 0) + 1

    await log_step(
        job_id=str(job.id),
        name="run_exam_agent",
        stage=Stage.generating.value,
        input={"context_keys": list(main_agent_context.keys())},
        output={
            "plan_items": len(main_result.plan_items),
            "abandoned": len(main_result.abandoned_seqs),
            "completed_normally": main_result.completed_normally,
            "integration": integration_result,
            "distribution": distribution,
        },
    )

    job.planned_total = integration_result.get("total_plan_items", 0)
    await db.commit()

    # ---------- rendering（提交渲染产物；渲染本体已在 agent 的 render_paper 工具内完成） ----------
    if await _check_cancelled(db, job.id, bus):
        return
    if not render_stage_emitted:
        # 模型未调用渲染工具（兜底在 ainvoke 之后执行）：补发事件保证阶段序列完整
        await bus.emit(
            job.id,
            "stage_changed",
            {"stage": Stage.rendering.value, "previous": Stage.generating.value},
            stage=Stage.rendering.value,
        )
    await _advance_status(db, job, JobStatus.rendering)
    await db.commit()

    md_key = f"jobs/{job.id}/output/paper.md"
    pdf_key = f"jobs/{job.id}/output/paper.pdf"
    if main_result.render_status == "succeeded":
        job.md_key = md_key
        job.pdf_key = pdf_key
        await _advance_status(db, job, JobStatus.completed)
    else:
        # md_only：PDF 渲染失败但 paper.md 已生成；not_attempted 为防御式兜底
        job.md_key = md_key if main_result.render_status == "md_only" else None
        job.pdf_key = None
        await _advance_status(db, job, JobStatus.partially_completed)
    await db.commit()

    if main_result.render_error:
        await bus.emit(
            job.id,
            "warning",
            {"code": ErrorCode.RENDER_FAILED, "message": main_result.render_error},
            stage=Stage.rendering.value,
        )

    await log_step(
        job_id=str(job.id),
        name="rendering",
        stage=Stage.rendering.value,
        input={"plan_items_count": len(main_result.plan_items)},
        output={
            "render_status": main_result.render_status,
            "render_error": main_result.render_error,
        },
    )

    await bus.emit_and_close(
        job.id,
        "done",
        {
            "status": job.status,
            "total": job.planned_total or 0,
            "abandoned": len(main_result.abandoned_seqs),
            "md_url": f"/api/v1/jobs/{job.id}/paper.md" if job.md_key else None,
            "pdf_url": f"/api/v1/jobs/{job.id}/paper.pdf" if job.pdf_key else None,
        },
        stage=Stage.rendering.value,
    )
    await db.commit()


# ---------------------------------------------------------------------------
# 预处理：把现成解析产物整理进 agent 工作区
# ---------------------------------------------------------------------------


async def _load_parsed_text(job: Job, bus: EventBus, upload: Upload) -> str | None:
    """读取上传件全文：文本类直接用 raw_text，文件类读解析产物。

    产物缺失或解析失败时发告警并返回 None（该材料不写入工作区，
    agent 基于其余材料继续）。
    """
    if upload.source_type in TEXT_SOURCE_TYPES:
        return upload.raw_text or ""

    if upload.parse_status != "succeeded" or not upload.parsed_key:
        await bus.emit(
            job.id,
            "warning",
            {
                "code": ErrorCode.PARSE_FAILED.value,
                "upload_id": str(upload.id),
                "message": upload.parse_error or "解析产物缺失，材料未写入工作区",
            },
        )
        return None

    try:
        raw = await storage.get(upload.parsed_key)
        return json.loads(raw.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        await bus.emit(
            job.id,
            "warning",
            {
                "code": ErrorCode.PARSE_FAILED.value,
                "upload_id": str(upload.id),
                "message": f"解析产物读取失败：{exc}",
            },
        )
        return None


async def _preprocess(db: AsyncSession, job: Job, bus: EventBus) -> dict[str, Any]:
    """把所选上传件的解析产物整理进 agent 工作区（解析已在上传阶段完成）。"""
    # 1. 加载 uploads（按创建时间排序，保证材料命名与撞名后缀的确定性）
    upload_rows = (
        await db.scalars(
            select(Upload)
            .where(Upload.id.in_(await _get_job_upload_ids(db, job.id)))
            .order_by(Upload.created_at)
        )
    ).all()
    uploads = list(upload_rows)

    past_papers: list[tuple[str, str]] = []
    exclusive_texts: dict[str, list[str]] = {}

    # 2. 按内容类型归置全文材料
    for upload in uploads:
        text = await _load_parsed_text(job, bus, upload)
        if text is None:
            continue
        if upload.source_type == SourceType.past_paper:
            # 往期试卷：全文留存，稍后写入工作区材料
            past_papers.append((upload.filename or f"unnamed_{len(past_papers) + 1}", text))
        elif upload.source_type == SourceType.keypoint_list:
            # 重点清单：全文留存供意图提取与 agent 查阅，不走向量检索
            exclusive_texts.setdefault(upload.source_type.value, []).append(text)
        elif upload.source_type in FILE_SOURCE_TYPES:
            # 可向量化类（book / lecture / note）：检索走 chunk 白名单，不写全文材料
            pass
        else:
            # 文本类（manual_text / extra_requirement）：全文留存
            exclusive_texts.setdefault(upload.source_type.value, []).append(text)

    # 3. 用户材料写入 agent 工作区（materials/）
    workspace = Workspace(storage, f"jobs/{job.id}/agent")
    material_files: list[str] = []
    used_stems: set[str] = set()
    for index, (filename, paper_text) in enumerate(past_papers, start=1):
        stem = _safe_material_stem(filename) or f"past_paper_{index:02d}"
        if stem in used_stems:
            # 同名或清洗后撞名的试卷：追加序号，避免后写覆盖先写
            stem = f"{stem}_{index:02d}"
        used_stems.add(stem)
        rel = f"materials/{stem}.md"
        await workspace.write(rel, paper_text)
        material_files.append(rel)
    _material_from_texts = {
        "keypoint_list": "materials/keypoints.md",
        "extra_requirement": "materials/requirements.md",
        "manual_text": "materials/manual_text.md",
    }
    for source_key, rel in _material_from_texts.items():
        texts = exclusive_texts.get(source_key)
        if texts:
            await workspace.write(rel, _join_material_texts(source_key, texts))
            material_files.append(rel)

    context = {"materials": material_files}

    await db.commit()
    return context


def _safe_material_stem(filename: str) -> str:
    """把上传文件名转成材料文件名可用的词干。"""
    stem = Path(filename).stem
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", stem).strip("_")
    return safe[:60]


def _join_material_texts(source_key: str, texts: list[str]) -> str:
    """拼接全文类材料；重点清单超长时按配置截断（保护 agent 上下文）。"""
    joined = "\n\n".join(texts)
    if source_key == "keypoint_list":
        limit = settings.max_keypoint_list_chars
        if len(joined) > limit:
            joined = joined[:limit] + f"\n\n[内容过长，已截断至 {limit} 字符]"
    return joined
