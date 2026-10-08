"""过滤表达式构造器。

集中管理检索过滤逻辑。任务检索采用白名单语义：
只在任务显式选中的上传件集合内检索，白名单之外的内容在
SQL 层面即不可达，无需额外的跨用户隔离分支。
`tools._knowledge_filter` 组合本模块构造最终检索条件。
"""

from __future__ import annotations

import uuid

from app.core.enums import SourceType


class FilterBuilder:
    """检索过滤条件构造器。"""

    @staticmethod
    def whitelist(
        upload_ids: list[uuid.UUID],
        source_types: list[SourceType],
    ) -> dict:
        """任务检索白名单：限定选中的上传件 × 可检索的内容类型。"""
        return {
            "and": [
                {"in": {"upload_id": [str(u) for u in upload_ids]}},
                {
                    "in": {
                        "source_type": [st.value for st in source_types],
                    }
                },
            ]
        }
