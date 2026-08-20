"""预置学校与课程数据。

用法：`python -m app.core.seed`（在已 `alembic upgrade head` 之后执行）。
幂等：已存在的学校/课程不重复插入。
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.catalog import Course, School

SEED_DATA: list[tuple[str, list[str]]] = [
    ("某大学", ["信号与系统", "高等数学", "大学物理", "电路分析"]),
    ("示例理工学院", ["数据结构", "操作系统"]),
]


async def seed() -> None:
    async with AsyncSessionLocal() as db:
        for school_name, course_names in SEED_DATA:
            school = await db.scalar(select(School).where(School.name == school_name))
            if school is None:
                school = School(id=uuid.uuid4(), name=school_name)
                db.add(school)
                await db.flush()

            existing = set(
                (
                    await db.scalars(
                        select(Course.name).where(Course.school_id == school.id)
                    )
                ).all()
            )
            for course_name in course_names:
                if course_name not in existing:
                    db.add(Course(id=uuid.uuid4(), school_id=school.id, name=course_name))
        await db.commit()


if __name__ == "__main__":
    asyncio.run(seed())
    print("seed 完成")
