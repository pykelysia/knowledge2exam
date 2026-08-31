"""编排层入口。

真实实现：直接编排各阶段（planner -> fan-out -> reviewer -> rendering）。
LangGraph 编排图见 graph.py（首版保留为可选入口，因 MemorySaver 在
BackgroundTasks 多任务并发下存在 checkpoint 冲突，直接流程更稳定）。
Fallback：若 LLM 配置缺失或真实流程失败，回退到模拟 pipeline。
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planner import run_planner
from app.agents.reviewer import run_reviewer
from app.config import settings
from app.core.db import AsyncSessionLocal
from app.core.exceptions import ErrorCode
from app.core.storage import storage
from app.ingestion.chunking import Chunker
from app.ingestion.embedding import EmbeddingClient
from app.ingestion.parsers import get_parser
from app.ingestion.postprocess import build_merged_text, process_images
from app.models.job import Job, JobUpload
from app.models.plan import PlanItem
from app.models.question import Question, RetryLog
from app.models.upload import Upload
from app.orchestration.events import EventBus
from app.orchestration.fanout import run_fanout
from app.orchestration.state_machine import JobStatus, Stage
from app.rendering.markdown import build_markdown
from app.rendering.renderer import _get_renderer
from app.retrieval.past_papers import past_paper_cache
from app.retrieval.vector_store import PgVectorStore

from app.core.debug_log import log_error, log_step

MAX_QUESTION_RETRY = 3
MAX_REVIEW_CYCLES = 5


async def _get_job_upload_ids(db: AsyncSession, job_id: uuid.UUID) -> list[uuid.UUID]:
    """从 job_upload 中间表获取某个 job 关联的 upload_id 列表。"""
    from app.models.job import JobUpload

    result = await db.scalars(
        select(JobUpload.upload_id).where(JobUpload.job_id == job_id)
    )
    return list(result.all())


def _write_retry_log(
    db: AsyncSession,
    question_id: uuid.UUID | None,
    plan_item_id: uuid.UUID,
    attempt: int,
    reason: str,
    counted: bool,
    detail: dict[str, Any] | None = None,
) -> None:
    """写入一条 retry_log 记录。"""
    log = RetryLog(
        question_id=question_id,
        plan_item_id=plan_item_id,
        attempt=attempt,
        reason=reason,
        counted=counted,
        detail=detail,
    )
    db.add(log)


async def run_pipeline(job_id: uuid.UUID) -> None:
    """执行生成 pipeline。"""
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is None:
            return

        bus = EventBus(db)

        has_llm = bool(
            getattr(settings, "llm_api_key", None)
            and getattr(settings, "llm_base_url", None)
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


async def _run_real_pipeline(db: AsyncSession, job: Job, bus: EventBus) -> None:
    """真实 pipeline：preprocessing -> planning -> fan-out -> reviewer -> rendering。"""
    import time

    t0 = time.perf_counter()
    await log_step(
        job_id=str(job.id),
        name="_run_real_pipeline",
        stage=Stage.preprocessing.value,
        input={"upload_count": len(await _get_job_upload_ids(db, job.id))},
    )

    # ---------- preprocessing ----------
    job.status = JobStatus.preprocessing.value
    await bus.emit(
        job.id, "stage_changed",
        {"stage": Stage.preprocessing.value, "previous": JobStatus.pending.value},
        stage=Stage.preprocessing.value,
    )
    await db.commit()

    # 真实解析、切块、嵌入
    import time

    pre_t0 = time.perf_counter()
    context = await _preprocess(db, job, bus)
    pre_elapsed = (time.perf_counter() - pre_t0) * 1000

    await log_step(
        job_id=str(job.id),
        name="_preprocess",
        stage=Stage.preprocessing.value,
        input={"upload_count": len(await _get_job_upload_ids(db, job.id))},
        output={
            "past_papers": len(context.get("past_papers_full_text_or_none") or ""),
            "shared_summary": bool(context.get("shared_library_summary")),
        },
        elapsed_ms=pre_elapsed,
    )

    # ---------- planning ----------
    job.status = JobStatus.planning.value
    await bus.emit(
        job.id, "stage_changed",
        {"stage": Stage.planning.value, "previous": Stage.preprocessing.value},
        stage=Stage.planning.value,
    )
    await db.commit()

    plan_items = await run_planner(
        db=db,
        job_id=job.id,
        bus=bus,
        context={
            "duration_minutes": job.duration_minutes,
            "past_papers_full_text_or_none": context.get("past_papers_full_text_or_none"),
            "keypoint_list_or_none": context.get("keypoint_list_or_none"),
            "extra_requirement_or_none": context.get("extra_requirement_or_none"),
            "shared_library_summary": context.get("shared_library_summary"),
            "planner_model": getattr(settings, "planner_model", "gpt-4o"),
            "reference_used": "shared_library",
        },
    )

    await log_step(
        job_id=str(job.id),
        name="run_planner",
        stage=Stage.planning.value,
        input={"model": getattr(settings, "planner_model", "gpt-4o")},
        output={"plan_items": len(plan_items)},
    )

    job.planned_total = len(plan_items)
    await db.commit()

    # ---------- generating (fan-out) ----------
    job.status = JobStatus.generating.value
    await bus.emit(
        job.id, "stage_changed",
        {"stage": Stage.generating.value, "previous": Stage.planning.value},
        stage=Stage.generating.value,
    )
    await db.commit()

    await run_fanout(
        db=db,
        job_id=job.id,
        bus=bus,
        plan_items=plan_items,
        need_explanation=job.need_explanation,
        writer_model=getattr(settings, "writer_model", "gpt-4o-mini"),
        knowledge_context="",
        max_concurrent=getattr(settings, "max_concurrent_writers", 4),
    )

    distribution: dict[str, int] = {}
    for p in plan_items:
        distribution[p.question_type] = distribution.get(p.question_type, 0) + 1
    await log_step(
        job_id=str(job.id),
        name="run_fanout",
        stage=Stage.generating.value,
        input={"plan_count": len(plan_items), "max_concurrent": getattr(settings, "max_concurrent_writers", 4)},
        output={"distribution": distribution},
    )

    # ---------- reviewing (with retry loop) ----------
    if job.enable_review:
        await _run_review_with_retry(
            db=db,
            job=job,
            bus=bus,
            plan_items=plan_items,
            need_explanation=job.need_explanation,
            reviewer_model=getattr(settings, "reviewer_model", "gpt-4o"),
            writer_model=getattr(settings, "writer_model", "gpt-4o-mini"),
            max_concurrent=getattr(settings, "max_concurrent_writers", 4),
            context=context,
        )

    # ---------- rendering ----------
    previous_stage = Stage.reviewing if job.enable_review else Stage.generating
    job.status = JobStatus.rendering.value
    await bus.emit(
        job.id, "stage_changed",
        {"stage": Stage.rendering.value, "previous": previous_stage.value},
        stage=Stage.rendering.value,
    )
    await db.commit()

    q_rows = (
        await db.scalars(
            select(Question).where(Question.job_id == job.id).order_by(Question.seq)
        )
    ).all()
    questions = list(q_rows)

    # 过滤掉已废弃的题目
    active_questions = [q for q in questions if q.status != "abandoned"]

    # 重新获取 plan_map（仅包含未被 superseded 的 plan_item）
    all_plan_items = (
        await db.scalars(
            select(PlanItem).where(
                PlanItem.job_id == job.id,
                PlanItem.superseded_by.is_(None),
            )
        )
    ).all()
    plan_map = {p.seq: p for p in all_plan_items}

    questions_with_plan = [
        (plan_map.get(q.seq), q) for q in active_questions if q.seq in plan_map
    ]

    md_text = build_markdown(
        title=f"试卷（{job.duration_minutes} 分钟）",
        questions=questions_with_plan,
        need_explanation=job.need_explanation,
    )

    import time

    render_t0 = time.perf_counter()
    md_key = f"jobs/{job.id}/output/paper.md"
    from app.core.storage import storage
    await storage.put(md_key, md_text.encode("utf-8"))

    renderer = _get_renderer()
    result = await renderer.render(
        f"试卷（{job.duration_minutes} 分钟）",
        [
            {
                "seq": q.seq,
                "stem": q.stem,
                "question_type": q.question_type,
                "options": q.options,
                "answer": q.answer,
                "explanation": q.explanation if job.need_explanation else None,
                "sub_questions": q.sub_questions,
                "sub_answers": q.sub_answers,
            }
            for _, q in questions_with_plan
        ],
    )

    pdf_key = f"jobs/{job.id}/output/paper.pdf"
    await storage.put(pdf_key, result.pdf)
    render_elapsed = (time.perf_counter() - render_t0) * 1000

    await log_step(
        job_id=str(job.id),
        name="rendering",
        stage=Stage.rendering.value,
        input={"questions_count": len(questions_with_plan), "md_chars": len(md_text)},
        output={"pdf_failed": result.pdf_failed},
        elapsed_ms=render_elapsed,
    )

    job.md_key = md_key
    job.pdf_key = None if result.pdf_failed else pdf_key
    job.status = (
        JobStatus.completed.value
        if not result.pdf_failed
        else JobStatus.partially_completed.value
    )
    await db.commit()

    # 统计废弃题目数
    abandoned_count = sum(1 for q in questions if q.status == "abandoned")

    await bus.emit(
        job.id, "done",
        {
            "status": job.status,
            "total": job.planned_total or 0,
            "abandoned": abandoned_count,
            "md_url": f"/api/v1/jobs/{job.id}/paper.md",
            "pdf_url": f"/api/v1/jobs/{job.id}/paper.pdf" if not result.pdf_failed else None,
        },
        stage=Stage.rendering.value,
    )
    await db.commit()


async def _run_review_with_retry(
    db: AsyncSession,
    job: Job,
    bus: EventBus,
    plan_items: list[PlanItem],
    need_explanation: bool,
    reviewer_model: str,
    writer_model: str,
    max_concurrent: int,
    context: dict[str, Any] | None = None,
) -> None:
    """执行 reviewer，对打回的题目进入重试/换题循环。"""

    plan_map = {p.seq: p for p in plan_items}

    # 加载已有题目
    q_rows = (
        await db.scalars(
            select(Question).where(Question.job_id == job.id).order_by(Question.seq)
        )
    ).all()
    questions = list(q_rows)

    for cycle in range(MAX_REVIEW_CYCLES):
        # 执行 reviewer
        job.status = JobStatus.reviewing.value
        await bus.emit(
            job.id, "stage_changed",
            {"stage": Stage.reviewing.value, "previous": Stage.generating.value},
            stage=Stage.reviewing.value,
        )
        await db.commit()

        await run_reviewer(
            db=db,
            job_id=job.id,
            bus=bus,
            questions=questions,
            plan_items_map=plan_map,
            model=reviewer_model,
        )

        import time

        review_elapsed = 0  # 可通过 reviewer 返回值补充
        rejected_qs = [q for q in questions if q.review_passed is False]
        await log_step(
            job_id=str(job.id),
            name="run_reviewer",
            stage=Stage.reviewing.value,
            input={"model": reviewer_model, "question_count": len(questions), "cycle": cycle + 1},
            output={"passed": len(questions) - len(rejected_qs), "rejected": len(rejected_qs)},
            elapsed_ms=review_elapsed,
        )
        if not rejected_qs:
            break

        # 分类：可重试 vs 需废弃
        to_retry: list[Question] = []
        to_abandon: list[Question] = []

        for q in rejected_qs:
            if q.retry_count < MAX_QUESTION_RETRY:
                to_retry.append(q)
            else:
                to_abandon.append(q)

        # 处理废弃题目：标记 + emit question_replanned
        replacement_requests: list[dict[str, Any]] = []
        for q in to_abandon:
            q.status = "abandoned"
            _write_retry_log(
                db, q.id, q.plan_item_id, q.retry_count + 1,
                "retry_exhausted", True,
                {"action": "abandon", "review_cycle": cycle},
            )
            await bus.emit(
                job.id, "question_replanned",
                {
                    "question_id": str(q.id),
                    "plan_item_id": str(q.plan_item_id),
                    "seq": q.seq,
                    "retry_count": q.retry_count,
                },
            )

            # 收集换题请求信息
            old_plan = plan_map.get(q.seq)
            if old_plan:
                replacement_requests.append({
                    "seq": q.seq,
                    "question_type": q.question_type,
                    "knowledge_point": old_plan.knowledge_point,
                    "exam_direction": old_plan.exam_direction,
                    "difficulty": old_plan.difficulty,
                    "failure_reason": "retry_exhausted",
                    "retry_count": q.retry_count,
                })

        # 如果有换题请求，调用 planner 生成替换方案
        new_plan_items: list[PlanItem] = []
        if replacement_requests and context:
            try:
                # 构建当前试卷的 plan_items 列表（仅包含未被 superseded 的）
                current_plans = [p for p in plan_items if p.superseded_by is None]
                new_plan_items = await run_planner(
                    db=db,
                    job_id=job.id,
                    bus=bus,
                    context={
                        "duration_minutes": job.duration_minutes,
                        "past_papers_full_text_or_none": context.get("past_papers_full_text_or_none"),
                        "keypoint_list_or_none": context.get("keypoint_list_or_none"),
                        "extra_requirement_or_none": context.get("extra_requirement_or_none"),
                        "shared_library_summary": context.get("shared_library_summary"),
                        "planner_model": getattr(settings, "planner_model", "gpt-4o"),
                        "reference_used": "shared_library",
                        "mode": "replacement",
                        "replacement_requests": replacement_requests,
                        "current_plan_items": [
                            {
                                "seq": p.seq,
                                "question_type": p.question_type,
                                "knowledge_point": p.knowledge_point,
                                "exam_direction": p.exam_direction,
                            }
                            for p in current_plans
                        ],
                    },
                )
            except Exception as exc:
                await log_error(
                    job_id=str(job.id),
                    exc=exc,
                    stage=Stage.reviewing.value,
                    context={
                        "cycle": cycle + 1,
                        "mode": "replacement",
                        "planner_model": getattr(settings, "planner_model", "gpt-4o"),
                    },
                )
                new_plan_items = []

        # 为换题题目创建新 plan_item 并生成题目
        for new_plan in new_plan_items:
            old_plan = plan_map.get(new_plan.seq)
            if old_plan:
                old_plan.superseded_by = new_plan.id
                db.add(new_plan)
                await db.flush()

                # 为新 plan_item 生成题目（retry_count 重置为 0）
                try:
                    knowledge_context = await _retrieve_knowledge_for_plan(db, job.id, new_plan)
                    new_q = await generate_single_question(
                        db=db,
                        job_id=job.id,
                        plan_item=new_plan,
                        need_explanation=need_explanation,
                        model=writer_model,
                        knowledge_context=knowledge_context,
                        retry_count=0,
                    )
                    new_q.replanned_from = old_plan.id
                    if bus:
                        await bus.emit(
                            job.id, "question_completed",
                            {"seq": new_q.seq, "question_type": new_q.question_type,
                             "completed": 1, "total": 1},
                            stage=None,
                        )
                        await db.commit()
                except Exception as exc:
                    await log_error(
                        job_id=str(job.id),
                        exc=exc,
                        stage=Stage.reviewing.value,
                        context={
                            "cycle": cycle + 1,
                            "mode": "new_plan_generation",
                            "plan_seq": new_plan.seq,
                            "writer_model": writer_model,
                        },
                    )
                    _write_retry_log(
                        db, None, new_plan.id, 0,
                        "generation_failed", False,
                        {"error": str(exc), "model": writer_model},
                    )
                    await db.commit()

        # 对于 planner 未覆盖的废弃题目，回退到直接复制逻辑
        covered_seqs = {p.seq for p in new_plan_items}
        for q in to_abandon:
            if q.seq not in covered_seqs:
                old_plan = plan_map.get(q.seq)
                if old_plan:
                    # 直接复制旧 plan_item 并生成新题
                    fallback_plan = PlanItem(
                        job_id=job.id,
                        seq=old_plan.seq,
                        question_type=old_plan.question_type,
                        knowledge_point=old_plan.knowledge_point,
                        exam_direction=old_plan.exam_direction,
                        difficulty=old_plan.difficulty,
                        reference_source=old_plan.reference_source,
                    )
                    db.add(fallback_plan)
                    await db.flush()
                    old_plan.superseded_by = fallback_plan.id

                    try:
                        knowledge_context = await _retrieve_knowledge_for_plan(db, job.id, fallback_plan)
                        new_q = await generate_single_question(
                            db=db,
                            job_id=job.id,
                            plan_item=fallback_plan,
                            need_explanation=need_explanation,
                            model=writer_model,
                            knowledge_context=knowledge_context,
                            retry_count=0,
                        )
                        new_q.replanned_from = old_plan.id
                        if bus:
                            await bus.emit(
                                job.id, "question_completed",
                                {"seq": new_q.seq, "question_type": new_q.question_type,
                                 "completed": 1, "total": 1},
                                stage=None,
                            )
                            await db.commit()
                    except Exception as exc:
                        await log_error(
                            job_id=str(job.id),
                            exc=exc,
                            stage=Stage.reviewing.value,
                            context={
                                "cycle": cycle + 1,
                                "mode": "fallback_generation",
                                "plan_seq": fallback_plan.seq,
                                "writer_model": writer_model,
                            },
                        )
                        _write_retry_log(
                            db, None, fallback_plan.id, 0,
                            "generation_failed", False,
                            {"error": str(exc), "model": writer_model},
                        )
                        await db.commit()

        if not to_retry:
            # 所有打回题目均已废弃，无更多重试
            await db.commit()
            break

        # 删除旧题目，准备重试
        retry_plan_items: list[PlanItem] = []
        retry_count_map: dict[uuid.UUID, int] = {}
        for q in to_retry:
            plan_item = plan_map.get(q.seq)
            if plan_item:
                retry_plan_items.append(plan_item)
                retry_count_map[plan_item.id] = q.retry_count + 1

            _write_retry_log(
                db, q.id, q.plan_item_id, q.retry_count + 1,
                "reviewer_rejected", True,
                {"old_status": q.status, "review_cycle": cycle},
            )
            await db.delete(q)

        await db.flush()

        # 重新 fanout 仅包含打回的题目
        await run_fanout(
            db=db,
            job_id=job.id,
            bus=bus,
            plan_items=retry_plan_items,
            need_explanation=need_explanation,
            writer_model=writer_model,
            knowledge_context="",
            max_concurrent=max_concurrent,
            retry_count_map=retry_count_map,
        )

        # 刷新题目列表，进入下一轮 reviewer
        q_rows = (
            await db.scalars(
                select(Question).where(Question.job_id == job.id).order_by(Question.seq)
            )
        ).all()
        questions = list(q_rows)

        # 更新 plan_map（仅保留未被 superseded 的 plan_item）
        all_plan_items = (
            await db.scalars(
                select(PlanItem).where(
                    PlanItem.job_id == job.id,
                    PlanItem.superseded_by.is_(None),
                )
            )
        ).all()
        plan_map = {p.seq: p for p in all_plan_items}

    # 更新 planned_total 为有效题目数
    active_count = await db.scalar(
        select(func.count()).where(
            Question.job_id == job.id,
            Question.status != "abandoned",
        )
    )
    job.planned_total = active_count or 0
    await db.commit()


# ---------------------------------------------------------------------------
# 模拟 pipeline（fallback / 本地演示）
# ---------------------------------------------------------------------------

_MOCK_TOTAL = 6
_MOCK_DISTRIBUTION = {"choice": 2, "blank": 2, "short_answer": 2}


async def run_mock_pipeline(job_id: uuid.UUID) -> None:
    """在独立会话中执行模拟生成流程。"""
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is None:
            return
        bus = EventBus(db)

        job.status = JobStatus.preprocessing.value
        await bus.emit(
            job.id, "stage_changed",
            {"stage": Stage.preprocessing.value, "previous": JobStatus.pending.value},
            stage=Stage.preprocessing.value,
        )
        await db.commit()
        await asyncio.sleep(getattr(settings, "mock_stage_delay_seconds", 0.5))

        job.status = JobStatus.planning.value
        await bus.emit(
            job.id, "stage_changed",
            {"stage": Stage.planning.value, "previous": Stage.preprocessing.value},
            stage=Stage.planning.value,
        )
        await db.commit()
        await asyncio.sleep(getattr(settings, "mock_stage_delay_seconds", 0.5))

        await bus.emit(
            job.id, "plan_ready",
            {
                "total": _MOCK_TOTAL,
                "distribution": _MOCK_DISTRIBUTION,
                "reference_used": "default_template",
                "duration_minutes": job.duration_minutes,
            },
            stage=Stage.planning.value,
        )
        job.planned_total = _MOCK_TOTAL
        await db.commit()
        await asyncio.sleep(getattr(settings, "mock_stage_delay_seconds", 0.5))

        job.status = JobStatus.generating.value
        await bus.emit(
            job.id, "stage_changed",
            {"stage": Stage.generating.value, "previous": Stage.planning.value},
            stage=Stage.generating.value,
        )
        await db.commit()
        await asyncio.sleep(getattr(settings, "mock_stage_delay_seconds", 0.5))

        type_specs = [
            ("choice", {"A": "正确选项", "B": "干扰项", "C": "干扰项", "D": "干扰项"}, "A"),
            ("choice", {"A": "干扰项", "B": "正确选项", "C": "干扰项", "D": "干扰项"}, "B"),
            ("blank", None, "单位冲激响应"),
            ("blank", None, "狄利克雷条件"),
            ("short_answer", None, "见各子问题答案"),
            ("short_answer", None, "见各子问题答案"),
        ]

        seq = 0
        for question_type, options, answer in type_specs:
            seq += 1
            plan = PlanItem(
                job_id=job_id,
                seq=seq,
                question_type=question_type,
                knowledge_point=f"知识点 {seq}",
                exam_direction=f"考察方向 {seq}",
                difficulty="medium",
            )
            db.add(plan)
            await db.flush()

            stem = f"第 {seq} 题（{question_type}）模拟题干"
            explanation = f"第 {seq} 题解析" if job.need_explanation else None
            q = Question(
                job_id=job_id,
                plan_item_id=plan.id,
                seq=seq,
                question_type=question_type,
                stem=stem,
                options=options,
                answer=answer,
                sub_questions=["子问题 1", "子问题 2"] if question_type == "short_answer" else None,
                sub_answers=["子答案 1", "子答案 2"] if question_type == "short_answer" else None,
                explanation=explanation,
                status="accepted",
            )
            db.add(q)
            await db.flush()

            await bus.emit(
                job.id, "question_completed",
                {
                    "seq": seq,
                    "question_type": question_type,
                    "completed": seq,
                    "total": _MOCK_TOTAL,
                },
                stage=Stage.generating.value,
            )
            await db.commit()
            await asyncio.sleep(getattr(settings, "mock_stage_delay_seconds", 0.5))

        if job.enable_review:
            job.status = JobStatus.reviewing.value
            await bus.emit(
                job.id, "stage_changed",
                {"stage": Stage.reviewing.value, "previous": Stage.generating.value},
                stage=Stage.reviewing.value,
            )
            await db.commit()
            await asyncio.sleep(getattr(settings, "mock_stage_delay_seconds", 0.5))

            await bus.emit(
                job.id, "review_result",
                {"checked": _MOCK_TOTAL, "passed": _MOCK_TOTAL, "rejected": [], "auto_fixed": []},
                stage=Stage.reviewing.value,
            )
            await db.commit()
            await asyncio.sleep(getattr(settings, "mock_stage_delay_seconds", 0.5))

        previous = Stage.reviewing if job.enable_review else Stage.generating
        job.status = JobStatus.rendering.value
        await bus.emit(
            job.id, "stage_changed",
            {"stage": Stage.rendering.value, "previous": previous.value},
            stage=Stage.rendering.value,
        )
        await db.commit()
        await asyncio.sleep(getattr(settings, "mock_stage_delay_seconds", 0.5))

        questions = [
            {
                "seq": i + 1,
                "stem": f"第 {i + 1} 题（{t}）模拟题干",
                "question_type": t,
                "options": opts,
                "answer": ans,
                "explanation": f"第 {i + 1} 题解析" if job.need_explanation else None,
                "sub_questions": ["子问题 1", "子问题 2"] if t == "short_answer" else None,
                "sub_answers": ["子答案 1", "子答案 2"] if t == "short_answer" else None,
            }
            for i, (t, opts, ans) in enumerate(type_specs)
        ]

        renderer = _get_renderer()
        result = await renderer.render(f"模拟试卷（{job.duration_minutes} 分钟）", questions)
        md_key = f"jobs/{job_id}/output/paper.md"
        pdf_key = f"jobs/{job_id}/output/paper.pdf"
        await storage.put(md_key, result.md)
        await storage.put(pdf_key, result.pdf)
        job.md_key = md_key
        job.pdf_key = pdf_key

        job.status = JobStatus.completed.value
        await db.commit()

        await bus.emit(
            job.id, "done",
            {
                "status": JobStatus.completed.value,
                "total": _MOCK_TOTAL,
                "abandoned": 0,
                "md_url": f"/api/v1/jobs/{job_id}/paper.md",
                "pdf_url": f"/api/v1/jobs/{job_id}/paper.pdf",
            },
            stage=Stage.rendering.value,
        )
        await db.commit()


# ---------------------------------------------------------------------------
# 真实预处理（解析、切块、嵌入）
# ---------------------------------------------------------------------------

# 可向量化的内容类型
_ADDITIVE_TYPES = {
    "book",
    "lecture",
    "note",
}
_EXCLUSIVE_TYPES = {
    "keypoint_list",
    "past_paper",
    "manual_text",
    "extra_requirement",
}


async def _preprocess(db: AsyncSession, job: Job, bus: EventBus) -> dict[str, Any]:
    """解析所有上传件，切块嵌入，准备检索上下文。"""
    from sqlalchemy import select

    from app.core.enums import FILE_SOURCE_TYPES, SourceType
    from app.models.resource import Resource

    # 1. 加载 uploads
    upload_rows = (
        await db.scalars(
            select(Upload).where(Upload.id.in_(await _get_job_upload_ids(db, job.id)))
        )
    ).all()
    uploads = list(upload_rows)

    # 2. 初始化组件
    vector_store = PgVectorStore(AsyncSessionLocal)
    embedder = EmbeddingClient.from_settings()
    chunker = Chunker(
        chunk_size=getattr(settings, "chunk_size", 700),
        overlap=getattr(settings, "chunk_overlap", 100),
    )

    past_papers: dict[str, Any] = {}
    additive_chunks: dict[str, list[Any]] = {}
    exclusive_texts: dict[str, list[str]] = {}

    # 3. 逐文件/文本解析
    from app.core.enums import TEXT_SOURCE_TYPES

    for upload in uploads:
        # 文本类上传（manual_text / extra_requirement）没有 filename，
        # 直接使用 raw_text，无需调用文件解析器。
        if upload.source_type in TEXT_SOURCE_TYPES:
            text = upload.raw_text or ""
            char_count = len(text)
            upload.parse_status = "succeeded"
        else:
            parser = get_parser(upload.filename or "")
            try:
                data = await storage.get(upload.storage_key)
                result = parser.parse(upload.filename or "", data)

                # 图片后处理：去重、合并、间隙标记
                if result.images:
                    process_images(result.images)
                    image_text = build_merged_text(result.images)
                    if image_text:
                        result.text = result.text + "\n\n" + image_text
                        result.char_count = len(result.text)

                text = result.text
                char_count = result.char_count
                upload.parse_status = "succeeded"
            except Exception as exc:
                upload.parse_status = "failed"
                upload.parse_error = str(exc)
                await bus.emit(job.id, "warning", {
                    "code": ErrorCode.PARSE_FAILED,
                    "upload_id": upload.id,
                    "message": str(exc),
                })
                await db.commit()
                continue

        # 创建 resource
        resource = Resource(
            upload_id=upload.id,
            source_type=upload.source_type,
            school_id=job.school_id,
            course_id=job.course_id,
            char_count=char_count,
            is_shared=upload.shareable and job.school_id is not None and job.course_id is not None,
        )
        db.add(resource)
        await db.flush()

        # 存解析产物到对象存储
        parsed_key = f"jobs/{job.id}/parsed/{resource.id}.json"
        import json
        await storage.put(parsed_key, json.dumps(text).encode("utf-8"))

        if upload.source_type == SourceType.past_paper:
            # 往期试卷：全文进快读缓存
            # 构造一个轻量结果对象，保留 .text 以便后续统一处理
            class _PaperResult:
                pass

            past_papers[upload.filename or "unnamed"] = _PaperResult()
            past_papers[upload.filename or "unnamed"].text = text
        elif upload.source_type in FILE_SOURCE_TYPES - {SourceType.past_paper}:
            # 可向量化的类型：切块 + 嵌入
            chunks = chunker.chunk(
                text,
                page=None,
                source_type=upload.source_type,
            )
            if chunks:
                # 批量嵌入
                embeddings = await embedder.embed([c.text for c in chunks])
                for chunk, _emb in zip(chunks, embeddings, strict=True):
                    chunk.resource_id = resource.id
                    chunk.upload_id = upload.id
                    chunk.user_id = job.user_id
                    chunk.school_id = job.school_id
                    chunk.course_id = job.course_id
                    chunk.source_type = upload.source_type
                    chunk.is_shared = (
                        upload.shareable
                        and job.school_id is not None
                        and job.course_id is not None
                    )

                await vector_store.upsert(chunks)

            # 汇总上下文
            if upload.source_type in _ADDITIVE_TYPES:
                additive_chunks.setdefault(upload.source_type.value, []).extend(chunks)
            else:
                exclusive_texts.setdefault(upload.source_type.value, []).append(text)

    # 4. 存快读缓存
    past_paper_cache.set(job.id, past_papers)

    # 5. 构造 planner context
    past_papers_full_text = (
        "\n\n".join(p.text for p in past_papers.values())
        if past_papers
        else None
    )
    keypoint_list = (
        "\n\n".join(exclusive_texts.get("keypoint_list", []))
        if exclusive_texts.get("keypoint_list")
        else None
    )
    extra_requirement = (
        "\n\n".join(exclusive_texts.get("extra_requirement", []))
        if exclusive_texts.get("extra_requirement")
        else None
    )

    # 6. 多份往期试卷综合（题型分布、知识点、难度三维度）
    past_papers_data: list[dict[str, Any]] = []
    if past_papers:
        # 提取每份试卷的结构化信息
        for filename, paper in past_papers.items():
            paper_info: dict[str, Any] = {
                "filename": filename,
                "text": paper.text,
                # 题型分布（从文本中统计各题型题数）
                "distribution": _extract_type_distribution(paper.text),
                # 知识点列表（从文本中提取）
                "knowledge_points": _extract_knowledge_points(paper.text),
                # 难度分布
                "difficulty_distribution": _extract_difficulty_distribution(paper.text),
            }
            past_papers_data.append(paper_info)

    # 7. 获取共享库摘要（若有 school/course）
    shared_summary = None
    if job.school_id and job.course_id:
        shared_summary = await _get_shared_summary(db, job)

    context = {
        "past_papers_full_text_or_none": past_papers_full_text,
        "past_papers_data": past_papers_data,
        "keypoint_list_or_none": keypoint_list,
        "extra_requirement_or_none": extra_requirement,
        "shared_library_summary": shared_summary,
    }

    await db.commit()
    return context


def _extract_type_distribution(text: str) -> dict[str, int]:
    """从往期试卷文本中提取题型分布。"""
    distribution: dict[str, int] = {"choice": 0, "blank": 0, "short_answer": 0}

    # 简单的关键词匹配（实际可由更复杂的 NLP 替换）
    choice_markers = ["选择题", "单项选择", "多项选择", "Choose", "Selection"]
    blank_markers = ["填空题", "Fill in the blank", "____"]
    short_markers = ["简答题", "论述题", "计算题", "Essay", "Short answer"]

    for marker in choice_markers:
        if marker in text:
            distribution["choice"] += 1
            break

    for marker in blank_markers:
        if marker in text:
            distribution["blank"] += 1
            break

    for marker in short_markers:
        if marker in text:
            distribution["short_answer"] += 1
            break

    # 如果未检测到任何题型，返回默认值
    total = sum(distribution.values())
    if total == 0:
        return {"choice": 5, "blank": 5, "short_answer": 5}

    return distribution


def _extract_knowledge_points(text: str) -> list[str]:
    """从往期试卷文本中提取知识点列表。"""
    # 简单的知识点提取（实际可由更复杂的 NLP 替换）
    # 这里返回空列表，由 planner LLM 自行理解
    return []


def _extract_difficulty_distribution(text: str) -> dict[str, float]:
    """从往期试卷文本中提取难度分布。"""
    # 简单的难度分布提取
    easy_count = text.lower().count("easy") + text.count("简单") + text.count("基础")
    medium_count = text.lower().count("medium") + text.count("中等")
    hard_count = text.lower().count("hard") + text.count("困难") + text.count("挑战")

    total = easy_count + medium_count + hard_count
    if total == 0:
        return {"easy": 0.3, "medium": 0.5, "hard": 0.2}

    return {
        "easy": easy_count / total,
        "medium": medium_count / total,
        "hard": hard_count / total,
    }


async def _get_shared_summary(db: AsyncSession, job: Job) -> str | None:
    """从共享库检索相关内容，生成摘要供 planner 使用。"""
    from app.retrieval.vector_store import PgVectorStore

    if not job.school_id or not job.course_id:
        return None

    # 检索共享库中同校同课程的 book / lecture / note 类型的 chunk
    vector_store = PgVectorStore(AsyncSessionLocal)

    additive_types = ["book", "lecture", "note"]
    summaries: list[str] = []

    for source_type in additive_types:
        filter_expr = {
            "and": [
                {"eq": {"school_id": str(job.school_id)}},
                {"eq": {"course_id": str(job.course_id)}},
                {"eq": {"source_type": source_type}},
                {"eq": {"is_shared": True}},
            ]
        }
        result = await vector_store.search(
            query="",  # 空查询，取全部（后续可优化为随机采样）
            filter_expr=filter_expr,
            top_k=5,
        )
        if result.chunks:
            chunk_texts = "\n\n".join(c["text"] for c in result.chunks)
            summaries.append(f"【{source_type}】\n{chunk_texts}")

    if not summaries:
        return None

    return "\n\n".join(summaries)


async def _retrieve_knowledge_for_plan(
    db: AsyncSession, job_id: uuid.UUID, plan: PlanItem
) -> str:
    """为单个 plan_item 检索相关知识库内容（供换题时复用）。"""
    from app.models.job import JobUpload

    job = await db.get(Job, job_id)
    if not job or not job.school_id or not job.course_id:
        return ""

    upload_ids = (
        await db.scalars(
            select(JobUpload.upload_id).where(JobUpload.job_id == job_id)
        )
    ).all()

    if not upload_ids:
        return ""

    vector_store = PgVectorStore(AsyncSessionLocal)

    additive_types = ["book", "lecture", "note"]
    chunks: list[str] = []

    for source_type in additive_types:
        filter_expr = {
            "and": [
                {"eq": {"school_id": str(job.school_id)}},
                {"eq": {"course_id": str(job.course_id)}},
                {"eq": {"source_type": source_type}},
                {"eq": {"is_shared": True}},
            ]
        }
        try:
            result = await vector_store.search(
                query=plan.exam_direction,
                filter_expr=filter_expr,
                top_k=3,
            )
            for c in result.chunks:
                chunks.append(c["text"])
        except Exception:
            continue

    return "\n\n".join(chunks)
