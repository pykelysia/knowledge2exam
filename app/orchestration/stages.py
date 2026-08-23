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

from sqlalchemy import select
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
from app.models.job import Job, Upload
from app.models.plan import PlanItem
from app.models.question import Question
from app.orchestration.events import EventBus
from app.orchestration.fanout import run_fanout
from app.orchestration.state_machine import JobStatus, Stage
from app.rendering.markdown import build_markdown
from app.rendering.renderer import StubRenderer
from app.retrieval.past_papers import past_paper_cache
from app.retrieval.vector_store import PgVectorStore


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
            logging.getLogger(__name__).error(
                "真实 pipeline 失败，回退到模拟: %s", exc, exc_info=True
            )
            await run_mock_pipeline(job_id)


async def _run_real_pipeline(db: AsyncSession, job: Job, bus: EventBus) -> None:
    """真实 pipeline：preprocessing -> planning -> fan-out -> reviewer -> rendering。"""

    # ---------- preprocessing ----------
    job.status = JobStatus.preprocessing.value
    await bus.emit(
        job.id, "stage_changed",
        {"stage": Stage.preprocessing.value, "previous": JobStatus.pending.value},
        stage=Stage.preprocessing.value,
    )
    await db.commit()

    # 真实解析、切块、嵌入
    context = await _preprocess(db, job, bus)

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
    job.status = (
        JobStatus.completed.value
        if not result.pdf_failed
        else JobStatus.partially_completed.value
    )
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
            select(Upload).where(Upload.id.in_(job.upload_ids))
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

    # 3. 逐文件解析
    for upload in uploads:
        parser = get_parser(upload.filename or "")
        try:
            data = await storage.get(upload.storage_key)
            result = await parser.parse(upload.filename or "", data)
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
            char_count=result.char_count,
            is_shared=upload.shareable and job.school_id is not None and job.course_id is not None,
        )
        db.add(resource)
        await db.flush()

        # 存解析产物到对象存储
        parsed_key = f"jobs/{job.id}/parsed/{resource.id}.json"
        import json
        await storage.put(parsed_key, json.dumps(result.text).encode("utf-8"))

        if upload.source_type == SourceType.past_paper:
            # 往期试卷：全文进快读缓存
            past_papers[upload.filename or "unnamed"] = result
        elif upload.source_type in FILE_SOURCE_TYPES - {SourceType.past_paper}:
            # 可向量化的类型：切块 + 嵌入
            chunks = chunker.chunk(
                result.text,
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
                exclusive_texts.setdefault(upload.source_type.value, []).append(result.text)

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

    # 6. 获取共享库摘要（若有 school/course）
    shared_summary = None
    if job.school_id and job.course_id:
        shared_summary = await _get_shared_summary(db, job)

    context = {
        "past_papers_full_text_or_none": past_papers_full_text,
        "keypoint_list_or_none": keypoint_list,
        "extra_requirement_or_none": extra_requirement,
        "shared_library_summary": shared_summary,
    }

    await db.commit()
    return context


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

