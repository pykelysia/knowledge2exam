"""过滤表达式构造器。

集中管理所有检索过滤逻辑，确保跨校隔离策略统一落地。
"""

from __future__ import annotations

import uuid

from app.core.enums import SourceType


class FilterBuilder:
    """检索过滤条件构造器。"""

    @staticmethod
    def additive(
        user_id: uuid.UUID,
        school_id: uuid.UUID,
        course_id: uuid.UUID,
        source_type: SourceType,
        upload_ids: list[uuid.UUID],
    ) -> dict:
        """叠加类：本次上传 OR 共享库同校同课程。

        叠加类（book / lecture / note）无论用户本次是否提供，都叠加共享库内容。
        """
        return {
            "or": [
                {"and": [
                    {"in": {"upload_id": upload_ids}},
                    {"eq": {"source_type": source_type.value}},
                ]},
                {"and": [
                    {"eq": {"school_id": school_id}},
                    {"eq": {"course_id": course_id}},
                    {"eq": {"source_type": source_type.value}},
                    {"eq": {"is_shared": True}},
                ]},
            ],
            "user_scope": user_id,
        }

    @staticmethod
    def exclusive(
        user_id: uuid.UUID,
        school_id: uuid.UUID,
        course_id: uuid.UUID,
        source_type: SourceType,
        upload_ids: list[uuid.UUID],
    ) -> dict:
        """排他类：用户提供则只用用户的，否则用共享库。

        排他类（keypoint_list / past_paper）用户本次提供后即不再引入共享库同类内容。
        注意：往期试卷实际不走向量检索，此方法保留用于 filter 一致性。
        """
        own = [uid for uid in upload_ids]  # 实际应由调用方传入已提供的 upload_ids
        if own:
            return {
                "and": [
                    {"in": {"upload_id": own}},
                    {"eq": {"source_type": source_type.value}},
                ],
                "user_scope": user_id,
            }
        return {
            "and": [
                {"eq": {"school_id": school_id}},
                {"eq": {"course_id": course_id}},
                {"eq": {"source_type": source_type.value}},
                {"eq": {"is_shared": True}},
            ],
            "user_scope": user_id,
        }

    @staticmethod
    def user_scope(user_id: uuid.UUID) -> dict:
        """跨用户隔离：个人内容限 user_id，共享内容不限但需 is_shared=true。"""
        return {
            "or": [
                {"eq": {"user_id": user_id}},
                {"eq": {"is_shared": True}},
            ]
        }

    @staticmethod
    def merge(*filters: dict) -> dict:
        """合并多个过滤条件。"""
        if not filters:
            return {}
        if len(filters) == 1:
            return filters[0]

        merged_parts: list[dict] = []
        for f in filters:
            if f:
                merged_parts.append(f)

        return {"and": merged_parts} if merged_parts else {}
