# knowledge2exam

把你的课程资料变成一份完整的模拟试卷。

上传书籍、PPT、PDF、笔记、往期试卷等学习资料，再用一句自然语言说明出卷要求（题型、重点、考试时长……），系统会自动解析资料、建立课程知识库，由 AI 出卷 agent 规划并逐题撰写整卷，最终交付 Markdown 与 PDF 双格式的模拟试卷——不满意还可以划选任意范围继续修改。

> 出卷由单个 ReAct agent 闭环完成：规划、逐题产出、PDF 渲染与结果检验都在同一个循环内自动修正。后端提供 REST API 与 SSE 实时进度流，配套 React 前端负责用户交互与文件上传。

---

## 核心功能

### 上传资料，建立课程知识库

- 支持 PDF / DOCX / PPTX / Markdown / TXT 文档，JPG / PNG / WebP / BMP 图片，以及直接粘贴纯文本（不支持 doc/ppt 旧格式）
- 扫描件与图片自动 OCR 识别为文本
- 同一份内容重复上传自动去重，不重复占用空间
- 往期试卷整篇保留，出卷时作为参考样例
- 资料可挂到学校 + 课程下；同校同课程的资料与解析结果自动共享复用

### 用自然语言描述需求，自动出整卷

- 一句话说明要求即可：题型、重点清单、额外要求
- 可指定考试时长（5–300 分钟）与是否生成答案解析
- agent 自主制定逐题计划，从知识库检索相关内容，一次性写出整卷

### 实时进度

- 从预处理、出卷到渲染，每个阶段的变化实时推送到页面
- 断线重连自动补发错过的事件，进度不丢失

### 试卷交付

- Markdown + PDF 双格式，题目与答案解析分篇
- 个别题目确实无法产出时会在卷面标记并跳过，不影响其余题目
- 即使 PDF 排版失败，也保证交付 Markdown 版本

### 划选修订

- 任务完成后，在整卷预览中划选任意范围，在所选位置下方写一句反馈提交
- 同一个出卷 agent 携带全部历史记录直接续跑改卷——是"改"这份卷子，不是重新出一套
- 每轮修订的前后版本自动留档，可随时回溯

### 偏好沉淀

- 修订反馈可勾选沉淀为课程级出题偏好（同校同课程生效）
- 之后出同一门课的试卷时自动应用这些偏好，越用越贴合课程风格

---

## 快速开始

### 环境要求

- Python ≥ 3.12 与 [uv](https://docs.astral.sh/uv/) 包管理器
- PostgreSQL 16（需 pgvector 扩展；推荐用仓库内置的 Docker Compose 启动）
- Node.js + npm（前端，可选）

### 启动后端

```bash
git clone https://github.com/pykelysia/knowledge2exam.git
cd knowledge2exam

# 1. 配置环境变量（各项含义见文件内注释；关键项：DATABASE_URL / LLM_API_KEY，生产环境还需 JWT_SECRET）
cp .env.example .env

# 2. 启动数据库（内置 pgvector 的 PostgreSQL，默认映射到本机 5433 端口）
docker compose up -d db

# 3. 安装依赖并执行数据库迁移
uv sync
uv run alembic upgrade head

# 4. 启动开发服务器
uv run uvicorn app.main:app --reload --port 8000
```

服务运行在 `http://localhost:8000`，API 文档见 `http://localhost:8000/docs`。

### 启动前端（可选）

```bash
cd frontend
cp .env.example .env
npm install
npm run dev        # http://localhost:3000
```

> **部署提示**：生产环境必须将 `JWT_SECRET` 配置为非默认值；当前仅支持单进程单 worker 部署
> （SSE 推送依赖进程内状态）。完整说明见[开发指南](docs/DEVELOPMENT.md)。

---

## 了解更多

- [系统架构与技术设计](docs/ARCHITECTURE.md) — 架构图、分层结构、项目结构、任务状态机、API 速览、技术栈
- [开发指南](docs/DEVELOPMENT.md) — 环境变量全表、代码质量、数据库迁移、前端开发、部署约束
- [openapi.yaml](docs/openapi.yaml) — API 契约（运行后也可访问 `/docs` 查看交互式文档）
