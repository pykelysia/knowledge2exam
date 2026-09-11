"""编排层入口。

真实实现：文件式 ReAct agent（见 app/agents/agent.py）。
预处理把用户材料写入 agent 工作区（materials/），agent 规划蓝图并逐题
产出题目文件，最后经 render_paper 工具完成整卷渲染（md + PDF，失败自动
降级交付 md）；编排层在 agent 返回后仅负责入库（persist_exam_result）与
提交渲染产物（md_key/pdf_key/状态）。
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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import AgentHooks, run_exam_agent
from app.agents.workspace import Workspace
from app.config import settings
from app.core.db import AsyncSessionLocal
from app.core.debug_log import log_error, log_step
from app.core.enums import FILE_SOURCE_TYPES, TEXT_SOURCE_TYPES, SourceType
from app.core.exceptions import ErrorCode
from app.core.storage import StorageError, storage
from app.ingestion.chunking import Chunker
from app.ingestion.embedding import EmbeddingClient
from app.ingestion.parsers import get_parser
from app.ingestion.postprocess import build_merged_text, process_images
from app.models.job import Job, JobUpload
from app.models.plan import PlanItem
from app.models.resource import Resource
from app.models.upload import Upload
from app.orchestration.events import EventBus
from app.orchestration.integration import persist_exam_result
from app.orchestration.state_machine import JobStatus, Stage, is_terminal
from app.retrieval.vector_store import PgVectorStore

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


async def _run_pipeline_with_cancellation(job_id: uuid.UUID) -> None:
    """包装 run_pipeline，处理任务注册、取消与意外失败的终态收敛。"""
    try:
        await run_pipeline(job_id)
    except asyncio.CancelledError:
        # 任务被取消：更新数据库状态并通知订阅者
        async with AsyncSessionLocal() as db:
            job = await db.get(Job, job_id)
            if job and job.status != JobStatus.cancelled.value:
                job.status = JobStatus.cancelled.value
                job.finished_at = datetime.now(UTC)
                await db.commit()
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
                job = await db.get(Job, job_id)
                if job and not is_terminal(job.status):
                    stage_snapshot = job.status  # 事件里标注实际出错阶段
                    job.status = JobStatus.failed.value
                    job.error_code = ErrorCode.PIPELINE_FAILED.value
                    job.finished_at = datetime.now(UTC)
                    await db.commit()

                    bus = EventBus(db)
                    await bus.emit(
                        job_id,
                        "error",
                        {
                            "error_code": ErrorCode.PIPELINE_FAILED.value,
                            "message": str(exc) or type(exc).__name__,
                        },
                        stage=stage_snapshot,
                    )
                    await bus.emit_and_close(
                        job_id,
                        "done",
                        {"status": JobStatus.failed.value},
                        stage=stage_snapshot,
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
    job.status = JobStatus.preprocessing.value
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
    job.status = JobStatus.generating.value
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
        "max_retries": settings.agent_max_retries,
        "school_id": job.school_id,
        "course_id": job.course_id,
        "upload_ids": await _get_job_upload_ids(db, job.id),
    }

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

    async def on_question_accepted(question, completed: int, total: int) -> None:  # noqa: ANN001
        await bus.emit(
            job.id,
            "question_completed",
            {
                "seq": question.seq,
                "question_type": question.question_type,
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
        on_question_accepted=on_question_accepted,
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
            "questions": len(main_result.questions),
            "abandoned": len(main_result.abandoned_seqs),
            "completed_normally": main_result.completed_normally,
            "integration": integration_result,
            "distribution": distribution,
        },
    )

    job.planned_total = integration_result.get("total_questions", 0)
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
    job.status = JobStatus.rendering.value
    await db.commit()

    md_key = f"jobs/{job.id}/output/paper.md"
    pdf_key = f"jobs/{job.id}/output/paper.pdf"
    if main_result.render_status == "succeeded":
        job.md_key = md_key
        job.pdf_key = pdf_key
        job.status = JobStatus.completed.value
    else:
        # md_only：PDF 渲染失败但 paper.md 已生成；not_attempted 为防御式兜底
        job.md_key = md_key if main_result.render_status == "md_only" else None
        job.pdf_key = None
        job.status = JobStatus.partially_completed.value
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
        input={"questions_count": len(main_result.questions)},
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
# 真实预处理（解析、切块、嵌入、材料落盘）
# ---------------------------------------------------------------------------


async def _preprocess(db: AsyncSession, job: Job, bus: EventBus) -> dict[str, Any]:
    """解析所有上传件，切块嵌入，把用户材料写入 agent 工作区。"""
    # 1. 加载 uploads（按创建时间排序，保证材料命名与撞名后缀的确定性）
    upload_rows = (
        await db.scalars(
            select(Upload)
            .where(Upload.id.in_(await _get_job_upload_ids(db, job.id)))
            .order_by(Upload.created_at)
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

    past_papers: list[tuple[str, str]] = []
    exclusive_texts: dict[str, list[str]] = {}

    # 3. 逐文件/文本解析
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
                if not upload.storage_key:
                    raise StorageError(f"上传件缺少存储对象: id={upload.id}")
                data = await storage.get(upload.storage_key)
                result = await parser.parse(upload.filename or "", data)

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
                await bus.emit(
                    job.id,
                    "warning",
                    {
                        "code": ErrorCode.PARSE_FAILED,
                        "upload_id": upload.id,
                        "message": str(exc),
                    },
                )
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
        await storage.put(parsed_key, json.dumps(text).encode("utf-8"))

        if upload.source_type == SourceType.past_paper:
            # 往期试卷：全文留存，稍后写入工作区材料
            past_papers.append((upload.filename or f"unnamed_{len(past_papers) + 1}", text))
        elif upload.source_type in FILE_SOURCE_TYPES:
            # 可向量化的文件类：切块 + 嵌入
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
                        upload.shareable and job.school_id is not None and job.course_id is not None
                    )

                await vector_store.upsert(chunks)

            # 重点清单：全文留存供意图提取与 agent 查阅
            if upload.source_type == SourceType.keypoint_list:
                exclusive_texts.setdefault(upload.source_type.value, []).append(text)
        else:
            # 文本类（manual_text / extra_requirement）：全文留存
            exclusive_texts.setdefault(upload.source_type.value, []).append(text)

    # 4. 用户材料写入 agent 工作区（materials/）
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
            await workspace.write(rel, "\n\n".join(texts))
            material_files.append(rel)

    context = {"materials": material_files}

    await db.commit()
    return context


def _safe_material_stem(filename: str) -> str:
    """把上传文件名转成材料文件名可用的词干。"""
    stem = Path(filename).stem
    safe = re.sub(r"[^A-Za-z0-9_\-]", "_", stem).strip("_")
    return safe[:60]
