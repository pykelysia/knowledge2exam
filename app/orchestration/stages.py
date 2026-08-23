"""编排层入口。

真实实现：直接编排各阶段（planner -> fan-out -> reviewer -> rendering）。
LangGraph 编排图见 graph.py（首版保留为可选入口，因 MemorySaver 在
BackgroundTasks 多任务并发下存在 checkpoint 冲突，直接流程更稳定）。
Fallback：若 LLM 配置缺失或真实流程失败，回退到模拟 pipeline。
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.planner import run_planner
from app.agents.reviewer import run_reviewer
from app.config import settings
from app.core.db import AsyncSessionLocal
from app.core.exceptions import AppException, ErrorCode
from app.core.storage import storage
from app.models.job import Job
from app.models.plan import PlanItem
from app.models.question import Question
from app.orchestration.events import EventBus
from app.orchestration.fanout import run_fanout
from app.orchestration.state_machine import JobStatus, Stage
from app.rendering.markdown import build_markdown
from app.rendering.renderer import StubRenderer


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
            await run_mock_pipeline(job_id)
            return

        try:
            await _run_real_pipeline(db, job, bus)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("真实 pipeline 失败，回退到模拟: %s", exc, exc_info=True)
            await run_mock_pipeline(job_id)


async def _run_real_pipeline(db: AsyncSession, job: Job, bus: EventBus) -> None:
    """真实 pipeline：planner -> fan-out -> reviewer -> rendering。"""

    # ---------- preprocessing ----------
    job.status = JobStatus.preprocessing.value
    await bus.emit(
        job.id, "stage_changed",
        {"stage": Stage.preprocessing.value, "previous": JobStatus.pending.value},
        stage=Stage.preprocessing.value,
    )
    await db.commit()

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
            "past_papers_full_text_or_none": None,
            "keypoint_list_or_none": None,
            "extra_requirement_or_none": None,
            "shared_library_summary": None,
            "planner_model": getattr(settings, "planner_model", "gpt-4o"),
            "reference_used": "default_template",
        },
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

    fanout_result = await run_fanout(
        db=db,
        job_id=job.id,
        bus=bus,
        plan_items=plan_items,
        need_explanation=job.need_explanation,
        writer_model=getattr(settings, "writer_model", "gpt-4o-mini"),
        knowledge_context="",
        max_concurrent=getattr(settings, "max_concurrent_writers", 4),
    )

    # ---------- reviewing (optional) ----------
    if job.enable_review:
        job.status = JobStatus.reviewing.value
        await bus.emit(
            job.id, "stage_changed",
            {"stage": Stage.reviewing.value, "previous": Stage.generating.value},
            stage=Stage.reviewing.value,
        )
        await db.commit()

        q_rows = (
            await db.scalars(
                select(Question).where(Question.job_id == job.id).order_by(Question.seq)
            )
        ).all()
        questions = list(q_rows)
        plan_map = {p.seq: p for p in plan_items}

        await run_reviewer(
            db=db,
            job_id=job.id,
            bus=bus,
            questions=questions,
            plan_items_map=plan_map,
            model=getattr(settings, "reviewer_model", "gpt-4o"),
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
    plan_map = {p.seq: p for p in plan_items}

    questions_with_plan = [(plan_map.get(q.seq), q) for q in questions if q.seq in plan_map]

    md_text = build_markdown(
        title=f"试卷（{job.duration_minutes} 分钟）",
        questions=questions_with_plan,
        need_explanation=job.need_explanation,
    )

    md_key = f"jobs/{job.id}/output/paper.md"
    from app.core.storage import storage
    await storage.put(md_key, md_text.encode("utf-8"))

    renderer = StubRenderer()
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

    job.md_key = md_key
    job.pdf_key = None if result.pdf_failed else pdf_key
    job.status = JobStatus.completed.value if not result.pdf_failed else JobStatus.partially_completed.value
    await db.commit()

    await bus.emit(
        job.id, "done",
        {
            "status": job.status,
            "total": job.planned_total or 0,
            "abandoned": 0,
            "md_url": f"/api/v1/jobs/{job.id}/paper.md",
            "pdf_url": f"/api/v1/jobs/{job.id}/paper.pdf" if not result.pdf_failed else None,
        },
        stage=Stage.rendering.value,
    )
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

        renderer = StubRenderer()
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
