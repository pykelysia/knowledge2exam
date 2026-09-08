---
description: 题目文件 schema、出题规范与改写尺度。写 questions/NNN.json 前必读。
---

# 出题规范（exam-authoring）

## 题目文件 schema

每道题一个文件：`questions/NNN.json`，NNN 为三位题号（001、002……），内容为符合下述
schema 的 **纯 JSON**（不要包裹 markdown 代码块）：

```json
{
  "seq": 3,
  "question_type": "choice | blank | short_answer",
  "stem": "题干；填空题空位用 ______ 表示",
  "options": {"A": "...", "B": "...", "C": "...", "D": "..."},
  "answer": "B",
  "sub_questions": ["(1) ...", "(2) ..."],
  "sub_answers": ["...", "..."],
  "explanation": "答案解析"
}
```

字段规则：

| 题型 | 必填 | 禁止 |
| --- | --- | --- |
| choice | stem、options(A-D 全部非空)、answer(∈A/B/C/D) | sub_questions/sub_answers |
| blank | stem、answer | options、sub_questions/sub_answers |
| short_answer | stem、answer（有子问题时 answer 可写「见各子问题答案」） | options |

- `sub_questions` 与 `sub_answers` 必须同时提供或同时省略，且长度相等、按索引对应。
- `explanation`：用户开启解析开关时**每题必填**；未开启时**不要携带**该字段。
- `seq` 必须与文件名 NNN 及蓝图 todo 的 seq 一致。

## 命题质量要求

- 选择题：四个干扰项应有代表性——常见误解、相近概念、计算中间结果；正确答案
  A/B/C/D 均匀分布，同一份卷中不要集中在同一字母。
- 填空题：空位处应是关键概念、关键结论或关键数值，避免在无关紧要处挖空。
- 简答题：考察路径清晰可作答；子问题之间应有递进关系，各子问题可独立评分。
- 难度标注（easy/medium/hard）应与实际解题步数一致。

## 对往期试卷的改写尺度

- 原则：不得与原题高度雷同——换情境、换设问角度、换数值与表述。
- 例外一：简单计算题（数值代入、解法路径固定）可保留解法路径，仅修改数值与参数。
- 例外二：概念性填空题（对某现象/定义/性质的解释）可原样沿用。
- 推导、论述类内容必须实质改写，仅保留知识点层面的关联。

## 自查

全部题目完成后通读一遍：题号连续、题型与蓝图一致、选项风格不雷同、答案分布均衡、
同一知识点的相似考察方向不超过两次。
