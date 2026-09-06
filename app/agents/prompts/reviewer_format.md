你负责审查以下题目是否存在格式问题。对每道题给出结论。

题目与对应规划信息：
{{ questions_with_plan_items }}

仅检查格式问题（语法、结构），不检查内容正确性和违规内容。

输出 JSON：
{
  "rejected": [{"seq": ..., "reason": "format_error", "detail": "..."}],
  "auto_fixed": [{"seq": ..., "reason": "format_fix", "fix": "..."}]
}
