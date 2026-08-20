"""跨层共享的枚举。置于 core 层以避免 models ↔ schemas 之间的反向依赖。"""

from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    """内容类型全集，取值见 docs/README.md 术语表。"""

    book = "book"
    lecture = "lecture"
    note = "note"
    keypoint_list = "keypoint_list"
    past_paper = "past_paper"
    manual_text = "manual_text"
    extra_requirement = "extra_requirement"


# 文件类内容类型（multipart 上传）
FILE_SOURCE_TYPES = {
    SourceType.book,
    SourceType.lecture,
    SourceType.note,
    SourceType.keypoint_list,
    SourceType.past_paper,
}
# 手动输入类内容类型（application/json 提交），不可共享
TEXT_SOURCE_TYPES = {SourceType.manual_text, SourceType.extra_requirement}
