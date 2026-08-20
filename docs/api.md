# API 接口文档

REST + SSE。所有端点前缀 `/api/v1`。请求与响应均为 JSON，除上传接口用 `multipart/form-data`。

字段命名与 [data-model.md](./data-model.md) 的列名保持一致；`source_type` 枚举见 [README.md](./README.md#内容类型source_type取值)。

> **机器可读契约**：本接口的完整 OpenAPI 3.0 描述见 [openapi.yaml](./openapi.yaml)。它是前后端共用的唯一契约来源——两个工作区（前端、后端）均可由它生成客户端 SDK / 服务端骨架，或直接用于 Swagger UI 渲染。本文档为人类可读说明，[openapi.yaml](./openapi.yaml) 为权威版本；二者不一致时以 openapi.yaml 为准。

## 1. 端点总览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/auth/register` | 注册账号 |
| POST | `/auth/login` | 登录，返回 access + refresh 双 token |
| POST | `/auth/refresh` | 刷新 access token，并轮换 refresh token |
| POST | `/auth/logout` | 登出，吊销 refresh token |
| POST | `/uploads` | 上传单个文件或提交文本，返回 `upload_id` 与解析预览 |
| DELETE | `/uploads/{upload_id}` | 删除尚未用于任务的上传件 |
| POST | `/jobs` | 创建生成任务，返回 `job_id` |
| GET | `/jobs/{job_id}` | 任务快照（轮询降级用） |
| GET | `/jobs/{job_id}/events` | SSE 进度流 |
| GET | `/jobs/{job_id}/questions` | 已产出的题目列表 |
| GET | `/jobs/{job_id}/paper.md` | 下载 md |
| GET | `/jobs/{job_id}/paper.pdf` | 下载 PDF |
| POST | `/jobs/{job_id}/cancel` | 取消运行中的任务 |
| DELETE | `/jobs/{job_id}` | 删除任务及产物 |
| GET | `/schools` | 学校列表 |
| GET | `/schools/{school_id}/courses` | 某学校的课程列表 |

## 2. 鉴权与用户

双 JWT 鉴权。access token 15 分钟短时效，refresh token 30 天长时效，每次刷新时轮换。重放检测与 `family_id` 机制见 [data-model.md](./data-model.md#8-鉴权与用户系统)。

### POST /auth/register

```json
{
  "email": "user@example.com",
  "username": "zhangsan",
  "password": "password123"
}
```

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `email` | string | 是 | 唯一，格式校验 |
| `username` | string | 是 | 唯一，1~32 字符 |
| `password` | string | 是 | 至少 8 字符 |

响应 `201`：

```json
{
  "user": {"user_id": "a1b2...", "email": "user@example.com", "username": "zhangsan"}
}
```

邮箱或用户名已存在返回 409 `USER_EXISTS`。

### POST /auth/login

`username` 与 `email` 二选一（至少提供一个，优先 `email`）：

```json
{
  "email": "user@example.com",
  "username": null,
  "password": "password123"
}
```

响应 `200`：

```json
{
  "access_token": "eyJhbGci...",
  "refresh_token": "dGhpcyBpcyBh...",
  "expires_in": 900,
  "user": {"user_id": "a1b2...", "email": "user@example.com", "username": "zhangsan"}
}
```

`expires_in` 单位为秒，供前端计算自动刷新时机。凭证错误返回 401 `INVALID_CREDENTIALS`。

### POST /auth/refresh

```json
{
  "refresh_token": "dGhpcyBpcyBh..."
}
```

响应 `200`：

```json
{
  "access_token": "eyJhbGci...",
  "refresh_token": "bmV3IHJlZnJl...",
  "expires_in": 900
}
```

旧 refresh token 立即吊销，新 token 同 `family_id`。refresh token 已吊销/过期返回 401 `REFRESH_INVALID`；检测到重放（已吊销的 token 被再次使用）返回 401 `REFRESH_REUSE_DETECTED`，整条链全部吊销。

### POST /auth/logout

```json
{
  "refresh_token": "bmV3IHJlZnJl..."
}
```

响应 `204`，该 refresh token 被标记 `revoked_at`，不可再用于刷新。access token 在接下来 ≤15 分钟内仍有效，不做主动失效。

## 3. 上传

### POST /uploads

文件上传（`multipart/form-data`）：

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `file` | file | 是 | 文件本体 |
| `source_type` | string | 是 | `book` / `lecture` / `note` / `keypoint_list` / `past_paper` |
| `shareable` | bool | 否 | 默认 `false` |

文本提交（`application/json`）：

```json
{
  "source_type": "manual_text",
  "raw_text": "本学期重点是傅里叶变换与拉普拉斯变换的关系……"
}
```

`manual_text` 与 `extra_requirement` 不接受 `shareable` 字段，传入即 400（FR-4）。

响应 `201`：

```json
{
  "upload_id": "0f8b2c1e-...",
  "source_type": "lecture",
  "filename": "第三章-傅里叶变换.pptx",
  "size_bytes": 2841600,
  "shareable": true,
  "parse_status": "succeeded",
  "preview": {
    "char_count": 8420,
    "page_count": 32,
    "excerpt": "第三章 傅里叶变换\n3.1 连续时间傅里叶变换的定义……"
  }
}
```

`parse_status` 取 `succeeded` / `failed`。解析失败仍返回 201 并置 `parse_status: "failed"` 与 `parse_error`，因为用户可以选择带着这个失败继续（其他文件仍可用，NFR-5）；只有格式或体积不合规才在此拒绝。

### DELETE /uploads/{upload_id}

删除尚未被任务引用的上传件。已被任务引用时返回 409。

## 4. 创建任务

### POST /jobs

```json
{
  "upload_ids": ["0f8b2c1e-...", "3a91d0f2-..."],
  "school_id": "b21c...",
  "course_id": "77ae...",
  "duration_minutes": 100,
  "need_explanation": true,
  "enable_review": true
}
```

| 字段 | 类型 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- | --- |
| `upload_ids` | string[] | 否 | `[]` | 本次使用的上传件 |
| `school_id` | UUID | 条件 | — | 有共享项或需读共享库时必填 |
| `course_id` | UUID | 条件 | — | 同上 |
| `duration_minutes` | int | 否 | `100` | 考试时长，5~300（FR-7） |
| `need_explanation` | bool | 否 | `false` | 是否生成解析（FR-6） |
| `enable_review` | bool | 否 | `false` | 是否启用审查（FR-8） |

`upload_ids` 为空且不存在任何文本类上传件时返回 400 `INPUT_EMPTY`（FR-3）。

共享意愿在上传时通过 `shareable` 表达，不在创建任务时重复指定——避免两处状态不一致。

响应 `202`：

```json
{
  "job_id": "9c4e7f10-...",
  "status": "pending",
  "created_at": "2026-08-20T07:41:12Z"
}
```

## 5. 任务快照

### GET /jobs/{job_id}

轮询降级用（FR-10）。返回与 SSE 一致的状态。

```json
{
  "job_id": "9c4e7f10-...",
  "status": "generating",
  "stage": "generating",
  "duration_minutes": 100,
  "need_explanation": true,
  "enable_review": true,
  "plan": {
    "total": 24,
    "distribution": {"choice": 6, "blank": 5, "short_answer": 13}
  },
  "progress": {
    "completed": 11,
    "total": 24,
    "retried": 2,
    "replanned": 0,
    "abandoned": 0
  },
  "warnings": [
    {
      "code": "PARSE_FAILED",
      "upload_id": "3a91d0f2-...",
      "message": "扫描版 PDF 的 OCR 未能识别出有效文本"
    }
  ],
  "artifacts": {"md_url": null, "pdf_url": null},
  "last_event_seq": 37,
  "error_code": null,
  "created_at": "2026-08-20T07:41:12Z",
  "finished_at": null
}
```

`status` 取值见 [architecture.md](./architecture.md#4-任务状态机)。`last_event_seq` 可用于 SSE 重连续传。

完成后 `artifacts` 填充：

```json
{
  "status": "completed",
  "artifacts": {
    "md_url": "/api/v1/jobs/9c4e7f10-.../paper.md",
    "pdf_url": "/api/v1/jobs/9c4e7f10-.../paper.pdf"
  },
  "finished_at": "2026-08-20T07:45:38Z"
}
```

## 6. SSE 事件

### GET /jobs/{job_id}/events

`Content-Type: text/event-stream`。每帧含 `id`（对应 `job_stage.seq`）、`event`、`data`。

客户端断连后用 `Last-Event-ID` 头重连，服务端从该 `seq` 之后补发（FR-10）。

事件类型：

| `event` | 触发时机 | 是否可多次 |
| --- | --- | --- |
| `stage_changed` | 阶段切换 | 是 |
| `plan_ready` | 规划完成 | 一次 |
| `question_completed` | 单题产出并通过 | 是 |
| `question_retried` | 单题被打回重生成 | 是 |
| `question_replanned` | 重试耗尽，更换知识点或方向 | 是 |
| `question_abandoned` | 换题后仍失败，放弃该题 | 是 |
| `review_result` | 审查阶段结论 | 一次 |
| `warning` | 非致命问题 | 是 |
| `done` | 任务终结（含部分完成） | 一次 |
| `error` | 任务失败 | 一次 |

### 事件示例

`stage_changed`：

```
id: 3
event: stage_changed
data: {"stage": "planning", "previous": "preprocessing", "at": "2026-08-20T07:41:40Z"}
```

`plan_ready`：

```
id: 8
event: plan_ready
data: {"total": 24, "distribution": {"choice": 6, "blank": 5, "short_answer": 13},
       "reference_used": "past_paper", "duration_minutes": 100}
```

`reference_used` 取 `past_paper`（用户提供）/ `shared_past_paper`（共享库）/ `default_template`（20/20/60 模板），让用户知道占比依据来自哪里。

`question_completed`：

```
id: 15
event: question_completed
data: {"seq": 7, "question_type": "choice", "completed": 7, "total": 24}
```

`completed` / `total` 直接用于进度条，前端不必自行累加。

`question_retried`：

```
id: 19
event: question_retried
data: {"seq": 9, "attempt": 2, "reason": "deviation", "counted": true}
```

`reason` 取值：

| 值 | 含义 | `counted` |
| --- | --- | --- |
| `violation` | 违规内容 | `true` |
| `deviation` | 与规划偏差超限 | `true` |
| `schema_invalid` | tool 参数不合法 | `false` |
| `latex_unrenderable` | 公式无法渲染 | `false` |

`counted` 表示是否计入 3 次上限，对应 FR-20 与 [agent-design.md](./agent-design.md#4-重试与换题协议)。

`question_replanned`：

```
id: 24
event: question_replanned
data: {"seq": 9, "old_plan_item_id": "aa11...", "new_plan_item_id": "bb22...",
       "reason": "连续 3 次生成的题目均偏离规划的考察方向"}
```

`review_result`：

```
id: 31
event: review_result
data: {"checked": 24, "passed": 22,
       "rejected": [{"seq": 5, "reason": "violation"}, {"seq": 18, "reason": "deviation"}],
       "auto_fixed": [{"seq": 12, "reason": "latex_unrenderable"}]}
```

`auto_fixed` 中的项已在审查阶段直接修正，不计入重试（FR-20）。

`warning`：

```
id: 5
event: warning
data: {"code": "PARSE_FAILED", "upload_id": "3a91d0f2-...",
       "message": "扫描版 PDF 的 OCR 未能识别出有效文本"}
```

```
id: 6
event: warning
data: {"code": "MODERATION_REJECTED", "upload_id": "5c73e9a1-...",
       "message": "该资源未通过内容安全检测，不会进入共享库；本次生成不受影响"}
```

`done`：

```
id: 40
event: done
data: {"status": "completed", "total": 24, "abandoned": 0,
       "md_url": "/api/v1/jobs/9c4e7f10-.../paper.md",
       "pdf_url": "/api/v1/jobs/9c4e7f10-.../paper.pdf"}
```

部分完成时：

```
id: 40
event: done
data: {"status": "partially_completed", "total": 24, "abandoned": 1,
       "md_url": "/api/v1/jobs/9c4e7f10-.../paper.md", "pdf_url": null,
       "note": "1 道题因多次生成未通过而放弃；PDF 渲染失败，md 仍可下载"}
```

`error`：

```
id: 12
event: error
data: {"error_code": "PLANNING_FAILED", "message": "规划阶段连续失败，无法产出题目计划"}
```

## 7. 题目列表

### GET /jobs/{job_id}/questions

返回已产出的题目。生成过程中可调用，返回当前已完成的部分。

```json
{
  "job_id": "9c4e7f10-...",
  "need_explanation": true,
  "total": 24,
  "questions": [
    {
      "seq": 1,
      "question_type": "choice",
      "stem": "关于连续时间傅里叶变换的收敛条件，下列说法正确的是：",
      "options": {
        "A": "所有周期信号都满足狄利克雷条件",
        "B": "绝对可积是充分条件而非必要条件",
        "C": "能量有限信号一定不存在傅里叶变换",
        "D": "变换存在性与信号的连续性无关"
      },
      "answer": "B",
      "explanation": "绝对可积保证变换积分收敛，属充分条件……"
    },
    {
      "seq": 7,
      "question_type": "blank",
      "stem": "线性时不变系统的输出等于输入与系统 ______ 的卷积。",
      "answer": "单位冲激响应",
      "explanation": "由 LTI 系统的定义可直接推出……"
    },
    {
      "seq": 12,
      "question_type": "short_answer",
      "stem": "已知某 LTI 系统的频率响应 $H(j\\omega)=\\frac{1}{1+j\\omega}$，回答下列问题。",
      "sub_questions": [
        "求该系统的单位冲激响应 $h(t)$。",
        "判断该系统是否稳定，并说明理由。"
      ],
      "answer": "见各子问题答案。",
      "sub_answers": [
        "$h(t)=e^{-t}u(t)$。",
        "稳定。因为 $\\int_{-\\infty}^{\\infty}|h(t)|dt=1<\\infty$，满足 BIBO 稳定条件。"
      ],
      "explanation": "第 (1) 问由傅里叶反变换的标准对得出……"
    }
  ]
}
```

`sub_questions` 与 `sub_answers` **按索引严格对应**（idea.md 明确要求）。`need_explanation` 为 `false` 时所有 `explanation` 字段不出现。

## 8. 产物下载

### GET /jobs/{job_id}/paper.md

`Content-Type: text/markdown; charset=utf-8`

### GET /jobs/{job_id}/paper.pdf

`Content-Type: application/pdf`

任务未到 `rendering` 完成时返回 409；`partially_completed` 且 PDF 渲染失败时，md 可下载而 PDF 返回 409 `RENDER_FAILED`。

md 结构见 [agent-design.md](./agent-design.md#10-md-合成规范)：题卷在前，答案与解析单独成篇。

## 9. 取消与删除

### POST /jobs/{job_id}/cancel

对运行中的任务发出取消。已终结的任务返回 409。

### DELETE /jobs/{job_id}

删除任务与产物。级联规则见 [data-model.md](./data-model.md#7-数据删除)——已共享的资源不随之删除。

## 10. 学校与课程

### GET /schools

```json
{"schools": [{"school_id": "b21c...", "name": "某大学"}]}
```

### GET /schools/{school_id}/courses

```json
{"courses": [{"course_id": "77ae...", "name": "信号与系统",
              "shared_resource_count": 47}]}
```

`shared_resource_count` 让用户判断该课程的共享库是否有内容可用。

## 11. 错误码

统一错误响应：

```json
{
  "error_code": "UNSUPPORTED_FORMAT",
  "message": "不支持的文件格式：.gif",
  "detail": {"filename": "板书.gif", "allowed": ["docx", "pptx", "pdf", "md", "jpg", "png"]}
}
```

| 错误码 | HTTP | 含义 | 前端建议动作 |
| --- | --- | --- | --- |
| `INPUT_EMPTY` | 400 | 无任何输入 | 提交前拦截，提示至少提供一项 |
| `UNSUPPORTED_FORMAT` | 400 | 格式不支持 | 提示允许的格式，不动摇其他文件 |
| `FILE_TOO_LARGE` | 413 | 超出大小上限 | 提示上限值，建议拆分 |
| `PDF_ENCRYPTED` | 400 | 加密 PDF 无法解析 | 提示先移除密码 |
| `SHARE_NOT_ALLOWED` | 400 | 对手动输入类内容请求共享 | 前端不应呈现该选项，此为兜底 |
| `SHARE_SCOPE_REQUIRED` | 400 | 共享但未指定学校课程 | 引导选择学校与课程 |
| `INVALID_DURATION` | 400 | 时长超出 5~300 | 限制输入范围 |
| `JOB_NOT_FOUND` | 404 | 任务不存在或无权访问 | 返回任务列表 |
| `UPLOAD_IN_USE` | 409 | 上传件已被任务引用 | 提示不可删除 |
| `JOB_NOT_READY` | 409 | 产物尚未生成 | 继续等待，勿反复请求 |
| `JOB_ALREADY_FINISHED` | 409 | 对已终结任务发取消 | 刷新状态 |
| `PARSE_FAILED` | — | 解析失败（作为 warning 出现） | 展示为可忽略的警告 |
| `MODERATION_REJECTED` | — | 未通过安全检测（作为 warning） | 说明不影响本次生成 |
| `PLANNING_FAILED` | — | 规划阶段失败（任务级 error） | 建议补充资料后重试 |
| `GENERATION_EXHAUSTED` | — | 题目生成耗尽重试（任务级 error） | 建议减少时长或补充资料 |
| `RENDER_FAILED` | 409 | PDF 渲染失败 | 提供 md 下载作为替代 |
| `MODEL_UNAVAILABLE` | 503 | LLM 服务不可用 | 稍后重试 |
| `RATE_LIMITED` | 429 | 超出速率限制 | 按 `Retry-After` 退避 |
| `USER_EXISTS` | 409 | 邮箱或用户名已被注册 | 提示换一个 |
| `INVALID_CREDENTIALS` | 401 | 登录凭证错误 | 提示用户重输 |
| `TOKEN_MISSING` | 401 | 未携带 access token | 前端中间件拦截，重定向登录 |
| `TOKEN_EXPIRED` | 401 | access token 已过期 | 前端自动用 refresh token 续期，刷新后重放原请求 |
| `REFRESH_INVALID` | 401 | refresh token 已吊销/过期 | 清除本地 token，引导重新登录 |
| `REFRESH_REUSE_DETECTED` | 401 | 检测到重放（已吊销 token 被复用） | 吊销整条链，强制重新登录；提示安全问题 |

`PARSE_FAILED`、`MODERATION_REJECTED` 无 HTTP 状态，因为它们只作为 `warning` 事件或 `job.warnings` 出现，不构成请求失败（FR-16、NFR-5）。`PLANNING_FAILED`、`GENERATION_EXHAUSTED` 同理，出现在 `error` 事件与 `job.error_code` 中。

## 12. 约定

- 时间戳统一 ISO 8601 UTC，带 `Z`
- 所有 ID 为 UUID 字符串
- 分页暂不需要（单用户任务量有限），将来加则用 `cursor` + `limit`
- 鉴权采用双 JWT（见第 2 节），除 `/auth/*` 与 `GET /schools`、`GET /schools/{school_id}/courses` 外，所有端点需携带有效 access token；`/uploads`、`/jobs` 及子资源还需校验资源归属于当前用户
