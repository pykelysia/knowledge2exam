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
  | 'SCHOOL_NOT_FOUND'
  | 'UPLOAD_IN_USE'
  | 'JOB_NOT_READY'
  | 'JOB_ALREADY_FINISHED'
  | 'PARSE_FAILED'
  | 'MODERATION_REJECTED'
  | 'PLANNING_FAILED'
  | 'GENERATION_EXHAUSTED'
  | 'RENDER_FAILED'
  | 'AGENT_WARNING'
  | 'PIPELINE_FAILED'
  | 'SERVER_RESTARTED'
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
  | 'question_completed'
  | 'warning'
  | 'done'
  | 'error'

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

export interface QuestionCompletedData {
  seq: number
  question_type: QuestionType
  completed: number
  total: number
}

export interface WarningData {
  code: 'PARSE_FAILED' | 'AGENT_WARNING' | 'RENDER_FAILED'
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
  | QuestionCompletedData
  | WarningData
  | DoneData
  | ErrorData

export interface JobEvent {
  id?: number
  event: JobEventType
  data: JobEventData
}
