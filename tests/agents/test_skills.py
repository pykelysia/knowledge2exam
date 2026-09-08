"""skills 模块单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.skills import SkillError, SkillLoader


@pytest.fixture
def skills_dir(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    (root / "alpha").mkdir(parents=True)
    (root / "alpha" / "SKILL.md").write_text(
        "---\ndescription: Alpha 技能说明\n---\n\n# Alpha\n\n正文\n",
        encoding="utf-8",
    )
    (root / "beta").mkdir()
    (root / "beta" / "SKILL.md").write_text(
        "# Beta\n\nBeta 第一个段落说明，较长一些。\n\n第二段\n",
        encoding="utf-8",
    )
    # 不合规目录：缺少 SKILL.md / 命名不合法
    (root / "empty_dir").mkdir()
    (root / "Bad_Name").mkdir()
    (root / "Bad_Name" / "SKILL.md").write_text("x", encoding="utf-8")
    return root


class TestDiscover:
    def test_discover_sorted_and_filtered(self, skills_dir: Path) -> None:
        loader = SkillLoader(skills_dir)
        skills = loader.discover()
        assert [s.name for s in skills] == ["alpha", "beta"]

    def test_frontmatter_description(self, skills_dir: Path) -> None:
        (alpha,) = [s for s in SkillLoader(skills_dir).discover() if s.name == "alpha"]
        assert alpha.description == "Alpha 技能说明"

    def test_fallback_first_paragraph(self, skills_dir: Path) -> None:
        (beta,) = [s for s in SkillLoader(skills_dir).discover() if s.name == "beta"]
        assert beta.description.startswith("Beta 第一个段落")

    def test_missing_root_returns_empty(self, tmp_path: Path) -> None:
        assert SkillLoader(tmp_path / "nope").discover() == []


class TestLoad:
    def test_load_full_text(self, skills_dir: Path) -> None:
        text = SkillLoader(skills_dir).load("alpha")
        assert "正文" in text and text.startswith("---")

    def test_load_unknown_raises_with_available(self, skills_dir: Path) -> None:
        with pytest.raises(SkillError, match="alpha"):
            SkillLoader(skills_dir).load("gamma")

    def test_load_invalid_name_raises(self, skills_dir: Path) -> None:
        with pytest.raises(SkillError, match="非法技能名"):
            SkillLoader(skills_dir).load("../escape")


class TestIndexPrompt:
    def test_index_lines(self, skills_dir: Path) -> None:
        index = SkillLoader(skills_dir).index_prompt()
        assert index.count("\n") == 1
        assert "- `alpha`：Alpha 技能说明" in index
        assert "- `beta`：" in index

    def test_empty_index(self, tmp_path: Path) -> None:
        assert SkillLoader(tmp_path).index_prompt() == "（当前没有可用技能）"
