// 与 docs/openapi.yaml 的 components/schemas 严格对应。
// source_type / 错误码等枚举见 docs/README.md 术语表与 api.md 第 11 节。

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

export interface User {
  user_id: string
  email: string
  username: string
}

export interface UserResponse {
  user: User
}

export interface RegisterRequest {
  email: string
  username: string
  password: string
}

export interface LoginRequest {
  email?: string | null
  username?: string | null
  password: string
}

export interface RefreshRequest {
  refresh_token: string
}

export interface LogoutRequest {
  refresh_token: string
}

export interface AuthTokens {
  access_token: string
  refresh_token: string
  expires_in: number
  user?: User
}

// ---- 上传 ----

export interface Preview {
  char_count?: number
  page_count?: number | null
  excerpt?: string
}

export type ParseStatus = 'succeeded' | 'failed'

export interface Upload {
  upload_id: string
  source_type: SourceType
  filename?: string | null
  size_bytes?: number | null
  shareable?: boolean
  parse_status: ParseStatus
  parse_error?: string | null
  preview?: Preview
}

export interface TextUploadCreate {
  source_type: SourceTypeText
  raw_text: string
}

// ---- 任务 ----

export interface JobCreate {
  upload_ids?: string[]
  school_id?: string | null
  course_id?: string | null
  duration_minutes?: number
  need_explanation?: boolean
  enable_review?: boolean
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
  code: 'PARSE_FAILED' | 'MODERATION_REJECTED'
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
  | 'planning'
  | 'generating'
  | 'reviewing'
  | 'rendering'
  | 'completed'
  | 'partially_completed'
  | 'failed'
  | 'cancelled'

export type Stage =
  | 'preprocessing'
  | 'planning'
  | 'generating'
  | 'reviewing'
  | 'rendering'

export interface Job {
  job_id: string
  status: JobStatus
  stage?: Stage
  duration_minutes?: number
  need_explanation?: boolean
  enable_review?: boolean
  plan?: Plan
  progress?: Progress
  warnings?: Warning[]
  artifacts?: Artifacts
  last_event_seq?: number
  error_code?: string | null
  created_at: string
  finished_at?: string | null
}

// ---- 题目 ----

export type QuestionType = 'choice' | 'blank' | 'short_answer'

export interface Question {
  seq: number
  question_type: QuestionType
  stem: string
  options?: Record<string, string> | null
  answer: string
  explanation?: string | null
  sub_questions?: string[] | null
  sub_answers?: string[] | null
}

export interface QuestionsResponse {
  job_id: string
  need_explanation?: boolean
  total: number
  questions: Question[]
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
  shared_resource_count?: number
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
  | 'UPLOAD_IN_USE'
  | 'JOB_NOT_READY'
  | 'JOB_ALREADY_FINISHED'
  | 'PARSE_FAILED'
  | 'MODERATION_REJECTED'
  | 'PLANNING_FAILED'
  | 'GENERATION_EXHAUSTED'
  | 'RENDER_FAILED'
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
// 见 api.md 第 6 节。每帧含 id（seq）、event、data。

export type JobEventType =
  | 'stage_changed'
  | 'plan_ready'
  | 'question_completed'
  | 'question_retried'
  | 'question_replanned'
  | 'question_abandoned'
  | 'review_result'
  | 'warning'
  | 'done'
  | 'error'

export interface StageChangedData {
  stage: Stage
  previous?: Stage
  at?: string
}

export interface PlanReadyData {
  total: number
  distribution: PlanDistribution
  reference_used: 'past_paper' | 'shared_past_paper' | 'default_template'
  duration_minutes?: number
}

export interface QuestionCompletedData {
  seq: number
  question_type: QuestionType
  completed: number
  total: number
}

export type RetryReason = 'violation' | 'deviation' | 'schema_invalid' | 'latex_unrenderable'

export interface QuestionRetriedData {
  seq: number
  attempt: number
  reason: RetryReason
  counted: boolean
}

export interface QuestionReplannedData {
  seq: number
  old_plan_item_id: string
  new_plan_item_id: string
  reason: string
}

export interface QuestionAbandonedData {
  seq: number
  reason: string
}

export interface ReviewResultData {
  checked: number
  passed: number
  rejected: Array<{ seq: number; reason: RetryReason }>
  auto_fixed: Array<{ seq: number; reason: RetryReason }>
}

export interface WarningData {
  code: 'PARSE_FAILED' | 'MODERATION_REJECTED'
  upload_id?: string | null
  message: string
}

export interface DoneData {
  status: 'completed' | 'partially_completed'
  total: number
  abandoned: number
  md_url?: string | null
  pdf_url?: string | null
  note?: string
}

export interface ErrorData {
  error_code: string
  message: string
}

export type JobEventData =
  | StageChangedData
  | PlanReadyData
  | QuestionCompletedData
  | QuestionRetriedData
  | QuestionReplannedData
  | QuestionAbandonedData
  | ReviewResultData
  | WarningData
  | DoneData
  | ErrorData

export interface JobEvent {
  id?: number
  event: JobEventType
  data: JobEventData
}
