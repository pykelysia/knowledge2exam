"""agent 入口端到端单元测试：fake 模型驱动完整 ReAct 循环。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

from app.agents import agent as agent_module
from app.agents.agent import run_exam_agent
from app.agents.prompts import load_prompt, render_prompt
from app.agents.schemas import ExamIntent
from app.agents.workspace import Workspace
from app.config import settings
from app.core.storage import LocalStorage
from tests.agents.helpers import FakeToolModel, Recorder, tool_call

# ---------------------------------------------------------------------------
# fake 意图模型
# ---------------------------------------------------------------------------


class FakeStructured:
    """with_structured_output 返回的 stub，直接回放/抛错。"""

    def __init__(self, intent: ExamIntent | None = None, error: Exception | None = None):
        self.intent = intent
        self.error = error

    async def ainvoke(self, prompt: str) -> ExamIntent:
        if self.error:
            raise self.error
        if self.intent is None:
            return None  # type: ignore[return-value]
        return self.intent


class FakeIntentModel:
    """只实现 with_structured_output 的假模型（extract_intent 只用到它）。"""

    def __init__(self, structured: FakeStructured):
        self.structured = structured

    def with_structured_output(self, schema: Any, **kwargs: Any) -> FakeStructured:
        return self.structured


def fake_intent() -> ExamIntent:
    return ExamIntent(
        exam_scope="高等数学期末",
        question_type_preference="选择与填空为主",
        focus_points=["导数", "极限"],
        summary="期中模拟卷",
    )


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def make_question(seq: int, **overrides: object) -> str:
    data: dict = {
        "seq": seq,
        "question_type": "choice",
        "stem": f"第 {seq} 题题干",
        "options": {"A": "1", "B": "2", "C": "3", "D": "4"},
        "answer": "A",
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


class StubVectorStore:
    """避免触碰 pgvector 的空实现（本测试不触发检索）。"""

    async def search(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise AssertionError("测试脚本未安排检索调用")


class StubRenderOnce:
    """_render_paper_once 的 stub：计数调用并直接置渲染成功（不触 pandoc）。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, ctx: Any) -> int:
        self.calls += 1
        ctx.render_status = "succeeded"
        ctx.render_error = None
        return max(len(ctx.todos), 1)


@pytest.fixture
def job_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    """补丁后的运行环境：本地存储 + 临时 skills + stub 依赖。"""
    job_id = uuid4()
    ws = Workspace(LocalStorage(tmp_path), prefix=f"jobs/{job_id}/agent")
    skills_dir = tmp_path / "skills_installed"
    skill_file = skills_dir / "exam-authoring" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("题目文件 questions/NNN.json 的规范说明。", encoding="utf-8")

    render_stub = StubRenderOnce()
    monkeypatch.setattr(agent_module, "storage", LocalStorage(tmp_path))
    monkeypatch.setattr(agent_module, "PgVectorStore", lambda session: StubVectorStore())
    monkeypatch.setattr(agent_module, "extract_intent", _fake_extract_intent)
    # 工具闭包在 tools 模块全局解析 _render_paper_once，agent.py 兜底解析
    # 自己 import 的名字——两个命名空间都要替换，且绝不触真实渲染
    monkeypatch.setattr("app.agents.tools._render_paper_once", render_stub)
    monkeypatch.setattr(agent_module, "_render_paper_once", render_stub)
    monkeypatch.setattr(settings, "skills_dir", skills_dir)
    ws._job_id = job_id  # 供测试取回
    ws._render_calls = render_stub  # 供测试断言兜底/工具触发次数
    return ws


async def _fake_extract_intent(model: Any, **kwargs: Any) -> ExamIntent:
    return fake_intent()


# ---------------------------------------------------------------------------
# 完整循环
# ---------------------------------------------------------------------------


class TestRunExamAgent:
    async def test_full_loop(self, job_env: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
        ws = job_env
        job_id = ws._job_id
        await ws.write("materials/past_paper_01.md", "往期试卷全文")
        await ws.write("materials/keypoints.md", "重点：导数")

        fake = FakeToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        tool_call("read_file", {"path": "materials/past_paper_01.md"}, "c1")
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[tool_call("load_skill", {"name": "exam-authoring"}, "c2")],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        tool_call(
                            "todo_write",
                            {
                                "todos": [
                                    {
                                        "seq": 1,
                                        "question_type": "choice",
                                        "knowledge_point": "导数",
                                    },
                                    {"seq": 2, "question_type": "blank", "knowledge_point": "极限"},
                                ]
                            },
                            "c3",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        tool_call(
                            "edit_file",
                            {
                                "path": "questions/001.json",
                                "old_string": "",
                                "new_string": make_question(1),
                            },
                            "c4",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        tool_call(
                            "edit_file",
                            {
                                "path": "questions/002.json",
                                "old_string": "",
                                "new_string": "not-json",
                            },
                            "c5",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        tool_call(
                            "edit_file",
                            {
                                "path": "questions/002.json",
                                "old_string": "",
                                "new_string": make_question(
                                    2,
                                    question_type="blank",
                                    options=None,
                                    stem="1+1=______",
                                    answer="2",
                                ),
                            },
                            "c6",
                        )
                    ],
                ),
                AIMessage(content="", tool_calls=[tool_call("check_todo", {}, "c7")]),
                AIMessage(content="", tool_calls=[tool_call("render_paper", {}, "c8")]),
                AIMessage(
                    content="",
                    tool_calls=[
                        tool_call(
                            "AgentSummary",
                            {"total_questions": 2, "abandoned_count": 0, "summary": "两题完成"},
                            "c9",
                        )
                    ],
                ),
            ]
        )
        monkeypatch.setattr(agent_module, "get_chat_model", lambda: fake)

        recorder = Recorder()
        result = await run_exam_agent(
            job_id,
            {"duration_minutes": 100, "need_explanation": False},
            recorder.hooks(),
        )

        # 结果
        assert result.completed_normally is True
        assert result.summary == "两题完成"
        assert [q.seq for q in result.questions] == [1, 2]
        assert [t.seq for t in result.plan_items] == [1, 2]
        assert result.abandoned_seqs == []
        assert result.intent is not None and result.intent.exam_scope == "高等数学期末"

        # 渲染：模型调用了 render_paper 工具，兜底不再触发
        assert result.render_status == "succeeded"
        assert ws._render_calls.calls == 1

        # 钩子序列
        assert len(recorder.plans) == 1
        assert [c for _q, c, _t in recorder.questions] == [1, 2]
        assert recorder.questions[1][2] == 2  # total
        assert recorder.warnings == []

        # 结构化输出工具确实绑定给了模型
        assert "AgentSummary" in fake.bound_tool_names
        assert "render_paper" in fake.bound_tool_names

        # 系统提示词注入了材料清单与技能索引
        system_text = str(fake.seen_messages[0][0].content)
        assert "materials/past_paper_01.md" in system_text
        assert "exam-authoring" in system_text
        assert "100 分钟" in system_text

        # 工作区产物
        assert await ws.exists("questions/001.json")

    async def test_model_crash_partial_result(
        self, job_env: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        ws = job_env

        class ExplodingModel(FakeToolModel):
            def _generate(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("LLM 爆炸")

        monkeypatch.setattr(agent_module, "get_chat_model", lambda: ExplodingModel(responses=[]))
        recorder = Recorder()
        result = await run_exam_agent(ws._job_id, {}, recorder.hooks())

        assert result.completed_normally is False
        assert result.questions == []
        assert len(recorder.warnings) == 1
        assert "LLM 爆炸" in recorder.warnings[0]
        # 循环异常中断：兜底渲染仍被触发，渲染状态有值
        assert ws._render_calls.calls == 1
        assert result.render_status == "succeeded"

    async def test_skips_render_tool_gets_fallback(
        self, job_env: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """模型不调 render_paper 直接总结：agent.py 兜底补渲染一次。"""
        ws = job_env
        fake = FakeToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        tool_call(
                            "todo_write",
                            {
                                "todos": [
                                    {
                                        "seq": 1,
                                        "question_type": "blank",
                                        "knowledge_point": "极限",
                                    },
                                ]
                            },
                            "c1",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        tool_call(
                            "edit_file",
                            {
                                "path": "questions/001.json",
                                "old_string": "",
                                "new_string": make_question(
                                    1,
                                    question_type="blank",
                                    options=None,
                                    stem="1+1=______",
                                    answer="2",
                                ),
                            },
                            "c2",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        tool_call(
                            "AgentSummary",
                            {"total_questions": 1, "abandoned_count": 0, "summary": "ok"},
                            "c3",
                        )
                    ],
                ),
            ]
        )
        monkeypatch.setattr(agent_module, "get_chat_model", lambda: fake)
        recorder = Recorder()
        result = await run_exam_agent(ws._job_id, {}, recorder.hooks())

        assert result.completed_normally is True
        assert [q.seq for q in result.questions] == [1]
        # 工具未被模型调用，兜底路径补了一次渲染
        assert ws._render_calls.calls == 1
        assert result.render_status == "succeeded"

    async def test_materials_optional(
        self, job_env: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """无材料时系统提示词提示（无材料），循环仍可完成。"""

        fake = FakeToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        tool_call(
                            "todo_write",
                            {
                                "todos": [
                                    {
                                        "seq": 1,
                                        "question_type": "short_answer",
                                        "knowledge_point": "中值定理",
                                    },
                                ]
                            },
                            "c1",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        tool_call(
                            "edit_file",
                            {
                                "path": "questions/001.json",
                                "old_string": "",
                                "new_string": json.dumps(
                                    {
                                        "seq": 1,
                                        "question_type": "short_answer",
                                        "stem": "证明拉格朗日中值定理",
                                        "answer": "略",
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                            "c2",
                        )
                    ],
                ),
                AIMessage(
                    content="",
                    tool_calls=[
                        tool_call(
                            "AgentSummary",
                            {"total_questions": 1, "abandoned_count": 0, "summary": "ok"},
                            "c3",
                        )
                    ],
                ),
            ]
        )
        monkeypatch.setattr(agent_module, "get_chat_model", lambda: fake)
        recorder = Recorder()
        result = await run_exam_agent(job_env._job_id, {}, recorder.hooks())

        assert result.completed_normally is True
        assert [q.seq for q in result.questions] == [1]
        system_text = str(fake.seen_messages[0][0].content)
        assert "无材料" in system_text
        # 脚本未调 render_paper：兜底补渲染
        assert job_env._render_calls.calls == 1
        assert result.render_status == "succeeded"


# ---------------------------------------------------------------------------
# 意图提取
# ---------------------------------------------------------------------------


class TestExtractIntent:
    async def test_success(self) -> None:
        from app.agents.intent import extract_intent

        intent = await extract_intent(
            FakeIntentModel(FakeStructured(intent=fake_intent())),
            duration_minutes=90,
            keypoint_list="导数",
            extra_requirement=None,
        )
        assert intent.exam_scope == "高等数学期末"
        assert intent.focus_points == ["导数", "极限"]

    async def test_model_failure_falls_back(self) -> None:
        from app.agents.intent import extract_intent

        intent = await extract_intent(
            FakeIntentModel(FakeStructured(error=RuntimeError("超时"))),
            duration_minutes=90,
        )
        assert "意图提取" in intent.exam_scope

    async def test_none_result_falls_back(self) -> None:
        from app.agents.intent import extract_intent

        intent = await extract_intent(
            FakeIntentModel(FakeStructured(intent=None)), duration_minutes=90
        )
        assert "意图提取" in intent.exam_scope


# ---------------------------------------------------------------------------
# 提示词渲染
# ---------------------------------------------------------------------------


class TestPrompts:
    def test_system_prompt_renders_all_placeholders(self) -> None:
        rendered = render_prompt(
            load_prompt("system"),
            duration_minutes="120",
            need_explanation_label="需要解析",
            intent="{}",
            material_list="- materials/a.md",
            explanation_rule="每题必须携带 explanation 解析字段。",
            max_retries="3",
            skill_index="- `exam-authoring`：出题规范",
        )
        assert "{{" not in rendered and "}}" not in rendered
        assert "120 分钟" in rendered
        assert "materials/a.md" in rendered
