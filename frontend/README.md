# 前端 — knowledge2exam

试卷生成工具的前端应用。用户上传课程资料或输入文本，系统据此生成一份模拟试卷并输出 PDF。

本目录是 `feat/frontend` 工作区内的前端工程，位于项目根目录下的 `frontend` 文件夹中。后端接口契约见仓库根目录 `docs/`（`api.md` / `openapi.yaml`）。

## 技术栈

| 项 | 选型 |
| --- | --- |
| 框架 | React 19 |
| 构建 | Vite 8 |
| 语言 | TypeScript（strict） |
| 路由 | react-router-dom v7 |
| HTTP | axios |
| 样式 | Tailwind CSS v4 |
| 图标 | lucide-react |

## 目录结构

```
frontend/
├── src/
│   ├── api/            # API 层：client、类型、各模块
│   │   ├── client.ts   #   axios 实例 + 401 自动续期
│   │   ├── types.ts    #   与 openapi.yaml 对应的类型
│   │   ├── auth.ts     #   /auth/*
│   │   ├── uploads.ts  #   /uploads
│   │   ├── jobs.ts     #   /jobs/*
│   │   └── schools.ts  #   /schools
│   ├── components/     # 布局、受保护路由、UI 基础组件
│   ├── hooks/          # useAuth、useJobStream（SSE）
│   ├── lib/            # token 存储、工具、常量
│   ├── pages/          # Login / Register / Dashboard / JobDetail
│   ├── App.tsx         # 路由
│   ├── main.tsx
│   └── index.css       # Tailwind 入口
├── .env.example
├── vite.config.ts
└── tsconfig*.json
```

## 环境变量

复制 `.env.example` 为 `.env.local`（或 `.env`）按需配置：

| 变量 | 说明 | 默认 |
| --- | --- | --- |
| `VITE_API_BASE_URL` | 后端 API 基础地址 | `http://localhost:8000` |

> 开发环境默认走 Vite 代理（`/api` → 后端），前端代码里的请求统一使用 `/api/v1` 前缀，无需配置该变量即可联调。

## 本地开发

```bash
npm install
npm run dev      # http://localhost:3000
```

## 构建与检查

```bash
npm run build    # tsc -b && vite build
npm run lint     # oxlint
npm run preview  # 预览构建产物
```

## 鉴权说明

采用后端约定的**双 JWT** 方案：

- **access token**（15 分钟）：仅存前端内存，不落地（满足 NFR-8）。
- **refresh token**（30 天）：存 `localStorage`，用于换取新 access token。

axios 响应拦截器在收到 401（`TOKEN_EXPIRED` / `TOKEN_MISSING`）时自动用 refresh token 透明续期并重放原请求；并发 401 只触发一次刷新。refresh 失败或检测到重放（`REFRESH_REUSE_DETECTED`）时清除本地令牌并跳转登录。

## 接口契约

类型定义（`src/api/types.ts`）严格对齐 `docs/openapi.yaml`。若后端契约更新，需同步此处类型与 `src/lib/constants.ts` 中的文案。
