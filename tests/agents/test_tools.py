"""tools 模块单元测试：7 个工具工厂的行为验证（整卷直写版）。"""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from app.agents.schemas import TodoItem
from app.agents.skills import SkillLoader
from app.agents.tools import (
    PAPER_PATH,
    PDF_PATH,
    AgentContext,
    build_agent_tools,
    finalize,
)
from app.agents.workspace import Workspace
from app.core.storage import LocalStorage
from app.retrieval.vector_store import RetrievalResult, VectorStore
from tests.agents.helpers import Recorder


def make_ctx(tmp_path: Path, **overrides: object) -> AgentContext:
    store = LocalStorage(tmp_path)
    ws = Workspace(store, prefix=f"jobs/{uuid4()}")
    # 技能目录也放在 tmp_path，测试不依赖仓库真实目录与 cwd
    skills_dir = tmp_path / "skills"
    skill_file = skills_dir / "exam-authoring" / "SKILL.md"
    skill_file.parent.mkdir(parents=True, exist_ok=True)
    skill_file.write_text(
        "---\ndescription: 出题规范\n---\n\n试卷直写 output/paper.md 的规范说明。\n",
        encoding="utf-8",
    )
    defaults: dict = {
        "job_id": uuid4(),
        "user_id": uuid4(),
        "workspace": ws,
        "storage": store,
        "skills": SkillLoader(skills_dir),
        "vector_store": StubStore(),
        "hooks": Recorder().hooks(),
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


class StubStore(VectorStore):
    """可编程的向量存储 stub：记录 filter_expr，回放预设 chunks 或抛异常。"""

    def __init__(self, chunks: list[dict] | None = None, error: Exception | None = None):
        self.chunks = chunks or []
        self.error = error
        self.filter_exprs: list[dict] = []

    async def search(self, query: str, filter_expr: dict, top_k: int = 5) -> RetrievalResult:
        self.filter_exprs.append(filter_expr)
        if self.error:
            raise self.error
        return RetrievalResult(chunks=self.chunks[:top_k])


def todo(seq: int, status: str = "pending", **kw: str) -> TodoItem:
    return TodoItem(
        seq=seq,
        question_type=kw.get("question_type", "choice"),
        knowledge_point=kw.get("knowledge_point", "导数"),
        status=status,
    )


PAPER_V1 = """# 试卷（60 分钟）

## 一、选择题

1. 1+1=？
   A. 1
   B. 2
   C. 3
   D. 4
"""


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


class TestTodoWrite:
    async def test_first_call_fires_plan_ready(self, tmp_path: Path, recorder: Recorder) -> None:
        ctx = make_ctx(tmp_path, hooks=recorder.hooks())
        (todo_write, *_rest) = build_agent_tools(ctx)[:1]
        result = await todo_write.coroutine(todos=[todo(1), todo(2)])
        assert len(recorder.plans) == 1
        assert "共 2 题" in result

    async def test_progress_fired_on_every_call(
        self, tmp_path: Path, recorder: Recorder
    ) -> None:
        """进度事件随 todo status 变化逐次上报（completed/total）。"""
        ctx = make_ctx(tmp_path, hooks=recorder.hooks())
        (todo_write, check_todo, *_rest) = build_agent_tools(ctx)
        await todo_write.coroutine(todos=[todo(1, status="pending")])
        await todo_write.coroutine(todos=[todo(1, status="in_progress")])
        await todo_write.coroutine(todos=[todo(1, status="completed")])
        assert len(recorder.plans) == 1  # plan_ready 只发一次
        assert recorder.progress == [(0, 1), (0, 1), (1, 1)]
        text = await check_todo.coroutine()
        assert "[x]" in text

    async def test_duplicate_seq_rejected(self, tmp_path: Path, recorder: Recorder) -> None:
        ctx = make_ctx(tmp_path, hooks=recorder.hooks())
        (todo_write, *_rest) = build_agent_tools(ctx)
        result = await todo_write.coroutine(todos=[todo(1), todo(1)])
        assert "重复的 seq" in result
        assert ctx.todos == []
        assert recorder.plans == []


class TestCheckTodo:
    async def test_empty_hint(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        (_w, check_todo, *_rest) = build_agent_tools(ctx)
        assert "尚未撰写蓝图" in await check_todo.coroutine()

    async def test_marks(self, tmp_path: Path, recorder: Recorder) -> None:
        ctx = make_ctx(tmp_path, hooks=recorder.hooks())
        (todo_write, check_todo, *_rest) = build_agent_tools(ctx)
        await todo_write.coroutine(todos=[todo(1, status="in_progress"), todo(2, status="pending")])
        text = await check_todo.coroutine()
        assert "[>]" in text and "[ ]" in text


class TestReadFile:
    async def test_missing_returns_error_text(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        read_file = build_agent_tools(ctx)[2]
        assert "读取失败" in await read_file.coroutine(path="agent/materials/none.md")

    async def test_reads_content(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        await ctx.workspace.write("agent/materials/a.md", "材料内容")
        read_file = build_agent_tools(ctx)[2]
        assert "材料内容" in await read_file.coroutine(path="agent/materials/a.md")


class TestEditFile:
    async def test_create_and_replace_paper(self, tmp_path: Path) -> None:
        """试卷直写：创建 output/paper.md 后用唯一匹配替换修改。"""
        ctx = make_ctx(tmp_path)
        edit_file = build_agent_tools(ctx)[3]
        created = await edit_file.coroutine(path=PAPER_PATH, old_string="", new_string=PAPER_V1)
        assert "已创建" in created
        replaced = await edit_file.coroutine(
            path=PAPER_PATH, old_string="1. 1+1=？", new_string="1. 2+2=？"
        )
        assert "已修改" in replaced
        assert "2+2=？" in await ctx.workspace.read(PAPER_PATH)

    async def test_plain_file_create_and_replace(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        edit_file = build_agent_tools(ctx)[3]
        created = await edit_file.coroutine(path="notes/draft.md", old_string="", new_string="v1")
        assert "已创建" in created
        replaced = await edit_file.coroutine(
            path="notes/draft.md", old_string="v1", new_string="v2"
        )
        assert "已修改" in replaced
        assert await ctx.workspace.read("notes/draft.md") == "v2"

    async def test_workspace_error_becomes_text(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        edit_file = build_agent_tools(ctx)[3]
        result = await edit_file.coroutine(path="nope.md", old_string="x", new_string="y")
        assert "编辑失败" in result


class TestSearchKnowledge:
    async def test_no_filter_hint(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)  # 无 upload/school/course
        search = build_agent_tools(ctx)[4]
        assert "知识库为空" in await search.coroutine(query="导数")

    async def test_returns_formatted_chunks(self, tmp_path: Path, recorder: Recorder) -> None:
        store = StubStore(
            chunks=[
                {"text": "导数定义……", "source_type": "book", "page": 3, "similarity": 0.91},
                {"text": "极限性质……", "source_type": "note", "similarity": 0.8},
            ]
        )
        ctx = make_ctx(
            tmp_path,
            vector_store=store,
            hooks=recorder.hooks(),
            upload_ids=[uuid4()],
        )
        search = build_agent_tools(ctx)[4]
        result = await search.coroutine(query="导数", top_k=1)
        assert "导数定义" in result
        assert "极限性质" not in result  # top_k 生效
        assert "第 3 页" in result

    async def test_empty_result_hint(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path, upload_ids=[uuid4()])
        search = build_agent_tools(ctx)[4]
        assert "未检索到" in await search.coroutine(query=" anything")

    async def test_error_becomes_text(self, tmp_path: Path) -> None:
        ctx = make_ctx(
            tmp_path,
            vector_store=StubStore(error=RuntimeError("db down")),
            upload_ids=[uuid4()],
        )
        search = build_agent_tools(ctx)[4]
        assert "检索失败" in await search.coroutine(query="x")


class TestKnowledgeFilter:
    @staticmethod
    def _base(expr: dict) -> dict:
        """拆出基础过滤：user_id 存在时结构为 {"and": [base, user_scope]}。"""
        if "and" in expr and isinstance(expr["and"], list) and len(expr["and"]) == 2:
            return expr["and"][0]
        return expr

    def test_uploads_only(self, tmp_path: Path) -> None:
        from app.agents.tools import _knowledge_filter

        u1, u2 = uuid4(), uuid4()
        expr = _knowledge_filter(make_ctx(tmp_path, upload_ids=[u1, u2]))
        assert expr is not None
        base = self._base(expr)
        assert len(base["or"]) == 3  # book/lecture/note 各一分支
        upload_branch = base["or"][0]["and"]
        assert {"in": {"upload_id": [str(u1), str(u2)]}} in upload_branch

    def test_school_course_only(self, tmp_path: Path) -> None:
        from app.agents.tools import _knowledge_filter

        school, course = uuid4(), uuid4()
        expr = _knowledge_filter(make_ctx(tmp_path, school_id=school, course_id=course))
        base = self._base(expr)
        assert "or" in base  # 3 种 source_type × 1 分支
        assert len(base["or"]) == 3

    def test_combined_produces_or(self, tmp_path: Path) -> None:
        """有上传件 + 学校课程：每个 source_type 一个 additive 分支（内含 or）。"""
        from app.agents.tools import _knowledge_filter

        expr = _knowledge_filter(
            make_ctx(
                tmp_path,
                upload_ids=[uuid4()],
                school_id=uuid4(),
                course_id=uuid4(),
            )
        )
        assert expr is not None
        base = self._base(expr)
        assert len(base["or"]) == 3  # book/lecture/note 各一个 additive 分支
        for branch in base["or"]:
            # additive：本次上传 OR 同校同课程共享库
            assert "or" in branch
            assert len(branch["or"]) == 2

    def test_shared_flag_present(self, tmp_path: Path) -> None:
        from app.agents.tools import _knowledge_filter

        expr = _knowledge_filter(make_ctx(tmp_path, school_id=uuid4(), course_id=uuid4()))
        base = self._base(expr)
        branch = base["or"][0]["and"]
        assert {"eq": {"is_shared": True}} in branch

    def test_user_scope_guard_wraps_base(self, tmp_path: Path) -> None:
        """有 user_id 时必须包一层跨用户隔离：本人 OR 显式共享。"""
        from app.agents.tools import _knowledge_filter

        ctx = make_ctx(tmp_path, upload_ids=[uuid4()])
        expr = _knowledge_filter(ctx)
        assert "and" in expr and len(expr["and"]) == 2
        scope = expr["and"][1]
        assert scope == {
            "or": [
                {"eq": {"user_id": str(ctx.user_id)}},
                {"eq": {"is_shared": True}},
            ],
        }

    def test_no_user_id_skips_guard(self, tmp_path: Path) -> None:
        from app.agents.tools import _knowledge_filter

        expr = _knowledge_filter(make_ctx(tmp_path, upload_ids=[uuid4()], user_id=None))
        assert "or" in expr
        assert "and" not in expr

    def test_no_uploads_no_scope_returns_none(self, tmp_path: Path) -> None:
        from app.agents.tools import _knowledge_filter

        assert _knowledge_filter(make_ctx(tmp_path, user_id=None)) is None


class TestLoadSkill:
    async def test_load_installed_skill(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        load_skill = build_agent_tools(ctx)[5]
        result = await load_skill.coroutine(name="exam-authoring")
        assert "output/paper.md" in result

    async def test_load_unknown_becomes_text(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        load_skill = build_agent_tools(ctx)[5]
        assert "技能加载失败" in await load_skill.coroutine(name="nope")


class TestFinalize:
    def test_abandoned_from_todo_marks(self, tmp_path: Path, recorder: Recorder) -> None:
        """放弃题来自蓝图的「[已放弃]」标记，不再扫题目文件。"""
        ctx = make_ctx(tmp_path, hooks=recorder.hooks())
        ctx.todos = [todo(1), todo(2), todo(3, knowledge_point="[已放弃] 超纲")]
        result = finalize(ctx, intent=None, summary="s", completed_normally=True)
        assert result.abandoned_seqs == [3]
        assert [t.seq for t in result.plan_items] == [1, 2, 3]
        assert result.completed_normally is True

    def test_carries_render_state(self, tmp_path: Path, recorder: Recorder) -> None:
        """finalize 把 ctx 的渲染状态透传到 ExamResult。"""
        ctx = make_ctx(tmp_path, hooks=recorder.hooks())
        ctx.render_status = "md_only"
        ctx.render_error = "pandoc 退出码 1"
        result = finalize(ctx, intent=None)
        assert result.render_status == "md_only"
        assert result.render_error == "pandoc 退出码 1"


class FakeRenderer:
    """可编程渲染器 stub：按脚本依次回放 bytes 结果或抛异常。"""

    def __init__(self, outcomes: list[bytes | Exception]) -> None:
        self.outcomes = outcomes
        self.calls = 0
        self.seen: list[bytes] = []

    async def render_markdown(self, md: bytes, title: str) -> bytes:
        self.seen.append(md)
        outcome = self.outcomes[min(self.calls, len(self.outcomes) - 1)]
        self.calls += 1
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class TestRenderPaper:
    def _ctx(
        self,
        tmp_path: Path,
        recorder: Recorder,
        renderer: FakeRenderer,
        **overrides: object,
    ) -> AgentContext:
        ctx = make_ctx(
            tmp_path,
            hooks=recorder.hooks(),
            duration_minutes=60,
            render_max_retries=2,
            renderer_factory=lambda: renderer,
            **overrides,
        )
        ctx.todos = [todo(1, status="completed"), todo(2, status="completed")]
        return ctx

    async def test_success_renders_pdf_and_keeps_raw_md(
        self, tmp_path: Path, recorder: Recorder
    ) -> None:
        """直渲：pdf 落盘，磁盘上的 paper.md 保持 agent 原稿（不被清洗改写）。"""
        renderer = FakeRenderer([b"%PDF-1.4 fake\n"])
        ctx = self._ctx(tmp_path, recorder, renderer)
        await ctx.workspace.write(PAPER_PATH, PAPER_V1 + "残留转义：abc\\ndef\n")
        tool = build_agent_tools(ctx)[6]

        text = await tool.coroutine()

        assert "渲染成功" in text
        assert ctx.render_status == "succeeded"
        assert ctx.render_error is None
        # 磁盘 md 保持原稿（含字面量转义，未回写清洗结果）
        assert "abc\\ndef" in await ctx.workspace.read(PAPER_PATH)
        # 喂给 pandoc 的字节经过清洗
        assert b"abc\\\\ndef" in renderer.seen[0]
        pdf = await ctx.storage.get(f"jobs/{ctx.job_id}/{PDF_PATH}")
        assert pdf.startswith(b"%PDF")
        assert recorder.render_starts == [True]

    async def test_failure_then_success(self, tmp_path: Path, recorder: Recorder) -> None:
        """内容性失败：回传含 stderr 的修正提示，重试成功后正常收尾。"""
        renderer = FakeRenderer(
            [
                RuntimeError("Pandoc 渲染失败，退出码 1: Undefined control sequence"),
                b"%PDF-1.4 fake\n",
            ]
        )
        ctx = self._ctx(tmp_path, recorder, renderer)
        await ctx.workspace.write(PAPER_PATH, PAPER_V1)
        tool = build_agent_tools(ctx)[6]

        text1 = await tool.coroutine()

        assert "第 1/2 次" in text1
        assert "Undefined control sequence" in text1
        assert "latex-rendering" in text1
        assert ctx.render_status == "not_attempted"  # 仍在重试环内
        assert not ctx.storage.exists(f"jobs/{ctx.job_id}/{PDF_PATH}")

        text2 = await tool.coroutine()

        assert "渲染成功" in text2
        assert ctx.render_status == "succeeded"
        assert renderer.calls == 2

    async def test_retry_limit_degrades_to_md_only(
        self, tmp_path: Path, recorder: Recorder
    ) -> None:
        renderer = FakeRenderer([RuntimeError("exit 1")])
        ctx = self._ctx(tmp_path, recorder, renderer)
        await ctx.workspace.write(PAPER_PATH, PAPER_V1)
        tool = build_agent_tools(ctx)[6]

        text1 = await tool.coroutine()
        text2 = await tool.coroutine()

        assert "第 1/2 次" in text1
        assert "已放弃" in text2
        assert ctx.render_status == "md_only"
        assert "exit 1" in ctx.render_error
        assert not ctx.storage.exists(f"jobs/{ctx.job_id}/{PDF_PATH}")
        assert len(recorder.warnings) == 1

        # md_only 为终态：再调用不重跑渲染，直接重申放弃
        text3 = await tool.coroutine()
        assert "已停止" in text3
        assert renderer.calls == 2

    async def test_env_error_file_not_found_skips_retry(
        self, tmp_path: Path, recorder: Recorder
    ) -> None:
        """环境性错误（pandoc 缺失）：一次即降级，不进重试环。"""
        renderer = FakeRenderer([FileNotFoundError("pandoc not found")])
        ctx = self._ctx(tmp_path, recorder, renderer)
        await ctx.workspace.write(PAPER_PATH, PAPER_V1)
        tool = build_agent_tools(ctx)[6]

        text = await tool.coroutine()

        assert "渲染环境异常" in text
        assert ctx.render_status == "md_only"
        assert renderer.calls == 1
        assert len(recorder.warnings) == 1

    async def test_env_error_factory_failure_skips_retry(
        self, tmp_path: Path, recorder: Recorder
    ) -> None:
        """renderer 构造失败（apt 安装失败）：转译为环境错误直接降级。"""

        def broken_factory() -> object:
            raise RuntimeError("apt 安装失败")

        ctx = make_ctx(
            tmp_path,
            hooks=recorder.hooks(),
            duration_minutes=60,
            render_max_retries=3,
            renderer_factory=broken_factory,
        )
        await ctx.workspace.write(PAPER_PATH, PAPER_V1)
        tool = build_agent_tools(ctx)[6]

        text = await tool.coroutine()

        assert "渲染环境异常" in text
        assert "apt 安装失败" in text
        assert ctx.render_status == "md_only"

    async def test_missing_paper_does_not_render(
        self, tmp_path: Path, recorder: Recorder
    ) -> None:
        """尚未创建试卷：给出可读提示，不触发渲染。"""
        renderer = FakeRenderer([b"%PDF-1.4 fake\n"])
        ctx = self._ctx(tmp_path, recorder, renderer)
        tool = build_agent_tools(ctx)[6]

        text = await tool.coroutine()

        assert "尚未创建试卷" in text
        assert renderer.calls == 0
        assert ctx.render_status == "not_attempted"
        assert ctx.render_attempts == 0

    async def test_idempotent_after_success(self, tmp_path: Path, recorder: Recorder) -> None:
        renderer = FakeRenderer([b"%PDF-1.4 fake\n"])
        ctx = self._ctx(tmp_path, recorder, renderer)
        await ctx.workspace.write(PAPER_PATH, PAPER_V1)
        tool = build_agent_tools(ctx)[6]
        await tool.coroutine()

        text = await tool.coroutine()

        assert "无需重复渲染" in text
        assert renderer.calls == 1

    async def test_revision_context_re_renders(self, tmp_path: Path, recorder: Recorder) -> None:
        """修订续跑是新 ctx（render_status 复位），同卷可再次渲染出新 PDF。"""
        from app.agents.schemas import RevisionDirective, SelectionAnchor

        renderer = FakeRenderer([b"%PDF-1.4 fake\n", b"%PDF-1.4 revised\n"])
        ctx1 = self._ctx(tmp_path, recorder, renderer)
        await ctx1.workspace.write(PAPER_PATH, PAPER_V1)
        await build_agent_tools(ctx1)[6].coroutine()

        ctx2 = make_ctx(
            tmp_path,
            hooks=recorder.hooks(),
            duration_minutes=60,
            renderer_factory=lambda: renderer,
            revision=RevisionDirective(
                selection=SelectionAnchor(text="1+1"),
                feedback="加大难度",
            ),
        )
        ctx2.workspace = ctx1.workspace
        ctx2.storage = ctx1.storage
        text = await build_agent_tools(ctx2)[6].coroutine()

        assert "渲染成功" in text
        assert renderer.calls == 2
