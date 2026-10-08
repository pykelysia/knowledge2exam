import { useCallback, useEffect, useState, type FormEvent } from 'react'
import {
  AlertCircle,
  BookmarkCheck,
  BookmarkPlus,
  ChevronLeft,
  ChevronRight,
  Eye,
  FileText,
  Search,
  Sparkles,
} from 'lucide-react'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardIcon, CardTitle } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { Badge } from '@/components/ui/badge'
import { FilePreview } from '@/components/FilePreview'
import { listPoolFiles, savePoolFile, unsavePoolFile } from '@/api/files'
import { listCourses, listSchools } from '@/api/schools'
import { extractApiError } from '@/api/client'
import type { Course, PoolFile, School, SourceType } from '@/api/types'
import { FILE_SOURCE_TYPES, SOURCE_TYPE_META, VISIBILITY_META, errorMessage } from '@/lib/constants'

const PAGE_SIZE = 20

export function Pool() {
  const [files, setFiles] = useState<PoolFile[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [previewId, setPreviewId] = useState<string | null>(null)

  const [query, setQuery] = useState('')
  const [appliedQuery, setAppliedQuery] = useState('')
  const [schoolId, setSchoolId] = useState('')
  const [courseId, setCourseId] = useState('')
  const [sourceType, setSourceType] = useState('')

  const [schools, setSchools] = useState<School[]>([])
  const [courses, setCourses] = useState<Course[]>([])

  const load = useCallback(
    async (targetPage: number) => {
      setLoading(true)
      setError(null)
      try {
        const res = await listPoolFiles({
          q: appliedQuery || undefined,
          school_id: schoolId || undefined,
          course_id: courseId || undefined,
          source_type: (sourceType || undefined) as SourceType | undefined,
          page: targetPage,
          page_size: PAGE_SIZE,
        })
        setFiles(res.files)
        setTotal(res.total)
        setPage(res.page)
      } catch (err) {
        const apiErr = extractApiError(err)
        setError(apiErr ? errorMessage(apiErr.error_code) : '加载失败，请稍后再试')
      } finally {
        setLoading(false)
      }
    },
    [appliedQuery, schoolId, courseId, sourceType],
  )

  useEffect(() => {
    listSchools()
      .then((res) => setSchools(res.schools))
      .catch(() => setSchools([]))
    listCourses()
      .then((res) => setCourses(res.courses))
      .catch(() => setCourses([]))
  }, [])

  useEffect(() => {
    void load(1)
  }, [load])

  async function handleSave(file: PoolFile) {
    const optimistic = (saved: boolean) =>
      setFiles((prev) =>
        prev.map((f) => (f.upload_id === file.upload_id ? { ...f, is_saved: saved } : f)),
      )
    try {
      if (file.is_saved) {
        await unsavePoolFile(file.upload_id)
        optimistic(false)
      } else {
        await savePoolFile(file.upload_id)
        optimistic(true)
      }
    } catch (err) {
      const apiErr = extractApiError(err)
      setError(apiErr ? errorMessage(apiErr.error_code) : '操作失败，请稍后再试')
    }
  }

  function handleSearch(e: FormEvent) {
    e.preventDefault()
    setAppliedQuery(query.trim())
  }

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE))

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-slate-900">公开池</h1>
        <p className="mt-1.5 text-[13.5px] text-slate-500">
          搜索本校共享与全局公开的学习资料，保存到个人库后在出卷时使用（引用级保存）。
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardIcon>
            <Search />
          </CardIcon>
          <CardTitle>搜索与筛选</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <form onSubmit={handleSearch} className="flex gap-2">
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="按文件名 / 学校 / 课程搜索，或输入内容语义检索（如：傅里叶变换习题）"
            />
            <Button type="submit" disabled={loading}>
              {loading ? <Spinner className="h-4 w-4" /> : <Search className="h-4 w-4" />}
              搜索
            </Button>
          </form>
          <div className="grid grid-cols-3 gap-4 max-[720px]:grid-cols-1">
            <div>
              <Label htmlFor="pool-school">学校</Label>
              <Select id="pool-school" value={schoolId} onChange={(e) => setSchoolId(e.target.value)}>
                <option value="">全部学校</option>
                {schools.map((s) => (
                  <option key={s.school_id} value={s.school_id}>
                    {s.name}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="pool-course">课程</Label>
              <Select id="pool-course" value={courseId} onChange={(e) => setCourseId(e.target.value)}>
                <option value="">全部课程</option>
                {courses.map((c) => (
                  <option key={c.course_id} value={c.course_id}>
                    {c.name}
                    {typeof c.pool_file_count === 'number' && c.pool_file_count > 0
                      ? `（${c.pool_file_count}）`
                      : ''}
                  </option>
                ))}
              </Select>
            </div>
            <div>
              <Label htmlFor="pool-type">类型</Label>
              <Select id="pool-type" value={sourceType} onChange={(e) => setSourceType(e.target.value)}>
                <option value="">全部类型</option>
                {FILE_SOURCE_TYPES.map((t) => (
                  <option key={t} value={t}>
                    {SOURCE_TYPE_META[t].label}
                  </option>
                ))}
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      {error && (
        <div className="flex items-start gap-2.5 rounded-[10px] border border-red-200 bg-red-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-red-700">
          <AlertCircle className="mt-0.5 h-4 w-4 flex-none text-red-500" />
          <span>{error}</span>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardIcon>
            <FileText />
          </CardIcon>
          <CardTitle>搜索结果（{total}）</CardTitle>
        </CardHeader>
        <CardContent>
          {loading && files.length === 0 ? (
            <p className="py-8 text-center text-[13px] text-slate-400">搜索中…</p>
          ) : files.length === 0 ? (
            <p className="py-8 text-center text-[13px] text-slate-400">
              没有找到合适的资料，换个关键词或放宽筛选试试。
            </p>
          ) : (
            <ul className="space-y-2">
              {files.map((file) => (
                <li
                  key={file.upload_id}
                  className="flex items-center gap-3 rounded-[10px] border border-slate-200 bg-white px-3 py-2.5 transition-[border-color,box-shadow] hover:border-slate-300 hover:shadow-card max-[720px]:flex-wrap max-[720px]:row-gap-2"
                >
                  <span className="grid h-[30px] w-[30px] flex-none place-items-center rounded-lg bg-slate-100 text-slate-600">
                    <FileText className="h-[15px] w-[15px]" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[13px] font-medium text-slate-800">
                        {file.filename || '未命名文件'}
                      </span>
                      {file.semantic_score != null && (
                        <Badge variant="success">
                          <Sparkles className="mr-1 h-3 w-3" />
                          语义匹配 {(file.semantic_score * 100).toFixed(0)}%
                        </Badge>
                      )}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      <Badge>{SOURCE_TYPE_META[file.source_type].label}</Badge>
                      <Badge>{VISIBILITY_META[file.visibility].label}</Badge>
                      <span className="text-[11.5px] text-slate-400">
                        {file.owner.username}
                        {[file.school_name, file.course_name].filter(Boolean).length > 0 &&
                          ` · ${[file.school_name, file.course_name].filter(Boolean).join(' · ')}`}
                        {file.char_count != null && ` · 约 ${file.char_count} 字`}
                      </span>
                    </div>
                  </div>
                  <div className="flex flex-none items-center gap-1.5">
                    <button
                      type="button"
                      onClick={() => setPreviewId(file.upload_id)}
                      className="grid h-7 w-7 place-items-center rounded-[7px] text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-900"
                      aria-label="预览"
                    >
                      <Eye className="h-4 w-4" />
                    </button>
                    <Button
                      type="button"
                      size="sm"
                      variant={file.is_saved ? 'secondary' : 'primary'}
                      onClick={() => void handleSave(file)}
                    >
                      {file.is_saved ? (
                        <>
                          <BookmarkCheck className="h-3.5 w-3.5" /> 已保存
                        </>
                      ) : (
                        <>
                          <BookmarkPlus className="h-3.5 w-3.5" /> 保存到个人库
                        </>
                      )}
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {totalPages > 1 && (
            <div className="mt-4 flex items-center justify-center gap-3">
              <Button
                type="button"
                size="sm"
                variant="secondary"
                disabled={page <= 1 || loading}
                onClick={() => void load(page - 1)}
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
                disabled={page >= totalPages || loading}
                onClick={() => void load(page + 1)}
              >
                下一页 <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          )}
        </CardContent>
      </Card>

      {previewId && <FilePreview uploadId={previewId} onClose={() => setPreviewId(null)} />}
    </div>
  )
}
