"""试卷 Markdown 清洗。

试卷由 agent 直写 output/paper.md（磁盘上永远是 agent 原稿，也是前端预览源）；
本模块只在渲染 PDF 前对喂给 pandoc 的字节做清洗，不回写文件。
"""

from __future__ import annotations

import re


def sanitize_markdown(md_text: str) -> str:
    """清理 markdown 中的字面量转义序列（如 `\\n`、`\\t`），
    避免 Pandoc 渲染为 PDF 时生成无效 LaTeX。

    LLM 有时会生成包含字面量 ``\\n``/``\\t`` 的代码片段，而不是真正的换行/制表符。
    此函数会：
    - 识别围栏代码块（fenced code blocks）内的字面量转义序列，并将其替换为实际字符；
    - 同时清理围栏代码块标记周围多余的空白，确保 Pandoc 正确识别代码块。
    """
    # 匹配模式：字面量 \n + ```lang + 字面量 \n + 代码内容 + 字面量 \n + ```
    # 使用 DOTALL 使 . 能匹配换行
    fence_pattern = re.compile(
        r"(?:\\n|\n)(```(?:[a-zA-Z0-9_+-]*)(?:\\n|\n))(.*?)((?:\\n|\n)```)",
        re.DOTALL,
    )

    def _replace_escapes_in_code(match: re.Match) -> str:
        opening = match.group(1)  # 包含 ```lang\n 或 ```lang
        code = match.group(2)
        closing = match.group(3)  # 包含 \n``` 或 \n```
        # 将围栏标记中的字面量 \n 转换为实际换行
        opening = opening.replace("\\n", "\n")
        closing = closing.replace("\\n", "\n")
        # 将代码内的字面量转义序列替换为实际字符
        code = code.replace("\\n", "\n")
        code = code.replace("\\t", "\t")
        code = code.replace('\\"', '"')
        code = code.replace("\\'", "'")
        # 确保代码块前后都有实际换行
        result = f"\n{opening}{code}{closing}"
        return result

    md_text = fence_pattern.sub(_replace_escapes_in_code, md_text)

    # 清理游离的字面量 ``\n`` / ``\t``（非代码块内），避免被 Pandoc 解释为
    # 无效 LaTeX 命令导致渲染失败。按前/后随字符区分两类情形：
    # - 后随非字母（行尾、标点前）：必为转义残留，转义；
    # - 前后都是单词字符（如 abc\ndef）：正文中的转义残留，转义；
    # - 其余（\neq、\times、\theta 等命令用法，前随非单词字符）：保留。
    md_text = re.sub(r"\\n(?![a-zA-Z])", r"\\\\n", md_text)
    md_text = re.sub(r"\\t(?![a-zA-Z])", r"\\\\t", md_text)
    md_text = re.sub(r"(?<=[A-Za-z0-9])\\n", r"\\\\n", md_text)
    md_text = re.sub(r"(?<=[A-Za-z0-9])\\t", r"\\\\t", md_text)

    return md_text
