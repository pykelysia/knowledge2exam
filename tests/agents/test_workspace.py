"""workspace 模块单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.workspace import Workspace, WorkspaceError
from app.core.storage import LocalStorage


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    return Workspace(LocalStorage(tmp_path), prefix="jobs/j1/agent")


class TestPaths:
    async def test_write_read_roundtrip(self, workspace: Workspace) -> None:
        await workspace.write("materials/notes.md", "内容")
        assert await workspace.exists("materials/notes.md")
        assert await workspace.read("materials/notes.md") == "内容"

    async def test_missing_file_raises(self, workspace: Workspace) -> None:
        with pytest.raises(WorkspaceError, match="文件不存在"):
            await workspace.read("materials/none.md")

    @pytest.mark.parametrize("bad", ["../escape.md", "a/../b.md", "/abs.md", "a b.md", ""])
    async def test_invalid_path_rejected(self, workspace: Workspace, bad: str) -> None:
        with pytest.raises(WorkspaceError, match="非法的工作区路径"):
            await workspace.write(bad, "x")

    async def test_path_confined_to_prefix(self, workspace: Workspace, tmp_path: Path) -> None:
        await workspace.write("materials/a.md", "hi")
        assert (tmp_path / "jobs/j1/agent/materials/a.md").exists()


class TestRead:
    async def test_truncation(self, workspace: Workspace) -> None:
        await workspace.write("big.md", "x" * 100)
        text = await workspace.read("big.md", max_chars=10)
        assert text.startswith("x" * 10)
        assert "已截断" in text
        assert "100" in text

    async def test_no_truncation_by_default(self, workspace: Workspace) -> None:
        await workspace.write("big.md", "x" * 100)
        assert len(await workspace.read("big.md")) == 100


class TestListDir:
    async def test_lists_relative_names_sorted(self, workspace: Workspace) -> None:
        await workspace.write("questions/002.json", "{}")
        await workspace.write("questions/001.json", "{}")
        await workspace.write("other.txt", "{}")
        assert await workspace.list_dir("questions") == ["001.json", "002.json"]

    async def test_empty_dir(self, workspace: Workspace) -> None:
        assert await workspace.list_dir("questions") == []


class TestEdit:
    async def test_create_when_old_string_empty(self, workspace: Workspace) -> None:
        result = await workspace.edit("new.md", old_string="", new_string="hello")
        assert result.created is True
        assert await workspace.read("new.md") == "hello"

    async def test_create_existing_file_raises(self, workspace: Workspace) -> None:
        await workspace.write("new.md", "hello")
        with pytest.raises(WorkspaceError, match="文件已存在"):
            await workspace.edit("new.md", old_string="", new_string="again")

    async def test_replace_unique_match(self, workspace: Workspace) -> None:
        await workspace.write("doc.md", "alpha beta alpha gamma")
        result = await workspace.edit("doc.md", old_string="alpha beta", new_string="ALPHA")
        assert result.created is False
        assert result.content == "ALPHA alpha gamma"

    async def test_replace_not_found_raises(self, workspace: Workspace) -> None:
        await workspace.write("doc.md", "content")
        with pytest.raises(WorkspaceError, match="未找到"):
            await workspace.edit("doc.md", old_string="nope", new_string="x")

    async def test_replace_ambiguous_raises(self, workspace: Workspace) -> None:
        await workspace.write("doc.md", "dup dup")
        with pytest.raises(WorkspaceError, match="出现 2 次"):
            await workspace.edit("doc.md", old_string="dup", new_string="x")
