"""上传即解析管线。

上传件落库后立即异步执行：解析（含页级 OCR 与图片后处理）→ 落盘解析产物
→ 切块 + 嵌入写入 chunk 表。任务阶段不再重复解析，只消费现成产物。
写入幂等（ON CONFLICT (upload_id, chunk_index)），服务重启后由
`recover_pending_uploads` 重新入队补跑。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.db import AsyncSessionLocal
from app.core.debug_log import log_error
from app.core.enums import (
    FILE_SOURCE_TYPES,
    TEXT_SOURCE_TYPES,
    VECTORIZED_SOURCE_TYPES,
)
from app.core.exceptions import ErrorCode
from app.core.storage import StorageError, storage
from app.core.task_registry import is_running, register
from app.ingestion.chunking import Chunk, Chunker
from app.ingestion.embedding import EmbeddingClient
from app.ingestion.parsers import get_parser
from app.ingestion.parsers.base import ParseResult
from app.ingestion.postprocess import (
    build_merged_text,
    inline_image_text,
    process_images,
    replace_page_text,
    run_page_ocr,
)
from app.models.upload import Upload
from app.retrieval.vector_store import PgVectorStore

logger = logging.getLogger(__name__)


def parsed_text_key(upload_id: uuid.UUID) -> str:
    """解析产物在对象存储中的位置。"""
    return f"uploads/{upload_id}/parsed/text.json"


async def parse_upload(upload_id: uuid.UUID) -> None:
    """解析单个上传件并登记产物。幂等，可安全重跑。"""
    async with AsyncSessionLocal() as db:
        upload = await db.get(Upload, upload_id)
        if upload is None or upload.deleted_at is not None:
            return
        if upload.parse_status == "succeeded":
            return

        # 文本类：无文件与解析器，直接按正文长度登记
        if upload.source_type in TEXT_SOURCE_TYPES:
            upload.char_count = len(upload.raw_text or "")
            upload.parse_status = "succeeded"
            await db.commit()
            return

        upload.parse_status = "parsing"
        upload.parse_error = None
        await db.commit()

        warnings: list[dict] = []
        try:
            text, char_count = await _parse_file(upload, warnings)
            await _persist_artifacts(db, upload, text, char_count, warnings)
        except asyncio.CancelledError:
            # 软删除触发的取消：行已标记 deleted_at，无需收敛解析状态
            await db.rollback()
            raise
        except Exception as exc:
            await db.rollback()
            upload.parse_status = "failed"
            upload.parse_error = str(exc)
            await db.commit()
            logger.warning("upload=%s 解析失败: %s", upload_id, exc)


async def _parse_file(upload: Upload, warnings: list[dict]) -> tuple[str, int]:
    """执行文件解析链：解析 → 页级 OCR → 图片后处理，返回 (全文, 字符数)。"""
    parser = get_parser(upload.filename or "")
    if not upload.storage_key:
        raise StorageError(f"上传件缺少存储对象: id={upload.id}")
    data = await storage.get(upload.storage_key)
    result = await parser.parse_async(upload.filename or "", data)

    # 页级视觉 OCR 兜底：损坏页/扫描页整页转录（失败保留原文并记告警）
    ocr_pages: set[int] = set()
    if result.page_renders:
        ocr_pages = await _apply_page_ocr(upload, result, warnings)

    # 整页已被视觉 OCR 覆盖的页面，跳过其内嵌图片的单独 OCR（避免内容重复）
    if result.images and ocr_pages:
        result.images = [img for img in result.images if img.page not in ocr_pages] or None

    if result.images:
        # 图片后处理（OCR 在工作线程内执行）：去重、合并、间隙标记
        await asyncio.to_thread(process_images, result.images)
        # 占位符就地融合（PDF 图片/图表引用回填原文位置）；
        # 无占位符的解析产物（旧格式/其他解析器）回退文末追加
        inlined = await asyncio.to_thread(inline_image_text, result.text, result.images)
        if inlined is not None:
            result.text = inlined
        else:
            image_text = await asyncio.to_thread(build_merged_text, result.images)
            if image_text:
                result.text = result.text + "\n\n" + image_text
        result.char_count = len(result.text)

    return result.text, result.char_count


async def _apply_page_ocr(
    upload: Upload, result: ParseResult, warnings: list[dict]
) -> set[int]:
    """对解析器标记的损坏页执行视觉 OCR；成功页替换文本，失败页保留原文并记告警。

    返回成功完成 OCR 的页码集合（1-based），供调用方跳过这些页的内嵌图片 OCR。
    """
    try:
        page_texts, failed = await run_page_ocr(result.page_renders or [])
    except Exception as exc:
        logger.warning("upload=%s 页级 OCR 整体失败: %s", upload.id, exc)
        failed = list(result.page_renders or [])
        page_texts = {}

    if page_texts:
        result.text = replace_page_text(result.text, page_texts)
        result.char_count = len(result.text)

    for render in failed:
        warnings.append(
            {
                "code": ErrorCode.OCR_DEGRADED.value,
                "message": (
                    f"第 {render.page} 页文本层损坏（{render.reason}），"
                    "视觉识别失败，该页内容可能缺失"
                ),
            }
        )

    if result.page_renders_truncated:
        warnings.append(
            {
                "code": ErrorCode.OCR_DEGRADED.value,
                "message": (
                    "损坏页数超过上限 "
                    f"(ocr_max_pages={settings.ocr_max_pages})，"
                    "部分页面未做视觉识别"
                ),
            }
        )
    return set(page_texts)


async def _persist_artifacts(
    db: AsyncSession,
    upload: Upload,
    text: str,
    char_count: int,
    warnings: list[dict],
) -> None:
    """落盘解析产物、切块嵌入，并原子收敛解析状态。

    收敛用条件 UPDATE（`WHERE deleted_at IS NULL`）在同一事务内完成：
    解析期间被软删除的行 rowcount=0，chunk 写入与产物登记一并回滚，
    不留孤儿数据；未删除则整体提交。
    """
    parsed_key = parsed_text_key(upload.id)
    await storage.put(parsed_key, json.dumps(text).encode("utf-8"))

    if upload.source_type in VECTORIZED_SOURCE_TYPES:
        chunker = Chunker(
            chunk_size=settings.chunk_size,
            overlap=settings.chunk_overlap,
        )
        # CPU 密集：下放线程池执行，避免阻塞事件循环（大文本切块可达秒级）
        chunks = await asyncio.to_thread(
            chunker.chunk_pdf, text, source_type=upload.source_type
        )
        if chunks:
            embedder = EmbeddingClient.from_settings()
            embeddings = await embedder.embed([c.text for c in chunks])
            _bind_chunk_metadata(chunks, embeddings, upload=upload)
            # 复用 db 事务：chunk 写入失败时与解析产物登记一起回滚
            await PgVectorStore(AsyncSessionLocal).upsert(chunks, session=db)

    result = await db.execute(
        update(Upload)
        .where(Upload.id == upload.id, Upload.deleted_at.is_(None))
        .values(
            parsed_key=parsed_key,
            char_count=char_count,
            parse_status="succeeded",
            parse_warnings=warnings,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        # 解析期间行已被软删除：丢弃本事务全部写入，并尽力清理已落盘的解析产物
        await db.rollback()
        try:
            await storage.delete(parsed_key)
        except Exception:  # noqa: BLE001
            logger.warning("孤儿解析产物清理失败（key=%s）", parsed_key, exc_info=True)
        return
    await db.commit()


def _bind_chunk_metadata(
    chunks: list[Chunk],
    embeddings: list[list[float]],
    *,
    upload: Upload,
) -> None:
    """把上传件元信息与嵌入向量绑定到 chunk（zip 严格等长，多则抛错）。"""
    for chunk, embedding in zip(chunks, embeddings, strict=True):
        chunk.upload_id = upload.id
        chunk.source_type = upload.source_type
        chunk.embedding = embedding


def start_parse_task(upload_id: uuid.UUID) -> None:
    """异步启动解析任务（同 id 在途任务不重复入队）。"""
    if is_running(upload_id):
        return
    register(upload_id, _run_parse_upload(upload_id))


async def _run_parse_upload(upload_id: uuid.UUID) -> None:
    """包装 parse_upload：意外失败记日志并收敛为 failed。"""
    try:
        await parse_upload(upload_id)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await log_error(
            job_id=str(upload_id),
            exc=exc,
            stage="upload_parsing",
            context={"upload_id": str(upload_id)},
        )
        try:
            async with AsyncSessionLocal() as db:
                upload = await db.get(Upload, upload_id)
                if upload is not None and upload.parse_status not in (
                    "succeeded",
                    "failed",
                ):
                    upload.parse_status = "failed"
                    upload.parse_error = str(exc)
                    await db.commit()
        except Exception:
            logger.exception("解析任务失败兜底处理时再次出错（upload=%s）", upload_id)


async def recover_pending_uploads() -> int:
    """服务启动时把非终态的文件类上传件重新入队（进程内任务随重启丢失）。

    解析写入幂等，重跑安全；软删除与文本类行不入队。
    """
    async with AsyncSessionLocal() as db:
        upload_ids = (
            await db.scalars(
                select(Upload.id).where(
                    Upload.deleted_at.is_(None),
                    Upload.parse_status.in_(["pending", "parsing"]),
                    Upload.source_type.in_(FILE_SOURCE_TYPES),
                )
            )
        ).all()

    for upload_id in upload_ids:
        start_parse_task(upload_id)
    return len(upload_ids)
