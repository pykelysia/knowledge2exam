import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { useParams } from 'react-router-dom'
import { AlertCircle, Check, Download, PenLine, Terminal, BookOpen } from 'lucide-react'
import { getJob, cancelJob, listQuestions, resolveArtifactUrl, downloadArtifact } from '@/api/jobs'
import { useJobStream } from '@/hooks/useJobStream'
import { extractApiError } from '@/api/client'
import { JOB_STATUS_META, errorMessage } from '@/lib/constants'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardIcon, CardTitle } from '@/components/ui/card'
import { MarkdownContent } from '@/components/MarkdownContent'
import { Skeleton } from '@/components/ui/skeleton'
import type { Job, JobEventData, JobEventType, Question } from '@/api/types'

const STAGE_LABEL: Record<string, string> = {
  preprocessing: '预处理',
  // planning / reviewing 为历史任务的 SSE 回放兼容标签（旧 job_stage 行仍含这些值）
  planning: '规划',
  generating: '出题',
  reviewing: '审查',
  rendering: '渲染',
}

/** 可取消任务的运行态。 */
const CANCELLABLE_STATUSES = ['pending', 'preprocessing', 'generating', 'rendering']

/** 已进入出题、可列出题目的状态。 */
const QUESTION_VISIBLE_STATUSES = ['generating', 'rendering', 'completed', 'partially_completed']

interface LogEntry {
  time: string
  text: string
  level?: 'warn' | 'error'
}

function nowTime() {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

/** 答案块：emerald 淡底 + 图标标签。 */
function AnswerBlock({ content }: { content: string }) {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-emerald-200 bg-emerald-50 px-3 py-2 text-[13px] leading-[1.7] text-emerald-800">
      <span className="mt-[2px] inline-flex flex-none items-center gap-1 text-xs font-semibold text-emerald-700">
        <Check className="h-3.5 w-3.5" />
        答案
      </span>
      <MarkdownContent content={content} className="min-w-0 flex-1" />
    </div>
  )
}

/** 解析块：slate 淡底 + 图标标签。 */
function ExplainBlock({ content }: { content: string }) {
  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-[13px] leading-[1.7] text-slate-600">
      <span className="mt-[2px] inline-flex flex-none items-center gap-1 text-xs font-semibold">
        <PenLine className="h-3.5 w-3.5" />
        解析
      </span>
      <MarkdownContent content={content} className="min-w-0 flex-1" />
    </div>
  )
}

/** SSE 连接状态徽标：open 显示绿色"已连接"，其余显示灰色状态。 */
function ConnChip({ state }: { state: 'idle' | 'connecting' | 'open' | 'closed' }) {
  if (state === 'open') {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-emerald-200 bg-emerald-50 px-2.5 py-0.5 text-xs text-emerald-700">
        <span className="h-1.5 w-1.5 flex-none animate-dot-pulse-green rounded-full bg-emerald-500" />
        已连接
      </span>
    )
  }
  const label =
    state === 'connecting' ? '连接中' : state === 'closed' ? '连接中断' : '未连接'
  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-200 bg-slate-50 px-2.5 py-0.5 text-xs text-slate-500">
      <span className="h-1.5 w-1.5 flex-none rounded-full bg-slate-400" />
      {label}
    </span>
  )
}

export function JobDetail() {
  const { jobId } = useParams<{ jobId: string }>()
  const [job, setJob] = useState<Job | null>(null)
  const [questions, setQuestions] = useState<Question[]>([])
  const [error, setError] = useState<string | null>(null)
  const [logs, setLogs] = useState<LogEntry[]>([])
  // 出题进度的单条日志：每完成一题原地更新，不追加（追加会在长任务里刷屏）
  const [progressLine, setProgressLine] = useState<string | null>(null)
  // 任务删除（404）或进入终态后停止轮询
  const [pollingStopped, setPollingStopped] = useState(false)
  const inFlightRef = useRef(false)

  const TERMINAL_STATUSES = ['completed', 'partially_completed', 'failed', 'cancelled']

  /** 拉取任务快照；返回 true 表示任务已不存在（404）。 */
  const load = useCallback(async (id: string): Promise<boolean> => {
    if (inFlightRef.current) return false // 并发去重：丢弃重复触发
    inFlightRef.current = true
    try {
      const snap = await getJob(id)
      setJob(snap)
      setError(null)
      if (TERMINAL_STATUSES.includes(snap.status)) {
        setPollingStopped(true)
      }
      return false
    } catch (err) {
      if (axios.isAxiosError(err) && err.response?.status === 404) {
        setError('任务不存在或已被删除')
        setPollingStopped(true)
        return true
      }
      const apiErr = extractApiError(err)
      setError(apiErr ? errorMessage(apiErr.error_code) : '加载任务失败')
      return false
    } finally {
      inFlightRef.current = false
    }
  }, [])

  // 初始快照。
  useEffect(() => {
    if (!jobId) return
    void load(jobId)
  }, [jobId, load])

  const handleEvent = useCallback(
    (type: JobEventType, data: JobEventData) => {
      switch (type) {
        case 'stage_changed': {
          const d = data as Extract<JobEventData, { stage: string }>
          setLogs((prev) => [
            ...prev,
            { time: nowTime(), text: `阶段切换：${STAGE_LABEL[d.stage] ?? d.stage}` },
          ])
          break
        }
        case 'plan_ready': {
          const d = data as Extract<JobEventData, { total: number }>
          setLogs((prev) => [...prev, { time: nowTime(), text: `规划完成：共 ${d.total} 题` }])
          break
        }
        case 'question_completed': {
          const d = data as Extract<JobEventData, { completed: number; total: number }>
          // 进度条同样从事件流实时更新（SSE 打开期间轮询已停止，快照要等 done 才刷新）
          setJob((prev) =>
            prev
              ? { ...prev, progress: { ...prev.progress, completed: d.completed, total: d.total } }
              : prev,
          )
          setProgressLine(`完成第 ${d.seq} 题（${d.completed}/${d.total}）`)
          break
        }
        case 'warning': {
          const d = data as Extract<JobEventData, { message: string }>
          setLogs((prev) => [...prev, { time: nowTime(), text: d.message, level: 'warn' }])
          break
        }
        case 'done': {
          const d = data as Extract<JobEventData, { status: string }>
          setLogs((prev) => [...prev, { time: nowTime(), text: `任务结束：${d.status}` }])
          // done 事件不含完整快照（产物 URL 等），拉一次终态快照
          if (jobId) void load(jobId)
          break
        }
        case 'error': {
          const d = data as Extract<JobEventData, { message: string }>
          setLogs((prev) => [...prev, { time: nowTime(), text: d.message, level: 'error' }])
          break
        }
      }
    },
    [jobId, load],
  )

  const { connectionState } = useJobStream({
    jobId: jobId ?? null,
    onEvent: handleEvent,
  })

  // SSE 已打开时以事件流为准（done 事件会触发快照刷新）；
  // 未连接或连接异常时降级为 5s 轮询，404/终态后停止。
  useEffect(() => {
    if (!jobId || pollingStopped || connectionState === 'open') return
    const timer = window.setInterval(() => {
      void load(jobId)
    }, 5000)
    return () => window.clearInterval(timer)
  }, [jobId, connectionState, pollingStopped, load])
  useEffect(() => {
    if (!jobId || !job) return
    if (QUESTION_VISIBLE_STATUSES.includes(job.status)) {
      listQuestions(jobId)
        .then((res) => setQuestions(res.questions))
        .catch(() => {})
    }
  }, [jobId, job?.status])

  if (error && !job) {
    return (
      <div className="rounded-[10px] border border-red-200 bg-red-50 p-4 text-red-700">{error}</div>
    )
  }

  if (!job) {
    return (
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <div>
            <Skeleton className="h-8 w-32" />
            <Skeleton className="mt-2 h-4 w-48" />
          </div>
          <Skeleton className="h-8 w-20" />
        </div>
        <Card>
          <CardContent className="pt-5">
            <Skeleton className="h-2 w-full" />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <Skeleton className="h-6 w-24" />
          </CardHeader>
          <CardContent>
            <Skeleton className="h-20 w-full" />
          </CardContent>
        </Card>
      </div>
    )
  }

  const statusMeta = JOB_STATUS_META[job.status]
  const progress = job.progress
  const pct =
    progress && progress.total ? Math.round((progress.completed! / progress.total) * 100) : 0
  const running = CANCELLABLE_STATUSES.includes(job.status)

  async function handleCancel() {
    if (!jobId) return
    try {
      await cancelJob(jobId)
      // 取消后拉一次终态快照（轮询在进入终态后自动停止）
      void load(jobId)
      setLogs((prev) => [...prev, { time: nowTime(), text: '已发出取消请求' }])
    } catch (err) {
      const apiErr = extractApiError(err)
      setError(apiErr ? errorMessage(apiErr.error_code) : '取消失败')
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-[-0.02em] text-slate-900">任务详情</h1>
          <p className="mt-2">
            <span className="inline-block max-w-[340px] truncate rounded-md bg-slate-100 px-2.5 py-0.5 align-bottom font-mono text-xs text-slate-600">
              {jobId}
            </span>
          </p>
        </div>
        <div className="flex items-center gap-2.5">
          <Badge variant={statusMeta.variant} pulse={running && job.status !== 'pending'}>
            {statusMeta.label}
          </Badge>
          {running && (
            <Button variant="secondary" size="sm" onClick={handleCancel}>
              取消
            </Button>
          )}
        </div>
      </div>

      {/* 进度 */}
      {progress && (
        <Card>
          <CardContent className="pb-[18px] pt-4">
            <div className="mb-2.5 flex items-center justify-between gap-3">
              <span className="text-[13px] text-slate-600">
                出题进度：
                <b className="font-semibold tabular-nums text-slate-900">
                  {progress.completed ?? 0} / {progress.total ?? 0}
                </b>
              </span>
              <div className="flex items-center gap-2.5">
                <ConnChip state={connectionState} />
                <span className="text-[13px] font-semibold tabular-nums text-slate-900">{pct}%</span>
              </div>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100 shadow-[inset_0_1px_2px_rgb(15_23_42/0.06)]">
              <div
                className="relative h-full overflow-hidden rounded-full bg-gradient-to-r from-slate-800 to-slate-900 transition-[width] duration-500"
                style={{ width: `${pct}%` }}
              >
                {running && (
                  <div className="absolute inset-0 animate-sweep bg-[linear-gradient(90deg,transparent,rgb(255_255_255/0.38),transparent)]" />
                )}
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* 产物下载 */}
      {job.artifacts && (job.artifacts.md_url || job.artifacts.pdf_url) && (
        <Card>
          <CardHeader>
            <CardIcon>
              <Download />
            </CardIcon>
            <CardTitle>下载产物</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="flex gap-3">
              {job.artifacts.md_url && (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => {
                    const url = resolveArtifactUrl(job.artifacts!.md_url!)!
                    downloadArtifact(url, 'paper.md').catch((err) => {
                      const apiErr = extractApiError(err)
                      setError(apiErr ? errorMessage(apiErr.error_code) : '下载 Markdown 失败')
                    })
                  }}
                >
                  <Download className="h-3.5 w-3.5" />
                  下载 Markdown
                </Button>
              )}
              {job.artifacts.pdf_url && (
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => {
                    const url = resolveArtifactUrl(job.artifacts!.pdf_url!)!
                    downloadArtifact(url, 'paper.pdf').catch((err) => {
                      const apiErr = extractApiError(err)
                      setError(apiErr ? errorMessage(apiErr.error_code) : '下载 PDF 失败')
                    })
                  }}
                >
                  <Download className="h-3.5 w-3.5" />
                  下载 PDF
                </Button>
              )}
              {!job.artifacts.pdf_url && job.status === 'partially_completed' && (
                <span className="text-sm text-slate-500">PDF 渲染失败，可下载 md 作为替代</span>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* 事件日志 */}
      <Card>
        <CardHeader>
          <CardIcon>
            <Terminal />
          </CardIcon>
          <CardTitle>进度日志</CardTitle>
        </CardHeader>
        <CardContent>
          {progressLine === null && logs.length === 0 ? (
            <p className="text-sm text-slate-400">暂无事件，等待任务开始…</p>
          ) : (
            <div className="max-h-80 overflow-y-auto rounded-[10px] border border-slate-200 bg-slate-50 p-3 font-mono text-[12.5px] leading-[1.8] text-slate-600">
              {progressLine && (
                <div className="mb-1.5 flex items-center gap-2 border-b border-dashed border-slate-200 pb-1.5 font-semibold text-slate-900">
                  {running && (
                    <span className="h-[7px] w-[7px] flex-none animate-dot-pulse-green rounded-full bg-emerald-500" />
                  )}
                  <span>{progressLine}</span>
                </div>
              )}
              <ul className="space-y-0.5">
                {logs.map((l, i) => (
                  <li key={i} className="flex gap-2.5">
                    <span className="flex-none tabular-nums text-slate-400">{l.time}</span>
                    <span
                      className={
                        l.level === 'warn'
                          ? 'text-amber-700'
                          : l.level === 'error'
                            ? 'text-red-600'
                            : undefined
                      }
                    >
                      {l.text}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 失败提示 */}
      {job.status === 'failed' && job.error_code && (
        <div className="flex items-start gap-2.5 rounded-[10px] border border-red-200 bg-red-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-none text-red-500" />
          <span>{errorMessage(job.error_code)}</span>
        </div>
      )}

      {/* 已产出题目 */}
      {QUESTION_VISIBLE_STATUSES.includes(job.status) && (
        <Card>
          <CardHeader>
            <CardIcon>
              <BookOpen />
            </CardIcon>
            <CardTitle>已产出的题目（{questions.length}）</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3.5">
            {questions.length === 0 ? (
              <div className="space-y-3">
                {Array.from({ length: 3 }).map((_, i) => (
                  <div key={i} className="space-y-2">
                    <div className="flex items-center gap-2">
                      <Skeleton className="h-5 w-8" />
                      <Skeleton className="h-5 w-12" />
                    </div>
                    <Skeleton className="h-4 w-full" />
                    <Skeleton className="h-4 w-2/3" />
                  </div>
                ))}
              </div>
            ) : (
              questions.map((q) => (
                <div
                  key={q.seq}
                  className="space-y-3 rounded-xl border border-slate-200 p-4 transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-px hover:border-slate-300 hover:shadow-card-hover"
                >
                  <div className="flex items-center gap-2.5">
                    <span className="grid h-[26px] w-[26px] flex-none place-items-center rounded-full bg-slate-900 text-[12.5px] font-semibold tabular-nums text-white shadow-btn">
                      {q.seq}
                    </span>
                    <Badge>
                      {q.question_type === 'choice'
                        ? '选择题'
                        : q.question_type === 'blank'
                          ? '填空题'
                          : '简答题'}
                    </Badge>
                  </div>
                  <MarkdownContent content={q.stem} className="text-[13.5px] leading-[1.75] text-slate-800" />
                  {q.options && (
                    <ul className="space-y-1">
                      {Object.entries(q.options).map(([k, v]) => (
                        <li
                          key={k}
                          className="flex items-start gap-2.5 rounded-lg px-2 py-1 text-[13.5px] leading-[1.6] text-slate-700 transition-colors hover:bg-slate-50"
                        >
                          <span className="mt-[3px] grid h-[22px] w-[22px] flex-none place-items-center rounded-[7px] bg-slate-100 text-[11.5px] font-semibold text-slate-700">
                            {k}
                          </span>
                          <MarkdownContent content={v} className="min-w-0 flex-1" />
                        </li>
                      ))}
                    </ul>
                  )}
                  {q.answer && <AnswerBlock content={q.answer} />}
                  {q.explanation && <ExplainBlock content={q.explanation} />}
                  {q.sub_questions && q.sub_questions.length > 0 && (
                    <div className="space-y-2.5">
                      {q.sub_questions.map((sq, i) => {
                        const subAnswer = q.sub_answers?.[i]
                        return (
                          <div key={i} className="space-y-2">
                            <div className="flex gap-1.5 text-[13.5px] leading-[1.7] text-slate-700">
                              <span className="flex-none font-semibold text-slate-800">
                                ({i + 1})
                              </span>
                              <MarkdownContent content={sq} className="min-w-0 flex-1" />
                            </div>
                            {subAnswer && <AnswerBlock content={subAnswer} />}
                          </div>
                        )
                      })}
                    </div>
                  )}
                </div>
              ))
            )}
          </CardContent>
        </Card>
      )}

      {/* 兜底错误提示（下载失败 / 取消失败等） */}
      {error && job && (
        <div className="flex items-start gap-2.5 rounded-[10px] border border-red-200 bg-red-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-none text-red-500" />
          <span>{error}</span>
        </div>
      )}
    </div>
  )
}
