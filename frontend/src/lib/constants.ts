import type { ErrorCode, JobStatus, SourceType } from '@/api/types'

/** 内容类型中文标签与是否共享的元信息。 */
export const SOURCE_TYPE_META: Record<
  SourceType,
  { label: string; fileOnly: boolean; textOnly: boolean }
> = {
  book: { label: '书籍资料', fileOnly: true, textOnly: false },
  lecture: { label: '教师授课用件', fileOnly: true, textOnly: false },
  note: { label: '学习笔记', fileOnly: true, textOnly: false },
  keypoint_list: { label: '重点知识点清单', fileOnly: true, textOnly: false },
  past_paper: { label: '往期试卷', fileOnly: true, textOnly: false },
  manual_text: { label: '手动输入文本', fileOnly: false, textOnly: true },
  extra_requirement: { label: '额外要求', fileOnly: false, textOnly: true },
}

/** 文件类 source_type（上传 file 分支可用）。 */
export const FILE_SOURCE_TYPES = (
  Object.keys(SOURCE_TYPE_META) as SourceType[]
).filter((t) => SOURCE_TYPE_META[t].fileOnly)

/** 允许上传的文件扩展名（与后端 SUPPORTED_EXTENSIONS 保持一致，不含 doc/ppt 旧格式）。 */
export const ALLOWED_EXTENSIONS = [
  'docx',
  'pptx',
  'pdf',
  'md',
  'txt',
  'jpg',
  'jpeg',
  'png',
  'webp',
  'bmp',
]

/** 任务状态中文标签。 */
export const JOB_STATUS_META: Record<JobStatus, { label: string; variant: 'default' | 'success' | 'warning' | 'danger' }> = {
  pending: { label: '排队中', variant: 'default' },
  preprocessing: { label: '预处理', variant: 'default' },
  generating: { label: '出题中', variant: 'default' },
  rendering: { label: '渲染中', variant: 'default' },
  completed: { label: '已完成', variant: 'success' },
  partially_completed: { label: '部分完成', variant: 'warning' },
  failed: { label: '失败', variant: 'danger' },
  cancelled: { label: '已取消', variant: 'default' },
}

/** 错误码中文提示。 */
export const ERROR_MESSAGES: Record<string, string> = {
  INPUT_EMPTY: '请至少提供一项输入（文件或文本）',
  UNSUPPORTED_FORMAT: '不支持的文件格式',
  FILE_TOO_LARGE: '文件超出大小上限，建议拆分',
  PDF_ENCRYPTED: '加密 PDF 无法解析，请先移除密码',
  SHARE_NOT_ALLOWED: '该内容类型不支持共享',
  SHARE_SCOPE_REQUIRED: '选择共享需指定学校与课程',
  INVALID_DURATION: '考试时长需在 5~300 分钟之间',
  JOB_NOT_FOUND: '任务不存在或无权访问',
  SCHOOL_NOT_FOUND: '学校不存在或已被移除',
  UPLOAD_IN_USE: '该上传件已被任务引用，不可删除',
  JOB_NOT_READY: '产物尚未生成，请稍后再试',
  JOB_ALREADY_FINISHED: '任务已终结，无法取消',
  JOB_NOT_FINISHED: '任务尚未完成，完成后才能修订',
  REVISION_IN_PROGRESS: '已有修订正在进行，请等待完成后再提交',
  PAPER_NOT_READY: '试卷尚未生成，无法修订',
  PARSE_FAILED: '资料解析失败，对应内容将被跳过',
  MODERATION_REJECTED: '内容安全审核未通过',
  MODEL_UNAVAILABLE: '模型服务不可用，请稍后重试',
  RATE_LIMITED: '请求过于频繁，请稍后重试',
  USER_EXISTS: '邮箱或用户名已被注册',
  INVALID_CREDENTIALS: '登录凭证错误',
  TOKEN_MISSING: '未登录，请重新登录',
  TOKEN_EXPIRED: '登录已过期，正在自动续期',
  REFRESH_INVALID: '登录状态已失效，请重新登录',
  REFRESH_REUSE_DETECTED: '检测到令牌重放，已吊销全部会话，请重新登录',
  PLANNING_FAILED: '规划阶段失败，建议补充资料后重试',
  GENERATION_EXHAUSTED: '题目生成耗尽重试，建议减少时长或补充资料',
  RENDER_FAILED: 'PDF 渲染失败，可下载 md 作为替代',
  AGENT_WARNING: '生成过程告警，请留意任务日志',
  PIPELINE_FAILED: '任务执行失败，请查看任务日志或稍后重试',
  SERVER_RESTARTED: '服务重启导致任务中断，请重新创建任务',
  OCR_DEGRADED: '部分页面文本层损坏，已尝试视觉识别，个别页面内容可能缺失',
}

/** 将任意错误码映射为可展示的中文提示。 */
export function errorMessage(code: ErrorCode | string | undefined): string {
  if (!code) return '请求失败，请稍后重试'
  return ERROR_MESSAGES[code] ?? '请求失败，请稍后重试'
}

/** 从文件名判断扩展名是否在允许列表内（小写、去点）。 */
export function isAllowedExtension(filename: string): boolean {
  const ext = filename.split('.').pop()?.toLowerCase() ?? ''
  return ALLOWED_EXTENSIONS.includes(ext)
}
