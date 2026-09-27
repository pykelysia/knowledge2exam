"""结构化输出的统一恢复调用：解析失败时手工提取 JSON 并重试一次。

OpenAI 兼容端点可能不支持 response_format=json_schema（或模型输出
Markdown/围栏文本），导致结构化解析失败。本模块把「include_raw 取
原文 → 手工抠出 JSON 校验 → 带纠正指令重试一次」的降级链路收敛为
一处，供 intent / preference 等结构化调用共用。
"""

from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

# 首次调用 + 一次重试
_MAX_ATTEMPTS = 2

# 重试时追加的纠正指令：同输入重试必然同失败（参见 agent.py 修订轮的兜底结论），
# 必须改变输入才可能得到不同结果
_RETRY_SUFFIX = (
    "\n\n注意：上一次输出无法解析为 JSON。"
    "请只输出一个符合目标结构的 JSON 对象，"
    "禁止 Markdown 标题、代码围栏或任何 JSON 以外的文字。"
)


async def structured_invoke[T: BaseModel](
    model: BaseChatModel,
    schema: type[T],
    prompt: str,
    *,
    label: str,
) -> T | None:
    """带恢复链路的结构化调用：全部降级手段失败时返回 None。

    调用方负责对 None 自行兜底（默认意图 / 原文保存等）。
    API 层异常（超时等）不抛出，同样按失败处理进入重试。
    """
    # method="function_calling"：默认的 json_schema 会被 openai SDK 在
    # 客户端解析（raw_response.parse() → model_validate_json），非 OpenAI
    # 后端返回 Markdown/围栏文本时在 llm 阶段直接抛 ValidationError，
    # 绕过 include_raw 只包解析器的 fallback 保护；工具调用协议的解析
    # 在解析器阶段进行，失败按契约落入 parsing_error 字段。
    runnable = model.with_structured_output(
        schema, include_raw=True, method="function_calling"
    )
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            result = await runnable.ainvoke(
                prompt if attempt == 1 else prompt + _RETRY_SUFFIX
            )
        except Exception as exc:
            logger.warning("%s调用失败（第 %d 次）", label, attempt, exc_info=exc)
            # llm 阶段抛出的异常可能携带模型原文，先尝试从中恢复再重试
            parsed = _recover_from_exception(schema, exc)
            if parsed is not None:
                return parsed
            continue
        parsed = _structured_result(schema, result)
        if parsed is not None:
            return parsed
        raw_text = _raw_text(result)
        parsed = _manual_parse(schema, raw_text)
        if parsed is not None:
            logger.info("%s结构化输出解析失败，已从原文手工恢复 JSON（第 %d 次）", label, attempt)
            return parsed
        logger.warning(
            "%s结构化输出解析失败且手工恢复无效（第 %d 次），原文片段: %.200s",
            label,
            attempt,
            raw_text,
        )
    return None


def _structured_result[T: BaseModel](schema: type[T], result: object) -> T | None:
    """解释 with_structured_output(include_raw=True) 的返回。

    兼容直接返回 schema 实例的替身/Runnable；字典形态要求
    parsed 有效且 parsing_error 为空，否则视为解析失败。
    """
    if isinstance(result, schema):
        return result
    if isinstance(result, dict):
        parsed = result.get("parsed")
        if result.get("parsing_error") is None and isinstance(parsed, schema):
            return parsed
    return None


def _raw_text(result: object) -> str:
    """从 include_raw 字典中取出模型的原始回复文本。"""
    if not isinstance(result, dict):
        return ""
    content = getattr(result.get("raw"), "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # 分块 content：拼接字符串块与 {"type": "text", "text": ...} 块
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return "".join(parts)
    return ""


def _manual_parse[T: BaseModel](schema: type[T], text: str) -> T | None:
    """从自由格式文本（Markdown/代码围栏/前后说明）中手工提取并校验结构化输出。"""
    candidates: list[str] = [text.strip()]
    candidates.extend(_balanced_blocks(text))
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return schema.model_validate_json(candidate)
        except ValueError:  # pydantic 的 JSON 解析与校验错误均继承 ValueError
            continue
    return None


def _recover_from_exception[T: BaseModel](
    schema: type[T], exc: BaseException
) -> T | None:
    """尽力从异常对象携带的原文中手工提取结构化输出（llm 阶段异常的兜底）。"""
    for text in _exception_texts(exc):
        parsed = _manual_parse(schema, text)
        if parsed is not None:
            return parsed
    return None


def _exception_texts(exc: BaseException) -> list[str]:
    """收集异常对象中可能携带的模型原始输出文本。

    - OutputParserException.llm_output：langchain 解析器携带的完整原文
    - ValidationError.errors()[0]["input"]：SDK 客户端解析失败时携带的
      完整原文（str(exc) 里的 input_value 是截断版，不可用）
    - str(exc)：兜底，异常消息本身可能内嵌原文
    """
    texts: list[str] = []
    llm_output = getattr(exc, "llm_output", None)
    if isinstance(llm_output, str):
        texts.append(llm_output)
    if isinstance(exc, ValidationError):
        for error in exc.errors():
            value = error.get("input")
            if isinstance(value, str):
                texts.append(value)
    texts.append(str(exc))
    return texts


def _balanced_blocks(text: str) -> list[str]:
    """单遍栈式扫描：给出每个配对成功的花括号块（按起点排序）。

    `{` 压栈、`}` 弹栈并记录 (start, end) 区间，字符串字面量内的
    花括号不计入；未闭合的 `{` 结束后留在栈中直接丢弃。每个字符
    恰好访问一次（O(n)），避免逐起点重扫在未闭合输入上的 O(n²)。
    """
    spans: list[tuple[int, int]] = []
    stack: list[int] = []
    in_string = False
    escaped = False
    for idx, cur in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif cur == "\\":
                escaped = True
            elif cur == '"':
                in_string = False
            continue
        if cur == '"':
            in_string = True
        elif cur == "{":
            stack.append(idx)
        elif cur == "}" and stack:
            spans.append((stack.pop(), idx))
    spans.sort()
    return [text[start : end + 1] for start, end in spans]
