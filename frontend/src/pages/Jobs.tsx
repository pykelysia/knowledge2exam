import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { Clock, Eye, FileText, Plus, Trash2 } from 'lucide-react'
import { listJobs, deleteJob, cancelJob } from '@/api/jobs'
import { useAuth } from '@/hooks/useAuth'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { extractApiError } from '@/api/client'
import { JOB_STATUS_META, errorMessage } from '@/lib/constants'
import type { Job } from '@/api/types'

/** 这些阶段任务仍在推进，徽章状态点播放呼吸动画。 */
const PULSING_STATUSES = ['preprocessing', 'generating', 'rendering']

export function Jobs() {
  const { isAuthenticated } = useAuth()
  const [jobs, setJobs] = useState<Job[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // 任务列表以后端为唯一数据源：跨设备一致，账号切换不串数据
  const refresh = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const data = await listJobs()
      setJobs(data.jobs)
    } catch (err) {
      const apiErr = extractApiError(err)
      setError(apiErr ? errorMessage(apiErr.error_code) : '加载任务列表失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (isAuthenticated) {
      refresh()
    }
  }, [isAuthenticated, refresh])

  async function handleDelete(jobId: string) {
    try {
      await deleteJob(jobId)
      await refresh()
    } catch (err) {
      const apiErr = extractApiError(err)
      alert(apiErr ? errorMessage(apiErr.error_code) : '删除失败')
    }
  }

  async function handleCancel(jobId: string) {
    try {
      await cancelJob(jobId)
      // 取消成功后任务仍保留在列表中（状态变为 cancelled），刷新以展示真实状态
      await refresh()
    } catch (err) {
      const apiErr = extractApiError(err)
      alert(apiErr ? errorMessage(apiErr.error_code) : '取消失败')
    }
  }

  if (!isAuthenticated) {
    return (
      <div className="flex items-center justify-center py-24">
        <p className="text-slate-600">请先登录查看任务列表</p>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-[-0.02em] text-slate-900">我的任务</h1>
          <p className="mt-1.5 text-[13.5px] text-slate-500">查看和管理已创建的试卷生成任务</p>
        </div>
        <Link to="/">
          <Button>
            <Plus className="h-4 w-4" />
            新建任务
          </Button>
        </Link>
      </div>

      {error && (
        <div className="rounded-[10px] border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="space-y-2.5">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="rounded-xl border border-slate-200 p-4">
              <Skeleton className="mb-2 h-5 w-1/3" />
              <Skeleton className="h-4 w-1/2" />
            </div>
          ))}
        </div>
      ) : jobs.length === 0 ? (
        <div className="rounded-[14px] border-[1.5px] border-dashed border-slate-300 bg-white/50 px-5 py-11 text-center">
          <span className="mx-auto mb-3 grid h-[46px] w-[46px] place-items-center rounded-full bg-slate-100 text-slate-400">
            <FileText className="h-5 w-5" />
          </span>
          <p className="text-sm font-medium text-slate-700">暂无任务记录</p>
          <p className="mt-1 text-[12.5px] text-slate-400">生成的试卷任务会显示在这里</p>
          <Link to="/" className="mt-4 inline-block">
            <Button variant="secondary" size="sm">
              去生成试卷
            </Button>
          </Link>
        </div>
      ) : (
        <ul className="space-y-2.5">
          {jobs.map((job) => {
            const statusMeta = JOB_STATUS_META[job.status]
            const cancellable = ['pending', 'preprocessing', 'generating', 'rendering'].includes(
              job.status,
            )
            return (
              <li
                key={job.job_id}
                className="flex items-center justify-between gap-4 rounded-xl border border-slate-200 bg-white px-4 py-3.5 shadow-[0_1px_2px_rgb(15_23_42/0.03)] transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-px hover:border-slate-300 hover:shadow-card-hover max-[720px]:flex-col max-[720px]:items-start max-[720px]:gap-2.5"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2.5">
                    <span className="truncate font-mono text-[12.5px] font-medium text-slate-900">
                      {job.job_id}
                    </span>
                    <Badge variant={statusMeta.variant} pulse={PULSING_STATUSES.includes(job.status)}>
                      {statusMeta.label}
                    </Badge>
                  </div>
                  <p className="mt-1.5 flex items-center gap-1.5 text-xs text-slate-500">
                    <Clock className="h-3 w-3" />
                    <span className="tabular-nums">{new Date(job.created_at).toLocaleString()}</span>
                  </p>
                </div>
                <div className="flex flex-none items-center gap-1.5 max-[720px]:self-end">
                  <Link to={`/jobs/${job.job_id}`}>
                    <Button variant="ghost" size="sm">
                      <Eye className="h-3.5 w-3.5" />
                      查看
                    </Button>
                  </Link>
                  {cancellable && (
                    <>
                      <Button variant="secondary" size="sm" onClick={() => handleCancel(job.job_id)}>
                        取消
                      </Button>
                      <Button variant="ghostDanger" size="sm" onClick={() => handleDelete(job.job_id)}>
                        <Trash2 className="h-3.5 w-3.5" />
                        删除
                      </Button>
                    </>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
