# 数据模型与存储设计

关系库为 PostgreSQL 风格 DDL（具体向量库选型见 [tech-selection.md](./tech-selection.md#3-向量库)）。术语与 `source_type` 枚举见 [README.md](./README.md#术语表)。

## 1. 存储职责划分

| 存储 | 存什么 | 为什么放这 |
| --- | --- | --- |
| 关系库 | 用户、学校、课程、上传件、资源、任务、规划项、题目、审核记录 | 需要事务、外键约束、按条件聚合查询 |
| 向量库 | 叠加类资源的 chunk 及其向量 | 语义检索 |
| 对象存储 | 原始文件、解析产物、md、PDF | 大文件，不适合进数据库 |
| 快读缓存 | 往期试卷全文 | 规划与出题阶段反复读取，见第 5 节 |

## 2. 关系表

### 2.1 用户与归属

```sql
CREATE TABLE app_user (
    id          UUID PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE school (
    id          UUID PRIMARY KEY,
    name        TEXT NOT NULL,
    UNIQUE (name)
);

CREATE TABLE course (
    id          UUID PRIMARY KEY,
    school_id   UUID NOT NULL REFERENCES school(id),
    name        TEXT NOT NULL,
    UNIQUE (school_id, name)
);
```

课程隶属于学校，因为「高等数学」在不同学校的考察范围不同，共享库必须按 `(school_id, course_id)` 双维度隔离。

### 2.2 上传件与资源

```sql
CREATE TYPE source_type AS ENUM (
    'book', 'lecture', 'note',
    'keypoint_list', 'past_paper',
    'manual_text', 'extra_requirement'
);

CREATE TABLE upload (
    id            UUID PRIMARY KEY,
    user_id       UUID NOT NULL REFERENCES app_user(id),
    source_type   source_type NOT NULL,
    -- 文件类字段，manual_text / extra_requirement 时为 NULL
    filename      TEXT,
    mime_type     TEXT,
    size_bytes    BIGINT,
    storage_key   TEXT,
    -- 文本类字段，文件类时为 NULL
    raw_text      TEXT,
    shareable     BOOLEAN NOT NULL DEFAULT false,
    parse_status  TEXT NOT NULL DEFAULT 'pending',
    parse_error   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- FR-4：手动输入类内容不可共享，这是数据库约束而非前端约定
    CONSTRAINT manual_not_shareable CHECK (
        source_type NOT IN ('manual_text', 'extra_requirement')
        OR shareable = false
    ),
    -- 文件类必须有存储位置，文本类必须有正文
    CONSTRAINT payload_present CHECK (
        (source_type IN ('manual_text', 'extra_requirement') AND raw_text IS NOT NULL)
        OR (source_type NOT IN ('manual_text', 'extra_requirement') AND storage_key IS NOT NULL)
    )
);

CREATE INDEX idx_upload_user ON upload(user_id, created_at DESC);
```

`manual_not_shareable` 这条 CHECK 是 FR-4 的落地。写成约束而非应用层校验，是因为它属于产品的硬规则——用户手动输入的内容可能包含私人信息，任何代码路径都不应能把它写进共享库。

```sql
CREATE TABLE resource (
    id            UUID PRIMARY KEY,
    upload_id     UUID NOT NULL REFERENCES upload(id) ON DELETE CASCADE,
    source_type   source_type NOT NULL,
    school_id     UUID REFERENCES school(id),
    course_id     UUID REFERENCES course(id),
    parsed_key    TEXT,           -- 解析产物在对象存储中的位置
    char_count    INTEGER,
    is_shared     BOOLEAN NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- 进入共享库必须有归属
    CONSTRAINT shared_needs_scope CHECK (
        is_shared = false OR (school_id IS NOT NULL AND course_id IS NOT NULL)
    )
);

-- 共享库检索的主索引，对应第 4 节的取用策略
CREATE INDEX idx_resource_shared_scope
    ON resource(school_id, course_id, source_type)
    WHERE is_shared = true;
```

`resource` 与 `upload` 分开，因为一个上传件可能解析出多个资源（如一份混合了讲义与例题的 PDF），也因为共享状态属于解析后的资源而非原始文件。

### 2.3 内容安全记录

```sql
CREATE TABLE moderation_record (
    id            UUID PRIMARY KEY,
    resource_id   UUID NOT NULL REFERENCES resource(id) ON DELETE CASCADE,
    passed        BOOLEAN NOT NULL,
    provider      TEXT NOT NULL,      -- 检测服务标识
    labels        JSONB,              -- 命中的标签，如 ["political", "porn"]
    raw_response  JSONB,
    checked_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_moderation_resource ON moderation_record(resource_id);
```

保留 `raw_response` 是为了合规追溯（NFR-6）。检测不通过时，`resource.is_shared` 保持 `false`，但资源本身仍存在——它对本次生成仍然可用（FR-16）。

### 2.4 任务

```sql
CREATE TABLE job (
    id                UUID PRIMARY KEY,
    user_id           UUID NOT NULL REFERENCES app_user(id),
    school_id         UUID REFERENCES school(id),
    course_id         UUID REFERENCES course(id),
    status            TEXT NOT NULL DEFAULT 'pending',
    duration_minutes  INTEGER NOT NULL DEFAULT 100,   -- FR-7
    need_explanation  BOOLEAN NOT NULL DEFAULT false, -- FR-6
    enable_review     BOOLEAN NOT NULL DEFAULT false, -- FR-8
    -- 规划结果概要，便于不读 plan_item 就能展示
    planned_total     INTEGER,
    md_key            TEXT,
    pdf_key           TEXT,
    error_code        TEXT,
    warnings          JSONB NOT NULL DEFAULT '[]',    -- NFR-5 单文件失败等
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ,

    CONSTRAINT valid_duration CHECK (duration_minutes BETWEEN 5 AND 300)
);

CREATE INDEX idx_job_user ON job(user_id, created_at DESC);

-- 任务与上传件的多对多
CREATE TABLE job_upload (
    job_id     UUID NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    upload_id  UUID NOT NULL REFERENCES upload(id),
    PRIMARY KEY (job_id, upload_id)
);
```

`status` 取值见 [architecture.md](./architecture.md#4-任务状态机)。

```sql
CREATE TABLE job_stage (
    id          BIGSERIAL PRIMARY KEY,
    job_id      UUID NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    stage       TEXT NOT NULL,
    event_type  TEXT NOT NULL,   -- 见 api.md 的 SSE 事件类型
    payload     JSONB NOT NULL,
    seq         INTEGER NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (job_id, seq)
);

CREATE INDEX idx_job_stage_job_seq ON job_stage(job_id, seq);
```

`job_stage` 同时服务三个用途：SSE 实时推送的落盘、轮询降级接口的数据源（FR-10）、SSE 断连重连后的事件补发。`seq` 单调递增，前端用 `Last-Event-ID` 续传。

### 2.5 规划项与题目

```sql
CREATE TABLE plan_item (
    id               UUID PRIMARY KEY,
    job_id           UUID NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    seq              INTEGER NOT NULL,      -- 试卷内顺序
    question_type    TEXT NOT NULL,         -- choice / blank / short_answer
    knowledge_point  TEXT NOT NULL,
    exam_direction   TEXT NOT NULL,         -- 考察方向，FR-13 去重的判断依据
    difficulty       TEXT NOT NULL,         -- easy / medium / hard
    reference_source JSONB,                 -- 参考来源，FR-12 沿用判定用
    superseded_by    UUID REFERENCES plan_item(id),  -- 换题后指向新规划项
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (job_id, seq)
);
```

`plan_item` 的字段**不会作为出题 tool 的独立参数传递**（idea.md 明确要求），它们经提示词进入子 agent，最终以 HTML 注释形式隐式保存在 md 中。`superseded_by` 记录换题链，用于排查某题为何被换以及防止无限换题。

```sql
CREATE TABLE question (
    id              UUID PRIMARY KEY,
    job_id          UUID NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    plan_item_id    UUID NOT NULL REFERENCES plan_item(id),
    seq             INTEGER NOT NULL,
    question_type   TEXT NOT NULL,
    stem            TEXT NOT NULL,
    -- 选择题：{"A": "...", "B": "...", "C": "...", "D": "..."}
    options         JSONB,
    answer          TEXT NOT NULL,
    -- 简答题子问题与子答案，按索引严格对应
    sub_questions   JSONB,
    sub_answers     JSONB,
    explanation     TEXT,                  -- need_explanation=false 时为 NULL
    retry_count     INTEGER NOT NULL DEFAULT 0,
    replanned_from  UUID REFERENCES plan_item(id),
    review_passed   BOOLEAN,               -- NULL 表示未审查
    status          TEXT NOT NULL DEFAULT 'draft',  -- draft/accepted/abandoned
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    UNIQUE (job_id, seq),
    -- 选择题必须有四个选项
    CONSTRAINT choice_has_options CHECK (
        question_type <> 'choice' OR options IS NOT NULL
    ),
    -- 子问题与子答案必须同时存在或同时为空
    CONSTRAINT sub_pair CHECK (
        (sub_questions IS NULL) = (sub_answers IS NULL)
    ),
    CONSTRAINT retry_bounded CHECK (retry_count <= 3)
);

CREATE INDEX idx_question_job ON question(job_id, seq);
```

`sub_pair` 与长度相等的校验共同保证 idea.md 强调的「问题和答案的调用顺序应当相互对应」。长度相等无法用 CHECK 表达（跨 JSONB 数组），需在 tool 层校验，见 [agent-design.md](./agent-design.md#7-出题-tool-schema)。

`retry_count` 必须落盘，否则进程重启会重置计数，绕过 3 次上限。

```sql
CREATE TABLE retry_log (
    id            BIGSERIAL PRIMARY KEY,
    question_id   UUID REFERENCES question(id) ON DELETE CASCADE,
    plan_item_id  UUID NOT NULL REFERENCES plan_item(id),
    attempt       INTEGER NOT NULL,
    reason        TEXT NOT NULL,        -- 见 agent-design.md 的重试原因分类
    counted       BOOLEAN NOT NULL,     -- 是否计入 3 次上限
    detail        JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

`counted` 字段区分「计入上限的内容问题」与「不计入的语法/渲染问题」，是 FR-20 的落地依据。

### 2.6 LLM 调用记录

```sql
CREATE TABLE llm_call (
    id                 BIGSERIAL PRIMARY KEY,
    job_id             UUID NOT NULL REFERENCES job(id) ON DELETE CASCADE,
    agent_role         TEXT NOT NULL,   -- planner/choice_writer/.../reviewer/compressor
    model              TEXT NOT NULL,
    prompt_tokens      INTEGER,
    completion_tokens  INTEGER,
    latency_ms         INTEGER,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_llm_call_job ON llm_call(job_id);
```

支撑 NFR-4 的 token 用量按角色聚合，用于判断成本花在规划还是出题上。

## 3. 向量库

### 3.1 Collection 划分

两种方案，取舍如下：

| 方案 | 优点 | 缺点 |
| --- | --- | --- |
| **单 collection + metadata 过滤** | 运维简单；跨校检索（若将来需要）零成本；共享库与个人内容同构 | 数据量增长后过滤选择性依赖 payload 索引质量；误配 filter 会跨校泄漏 |
| **按学校分 collection** | 物理隔离，不可能跨校泄漏；单 collection 规模小，检索快 | collection 数量随学校数增长，运维负担重；跨校统计困难 |

**倾向单 collection + metadata 过滤**，理由是首版学校数量有限，物理隔离带来的运维成本不划算；跨校泄漏风险用「filter 构造集中在一处、有单元测试覆盖」来控制，见第 4 节。此项标注 【待定】，若合规要求物理隔离则改用第二种。

### 3.2 Chunk metadata

每个 chunk 必须携带以下字段，它们直接决定第 4 节的过滤能力：

| 字段 | 类型 | 用途 |
| --- | --- | --- |
| `chunk_id` | string | 主键 |
| `resource_id` | UUID | 回溯到 `resource` 表，也用于断点判断是否已入库 |
| `upload_id` | UUID | 区分「本次上传」与「共享库已有」 |
| `user_id` | UUID | 个人内容的归属，防跨用户读取 |
| `school_id` | UUID | 共享库过滤维度 |
| `course_id` | UUID | 共享库过滤维度 |
| `source_type` | string | 排他/叠加策略的判断依据 |
| `is_shared` | bool | 是否属于共享库 |
| `page` | int | PDF 页码 / PPT 页号，用于溯源标注 |
| `chunk_index` | int | 在资源内的顺序 |

**`school_id`、`course_id`、`source_type`、`is_shared` 必须建 payload 索引**。部分向量库要求索引在数据写入前创建，否则已写入的数据不会被索引，需在初始化脚本中处理。

### 3.3 切块策略

| 格式 | 切块方式 | 理由 |
| --- | --- | --- |
| PPT / PPTX | 按页（每页一 chunk，超长再切） | 幻灯片本身就是语义单元 |
| PDF | 按语义段落，跨页合并 | 段落可能跨页，按页切会截断句子 |
| DOCX | 按标题层级 | 标题结构反映知识点组织 |
| Markdown | 按标题层级 | 同上 |
| 图片 | OCR 全文作一 chunk，超长再切 | 单张图片信息量有限 |

建议参数：chunk 大小 500~800 字（中文），overlap 100 字。这些值需在真实资料上验证后调整，标注 【待定】。

## 4. 取用策略 → 检索过滤

这是 [prd.md](./prd.md#2-输入矩阵) 取用策略的技术落地。构造过滤条件的代码集中在 `app/retrieval/filters.py`，不允许在别处手写 filter。

### 4.1 叠加类（`book` / `lecture` / `note`）

无论用户本次是否提供，都叠加共享库同校同课程的同类内容：

```
本次上传的该类内容  OR  共享库中同校同课程的该类内容
```

伪代码：

```python
def additive_filter(job, source_type) -> Filter:
    return Or([
        # 本次上传
        And([
            In("upload_id", job.upload_ids),
            Eq("source_type", source_type),
        ]),
        # 共享库
        And([
            Eq("school_id", job.school_id),
            Eq("course_id", job.course_id),
            Eq("source_type", source_type),
            Eq("is_shared", True),
        ]),
    ])
```

### 4.2 排他类（`keypoint_list` / `past_paper`）

用户本次提供了该类型，就只用用户的；未提供才用共享库的：

```python
def exclusive_source(job, source_type) -> Filter:
    own = [u for u in job.uploads if u.source_type == source_type]
    if own:
        # 用户已提供，完全不引入共享库同类内容
        return And([
            In("upload_id", [u.id for u in own]),
            Eq("source_type", source_type),
        ])
    return And([
        Eq("school_id", job.school_id),
        Eq("course_id", job.course_id),
        Eq("source_type", source_type),
        Eq("is_shared", True),
    ])
```

注意排他类实际不走向量检索（重点清单进提示词、往期试卷走快读缓存），这里的 filter 用于**筛选资源清单**，而非 chunk 检索。两者共用同一套 metadata 字段以保持一致。

### 4.3 跨用户隔离

任何检索都必须附加：本次上传的部分限定 `user_id = job.user_id`；共享库部分不限 `user_id`（那正是共享的意义），但必须限 `is_shared = true`。

`is_shared = true` 与 `school_id`/`course_id` 三者必须同时出现，漏掉 `is_shared` 会读到同校同课程其他用户未共享的私有内容。这是本设计中最容易出错的一处，需有单元测试覆盖。

## 5. 往期试卷的特殊处理

idea.md 要求往期试卷「保存在文件中并在内存中保留尽可能方便读取的存储，在规划和出题阶段需要时常查看」。

**不切块入向量库**，因为切块会丢失试卷的整体结构——题型分布、题目顺序、分值配置，而这恰是规划阶段最需要的信息。检索回来的零散片段无法支撑「各题型占比是多少」这类判断。

存储方式：

1. 原始文件与解析产物存对象存储
2. 任务开始时把解析后的全文加载进进程内缓存，键为 `job_id`
3. 规划阶段全文进上下文；出题阶段按 `plan_item.reference_source` 定位到具体题目片段
4. 任务结束时释放

若同时有多份往期试卷，全部加载。规划 agent 需综合参考取较大共同点（题型分布、知识点覆盖、难度分配三个维度），见 [agent-design.md](./agent-design.md#3-多份往期试卷的综合)。

缓存容量需设上限，超出时降级为按需从对象存储读取。上限值 【待定】。

## 6. 对象存储布局

```
jobs/{job_id}/
├── raw/{upload_id}.{ext}        # 原始上传文件
├── parsed/{resource_id}.json    # 解析产物（结构化文本）
├── prompt/{name}.txt            # 压缩后的提示词素材
└── output/
    ├── paper.md
    └── paper.pdf

shared/{school_id}/{course_id}/{source_type}/
├── raw/{resource_id}.{ext}
└── parsed/{resource_id}.json
```

共享资源单独一套路径，因为它的生命周期独立于产生它的任务（NFR-7）。

留存期：

| 内容 | 留存 |
| --- | --- |
| `jobs/{job_id}/raw/` | 30 天 【待定】 |
| `jobs/{job_id}/parsed/` | 30 天 【待定】 |
| `jobs/{job_id}/output/` | 90 天 【待定】 |
| `shared/**` | 长期 |

## 7. 数据删除

用户删除任务时的级联行为：

| 数据 | 是否删除 |
| --- | --- |
| `job` 及 `job_stage`、`plan_item`、`question`、`retry_log`、`llm_call` | 删除（外键 CASCADE） |
| `jobs/{job_id}/**` 对象存储 | 删除 |
| `upload`、`resource` | 删除**未共享的**；已共享的保留 |
| 向量库中 `is_shared = false` 的 chunk | 删除 |
| 向量库中 `is_shared = true` 的 chunk | 保留 |
| `shared/**` 对象存储 | 保留 |
| `moderation_record` | 随 `resource` 级联 |

已共享内容不随个人任务删除而消失——它已进入公共知识库，其他用户可能正依赖它。这条规则必须在用户勾选共享时明确告知（NFR-7），否则构成预期落差。
