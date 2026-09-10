"""对象存储抽象接口 + 本地文件系统实现。

首版用本地文件系统代替对象存储（见 data-model.md 第 6 节的对象存储布局）。
后续可替换为 S3 / OSS 等，接口保持不变。

已知取舍：LocalStorage 内部为同步文件 I/O（本地盘、单文件较小，暂可接受）；
若并发写入成为瓶颈，再改为线程池 / aiofiles，接口不变。
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

    async def list(self, prefix: str) -> list[str]:  # pragma: no cover
        raise NotImplementedError

    def exists(self, key: str) -> bool:  # pragma: no cover
        raise NotImplementedError


class LocalStorage(Storage):
    """本地文件系统实现，`key` 为相对于 `settings.storage_dir` 的路径。"""

    def __init__(self, root: Path | None = None) -> None:
        # 统一 resolve：settings.storage_dir 默认是相对路径（./storage），
        # 而 _path/list 均基于绝对路径比较，root 不 resolve 会让 list 的
        # relative_to 直接抛 ValueError。
        self.root = (Path(root) if root else settings.storage_dir).resolve()

    def _path(self, key: str) -> Path:
        # 防目录穿越：is_relative_to 按路径分量比较，
        # 兄弟目录（如 root=/a/storage、目标 /a/storage2/x）不会误判通过
        p = (self.root / key).resolve()
        if not p.is_relative_to(self.root):
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

    async def list(self, prefix: str) -> list[str]:
        """列出前缀下的所有 key（排序后返回）。"""
        base = self._path(prefix)
        if not base.exists():
            return []
        return sorted(p.relative_to(self.root).as_posix() for p in base.rglob("*") if p.is_file())

    def exists(self, key: str) -> bool:
        return self._path(key).exists()


storage: Storage = LocalStorage()
