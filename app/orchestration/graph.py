"""LangGraph 编排图（architecture.md 第 2 节 L2）。

状态流转：planner -> fanout -> [reviewer] -> rendering。
db/bus 等运行时对象通过节点闭包注入，不进入 State（避免序列化问题）。

首版使用 MemorySaver 作为 checkpointer；后续可换持久化 checkpointer。
为避免循环导入，节点内部使用延迟导入。
"""

from __future__ import annotations

import uuid
from typing import Any, Sequence

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver

from app.orchestration.events import EventBus
from app.orchestration.state_machine import JobStatus, Stage
from app.rendering.markdown import build_markdown
from app.rendering.renderer import StubRenderer


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class GraphState(dict):
    """LangGraph 状态：只包含可序列化的业务数据。"""

    job_id: uuid.UUID
    duration_minutes: int
    need_explanation: bool
    enable_review: bool
    plan_items: list[dict]
    questions: list[dict]
    context: dict
    error: str | None
    current_stage: str


# ---------------------------------------------------------------------------
# 节点工厂
# ---------------------------------------------------------------------------

def make_planner_node(db_factory, bus_factory):
    async def planner_node(state: GraphState) -> dict[str, Any]:
        from app.agents.planner import run_planner

        job_id = state["job_id"]
        async with db_factory() as db:
            bus = bus_factory(db)
            plan_items = await run_planner(
                db=db,
                job_id=job_id,
                bus=bus,
                context={
                    "duration_minutes": state.get("duration_minutes", 100),
                    "past_papers_full_text_or_none": state.get("context", {}).get("past_papers_full_text_or_none"),
                    "keypoint_list_or_none": state.get("context", {}).get("keypoint_list_or_none"),
                    "extra_requirement_or_none": state.get("context", {}).get("extra_requirement_or_none"),
                    "shared_library_summary": state.get("context", {}).get("shared_library_summary"),
                    "planner_model": state.get("context", {}).get("planner_model", "gpt-4o"),
                    "reference_used": state.get("context", {}).get("reference_used", "default_template"),
                },
            )
            return {
                "plan_items": plan_items,
                "current_stage": Stage.generating.value,
            }
    return planner_node


def make_fanout_node(db_factory, bus_factory):
    async def fanout_node(state: GraphState) -> dict[str, Any]:
        from app.orchestration.fanout import run_fanout
        from app.models.plan import PlanItem
        from sqlalchemy import select

        job_id = state["job_id"]
        async with db_factory() as db:
            bus = bus_factory(db)
            rows = (
                await db.scalars(
                    select(PlanItem).where(PlanItem.job_id == job_id).order_by(PlanItem.seq)
                )
            ).all()
            plan_items = list(rows)

            fanout_result = await run_fanout(
                db=db,
                job_id=job_id,
                bus=bus,
                plan_items=plan_items,
                need_explanation=state.get("need_explanation", False),
                writer_model=state.get("context", {}).get("writer_model", "gpt-4o-mini"),
                knowledge_context=state.get("context", {}).get("knowledge_context", ""),
                max_concurrent=state.get("context", {}).get("max_concurrent_writers", 4),
            )

            next_stage = Stage.reviewing.value if state.get("enable_review") else Stage.rendering.value
            return {
                "questions": [
                    *fanout_result.choice_questions,
                    *fanout_result.blank_questions,
                    *fanout_result.short_questions,
                ],
                "current_stage": next_stage,
            }
    return fanout_node


def make_reviewer_node(db_factory, bus_factory):
    async def reviewer_node(state: GraphState) -> dict[str, Any]:
        from app.agents.reviewer import run_reviewer
        from app.models.plan import PlanItem
        from app.models.question import Question
        from sqlalchemy import select

        job_id = state["job_id"]
        async with db_factory() as db:
            bus = bus_factory(db)
            q_rows = (
                await db.scalars(
                    select(Question).where(Question.job_id == job_id).order_by(Question.seq)
                )
            ).all()
            questions = list(q_rows)
            plan_rows = (
                await db.scalars(
                    select(PlanItem).where(PlanItem.job_id == job_id)
                )
            ).all()
            plan_map = {p.seq: p for p in plan_rows}

            review_result = await run_reviewer(
                db=db,
                job_id=job_id,
                bus=bus,
                questions=questions,
                plan_items_map=plan_map,
                model=state.get("context", {}).get("reviewer_model", "gpt-4o"),
            )
            return {
                "review_rejected": review_result.rejected,
                "review_auto_fixed": review_result.auto_fixed,
                "current_stage": Stage.rendering.value,
            }
    return reviewer_node


def make_rendering_node(db_factory, bus_factory):
    async def rendering_node(state: GraphState) -> dict[str, Any]:
        from app.models.job import Job
        from app.models.plan import PlanItem
        from app.models.question import Question
        from app.core.storage import storage
        from app.orchestration.state_machine import JobStatus
        from sqlalchemy import select

        job_id = state["job_id"]
        async with db_factory() as db:
            job = await db.get(Job, job_id)
            if not job:
                return {"error": f"job {job_id} 不存在"}

            q_rows = (
                await db.scalars(
                    select(Question).where(Question.job_id == job_id).order_by(Question.seq)
                )
            ).all()
            p_rows = (
                await db.scalars(
                    select(PlanItem).where(PlanItem.job_id == job_id).order_by(PlanItem.seq)
                )
            ).all()

            plan_map = {p.seq: p for p in p_rows}
            questions_with_plan = [(plan_map.get(q.seq), q) for q in q_rows if q.seq in plan_map]

            md_text = build_markdown(
                title=f"试卷（{state.get('duration_minutes', 100)} 分钟）",
                questions=questions_with_plan,
                need_explanation=state.get("need_explanation", False),
            )

            md_key = f"jobs/{job_id}/output/paper.md"
            await storage.put(md_key, md_text.encode("utf-8"))

            renderer = StubRenderer()
            result = await renderer.render(
                f"试卷（{state.get('duration_minutes', 100)} 分钟）",
                [
                    {
                        "seq": q.seq,
                        "stem": q.stem,
                        "question_type": q.question_type,
                        "options": q.options,
                        "answer": q.answer,
                        "explanation": q.explanation if state.get("need_explanation") else None,
                        "sub_questions": q.sub_questions,
                        "sub_answers": q.sub_answers,
                    }
                    for _, q in questions_with_plan
                ],
            )

            pdf_key = f"jobs/{job_id}/output/paper.pdf"
            await storage.put(pdf_key, result.pdf)

            job.md_key = md_key
            job.pdf_key = None if result.pdf_failed else pdf_key
            job.status = JobStatus.completed.value if not result.pdf_failed else JobStatus.partially_completed.value
            await db.commit()

            return {"current_stage": Stage.rendering.value}
    return rendering_node


# ---------------------------------------------------------------------------
# Graph 构建
# ---------------------------------------------------------------------------

def build_graph(db_factory, bus_factory) -> StateGraph:
    builder = StateGraph(GraphState)

    builder.add_node("planner", make_planner_node(db_factory, bus_factory))
    builder.add_node("fanout", make_fanout_node(db_factory, bus_factory))
    builder.add_node("reviewer", make_reviewer_node(db_factory, bus_factory))
    builder.add_node("rendering", make_rendering_node(db_factory, bus_factory))

    builder.set_entry_point("planner")
    builder.add_edge("planner", "fanout")
    builder.add_conditional_edges(
        "fanout",
        lambda s: "reviewer" if s.get("enable_review") else "rendering",
        {
            "reviewer": "reviewer",
            "rendering": "rendering",
        },
    )
    builder.add_edge("reviewer", "rendering")
    builder.add_edge("rendering", END)

    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)


async def run_graph(
    db_factory: Any,
    bus_factory: Any,
    job_id: uuid.UUID,
    context: dict[str, Any],
) -> None:
    """执行编排图。db_factory/bus_factory 用于在节点内创建会话。"""

    from app.models.job import Job

    async with db_factory() as db:
        job = await db.get(Job, job_id)
        if not job:
            raise RuntimeError(f"job {job_id} 不存在")

        initial_state = GraphState(
            job_id=job_id,
            duration_minutes=job.duration_minutes,
            need_explanation=job.need_explanation,
            enable_review=job.enable_review,
            plan_items=[],
            questions=[],
            context=context,
            error=None,
            current_stage=Stage.preprocessing.value,
        )

        graph = build_graph(db_factory, bus_factory)
        config = {"configurable": {"thread_id": str(job_id)}}
        await graph.ainvoke(initial_state, config)
