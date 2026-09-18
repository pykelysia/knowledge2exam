"""markdown 清洗单元测试：sanitize_markdown 只清理游离转义，不误伤 LaTeX 命令。"""

from __future__ import annotations

from app.rendering.markdown import sanitize_markdown


class TestSanitizeMarkdown:
    def test_latex_commands_preserved(self) -> None:
        """\\times / \\neq / \\theta 等真实数学命令不能被改写成双重转义。"""
        md = "已知 $a \\times b \\neq 0$ 且 $\\theta \\in (0, \\pi)$，则 ______"

        out = sanitize_markdown(md)

        assert "\\times" in out
        assert "\\neq" in out
        assert "\\theta" in out
        assert "\\\\times" not in out
        assert "\\\\neq" not in out

    def test_standalone_literal_escape_still_cleaned(self) -> None:
        """游离的字面量 \\n / \\t 仍被转义（原修复目标不回退）。"""
        md = "转义示例一：abc\\ndef；转义示例二：x\\ty；行尾：z\\n"

        out = sanitize_markdown(md)

        assert "abc\\\\ndef" in out
        assert "x\\\\ty" in out
        assert "z\\\\n" in out

    def test_fenced_code_literal_escapes_become_real_chars(self) -> None:
        """围栏代码块内的字面量 \\n/\\t 转为真实字符，围栏标记可被 pandoc 识别。"""
        md = "代码：\\n```python\\nx\\n= 1\\n```\\n"

        out = sanitize_markdown(md)

        assert "\n```python\nx\n= 1\n```" in out
