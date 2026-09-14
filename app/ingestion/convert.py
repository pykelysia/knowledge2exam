"""格式转换层：把非 PDF 的二进制上传件规范化为 PDF，统一进入混合解析管线。

- docx / pptx：LibreOffice（soffice）无头转换
- 图片：用 pymupdf 包成单页 PDF（EXIF 方向矫正后按比例适配 A4 页面）
- pdf：直通

soffice 缺失或转换失败时返回 converted=False，由调用方回落原生解析器，
此处不抛异常；文件本身损坏的情形最终会在解析阶段以 PARSE_FAILED 告警降级。
"""

from __future__ import annotations

import asyncio
import inspect
import io
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

from app.ingestion.parsers.base import Parser, ParseResult

logger = logging.getLogger(__name__)

_CONVERTIBLE_EXTS = {".docx", ".pptx"}
_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
_SOFFICE_TIMEOUT_SECONDS = 120
# 图片包装用 A4 页面（pt）；视觉模型对 ~1650px 宽的渲染图识别效果稳定
_PAGE_WIDTH, _PAGE_HEIGHT = 595.0, 842.0


def soffice_available() -> bool:
    return shutil.which("soffice") is not None or shutil.which("libreoffice") is not None


def to_pdf(filename: str, data: bytes) -> tuple[bytes, bool]:
    """把上传件规范化为 PDF，返回 (pdf_bytes, converted)。

    converted=False 时 data 原样返回，调用方应回落到原生解析器。
    """
    ext = Path(filename).suffix.lower()
    if ext == ".pdf":
        return data, False
    if ext in _IMAGE_EXTS:
        pdf = _image_to_pdf(data)
        if pdf is not None:
            return pdf, True
        return data, False
    if ext in _CONVERTIBLE_EXTS:
        pdf = _convert_via_soffice(filename, data)
        if pdf is not None:
            return pdf, True
        logger.warning("%s 转 PDF 失败（soffice 缺失或转换出错），将回落原生解析器", filename)
        return data, False
    return data, False


class ConvertedToPdfParser(Parser):
    """先把上传件规范化为 PDF，再走 PDF 混合解析；转换不可用时回落后备解析器。"""

    def __init__(self, fallback: Parser | None = None) -> None:
        # 延迟导入避免 parsers/__init__ 与本模块的循环导入
        from app.ingestion.parsers.pdf import PyMuPDFParser

        self._pdf_parser = PyMuPDFParser()
        self._fallback = fallback

    def parse(self, filename: str, data: bytes) -> ParseResult:
        pdf_bytes, converted = to_pdf(filename, data)
        if converted:
            return self._pdf_parser.parse(_pdf_name(filename), pdf_bytes)
        if self._fallback is not None:
            if inspect.iscoroutinefunction(self._fallback.parse):
                # 异步后备解析器（ImageParser）：本方法在 to_thread 工作线程内
                # 执行，无运行中的事件循环，用 asyncio.run 桥接
                return asyncio.run(self._fallback.parse_async(filename, data))
            return self._fallback.parse(filename, data)
        raise ValueError(f"无法将 {filename} 规范化为 PDF")


def _pdf_name(filename: str) -> str:
    return Path(filename).stem + ".pdf"


def _image_to_pdf(data: bytes) -> bytes | None:
    """把图片包成单页 PDF：EXIF 方向矫正，按比例适配 A4 页面居中放置。"""
    try:
        normalized = _normalize_image(data)
        doc = pymupdf.open()
        page = doc.new_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
        page.insert_image(page.rect, stream=normalized, keep_proportion=True)
        out = doc.tobytes()
        doc.close()
        return out
    except Exception as exc:
        logger.warning("图片包装 PDF 失败: %s", exc)
        return None


def _normalize_image(data: bytes) -> bytes:
    """EXIF 矫正 + 统一转 RGB JPEG，避免旋转方向丢失；PIL 搞不定则原样返回。"""
    try:
        from PIL import Image, ImageOps

        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=92)
        return buffer.getvalue()
    except Exception:
        return data


def _convert_via_soffice(filename: str, data: bytes) -> bytes | None:
    binary = shutil.which("soffice") or shutil.which("libreoffice")
    if binary is None:
        return None
    suffix = Path(filename).suffix.lower()
    try:
        with tempfile.TemporaryDirectory(prefix="k2e_convert_") as tmp:
            src = Path(tmp) / f"input{suffix}"
            src.write_bytes(data)
            # 独立 UserInstallation 避免 LibreOffice 单实例锁导致并发转换互相失败
            result = subprocess.run(
                [
                    binary,
                    "--headless",
                    f"-env:UserInstallation=file://{tmp}/lo_profile",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    tmp,
                    str(src),
                ],
                capture_output=True,
                text=True,
                timeout=_SOFFICE_TIMEOUT_SECONDS,
                check=False,
            )
            pdf_path = src.with_suffix(".pdf")
            if result.returncode != 0 or not pdf_path.exists():
                logger.warning(
                    "soffice 转换失败: %s %s", result.stdout.strip(), result.stderr.strip()
                )
                return None
            return pdf_path.read_bytes()
    except Exception as exc:
        logger.warning("soffice 转换异常: %s", exc)
        return None
