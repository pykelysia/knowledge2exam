"""过滤表达式构造器。

集中管理检索过滤逻辑，确保跨校隔离策略统一落地。
`tools._knowledge_filter` 组合本模块构造最终检索条件。
"""

from __future__ import annotations

import uuid

from app.core.enums import SourceType


class FilterBuilder:
    """检索过滤条件构造器。"""

    @staticmethod
    def additive(
        school_id: uuid.UUID,
        course_id: uuid.UUID,
        source_type: SourceType,
        upload_ids: list[uuid.UUID],
    ) -> dict:
        """叠加类（book / lecture / note）：本次上传 OR 共享库同校同课程。"""
        branches: list[dict] = []
        if upload_ids:
            branches.append(
                {
                    "and": [
                        {"in": {"upload_id": [str(u) for u in upload_ids]}},
                        {"eq": {"source_type": source_type.value}},
                    ],
                }
            )
        branches.append(
            {
                "and": [
                    {"eq": {"school_id": str(school_id)}},
                    {"eq": {"course_id": str(course_id)}},
                    {"eq": {"source_type": source_type.value}},
                    {"eq": {"is_shared": True}},
                ],
            }
        )
        return {"or": branches} if len(branches) > 1 else branches[0]

    @staticmethod
    def user_scope(user_id: uuid.UUID) -> dict:
        """跨用户隔离：个人内容限 user_id，共享内容不限但需 is_shared=true。

        由调用方 AND 到最终条件上，作为与分支组合无关的兜底不变量。
        """
        return {
            "or": [
                {"eq": {"user_id": str(user_id)}},
                {"eq": {"is_shared": True}},
            ]
        }
