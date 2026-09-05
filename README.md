# knowledge2exam

试卷生成工具后端 —— 将学生的课程资料（书籍、PPT、PDF、笔记、往期试卷等）自动转化为结构化的模拟试卷。

> 后端提供 REST API + SSE 进度流；前端（Vite + TypeScript）负责用户交互与文件上传。

---

## 功能概览

| 阶段 | 说明 |
|------|------|
| **资料上传** | 支持 PDF、DOCX、PPTX、Markdown、图片（JPG/PNG）及纯文本输入 |
| **预处理** | 文档解析 → OCR → 文本切块 → 向量嵌入（pgvector）；长文本触发 LLM 压缩 agent |
| **安全检测** | 共享入库前自动进行内容安全审核，拦截违规内容 |
| **智能规划** | Planner Agent 参考往期试卷与重点清单，规划题型分布、知识点覆盖与难度分配 |
| **并行出题** | 选择题 / 填空题各由 1 个 agent 批量完成；简答题每题独占 1 个 agent，支持并发控制 |
| **质量审查** | 可选 Review Agent，检查违规内容、与规划的偏差、LaTeX 渲染问题 |
| **产物渲染** | 合成 Markdown 并渲染为 PDF，支持题目与答案解析分篇输出 |

---

## 系统架构

```
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   接入层     │───▶│   编排层     │───▶│   Agent 层   │
│  (FastAPI)   │    │ 状态机 / 调度 │    │ Planner /    │
│ auth/upload  │    │ 断点续跑 /   │    │ Writer /     │
│ jobs / SSE   │    │ fan-out      │    │ Reviewer     │
└──────────────┘    └──────────────┘    └──────────────┘
       │                    │                   │
       ▼                    ▼                   ▼
┌──────────────┐    ┌──────────────┐    ┌──────────────┐
│   能力层     │    │   存储层     │    │   LLM 层     │
│ 解析 / OCR   │    │ PostgreSQL   │    │ OpenAI 兼容  │
│ 向量检索     │    │ pgvector     │    │ LangGraph    │
│ 安全检测     │    │ 对象存储     │    │ tool calling │
│ 渲染 (PDF)   │    │ 试卷快读缓存 │    │              │
└──────────────┘    └──────────────┘    └──────────────┘
```

---

## 技术栈

| 类别 | 技术 |
|------|------|
| 框架 | FastAPI ≥ 0.115 |
| 运行时 | Uvicorn (standard) |
| 数据库 | PostgreSQL 16 + pgvector |
| ORM / 迁移 | SQLAlchemy 2.x (async) + Alembic |
| 验证 | Pydantic ≥ 2.9 + pydantic-settings |
| Agent 编排 | LangGraph ≥ 0.2.60 |
| LLM | OpenAI API（兼容协议，可替换后端） |
| 文档解析 | PyMuPDF、python-docx、python-pptx、Pillow |
| 认证 | httpOnly Cookie + JWT + bcrypt |
| 实时推送 | SSE (sse-starlette) |
| HTTP 客户端 | httpx |
| 前端 | Vite + TypeScript（见 `frontend/`） |

---

## 快速开始

### 环境要求

- Python ≥ 3.12
- PostgreSQL 16（需启用 pgvector 扩展）
- [uv](https://docs.astral.sh/uv/) 包管理器

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
# 数据库（默认使用 docker-compose 启动的 5433 端口）
DATABASE_URL=postgresql+asyncpg://user:password@localhost:5433/knowledge2exam

# JWT
JWT_SECRET=your-secret-key
ACCESS_TOKEN_EXPIRE_MINUTES=15
REFRESH_TOKEN_EXPIRE_DAYS=30

# LLM（OpenAI 兼容接口）
LLM_API_KEY=sk-your-key
LLM_BASE_URL=https://api.openai.com/v1
PLANNER_MODEL=gpt-4o
WRITER_MODEL=gpt-4o-mini
REVIEWER_MODEL=gpt-4o
COMPRESSOR_MODEL=gpt-4o-mini
MAX_CONCURRENT_WRITERS=4
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

---

## 项目结构

```
app/
├── main.py                     # FastAPI 入口
├── config.py                   # 配置加载（pydantic-settings）
│
├── api/                        # ── 接入层 ──
│   ├── auth.py                 # Cookie 登录 / 注册 / 刷新 / 登出
│   ├── uploads.py              # 文件上传
│   ├── jobs.py                 # 任务创建 / 查询 / 取消
│   ├── events.py               # SSE 进度事件
│   ├── artifacts.py            # Markdown / PDF 下载
│   └── catalog.py              # 学校 / 课程列表
│
├── orchestration/              # ── 编排层 ──
│   ├── state_machine.py        # 任务状态机
│   ├── stages.py               # 阶段调度
│   ├── checkpoint.py           # 断点续跑
│   ├── fanout.py               # 出题并发调度
│   ├── graph.py                # LangGraph 编排图
│   ├── facade.py               # 编排门面
│   └── events.py               # 进度事件总线
│
├── agents/                     # ── Agent 层 ──
│   ├── planner.py              # 规划 agent（题型分布 / 知识点覆盖）
│   ├── question_writers.py     # 出题 agent（选择 / 填空 / 简答）
│   ├── reviewer.py             # 审查 agent
│   ├── compressor.py           # 压缩 agent（长文本摘要）
│   ├── tools.py                # 出题 tool schema（选择 / 填空 / 简答）
│   ├── llm.py                  # LLM 调用封装
│   ├── base.py                 # Agent 基类
│   └── prompts/                # 提示词模板
│
├── ingestion/                  # ── 能力层：入 ──
│   ├── parsers/                # 格式解析器（pdf / docx / pptx / image / text）
│   ├── ocr.py                  # 图片 OCR
│   ├── chunking.py             # 文本切块
│   ├── embedding.py            # 向量嵌入
│   └── postprocess.py          # 图片后处理（去重 / 合并 / 间隙标记）
│
├── retrieval/                  # ── 能力层：检索 ──
│   ├── vector_store.py         # 向量库抽象接口
│   ├── filters.py              # 排他 / 叠加检索策略
│   └── past_papers.py          # 往期试卷快读缓存
│
├── moderation/                 # ── 能力层：安全 ──
│   └── moderator.py            # 内容安全检测
│
├── rendering/                  # ── 能力层：产出 ──
│   ├── markdown.py             # Markdown 合成
│   ├── renderer.py             # PDF 渲染
│   └── setup.py                # 渲染器初始化
│
├── models/                     # SQLAlchemy ORM 模型
│   ├── user.py
│   ├── upload.py
│   ├── resource.py
│   ├── chunk.py
│   ├── job.py
│   ├── plan.py
│   ├── question.py
│   ├── moderation.py
│   ├── llm_call.py
│   ├── auth.py
│   ├── catalog.py
│   └── base.py
│
├── schemas/                    # Pydantic 请求 / 响应模型
│   ├── auth.py
│   ├── upload.py
│   ├── job.py
│   ├── question.py
│   ├── catalog.py
│   └── common.py
│
├── core/                       # 基础设施
│   ├── db.py                   # 数据库连接 / Session
│   ├── security.py             # 密码哈希 / JWT（Cookie 认证）
│   ├── storage.py              # 对象存储抽象
│   ├── exceptions.py           # 业务异常 + 错误码
│   ├── deps.py                 # FastAPI 依赖注入
│   ├── events.py               # 事件定义
│   ├── enums.py                # 枚举（任务状态、题型等）
│   └── seed.py                 # 初始数据

alembic/                       # 数据库迁移（Alembic）
tests/                         # 测试（pytest + pytest-asyncio）
docs/                          # 设计文档
frontend/                      # 前端项目（Vite + TypeScript）
```

---

## API 速览

所有接口挂载在 `/api/v1` 前缀下，Swagger UI 位于 `/docs`。

| 模块 | 路径前缀 | 说明 |
|------|----------|------|
| 认证 | `/api/v1/auth` | 注册 / 登录 / 刷新 / 登出（Cookie 认证） |
| 上传 | `/api/v1/uploads` | 上传课程资料文件 |
| 任务 | `/api/v1/jobs` | 创建试卷生成任务 / 查询状态 / 取消 |
| 产物 | `/api/v1/artifacts` | 下载 Markdown / PDF |
| 学校/课程 | `/api/v1/catalog` | 可选：同校同课程的共享知识库 |

---

## 任务状态机

试卷生成任务经历以下状态：

```
pending → preprocessing → planning → generating → reviewing → rendering → completed
                                                    ↘ partially_completed
```

- **generating** 阶段支持并发出题，单个 agent 失败不影响其余题目
- 单题重试超过 3 次后自动换题，不会无限循环
- 支持断点续跑：崩溃重启后只重新生成未完成的题目

---

## 前端

前端项目位于 `frontend/` 目录，使用 Vite + TypeScript 构建。开发模式下后端与前端可独立运行。

```bash
cd frontend
npm install
npm run dev
```

---

## 开发

### 代码质量

```bash
# 格式检查
uv run ruff check app/

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

### 环境变量说明

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DATABASE_URL` | 数据库连接字符串 | — |
| `JWT_SECRET` | JWT 签名密钥（Cookie 中 access_token 使用） | — |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access Token 有效期 | 15 |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Refresh Token 有效期 | 30 |
| `COOKIE_SECURE` | Cookie 仅 HTTPS 传输（生产环境必须设为 true） | `false` |
| `COOKIE_SAMESITE` | Cookie SameSite 策略（`lax` / `strict` / `none`） | `lax` |
| `COOKIE_DOMAIN` | Cookie 域名（跨子域时配置，如 `.example.com`） | `null` |
| `STORAGE_DIR` | 产物存储目录 | `./storage` |
| `CORS_ORIGINS` | 允许的跨域来源 | `["http://localhost:5173"]` |
| `LLM_API_KEY` | LLM API 密钥 | — |
| `LLM_BASE_URL` | LLM 兼容接口地址 | `https://api.openai.com/v1` |
| `PLANNER_MODEL` | 规划 agent 模型 | `gpt-4o` |
| `WRITER_MODEL` | 出题 agent 模型 | `gpt-4o-mini` |
| `REVIEWER_MODEL` | 审查 agent 模型 | `gpt-4o` |
| `COMPRESSOR_MODEL` | 压缩 agent 模型 | `gpt-4o-mini` |
| `MAX_CONCURRENT_WRITERS` | 出题 agent 并发上限 | 4 |

---

## 设计文档

详细设计文档位于 `docs/` 目录：

| 文档 | 说明 |
|------|------|
| [idea.md](./docs/idea.md) | 产品需求与业务流程 |
| [architecture.md](./docs/architecture.md) | 系统架构、分层、状态机与全链路时序 |
| [data-model.md](./docs/data-model.md) | 数据库表结构与关系 |
| [api.md](./docs/api.md) | REST API 与 SSE 事件规范 |
| [agent-design.md](./docs/agent-design.md) | Agent 设计与提示词策略 |
| [tech-selection.md](./docs/tech-selection.md) | 技术选型与候选对比 |
| [prd.md](./docs/prd.md) | 产品需求文档 |
