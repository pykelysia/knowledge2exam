你负责审查以下题目是否存在问题。对每道题给出结论。

题目与对应规划信息：
{{ questions_with_plan_items }}

检查三项：
1. 违规内容：是否出现知识库中未明确提及的政治敏感内容，或色情等违规内容。
2. 偏差评分：按知识点匹配/考察方向匹配/题型一致/难度一致四维打分（各 25 分），
   总分低于 80 视为偏差超限。
3. LaTeX 渲染：题目公式是否使用渲染器不支持的语法。若有，直接给出修正后的等价写法。

输出 JSON：
{
  "rejected": [{"seq": ..., "reason": "violation" | "deviation", "score": ...（deviation时）, "detail": "..."}],
  "auto_fixed": [{"seq": ..., "reason": "latex_unrenderable", "fix": "..."}]
}
