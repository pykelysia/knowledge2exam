"""产物下载端点：paper.md / paper.pdf。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.deps import get_current_user
from app.core.exceptions import JobNotFound, JobNotReady, RenderFailed
from app.core.storage import storage
from app.models.job import Job
from app.models.user import AppUser

router = APIRouter(tags=["Artifacts"])


@router.get("/jobs/{job_id}/paper.md")
async def download_paper_md(
    job_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    job = await db.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise JobNotFound()
    if job.md_key is None:
        raise JobNotReady()
    try:
        data = await storage.get(job.md_key)
    except Exception as exc:  # noqa: BLE001
        raise JobNotReady() from exc
    return Response(
        content=data,
        media_type="text/markdown; charset=utf-8",
    )


@router.get("/jobs/{job_id}/paper.pdf")
async def download_paper_pdf(
    job_id: uuid.UUID,
    user: AppUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    job = await db.get(Job, job_id)
    if job is None or job.user_id != user.id:
        raise JobNotFound()
    if job.pdf_key is None:
        # partially_completed 且 md 存在但 PDF 失败 → RENDER_FAILED
        if job.md_key is not None:
            raise RenderFailed()
        raise JobNotReady()
    try:
        data = await storage.get(job.pdf_key)
    except Exception as exc:  # noqa: BLE001
        raise JobNotReady() from exc
    return Response(content=data, media_type="application/pdf")
