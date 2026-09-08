"""Agent 工作区：以对象存储为后端的受控文件面。

agent 的所有文件操作（read_file / edit_file）都经此收口：
- 路径限制在工作区前缀内，防目录穿越；
- 写入走 Storage.put 的原子替换语义；
- edit 提供创建（old_string 为空）与唯一匹配替换两种语义。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.storage import Storage

# 相对路径白名单：字母/数字/下划线/连字符/点/斜杠，不允许反斜杠与空白
_REL_PATH_RE = re.compile(r"^(?!/)[A-Za-z0-9][A-Za-z0-9_\-./]*$")


class WorkspaceError(Exception):
    """工作区操作错误（路径非法 / 文件不存在 / 替换不唯一等）。"""


@dataclass
class EditResult:
    """一次 edit 的结果。"""

    created: bool
    content: str


class Workspace:
    """工作区：`jobs/{job_id}/agent/` 前缀下的受控文件视图。

    materials/ 由预处理阶段写入（用户材料），questions/ 由 agent 产出。
    """

    def __init__(self, storage: Storage, prefix: str) -> None:
        self._storage = storage
        self.prefix = prefix.strip("/")

    # ------------------------------------------------------------------
    # 路径
    # ------------------------------------------------------------------

    def _key(self, rel: str) -> str:
        rel = rel.strip()
        if not rel or not _REL_PATH_RE.match(rel) or ".." in rel.split("/"):
            raise WorkspaceError(f"非法的工作区路径: {rel!r}")
        return f"{self.prefix}/{rel}"

    # ------------------------------------------------------------------
    # 基础操作
    # ------------------------------------------------------------------

    async def exists(self, rel: str) -> bool:
        return self._storage.exists(self._key(rel))

    async def write(self, rel: str, content: str) -> None:
        await self._storage.put(self._key(rel), content.encode("utf-8"))

    async def read(self, rel: str, max_chars: int | None = None) -> str:
        """读取文件文本；max_chars 超限时截断并附提示。"""
        key = self._key(rel)
        if not self._storage.exists(key):
            raise WorkspaceError(f"文件不存在: {rel}")
        text = (await self._storage.get(key)).decode("utf-8", errors="replace")
        if max_chars is not None and len(text) > max_chars:
            note = f"\n\n[内容已截断：共 {len(text)} 字符，仅显示前 {max_chars} 字符]"
            return text[:max_chars] + note
        return text

    async def list_dir(self, rel_dir: str) -> list[str]:
        """列出目录下的文件（返回目录内相对路径，排序）。"""
        dir_prefix = f"{self._key(rel_dir)}/"
        keys = await self._storage.list(dir_prefix)
        return [k[len(dir_prefix):] for k in keys]

    # ------------------------------------------------------------------
    # edit 语义
    # ------------------------------------------------------------------

    async def edit(self, rel: str, old_string: str, new_string: str) -> EditResult:
        """创建或替换文件内容。

        - old_string 为空：创建新文件（已存在则报错）；
        - 否则：old_string 必须在文件中恰好出现一次，替换为 new_string。
        """
        if old_string == "":
            if await self.exists(rel):
                raise WorkspaceError(f"文件已存在，不能重复创建: {rel}（请用替换语义修改）")
            await self.write(rel, new_string)
            return EditResult(created=True, content=new_string)

        content = await self.read(rel)
        count = content.count(old_string)
        if count == 0:
            raise WorkspaceError(f"old_string 在 {rel} 中未找到，请先用 read_file 确认内容")
        if count > 1:
            raise WorkspaceError(
                f"old_string 在 {rel} 中出现 {count} 次，请提供更长的上下文以唯一匹配"
            )
        new_content = content.replace(old_string, new_string, 1)
        await self.write(rel, new_content)
        return EditResult(created=False, content=new_content)
