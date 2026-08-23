"""往期试卷快读缓存。

任务级内存缓存，任务开始时加载解析后的全文，任务结束时释放。
规划与出题阶段可频繁读取，避免重复从对象存储加载。
"""

from __future__ import annotations

import uuid

from app.ingestion.parsers.base import ParseResult


class PastPaperCache:
    """任务级往期试卷缓存。"""

    def __init__(self) -> None:
        self._cache: dict[uuid.UUID, dict[str, ParseResult]] = {}

    def set(self, job_id: uuid.UUID, papers: dict[str, ParseResult]) -> None:
        """为某任务设置试卷缓存。"""
        self._cache[job_id] = papers

    def get(self, job_id: uuid.UUID) -> dict[str, ParseResult] | None:
        """获取某任务的试卷缓存。"""
        return self._cache.get(job_id)

    def release(self, job_id: uuid.UUID) -> None:
        """释放某任务的试卷缓存。"""
        self._cache.pop(job_id, None)

    def get_full_text(self, job_id: uuid.UUID) -> str | None:
        """获取某任务所有往期试卷的全文拼接（供 planner 使用）。"""
        papers = self.get(job_id)
        if not papers:
            return None
        return "\n\n".join(p.text for p in papers.values())


# 全局单例
past_paper_cache = PastPaperCache()
