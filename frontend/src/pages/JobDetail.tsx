import { useCallback, useEffect, useRef, useState } from 'react'
import axios from 'axios'
import { useParams } from 'react-router-dom'
import { AlertCircle, Download, FileText, History, PenLine, Terminal } from 'lucide-react'
import {
  getJob,
  cancelJob,
  fetchPaperMd,
  createRevision,
  listRevisions,
  resolveArtifactUrl,
  downloadArtifact,
} from '@/api/jobs'
import { useJobStream } from '@/hooks/useJobStream'
import { useTextSelection } from '@/hooks/useTextSelection'
import { extractApiError } from '@/api/client'
import { JOB_STATUS_META, errorMessage } from '@/lib/constants'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardIcon, CardTitle } from '@/components/ui/card'
import { MarkdownContent } from '@/components/MarkdownContent'
import { PaperRevisionBox } from '@/components/PaperRevisionBox'
import { Skeleton } from '@/components/ui/skeleton'
import { Spinner } from '@/components/ui/spinner'
import type { Job, JobEventData, JobEventType, RevisionItem } from '@/api/types'

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

/** 已进入出题、可预览试卷的状态。 */
const PAPER_VISIBLE_STATUSES = ['generating', 'rendering', 'completed', 'partially_completed']

/** 可提交划选修订的状态。 */
const REVISIONABLE_STATUSES = ['completed', 'partially_completed']

/** 修订会话历史的状态徽标。 */
const REVISION_STATUS_META: Record<string, { label: string; variant: 'default' | 'success' | 'danger' }> = {
  running: { label: '进行中', variant: 'default' },
  done: { label: '已完成', variant: 'success' },
  failed: { label: '失败', variant: 'danger' },
}

interface LogEntry {
  time: string
  text: string
  level?: 'warn' | 'error'
}

function nowTime() {
  return new Date().toLocaleTimeString('zh-CN', { hour12: false })
}

function formatDateTime(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString('zh-CN', { hour12: false, month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })
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
  const [paper, setPaper] = useState<string | null>(null)
  const [revisions, setRevisions] = useState<RevisionItem[]>([])
  const [revisionRunning, setRevisionRunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [logs, setLogs] = useState<LogEntry[]>([])
  // 出题进度的单条日志：进度推进时原地更新，不追加（追加会在长任务里刷屏）
  const [progressLine, setProgressLine] = useState<string | null>(null)
  // 任务删除（404）或进入终态后停止轮询
  const [pollingStopped, setPollingStopped] = useState(false)
  const inFlightRef = useRef(false)
  const paperRef = useRef<HTMLDivElement | null>(null)

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

  /** 拉取整卷 Markdown（任务进行中即为 agent 当前稿）。 */
  const loadPaper = useCallback(async (id: string) => {
    try {
      const text = await fetchPaperMd(id)
      setPaper(text)
    } catch {
      setPaper(null) // 试卷尚未开始书写
    }
  }, [])

  /** 拉取修订会话历史；据此维护"修订进行中"状态。 */
  const loadRevisions = useCallback(async (id: string) => {
    try {
      const res = await listRevisions(id)
      setRevisions(res.revisions)
      setRevisionRunning(res.revisions.some((r) => r.status === 'running'))
    } catch {
      // 历史拉取失败不打断页面
    }
  }, [])

  // 初始快照。
  useEffect(() => {
    if (!jobId) return
    void load(jobId)
  }, [jobId, load])

  // 试卷预览：进入可见状态即拉取（渐进预览 agent 正在书写的当前稿）。
  useEffect(() => {
    if (!jobId || !job) return
    if (PAPER_VISIBLE_STATUSES.includes(job.status)) {
      void loadPaper(jobId)
    }
  }, [jobId, job?.status, loadPaper])

  // 任务进入可修订状态后拉取会话历史。
  useEffect(() => {
    if (!jobId || !job) return
    if (REVISIONABLE_STATUSES.includes(job.status)) {
      void loadRevisions(jobId)
    }
  }, [jobId, job?.status, loadRevisions])

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
        case 'progress': {
          const d = data as Extract<JobEventData, { completed: number; total: number }>
          setJob((prev) =>
            prev
              ? { ...prev, progress: { ...prev.progress, completed: d.completed, total: d.total } }
              : prev,
          )
          setProgressLine(`已完成 ${d.completed}/${d.total} 题`)
          break
        }
        case 'revision_started': {
          const d = data as Extract<JobEventData, { round_no: number }>
          setRevisionRunning(true)
          setLogs((prev) => [
            ...prev,
            { time: nowTime(), text: `第 ${d.round_no} 轮修订开始，agent 正在修改试卷` },
          ])
          if (jobId) void loadRevisions(jobId)
          break
        }
        case 'revision_done': {
          const d = data as Extract<JobEventData, { round_no: number }>
          setRevisionRunning(false)
          setLogs((prev) => [...prev, { time: nowTime(), text: `第 ${d.round_no} 轮修订完成` }])
          if (jobId) {
            void loadPaper(jobId)
            void loadRevisions(jobId)
          }
          break
        }
        case 'revision_failed': {
          const d = data as Extract<JobEventData, { round_no: number; message?: string }>
          setRevisionRunning(false)
          setLogs((prev) => [
            ...prev,
            {
              time: nowTime(),
              text: `第 ${d.round_no} 轮修订失败：${d.message ?? '未知原因'}`,
              level: 'error',
            },
          ])
          if (jobId) void loadRevisions(jobId)
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
    [jobId, load, loadPaper, loadRevisions],
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

  // 修订进行中：轮询会话状态兜底（SSE 在 done 后已关闭，修订事件不一定可达）；
  // 结束时（true→false）刷新试卷。
  const wasRevisionRunning = useRef(false)
  useEffect(() => {
    if (revisionRunning) wasRevisionRunning.current = true
  }, [revisionRunning])
  useEffect(() => {
    if (!jobId) return
    if (revisionRunning) {
      const timer = window.setInterval(() => {
        void loadRevisions(jobId)
      }, 4000)
      return () => window.clearInterval(timer)
    }
    if (wasRevisionRunning.current) {
      wasRevisionRunning.current = false
      void loadPaper(jobId)
      void loadRevisions(jobId)
    }
  }, [jobId, revisionRunning, loadRevisions, loadPaper])

  const revisionable = job !== null && REVISIONABLE_STATUSES.includes(job.status)
  const { selectionBox, clearSelection } = useTextSelection(paperRef, revisionable && !revisionRunning)

  async function handleSubmitFeedback(feedback: string) {
    if (!jobId || !selectionBox) return
    try {
      await createRevision(jobId, { selection: selectionBox.anchor, feedback })
      clearSelection()
      setRevisionRunning(true)
      setLogs((prev) => [...prev, { time: nowTime(), text: '修订反馈已提交' }])
      void loadRevisions(jobId)
    } catch (err) {
      const apiErr = extractApiError(err)
      setError(apiErr ? errorMessage(apiErr.error_code) : '提交修订失败')
    }
  }

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
  const paperVisible = PAPER_VISIBLE_STATUSES.includes(job.status)

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

      {/* 整卷预览 + 划选修订 */}
      {paperVisible && (
        <Card>
          <CardHeader className="flex-wrap">
            <CardIcon>
              <FileText />
            </CardIcon>
            <CardTitle>试卷预览</CardTitle>
            {revisionRunning ? (
              <span className="inline-flex items-center gap-2 text-[13px] text-slate-500">
                <Spinner className="h-3.5 w-3.5" />
                修订进行中，试卷更新后自动刷新…
              </span>
            ) : revisionable ? (
              <span className="inline-flex items-center gap-1.5 text-[13px] text-slate-500">
                <PenLine className="h-3.5 w-3.5" />
                划选试卷中的任意内容，即可提交修订
              </span>
            ) : (
              <span className="text-[13px] text-slate-400">任务完成后可划选内容提交修订</span>
            )}
          </CardHeader>
          <CardContent>
            {paper === null ? (
              <div className="space-y-3">
                {Array.from({ length: 6 }).map((_, i) => (
                  <Skeleton key={i} className="h-4" style={{ width: `${60 + ((i * 13) % 40)}%` }} />
                ))}
              </div>
            ) : (
              <div ref={paperRef} className="select-text">
                <MarkdownContent content={paper} className="text-[13.5px] leading-[1.8] text-slate-800" />
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {/* 划选反馈框（所选位置下方，可关闭） */}
      {selectionBox && (
        <PaperRevisionBox
          anchor={selectionBox.anchor}
          rect={selectionBox.rect}
          running={revisionRunning}
          onClose={clearSelection}
          onSubmit={handleSubmitFeedback}
        />
      )}

      {/* 修订会话历史 */}
      {revisions.length > 0 && (
        <Card>
          <CardHeader>
            <CardIcon>
              <History />
            </CardIcon>
            <CardTitle>修订历史（{revisions.length}）</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2.5">
            {revisions.map((r) => {
              const meta = REVISION_STATUS_META[r.status] ?? REVISION_STATUS_META.running
              return (
                <div
                  key={r.revision_id}
                  className="rounded-xl border border-slate-200 px-3.5 py-2.5"
                >
                  <div className="flex flex-wrap items-center gap-2.5">
                    <span className="text-[13px] font-semibold text-slate-900">
                      第 {r.round_no} 轮
                    </span>
                    <Badge variant={meta.variant} pulse={r.status === 'running'}>
                      {meta.label}
                    </Badge>
                    <span className="ml-auto text-xs tabular-nums text-slate-400">
                      {formatDateTime(r.created_at)}
                    </span>
                  </div>
                  {r.selection?.text && (
                    <p className="mt-1.5 line-clamp-1 text-[12.5px] text-slate-400">
                      选中：「{r.selection.text}」
                    </p>
                  )}
                  <p className="mt-1 text-[13px] leading-[1.6] text-slate-700">{r.feedback}</p>
                  {r.status === 'failed' && r.error && (
                    <p className="mt-1 text-[12.5px] leading-[1.6] text-red-600">{r.error}</p>
                  )}
                  {r.status === 'done' && r.summary && (
                    <p className="mt-1 text-[12.5px] text-slate-400">{r.summary}</p>
                  )}
                </div>
              )
            })}
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

      {/* 兜底错误提示（下载失败 / 修订提交失败等） */}
      {error && job && (
        <div className="flex items-start gap-2.5 rounded-[10px] border border-red-200 bg-red-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-none text-red-500" />
          <span>{error}</span>
        </div>
      )}
    </div>
  )
}
