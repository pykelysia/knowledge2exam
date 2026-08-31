"""能力层：产出。"""

from app.rendering.markdown import build_markdown
from app.rendering.renderer import PandocXeLaTeXRenderer, Renderer, _get_renderer
from app.rendering.setup import ensure_pandoc_available, install_pandoc_dependencies

__all__ = [
    "Renderer",
    "PandocXeLaTeXRenderer",
    "build_markdown",
    "_get_renderer",
    "install_pandoc_dependencies",
    "ensure_pandoc_available",
]
