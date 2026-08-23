"""进度事件总线（编排层 re-export）。

实际实现位于 app.core.events，编排层通过此模块保持向后兼容。
"""

from __future__ import annotations

from app.core.events import EventBus, has_subscribers, subscribe, unsubscribe

__all__ = ["EventBus", "subscribe", "unsubscribe", "has_subscribers"]
