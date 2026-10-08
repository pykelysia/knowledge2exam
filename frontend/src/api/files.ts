import { client } from '@/api/client'
import type {
  ImportAccepted,
  LibraryResponse,
  PoolFilesResponse,
  SourceType,
  UploadPreview,
} from '@/api/types'

/** 个人库：我的上传 + 公开池保存的引用（含失效原件标记）。 */
export async function listMyFiles(): Promise<LibraryResponse> {
  const { data } = await client.get<LibraryResponse>('/files/mine')
  return data
}

/** 文件预览（个人库与公开池共用；基于解析产物文本）。 */
export async function getPreview(uploadId: string): Promise<UploadPreview> {
  const { data } = await client.get<UploadPreview>(`/files/${uploadId}/preview`)
  return data
}

export interface PoolQuery {
  q?: string
  school_id?: string
  course_id?: string
  source_type?: SourceType
  page?: number
  page_size?: number
}

/** 公开池浏览 / 检索（关键词 ∪ 内容语义）。 */
export async function listPoolFiles(query: PoolQuery = {}): Promise<PoolFilesResponse> {
  const { data } = await client.get<PoolFilesResponse>('/pool/files', {
    params: query,
  })
  return data
}

/** 保存公开池文件到个人库（引用级，不复制数据；幂等）。 */
export async function savePoolFile(uploadId: string): Promise<ImportAccepted> {
  const { data } = await client.post<ImportAccepted>(
    `/pool/files/${uploadId}/import`,
  )
  return data
}

/** 取消保存（移除个人库引用；幂等）。 */
export async function unsavePoolFile(uploadId: string): Promise<void> {
  await client.delete(`/pool/files/${uploadId}/import`)
}
