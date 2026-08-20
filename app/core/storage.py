"""对象存储抽象接口 + 本地文件系统实现。

首版用本地文件系统代替对象存储（见 data-model.md 第 6 节的对象存储布局）。
后续可替换为 S3 / OSS 等，接口保持不变。
"""

from __future__ import annotations

import os
from pathlib import Path

from app.config import settings


class StorageError(Exception):
    pass


class Storage:
    """对象存储抽象接口。"""

    async def put(self, key: str, data: bytes) -> None:  # pragma: no cover
        raise NotImplementedError

    async def get(self, key: str) -> bytes:  # pragma: no cover
        raise NotImplementedError

    async def delete(self, key: str) -> None:  # pragma: no cover
        raise NotImplementedError

    def exists(self, key: str) -> bool:  # pragma: no cover
        raise NotImplementedError


class LocalStorage(Storage):
    """本地文件系统实现，`key` 为相对于 `settings.storage_dir` 的路径。"""

    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root else settings.storage_dir

    def _path(self, key: str) -> Path:
        # 防目录穿越
        p = (self.root / key).resolve()
        if not str(p).startswith(str(self.root.resolve())):
            raise StorageError(f"非法存储路径: {key}")
        return p

    async def put(self, key: str, data: bytes) -> None:
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        # 用临时文件 + 原子替换，避免写一半
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    async def get(self, key: str) -> bytes:
        p = self._path(key)
        if not p.exists():
            raise StorageError(f"存储对象不存在: {key}")
        return p.read_bytes()

    async def delete(self, key: str) -> None:
        p = self._path(key)
        if p.exists():
            p.unlink()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


storage: Storage = LocalStorage()
