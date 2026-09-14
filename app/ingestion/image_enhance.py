"""扫描页图像增强：在送视觉 OCR 前改善低质量渲染图的可读性。

零依赖 PIL 处理：灰度 → 自动对比度（裁剪 1% 极值）→ 轻锐化。
任何一步失败都返回原图，绝不阻塞 OCR 链路。
"""

from __future__ import annotations

import io


def enhance_scan_image(png: bytes) -> bytes:
    """增强扫描页渲染图（PNG）；失败时原样返回。"""
    try:
        from PIL import Image, ImageFilter, ImageOps

        with Image.open(io.BytesIO(png)) as img:
            gray = ImageOps.grayscale(img)
            gray = ImageOps.autocontrast(gray, cutoff=1)
            gray = gray.filter(ImageFilter.UnsharpMask(radius=1, percent=80, threshold=2))
            out = io.BytesIO()
            gray.convert("RGB").save(out, format="PNG")
            return out.getvalue()
    except Exception:
        return png
