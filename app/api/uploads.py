"""上传件端点：文件上传（multipart）与文本提交（JSON）。"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.enums import FILE_SOURCE_TYPES, SourceType
from app.core.exceptions import (
    AppException,
    ErrorCode,
    UploadInUse,
    UploadNotFound,
)
from app.core.storage import storage
from app.ingestion.parsers.base import SUPPORTED_EXTENSIONS
from app.models.job import JobUpload
from app.models.resource import Resource
from app.models.upload import Upload
from app.models.user import AppUser
from app.schemas.upload import TextUploadCreate
from app.schemas.upload import Upload as UploadSchema

router = APIRouter(tags=["Uploads"])

_ALLOWED_EXTENSIONS_FOR_MSG = sorted(SUPPORTED_EXTENSIONS)
_READ_CHUNK_SIZE = 1024 * 1024


def _file_source_type(value: str) -> SourceType:
    try:
        st = SourceType(value)
    except ValueError as exc:
        raise AppException(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"未知的 source_type：{value}",
            detail={"allowed": [t.value for t in FILE_SOURCE_TYPES]},
        ) from exc
    if st not in FILE_SOURCE_TYPES:
        raise AppException(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"文件上传不支持 source_type={value}",
            detail={"allowed": [t.value for t in FILE_SOURCE_TYPES]},
        )
    return st


async def _read_capped(file: Any, max_bytes: int) -> bytes:
    """分块读取上传流，超限立即中断，避免超大请求整体载入内存。"""
    blocks: list[bytes] = []
    total = 0
    while True:
        block = await file.read(_READ_CHUNK_SIZE)
        if not block:
            break
        total += len(block)
        if total > max_bytes:
            raise AppException(
                ErrorCode.FILE_TOO_LARGE,
                f"文件超出大小上限（{max_bytes} 字节）",
                detail={"max_bytes": max_bytes},
            )
        blocks.append(block)
    return b"".join(blocks)


async def _store_file(
    db: AsyncSession,
    user: AppUser,
    source_type: SourceType,
    filename: str,
    data: bytes,
    shareable: bool,
    school_id: uuid.UUID | None = None,
    course_id: uuid.UUID | None = None,
) -> UploadSchema:
    if len(data) > settings.max_upload_size_bytes:
        raise AppException(
            ErrorCode.FILE_TOO_LARGE,
            f"文件超出大小上限（{settings.max_upload_size_bytes} 字节）",
            detail={"max_bytes": settings.max_upload_size_bytes},
        )

    ext = Path(filename).suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise AppException(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"不支持的文件格式：{ext}",
            detail={"filename": filename, "allowed": _ALLOWED_EXTENSIONS_FOR_MSG},
        )

    upload = Upload(
        user_id=user.id,
        source_type=source_type,
        filename=filename,
        mime_type="application/octet-stream",
        size_bytes=len(data),
        storage_key=f"uploads/{user.id}/{uuid.uuid4()}{ext}",
        shareable=shareable,
    )
    await storage.put(upload.storage_key, data)
    db.add(upload)
    await db.flush()

    # 解析不在上传时执行（上传只落盘登记），统一推迟到任务预处理阶段
    resource = Resource(
        upload_id=upload.id,
        source_type=source_type,
        school_id=school_id,
        course_id=course_id,
        parsed_key=None,
        char_count=None,
        is_shared=shareable and school_id is not None and course_id is not None,
    )
    db.add(resource)

    await db.commit()
    await db.refresh(upload)

    return UploadSchema(
        upload_id=upload.id,
        source_type=upload.source_type,
        filename=upload.filename,
        size_bytes=upload.size_bytes,
        shareable=upload.shareable,
    )


async def _store_text(
    db: AsyncSession,
    user: AppUser,
    source_type: SourceType,
    raw_text: str,
) -> UploadSchema:
    upload = Upload(
        user_id=user.id,
        source_type=source_type,
        raw_text=raw_text,
        shareable=False,
    )
    db.add(upload)
    await db.flush()

    resource = Resource(
        upload_id=upload.id,
        source_type=source_type,
        char_count=len(raw_text),
        is_shared=False,
    )
    db.add(resource)

    await db.commit()
    await db.refresh(upload)

    return UploadSchema(
        upload_id=upload.id,
        source_type=upload.source_type,
        filename=None,
        size_bytes=None,
        shareable=False,
    )


@router.post("/uploads", response_model=UploadSchema, status_code=201)
async def create_upload(
    request: Request,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UploadSchema:
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("multipart/form-data"):
        # 请求体级预检：超限请求在读入任何内容前直接拒绝
        content_length = request.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > (
            settings.max_upload_size_bytes
        ):
            raise AppException(
                ErrorCode.FILE_TOO_LARGE,
                f"请求体超出大小上限（{settings.max_upload_size_bytes} 字节）",
                detail={"max_bytes": settings.max_upload_size_bytes},
            )

        form = await request.form()
        file = form.get("file")
        if file is None:
            raise AppException(ErrorCode.INPUT_EMPTY, "缺少 file 字段")
        source_type_raw = form.get("source_type")
        if source_type_raw is None:
            raise AppException(ErrorCode.INPUT_EMPTY, "缺少 source_type 字段")
        shareable_raw = form.get("shareable", "false")
        shareable = str(shareable_raw).lower() in {"true", "1", "yes"}

        source_type = _file_source_type(str(source_type_raw))
        filename = getattr(file, "filename", None) or "upload"
        data = await _read_capped(file, settings.max_upload_size_bytes)

        return await _store_file(
            db, user, source_type, filename, data, shareable
        )

    if content_type.startswith("application/json"):
        try:
            body = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise AppException(ErrorCode.INPUT_EMPTY, "请求体不是合法 JSON") from exc
        try:
            payload = TextUploadCreate(**body)
        except Exception as exc:  # noqa: BLE001
            raise AppException(ErrorCode.INPUT_EMPTY, "请求体不合法") from exc
        if "shareable" in body:
            raise AppException(
                ErrorCode.SHARE_NOT_ALLOWED,
                "手动输入类内容不接受 shareable 字段",
            )
        if (
            payload.source_type == SourceType.extra_requirement
            and len(payload.raw_text) > settings.max_extra_requirement_chars
        ):
            raise AppException(
                ErrorCode.INPUT_EMPTY,
                f"额外要求超过长度上限（{settings.max_extra_requirement_chars} 字符）",
                detail={"max_chars": settings.max_extra_requirement_chars},
            )
        return await _store_text(db, user, payload.source_type, payload.raw_text)

    raise AppException(ErrorCode.INPUT_EMPTY, "不支持的内容类型")


@router.delete("/uploads/{upload_id}", status_code=204)
async def delete_upload(
    upload_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    upload = await db.get(Upload, upload_id)
    if upload is None or upload.user_id != user.id:
        raise UploadNotFound()

    # 已被任务引用则不可删除
    in_use = await db.scalar(
        select(JobUpload.job_id).where(JobUpload.upload_id == upload_id).limit(1)
    )
    if in_use is not None:
        raise UploadInUse()

    if upload.storage_key is not None:
        await storage.delete(upload.storage_key)

    await db.delete(upload)
    await db.commit()
