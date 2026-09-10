"""Skill system：从技能目录加载 markdown 技能，注入索引、按需读取全文。

技能布局：`<skills_dir>/<skill-name>/SKILL.md`，文件开头可选 frontmatter：

    ---
    description: 一句话描述（展示在技能索引中）
    ---

无 frontmatter 时取正文第一个非标题段落作为描述。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_DESCRIPTION_RE = re.compile(r"^description:\s*(.+)$", re.MULTILINE)


class SkillError(Exception):
    """技能加载错误。"""


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SkillMeta:
    """技能元信息（索引用）。"""

    name: str
    description: str


class SkillLoader:
    """技能目录扫描器与加载器。"""

    def __init__(self, root: Path) -> None:
        self._root = Path(root)

    def discover(self) -> list[SkillMeta]:
        """扫描全部技能，按名称排序。目录损坏的技能跳过并记日志。"""
        if not self._root.is_dir():
            return []
        skills: list[SkillMeta] = []
        for skill_dir in sorted(self._root.iterdir()):
            skill_file = skill_dir / "SKILL.md"
            if not skill_dir.is_dir() or not skill_file.is_file():
                continue
            if not _SKILL_NAME_RE.match(skill_dir.name):
                logger.warning("跳过命名不合规的技能目录: %s", skill_dir.name)
                continue
            try:
                description = _extract_description(skill_file.read_text(encoding="utf-8"))
            except OSError:
                logger.warning("技能文件不可读: %s", skill_file)
                continue
            skills.append(SkillMeta(name=skill_dir.name, description=description))
        return skills

    def load(self, name: str) -> str:
        """读取技能全文；名称非法或技能不存在时抛 SkillError。"""
        if not _SKILL_NAME_RE.match(name):
            raise SkillError(f"非法技能名: {name!r}")
        skill_file = self._root / name / "SKILL.md"
        if not skill_file.is_file():
            available = ", ".join(s.name for s in self.discover()) or "无"
            raise SkillError(f"技能不存在: {name}（可用：{available}）")
        try:
            return skill_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise SkillError(f"技能文件不可读: {name}") from exc

    def index_prompt(self) -> str:
        """渲染进 system prompt 的技能索引段落。"""
        skills = self.discover()
        if not skills:
            return "（当前没有可用技能）"
        return "\n".join(f"- `{s.name}`：{s.description}" for s in skills)


def _extract_description(text: str) -> str:
    """优先取 frontmatter 的 description，否则取正文第一个非标题段落。"""
    match = _FRONTMATTER_RE.match(text)
    if match:
        desc = _DESCRIPTION_RE.search(match.group(1))
        if desc:
            return desc.group(1).strip().strip('"').strip("'")
        body = text[match.end() :]
    else:
        body = text
    for para in body.split("\n\n"):
        para = para.strip()
        if para and not para.startswith("#"):
            return para.replace("\n", " ")[:120]
    return ""
