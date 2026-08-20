"""学校 / 课程目录端点（无需鉴权）。"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.exceptions import JobNotFound
from app.models.catalog import Course, School
from app.models.resource import Resource
from app.schemas.catalog import Course as CourseSchema
from app.schemas.catalog import CoursesResponse, SchoolsResponse
from app.schemas.catalog import School as SchoolSchema

router = APIRouter(tags=["Catalog"])


@router.get("/schools", response_model=SchoolsResponse)
async def list_schools(db: AsyncSession = Depends(get_db)) -> SchoolsResponse:
    rows = (await db.scalars(select(School).order_by(School.name))).all()
    return SchoolsResponse(
        schools=[SchoolSchema(school_id=s.id, name=s.name) for s in rows]
    )


@router.get("/schools/{school_id}/courses", response_model=CoursesResponse)
async def list_courses(
    school_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CoursesResponse:
    school = await db.get(School, school_id)
    if school is None:
        raise JobNotFound()

    # 每门课程共享库资源数（is_shared = true）
    counts = dict(
        (
            await db.execute(
                select(Resource.course_id, func.count(Resource.id))
                .where(
                    Resource.school_id == school_id,
                    Resource.is_shared.is_(True),
                )
                .group_by(Resource.course_id)
            )
        ).all()
    )

    rows = (
        await db.scalars(
            select(Course).where(Course.school_id == school_id).order_by(Course.name)
        )
    ).all()
    return CoursesResponse(
        courses=[
            CourseSchema(
                course_id=c.id,
                name=c.name,
                shared_resource_count=counts.get(c.id, 0),
            )
            for c in rows
        ]
    )
