import { client } from '@/api/client'
import type { SourceTypeFile, TextUploadCreate, Upload } from '@/api/types'

/** 上传单个文件（multipart/form-data）。 */
export async function uploadFile(
  file: File,
  sourceType: SourceTypeFile,
  shareable: boolean,
): Promise<Upload> {
  const form = new FormData()
  form.append('file', file)
  form.append('source_type', sourceType)
  form.append('shareable', String(shareable))
  const { data } = await client.post<Upload>('/uploads', form)
  return data
}

/** 提交手动输入文本（manual_text / extra_requirement）。 */
export async function uploadText(payload: TextUploadCreate): Promise<Upload> {
  const { data } = await client.post<Upload>('/uploads', payload)
  return data
}

/** 删除尚未用于任务的上传件。 */
export async function deleteUpload(uploadId: string): Promise<void> {
  await client.delete(`/uploads/${uploadId}`)
}
