import { client } from '@/api/client'
import type {
  SourceTypeFile,
  TextUploadCreate,
  Upload,
  UploadUpdateRequest,
  Visibility,
} from '@/api/types'

/** 上传单个文件（multipart/form-data）：上传后异步解析，返回 parse_status=pending。 */
export async function uploadFile(
  file: File,
  sourceType: SourceTypeFile,
  visibility: Visibility,
  schoolId?: string | null,
  courseId?: string | null,
): Promise<Upload> {
  const form = new FormData()
  form.append('file', file)
  form.append('source_type', sourceType)
  form.append('visibility', visibility)
  if (schoolId) form.append('school_id', schoolId)
  if (courseId) form.append('course_id', courseId)
  const { data } = await client.post<Upload>('/uploads', form)
  return data
}

/** 提交手动输入文本（manual_text / extra_requirement），即时解析完成。 */
export async function uploadText(payload: TextUploadCreate): Promise<Upload> {
  const { data } = await client.post<Upload>('/uploads', payload)
  return data
}

/** 修改可见性 / 标签（显式 null 清除标签）。 */
export async function patchUpload(
  uploadId: string,
  payload: UploadUpdateRequest,
): Promise<Upload> {
  const { data } = await client.patch<Upload>(`/uploads/${uploadId}`, payload)
  return data
}

/** 软删除上传件（被运行中任务引用时后端会拒绝）。 */
export async function deleteUpload(uploadId: string): Promise<void> {
  await client.delete(`/uploads/${uploadId}`)
}
