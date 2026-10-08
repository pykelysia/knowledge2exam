"""学校 / 课程目录端点（无需鉴权）。

学校与课程是互相独立的标签：课程为全局实体，
按学校查课程的嵌套接口已由全局课程列表取代。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.enums import FILE_SOURCE_TYPES
from app.models.catalog import Course, School
from app.models.upload import Upload
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


@router.get("/courses", response_model=CoursesResponse)
async def list_courses(db: AsyncSession = Depends(get_db)) -> CoursesResponse:
    """全局课程列表，附每课程公开池（本校 + 全局可见）的活跃文件数。"""

    # extra_requirement 属 TEXT 类型本就不在 FILE_SOURCE_TYPES 内，此处直接全量
    pool_types = list(FILE_SOURCE_TYPES)

    counts = dict(
        (
            await db.execute(
                select(Upload.course_id, func.count(Upload.id))
                .where(
                    Upload.deleted_at.is_(None),
                    Upload.course_id.is_not(None),
                    Upload.visibility != "private",
                    Upload.source_type.in_(pool_types),
                )
                .group_by(Upload.course_id)
            )
        ).all()
    )

    rows = (await db.scalars(select(Course).order_by(Course.name))).all()
    return CoursesResponse(
        courses=[
            CourseSchema(
                course_id=c.id,
                name=c.name,
                pool_file_count=counts.get(c.id, 0),
            )
            for c in rows
        ]
    )
