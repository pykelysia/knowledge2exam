"""上传件端点：文件上传（multipart）与文本提交（JSON）。"""

from __future__ import annotations

import uuid
from pathlib import Path

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
from app.ingestion.parsers.base import SUPPORTED_EXTENSIONS, StubParser
from app.models.job import JobUpload
from app.models.resource import Resource
from app.models.upload import Upload
from app.models.user import AppUser
from app.schemas.upload import Preview, TextUploadCreate
from app.schemas.upload import Upload as UploadSchema

router = APIRouter(tags=["Uploads"])

_ALLOWED_EXTENSIONS_FOR_MSG = sorted(SUPPORTED_EXTENSIONS)


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


async def _parse_and_store_file(
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
        parse_status="pending",
    )
    await storage.put(upload.storage_key, data)
    db.add(upload)
    await db.flush()

    # 解析（stub）
    parser = StubParser()
    try:
        result = await parser.parse(filename, data)
        upload.parse_status = "succeeded"
        preview = Preview(
            char_count=result.char_count,
            page_count=result.page_count,
            excerpt=result.excerpt,
        )
    except Exception as exc:  # noqa: BLE001 —— 单文件失败隔离（NFR-5）
        upload.parse_status = "failed"
        upload.parse_error = str(exc)
        preview = None

    # 解析产物 → resource
    resource = Resource(
        upload_id=upload.id,
        source_type=source_type,
        school_id=school_id,
        course_id=course_id,
        parsed_key=None,
        char_count=result.char_count if preview else None,
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
        parse_status=upload.parse_status,
        parse_error=upload.parse_error,
        preview=preview,
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
        parse_status="succeeded",
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
        parse_status=upload.parse_status,
        parse_error=None,
        preview=Preview(char_count=len(raw_text), page_count=None, excerpt=raw_text[:200]),
    )


@router.post("/uploads", response_model=UploadSchema, status_code=201)
async def create_upload(
    request: Request,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UploadSchema:
    content_type = request.headers.get("content-type", "")

    if content_type.startswith("multipart/form-data"):
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
        data = await file.read()

        return await _parse_and_store_file(
            db, user, source_type, filename, data, shareable
        )

    if content_type.startswith("application/json"):
        body = await request.json()
        try:
            payload = TextUploadCreate(**body)
        except Exception as exc:  # noqa: BLE001
            raise AppException(ErrorCode.INPUT_EMPTY, "请求体不合法") from exc
        if "shareable" in body:
            raise AppException(
                ErrorCode.SHARE_NOT_ALLOWED,
                "手动输入类内容不接受 shareable 字段",
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
