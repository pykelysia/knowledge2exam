"""出题 Tool Schema 与校验。

三个 tool：create_choice_question / create_blank_question / create_short_answer_question。
schema 定义见 agent-design.md 第 7 节。
"""

from __future__ import annotations

from app.core.exceptions import AppException, ErrorCode


class SchemaInvalid(AppException):
    def __init__(self, message: str) -> None:
        super().__init__(ErrorCode.INPUT_EMPTY, message, status_code=400)


# ---------------------------------------------------------------------------
# Tool definitions（传给 LLM 的 function calling schema）
# ---------------------------------------------------------------------------

TOOL_CREATE_CHOICE_QUESTION: dict = {
    "type": "function",
    "function": {
        "name": "create_choice_question",
        "description": "提交一道选择题",
        "parameters": {
            "type": "object",
            "properties": {
                "stem": {"type": "string", "description": "题干"},
                "options": {
                    "type": "object",
                    "properties": {
                        "A": {"type": "string"},
                        "B": {"type": "string"},
                        "C": {"type": "string"},
                        "D": {"type": "string"},
                    },
                    "required": ["A", "B", "C", "D"],
                },
                "answer": {"type": "string", "enum": ["A", "B", "C", "D"]},
            },
            "required": ["stem", "options", "answer"],
        },
    },
}

TOOL_CREATE_BLANK_QUESTION: dict = {
    "type": "function",
    "function": {
        "name": "create_blank_question",
        "description": "提交一道填空题",
        "parameters": {
            "type": "object",
            "properties": {
                "stem": {"type": "string", "description": "题干，空位用 ______ 表示"},
                "answer": {"type": "string"},
            },
            "required": ["stem", "answer"],
        },
    },
}

TOOL_CREATE_SHORT_ANSWER_QUESTION: dict = {
    "type": "function",
    "function": {
        "name": "create_short_answer_question",
        "description": "提交一道简答题，可含子问题",
        "parameters": {
            "type": "object",
            "properties": {
                "stem": {"type": "string", "description": "题干（背景/引题部分）"},
                "sub_questions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选。若有子问题，按顺序列出",
                },
                "answer": {"type": "string", "description": "无子问题时的完整答案；有子问题时可写「见各子问题答案」"},
                "sub_answers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "与 sub_questions 按索引严格对应，长度必须相等",
                },
            },
            "required": ["stem", "answer"],
        },
    },
}

ALL_TOOLS = [
    TOOL_CREATE_CHOICE_QUESTION,
    TOOL_CREATE_BLANK_QUESTION,
    TOOL_CREATE_SHORT_ANSWER_QUESTION,
]

TOOL_NAME_TO_TYPE: dict[str, str] = {
    "create_choice_question": "choice",
    "create_blank_question": "blank",
    "create_short_answer_question": "short_answer",
}


# ---------------------------------------------------------------------------
# 校验逻辑
# ---------------------------------------------------------------------------

def validate_choice_args(args: dict) -> None:
    options = args.get("options")
    if not options or not isinstance(options, dict):
        raise SchemaInvalid("选择题 options 必须是非空对象")
    for key in ("A", "B", "C", "D"):
        if key not in options or not options[key]:
            raise SchemaInvalid(f"选择题 options 缺少 {key} 选项")
    answer = args.get("answer")
    if answer not in ("A", "B", "C", "D"):
        raise SchemaInvalid(f"选择题 answer 必须为 A/B/C/D，实际为 {answer}")


def validate_blank_args(args: dict) -> None:
    stem = args.get("stem")
    if not stem or not isinstance(stem, str):
        raise SchemaInvalid("填空题 stem 不能为空")
    answer = args.get("answer")
    if answer is None or (isinstance(answer, str) and not answer.strip()):
        raise SchemaInvalid("填空题 answer 不能为空")


def validate_short_answer_args(args: dict) -> None:
    sub_q = args.get("sub_questions")
    sub_a = args.get("sub_answers")
    if (sub_q is None) != (sub_a is None):
        raise SchemaInvalid("sub_questions 与 sub_answers 必须同时提供或同时省略")
    if sub_q is not None and len(sub_q) != len(sub_a):
        raise SchemaInvalid(
            f"sub_questions 长度 {len(sub_q)} 与 sub_answers 长度 {len(sub_a)} 不一致"
        )


VALIDATORS: dict[str, callable] = {  # type: ignore[type-arg]
    "create_choice_question": validate_choice_args,
    "create_blank_question": validate_blank_args,
    "create_short_answer_question": validate_short_answer_args,
}


def validate_tool_call(tool_name: str, args: dict) -> None:
    """校验 tool 调用参数，不合法则抛 SchemaInvalid。"""
    validator = VALIDATORS.get(tool_name)
    if validator:
        validator(args)


def tool_name_to_question_type(tool_name: str) -> str:
    return TOOL_NAME_TO_TYPE.get(tool_name, tool_name)
