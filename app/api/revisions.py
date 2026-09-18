"""修订端点：划选反馈提交与会话历史。"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.exceptions import JobNotFinished, JobNotFound, RevisionInProgress
from app.core.task_registry import is_running
from app.models.job import Job
from app.models.user import AppUser
from app.orchestration.revision import REVISIONABLE_STATUSES, list_revisions, start_revision
from app.schemas.revision import (
    RevisionAccepted,
    RevisionCreate,
    RevisionItem,
    RevisionsResponse,
    SelectionPayload,
)

router = APIRouter(tags=["Revisions"])

logger = logging.getLogger(__name__)


async def _get_owned_job(db: AsyncSession, job_id: uuid.UUID, user: AppUser) -> Job:
    job = await db.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise JobNotFound()
    return job


def _to_item(row: object) -> RevisionItem:  # noqa: ANN001 — PaperRevision ORM 行
    selection = None
    if getattr(row, "selection", None):
        try:
            selection = SelectionPayload.model_validate(row.selection)
        except Exception:  # noqa: BLE001 — 历史行的 selection 兜底为缺失
            selection = None
    return RevisionItem(
        revision_id=row.id,
        round_no=row.round_no,
        status=row.status,
        selection=selection,
        feedback=row.feedback,
        summary=row.summary,
        error=row.error,
        created_at=row.created_at,
        applied_at=row.applied_at,
    )


@router.post("/jobs/{job_id}/revisions", response_model=RevisionAccepted, status_code=202)
async def create_revision(
    job_id: uuid.UUID,
    payload: RevisionCreate,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RevisionAccepted:
    """提交一轮修订：划选位置与内容 + 用户要求，交回原 agent 续跑。"""
    job = await _get_owned_job(db, job_id, user)
    if job.status not in REVISIONABLE_STATUSES:
        raise JobNotFinished()
    if is_running(job_id):
        raise RevisionInProgress()

    revision = await start_revision(db, job, payload.selection.model_dump(), payload.feedback)
    return RevisionAccepted(
        revision_id=revision.id,
        round_no=revision.round_no,
        status=revision.status,
    )


@router.get("/jobs/{job_id}/revisions", response_model=RevisionsResponse)
async def list_job_revisions(
    job_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> RevisionsResponse:
    """修订会话历史（新→旧）。"""
    await _get_owned_job(db, job_id, user)
    rows = await list_revisions(db, job_id)
    return RevisionsResponse(
        job_id=job_id,
        total=len(rows),
        revisions=[_to_item(r) for r in rows],
    )
