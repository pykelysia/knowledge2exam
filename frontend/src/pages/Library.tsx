import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import {
  AlertCircle,
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
import { Select } from '@/components/ui/select'
import { Card, CardContent, CardHeader, CardIcon, CardTitle } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { Badge } from '@/components/ui/badge'
import { FilePreview } from '@/components/FilePreview'
import { uploadFile, patchUpload, deleteUpload } from '@/api/uploads'
import { listMyFiles, unsavePoolFile } from '@/api/files'
import { listCourses, listSchools } from '@/api/schools'
import { extractApiError } from '@/api/client'
import { useAuth } from '@/hooks/useAuth'
import type {
  LibraryFile,
  School,
  Course,
  SourceTypeFile,
  Visibility,
} from '@/api/types'
import {
  FILE_SOURCE_TYPES,
  FILE_STATUS_META,
  SOURCE_TYPE_META,
  VISIBILITY_META,
  errorMessage,
  isAllowedExtension,
} from '@/lib/constants'

/** 待上传队列项：选择后先配置标签，点击上传按钮才真正提交。 */
interface PendingItem {
  id: string
  file: File
  sourceType: SourceTypeFile
  visibility: Visibility
  courseId: string
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
  const { user } = useAuth()
  const [files, setFiles] = useState<LibraryFile[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [previewId, setPreviewId] = useState<string | null>(null)

  const [pending, setPending] = useState<PendingItem[]>([])
  const [dragging, setDragging] = useState(false)
  const [uploadingAny, setUploadingAny] = useState(false)
  // 学校标签默认携带注册学校，用户可选择「不设置」（不透露/无需学校归属）
  const [attachSchool, setAttachSchool] = useState(true)

  const [schools, setSchools] = useState<School[]>([])
  const [courses, setCourses] = useState<Course[]>([])

  // 过期响应守卫：翻页/轮询并发时，丢弃迟到的旧响应
  const reqSeq = useRef(0)

  // 学校标签锁定为注册学校；未设置则不可使用本校共享
  const userSchoolId = user?.school_id ?? null
  const userSchoolName = schools.find((s) => s.school_id === userSchoolId)?.name ?? null

  const schoolName = (id?: string | null) => schools.find((s) => s.school_id === id)?.name
  const courseName = (id?: string | null) => courses.find((c) => c.course_id === id)?.name

  const refresh = useCallback(async (targetPage: number, silent = false) => {
    const current = ++reqSeq.current
    try {
      const res = await listMyFiles(targetPage, PAGE_SIZE)
      if (current !== reqSeq.current) return // 已有更新请求，丢弃过期响应
      setFiles(res.files)
      setTotal(res.total)
      setPage(res.page)
      // 当前页被删空时回退一页并重新拉取，避免停留在空视图
      if (res.files.length === 0 && res.page > 1) {
        await refresh(res.page - 1, silent)
      }
    } catch (err) {
      if (!silent && current === reqSeq.current) {
        const apiErr = extractApiError(err)
        setError(apiErr ? errorMessage(apiErr.error_code) : '加载失败，请稍后再试')
      }
    } finally {
      if (current === reqSeq.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    listSchools()
      .then((res) => setSchools(res.schools))
      .catch(() => setSchools([]))
    listCourses()
      .then((res) => setCourses(res.courses))
      .catch(() => setCourses([]))
    void refresh(1)
  }, [refresh])

  // 有解析中的文件时轮询刷新（保持当前页；轮询失败静默）
  const anyParsing = files.some((f) => f.status === 'parsing')
  useEffect(() => {
    if (!anyParsing) return
    const timer = setInterval(() => void refresh(page, true), 3000)
    return () => clearInterval(timer)
  }, [anyParsing, refresh, page])

  function updatePending(id: string, patch: Partial<PendingItem>) {
    setPending((prev) => prev.map((it) => (it.id === id ? { ...it, ...patch } : it)))
  }

  /** 切换是否携带学校标签：切到「不设置」时重置已选本校共享的文件为私有。 */
  function toggleAttachSchool(attach: boolean) {
    setAttachSchool(attach)
    if (!attach) {
      setPending((prev) =>
        prev.map((it) =>
          it.visibility === 'school' ? { ...it, visibility: 'private' } : it,
        ),
      )
    }
  }

  function addFiles(list: FileList | File[]) {
    setError(null)
    const next: PendingItem[] = []
    Array.from(list).forEach((file) => {
      const item: PendingItem = {
        id: nextId(),
        file,
        sourceType: 'book',
        visibility: 'private',
        courseId: '',
        uploading: false,
      }
      if (!isAllowedExtension(file.name)) {
        item.error = '不支持的文件格式'
      }
      next.push(item)
    })
    setPending((prev) => [...prev, ...next])
  }

  /** 显式上传：逐文件按各自标签提交，成功项移出队列并刷新个人库。 */
  async function handleUploadAll() {
    setError(null)
    setUploadingAny(true)
    try {
      for (const item of pending) {
        if (item.error) continue
        updatePending(item.id, { uploading: true, error: undefined })
        try {
          await uploadFile(
            item.file,
            item.sourceType,
            item.visibility,
            attachSchool ? userSchoolId : null,
            item.courseId || null,
          )
          setPending((prev) => prev.filter((x) => x.id !== item.id))
        } catch (err) {
          const apiErr = extractApiError(err)
          updatePending(item.id, {
            uploading: false,
            error: apiErr ? errorMessage(apiErr.error_code) : '上传失败',
          })
        }
      }
      // 新文件按创建时间排最前，回第一页立即可见
      await refresh(1)
    } finally {
      setUploadingAny(false)
    }
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
  const uploadableCount = pending.filter((it) => !it.error && !it.uploading).length

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
              aria-label={`预览 ${file.filename || '未命名文件'}`}
            >
              <Eye className="h-4 w-4" />
            </button>
          )}
          {file.imported ? (
            <button
              type="button"
              onClick={() => void handleUnsave(file)}
              className="grid h-7 w-7 place-items-center rounded-[7px] text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600"
              aria-label={`取消保存 ${file.filename || '未命名文件'}`}
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
                aria-label={`修改可见性：${file.filename || '未命名文件'}`}
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
                aria-label={`删除 ${file.filename || '未命名文件'}`}
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
          {/* 学校标签：默认注册学校，可选择不设置（对本批文件生效） */}
          <div className="flex flex-wrap items-center gap-2.5 rounded-[10px] border border-slate-200 bg-slate-50/60 px-3.5 py-2.5">
            <span className="text-[12.5px] text-slate-500">学校标签：</span>
            {userSchoolId ? (
              <>
                <Select
                  value={attachSchool ? 'school' : 'none'}
                  onChange={(e) => toggleAttachSchool(e.target.value === 'school')}
                  className="w-44 px-2 py-1.5 text-[12.5px]"
                  aria-label="学校标签"
                >
                  <option value="school">{userSchoolName}（注册学校）</option>
                  <option value="none">不设置（无学校归属）</option>
                </Select>
                <span className="text-[11.5px] text-slate-400">
                  {attachSchool ? (
                    <>
                      默认携带注册学校；如需更换请先
                      <Link
                        to="/profile"
                        className="mx-0.5 text-slate-700 underline decoration-slate-300 hover:decoration-slate-900"
                      >
                        修改个人资料
                      </Link>
                    </>
                  ) : (
                    '本批文件不带学校归属，也不能使用「本校共享」'
                  )}
                </span>
              </>
            ) : (
              <span className="text-[11.5px] text-amber-600">
                未设置学校：上传不带学校标签，也不能使用「本校共享」；可在
                <Link
                  to="/profile"
                  className="mx-0.5 underline decoration-amber-300 hover:decoration-amber-600"
                >
                  个人资料
                </Link>
                中设置
              </span>
            )}
          </div>

          <label
            className={`group flex cursor-pointer flex-col items-center justify-center rounded-xl border-[1.5px] border-dashed px-4 py-[26px] text-center transition-[border-color,background-color,box-shadow] focus-within:ring-4 focus-within:ring-slate-900/10 ${
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
              选择后先配置标签，点击「上传」才开始上传与解析（支持 docx / pptx / pdf / md / txt / 图片）
            </span>
            {/* sr-only：视觉隐藏但保留在 Tab 序与 label 关联内，键盘可达 */}
            <input
              type="file"
              multiple
              className="sr-only"
              accept=".docx,.pptx,.pdf,.md,.txt,.jpg,.jpeg,.png,.webp,.bmp"
              onChange={(e) => {
                if (e.target.files) addFiles(e.target.files)
                e.target.value = ''
              }}
            />
          </label>

          {pending.length > 0 && (
            <div className="space-y-2">
              <ul className="space-y-2">
                {pending.map((it) => {
                  const lockBatch = it.uploading || uploadingAny
                  return (
                    <li
                      key={it.id}
                      className="flex flex-wrap items-center gap-2.5 rounded-[10px] border border-slate-200 bg-white px-3 py-2 max-[720px]:row-gap-2"
                    >
                      <span className="grid h-[30px] w-[30px] flex-none place-items-center rounded-lg bg-slate-100 text-slate-600">
                        <FileText className="h-[15px] w-[15px]" />
                      </span>
                      <span className="min-w-0 flex-1 truncate text-[13px] text-slate-700">
                        {it.file.name}
                      </span>
                      <Select
                        value={it.sourceType}
                        onChange={(e) =>
                          updatePending(it.id, {
                            sourceType: e.target.value as SourceTypeFile,
                          })
                        }
                        className="w-36 flex-none px-2 py-1.5 text-[12px]"
                        aria-label={`内容类型：${it.file.name}`}
                        disabled={lockBatch}
                      >
                        {FILE_SOURCE_TYPES.map((t) => (
                          <option key={t} value={t}>
                            {SOURCE_TYPE_META[t].label}
                          </option>
                        ))}
                      </Select>
                      <Select
                        value={it.visibility}
                        onChange={(e) =>
                          updatePending(it.id, { visibility: e.target.value as Visibility })
                        }
                        className="w-[118px] flex-none px-2 py-1.5 text-[12px]"
                        aria-label={`可见性：${it.file.name}`}
                        disabled={lockBatch}
                      >
                        <option value="private">私有</option>
                        <option value="school" disabled={!userSchoolId || !attachSchool}>
                          本校共享
                        </option>
                        <option value="public">公开池</option>
                      </Select>
                      <Select
                        value={it.courseId}
                        onChange={(e) => updatePending(it.id, { courseId: e.target.value })}
                        className="w-32 flex-none px-2 py-1.5 text-[12px]"
                        aria-label={`课程标签：${it.file.name}`}
                        disabled={lockBatch}
                      >
                        <option value="">课程：不设置</option>
                        {courses.map((c) => (
                          <option key={c.course_id} value={c.course_id}>
                            {c.name}
                          </option>
                        ))}
                      </Select>
                      {it.uploading && <Spinner className="h-4 w-4 flex-none" />}
                      {it.error && (
                        <span className="flex-none text-xs text-red-600">{it.error}</span>
                      )}
                      <button
                        type="button"
                        onClick={() =>
                          setPending((prev) => prev.filter((x) => x.id !== it.id))
                        }
                        className="grid h-7 w-7 flex-none place-items-center rounded-[7px] text-slate-400 transition-colors hover:bg-red-50 hover:text-red-600"
                        aria-label={`移除 ${it.file.name}`}
                        disabled={lockBatch}
                      >
                        <Trash2 className="h-4 w-4" />
                      </button>
                    </li>
                  )
                })}
              </ul>
              <div className="flex items-center gap-3">
                <Button
                  type="button"
                  disabled={uploadableCount === 0 || uploadingAny}
                  onClick={() => void handleUploadAll()}
                >
                  {uploadingAny ? (
                    <>
                      <Spinner className="h-4 w-4" /> 正在上传…
                    </>
                  ) : (
                    <>
                      <UploadIcon className="h-4 w-4" /> 上传 {uploadableCount} 个文件
                    </>
                  )}
                </Button>
                <span className="text-[12px] text-slate-400">
                  确认每个文件的内容类型与可见性后开始上传，上传后立即自动解析
                </span>
              </div>
            </div>
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

      {previewId && (
        <FilePreview
          key={previewId}
          uploadId={previewId}
          onClose={() => setPreviewId(null)}
        />
      )}
    </div>
  )
}
