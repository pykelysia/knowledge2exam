"""公开池端点：浏览 / 检索（关键词 ∪ 语义） / 保存与取消保存引用。

可见性谓词：`deleted_at IS NULL AND visibility != 'private' AND
(public OR (school AND 文件学校 == 观看者学校))`，排除本人上传。
保存为引用级（file_import 授权行），不复制数据；原件删除后
个人库条目标记失效。
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import delete, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal, get_db
from app.core.deps import get_current_user
from app.core.enums import FILE_SOURCE_TYPES, SourceType, Visibility
from app.core.exceptions import ImportNotAllowed, UploadNotFound
from app.models.catalog import Course, School
from app.models.library import FileImport
from app.models.upload import Upload
from app.models.user import AppUser
from app.retrieval.vector_store import PgVectorStore
from app.schemas.pool import ImportAccepted, PoolFile, PoolFilesResponse
from app.schemas.upload import FileOwner

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Pool"])

# 语义检索每次取回的 chunk 上限（按 upload 聚合前）
_SEMANTIC_TOP_K = 30
# 池内候选集上限（按创建时间取最新，防止内存聚合失控）
_POOL_CANDIDATE_LIMIT = 500
_DEFAULT_PAGE_SIZE = 20


def _pool_visibility_condition(user: AppUser):
    """池内可见谓词：全局公开，或本校可见且与观看者同校。

    观看者未设置学校归属时，本校可见文件对其不可见。
    """
    if user.school_id is None:
        return Upload.visibility == Visibility.public
    return or_(
        Upload.visibility == Visibility.public,
        (Upload.visibility == Visibility.school)
        & (Upload.school_id == user.school_id),
    )


@router.get("/pool/files", response_model=PoolFilesResponse)
async def list_pool_files(
    q: str | None = Query(None, description="关键词（文件名/学校/课程）或语义查询"),
    school_id: uuid.UUID | None = Query(None),
    course_id: uuid.UUID | None = Query(None),
    source_type: SourceType | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(_DEFAULT_PAGE_SIZE, ge=1, le=100),
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PoolFilesResponse:
    # 1. 候选集：池内可见（排除本人上传）+ 标签/类型过滤，最新在前
    conditions = [
        Upload.deleted_at.is_(None),
        Upload.user_id != user.id,
        Upload.source_type.in_(FILE_SOURCE_TYPES),
        _pool_visibility_condition(user),
    ]
    if school_id is not None:
        conditions.append(Upload.school_id == school_id)
    if course_id is not None:
        conditions.append(Upload.course_id == course_id)
    if source_type is not None:
        conditions.append(Upload.source_type == source_type)

    rows = (
        await db.execute(
            select(Upload, School.name, Course.name, FileImport.id)
            .outerjoin(School, School.id == Upload.school_id)
            .outerjoin(Course, Course.id == Upload.course_id)
            .outerjoin(
                FileImport,
                (FileImport.upload_id == Upload.id) & (FileImport.user_id == user.id),
            )
            .where(*conditions)
            .order_by(Upload.created_at.desc())
            .limit(_POOL_CANDIDATE_LIMIT)
        )
    ).all()

    candidates = [
        {
            "upload": row[0],
            "school_name": row[1],
            "course_name": row[2],
            "is_saved": row[3] is not None,
        }
        for row in rows
    ]

    # 2. 检索：无 q 直接分页；有 q = 关键词命中 ∪ 语义命中（语义降级安全）
    if q and q.strip():
        keyword = q.strip().lower()
        by_keyword = [
            c
            for c in candidates
            if keyword in (c["upload"].filename or "").lower()
            or keyword in (c["school_name"] or "").lower()
            or keyword in (c["course_name"] or "").lower()
        ]
        semantic_scores = await _semantic_scores(
            [c["upload"].id for c in candidates], q.strip()
        )
        keyword_ids = {c["upload"].id for c in by_keyword}
        semantic_only = [
            {**c, "semantic_score": semantic_scores[c["upload"].id]}
            for c in candidates
            if c["upload"].id in semantic_scores and c["upload"].id not in keyword_ids
        ]
        # 关键词命中在前（按时间），语义补充在后（按得分）
        selected = [{**c, "semantic_score": None} for c in by_keyword] + sorted(
            semantic_only,
            key=lambda c: c["semantic_score"],
            reverse=True,
        )
    else:
        selected = [{**c, "semantic_score": None} for c in candidates]

    total = len(selected)
    start = (page - 1) * page_size
    page_items = selected[start : start + page_size]

    # 3. owner 信息
    owner_ids = {c["upload"].user_id for c in page_items}
    owners: dict[uuid.UUID, AppUser] = {}
    if owner_ids:
        owner_rows = (
            await db.scalars(select(AppUser).where(AppUser.id.in_(owner_ids)))
        ).all()
        owners = {o.id: o for o in owner_rows}

    files = []
    for item in page_items:
        upload: Upload = item["upload"]
        owner = owners.get(upload.user_id)
        files.append(
            PoolFile(
                upload_id=upload.id,
                source_type=upload.source_type,
                filename=upload.filename,
                size_bytes=upload.size_bytes,
                visibility=upload.visibility,
                school_id=upload.school_id,
                course_id=upload.course_id,
                school_name=item["school_name"],
                course_name=item["course_name"],
                char_count=upload.char_count,
                created_at=upload.created_at,
                owner=FileOwner(
                    user_id=upload.user_id,
                    username=owner.username if owner else "未知用户",
                ),
                is_saved=item["is_saved"],
                semantic_score=item["semantic_score"],
            )
        )

    return PoolFilesResponse(files=files, total=total, page=page, page_size=page_size)


async def _semantic_scores(
    upload_ids: list[uuid.UUID], query: str
) -> dict[uuid.UUID, float]:
    """对候选集合做内容语义检索，返回 upload_id -> 最高相似度。

    embedding 服务不可用或检索失败时静默降级（返回空，仅关键词生效）。
    """
    if not upload_ids:
        return {}
    try:
        result = await PgVectorStore(AsyncSessionLocal).search(
            query=query,
            filter_expr={
                "and": [
                    {"in": {"upload_id": [str(u) for u in upload_ids]}},
                    {"in": {"source_type": ["book", "lecture", "note"]}},
                ]
            },
            top_k=_SEMANTIC_TOP_K,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("公开池语义检索降级为关键词检索: %s", exc)
        return {}

    scores: dict[uuid.UUID, float] = {}
    for chunk in result.chunks:
        upload_id = uuid.UUID(chunk["upload_id"])
        score = chunk.get("similarity", 0.0)
        if score > scores.get(upload_id, 0.0):
            scores[upload_id] = score
    return scores


def _pool_visible(user: AppUser, upload: Upload) -> bool:
    if upload.visibility == Visibility.public:
        return True
    return (
        upload.visibility == Visibility.school
        and upload.school_id is not None
        and user.school_id == upload.school_id
    )


@router.post("/pool/files/{upload_id}/import", response_model=ImportAccepted)
async def save_pool_file(
    upload_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ImportAccepted:
    """保存公开池文件到个人库（引用级，不复制数据；幂等）。"""
    upload = await db.get(Upload, upload_id)
    if upload is None or upload.deleted_at is not None:
        raise UploadNotFound()
    if upload.user_id == user.id:
        raise ImportNotAllowed()
    if not _pool_visible(user, upload):
        raise ImportNotAllowed()

    stmt = (
        insert(FileImport)
        .values(user_id=user.id, upload_id=upload_id)
        .on_conflict_do_nothing(index_elements=["user_id", "upload_id"])
    )
    await db.execute(stmt)
    await db.commit()
    return ImportAccepted(saved=True, upload_id=upload_id)


@router.delete("/pool/files/{upload_id}/import", status_code=204)
async def unsave_pool_file(
    upload_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """取消保存（移除个人库引用；幂等）。"""
    await db.execute(
        delete(FileImport).where(
            FileImport.user_id == user.id, FileImport.upload_id == upload_id
        )
    )
    await db.commit()
