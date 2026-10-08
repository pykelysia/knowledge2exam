"""上传件与个人库端点。

- POST /uploads：文件（multipart，带可见性与标签，异步解析）或文本（JSON，即时登记）
- PATCH /uploads/{id}：修改可见性 / 标签
- DELETE /uploads/{id}：软删除（引用侧标记「文件已失效」）
- GET /files/mine：个人库（我的上传 + 公开池保存的引用）
- GET /files/{id}/preview：文件预览（个人库与公开池共用）
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.enums import (
    FILE_SOURCE_TYPES,
    TEXT_SOURCE_TYPES,
    SourceType,
    Visibility,
)
from app.core.exceptions import (
    AppException,
    CourseNotFound,
    ErrorCode,
    SchoolNotFound,
    ShareScopeRequired,
    UploadInUse,
    UploadNotFound,
    UploadNotParsed,
)
from app.core.storage import storage
from app.core.task_registry import cancel as cancel_task
from app.ingestion.parsers.base import SUPPORTED_EXTENSIONS
from app.ingestion.pipeline import parsed_text_key, start_parse_task
from app.models.catalog import Course, School
from app.models.chunk import Chunk as ChunkModel
from app.models.job import Job, JobUpload
from app.models.library import FileImport
from app.models.upload import Upload
from app.models.user import AppUser
from app.orchestration.state_machine import TERMINAL_STATUSES
from app.schemas.upload import (
    FileOwner,
    LibraryFile,
    LibraryResponse,
    TextUploadCreate,
    UploadPreview,
)
from app.schemas.upload import Upload as UploadSchema
from app.schemas.upload import UploadUpdate

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


def _visibility(value: str | None) -> Visibility:
    if value is None or value == "":
        return Visibility.private
    try:
        return Visibility(value)
    except ValueError as exc:
        raise AppException(
            ErrorCode.INPUT_EMPTY,
            f"未知的 visibility：{value}",
            detail={"allowed": [v.value for v in Visibility]},
        ) from exc


def _form_uuid(value: str | None, field: str) -> uuid.UUID | None:
    if value is None or value == "":
        return None
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise AppException(ErrorCode.INPUT_EMPTY, f"{field} 不是合法的 UUID：{value}") from exc


async def _validate_tags(
    db: AsyncSession,
    school_id: uuid.UUID | None,
    course_id: uuid.UUID | None,
    visibility: Visibility,
) -> None:
    """标签存在性校验 + 本校可见必须有学校。"""
    if visibility == Visibility.school and school_id is None:
        raise ShareScopeRequired("本校可见的文件必须选择学校")
    if school_id is not None and await db.get(School, school_id) is None:
        raise SchoolNotFound()
    if course_id is not None and await db.get(Course, course_id) is None:
        raise CourseNotFound()


def _file_status(upload: Upload) -> str:
    if upload.deleted_at is not None:
        return "deleted"
    if upload.parse_status in ("pending", "parsing"):
        return "parsing"
    if upload.parse_status == "failed":
        return "failed"
    return "available"


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


def _upload_schema(upload: Upload) -> UploadSchema:
    return UploadSchema(
        upload_id=upload.id,
        source_type=upload.source_type,
        filename=upload.filename,
        size_bytes=upload.size_bytes,
        visibility=upload.visibility,
        parse_status=upload.parse_status,
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
        visibility = _visibility(form.get("visibility"))
        school_id = _form_uuid(form.get("school_id"), "school_id")
        course_id = _form_uuid(form.get("course_id"), "course_id")

        source_type = _file_source_type(str(source_type_raw))
        await _validate_tags(db, school_id, course_id, visibility)
        filename = getattr(file, "filename", None) or "upload"
        data = await _read_capped(file, settings.max_upload_size_bytes)

        return await _store_file(
            db, user, source_type, filename, data, visibility, school_id, course_id
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
        if "visibility" in body:
            raise AppException(
                ErrorCode.SHARE_NOT_ALLOWED,
                "手动输入类内容不接受 visibility 字段",
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


async def _store_file(
    db: AsyncSession,
    user: AppUser,
    source_type: SourceType,
    filename: str,
    data: bytes,
    visibility: Visibility,
    school_id: uuid.UUID | None,
    course_id: uuid.UUID | None,
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
        visibility=visibility,
        school_id=school_id,
        course_id=course_id,
        parse_status="pending",
    )
    await storage.put(upload.storage_key, data)
    db.add(upload)
    await db.commit()
    await db.refresh(upload)

    # 上传即解析：异步执行，状态经 parse_status 收敛（个人库轮询可见）
    start_parse_task(upload.id)

    return _upload_schema(upload)


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
        visibility=Visibility.private,
        char_count=len(raw_text),
        parse_status="succeeded",
    )
    db.add(upload)
    await db.commit()
    await db.refresh(upload)

    return _upload_schema(upload)


@router.patch("/uploads/{upload_id}", response_model=UploadSchema)
async def update_upload(
    upload_id: uuid.UUID,
    payload: UploadUpdate,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UploadSchema:
    upload = await db.get(Upload, upload_id)
    if upload is None or upload.user_id != user.id or upload.deleted_at is not None:
        raise UploadNotFound()

    provided = payload.model_fields_set

    if "visibility" in provided and upload.source_type in TEXT_SOURCE_TYPES:
        raise AppException(
            ErrorCode.SHARE_NOT_ALLOWED, "手动输入类内容不可修改可见性"
        )

    visibility = payload.visibility if "visibility" in provided else upload.visibility
    if "school_id" in provided:
        upload.school_id = payload.school_id
    if "course_id" in provided:
        upload.course_id = payload.course_id

    await _validate_tags(db, upload.school_id, upload.course_id, visibility)
    if "visibility" in provided:
        upload.visibility = visibility

    await db.commit()
    await db.refresh(upload)
    return _upload_schema(upload)


@router.delete("/uploads/{upload_id}", status_code=204)
async def delete_upload(
    upload_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    upload = await db.get(Upload, upload_id)
    if upload is None or upload.user_id != user.id:
        raise UploadNotFound()
    if upload.deleted_at is not None:
        # 幂等：已删除直接成功
        return

    # 非终态任务仍在使用该文件时拒绝删除，防打断运行中的出卷
    in_use = await db.scalar(
        select(JobUpload.job_id)
        .join(Job, Job.id == JobUpload.job_id)
        .where(
            JobUpload.upload_id == upload_id,
            Job.status.not_in([s.value for s in TERMINAL_STATUSES]),
        )
        .limit(1)
    )
    if in_use is not None:
        raise UploadInUse()

    # 取消在途解析（管线内的软删除检查点会丢弃未提交产物）
    cancel_task(upload_id)

    upload.deleted_at = datetime.now(UTC)
    # 引用级失效语义：chunk 随软删除清理；个人库引用行保留并标记失效
    await db.execute(delete(ChunkModel).where(ChunkModel.upload_id == upload_id))
    await db.commit()

    # 原件与解析产物尽力清理（失败不影响删除语义）
    for key in (upload.storage_key, upload.parsed_key):
        if key:
            try:
                await storage.delete(key)
            except Exception:  # noqa: BLE001
                pass


@router.get("/files/mine", response_model=LibraryResponse)
async def list_my_files(
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> LibraryResponse:
    """个人库：我的上传（活跃）+ 公开池保存的引用（含已失效原件）。"""

    # 本人上传：活跃、非任务输入文本类
    own_rows = (
        await db.scalars(
            select(Upload)
            .where(
                Upload.user_id == user.id,
                Upload.deleted_at.is_(None),
                Upload.source_type.not_in(TEXT_SOURCE_TYPES),
            )
            .order_by(Upload.created_at.desc())
        )
    ).all()
    own_ids = {u.id for u in own_rows}

    # 保存的引用：含原件已删除的行（标记「文件已失效」）
    import_rows = (
        await db.execute(
            select(Upload, FileImport.created_at)
            .join(FileImport, FileImport.upload_id == Upload.id)
            .where(FileImport.user_id == user.id)
        )
    ).all()
    imported_at = {row[0].id: row[1] for row in import_rows}
    imported_uploads = {
        row[0].id: row[0] for row in import_rows if row[0].id not in own_ids
    }

    # 汇总条目并按创建时间倒序
    entries: list[tuple[Upload, bool]] = [(u, False) for u in own_rows]
    entries += [(u, True) for u in imported_uploads.values()]
    entries.sort(key=lambda e: e[0].created_at, reverse=True)

    owner_ids = {u.user_id for u, _ in entries}
    owners: dict[uuid.UUID, AppUser] = {}
    if owner_ids:
        rows = (
            await db.scalars(select(AppUser).where(AppUser.id.in_(owner_ids)))
        ).all()
        owners = {o.id: o for o in rows}

    files = []
    for upload, is_imported in entries:
        item = _library_file(
            upload,
            imported=is_imported,
            imported_at=imported_at.get(upload.id) if is_imported else None,
        )
        owner = owners.get(upload.user_id)
        item.owner = FileOwner(
            user_id=upload.user_id,
            username=owner.username if owner else "未知用户",
        )
        files.append(item)

    return LibraryResponse(files=files)


def _library_file(
    upload: Upload,
    *,
    imported: bool,
    imported_at: datetime | None,
) -> LibraryFile:
    return LibraryFile(
        upload_id=upload.id,
        source_type=upload.source_type,
        filename=upload.filename,
        size_bytes=upload.size_bytes,
        visibility=upload.visibility,
        school_id=upload.school_id,
        course_id=upload.course_id,
        char_count=upload.char_count,
        parse_status=upload.parse_status,
        parse_error=upload.parse_error,
        parse_warnings=upload.parse_warnings or [],
        created_at=upload.created_at,
        owner=FileOwner(user_id=upload.user_id, username=""),  # 由调用方补全
        imported=imported,
        imported_at=imported_at,
        status=_file_status(upload),
    )


async def _can_preview(db: AsyncSession, user: AppUser, upload: Upload) -> bool:
    """预览权限：owner / 已保存引用 / 池内可见（本校或全局）。"""
    if upload.user_id == user.id:
        return True
    saved = await db.scalar(
        select(FileImport.id).where(
            FileImport.user_id == user.id, FileImport.upload_id == upload.id
        )
    )
    if saved is not None:
        return True
    if upload.visibility == Visibility.public:
        return True
    return (
        upload.visibility == Visibility.school
        and upload.school_id is not None
        and user.school_id == upload.school_id
    )


@router.get("/files/{upload_id}/preview", response_model=UploadPreview)
async def preview_file(
    upload_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UploadPreview:
    upload = await db.get(Upload, upload_id)
    if (
        upload is None
        or upload.deleted_at is not None
        or not await _can_preview(db, user, upload)
    ):
        raise UploadNotFound()

    if upload.parse_status != "succeeded":
        raise UploadNotParsed()

    if upload.source_type in TEXT_SOURCE_TYPES:
        text = upload.raw_text or ""
    else:
        key = upload.parsed_key or parsed_text_key(upload.id)
        try:
            raw = await storage.get(key)
            text = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise UploadNotParsed("解析产物缺失或不可读，无法预览") from exc

    limit = settings.max_preview_chars
    truncated = len(text) > limit
    preview_text = text[:limit]

    school_name = course_name = None
    if upload.school_id is not None:
        school = await db.get(School, upload.school_id)
        school_name = school.name if school else None
    if upload.course_id is not None:
        course = await db.get(Course, upload.course_id)
        course_name = course.name if course else None

    return UploadPreview(
        upload_id=upload.id,
        filename=upload.filename,
        source_type=upload.source_type,
        visibility=upload.visibility,
        school_id=upload.school_id,
        course_id=upload.course_id,
        school_name=school_name,
        course_name=course_name,
        char_count=upload.char_count,
        text=preview_text,
        truncated=truncated,
    )
