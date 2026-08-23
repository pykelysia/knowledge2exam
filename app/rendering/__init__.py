"""能力层：产出。"""

from app.rendering.markdown import build_markdown
from app.rendering.renderer import Renderer, StubRenderer

__all__ = ["Renderer", "StubRenderer", "build_markdown"]
