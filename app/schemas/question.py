"""题目相关 schema。"""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class Question(BaseModel):
    seq: int
    question_type: str
    stem: str
    options: dict[str, str] | None = None
    answer: str
    explanation: str | None = None
    sub_questions: list[str] | None = None
    sub_answers: list[str] | None = None


class QuestionsResponse(BaseModel):
    job_id: uuid.UUID
    need_explanation: bool = False
    total: int = 0
    questions: list[Question] = []
