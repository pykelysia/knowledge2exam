"""Reviewer subagent。

将结果写入 storage/jobs/{job_id}/subagent/review_{seq}.json。
支持两种模式：full（完整审核）和 format（仅格式审核）。
"""

from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from app.agents.llm import llm_client
from app.agents.prompts import load_prompt
from app.agents.subagent_io import write_subagent_result
from app.agents.base import SubAgentResult
from app.config import settings
from app.core.debug_log import log_step

logger = logging.getLogger(__name__)


async def run_reviewer_subagent(
    job_id: uuid.UUID,
    questions: list[dict[str, Any]],
    output_dir: Path,
    review_mode: str,
    model: str | None = None,
) -> SubAgentResult:
    """执行 reviewer subagent，审核题目并写入文件。

    Args:
        job_id: 任务 ID
        questions: 题目列表，每个题目包含 seq, question_type, stem, options/answer 等
        output_dir: 输出目录
        review_mode: "full"（完整审核）或 "format"（仅格式审核）
        model: 可选的模型名称，为空则使用 context 中的 reviewer_model

    Returns:
        SubAgentResult 包含审核统计
    """
    from app.config import get_agent_model

    used_model = model or get_agent_model("reviewer")

    # 构造审查输入
    questions_with_plan = []
    for q in questions:
        plan_item = q.get("plan_item")
        questions_with_plan.append({
            "seq": q["seq"],
            "question_type": q["question_type"],
            "stem": q.get("stem", ""),
            "options": q.get("options"),
            "answer": q.get("answer", ""),
            "sub_questions": q.get("sub_questions"),
            "sub_answers": q.get("sub_answers"),
            "plan_item": plan_item,
        })

    # 根据 review_mode 选择 prompt 模板
    if review_mode == "full":
        template = load_prompt("reviewer_full")
    else:
        template = load_prompt("reviewer_format")

    prompt = template.format(
        questions_with_plan_items=json.dumps(questions_with_plan, ensure_ascii=False, indent=2)
    )

    import time

    llm_t0 = time.perf_counter()
    response = await llm_client.chat(
        model=used_model,
        messages=[
            {"role": "system", "content": "你是一位严格的试卷审查专家，擅长发现题目中的问题并给出结构化反馈。"},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
    )
    llm_elapsed = (time.perf_counter() - llm_t0) * 1000

    content = response.content or ""

    # 解析审查结果
    rejected: list[dict[str, Any]] = []
    auto_fixed: list[dict[str, Any]] = []

    try:
        start = content.find("{")
        end = content.rfind("}") + 1
        if start >= 0 and end > start:
            result_data = json.loads(content[start:end])
            rejected = result_data.get("rejected", [])
            auto_fixed = result_data.get("auto_fixed", [])
    except (json.JSONDecodeError, ValueError):
        pass

    # 写入文件
    result_data = {
        "type": "review",
        "seq": 1,
        "status": "success",
        "reviewed_count": len(questions),
        "passed": len(questions) - len(rejected),
        "rejected": rejected,
        "auto_fixed": auto_fixed,
    }

    write_subagent_result(job_id, 1, "review", result_data)

    await log_step(
        job_id=str(job_id),
        name="run_reviewer_subagent",
        step="result",
        stage="reviewing",
        input={"model": used_model, "question_count": len(questions), "review_mode": review_mode},
        output={
            "reviewed": len(questions),
            "passed": len(questions) - len(rejected),
            "rejected_count": len(rejected),
            "auto_fixed_count": len(auto_fixed),
        },
        elapsed_ms=llm_elapsed,
    )

    return SubAgentResult(
        status="success",
        error=None,
    )
