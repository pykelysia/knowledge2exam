"""agent 入口端到端单元测试：fake 模型驱动完整 ReAct 循环（生成与修订）。"""

from __future__ import annotations

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

PAPER_V1 = """# 试卷（100 分钟）

## 一、选择题

1. 1+1=？
   A. 1
   B. 2
   C. 3
   D. 4
"""


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
        return 100


@pytest.fixture
def job_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Workspace:
    """补丁后的运行环境：本地存储 + 临时 skills + stub 依赖。

    工作区以 job 目录为根（与生产一致）：材料在 agent/materials/，
    试卷在 output/paper.md。
    """
    job_id = uuid4()
    ws = Workspace(LocalStorage(tmp_path), prefix=f"jobs/{job_id}")
    skills_dir = tmp_path / "skills_installed"
    skill_file = skills_dir / "exam-authoring" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text("试卷直写 output/paper.md 的规范说明。", encoding="utf-8")

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
# 完整循环（生成）
# ---------------------------------------------------------------------------


class TestRunExamAgent:
    async def test_full_loop(self, job_env: Workspace, monkeypatch: pytest.MonkeyPatch) -> None:
        ws = job_env
        job_id = ws._job_id
        await ws.write("agent/materials/past_paper_01.md", "往期试卷全文")
        await ws.write("agent/materials/keypoints.md", "重点：导数")

        fake = FakeToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        tool_call("read_file", {"path": "agent/materials/past_paper_01.md"}, "c1")
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
                                "path": "output/paper.md",
                                "old_string": "",
                                "new_string": PAPER_V1,
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
                                "path": "output/paper.md",
                                "old_string": "   D. 4\n",
                                "new_string": "   D. 4\n\n## 二、填空题\n\n2. 1+1=______\n",
                            },
                            "c5",
                        )
                    ],
                ),
                AIMessage(content="", tool_calls=[tool_call("check_todo", {}, "c6")]),
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
                                        "status": "completed",
                                    },
                                    {
                                        "seq": 2,
                                        "question_type": "blank",
                                        "knowledge_point": "极限",
                                        "status": "completed",
                                    },
                                ]
                            },
                            "c6b",
                        )
                    ],
                ),
                AIMessage(content="", tool_calls=[tool_call("render_paper", {}, "c7")]),
                AIMessage(
                    content="",
                    tool_calls=[
                        tool_call(
                            "AgentSummary",
                            {"total_questions": 2, "abandoned_count": 0, "summary": "两题完成"},
                            "c8",
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
        assert [t.seq for t in result.plan_items] == [1, 2]
        assert result.abandoned_seqs == []
        assert result.intent is not None and result.intent.exam_scope == "高等数学期末"

        # 渲染：模型调用了 render_paper 工具，兜底不再触发
        assert result.render_status == "succeeded"
        assert ws._render_calls.calls == 1

        # 钩子序列
        assert len(recorder.plans) == 1
        assert recorder.progress[-1] == (2, 2)
        assert recorder.warnings == []

        # 结构化输出工具确实绑定给了模型
        assert "AgentSummary" in fake.bound_tool_names
        assert "render_paper" in fake.bound_tool_names

        # 系统提示词注入了材料清单与技能索引
        system_text = str(fake.seen_messages[0][0].content)
        assert "agent/materials/past_paper_01.md" in system_text
        assert "exam-authoring" in system_text
        assert "100 分钟" in system_text

        # 工作区产物：整卷直写
        paper = await ws.read("output/paper.md")
        assert "## 二、填空题" in paper
        assert "1+1=______" in paper

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
                                "path": "output/paper.md",
                                "old_string": "",
                                "new_string": PAPER_V1,
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
        assert [t.seq for t in result.plan_items] == [1]
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
                                "path": "output/paper.md",
                                "old_string": "",
                                "new_string": (
                                    "# 试卷\n\n## 三、简答题\n\n1. 证明拉格朗日中值定理\n"
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
        system_text = str(fake.seen_messages[0][0].content)
        assert "无材料" in system_text
        # 脚本未调 render_paper：兜底补渲染
        assert job_env._render_calls.calls == 1
        assert result.render_status == "succeeded"


# ---------------------------------------------------------------------------
# 修订分支
# ---------------------------------------------------------------------------


class TestRevisionBranch:
    async def test_revision_loop_edits_paper(
        self, job_env: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """修订：提示词含试卷全文/划选/反馈/历史；全套工具；改完重渲染。"""
        ws = job_env
        await ws.write("output/paper.md", PAPER_V1)

        fake = FakeToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        tool_call(
                            "edit_file",
                            {
                                "path": "output/paper.md",
                                "old_string": "1. 1+1=？",
                                "new_string": "1. 25×4×2=？",
                            },
                            "r1",
                        )
                    ],
                ),
                AIMessage(content="", tool_calls=[tool_call("render_paper", {}, "r2")]),
                AIMessage(content="已按要求修改第 1 题。"),
            ]
        )
        monkeypatch.setattr(agent_module, "get_chat_model", lambda: fake)

        recorder = Recorder()
        result = await run_exam_agent(
            ws._job_id,
            {
                "duration_minutes": 100,
                "need_explanation": False,
                "revision": {
                    "selection": {"text": "1+1=？", "before": "## 一、选择题", "after": "A. 1"},
                    "feedback": "这题太简单，换个难一点的",
                    "history": [
                        {
                            "round_no": 1,
                            "selection": {"text": "2+2=？", "before": "", "after": ""},
                            "feedback": "上一轮的历史反馈",
                            "summary": "已修改",
                        }
                    ],
                },
            },
            recorder.hooks(),
        )

        # 修订模式：无意图提取、无结构化输出绑定，但全套 7 工具可用
        assert result.intent is None
        assert result.completed_normally is True
        assert "AgentSummary" not in fake.bound_tool_names
        for tool_name in (
            "todo_write",
            "check_todo",
            "read_file",
            "edit_file",
            "search_knowledge",
            "load_skill",
            "render_paper",
        ):
            assert tool_name in fake.bound_tool_names

        # 提示词注入：试卷全文、划选、反馈、历史
        system_text = str(fake.seen_messages[0][0].content)
        assert "1+1=？" in system_text  # 试卷全文
        assert "这题太简单" in system_text  # 本次反馈
        assert "上一轮的历史反馈" in system_text  # 会话记录
        assert "## 一、选择题" in system_text  # 划选前文

        # 试卷被修改且渲染被触发（工具路径，非兜底——修订分支无兜底）
        paper = await ws.read("output/paper.md")
        assert "25×4×2=？" in paper
        assert result.render_status == "succeeded"
        assert ws._render_calls.calls == 1

    async def test_revision_without_paper_warns(
        self, job_env: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """试卷不存在：告警并直接返回（completed_normally=False）。"""
        ws = job_env

        class NeverModel(FakeToolModel):
            def _generate(self, *args: Any, **kwargs: Any) -> Any:
                raise AssertionError("不应启动修订循环")

        monkeypatch.setattr(agent_module, "get_chat_model", lambda: NeverModel(responses=[]))
        recorder = Recorder()
        result = await run_exam_agent(
            ws._job_id,
            {
                "revision": {
                    "selection": {"text": "任意", "before": "", "after": ""},
                    "feedback": "改一下",
                }
            },
            recorder.hooks(),
        )

        assert result.completed_normally is False
        assert len(recorder.warnings) == 1
        assert "无法修订" in recorder.warnings[0]

    async def test_revision_crash_keeps_partial(
        self, job_env: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """修订循环异常中断：告警收尾，不触发兜底渲染。"""
        ws = job_env
        await ws.write("output/paper.md", PAPER_V1)

        class ExplodingModel(FakeToolModel):
            def _generate(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("修订爆炸")

        monkeypatch.setattr(agent_module, "get_chat_model", lambda: ExplodingModel(responses=[]))
        recorder = Recorder()
        result = await run_exam_agent(
            ws._job_id,
            {
                "revision": {
                    "selection": {"text": "1+1=？", "before": "", "after": ""},
                    "feedback": "改一下",
                }
            },
            recorder.hooks(),
        )

        assert result.completed_normally is False
        assert ws._render_calls.calls == 0  # 修订分支无渲染兜底
        assert "修订循环中断" in recorder.warnings[0]


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
            material_list="- agent/materials/a.md",
            explanation_rule="每题必须携带解析。",
            skill_index="- `exam-authoring`：出题规范",
        )
        assert "{{" not in rendered and "}}" not in rendered
        assert "120 分钟" in rendered
        assert "agent/materials/a.md" in rendered

    def test_revise_prompt_renders_all_placeholders(self) -> None:
        rendered = render_prompt(
            load_prompt("revise_system"),
            paper_content="# 试卷\n\n1. 题干",
            selection_before="前文",
            selection_text="选中的题干",
            selection_after="后文",
            feedback="加大难度",
            history="- 第 1 轮：反馈",
            material_list="- agent/materials/a.md",
            explanation_rule="不要添加解析。",
            skill_index="- `exam-authoring`：出题规范",
        )
        assert "{{" not in rendered and "}}" not in rendered
        assert "选中的题干" in rendered
        assert "加大难度" in rendered
        assert "第 1 轮" in rendered
