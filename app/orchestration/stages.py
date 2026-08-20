"""模拟 pipeline：POST /jobs 后由 BackgroundTasks 触发的全流程推进。

首版不调用真实 LLM，按状态机依次推进并产出**模拟题目**与占位产物，
让前端能完整验证 SSE 进度流、轮询快照与 md/PDF 下载全链路。
真实实现接入 LangGraph 后，本模块被替换为编排层真实调度。
"""

from __future__ import annotations

import asyncio
import uuid

from app.config import settings
from app.core.db import AsyncSessionLocal
from app.core.storage import storage
from app.models.job import Job
from app.models.plan import PlanItem
from app.models.question import Question
from app.orchestration.events import EventBus
from app.orchestration.state_machine import JobStatus, Stage
from app.rendering.renderer import StubRenderer

# 模拟题量与题型分布（对应 agent-design.md 第 1 节的 20/20/60 模板）
_MOCK_TOTAL = 6
_MOCK_DISTRIBUTION = {"choice": 2, "blank": 2, "short_answer": 2}


async def _sleep() -> None:
    await asyncio.sleep(settings.mock_stage_delay_seconds)


async def run_mock_pipeline(job_id: uuid.UUID) -> None:
    """在独立会话中执行模拟生成流程。"""
    async with AsyncSessionLocal() as db:
        job = await db.get(Job, job_id)
        if job is None:
            return
        bus = EventBus(db)

        # pending -> preprocessing
        job.status = JobStatus.preprocessing.value
        await bus.emit(
            job_id, "stage_changed",
            {"stage": Stage.preprocessing.value, "previous": JobStatus.pending.value},
            stage=Stage.preprocessing.value,
        )
        await db.commit()
        await _sleep()

        # preprocessing -> planning
        job.status = JobStatus.planning.value
        await bus.emit(
            job_id, "stage_changed",
            {"stage": Stage.planning.value, "previous": Stage.preprocessing.value},
            stage=Stage.planning.value,
        )
        await db.commit()
        await _sleep()

        # plan_ready
        await bus.emit(
            job_id, "plan_ready",
            {
                "total": _MOCK_TOTAL,
                "distribution": _MOCK_DISTRIBUTION,
                "reference_used": "default_template",
                "duration_minutes": job.duration_minutes,
            },
            stage=Stage.planning.value,
        )
        job.planned_total = _MOCK_TOTAL
        await db.commit()
        await _sleep()

        # generating
        job.status = JobStatus.generating.value
        await bus.emit(
            job_id, "stage_changed",
            {"stage": Stage.generating.value, "previous": Stage.planning.value},
            stage=Stage.generating.value,
        )
        await db.commit()
        await _sleep()

        # 生成 plan_item + question（模拟）
        questions: list[dict] = []
        seq = 0
        type_specs = [
            ("choice", {"A": "正确选项", "B": "干扰项", "C": "干扰项", "D": "干扰项"}, "A"),
            ("choice", {"A": "干扰项", "B": "正确选项", "C": "干扰项", "D": "干扰项"}, "B"),
            ("blank", None, "单位冲激响应"),
            ("blank", None, "狄利克雷条件"),
            ("short_answer", None, "见各子问题答案"),
            ("short_answer", None, "见各子问题答案"),
        ]
        for question_type, options, answer in type_specs:
            seq += 1
            plan = PlanItem(
                job_id=job_id,
                seq=seq,
                question_type=question_type,
                knowledge_point=f"知识点 {seq}",
                exam_direction=f"考察方向 {seq}",
                difficulty="medium",
            )
            db.add(plan)
            await db.flush()

            stem = f"第 {seq} 题（{question_type}）模拟题干"
            explanation = f"第 {seq} 题解析" if job.need_explanation else None
            q = Question(
                job_id=job_id,
                plan_item_id=plan.id,
                seq=seq,
                question_type=question_type,
                stem=stem,
                options=options,
                answer=answer,
                sub_questions=["子问题 1", "子问题 2"] if question_type == "short_answer" else None,
                sub_answers=["子答案 1", "子答案 2"] if question_type == "short_answer" else None,
                explanation=explanation,
                status="accepted",
            )
            db.add(q)
            await db.flush()

            await bus.emit(
                job_id, "question_completed",
                {
                    "seq": seq,
                    "question_type": question_type,
                    "completed": seq,
                    "total": _MOCK_TOTAL,
                },
                stage=Stage.generating.value,
            )
            await db.commit()
            await _sleep()

            is_short = question_type == "short_answer"
            questions.append(
                {
                    "seq": seq,
                    "stem": stem,
                    "question_type": question_type,
                    "options": options,
                    "answer": answer,
                    "explanation": explanation,
                    "sub_questions": ["子问题 1", "子问题 2"] if is_short else None,
                    "sub_answers": ["子答案 1", "子答案 2"] if is_short else None,
                }
            )

        # reviewing（可选）
        if job.enable_review:
            job.status = JobStatus.reviewing.value
            await bus.emit(
                job_id, "stage_changed",
                {"stage": Stage.reviewing.value, "previous": Stage.generating.value},
                stage=Stage.reviewing.value,
            )
            await db.commit()
            await _sleep()

            await bus.emit(
                job_id, "review_result",
                {"checked": _MOCK_TOTAL, "passed": _MOCK_TOTAL, "rejected": [], "auto_fixed": []},
                stage=Stage.reviewing.value,
            )
            await db.commit()
            await _sleep()

        # rendering
        previous = Stage.reviewing if job.enable_review else Stage.generating
        job.status = JobStatus.rendering.value
        await bus.emit(
            job_id, "stage_changed",
            {"stage": Stage.rendering.value, "previous": previous.value},
            stage=Stage.rendering.value,
        )
        await db.commit()
        await _sleep()

        renderer = StubRenderer()
        result = await renderer.render(f"模拟试卷（{job.duration_minutes} 分钟）", questions)
        md_key = f"jobs/{job_id}/output/paper.md"
        pdf_key = f"jobs/{job_id}/output/paper.pdf"
        await storage.put(md_key, result.md)
        await storage.put(pdf_key, result.pdf)
        job.md_key = md_key
        job.pdf_key = pdf_key

        job.status = JobStatus.completed.value
        await db.commit()

        await bus.emit(
            job_id, "done",
            {
                "status": JobStatus.completed.value,
                "total": _MOCK_TOTAL,
                "abandoned": 0,
                "md_url": f"/api/v1/jobs/{job_id}/paper.md",
                "pdf_url": f"/api/v1/jobs/{job_id}/paper.pdf",
            },
            stage=Stage.rendering.value,
        )
        await db.commit()
