# knowledge2exam 开发文档

试卷生成工具的开发文档集。产品构想的源头是 [idea.md](./idea.md)，本目录其余文档是在其基础上收敛出的可执行规格。

**idea.md 不随实现变更**，它保留为需求源头；当实现与 idea.md 冲突时，以本目录中的规格文档为准，并在文档中注明偏离原因。

## 文档索引

| 文档 | 内容 | 主要读者 |
| --- | --- | --- |
| [idea.md](./idea.md) | 原始产品构想（需求源头，只读） | 全体 |
| [prd.md](./prd.md) | 需求规格：功能/非功能需求、用户流程、验收标准、边界情况 | 产品、前端、后端 |
| [architecture.md](./architecture.md) | 分层架构、模块划分、全链路时序、任务状态机 | 后端、运维 |
| [data-model.md](./data-model.md) | 关系表结构、向量库 metadata、对象存储布局 | 后端 |
| [api.md](./api.md) | REST 端点（含鉴权）、请求/响应 schema、SSE 事件、错误码 | 前端、后端 |
| [openapi.yaml](./openapi.yaml) | 接口的机器可读 OpenAPI 3.0 契约（api.md 的权威版本，供 Swagger UI / SDK 生成） | 前端、后端 |
| [agent-design.md](./agent-design.md) | Agent 职责、Tool schema、核心算法、重试协议、提示词模板 | 后端、算法 |
| [tech-selection.md](./tech-selection.md) | 各能力位的候选技术对比（**不含最终决定**） | 技术决策者 |

## 建议阅读顺序

```
idea.md → prd.md → architecture.md → data-model.md → api.md → agent-design.md
```

`tech-selection.md` 可随时单独阅读，它不是其他文档的前置。

- 只关心接口的前端开发：`prd.md` 的用户流程 + `api.md` 全文
- 实现出题逻辑的开发：`agent-design.md` 全文 + `data-model.md` 的向量库部分
- 做技术选型的：`tech-selection.md` + `architecture.md` 的分层图

## 术语表

全部文档使用下表的统一命名。**代码中的标识符也应与此一致**。

| 中文 | 英文 / 标识符 | 含义 |
| --- | --- | --- |
| 上传件 | `upload` | 用户单次上传的一个文件或一段手动输入文本，是用户可见的最小单位 |
| 资源 | `resource` | 上传件经解析后的产物，携带内容类型与归属信息 |
| 内容类型 | `source_type` | 资源的内容分类，取值见下表 |
| 切块 | `chunk` | 资源为进入向量库而切分出的文本片段 |
| 生成任务 | `job` | 用户一次「生成试卷」请求对应的异步任务 |
| 阶段 | `stage` | 任务的处理阶段（预处理、规划、出题、审查、渲染） |
| 规划项 | `plan_item` | 规划 agent 为一道题产出的设计说明，出题前的蓝图 |
| 题目 | `question` | 子 agent 依据规划项实际产出的成题 |
| 试卷 | `paper` | 一次任务最终合成的 md / PDF 产物 |
| 共享知识库 | shared library | 按学校 + 课程维度沉淀的、经安全检测的公共资源集合 |
| 排他类 | `exclusive` | 用户本次提供后即不再引入共享库同类内容的内容类型 |
| 叠加类 | `additive` | 无论用户是否提供都会叠加共享库同类内容的内容类型 |
| 换题 | replan | 某题重试耗尽后，由主 agent 更换其知识点或考察方向 |
| 访问令牌 | `access token` | 双 JWT 中短时效（15 分钟）的 JWT，随 `Authorization: Bearer` 携带，仅存前端内存 |
| 刷新令牌 | `refresh token` | 双 JWT 中长时效（30 天）的不透明令牌，用于换取新 access token，服务端只存哈希 |
| 刷新链族 | `family_id` | 一次登录后连续轮换产生的 refresh token 链，用于重放检测与整链吊销 |

### 内容类型（`source_type`）取值

这套枚举值在 `prd.md`、`data-model.md`、`api.md`、`agent-design.md` 中必须完全一致。

| 值 | 中文 | 取用策略 | 预处理程度 |
| --- | --- | --- | --- |
| `book` | 书籍资料 | 叠加类 | 切块向量化 |
| `lecture` | 教师授课用 ppt/pdf | 叠加类 | 切块向量化 |
| `note` | 用户学习笔记 | 叠加类 | 切块向量化 |
| `keypoint_list` | 重点知识点清单 | 排他类 | 进提示词（过长则压缩） |
| `past_paper` | 往期试卷 | 排他类 | 整份留存于可快读存储 |
| `manual_text` | 用户手动输入文本 | 不入共享库 | 进提示词（过长则压缩） |
| `extra_requirement` | 用户额外要求 | 不入共享库 | 进提示词（过长则压缩） |

`manual_text` 与 `extra_requirement` **不可共享**，这是数据库约束而非前端约定，详见 [data-model.md](./data-model.md)。

## 文档约定

- 需求条目用 `FR-n`（功能需求）、`NFR-n`（非功能需求）编号，其他文档引用时使用该编号
- 错误码全大写下划线，如 `INPUT_EMPTY`，定义在 `api.md`，其他文档不得自造
- 图表统一使用 Mermaid
- 标注 `【待定】` 的内容表示尚未决策，需在实现前确认
- 标注 `【待核实】` 的内容表示数据来源未经验证，不可直接作为决策依据
