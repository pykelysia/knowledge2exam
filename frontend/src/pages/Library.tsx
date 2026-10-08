import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertCircle,
  Check,
  ChevronLeft,
  ChevronRight,
  Eye,
  FileText,
  Globe,
  Lock,
  Trash2,
  Upload as UploadIcon,
  UserCheck,
  X,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Card, CardContent, CardHeader, CardIcon, CardTitle } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { Badge } from '@/components/ui/badge'
import { FilePreview } from '@/components/FilePreview'
import { uploadFile, patchUpload, deleteUpload } from '@/api/uploads'
import { listMyFiles, unsavePoolFile } from '@/api/files'
import { listCourses, listSchools } from '@/api/schools'
import { extractApiError } from '@/api/client'
import type { LibraryFile, School, Course, SourceTypeFile, Visibility } from '@/api/types'
import {
  FILE_SOURCE_TYPES,
  FILE_STATUS_META,
  SOURCE_TYPE_META,
  VISIBILITY_META,
  errorMessage,
  isAllowedExtension,
} from '@/lib/constants'

/** 待上传队列项：选择后立即上传，解析在后台进行。 */
interface PendingItem {
  id: string
  file: File
  sourceType: SourceTypeFile
  uploading: boolean
  error?: string
}

let seq = 0
function nextId() {
  seq += 1
  return `lib-${seq}`
}

const PAGE_SIZE = 50

export function Library() {
  const [files, setFiles] = useState<LibraryFile[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [previewId, setPreviewId] = useState<string | null>(null)

  // 上传设置（对本批上传的文件统一生效）
  const [sourceType, setSourceType] = useState<SourceTypeFile>('book')
  const [visibility, setVisibility] = useState<Visibility>('private')
  const [schoolId, setSchoolId] = useState('')
  const [courseId, setCourseId] = useState('')
  const [schools, setSchools] = useState<School[]>([])
  const [courses, setCourses] = useState<Course[]>([])

  const [pending, setPending] = useState<PendingItem[]>([])
  const [dragging, setDragging] = useState(false)

  const schoolsRef = useRef(schools)
  const coursesRef = useRef(courses)
  schoolsRef.current = schools
  coursesRef.current = courses

  const schoolName = useCallback(
    (id?: string | null) => schoolsRef.current.find((s) => s.school_id === id)?.name,
    [],
  )
  const courseName = useCallback(
    (id?: string | null) => coursesRef.current.find((c) => c.course_id === id)?.name,
    [],
  )

  const refresh = useCallback(async (targetPage: number) => {
    try {
      const res = await listMyFiles(targetPage, PAGE_SIZE)
      setFiles(res.files)
      setTotal(res.total)
      setPage(res.page)
      // 当前页被删空时回退一页，避免停留在空页
      if (res.files.length === 0 && res.page > 1) {
        setPage(res.page - 1)
      }
    } catch {
      /* 轮询失败静默，下次再试 */
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    listSchools()
      .then((res) => setSchools(res.schools))
      .catch(() => setSchools([]))
    listCourses()
      .then((res) => setCourses(res.courses))
      .catch(() => setCourses([]))
    refresh(1)
  }, [refresh])

  // 有解析中的文件时轮询刷新（保持当前页）
  const anyParsing = files.some((f) => f.status === 'parsing')
  useEffect(() => {
    if (!anyParsing) return
    const timer = setInterval(() => void refresh(page), 3000)
    return () => clearInterval(timer)
  }, [anyParsing, refresh, page])

  function updatePending(id: string, patch: Partial<PendingItem>) {
    setPending((prev) => prev.map((it) => (it.id === id ? { ...it, ...patch } : it)))
  }

  async function startUpload(item: PendingItem) {
    updatePending(item.id, { uploading: true, error: undefined })
    try {
      await uploadFile(item.file, item.sourceType, visibility, schoolId || null, courseId || null)
      updatePending(item.id, { uploading: false })
      // 新文件按创建时间排最前，回第一页立即可见
      await refresh(1)
      setTimeout(() => {
        setPending((prev) => prev.filter((x) => x.id !== item.id))
      }, 1500)
    } catch (err) {
      const apiErr = extractApiError(err)
      updatePending(item.id, {
        uploading: false,
        error: apiErr ? errorMessage(apiErr.error_code) : '上传失败',
      })
    }
  }

  function addFiles(list: FileList | File[]) {
    if (visibility === 'school' && !schoolId) {
      setError(errorMessage('SHARE_SCOPE_REQUIRED'))
      return
    }
    setError(null)
    const next: PendingItem[] = []
    Array.from(list).forEach((file) => {
      const id = nextId()
      if (!isAllowedExtension(file.name)) {
        next.push({ id, file, sourceType, uploading: false, error: '不支持的文件格式' })
        return
      }
      next.push({ id, file, sourceType, uploading: false })
    })
    setPending((prev) => [...prev, ...next])
    next.filter((it) => !it.error).forEach((it) => void startUpload(it))
  }

  async function handleDelete(file: LibraryFile) {
    try {
      await deleteUpload(file.upload_id)
      await refresh(page)
    } catch (err) {
      const apiErr = extractApiError(err)
      setError(apiErr ? errorMessage(apiErr.error_code) : '删除失败')
    }
  }

  async function handleUnsave(file: LibraryFile) {
    try {
      await unsavePoolFile(file.upload_id)
      await refresh(page)
    } catch {
      setError('取消保存失败，请稍后再试')
    }
  }

  async function handleVisibilityChange(file: LibraryFile, value: Visibility) {
    try {
      await patchUpload(file.upload_id, { visibility: value })
      await refresh(page)
    } catch (err) {
      const apiErr = extractApiError(err)
      setError(apiErr ? errorMessage(apiErr.error_code) : '修改失败')
    }
  }

  const ownFiles = useMemo(() => files.filter((f) => !f.imported), [files])
  const savedFiles = useMemo(() => files.filter((f) => f.imported), [files])
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  const renderFileRow = (file: LibraryFile) => {
    const dead = file.status === 'deleted'
    return (
      <li
        key={file.upload_id}
        className={`flex items-center gap-3 rounded-[10px] border border-slate-200 bg-white px-3 py-2 transition-[border-color,box-shadow] hover:border-slate-300 hover:shadow-card max-[720px]:flex-wrap max-[720px]:row-gap-2 ${
          dead ? 'opacity-60' : ''
        }`}
      >
        <span className="grid h-[30px] w-[30px] flex-none place-items-center rounded-lg bg-slate-100 text-slate-600">
          <FileText className="h-[15px] w-[15px]" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-[13px] text-slate-700">
              {file.filename || '未命名文件'}
            </span>
            {file.imported && (
              <span className="flex flex-none items-center gap-1 text-[11.5px] text-slate-400">
                <UserCheck className="h-3 w-3" />
                {file.owner.username}
              </span>
            )}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-1.5">
            <Badge>{SOURCE_TYPE_META[file.source_type].label}</Badge>
            <Badge variant={FILE_STATUS_META[file.status].variant}>
              {file.status === 'parsing' && <Spinner className="mr-1 h-3 w-3" />}
              {FILE_STATUS_META[file.status].label}
            </Badge>
            <Badge>{VISIBILITY_META[file.visibility].label}</Badge>
            {(schoolName(file.school_id) || courseName(file.course_id)) && (
              <span className="text-[11.5px] text-slate-400">
                {[schoolName(file.school_id), courseName(file.course_id)]
                  .filter(Boolean)
                  .join(' · ')}
              </span>
            )}
            {file.char_count != null && file.status === 'available' && (
              <span className="text-[11.5px] text-slate-400">约 {file.char_count} 字</span>
            )}
          </div>
          {file.status === 'failed' && file.parse_error && (
            <p className="mt-1 truncate text-[11.5px] text-red-500">{file.parse_error}</p>
          )}
        </div>
        <div className="flex flex-none items-center gap-1.5">
          {!dead && (
            <button
              type="button"
              onClick={() => setPreviewId(file.upload_id)}
              className="grid h-7 w-7 place-items-center rounded-[7px] text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-900"
              aria-label="预览"
            >
              <Eye className="h-4 w-4" />
            </button>
          )}
          {file.imported ? (
            <button
              type="button"
              onClick={() => void handleUnsave(file)}
              className="grid h-7 w-7 place-items-center rounded-[7px] text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600"
              aria-label="取消保存"
            >
              <X className="h-4 w-4" />
            </button>
          ) : (
            <>
              <Select
                value={file.visibility}
                onChange={(e) =>
                  void handleVisibilityChange(file, e.target.value as Visibility)
                }
                className="w-[104px] flex-none px-2 py-1 text-[12px]"
                aria-label="修改可见性"
              >
                <option value="private">私有</option>
                <option value="school" disabled={!file.school_id}>
                  本校共享
                </option>
                <option value="public">公开池</option>
              </Select>
              <button
                type="button"
                onClick={() => void handleDelete(file)}
                className="grid h-7 w-7 place-items-center rounded-[7px] text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600"
                aria-label="删除"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </>
          )}
        </div>
      </li>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-slate-900">个人库</h1>
        <p className="mt-1.5 text-[13.5px] text-slate-500">
          上传文件自动解析入库、反复使用；也可从{' '}
          <Link
            to="/pool"
            className="font-medium text-slate-900 underline decoration-slate-300 underline-offset-[3px] hover:decoration-slate-900"
          >
            公开池
          </Link>{' '}
          保存他人共享的资料。共 {total} 个文件。
        </p>
      </div>

      {/* 上传区 */}
      <Card>
        <CardHeader>
          <CardIcon>
            <UploadIcon />
          </CardIcon>
          <CardTitle>上传文件</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-2 gap-4 max-[720px]:grid-cols-1">
            <div>
              <Label htmlFor="lib-source-type">内容类型</Label>
              <Select
                id="lib-source-type"
                value={sourceType}
                onChange={(e) => setSourceType(e.target.value as SourceTypeFile)}
              >
                {FILE_SOURCE_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {SOURCE_TYPE_META[t].label}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="lib-visibility">可见性</Label>
              <Select
                id="lib-visibility"
                value={visibility}
                onChange={(e) => setVisibility(e.target.value as Visibility)}
              >
                <option value="private">私有（仅自己可见）</option>
                <option value="school">本校共享（同校可见）</option>
                <option value="public">公开池（所有人可见）</option>
              </Select>
            </div>
            <div>
              <Label htmlFor="lib-school">学校标签（可选）</Label>
              <Select id="lib-school" value={schoolId} onChange={(e) => setSchoolId(e.target.value)}>
                <option value="">不设置</option>
                {schools.map((s) => (
                  <option key={s.school_id} value={s.school_id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="lib-course">课程标签（可选）</Label>
              <Select id="lib-course" value={courseId} onChange={(e) => setCourseId(e.target.value)}>
                <option value="">不设置</option>
                {courses.map((c) => (
                  <option key={c.course_id} value={c.course_id}>
                    {c.name}
                  </option>
                ))}
              </Select>
            </div>
          </div>
          {visibility === 'school' && !schoolId && (
            <p className="text-[12.5px] text-amber-600">本校共享需要先选择学校标签。</p>
          )}

          <label
            className={`group flex cursor-pointer flex-col items-center justify-center rounded-xl border-[1.5px] border-dashed px-4 py-[26px] text-center transition-[border-color,background-color,box-shadow] ${
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
              上传后自动解析进知识库，生成试卷时可直接选用（支持 docx / pptx / pdf / md / txt / 图片）
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

          {pending.length > 0 && (
            <ul className="space-y-2">
              {pending.map((it) => (
                <li
                  key={it.id}
                  className="flex items-center gap-3 rounded-[10px] border border-slate-200 bg-white px-3 py-2"
                >
                  <span className="grid h-[30px] w-[30px] flex-none place-items-center rounded-lg bg-slate-100 text-slate-600">
                    <FileText className="h-[15px] w-[15px]" />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[13px] text-slate-700">
                    {it.file.name}
                  </span>
                  {it.uploading && <Spinner className="h-4 w-4 flex-none" />}
                  {!it.uploading && !it.error && (
                    <span className="flex flex-none items-center gap-1 text-xs text-emerald-600">
                      <Check className="h-3.5 w-3.5" />
                      已上传
                    </span>
                  )}
                  {it.error && <span className="flex-none text-xs text-red-600">{it.error}</span>}
                  <button
                    type="button"
                    onClick={() => setPending((prev) => prev.filter((x) => x.id !== it.id))}
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

      {error && (
        <div className="flex items-start gap-2.5 rounded-[10px] border border-red-200 bg-red-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-none text-red-500" />
          <span>{error}</span>
        </div>
      )}

      {/* 我的文件 */}
      <Card>
        <CardHeader>
          <CardIcon>
            <Lock />
          </CardIcon>
          <CardTitle>我的文件</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="py-6 text-center text-[13px] text-slate-400">加载中…</p>
          ) : ownFiles.length === 0 ? (
            <p className="py-6 text-center text-[13px] text-slate-400">
              还没有上传文件，从上方开始上传吧。
            </p>
          ) : (
            <ul className="space-y-2">{ownFiles.map(renderFileRow)}</ul>
          )}
        </CardContent>
      </Card>

      {/* 已保存的公开池引用 */}
      <Card>
        <CardHeader>
          <CardIcon>
            <Globe />
          </CardIcon>
          <CardTitle>已保存的共享文件</CardTitle>
        </CardHeader>
        <CardContent>
          {savedFiles.length === 0 ? (
            <p className="py-6 text-center text-[13px] text-slate-400">
              去{' '}
              <Link to="/pool" className="text-slate-900 underline decoration-slate-300">
                公开池
              </Link>{' '}
              搜索并保存合适的资料（引用级保存，不占空间）。
            </p>
          ) : (
            <ul className="space-y-2">{savedFiles.map(renderFileRow)}</ul>
          )}
        </CardContent>
      </Card>

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex items-center justify-center gap-3">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={page <= 1}
            onClick={() => void refresh(page - 1)}
          >
            <ChevronLeft className="h-3.5 w-3.5" /> 上一页
          </Button>
          <span className="text-[12.5px] tabular-nums text-slate-500">
            {page} / {totalPages}
          </span>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={page >= totalPages}
            onClick={() => void refresh(page + 1)}
          >
            下一页 <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      )}

      {previewId && <FilePreview uploadId={previewId} onClose={() => setPreviewId(null)} />}
    </div>
  )
}
