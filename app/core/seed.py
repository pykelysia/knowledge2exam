"""预置学校与课程数据。

用法：`python -m app.core.seed`（在已 `alembic upgrade head` 之后执行）。
幂等：已存在的学校/课程不重复插入。

学校与课程互相独立：学校仅作归属标签，课程为全局实体。
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.catalog import Course, School

SEED_SCHOOLS: list[str] = ["某大学", "示例理工学院"]
SEED_COURSES: list[str] = [
    "信号与系统",
    "高等数学",
    "大学物理",
    "电路分析",
    "数据结构",
    "操作系统",
]


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        for school_name in SEED_SCHOOLS:
            if await db.scalar(select(School).where(School.name == school_name)) is None:
                db.add(School(id=uuid.uuid4(), name=school_name))
        existing_courses = set((await db.scalars(select(Course.name))).all())
        for course_name in SEED_COURSES:
            if course_name not in existing_courses:
                db.add(Course(id=uuid.uuid4(), name=course_name))
        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed())
    print("seed 完成")
