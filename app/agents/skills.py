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
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]*$")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_DESCRIPTION_RE = re.compile(r"^description:\s*(.+)$", re.MULTILINE)

# 课程偏好聚合伪技能的固定名称（load_skill 用）
COURSE_PREFERENCE_SKILL_NAME = "course-preferences"


class SkillError(Exception):
    """技能加载错误。"""


logger = logging.getLogger(__name__)


class SkillSource(Protocol):
    """技能源接口：SkillLoader 与 CompositeSkillLoader 的公共面。"""

    def discover(self) -> list[SkillMeta]: ...

    def load(self, name: str) -> str: ...

    def index_prompt(self) -> str: ...


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


@dataclass(slots=True)
class StaticSkill:
    """内存技能条目：由调用方预加载内容（如课程偏好聚合的伪技能）。"""

    name: str
    description: str
    content: str


class CompositeSkillLoader:
    """多源技能加载器：目录源（SkillLoader）+ 静态条目的合并视图。

    同名时静态条目优先（覆盖内置同名技能）；接口与 SkillLoader 一致，
    load_skill 工具与提示词渲染无感切换。
    """

    def __init__(self, primary: SkillSource, extra: Sequence[StaticSkill]) -> None:
        self._primary = primary
        self._extra = list(extra)

    def discover(self) -> list[SkillMeta]:
        """目录技能与静态条目合并（旧→名称排序）；同名时静态条目覆盖目录条目。"""
        shadowed = {s.name for s in self._extra}
        skills = [s for s in self._primary.discover() if s.name not in shadowed]
        skills.extend(SkillMeta(name=s.name, description=s.description) for s in self._extra)
        return sorted(skills, key=lambda s: s.name)

    def load(self, name: str) -> str:
        """读取技能全文；静态条目命中优先，否则回落目录源。"""
        for skill in self._extra:
            if skill.name == name:
                return skill.content
        return self._primary.load(name)

    def index_prompt(self) -> str:
        """渲染进 system prompt 的技能索引段落（静态条目与目录技能同列）。"""
        skills = self.discover()
        if not skills:
            return "（当前没有可用技能）"
        return "\n".join(f"- `{s.name}`：{s.description}" for s in skills)


def build_course_preference_skill(preferences: Sequence[str]) -> StaticSkill:
    """把课程偏好条目聚合为单个伪技能（进索引，agent 一次 load_skill 读全）。"""
    lines = "\n".join(f"{i}. {text}" for i, text in enumerate(preferences, 1))
    content = (
        "# 本课程出题偏好（历史修订沉淀）\n\n"
        f"以下 {len(preferences)} 条偏好来自本校本课程既往修订轮的用户反馈，"
        f"是对出题的持久性要求，本次出题必须逐条遵守：\n\n{lines}\n"
    )
    description = (
        f"【本课程偏好·必读】本课程历史修订沉淀的出题偏好（{len(preferences)} 条），"
        f"动笔前必须 load_skill 通读并在本卷中遵守"
    )
    return StaticSkill(
        name=COURSE_PREFERENCE_SKILL_NAME, description=description, content=content
    )


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
