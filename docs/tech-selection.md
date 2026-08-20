# 技术选型

本文档记录各能力位在首版的技术选型决策。每个能力位给出**选型 + 理由 + 已知限制**；仍带【待定】/【待核实】标记的项需在落地前确认。

其余文档中对应能力位均以抽象接口描述（如「向量库」「任务队列」），不绑定具体产品，见 [architecture.md](./architecture.md#1-分层)。

标注【待定】表示需在实现前决策；标注【待核实】表示数据来源未经二次验证（如版本号、许可证细节随时间变化快），不可直接作为决策依据，落地前应对照官方文档复核。

## 选型总览

| 能力位 | 选型 | 状态 |
| --- | --- | --- |
| 文档解析 | PyMuPDF（pymupdf4llm）+ python-docx / python-pptx | 已定 |
| OCR / 图片转文本 | 视觉 LLM（OpenAI 兼容协议） | 已定，具体模型待定 |
| 向量库 | pgvector | 已定 |
| 中文嵌入模型 | 通用嵌入接口 + 配置指定模型 | 已定，具体模型待定 |
| 任务编排 / 队列 | FastAPI BackgroundTasks | 首版临时，后续可换 |
| Agent 编排框架 | LangGraph | 已定 |
| LLM | OpenAI 兼容协议 + 配置指定模型 | 已定，供应商/模型待定 |
| 内容安全 | 仅保留抽象接口 | 首版不实现 |
| Markdown → PDF | Pandoc + XeLaTeX | 已定 |

## 1. 文档解析

| 方案 | 支持格式 | 许可证 | OCR/扫描件 | 表格与公式质量 | 安装体积 | 维护活跃度 |
| --- | --- | --- | --- | --- | --- | --- |
| PyMuPDF / pymupdf4llm | pdf（docx/pptx 需转换或另配） | **AGPL-3.0**，闭源商用需另购商业许可【待核实：具体条款以官方 pricing 页为准】 | 不含 OCR，需外部引擎配合 | pdf 文本层提取精确，表格布局还原较好 | 轻量 | 活跃 |
| python-docx / python-pptx | 仅各自单一格式 | MIT/BSD 类 | 无 | 只做结构化字段提取，不含版面理解 | 轻量 | 稳定但更新慢 |

**选型：两者结合，按格式分流。**

- PDF → PyMuPDF（`pymupdf4llm` 直接输出 Markdown，便于后续切块）
- DOCX → python-docx，PPTX → python-pptx
- MD / TXT 无需解析，直接读取

**已知限制与待办：**

- 旧格式 `.doc` / `.ppt` 上述两个库均不支持，而 [prd.md](./prd.md#2-输入矩阵) 将其列为支持格式。落地需经 LibreOffice 转换后再解析，或首版对其返回 `PARSE_FAILED`【待定】
- PDF 无文本层（扫描件）时 PyMuPDF 提取不到文本，转交第 2 节视觉 LLM 做 OCR
- PyMuPDF 的 AGPL-3.0 许可对闭源商用有约束，需在商用前复核【待核实】

## 2. OCR / 图片转文本

| 方案 | 中文印刷体准确率 | 手写体准确率 | 安装体积 | 速度 | 成本 |
| --- | --- | --- | --- | --- | --- |
| 视觉 LLM 直读（如通用多模态模型 API） | 高，且对版面理解优于传统 OCR | **明显更强**，对手写笔记的语义补全能力是传统 OCR 不具备的优势 | 无需本地部署 | 依赖网络延迟 | 按调用计费，用量越大成本越高 |

**选型：视觉 LLM 直读，不部署本地 OCR 引擎。**

**接入方式：** 走 OpenAI 兼容的多模态协议，与第 7 节 LLM 共用同一套客户端；具体模型、apikey、base_url 等在运行时从配置载入。

**已知限制与待办：**

- 具体视觉模型【待定】，取决于所选 LLM 供应商是否提供视觉能力
- 按调用计费，图片量大会推高成本，接入时对图片做压缩/降采样后再送检

## 3. 向量库

| 方案 | 部署重量 | metadata 过滤能力 | 混合检索/BM25 | 多租户 | 自带 rerank |
| --- | --- | --- | --- | --- | --- |
| pgvector | 轻——复用已有 PostgreSQL，无需额外组件 | 依赖 SQL WHERE，组合过滤（`school_id + course_id + source_type + is_shared`）表达自然，性能依赖索引设计 | 需搭配 `tsvector`/`pg_bm25` 类扩展自行实现 | 靠 schema/行级安全自行实现 | 无，需外部方案 |

**选型：pgvector（PostgreSQL 扩展）。**

**理由：** 复用已有 PostgreSQL，零额外组件、运维最轻；取用策略的组合过滤（[data-model.md](./data-model.md#4-取用策略--检索过滤) 中的 `school_id + course_id + source_type + is_shared`）用 SQL WHERE 天然表达。

**已知限制与待办：**

- 混合检索/BM25 需自行扩展，首版可只做向量检索（叠加类语义检索需求下已够用）
- 无自带 rerank，首版省略，后续需要时另配外部方案
- Collection 划分、chunk metadata 与 payload 索引见 [data-model.md](./data-model.md#3-向量库)（`school_id`/`course_id`/`source_type`/`is_shared` 必须建索引）

## 4. 中文嵌入模型

**选型：通用嵌入接口 + 配置指定模型。** 不自研嵌入，也不在代码里写死某一供应商。

**接入方式：** 走 OpenAI 兼容的 embeddings 协议（`POST /embeddings`），与第 7 节 LLM 共用客户端与配置；具体模型、维度在配置文件填写，运行时载入。

**已知限制与待办：**

- 具体模型【待定】，落地前按中文语义检索基准评测选取，不做硬编码
- 向量维度需与向量库列/索引定义一致；**更换模型导致维度变化时，已有向量不可复用，需重灌**——首版把维度写入配置并做启动校验
- 嵌入与生成解耦，可来自不同供应商

## 5. 任务编排 / 队列

| 方案 | 原生异步 | 进度上报 | 可恢复性 | 运维成本 |
| --- | --- | --- | --- | --- |
| FastAPI BackgroundTasks | 支持 | 无（进度靠编排层事件总线自行实现） | **无任务持久化，进程重启即丢失**，与 NFR-3 存在张力 | 最低，零额外组件 |

**选型：FastAPI BackgroundTasks（首版临时方案）。**

**理由：** 零额外组件、运维成本最低，适合首版单进程部署；后续量上来或有硬性可用性要求时再更换。

**已知限制（如实记录，不掩盖）：**

- **无任务持久化**：进程崩溃/重启会丢失正在运行的任务。NFR-3「不重复付费」靠数据库检查点落地（见 [architecture.md](./architecture.md#6-幂等与断点续跑)）而非队列本身——重启后未完成的 job 需外部触发重新入队才能从检查点续跑
- 无内建进度上报：SSE 进度由编排层事件总线 + `job_stage` 表自行实现（[architecture.md](./architecture.md#7-进度事件总线)），不受 BackgroundTasks 限制

**迁移路径：** 需持久化队列或更高并发时，迁移到 Celery / RQ / ARQ 等持久化任务队列（具体【待定】）。编排层按抽象接口隔离，迁移不影响上层。

## 6. Agent 编排框架

| 方案 | 与本项目结构的契合度 | 要点 |
| --- | --- | --- |
| LangGraph | 高 | supervisor 模式对应「规划 agent 决策、子 agent 执行」的主从结构；`Send` API 天然支持 fan-out（对应「选择题 1 个、填空题 1 个、简答题每题 1 个」的 map 阶段，见 [architecture.md](./architecture.md#5-出题-fan-out-模型)）；内置 checkpointer 对应断点续跑需求 |

**选型：LangGraph。**

**已知限制与待办：**

- LangGraph 的 checkpointer 与 [architecture.md](./architecture.md#6-幂等与断点续跑) 的数据库检查点是两套机制：前者恢复图内执行状态，后者恢复跨进程的业务状态（题目、规划项等）。实现时需明确二者职责边界，避免重复持久化【待定】
- 版本与许可证细节随时间变化快，落地前对照官方文档复核【待核实】

## 7. LLM

**选型：OpenAI 兼容协议 + 配置指定模型。** 代码只依赖 OpenAI 兼容接口，不绑定具体供应商。

**接入方式：** 统一走 OpenAI Chat Completions 协议；apikey、base_url、模型名等在运行时从配置载入。各 agent 角色可配不同模型档位（[agent-design.md](./agent-design.md#角色划分)：规划用强模型、出题用性价比模型、审查用中等模型、压缩用轻量模型）。

**已知限制与待办：**

- 需选一个支持 OpenAI 兼容协议的供应商或网关（自建/第三方），具体【待定】
- 速率限制（NFR-2）决定出题并发上限 `MAX_CONCURRENT_WRITERS`，与所选供应商 quota 绑定【待定】

## 8. 内容安全

**选型：首版仅保留抽象接口，不接入具体检测服务。**

- 按 [architecture.md](./architecture.md#2-模块划分) 保留 `app/moderation/moderator.py` 抽象接口，首版实现为空实现（默认通过），后续无侵入接入检测服务
- [data-model.md](./data-model.md#23-内容安全记录) 的 `moderation_record` 表结构保留，`passed`/`labels`/`provider` 字段为后续接入预留
- NFR-6 的境内合规事项（安全评估、算法备案、内容标识、未成年人保护）不通过程序实现，属上线前由运营/合规侧完成的组织性工作，须在正式服务前落实

## 9. Markdown → PDF

| 方案 | 中文字体 | LaTeX 公式保真度 | 容器体积 | 速度 |
| --- | --- | --- | --- | --- |
| Pandoc + XeLaTeX | 需显式配置中文字体（如思源黑体），否则中文缺字 | 高，LaTeX 引擎原生支持公式渲染 | 较重，需完整 TeX Live 发行版 | 较慢，尤其大文档 |

**选型：Pandoc + XeLaTeX。**

**理由：** LaTeX 引擎原生支持公式渲染，满足 FR-19 的「中文正常显示、LaTeX 正确渲染」要求。

**已知限制与待办：**

- 中文字体【待定】，需选一个开源中文字体（如思源黑体/宋体）并确认许可证
- 容器体积较重，镜像构建时裁剪 TeX Live（仅装用到的宏包与中文字体）
- 大文档渲染较慢，渲染阶段需设超时与失败兜底（对应 NFR-1 的 15 分钟上限与 E-5 `RENDER_FAILED`）
