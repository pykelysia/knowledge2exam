import { client } from '@/api/client'
import type { Job, JobAccepted, JobCreate, QuestionsResponse } from '@/api/types'

export async function createJob(payload: JobCreate): Promise<JobAccepted> {
  const { data } = await client.post<JobAccepted>('/jobs', payload)
  return data
}

/** 任务快照（轮询降级用）。 */
export async function getJob(jobId: string): Promise<Job> {
  const { data } = await client.get<Job>(`/jobs/${jobId}`)
  return data
}

export async function deleteJob(jobId: string): Promise<void> {
  await client.delete(`/jobs/${jobId}`)
}

export async function cancelJob(jobId: string): Promise<void> {
  await client.post(`/jobs/${jobId}/cancel`)
}

export async function listQuestions(jobId: string): Promise<QuestionsResponse> {
  const { data } = await client.get<QuestionsResponse>(`/jobs/${jobId}/questions`)
  return data
}

/** 拼接产物下载地址（artifacts 里的 md_url / pdf_url 已是相对路径）。 */
export function resolveArtifactUrl(url: string | null | undefined): string | null {
  if (!url) return null
  if (/^https?:\/\//.test(url)) return url
  return url
}

/** 通过 axios 下载产物文件，自动携带认证头并处理 blob 下载。 */
export async function downloadArtifact(url: string, filename: string): Promise<void> {
  const { data, headers } = await client.get(url, {
    responseType: 'blob',
  })

  const blob = new Blob([data], { type: headers['content-type'] || 'application/octet-stream' })
  const blobUrl = URL.createObjectURL(blob)

  const a = document.createElement('a')
  a.href = blobUrl
  a.download = filename
  a.style.display = 'none'
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(blobUrl)
}
