"""renderer 单元测试：pandoc 子进程与环境检测全 stub，绝不触发真实安装/渲染。"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.rendering import renderer as renderer_module
from app.rendering.renderer import PandocXeLaTeXRenderer, StubRenderer


class FakeProc:
    """asyncio 子进程 stub：可编程 returncode/stdout/stderr，记录写入 stdin 的 md。"""

    def __init__(
        self,
        returncode: int = 0,
        stdout: bytes = b"%PDF-1.4 fake\n",
        stderr: bytes = b"",
    ) -> None:
        self.returncode = returncode
        self._stdout = stdout
        self._stderr = stderr
        self.stdin_data: bytes | None = None

    # 形参名与 renderer 的调用方式一致（communicate(input=md)）
    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:  # noqa: A002
        self.stdin_data = input
        return self._stdout, self._stderr


def make_renderer(
    monkeypatch: pytest.MonkeyPatch,
    proc: FakeProc | None = None,
    exec_error: Exception | None = None,
) -> tuple[PandocXeLaTeXRenderer, list[tuple[Any, ...]]]:
    """构造环境全 stub 的 renderer，返回 (renderer, 收到的 pandoc 命令列表)。"""
    monkeypatch.setattr(renderer_module, "ensure_pandoc_available", lambda **kw: "/fake/pandoc")
    monkeypatch.setattr(
        PandocXeLaTeXRenderer, "_detect_chinese_font", staticmethod(lambda: "Noto Sans CJK SC")
    )

    seen_cmds: list[tuple[Any, ...]] = []

    async def fake_exec(*args: Any, **kwargs: Any) -> FakeProc:
        seen_cmds.append(args)
        if exec_error is not None:
            raise exec_error
        assert proc is not None
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    return PandocXeLaTeXRenderer(), seen_cmds


class TestRenderMarkdown:
    async def test_success_returns_pdf(self, monkeypatch: pytest.MonkeyPatch) -> None:
        proc = FakeProc(returncode=0, stdout=b"%PDF-1.4 fake\n")
        r, seen_cmds = make_renderer(monkeypatch, proc)

        md_bytes = "# 试卷 md".encode()
        pdf = await r.render_markdown(md_bytes, title="期末卷")

        assert pdf.startswith(b"%PDF")
        assert proc.stdin_data == md_bytes  # md 通过 stdin 喂给 pandoc
        cmd = list(seen_cmds[0])
        assert cmd[0] == "/fake/pandoc"
        assert "--pdf-engine=xelatex" in cmd
        assert "title=期末卷" in cmd

    async def test_nonzero_exit_raises_with_stderr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        proc = FakeProc(returncode=1, stderr=b"Undefined control sequence")
        r, _ = make_renderer(monkeypatch, proc)

        with pytest.raises(RuntimeError) as excinfo:
            await r.render_markdown(b"md", title="t")

        assert "退出码 1" in str(excinfo.value)
        assert "Undefined control sequence" in str(excinfo.value)

    async def test_invalid_pdf_output_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        proc = FakeProc(returncode=0, stdout=b"not a pdf")
        r, _ = make_renderer(monkeypatch, proc)

        with pytest.raises(RuntimeError, match="不是有效 PDF"):
            await r.render_markdown(b"md", title="t")

    async def test_timeout_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class TimeoutProc(FakeProc):
            async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:  # noqa: A002
                raise TimeoutError

        r, _ = make_renderer(monkeypatch, TimeoutProc())

        with pytest.raises(TimeoutError):
            await r.render_markdown(b"md", title="t")

    async def test_file_not_found_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        r, _ = make_renderer(monkeypatch, exec_error=FileNotFoundError("pandoc 缺失"))

        with pytest.raises(FileNotFoundError):
            await r.render_markdown(b"md", title="t")


class TestConstruction:
    def test_init_env_failure_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """pandoc 不可用（自动安装失败）：构造期即抛错，由上层转译为环境错误。"""
        monkeypatch.setattr(
            renderer_module,
            "ensure_pandoc_available",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("apt 安装失败")),
        )
        monkeypatch.setattr(
            PandocXeLaTeXRenderer, "_detect_chinese_font", staticmethod(lambda: None)
        )

        with pytest.raises(RuntimeError, match="apt 安装失败"):
            PandocXeLaTeXRenderer()

    async def test_stub_renderer_returns_placeholder(self) -> None:
        pdf = await StubRenderer().render_markdown(b"# md", title="t")
        assert pdf.startswith(b"%PDF")
        assert b"stub" in pdf
