"""PDF 解析器：使用 PyMuPDF / pymupdf4llm 提取文本和内嵌图片。"""

from __future__ import annotations

import io

from app.ingestion.parsers.base import ImageInfo, Parser, ParseResult


class PyMuPDFParser(Parser):
    """PDF 解析器，使用 pymupdf4llm 提取 Markdown 文本和内嵌图片。"""

    async def parse(self, filename: str, data: bytes) -> ParseResult:
        try:
            import pymupdf4llm
        except ImportError as exc:
            raise RuntimeError("pymupdf4llm 未安装，请运行 `pip install pymupdf4llm`") from exc

        # pymupdf4llm 接受文件路径或文件流
        doc = pymupdf4llm.Document(io.BytesIO(data))

        # 逐页提取文本，记录页码
        full_text_parts: list[str] = []
        page_count = len(doc)
        has_text = False
        images: list[ImageInfo] = []

        for page_num in range(page_count):
            page = doc[page_num]
            text = page.get_text()
            if text.strip():
                has_text = True
            full_text_parts.append(f"--- Page {page_num + 1} ---\n{text}")

            # 提取页面内嵌图片
            page_images = self._extract_page_images(doc, page, page_num)
            images.extend(page_images)

        doc.close()

        text = "\n\n".join(full_text_parts)

        # 如果没有提取到任何文本，可能是扫描件 PDF
        if not has_text:
            text = f"（扫描版 PDF {filename}，共 {page_count} 页，无文本层）"

        return ParseResult(
            char_count=len(text),
            page_count=page_count,
            excerpt=text[:200],
            text=text,
            images=images if images else None,
        )

    @staticmethod
    def _extract_page_images(doc, page, page_num: int) -> list[ImageInfo]:
        """提取单页中的内嵌图片。"""
        result: list[ImageInfo] = []
        try:
            img_list = page.get_images()
        except Exception:
            return result

        for img_index, img_item in enumerate(img_list):
            xref = img_item[0]
            try:
                base_image = doc.extract_image(xref)
                if base_image is None:
                    continue
                image_bytes = base_image.get("image")
                if image_bytes is None:
                    continue

                # 尝试获取图片在页面中的位置
                bbox = None
                try:
                    rect = page.get_image_rects(img_item[0])
                    if rect:
                        bbox = (rect[0].x0, rect[0].y0, rect[0].x1, rect[0].y1)
                except Exception:
                    pass

                result.append(
                    ImageInfo(
                        page=page_num + 1,
                        data=image_bytes,
                        bbox=bbox,
                        order=len(result),
                    )
                )
            except Exception:
                # 单张图片提取失败不影响整体
                continue

        return result
