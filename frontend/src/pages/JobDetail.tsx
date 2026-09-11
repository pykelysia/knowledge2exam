import { useCallback, useEffect, useRef, useState } from 'react'
import { useParams } from 'react-router-dom'
import { getJob, cancelJob, listQuestions, resolveArtifactUrl, downloadArtifact } from '@/api/jobs'
import { useJobStream } from '@/hooks/useJobStream'
import { extractApiError } from '@/api/client'
import { JOB_STATUS_META, errorMessage } from '@/lib/constants'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
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

export function JobDetail() {
  const { jobId } = useParams<{ jobId: string }>()
  const [job, setJob] = useState<Job | null>(null)
  const [questions, setQuestions] = useState<Question[]>([])
  const [error, setError] = useState<string | null>(null)
  const [logs, setLogs] = useState<string[]>([])
  const timerRef = useRef<number | null>(null)

  // 轮询降级 + 初始快照。
  useEffect(() => {
    if (!jobId) return
    const id = jobId
    let cancelled = false
    async function load() {
      try {
        const snap = await getJob(id)
        if (!cancelled) setJob(snap)
      } catch (err) {
        if (!cancelled) {
          const apiErr = extractApiError(err)
          setError(apiErr ? errorMessage(apiErr.error_code) : '加载任务失败')
        }
      }
    }
    load()
    timerRef.current = window.setInterval(load, 5000)
    return () => {
      cancelled = true
      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
  }, [jobId])

  const handleEvent = useCallback(
    (type: JobEventType, data: JobEventData) => {
      switch (type) {
        case 'stage_changed': {
          const d = data as Extract<JobEventData, { stage: string }>
          setLogs((prev) => [...prev, `阶段切换：${STAGE_LABEL[d.stage] ?? d.stage}`])
          break
        }
        case 'plan_ready': {
          const d = data as Extract<JobEventData, { total: number }>
          setLogs((prev) => [...prev, `规划完成：共 ${d.total} 题`])
          break
        }
        case 'question_completed': {
          const d = data as Extract<JobEventData, { completed: number; total: number }>
          setLogs((prev) => [...prev, `完成第 ${d.seq} 题（${d.completed}/${d.total}）`])
          break
        }
        case 'question_retried': {
          const d = data as Extract<JobEventData, { seq: number; attempt: number }>
          setLogs((prev) => [...prev, `第 ${d.seq} 题重试（第 ${d.attempt} 次）`])
          break
        }
        case 'question_replanned': {
          const d = data as Extract<JobEventData, { seq: number }>
          setLogs((prev) => [...prev, `第 ${d.seq} 题已更换考察方向`])
          break
        }
        case 'question_abandoned': {
          const d = data as Extract<JobEventData, { seq: number }>
          setLogs((prev) => [...prev, `第 ${d.seq} 题已放弃`])
          break
        }
        case 'warning': {
          const d = data as Extract<JobEventData, { message: string }>
          setLogs((prev) => [...prev, `⚠ ${d.message}`])
          break
        }
        case 'done': {
          const d = data as Extract<JobEventData, { status: string }>
          setLogs((prev) => [...prev, `任务结束：${d.status}`])
          break
        }
        case 'error': {
          const d = data as Extract<JobEventData, { message: string }>
          setLogs((prev) => [...prev, `✗ ${d.message}`])
          break
        }
      }
    },
    [],
  )

  const { connectionState } = useJobStream({
    jobId: jobId ?? null,
    onEvent: handleEvent,
  })

  // 任务进入终态后停止轮询。
  useEffect(() => {
    if (!job || !jobId) return
    const terminalStatuses = ['completed', 'partially_completed', 'failed', 'cancelled']
    if (terminalStatuses.includes(job.status) && timerRef.current) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [job?.status, jobId, job])
  useEffect(() => {
    if (!jobId || !job) return
    if (QUESTION_VISIBLE_STATUSES.includes(job.status)) {
      listQuestions(jobId)
        .then((res) => setQuestions(res.questions))
        .catch(() => {})
    }
  }, [jobId, job?.status])

  if (error && !job) {
    return <div className="rounded-md border border-red-200 bg-red-50 p-4 text-red-700">{error}</div>
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

  async function handleCancel() {
    if (!jobId) return
    try {
      await cancelJob(jobId)
      // 取消后立即停止轮询，避免继续请求已取消任务。
      if (timerRef.current) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
      setLogs((prev) => [...prev, '已发出取消请求'])
    } catch (err) {
      const apiErr = extractApiError(err)
      setError(apiErr ? errorMessage(apiErr.error_code) : '取消失败')
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">任务详情</h1>
          <p className="mt-1 text-sm text-slate-500">{jobId}</p>
        </div>
        <div className="flex items-center gap-3">
          <Badge variant={statusMeta.variant}>{statusMeta.label}</Badge>
          {CANCELLABLE_STATUSES.includes(job.status) && (
            <Button variant="secondary" size="sm" onClick={handleCancel}>
              取消
            </Button>
          )}
        </div>
      </div>

      {/* 进度 */}
      {progress && (
        <Card>
          <CardContent className="pt-5">
            <div className="mb-2 flex items-center justify-between text-sm">
              <span className="text-slate-600">
                出题进度：{progress.completed ?? 0} / {progress.total ?? 0}
              </span>
              <span className="text-slate-400">连接状态：{connectionState}</span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100">
              <div
                className="h-full rounded-full bg-slate-900 transition-all"
                style={{ width: `${pct}%` }}
              />
            </div>
          </CardContent>
        </Card>
      )}

      {/* 产物下载 */}
      {job.artifacts && (job.artifacts.md_url || job.artifacts.pdf_url) && (
        <Card>
          <CardHeader>
            <CardTitle>下载产物</CardTitle>
          </CardHeader>
          <CardContent className="flex gap-3">
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
                下载 PDF
              </Button>
            )}
            {!job.artifacts.pdf_url && job.status === 'partially_completed' && (
              <span className="text-sm text-slate-500">PDF 渲染失败，可下载 md 作为替代</span>
            )}
          </CardContent>
        </Card>
      )}

      {/* 事件日志 */}
      <Card>
        <CardHeader>
          <CardTitle>进度日志</CardTitle>
        </CardHeader>
        <CardContent>
          {logs.length === 0 ? (
            <p className="text-sm text-slate-400">暂无事件，等待任务开始…</p>
          ) : (
            <ul className="max-h-80 space-y-1 overflow-y-auto text-sm text-slate-600">
              {logs.map((l, i) => (
                <li key={i}>{l}</li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* 失败提示 */}
      {job.status === 'failed' && job.error_code && (
        <div className="rounded-md border border-red-200 bg-red-50 p-4 text-red-700">
          {errorMessage(job.error_code)}
        </div>
      )}

      {/* 已产出题目 */}
      {QUESTION_VISIBLE_STATUSES.includes(job.status) && (
        <Card>
          <CardHeader>
            <CardTitle>已产出的题目（{questions.length}）</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
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
                <div key={q.seq} className="rounded-md border border-slate-200 p-4">
                  <div className="mb-2 flex items-center gap-2">
                    <Badge>{q.seq}</Badge>
                    <Badge>
                      {q.question_type === 'choice'
                        ? '选择题'
                        : q.question_type === 'blank'
                          ? '填空题'
                          : '简答题'}
                    </Badge>
                  </div>
                  <p className="text-sm text-slate-800">{q.stem}</p>
                  {q.options && (
                    <ul className="mt-2 space-y-1 text-sm text-slate-600">
                      {Object.entries(q.options).map(([k, v]) => (
                        <li key={k}>
                          {k}. {v}
                        </li>
                      ))}
                    </ul>
                  )}
                  {q.answer && (
                    <div className="mt-2 rounded-md bg-slate-50 p-2 text-sm text-slate-700">
                      <span className="font-medium">答案：</span>
                      {q.answer}
                    </div>
                  )}
                  {q.explanation && (
                    <p className="mt-2 text-sm text-slate-500">解析：{q.explanation}</p>
                  )}
                  {q.sub_questions && q.sub_questions.length > 0 && (
                    <div className="mt-2 space-y-1">
                      {q.sub_questions.map((sq, i) => (
                        <div key={i} className="text-sm text-slate-600">
                          <span className="font-medium">小题 {i + 1}：</span>
                          {sq}
                          {q.sub_answers?.[i] && (
                            <span className="ml-2 text-slate-500">
                              答案：{q.sub_answers[i]}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              ))
            )}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
