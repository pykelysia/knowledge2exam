import { useCallback, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { listJobs, deleteJob, cancelJob } from '@/api/jobs'
import { useAuth } from '@/hooks/useAuth'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { Skeleton } from '@/components/ui/skeleton'
import { extractApiError } from '@/api/client'
import { JOB_STATUS_META, errorMessage } from '@/lib/constants'
import type { Job } from '@/api/types'

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
          <h1 className="text-2xl font-semibold text-slate-900">我的任务</h1>
          <p className="mt-1 text-sm text-slate-600">
            查看和管理已创建的试卷生成任务
          </p>
        </div>
        <Link to="/">
          <Button>新建任务</Button>
        </Link>
      </div>

      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="space-y-3">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="rounded-md border border-slate-200 p-4">
              <Skeleton className="mb-2 h-5 w-1/3" />
              <Skeleton className="h-4 w-1/2" />
            </div>
          ))}
        </div>
      ) : jobs.length === 0 ? (
        <div className="rounded-md border border-dashed border-slate-300 p-8 text-center">
          <p className="text-slate-500">暂无任务记录</p>
          <Link to="/" className="mt-2 inline-block">
            <Button variant="secondary" size="sm">
              去生成试卷
            </Button>
          </Link>
        </div>
      ) : (
        <ul className="space-y-3">
          {jobs.map((job) => {
            const statusMeta = JOB_STATUS_META[job.status]
            return (
              <li
                key={job.job_id}
                className="flex items-center justify-between rounded-md border border-slate-200 px-4 py-3"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="truncate text-sm font-medium text-slate-900">
                      {job.job_id}
                    </span>
                    <Badge variant={statusMeta.variant}>{statusMeta.label}</Badge>
                  </div>
                  <p className="mt-1 text-xs text-slate-500">
                    {new Date(job.created_at).toLocaleString()}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <Link to={`/jobs/${job.job_id}`}>
                    <Button variant="ghost" size="sm">
                      查看
                    </Button>
                  </Link>
                  {['pending', 'preprocessing', 'generating', 'rendering'].includes(
                    job.status,
                  ) && (
                    <>
                      <Button
                        variant="secondary"
                        size="sm"
                        onClick={() => handleCancel(job.job_id)}
                      >
                        取消
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleDelete(job.job_id)}
                      >
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
