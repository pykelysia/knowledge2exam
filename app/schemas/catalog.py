"""学校 / 课程目录 schema。"""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class School(BaseModel):
    school_id: uuid.UUID
    name: str


class SchoolsResponse(BaseModel):
    schools: list[School]


class Course(BaseModel):
    course_id: uuid.UUID
    name: str
    # 该课程在公开池（本校 + 全局可见）中的活跃文件数
    pool_file_count: int = 0


class CoursesResponse(BaseModel):
    courses: list[Course]
