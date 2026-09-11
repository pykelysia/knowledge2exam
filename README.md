# knowledge2exam

试卷生成工具 —— 将学生的课程资料（书籍、PPT、PDF、笔记、往期试卷等）自动转化为结构化的模拟试卷。

> 后端提供 REST API + SSE 进度流；前端（React 19 + TypeScript）负责用户交互与文件上传。
> 出卷由**单个 ReAct agent** 完成：规划、逐题产出、PDF 渲染与结果检验均在同一个 agent 循环内闭环。

---

## 功能概览

| 阶段 | 说明 |
|------|------|
| **资料上传** | 支持 PDF / DOCX / PPTX / Markdown / TXT / 图片（JPG、PNG、WebP、BMP）及纯文本输入（不支持 doc/ppt 旧格式） |
| **文件预处理** | 文档解析 → 视觉 LLM OCR → 文本切块 → 向量嵌入（pgvector）；往期试卷整篇注入 agent 工作区 |
| **意图提取** | 用户文本输入结构化为 ExamIntent（题型要求 / 重点清单 / 额外要求） |
| **Agent 出卷** | 单个 ReAct agent 通过 7 个工具完成出卷：todo 计划管理、知识库向量检索、技能加载（出卷 / LaTeX 排版）、工作区文件读写、整卷渲染 |
| **PDF 渲染** | Pandoc + XeLaTeX 渲染试卷（支持题目与答案解析分篇）；环境缺依赖时自动降级为仅交付 Markdown |
| **结果检验** | 逐题 Pydantic + 业务校验，失败重试、超限放弃该题；整卷渲染失败有独立重试上限，超限降级为 `partially_completed` |
| **进度推送** | SSE 实时事件流（9 种事件类型），断线按 Last-Event-ID 补发历史事件 |

---

## 系统架构

```
用户请求（上传资料 + 文本输入）
    │
    ▼
文件预处理（解析 → OCR → 切块 → 向量嵌入）
    │        └── 解析后文件 ──▶ 知识库（PostgreSQL + pgvector）
    │                                 │
    │ 文本输入（意图提取 → ExamIntent） │ search_knowledge 向量检索
    ▼                                 ▼
Agent 工作（单个 ReAct agent + 工具循环）
    │   todo_write / check_todo    出题计划与进度
    │   search_knowledge           知识库向量检索
    │   load_skill                 加载出卷 / LaTeX 排版技能
    │   read_file / edit_file      工作区文件读写
    ▼ render_paper
PDF 渲染（Pandoc + XeLaTeX，缺依赖自动降级 md_only）
    ▼
结果检验 ──成功──▶ 结果输出（paper.md / paper.pdf）
    │失败
    ▼
回到 Agent 工作（内容错误回传修正；重试超限降级）
```

分层结构：

- **接入层**（`app/api`）：FastAPI 路由、Cookie 认证、SSE 事件流
- **编排层**（`app/orchestration`）：状态机校验、管线调度、事件总线
- **Agent 层**（`app/agents`）：ReAct agent、7 个工具、技能系统、工作区
- **能力层**（`app/ingestion` / `app/retrieval` / `app/rendering`）：解析、OCR、嵌入、向量检索、渲染
- **存储层**（`app/models` + `app/core`）：PostgreSQL + pgvector、本地对象存储、SSE 事件持久化

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

---

## 快速开始

### 环境要求

- Python ≥ 3.12
- PostgreSQL 16（需启用 pgvector 扩展）
- [uv](https://docs.astral.sh/uv/) 包管理器
- Node.js + npm（前端，可选）

### 1. 克隆仓库

```bash
git clone https://github.com/<owner>/knowledge2exam.git
cd knowledge2exam
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

按需修改 `.env`，关键配置项：

```env
DEBUG_MODE=false

# 数据库（默认使用 docker-compose 启动的 5433 端口）
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@localhost:5433/knowledge2exam

# JWT
JWT_SECRET=your-jwt-secret-here
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=30

# 存储与跨域
STORAGE_DIR=./storage
CORS_ORIGINS=["http://localhost:5173","http://localhost:3000"]

# LLM（OpenAI 兼容协议）
LLM_API_KEY=sk-your-api-key
LLM_BASE_URL=https://api.openai.com/v1

# 出卷 agent
AGENT_MODEL=gpt-4o

# Embedding（OpenAI 兼容 embeddings 协议）
EMBEDDING_API_KEY=sk-your-embedding-api-key
EMBEDDING_BASE_URL=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
```

### 3. 启动数据库

```bash
docker compose up -d db
# 等待健康检查通过（约 3-5 秒）
docker compose ps
```

### 4. 安装依赖

```bash
uv sync
```

### 5. 运行数据库迁移

```bash
uv run alembic upgrade head
```

### 6. 启动开发服务器

```bash
uv run uvicorn app.main:app --reload --port 8000
```

服务将在 `http://localhost:8000` 启动，API 文档见 `http://localhost:8000/docs`。

### 7. 前端（可选）

```bash
cd frontend
cp .env.example .env
npm install
npm run dev
```

前端开发服务运行在 `http://localhost:3000`，`/api` 请求由 Vite 代理转发到后端 8000 端口。

---

## 项目结构

```
app/
├── main.py                     # FastAPI 入口（挂载 /api/v1 路由）
├── config.py                   # 配置加载（pydantic-settings）
│
├── api/                        # ── 接入层 ──
│   ├── auth.py                 # 注册 / 登录 / 刷新 / 登出（Cookie 双 JWT）
│   ├── uploads.py              # 资料上传（文件 / 文本）与删除
│   ├── jobs.py                 # 任务创建 / 详情 / SSE 进度流 / 题目 / 取消 / 删除
│   ├── artifacts.py            # paper.md / paper.pdf 下载
│   └── catalog.py              # 学校 / 课程列表
│
├── orchestration/              # ── 编排层 ──
│   ├── state_machine.py        # 任务状态与流转合法性校验
│   ├── stages.py               # 管线主体：预处理 → agent → 入库 → 终态
│   ├── integration.py          # ExamResult（蓝图 + 题目）批量入库
│   └── events.py               # 兼容 re-export（EventBus 本体在 core/events.py）
│
├── agents/                     # ── Agent 层（单个 ReAct agent）──
│   ├── agent.py                # 入口 run_exam_agent：意图 → 组装 → 循环 → 兜底渲染
│   ├── intent.py               # 用户文本 → ExamIntent 意图提取
│   ├── tools.py                # 7 个工具（todo / 检索 / 技能 / 文件 / 渲染）
│   ├── skills.py               # SKILL.md 技能加载器
│   ├── workspace.py            # 对象存储上的受控工作区文件面
│   ├── schemas.py              # ExamIntent / ExamQuestion / ExamResult 等契约
│   ├── llm.py                  # ChatOpenAI 工厂
│   └── prompts/                # 提示词模板（system.md / intent.md）
│
├── ingestion/                  # ── 能力层：入 ──
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
│   ├── markdown.py             # 试卷 Markdown 合成
│   ├── renderer.py             # Pandoc + XeLaTeX 渲染（缺依赖降级）
│   └── setup.py                # 渲染依赖自动安装
│
├── moderation/                 # ── 能力层：安全 ──
│   └── moderator.py            # 内容安全检测（当前管线未接入）
│
├── models/                     # SQLAlchemy ORM（user / auth / catalog / upload /
│                               # resource / chunk / job / plan / question /
│                               # moderation / llm_call）
├── schemas/                    # Pydantic 请求 / 响应模型（auth / upload / job /
│                               # question / catalog / common）
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

## API 速览

所有接口挂载在 `/api/v1` 前缀下，Swagger UI 位于 `/docs`。

| 模块 | 路径 | 说明 |
|------|------|------|
| 认证 | `/api/v1/auth` | 注册 / 登录 / 刷新 / 登出（httpOnly Cookie 双 JWT） |
| 上传 | `/api/v1/uploads` | 文件（multipart）或文本（JSON）上传；删除 |
| 任务 | `/api/v1/jobs` | 创建 / 详情 / SSE 进度流 / 题目列表 / 取消 / 删除 |
| 产物 | `/api/v1/jobs/{id}/paper.md`、`/paper.pdf` | 试卷产物下载 |
| 学校/课程 | `/api/v1/schools` | 学校列表 / 课程列表（可选：同校同课程共享知识库） |

创建任务（`POST /api/v1/jobs`）参数：

| 字段 | 类型 | 说明 |
|------|------|------|
| `upload_ids` | UUID 列表 | 关联的上传资料 |
| `school_id` / `course_id` | UUID，可选 | 学校与课程 |
| `duration_minutes` | int，5–300，默认 100 | 目标考试时长（分钟） |
| `need_explanation` | bool，默认 false | 是否生成答案解析 |

**SSE 事件类型**（`GET /api/v1/jobs/{id}/events`）：

`stage_changed` / `plan_ready` / `question_completed` / `question_retried` / `question_replanned` / `question_abandoned` / `warning` / `error` / `done`

事件持久化在 `job_stage` 表并带单调递增 `seq`；断线重连时服务端按 `Last-Event-ID` 补发历史事件。

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

- `generating` 覆盖 agent 工作全程：蓝图规划（todo）、逐题产出与校验、结果检验均在其中完成
- 管线意外异常时任务定格 `failed`（`error_code=PIPELINE_FAILED`），并推送 `error` + `done` 事件
- PDF 渲染失败不导致 `failed`：重试超限后任务以 `partially_completed` 结束，仅交付 paper.md，并携带 `RENDER_FAILED` warning
- 单题校验连续失败超过 `AGENT_MAX_RETRIES` 次即放弃该题（`question_abandoned` 事件），任务继续，不影响其余题目

---

## 前端

前端项目位于 `frontend/` 目录：

- **技术栈**：React 19 + TypeScript + Vite 8 + Tailwind CSS 4 + react-router 7 + axios，自研轻量 UI 组件，oxlint 检查
- **页面**：Dashboard（创建任务）/ Jobs（任务历史）/ JobDetail（SSE 实时进度 + 产物下载）/ Login / Register
- **SSE 消费**：原生 `EventSource` 订阅任务事件流，断线由浏览器自动重连（携带 Last-Event-ID），异常时降级为轮询任务详情

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000
```

`frontend/.env` 仅一个可选项 `VITE_API_BASE_URL`：留空时走 Vite 代理（`/api` → `http://localhost:8000`）；跨域直连部署时填后端完整地址。

---

## 开发

### 代码质量

```bash
# 格式检查
uv run ruff check app/

# 前端 lint
cd frontend && npm run lint

# 测试
uv run pytest
```

### 数据库迁移

```bash
# 生成迁移
uv run alembic revision --autogenerate -m "描述"

# 执行迁移
uv run alembic upgrade head

# 回滚
uv run alembic downgrade -1
```

### 清空数据表

```bash
# 清空任务相关表（job / job_stage / job_upload / plan_item / llm_call 等）
./scripts/clear_db.sh tasks

# 清空全部业务表（保留 alembic_version）
./scripts/clear_db.sh all
```

### 环境变量说明

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DATABASE_URL` | 数据库连接字符串 | `postgresql+asyncpg://localhost/knowledge2exam` |
| `JWT_SECRET` | JWT 签名密钥（生产环境必须覆盖） | 内置开发密钥 |
| `JWT_ALGORITHM` | JWT 签名算法 | `HS256` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access Token 有效期（分钟） | 15 |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Refresh Token 有效期（天） | 30 |
| `COOKIE_SECURE` | Cookie 仅 HTTPS 传输（生产环境必须设为 true） | `false` |
| `COOKIE_SAMESITE` | Cookie SameSite 策略（`lax` / `strict` / `none`） | `lax` |
| `COOKIE_DOMAIN` | Cookie 域名（跨子域部署时配置，如 `.example.com`） | — |
| `COOKIE_ACCESS_TOKEN_MAX_AGE` | Access Cookie 存活秒数 | 900 |
| `COOKIE_REFRESH_TOKEN_MAX_AGE` | Refresh Cookie 存活秒数 | 2592000 |
| `STORAGE_DIR` | 对象存储（产物）目录 | `./storage` |
| `MAX_UPLOAD_SIZE_BYTES` | 单文件上传上限（字节） | 52428800（50 MB） |
| `CORS_ORIGINS` | 允许的跨域来源 | `["http://localhost:5173","http://localhost:3000"]` |
| `LLM_API_KEY` | LLM API 密钥（OpenAI 兼容） | — |
| `LLM_BASE_URL` | LLM 接口地址 | `http://localhost:8000/v1` |
| `AGENT_MODEL` | 出卷 agent 模型 | `gpt-4o` |
| `AGENT_TEMPERATURE` | 采样温度 | 0.7 |
| `AGENT_RECURSION_LIMIT` | ReAct 循环最大步数 | 100 |
| `AGENT_MAX_RETRIES` | 单题校验失败重试上限（超限放弃该题） | 3 |
| `AGENT_MAX_RENDER_RETRIES` | 整卷渲染失败重试上限 | 3 |
| `SKILLS_DIR` | agent 技能目录 | `./skills` |
| `EMBEDDING_API_KEY` | 嵌入模型 API 密钥 | — |
| `EMBEDDING_BASE_URL` | 嵌入接口地址 | `https://api.openai.com/v1` |
| `EMBEDDING_MODEL` | 嵌入模型 | `text-embedding-3-small` |
| `EMBEDDING_DIMENSIONS` | 嵌入维度 | 1536 |
| `CHUNK_SIZE` | 文本切块大小（字符） | 700 |
| `CHUNK_OVERLAP` | 相邻切块重叠（字符） | 100 |
| `MAX_KEYPOINT_LIST_CHARS` | 重点清单长度上限（字符） | 3000 |
| `MAX_EXTRA_REQUIREMENT_CHARS` | 额外要求长度上限（字符） | 2000 |
| `OCR_MODEL` | 视觉 LLM OCR 模型 | `gpt-4o` |
| `DEBUG_MODE` | 调试日志开关（仅开发环境） | `false` |
