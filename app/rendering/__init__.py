"""能力层：产出。"""

from app.rendering.markdown import sanitize_markdown
from app.rendering.renderer import PandocXeLaTeXRenderer, Renderer, get_renderer
from app.rendering.setup import ensure_pandoc_available, install_pandoc_dependencies

__all__ = [
    "Renderer",
    "PandocXeLaTeXRenderer",
    "sanitize_markdown",
    "get_renderer",
    "install_pandoc_dependencies",
    "ensure_pandoc_available",
]
