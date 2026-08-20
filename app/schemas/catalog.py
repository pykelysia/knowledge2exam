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
    shared_resource_count: int = 0


class CoursesResponse(BaseModel):
    courses: list[Course]
