"""跨层共享的枚举。置于 core 层以避免 models ↔ schemas 之间的反向依赖。"""

from __future__ import annotations

from enum import StrEnum


class SourceType(StrEnum):
    """内容类型全集（文件类 + 手动输入文本类）。"""

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

# 可向量化检索的内容类型（解析入库、任务检索白名单、公开池语义检索共用）
VECTORIZED_SOURCE_TYPES: tuple[SourceType, ...] = (
    SourceType.book,
    SourceType.lecture,
    SourceType.note,
)


class Visibility(StrEnum):
    """文件可见性。

    - private：仅本人可见
    - school：本校可见（观看者学校归属与文件一致）
    - public：全局公开池
    """

    private = "private"
    school = "school"
    public = "public"
