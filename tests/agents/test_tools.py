"""tools 模块单元测试：6 个工具工厂的行为验证。"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest

from app.agents.schemas import TodoItem
from app.agents.skills import SkillLoader
from app.agents.tools import AgentContext, build_agent_tools, finalize
from app.agents.workspace import Workspace
from app.core.storage import LocalStorage
from app.retrieval.vector_store import RetrievalResult, VectorStore
from tests.agents.helpers import Recorder


def make_ctx(tmp_path: Path, **overrides: object) -> AgentContext:
    ws = Workspace(LocalStorage(tmp_path), prefix=f"jobs/{uuid4()}/agent")
    defaults: dict = {
        "job_id": uuid4(),
        "workspace": ws,
        "skills": SkillLoader(Path("./skills")),
        "vector_store": StubStore(),
        "hooks": Recorder().hooks(),
        "max_retries": 2,
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


class StubStore(VectorStore):
    """可编程的向量存储 stub：记录 filter_expr，回放预设 chunks 或抛异常。"""

    def __init__(self, chunks: list[dict] | None = None, error: Exception | None = None):
        self.chunks = chunks or []
        self.error = error
        self.filter_exprs: list[dict] = []

    async def search(
        self, query: str, filter_expr: dict, top_k: int = 5
    ) -> RetrievalResult:
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


def question_json(seq: int, **overrides: object) -> str:
    data: dict = {
        "seq": seq,
        "question_type": "choice",
        "stem": "题干",
        "options": {"A": "1", "B": "2", "C": "3", "D": "4"},
        "answer": "B",
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


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

    async def test_second_call_does_not_refire(self, tmp_path: Path, recorder: Recorder) -> None:
        ctx = make_ctx(tmp_path, hooks=recorder.hooks())
        (todo_write, check_todo, *_rest) = build_agent_tools(ctx)
        await todo_write.coroutine(todos=[todo(1, status="pending")])
        await todo_write.coroutine(todos=[todo(1, status="completed")])
        assert len(recorder.plans) == 1
        assert ctx.todos[0].status == "completed"
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
        await todo_write.coroutine(
            todos=[todo(1, status="in_progress"), todo(2, status="pending")]
        )
        text = await check_todo.coroutine()
        assert "[>]" in text and "[ ]" in text


class TestReadFile:
    async def test_missing_returns_error_text(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        tools = build_agent_tools(ctx)
        read_file = tools[2]
        assert "读取失败" in await read_file.coroutine(path="materials/none.md")

    async def test_reads_content(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        await ctx.workspace.write("materials/a.md", "材料内容")
        read_file = build_agent_tools(ctx)[2]
        assert "材料内容" in await read_file.coroutine(path="materials/a.md")


class TestEditFileQuestion:
    async def test_valid_question_accepted(
        self, tmp_path: Path, recorder: Recorder
    ) -> None:
        ctx = make_ctx(tmp_path, hooks=recorder.hooks())
        (todo_write, _c, _r, edit_file, *_rest) = build_agent_tools(ctx)
        await todo_write.coroutine(todos=[todo(1), todo(2)])
        result = await edit_file.coroutine(
            path="questions/001.json",
            old_string="",
            new_string=question_json(1),
        )
        assert "已保存" in result and "1/2" in result
        assert len(recorder.questions) == 1
        assert await ctx.workspace.exists("questions/001.json")

    async def test_invalid_json_counts_retry(self, tmp_path: Path, recorder: Recorder) -> None:
        ctx = make_ctx(tmp_path, hooks=recorder.hooks())
        edit_file = build_agent_tools(ctx)[3]
        result = await edit_file.coroutine(
            path="questions/001.json", old_string="", new_string="not json"
        )
        assert "校验失败" in result and "1/2" in result
        assert not await ctx.workspace.exists("questions/001.json")
        assert recorder.questions == []

    async def test_max_retries_abandons(
        self, tmp_path: Path, recorder: Recorder
    ) -> None:
        ctx = make_ctx(tmp_path, hooks=recorder.hooks())
        edit_file = build_agent_tools(ctx)[3]
        for _ in range(2):
            result = await edit_file.coroutine(
                path="questions/001.json", old_string="", new_string="bad"
            )
        assert "已放弃" in result
        assert ctx.abandoned_seqs == {1}
        assert len(recorder.warnings) == 1

    async def test_resubmission_washes_out_abandoned(
        self, tmp_path: Path, recorder: Recorder
    ) -> None:
        ctx = make_ctx(tmp_path, hooks=recorder.hooks())
        edit_file = build_agent_tools(ctx)[3]
        await edit_file.coroutine(path="questions/001.json", old_string="", new_string="bad")
        await edit_file.coroutine(path="questions/001.json", old_string="", new_string="bad")
        result = await edit_file.coroutine(
            path="questions/001.json", old_string="", new_string=question_json(1)
        )
        assert "已保存" in result
        assert ctx.abandoned_seqs == set()
        assert len(recorder.warnings) == 1
        assert len(recorder.questions) == 1

    async def test_need_explanation_enforced(
        self, tmp_path: Path, recorder: Recorder
    ) -> None:
        ctx = make_ctx(tmp_path, hooks=recorder.hooks(), need_explanation=True)
        edit_file = build_agent_tools(ctx)[3]
        result = await edit_file.coroutine(
            path="questions/001.json", old_string="", new_string=question_json(1)
        )
        assert "explanation" in result
        # 带解析后通过
        ok = await edit_file.coroutine(
            path="questions/001.json",
            old_string="",
            new_string=question_json(1, explanation="因为……"),
        )
        assert "已保存" in ok

    async def test_seq_mismatch_with_filename(
        self, tmp_path: Path, recorder: Recorder
    ) -> None:
        """文件名 001.json 里写 seq=2：按文件名落盘，内容按原样校验通过。"""
        ctx = make_ctx(tmp_path, hooks=recorder.hooks())
        edit_file = build_agent_tools(ctx)[3]
        result = await edit_file.coroutine(
            path="questions/001.json", old_string="", new_string=question_json(2)
        )
        assert "已保存" in result
        stored = json.loads(await ctx.workspace.read("questions/001.json"))
        assert stored["seq"] == 2


class TestEditFilePlain:
    async def test_create_and_replace(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        edit_file = build_agent_tools(ctx)[3]
        created = await edit_file.coroutine(
            path="notes/draft.md", old_string="", new_string="v1"
        )
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

    async def test_returns_formatted_chunks(
        self, tmp_path: Path, recorder: Recorder
    ) -> None:
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
    def test_uploads_only(self, tmp_path: Path) -> None:
        from app.agents.tools import _knowledge_filter

        u1, u2 = uuid4(), uuid4()
        expr = _knowledge_filter(make_ctx(tmp_path, upload_ids=[u1, u2]))
        assert expr is not None
        assert len(expr["or"]) == 3  # book/lecture/note 各一分支
        upload_branch = expr["or"][0]["and"]
        assert {"in": {"upload_id": [str(u1), str(u2)]}} in upload_branch

    def test_school_course_only(self, tmp_path: Path) -> None:
        from app.agents.tools import _knowledge_filter

        school, course = uuid4(), uuid4()
        expr = _knowledge_filter(make_ctx(tmp_path, school_id=school, course_id=course))
        assert "or" in expr  # 3 种 source_type × 1 分支
        assert len(expr["or"]) == 3

    def test_combined_produces_or(self, tmp_path: Path) -> None:
        from app.agents.tools import _knowledge_filter

        expr = _knowledge_filter(
            make_ctx(
                tmp_path,
                upload_ids=[uuid4()],
                school_id=uuid4(),
                course_id=uuid4(),
            )
        )
        assert expr is not None and len(expr["or"]) == 6

    def test_shared_flag_present(self, tmp_path: Path) -> None:
        from app.agents.tools import _knowledge_filter

        expr = _knowledge_filter(
            make_ctx(tmp_path, school_id=uuid4(), course_id=uuid4())
        )
        branch = expr["or"][0]["and"]
        assert {"eq": {"is_shared": True}} in branch


class TestLoadSkill:
    async def test_load_real_skill(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        load_skill = build_agent_tools(ctx)[5]
        result = await load_skill.coroutine(name="exam-authoring")
        assert "questions/NNN.json" in result or "题" in result

    async def test_load_unknown_becomes_text(self, tmp_path: Path) -> None:
        ctx = make_ctx(tmp_path)
        load_skill = build_agent_tools(ctx)[5]
        assert "技能加载失败" in await load_skill.coroutine(name="nope")


class TestFinalize:
    async def test_reads_questions_and_skips_invalid(
        self, tmp_path: Path, recorder: Recorder
    ) -> None:
        ctx = make_ctx(tmp_path, hooks=recorder.hooks())
        ctx.todos = [todo(1), todo(2)]
        await ctx.workspace.write("questions/001.json", question_json(1))
        await ctx.workspace.write("questions/002.json", "broken{")
        result = await finalize(ctx, intent=None, summary="s", completed_normally=True)
        assert [q.seq for q in result.questions] == [1]
        assert len(recorder.warnings) == 1
        assert result.completed_normally is True
        assert [t.seq for t in result.plan_items] == [1, 2]
