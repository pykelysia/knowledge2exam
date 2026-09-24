"""课程偏好服务：作用域条目加载与提炼决策执行（编排层与 agent 之间的桥）。

出题与修订两条链路在此加载 (school_id, course_id) 作用域的偏好条目；
修订轮勾选沉淀后在此执行 PreferenceDecision（ADD/UPDATE/DELETE/NOOP）。
本模块只做 DB 读写与上限检查，不调用 LLM。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.preference import PreferenceDecision
from app.config import settings
from app.models.job import Job
from app.models.revision import PaperRevision
from app.models.skill import CoursePreference


@dataclass(slots=True)
class PreferenceApplyResult:
    """一轮偏好沉淀的执行结果。"""

    saved: bool
    note: str
    # True 时调用方应以 warning 事件提示（目前仅上限触顶场景）
    warn: bool = False


async def list_course_preferences(
    db: AsyncSession, school_id: uuid.UUID, course_id: uuid.UUID
) -> list[CoursePreference]:
    """读取作用域内全部偏好条目（旧→新）。"""
    rows = (
        await db.scalars(
            select(CoursePreference)
            .where(
                CoursePreference.school_id == school_id,
                CoursePreference.course_id == course_id,
            )
            .order_by(CoursePreference.created_at, CoursePreference.id)
        )
    ).all()
    return list(rows)


async def apply_preference_decision(
    db: AsyncSession,
    *,
    job: Job,
    revision: PaperRevision,
    decision: PreferenceDecision,
    fallback_content: str = "",
) -> PreferenceApplyResult:
    """按决策操作 course_preference；不 commit（由调用方控制事务边界）。

    决策异常（target_id 不属于本作用域、update 缺内容等）一律降级为 add，
    不让坏决策丢掉用户的沉淀请求。
    """
    if decision.action == "noop":
        return PreferenceApplyResult(saved=False, note="已有等价偏好或无需泛化，未保存")

    if decision.action == "update":
        row = await _scoped_get(db, job, decision.target_id)
        content = decision.content.strip() or fallback_content.strip()
        if row is not None and content:
            row.content = content
            return PreferenceApplyResult(saved=True, note="已合并进既有偏好条目")
        # 目标不存在或内容为空 → 降级为新增

    if decision.action == "delete":
        row = await _scoped_get(db, job, decision.target_id)
        if row is not None:
            await db.delete(row)
            return PreferenceApplyResult(saved=True, note="已按反馈移除既有偏好条目")
        return PreferenceApplyResult(saved=False, note="目标偏好条目不存在，未变更")

    # add（含 update 的降级路径）
    content = decision.content.strip() or fallback_content.strip()
    if not content:
        return PreferenceApplyResult(saved=False, note="偏好内容为空，未保存")

    count = (
        await db.scalar(
            select(func.count())
            .select_from(CoursePreference)
            .where(
                CoursePreference.school_id == job.school_id,
                CoursePreference.course_id == job.course_id,
            )
        )
        or 0
    )
    if count >= settings.max_course_preferences:
        return PreferenceApplyResult(
            saved=False,
            note=f"本课程偏好条数已达上限（{settings.max_course_preferences}），本次未保存",
            warn=True,
        )

    db.add(
        CoursePreference(
            school_id=job.school_id,
            course_id=job.course_id,
            content=content,
            source_revision_id=revision.id,
            created_by=job.user_id,
        )
    )
    return PreferenceApplyResult(saved=True, note="已沉淀为课程出题偏好")


async def _scoped_get(
    db: AsyncSession, job: Job, target_id: uuid.UUID | None
) -> CoursePreference | None:
    """按 id 取条目并校验属于当前 job 的作用域（防跨课程误改）。"""
    if target_id is None:
        return None
    row = await db.get(CoursePreference, target_id)
    if row is None or row.school_id != job.school_id or row.course_id != job.course_id:
        return None
    return row
