import { useCallback, useEffect, useMemo, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertCircle,
  Check,
  FileText,
  PenLine,
  Plus,
  School as SchoolIcon,
  SlidersHorizontal,
  Trash2,
  Upload as UploadIcon,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { Card, CardContent, CardHeader, CardIcon, CardTitle } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { Skeleton } from '@/components/ui/skeleton'
import { uploadFile, uploadText, deleteUpload } from '@/api/uploads'
import { createJob } from '@/api/jobs'
import { listCourses, listSchools } from '@/api/schools'
import { extractApiError } from '@/api/client'
import { FILE_SOURCE_TYPES, SOURCE_TYPE_META, errorMessage, isAllowedExtension } from '@/lib/constants'
import type { Course, School, SourceType, SourceTypeFile, SourceTypeText } from '@/api/types'

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
  const [dragging, setDragging] = useState(false)

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

  const readyCount = useMemo(
    () =>
      items.filter((it) => !it.error).length +
      (manualText.trim() !== '' ? 1 : 0) +
      (extraRequirement.trim() !== '' ? 1 : 0),
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
            updateItem(item.id, { uploading: false, uploadId })
          } else if (item.kind === 'text' && item.rawText) {
            const res = await uploadText({
              source_type: item.sourceType as SourceTypeText,
              raw_text: item.rawText,
            })
            uploadId = res.upload_id
            updateItem(item.id, { uploading: false, uploadId })
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
      <Select
        value={item.sourceType}
        onChange={(e) => updateItem(item.id, { sourceType: e.target.value as SourceType })}
        className="w-40 flex-none px-2.5 py-1.5 text-[12.5px]"
      >
        {FILE_SOURCE_TYPES.map((t) => (
          <option key={t} value={t}>
            {SOURCE_TYPE_META[t].label}
          </option>
        ))}
      </Select>
    ),
    [],
  )

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-slate-900">生成试卷</h1>
        <p className="mt-1.5 text-[13.5px] text-slate-500">
          上传课程资料或输入文本，系统据此生成一份模拟试卷。
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* 上传文件 */}
        <Card>
          <CardHeader>
            <CardIcon>
              <UploadIcon />
            </CardIcon>
            <CardTitle>上传资料</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <label
              className={`group flex cursor-pointer flex-col items-center justify-center rounded-xl border-[1.5px] border-dashed px-4 py-[30px] text-center transition-[border-color,background-color,box-shadow] ${
                dragging
                  ? 'border-slate-900 bg-slate-100 ring-4 ring-slate-900/[0.06]'
                  : 'border-slate-300 bg-slate-50/60 hover:border-slate-400 hover:bg-slate-50'
              }`}
              onDragOver={(e) => {
                e.preventDefault()
                setDragging(true)
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={(e) => {
                e.preventDefault()
                setDragging(false)
                if (e.dataTransfer.files.length > 0) addFiles(e.dataTransfer.files)
              }}
            >
              <span
                className={`mb-3 grid h-[46px] w-[46px] place-items-center rounded-full border bg-white shadow-card transition-colors group-hover:border-slate-900 group-hover:bg-slate-900 group-hover:text-white ${
                  dragging ? 'border-slate-900 bg-slate-900 text-white' : 'border-slate-200 text-slate-500'
                }`}
              >
                <UploadIcon className="h-5 w-5" />
              </span>
              <span className="text-[13.5px] text-slate-700">
                <b className="font-semibold text-slate-900">点击选择文件</b>，或将文件拖拽到此处
              </span>
              <span className="mt-1.5 text-xs leading-relaxed text-slate-400">
                支持 docx / pptx / pdf / md / txt / jpg / jpeg / png / webp / bmp，可多选
              </span>
              <input
                type="file"
                multiple
                className="hidden"
                accept=".docx,.pptx,.pdf,.md,.txt,.jpg,.jpeg,.png,.webp,.bmp"
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
                      className="flex items-center gap-3 rounded-[10px] border border-slate-200 bg-white px-3 py-2 transition-[border-color,box-shadow] hover:border-slate-300 hover:shadow-card max-[720px]:flex-wrap max-[720px]:row-gap-2"
                    >
                      <span className="grid h-[30px] w-[30px] flex-none place-items-center rounded-lg bg-slate-100 text-slate-600">
                        <FileText className="h-[15px] w-[15px]" />
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[13px] text-slate-700">
                        {it.file?.name}
                      </span>
                      {renderSourceTypeSelect(it)}
                      <label className="flex flex-none items-center gap-1.5 text-xs text-slate-600">
                        <input
                          type="checkbox"
                          className="h-[15px] w-[15px] accent-slate-900"
                          checked={it.shareable}
                          onChange={(e) => updateItem(it.id, { shareable: e.target.checked })}
                        />
                        共享
                      </label>
                      {it.uploading && <Spinner className="h-4 w-4 flex-none" />}
                      {!it.uploading && it.uploadId && !it.error && (
                        <span className="flex flex-none items-center gap-1 text-xs text-emerald-600">
                          <Check className="h-3.5 w-3.5" />
                          已上传
                        </span>
                      )}
                      {it.error && <span className="flex-none text-xs text-red-600">{it.error}</span>}
                      <button
                        type="button"
                        onClick={() => removeItem(it.id)}
                        className="grid h-7 w-7 flex-none place-items-center rounded-[7px] text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600"
                        aria-label="移除"
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </li>
                  ))}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* 手动输入 */}
        <Card>
          <CardHeader>
            <CardIcon>
              <PenLine />
            </CardIcon>
            <CardTitle>手动输入</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <Label htmlFor="manual-text">
                学习笔记 / 重点内容 <span className="font-normal text-slate-400">（manual_text）</span>
              </Label>
              <Textarea
                id="manual-text"
                rows={4}
                value={manualText}
                onChange={(e) => setManualText(e.target.value)}
                placeholder="例如：本学期重点是傅里叶变换与拉普拉斯变换的关系……"
              />
            </div>
            <div>
              <Label htmlFor="extra-requirement">
                额外要求 <span className="font-normal text-slate-400">（extra_requirement）</span>
              </Label>
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
            <CardIcon>
              <SchoolIcon />
            </CardIcon>
              <CardTitle>共享归属</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="school">学校</Label>
                <Select
                  id="school"
                  value={schoolId}
                  onChange={(e) => {
                    setSchoolId(e.target.value)
                    setCourseId('')
                  }}
                >
                  <option value="">请选择学校</option>
                  {schools.map((s) => (
                    <option key={s.school_id} value={s.school_id}>
                      {s.name}
                    </option>
                  ))}
                </Select>
                {loadingSchools && <Skeleton className="mt-1 h-4 w-full" />}
              </div>
              <div>
                <Label htmlFor="course">课程</Label>
                <Select
                  id="course"
                  value={courseId}
                  onChange={(e) => setCourseId(e.target.value)}
                  disabled={!schoolId || loadingCourses}
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
                </Select>
                {loadingCourses && <Skeleton className="mt-1 h-4 w-full" />}
              </div>
            </CardContent>
          </Card>
        )}

        {/* 生成选项 */}
        <Card>
          <CardHeader>
            <CardIcon>
              <SlidersHorizontal />
            </CardIcon>
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
                  className="max-w-[200px] tabular-nums"
                />
              </div>
              <label className="flex items-end gap-2 pb-[9px] text-[13px] text-slate-700">
                <input
                  type="checkbox"
                  className="h-[15px] w-[15px] accent-slate-900"
                  checked={needExplanation}
                  onChange={(e) => setNeedExplanation(e.target.checked)}
                />
                生成答案解析
              </label>
            </div>
          </CardContent>
        </Card>

        {error && (
          <div className="flex items-start gap-2.5 rounded-[10px] border border-red-200 bg-red-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-red-700">
            <AlertCircle className="mt-0.5 h-4 w-4 flex-none text-red-500" />
            <span>{error}</span>
          </div>
        )}

        <div className="flex items-center gap-3.5">
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
          {hasAnyInput ? (
            <span className="inline-flex items-center gap-1.5 text-[12.5px] text-slate-500">
              <Check className="h-3.5 w-3.5 text-emerald-600" />
              已就绪 {readyCount} 项输入，可提交生成
            </span>
          ) : (
            <span className="text-[12.5px] text-slate-400">至少提供一项输入（文件或文本）</span>
          )}
        </div>
      </form>
    </div>
  )
}
