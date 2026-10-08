// 与 docs/openapi.yaml 的 components/schemas 严格对应。
// source_type / 错误码等枚举以后端 app/core（enums / exceptions）为准。

/** 全部内容类型（含手动输入） */
export type SourceType =
  | 'book'
  | 'lecture'
  | 'note'
  | 'keypoint_list'
  | 'past_paper'
  | 'manual_text'
  | 'extra_requirement'

/** 文件类内容类型（上传接口 file 分支可用） */
export type SourceTypeFile = Exclude<SourceType, 'manual_text' | 'extra_requirement'>

/** 文本类内容类型（上传接口 json 分支可用） */
export type SourceTypeText = 'manual_text' | 'extra_requirement'

/** 文件可见性：私有 / 本校 / 公开池 */
export type Visibility = 'private' | 'school' | 'public'

/** 解析状态（文件上传后异步解析） */
export type ParseStatus = 'pending' | 'parsing' | 'succeeded' | 'failed'

/** 个人库条目状态：可用 / 解析中 / 解析失败 / 原件已删除（文件已失效） */
export type FileStatus = 'available' | 'parsing' | 'failed' | 'deleted'

export interface User {
  user_id: string
  email: string
  username: string
  school_id?: string | null
}

export interface UserResponse {
  user: User
}

export interface RegisterRequest {
  email: string
  username: string
  password: string
  /** 学校归属（可选），决定「本校可见」文件的观看资格 */
  school_id?: string | null
}

export interface UserUpdateRequest {
  /** 显式 null 表示清除学校归属 */
  school_id?: string | null
}

export interface LoginRequest {
  email?: string | null
  username?: string | null
  password: string
}

// ---- 上传 ----

export interface Upload {
  upload_id: string
  source_type: SourceType
  filename?: string | null
  size_bytes?: number | null
  visibility: Visibility
  parse_status: ParseStatus
}

export interface UploadUpdateRequest {
  /** 显式 null 表示清除标签 */
  visibility?: Visibility
  school_id?: string | null
  course_id?: string | null
}

export interface TextUploadCreate {
  source_type: SourceTypeText
  raw_text: string
}

// ---- 个人库 / 公开池 ----

export interface FileOwner {
  user_id: string
  username: string
}

export interface LibraryFile {
  upload_id: string
  source_type: SourceType
  filename?: string | null
  size_bytes?: number | null
  visibility: Visibility
  school_id?: string | null
  course_id?: string | null
  char_count?: number | null
  parse_status: ParseStatus
  parse_error?: string | null
  parse_warnings?: { code: string; message: string }[]
  created_at: string
  owner: FileOwner
  /** 是否为公开池保存的引用（false = 本人上传） */
  imported: boolean
  imported_at?: string | null
  status: FileStatus
}

export interface LibraryResponse {
  files: LibraryFile[]
  total: number
  page: number
  page_size: number
}

export interface UploadPreview {
  upload_id: string
  filename?: string | null
  source_type: SourceType
  visibility: Visibility
  school_id?: string | null
  course_id?: string | null
  school_name?: string | null
  course_name?: string | null
  char_count?: number | null
  text: string
  truncated: boolean
}

export interface PoolFile {
  upload_id: string
  source_type: SourceType
  filename?: string | null
  size_bytes?: number | null
  visibility: Visibility
  school_id?: string | null
  course_id?: string | null
  school_name?: string | null
  course_name?: string | null
  char_count?: number | null
  created_at: string
  owner: FileOwner
  is_saved: boolean
  semantic_score?: number | null
}

export interface PoolFilesResponse {
  files: PoolFile[]
  total: number
  page: number
  page_size: number
}

export interface ImportAccepted {
  saved: boolean
  upload_id: string
}

// ---- 任务 ----

export interface JobCreate {
  upload_ids?: string[]
  duration_minutes?: number
  need_explanation?: boolean
}

export interface JobAccepted {
  job_id: string
  status: 'pending'
  created_at: string
}

export interface PlanDistribution {
  choice?: number
  blank?: number
  short_answer?: number
}

export interface Plan {
  total?: number
  distribution?: PlanDistribution
}

export interface Progress {
  completed?: number
  total?: number
  retried?: number
  replanned?: number
  abandoned?: number
}

export interface Warning {
  code: 'PARSE_FAILED' | 'AGENT_WARNING' | 'RENDER_FAILED'
  upload_id?: string | null
  message?: string
}

export interface Artifacts {
  md_url?: string | null
  pdf_url?: string | null
}

export type JobStatus =
  | 'pending'
  | 'preprocessing'
  | 'generating'
  | 'rendering'
  | 'completed'
  | 'partially_completed'
  | 'failed'
  | 'cancelled'

export type Stage =
  | 'preprocessing'
  | 'generating'
  | 'rendering'

export interface Job {
  job_id: string
  status: JobStatus
  stage?: Stage
  duration_minutes?: number
  need_explanation?: boolean
  school_id?: string | null
  course_id?: string | null
  plan?: Plan
  progress?: Progress
  warnings?: Warning[]
  artifacts?: Artifacts
  last_event_seq?: number
  error_code?: string | null
  created_at: string
  finished_at?: string | null
}

export interface JobsResponse {
  jobs: Job[]
}

// ---- 修订（划选反馈） ----

export interface SelectionAnchor {
  text: string
  before?: string
  after?: string
}

export interface RevisionCreate {
  selection: SelectionAnchor
  feedback: string
  /** 是否把本条反馈沉淀为该课程的持久出题偏好 */
  save_preference?: boolean
}

export interface RevisionAccepted {
  revision_id: string
  round_no: number
  status: 'running'
}

export type RevisionStatus = 'running' | 'done' | 'failed'

export interface RevisionItem {
  revision_id: string
  round_no: number
  status: RevisionStatus
  selection?: SelectionAnchor | null
  feedback: string
  summary?: string | null
  error?: string | null
  created_at: string
  applied_at?: string | null
  /** 本轮反馈是否已沉淀为课程出题偏好 */
  preference_saved?: boolean
}

export interface RevisionsResponse {
  job_id: string
  total: number
  revisions: RevisionItem[]
}

// ---- 学校 / 课程 ----

export interface School {
  school_id: string
  name: string
}

export interface SchoolsResponse {
  schools: School[]
}

export interface Course {
  course_id: string
  name: string
  /** 该课程在公开池中的可见文件数 */
  pool_file_count?: number
}

export interface CoursesResponse {
  courses: Course[]
}

// ---- 错误 ----

export type ErrorCode =
  | 'INPUT_EMPTY'
  | 'UNSUPPORTED_FORMAT'
  | 'FILE_TOO_LARGE'
  | 'PDF_ENCRYPTED'
  | 'SHARE_NOT_ALLOWED'
  | 'SHARE_SCOPE_REQUIRED'
  | 'INVALID_DURATION'
  | 'JOB_NOT_FOUND'
  | 'SCHOOL_NOT_FOUND'
  | 'COURSE_NOT_FOUND'
  | 'UPLOAD_IN_USE'
  | 'UPLOAD_NOT_PARSED'
  | 'IMPORT_NOT_ALLOWED'
  | 'JOB_NOT_READY'
  | 'JOB_ALREADY_FINISHED'
  | 'JOB_NOT_FINISHED'
  | 'REVISION_IN_PROGRESS'
  | 'PAPER_NOT_READY'
  | 'PREFERENCE_SCOPE_REQUIRED'
  | 'PARSE_FAILED'
  | 'PLANNING_FAILED'
  | 'GENERATION_EXHAUSTED'
  | 'RENDER_FAILED'
  | 'AGENT_WARNING'
  | 'PIPELINE_FAILED'
  | 'SERVER_RESTARTED'
  | 'OCR_DEGRADED'
  | 'MODEL_UNAVAILABLE'
  | 'RATE_LIMITED'
  | 'USER_EXISTS'
  | 'INVALID_CREDENTIALS'
  | 'TOKEN_MISSING'
  | 'TOKEN_EXPIRED'
  | 'REFRESH_INVALID'
  | 'REFRESH_REUSE_DETECTED'

export interface ErrorResponse {
  error_code: ErrorCode
  message: string
  detail?: Record<string, unknown>
}

// ---- SSE 事件 ----
// 事件结构见 docs/openapi.yaml 的 /jobs/{job_id}/events 端点描述。每帧含 id（seq）、event、data。

export type JobEventType =
  | 'stage_changed'
  | 'plan_ready'
  | 'progress'
  | 'warning'
  | 'done'
  | 'error'
  | 'revision_started'
  | 'revision_done'
  | 'revision_failed'
  | 'preference_saved'

export interface StageChangedData {
  stage: Stage
  // 首帧 previous 为 pending（进入 preprocessing），后续帧为上一个 Stage
  previous?: Stage | 'pending'
}

export interface PlanReadyData {
  total: number
  distribution: PlanDistribution
  reference_used: 'agent_planning'
  duration_minutes?: number
}

export interface ProgressData {
  completed: number
  total: number
}

export interface RevisionEventData {
  round_no: number
  feedback?: string
  message?: string
  render_status?: string
  /** preference_saved 事件：偏好沉淀结果说明 */
  note?: string
}

export interface WarningData {
  code: 'PARSE_FAILED' | 'OCR_DEGRADED' | 'AGENT_WARNING' | 'RENDER_FAILED'
  upload_id?: string | null
  message: string
}

export interface DoneData {
  status: 'completed' | 'partially_completed' | 'failed' | 'cancelled'
  total?: number
  abandoned?: number
  md_url?: string | null
  pdf_url?: string | null
}

export interface ErrorData {
  error_code: string
  message: string
}

export type JobEventData =
  | StageChangedData
  | PlanReadyData
  | ProgressData
  | WarningData
  | DoneData
  | ErrorData
  | RevisionEventData

export interface JobEvent {
  id?: number
  event: JobEventType
  data: JobEventData
}
