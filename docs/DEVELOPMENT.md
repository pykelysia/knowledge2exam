# 开发指南

面向开发者与部署运维：部署约束、环境变量、代码质量、数据库迁移与前端开发。
功能介绍见[根目录 README](../README.md)；架构设计见 [ARCHITECTURE.md](ARCHITECTURE.md)。

---

## 部署约束

- SSE 实时推送与任务取消依赖进程内状态（事件订阅队列、asyncio 任务注册表），当前仅支持
  **单进程单 worker** 部署（默认 `uvicorn app.main:app`）。多 worker 部署会导致事件推送丢失，
  需先引入跨进程事件通道（如 Redis pub/sub）。
- 生产环境启动前必须配置 `JWT_SECRET`（非默认值），服务会在启动时校验。
- 服务重启时会把遗留的非终态任务定格为 `failed`（`SERVER_RESTARTED`）。

---

## 代码质量

```bash
# 格式检查
uv run ruff check app/

# 前端 lint
cd frontend && npm run lint

# 测试
uv run pytest
```

---

## 数据库迁移

```bash
# 生成迁移
uv run alembic revision --autogenerate -m "描述"

# 执行迁移
uv run alembic upgrade head

# 回滚
uv run alembic downgrade -1
```

---

## 清空数据表

```bash
# 清空任务相关表（job / job_stage / job_upload / plan_item）
./scripts/clear_db.sh tasks

# 清空全部业务表（保留 alembic_version）
./scripts/clear_db.sh all
```

---

## 前端开发

前端项目位于 `frontend/` 目录：

- **技术栈**：React 19 + TypeScript + Vite 8 + Tailwind CSS 4 + react-router 7 + axios，自研轻量 UI 组件，oxlint 检查
- **页面**：Dashboard（创建任务）/ Jobs（任务历史）/ JobDetail（SSE 实时进度 + 整卷 Markdown 预览 + 划选修订 + 修订历史）/ Login / Register
- **SSE 消费**：原生 `EventSource` 订阅任务事件流，断线由浏览器自动重连（携带 Last-Event-ID），异常时降级为轮询任务详情；修订进行中轮询会话状态兜底

```bash
cd frontend
npm install
npm run dev        # http://localhost:3000
```

`frontend/.env` 仅一个可选项 `VITE_API_BASE_URL`：留空时走 Vite 代理（`/api` → `http://localhost:8000`）；跨域直连部署时填后端完整地址。

---

## 环境变量说明

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
| `AGENT_REASONING_EFFORT` | 推理模型思考深度（`none`/`minimal`/`low`/`medium`/`high`/`xhigh`/`max`），留空不发送；仅推理模型支持 | — |
| `AGENT_RECURSION_LIMIT` | ReAct 循环最大步数 | 100 |
| `AGENT_MAX_RETRIES` | 单题校验失败重试上限（超限放弃该题） | 3 |
| `AGENT_MAX_RENDER_RETRIES` | 整卷渲染失败重试上限 | 3 |
| `SKILLS_DIR` | agent 技能目录 | `./skills` |
| `MAX_COURSE_PREFERENCES` | 单课程偏好条数上限（超限不再沉淀并告警） | 50 |
| `EMBEDDING_API_KEY` | 嵌入模型 API 密钥 | — |
| `EMBEDDING_BASE_URL` | 嵌入接口地址 | `https://api.openai.com/v1` |
| `EMBEDDING_MODEL` | 嵌入模型 | `text-embedding-3-small` |
| `EMBEDDING_DIMENSIONS` | 嵌入维度 | 1536 |
| `CHUNK_SIZE` | 文本切块大小（字符） | 700 |
| `CHUNK_OVERLAP` | 相邻切块重叠（字符） | 100 |
| `MAX_KEYPOINT_LIST_CHARS` | 重点清单长度上限（字符） | 3000 |
| `MAX_EXTRA_REQUIREMENT_CHARS` | 额外要求长度上限（字符） | 2000 |
| `OCR_MODEL` | 视觉 LLM OCR 模型（须支持图片输入；凭证留空复用 LLM 配置） | `gpt-4o` |
| `OCR_API_KEY` | OCR 模型 API 密钥 | — |
| `OCR_BASE_URL` | OCR 接口地址 | — |
| `OCR_DPI` | 损坏页 OCR 渲染 DPI | 200 |
| `OCR_MAX_PAGES` | 单任务最多 OCR 页数 | 60 |
| `PDF_OCR_MODE` | OCR 触发模式（`auto` 坏页才 OCR / `off` / `always`） | `auto` |
| `DEBUG_MODE` | 调试日志开关（仅开发环境） | `false` |
