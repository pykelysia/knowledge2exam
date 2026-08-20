"""ORM 模型汇总导出。

Alembic 的 autogenerate 依赖 `Base.metadata`，因此必须在迁移环境导入全部模型，
这里集中导出便于 `alembic/env.py` 一次 import。
"""

from app.models.auth import RefreshToken
from app.models.base import Base
from app.models.catalog import Course, School
from app.models.job import Job, JobStage, JobUpload
from app.models.llm_call import LlmCall
from app.models.moderation import ModerationRecord
from app.models.plan import PlanItem
from app.models.question import Question, RetryLog
from app.models.resource import Resource
from app.models.upload import Upload
from app.models.user import AppUser

__all__ = [
    "Base",
    "AppUser",
    "RefreshToken",
    "School",
    "Course",
    "Upload",
    "Resource",
    "ModerationRecord",
    "Job",
    "JobUpload",
    "JobStage",
    "PlanItem",
    "Question",
    "RetryLog",
    "LlmCall",
]
