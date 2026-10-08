# 系统架构与技术设计

本文承接 README 的技术细节：系统架构、项目结构、任务状态机、API 速览与技术栈。
功能介绍见[根目录 README](../README.md)；开发与部署见 [DEVELOPMENT.md](DEVELOPMENT.md)。

---

## 系统架构

```
用户上传文件（个人库，带可见性与学校/课程标签）
    │
    ▼
上传即解析（异步：解析 → OCR → 切块 → 向量嵌入）
    │        └── 解析产物 ──▶ 知识库（PostgreSQL + pgvector，一次解析全局复用）
    │
公开池（public / school 可见）── 语义+关键词检索 ──▶ 保存到个人库（引用级 file_import）
    │
    ▼
创建任务（从个人库勾选文件；解析门禁 + 引用授权校验；标签自动推导）
    │
    ▼
文件预处理（读现成解析产物 → 写 agent 工作区 materials/，不再二次解析）
    │                                 │
    │ 文本输入（意图提取 → ExamIntent） │ search_knowledge 白名单检索（仅选中文件）
    ▼                                 ▼
Agent 工作（单个 ReAct agent + 工具循环，整卷直写 output/paper.md）
    │   todo_write / check_todo    出题计划与进度
    │   search_knowledge           知识库向量检索（任务选中文件白名单）
    │   load_skill                 加载出卷 / LaTeX 排版技能
    │   read_file / edit_file      工作区文件读写（试卷书写工具）
    ▼ render_paper
PDF 渲染（Pandoc + XeLaTeX，缺依赖自动降级 md_only）
    ▼
结果检验 ──成功──▶ 结果输出（output/paper.md / output/paper.pdf）
    │失败
    ▼
回到 Agent 工作（内容错误回传修正；重试超限降级）

任务完成后 ── 划选反馈修订（同一 agent 全工具续跑）
    │   前端整卷 Markdown 预览，划选任意范围 → 所选位置下方反馈框
    │   POST /jobs/{id}/revisions {selection, feedback}
    ▼
修订轮（携带划选锚点 + 反馈 + 全部历史会话）
    │   直接修改 output/paper.md → render_paper 重渲染
    ▼
paper_revision 表留存会话记录与前后快照（可回溯）
```

### 分层结构

- **接入层**（`app/api`）：FastAPI 路由、Cookie 认证、SSE 事件流
- **编排层**（`app/orchestration`）：状态机校验、管线调度、事件总线
- **Agent 层**（`app/agents`）：ReAct agent、7 个工具、技能系统、工作区
- **能力层**（`app/ingestion` / `app/retrieval` / `app/rendering`）：解析、OCR、嵌入、向量检索、渲染
- **存储层**（`app/models` + `app/core`）：PostgreSQL + pgvector、本地对象存储、SSE 事件持久化

---

## 项目结构

```
app/
├── main.py                     # FastAPI 入口（挂载 /api/v1 路由）
├── config.py                   # 配置加载（pydantic-settings）
│
├── api/                        # ── 接入层 ──
│   ├── auth.py                 # 注册 / 登录 / 刷新 / 登出 / PATCH me（Cookie 双 JWT）
│   ├── uploads.py              # 上传（可见性/标签/异步解析）、软删除、个人库、预览
│   ├── pool.py                 # 公开池浏览/语义检索/引用级保存
│   ├── jobs.py                 # 任务创建 / 详情 / SSE 进度流 / 取消 / 删除
│   ├── revisions.py            # 划选反馈修订提交与会话历史
│   ├── artifacts.py            # paper.md（含进行中预览）/ paper.pdf 下载
│   └── catalog.py              # 学校 / 全局课程列表
│
├── orchestration/              # ── 编排层 ──
│   ├── state_machine.py        # 任务状态与流转合法性校验
│   ├── stages.py               # 管线主体：材料整理 → agent → 入库 → 终态
│   ├── revision.py             # 修订编排：轮次创建、后台续跑、快照比对
│   ├── integration.py          # ExamResult（蓝图）批量入库
│   └── events.py               # 兼容 re-export（EventBus 本体在 core/events.py）
│
├── agents/                     # ── Agent 层（单个 ReAct agent）──
│   ├── agent.py                # 入口 run_exam_agent：生成 / 修订双分支
│   ├── intent.py               # 用户文本 → ExamIntent 意图提取
│   ├── tools.py                # 7 个工具（todo / 检索 / 技能 / 文件 / 渲染）
│   ├── skills.py               # SKILL.md 技能加载器
│   ├── workspace.py            # 对象存储上的受控工作区文件面
│   ├── schemas.py              # ExamIntent / RevisionDirective / ExamResult 等契约
│   ├── llm.py                  # ChatOpenAI 工厂
│   └── prompts/                # 提示词模板（system / revise_system / intent）
│
├── ingestion/                  # ── 能力层：入 ──
│   ├── pipeline.py             # 上传即解析管线（异步、幂等、启动恢复）
│   ├── parsers/                # 格式解析器（pdf / docx / pptx / image / text）
│   ├── ocr.py                  # 视觉 LLM OCR
│   ├── chunking.py             # 文本切块
│   ├── embedding.py            # 向量嵌入
│   └── postprocess.py          # 图片后处理
│
├── retrieval/                  # ── 能力层：检索 ──
│   ├── vector_store.py         # pgvector 向量库
│   ├── filters.py              # 检索过滤策略
│   └── past_papers.py          # 往期试卷处理
│
├── rendering/                  # ── 能力层：产出 ──
│   ├── markdown.py             # 渲染前清洗（sanitize_markdown）
│   ├── renderer.py             # Pandoc + XeLaTeX 渲染（缺依赖降级）
│   └── setup.py                # 渲染依赖自动安装
│
├── models/                     # SQLAlchemy ORM（user / auth / catalog / upload /
│                               # library / chunk / job / plan / revision）
├── schemas/                    # Pydantic 请求 / 响应模型（auth / upload / job /
│                               # revision / catalog / common）
├── core/                       # 基础设施（db / security / storage / events /
│                               # exceptions / deps / enums / seed / debug_log /
│                               # task_registry）

skills/                         # agent 技能目录（exam-authoring / latex-rendering）
scripts/                        # 辅助脚本（clear_db.sh 清空数据表）
alembic/                        # 数据库迁移（Alembic）
tests/                          # 测试（agents / orchestration / ingestion /
                                # rendering / retrieval）
frontend/                       # 前端项目（React 19 + TypeScript + Vite）
docs/                           # 文档目录
```

---

## 任务状态机

试卷生成任务共 8 个状态：

```
pending → preprocessing → generating → rendering → completed
                                                     ↘ partially_completed
```

合法流转（`app/orchestration/state_machine.py`）：

- `pending` → `preprocessing` / `cancelled`
- `preprocessing` → `generating` / `failed` / `cancelled`
- `generating` → `rendering` / `failed` / `cancelled`
- `rendering` → `completed` / `partially_completed` / `failed` / `cancelled`

行为要点：

- `generating` 覆盖 agent 工作全程：蓝图规划（todo）、整卷直写 output/paper.md 均在其中完成
- 管线意外异常时任务定格 `failed`（`error_code=PIPELINE_FAILED`），并推送 `error` + `done` 事件
- PDF 渲染失败不导致 `failed`：重试超限后任务以 `partially_completed` 结束，仅交付 paper.md，并携带 `RENDER_FAILED` warning
- 某题实在无法产出时 agent 在蓝图中以「[已放弃] <原因>」标记该题并继续，不影响其余题目
- 解析已前移到上传阶段（`app/ingestion/pipeline.py`）：`preprocessing` 只把现成解析产物整理进 agent 工作区，任务不再重复解析；创建任务时校验全部文件 `parse_status=succeeded`（409 `UPLOAD_NOT_PARSED`）
- 检索为白名单语义：`search_knowledge` 只在任务选中的 upload 集合 × book/lecture/note 范围内检索，白名单之外在 SQL 层面不可达

### 划选反馈修订

任务进入 `completed` / `partially_completed` 后，可在前端整卷预览中划选任意范围，
在所选位置下方的反馈框中撰写要求并提交：

- 后端把「划选锚点（选中文本 + 前后文）+ 反馈 + 全部历史轮次」交回**同一个 agent**
  （全套 7 工具、零裁剪）续跑，直接修改 `output/paper.md` 并重渲染 PDF——不是重新出题；
- 每轮落一行 `paper_revision`（会话记录），留存修订前后的试卷全文快照，可回溯；
- 同一任务同时只允许一轮修订在跑（409 `REVISION_IN_PROGRESS`）；
- 修订不改变任务状态；进度经 `revision_started` / `revision_done` / `revision_failed` 事件推送，
  前端在修订期间以轮询兜底。

---

## API 速览

所有接口挂载在 `/api/v1` 前缀下，Swagger UI 位于 `/docs`（完整契约见 [openapi.yaml](openapi.yaml)）。

| 模块 | 路径 | 说明 |
|------|------|------|
| 认证 | `/api/v1/auth` | 注册（可选学校归属）/ 登录 / 刷新 / 登出 / PATCH me（httpOnly Cookie 双 JWT） |
| 上传 | `/api/v1/uploads` | 文件（multipart，带可见性与学校/课程标签，上传即异步解析）或文本（JSON）上传；PATCH 改可见性/标签；软删除（被运行中任务引用时 409） |
| 个人库 | `/api/v1/files/mine` | 我的上传 + 公开池保存的引用（含「文件已失效」标记）；`/files/{id}/preview` 文件预览 |
| 公开池 | `/api/v1/pool/files` | 浏览 / 关键词∪语义检索（本校 + 全局可见，排除本人）；`/{id}/import` 引用级保存与取消 |
| 任务 | `/api/v1/jobs` | 列表 / 创建（解析门禁 + 引用授权 + 标签推导）/ 详情 / SSE 进度流 / 取消 / 删除 |
| 产物 | `/api/v1/jobs/{id}/paper.md`、`/paper.pdf` | 试卷产物（md 支持任务进行中渐进预览） |
| 修订 | `/api/v1/jobs/{id}/revisions` | 划选反馈修订（POST 提交 / GET 会话历史） |
| 学校/课程 | `/api/v1/schools`、`/api/v1/courses` | 学校列表 / 全局课程列表（互相独立的标签，课程跨校共用） |

创建任务（`POST /api/v1/jobs`）参数：

| 字段 | 类型 | 说明 |
|------|------|------|
| `upload_ids` | UUID 列表 | 本次使用的上传件（本人上传或已保存引用；须全部解析完成） |
| `duration_minutes` | int，5–300，默认 100 | 目标考试时长（分钟） |
| `need_explanation` | bool，默认 false | 是否生成答案解析 |

**SSE 事件类型**（`GET /api/v1/jobs/{id}/events`）：

`stage_changed` / `plan_ready` / `progress` / `warning` / `error` / `revision_started` / `revision_done` / `revision_failed` / `done`

事件持久化在 `job_stage` 表并带单调递增 `seq`；断线重连时服务端按 `Last-Event-ID` 补发历史事件。

---

## 技术栈

| 类别 | 技术 |
|------|------|
| 框架 | FastAPI ≥ 0.115 |
| 运行时 | Uvicorn (standard) |
| 数据库 | PostgreSQL 16 + pgvector |
| ORM / 迁移 | SQLAlchemy 2.x (async) + Alembic |
| 验证 | Pydantic ≥ 2.9 + pydantic-settings |
| Agent | LangChain ≥ 1.4（create_agent + 结构化输出）+ LangGraph ≥ 0.2.60 |
| LLM | OpenAI 兼容协议（ChatOpenAI，可替换后端） |
| 文档解析 | PyMuPDF、python-docx、python-pptx、Pillow、LibreOffice（旧格式转换） |
| OCR | 视觉 LLM（`OCR_MODEL`，默认 gpt-4o） |
| PDF 渲染 | Pandoc + XeLaTeX（缺依赖时降级为仅交付 Markdown） |
| 认证 | httpOnly Cookie + 双 JWT + bcrypt |
| 实时推送 | SSE (sse-starlette) |
| HTTP 客户端 | httpx |
| 前端 | React 19 + TypeScript + Vite 8 + Tailwind CSS 4（见 `frontend/`） |
