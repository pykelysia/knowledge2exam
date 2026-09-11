import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { FileText, Plus, Trash2, Upload as UploadIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { Skeleton } from '@/components/ui/skeleton'
import { uploadFile, uploadText, deleteUpload } from '@/api/uploads'
import { createJob } from '@/api/jobs'
import { listCourses, listSchools } from '@/api/schools'
import { extractApiError } from '@/api/client'
import { FILE_SOURCE_TYPES, SOURCE_TYPE_META, errorMessage, isAllowedExtension } from '@/lib/constants'
import type { Course, School, SourceType, SourceTypeFile, SourceTypeText, Upload } from '@/api/types'

/** 前端维护的输入项：一个文件（含其 source_type 与共享意愿）或一段文本。 */
interface InputItem {
  id: string
  kind: 'file' | 'text'
  sourceType: SourceType
  file?: File
  shareable: boolean
  rawText?: string
  // 已上传成功后得到的 upload_id（生成前统一上传）。
  uploadId?: string
  uploading?: boolean
  error?: string
  // 上传返回的解析预览与状态。
  parseStatus?: Upload['parse_status']
  parseError?: string | null
  preview?: Upload['preview']
}

let seq = 0
function nextId() {
  seq += 1
  return `input-${seq}`
}

export function Dashboard() {
  const navigate = useNavigate()

  const [items, setItems] = useState<InputItem[]>([])
  const [manualText, setManualText] = useState('')
  const [extraRequirement, setExtraRequirement] = useState('')
  const [duration, setDuration] = useState(100)
  const [needExplanation, setNeedExplanation] = useState(true)

  // 学校 / 课程（FR-5）
  const [schools, setSchools] = useState<School[]>([])
  const [courses, setCourses] = useState<Course[]>([])
  const [schoolId, setSchoolId] = useState('')
  const [courseId, setCourseId] = useState('')
  const [loadingSchools, setLoadingSchools] = useState(true)
  const [loadingCourses, setLoadingCourses] = useState(false)

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 是否需要共享选择（任一文件项勾选了共享）。
  const anyShareable = items.some((it) => it.kind === 'file' && it.shareable)

  useEffect(() => {
    listSchools()
      .then((res) => setSchools(res.schools))
      .catch(() => setSchools([]))
      .finally(() => setLoadingSchools(false))
  }, [])

  useEffect(() => {
    if (!schoolId) {
      setCourses([])
      return
    }
    setLoadingCourses(true)
    listCourses(schoolId)
      .then((res) => setCourses(res.courses))
      .catch(() => setCourses([]))
      .finally(() => setLoadingCourses(false))
  }, [schoolId])

  function addFiles(files: FileList | File[]) {
    const next: InputItem[] = []
    Array.from(files).forEach((file) => {
      if (!isAllowedExtension(file.name)) {
        next.push({
          id: nextId(),
          kind: 'file',
          sourceType: 'book',
          file,
          shareable: false,
          error: '不支持的文件格式',
        })
        return
      }
      next.push({
        id: nextId(),
        kind: 'file',
        sourceType: 'book',
        file,
        shareable: false,
      })
    })
    setItems((prev) => [...prev, ...next])
  }

  function updateItem(id: string, patch: Partial<InputItem>) {
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...patch } : it)))
  }

  function removeItem(id: string) {
    const it = items.find((x) => x.id === id)
    // 若已上传，尽力删除后端记录（可能被任务引用而 409，忽略）。
    if (it?.uploadId) {
      deleteUpload(it.uploadId).catch(() => {})
    }
    setItems((prev) => prev.filter((x) => x.id !== id))
  }

  const hasAnyInput = useMemo(
    () => items.some((it) => !it.error) || manualText.trim() !== '' || extraRequirement.trim() !== '',
    [items, manualText, extraRequirement],
  )

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)

    if (!hasAnyInput) {
      setError(errorMessage('INPUT_EMPTY'))
      return
    }
    if (anyShareable && (!schoolId || !courseId)) {
      setError(errorMessage('SHARE_SCOPE_REQUIRED'))
      return
    }
    if (duration < 5 || duration > 300) {
      setError(errorMessage('INVALID_DURATION'))
      return
    }

    setSubmitting(true)
    try {
      // 1) 逐项上传，收集 upload_id。文本项单独提交（FR-2）。
      const uploadIds: string[] = []

      for (const item of items) {
        if (item.error) continue
        if (item.uploadId) {
          uploadIds.push(item.uploadId)
          continue
        }
        updateItem(item.id, { uploading: true })
        try {
          let uploadId: string
          if (item.kind === 'file' && item.file) {
            const res = await uploadFile(item.file, item.sourceType as SourceTypeFile, item.shareable)
            uploadId = res.upload_id
            updateItem(item.id, {
              uploading: false,
              uploadId,
              parseStatus: res.parse_status,
              parseError: res.parse_error,
              preview: res.preview,
            })
          } else if (item.kind === 'text' && item.rawText) {
            const res = await uploadText({
              source_type: item.sourceType as SourceTypeText,
              raw_text: item.rawText,
            })
            uploadId = res.upload_id
            updateItem(item.id, {
              uploading: false,
              uploadId,
              parseStatus: res.parse_status,
              parseError: res.parse_error,
              preview: res.preview,
            })
          } else {
            continue
          }
          uploadIds.push(uploadId)
        } catch (err) {
          const apiErr = extractApiError(err)
          updateItem(item.id, {
            uploading: false,
            error: apiErr ? errorMessage(apiErr.error_code) : '上传失败',
          })
          // 单文件失败不阻塞整体（NFR-5）；但至少一项失败会继续。
        }
      }

      // 2) 提交两个文本框（如果非空）。
      if (manualText.trim()) {
        const res = await uploadText({ source_type: 'manual_text', raw_text: manualText.trim() })
        uploadIds.push(res.upload_id)
      }
      if (extraRequirement.trim()) {
        const res = await uploadText({
          source_type: 'extra_requirement',
          raw_text: extraRequirement.trim(),
        })
        uploadIds.push(res.upload_id)
      }

      if (uploadIds.length === 0) {
        setError(errorMessage('INPUT_EMPTY'))
        return
      }

      // 3) 创建任务。
      const job = await createJob({
        upload_ids: uploadIds,
        school_id: anyShareable ? schoolId : null,
        course_id: anyShareable ? courseId : null,
        duration_minutes: duration,
        need_explanation: needExplanation,
      })

      navigate(`/jobs/${job.job_id}`)
    } catch (err) {
      const apiErr = extractApiError(err)
      setError(apiErr ? errorMessage(apiErr.error_code) : '创建任务失败，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  const renderSourceTypeSelect = useCallback(
    (item: InputItem) => (
      <select
        value={item.sourceType}
        onChange={(e) => updateItem(item.id, { sourceType: e.target.value as SourceType })}
        className="w-40 rounded-md border border-slate-300 bg-white px-2 py-1.5 text-sm"
      >
        {FILE_SOURCE_TYPES.map((t) => (
          <option key={t} value={t}>
            {SOURCE_TYPE_META[t].label}
          </option>
        ))}
      </select>
    ),
    [],
  )

  function renderPreview(item: InputItem) {
    if (!item.uploadId) return null
    const status = item.parseStatus
    if (status === 'failed') {
      return (
        <div className="mt-1 text-xs text-red-600">
          {item.parseError || '解析失败'}
        </div>
      )
    }
    if (status === 'succeeded' && item.preview) {
      return (
        <div className="mt-1 space-y-1">
          {item.preview.excerpt && (
            <p className="text-xs text-slate-500 line-clamp-2">
              {item.preview.excerpt}
            </p>
          )}
          <div className="flex gap-3 text-xs text-slate-400">
            {typeof item.preview.char_count === 'number' && (
              <span>{item.preview.char_count} 字</span>
            )}
            {typeof item.preview.page_count === 'number' && (
              <span>{item.preview.page_count} 页</span>
            )}
          </div>
        </div>
      )
    }
    if (item.uploading) {
      return <div className="mt-1 text-xs text-slate-400">正在解析…</div>
    }
    return null
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">生成试卷</h1>
        <p className="mt-1 text-sm text-slate-600">
          上传课程资料或输入文本，系统据此生成一份模拟试卷。
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* 上传文件 */}
        <Card>
          <CardHeader>
            <CardTitle>上传资料</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <label className="flex cursor-pointer flex-col items-center justify-center rounded-lg border-2 border-dashed border-slate-300 bg-slate-50 px-4 py-8 text-center hover:bg-slate-100">
              <UploadIcon className="mb-2 h-6 w-6 text-slate-400" />
              <span className="text-sm text-slate-600">点击选择文件，可多选</span>
              <span className="mt-1 text-xs text-slate-400">
                支持 docx / doc / pptx / ppt / pdf / md / txt / jpg / jpeg / png / webp / bmp
              </span>
              <input
                type="file"
                multiple
                className="hidden"
                accept=".docx,.doc,.pptx,.ppt,.pdf,.md,.txt,.jpg,.jpeg,.png,.webp,.bmp"
                onChange={(e) => {
                  if (e.target.files) addFiles(e.target.files)
                  e.target.value = ''
                }}
              />
            </label>

            {items.filter((it) => it.kind === 'file').length > 0 && (
              <ul className="space-y-2">
                {items
                  .filter((it) => it.kind === 'file')
                  .map((it) => (
                    <li
                      key={it.id}
                      className="flex flex-col rounded-md border border-slate-200 px-3 py-2"
                    >
                      <div className="flex items-center gap-3">
                        <FileText className="h-4 w-4 shrink-0 text-slate-400" />
                        <span className="min-w-0 flex-1 truncate text-sm text-slate-700">
                          {it.file?.name}
                        </span>
                        {renderSourceTypeSelect(it)}
                        <label className="flex items-center gap-1 text-xs text-slate-600">
                          <input
                            type="checkbox"
                            checked={it.shareable}
                            onChange={(e) => updateItem(it.id, { shareable: e.target.checked })}
                          />
                          共享
                        </label>
                        {it.uploading && <Spinner className="h-4 w-4" />}
                        {it.error && <span className="text-xs text-red-600">{it.error}</span>}
                        <button
                          type="button"
                          onClick={() => removeItem(it.id)}
                          className="text-slate-400 hover:text-red-600"
                          aria-label="移除"
                        >
                          <Trash2 className="h-4 w-4" />
                        </button>
                      </div>
                      {renderPreview(it)}
                    </li>
                  ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* 手动输入 */}
        <Card>
          <CardHeader>
            <CardTitle>手动输入</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <Label htmlFor="manual-text">学习笔记 / 重点内容（manual_text）</Label>
              <Textarea
                id="manual-text"
                rows={4}
                value={manualText}
                onChange={(e) => setManualText(e.target.value)}
                placeholder="例如：本学期重点是傅里叶变换与拉普拉斯变换的关系……"
              />
            </div>
            <div>
              <Label htmlFor="extra-requirement">额外要求（extra_requirement）</Label>
              <Textarea
                id="extra-requirement"
                rows={2}
                value={extraRequirement}
                onChange={(e) => setExtraRequirement(e.target.value)}
                placeholder="例如：多出一些计算题，难度适中……"
              />
            </div>
          </CardContent>
        </Card>

        {/* 共享归属 */}
        {anyShareable && (
          <Card>
            <CardHeader>
              <CardTitle>共享归属</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="school">学校</Label>
                <select
                  id="school"
                  value={schoolId}
                  onChange={(e) => {
                    setSchoolId(e.target.value)
                    setCourseId('')
                  }}
                  className="w-full rounded-md border border-slate-300 bg-white px-2 py-2 text-sm"
                >
                  <option value="">请选择学校</option>
                  {schools.map((s) => (
                    <option key={s.school_id} value={s.school_id}>
                      {s.name}
                    </option>
                  ))}
                </select>
                {loadingSchools && <Skeleton className="mt-1 h-4 w-full" />}
              </div>
              <div>
                <Label htmlFor="course">课程</Label>
                <select
                  id="course"
                  value={courseId}
                  onChange={(e) => setCourseId(e.target.value)}
                  disabled={!schoolId || loadingCourses}
                  className="w-full rounded-md border border-slate-300 bg-white px-2 py-2 text-sm disabled:opacity-50"
                >
                  <option value="">请选择课程</option>
                  {courses.map((c) => (
                    <option key={c.course_id} value={c.course_id}>
                      {c.name}
                      {typeof c.shared_resource_count === 'number'
                        ? `（共享资源 ${c.shared_resource_count}）`
                        : ''}
                    </option>
                  ))}
                </select>
                {loadingCourses && <Skeleton className="mt-1 h-4 w-full" />}
              </div>
            </CardContent>
          </Card>
        )}

        {/* 生成选项 */}
        <Card>
          <CardHeader>
            <CardTitle>生成选项</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="duration">考试时长（分钟）</Label>
                <Input
                  id="duration"
                  type="number"
                  min={5}
                  max={300}
                  value={duration}
                  onChange={(e) => setDuration(Number(e.target.value))}
                />
              </div>
              <label className="flex items-center gap-2 pt-6 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={needExplanation}
                  onChange={(e) => setNeedExplanation(e.target.checked)}
                />
                生成答案解析
              </label>
            </div>
          </CardContent>
        </Card>

        {error && (
          <div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        )}

        <div className="flex items-center gap-3">
          <Button type="submit" disabled={submitting || !hasAnyInput}>
            {submitting ? (
              <>
                <Spinner className="h-4 w-4" /> 正在提交…
              </>
            ) : (
              <>
                <Plus className="h-4 w-4" /> 生成试卷
              </>
            )}
          </Button>
          {items.length === 0 && !manualText && !extraRequirement && (
            <span className="text-sm text-slate-400">至少提供一项输入（文件或文本）</span>
          )}
        </div>
      </form>
    </div>
  )
}
