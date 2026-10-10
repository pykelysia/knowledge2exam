import { memo, useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useVirtualizer } from '@tanstack/react-virtual'
import {
  AlertCircle,
  Check,
  Eye,
  FileText,
  Library as LibraryIcon,
  PenLine,
  Plus,
  SlidersHorizontal,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { Card, CardContent, CardHeader, CardIcon, CardTitle } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { Badge } from '@/components/ui/badge'
import { FilePreview } from '@/components/FilePreview'
import { uploadText } from '@/api/uploads'
import { listMyFiles } from '@/api/files'
import { createJob } from '@/api/jobs'
import { extractApiError } from '@/api/client'
import type { LibraryFile } from '@/api/types'
import { FILE_STATUS_META, SOURCE_TYPE_META, errorMessage } from '@/lib/constants'

// 全量拉取分页参数：page_size 必须不大于后端 /files/mine 的 le=200 上限
const DASHBOARD_PAGE_SIZE = 200
// 防御性上限：超过后截断（200×20=4000 个文件封顶）
const MAX_FETCH_PAGES = 20

interface FileSelectRowProps {
  file: LibraryFile
  checked: boolean
  onToggle: (id: string) => void
  onPreview: (id: string) => void
}

/** 单个可选文件行：memo 化，虚拟列表滚动时避免无关行重渲染。 */
const FileSelectRow = memo(function FileSelectRow({
  file,
  checked,
  onToggle,
  onPreview,
}: FileSelectRowProps) {
  const usable = file.status === 'available'
  const displayName = file.filename || '未命名文件'
  return (
    <div
      className={`flex items-center gap-3 rounded-[10px] border px-3 py-2 transition-[border-color,box-shadow] max-[720px]:flex-wrap max-[720px]:row-gap-2 ${
        checked
          ? 'border-slate-900 bg-slate-50/80 shadow-card'
          : 'border-slate-200 bg-white hover:border-slate-300'
      } ${usable ? '' : 'opacity-60'}`}
    >
      <input
        type="checkbox"
        className="h-[15px] w-[15px] flex-none accent-slate-900"
        disabled={!usable}
        checked={checked}
        onChange={() => onToggle(file.upload_id)}
        aria-label={`选择 ${displayName}`}
      />
      <span className="grid h-[30px] w-[30px] flex-none place-items-center rounded-lg bg-slate-100 text-slate-600">
        <FileText className="h-[15px] w-[15px]" />
      </span>
      <div className="min-w-0 flex-1">
        <div className="truncate text-[13px] text-slate-700">
          {displayName}
          {file.imported && (
            <span className="ml-1.5 text-[11.5px] text-slate-400">
              （来自 {file.owner.username}）
            </span>
          )}
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <Badge>{SOURCE_TYPE_META[file.source_type].label}</Badge>
          <Badge variant={FILE_STATUS_META[file.status].variant}>
            {file.status === 'parsing' && <Spinner className="mr-1 h-3 w-3" />}
            {FILE_STATUS_META[file.status].label}
          </Badge>
          {file.char_count != null && file.status === 'available' && (
            <span className="text-[11.5px] text-slate-400">约 {file.char_count} 字</span>
          )}
        </div>
      </div>
      {usable && (
        <button
          type="button"
          onClick={() => onPreview(file.upload_id)}
          className="grid h-7 w-7 flex-none place-items-center rounded-[7px] text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-900"
          aria-label={`预览 ${displayName}`}
        >
          <Eye className="h-4 w-4" />
        </button>
      )}
    </div>
  )
})

export function Dashboard() {
  const navigate = useNavigate()

  const [files, setFiles] = useState<LibraryFile[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [previewId, setPreviewId] = useState<string | null>(null)

  const [manualText, setManualText] = useState('')
  const [extraRequirement, setExtraRequirement] = useState('')
  const [duration, setDuration] = useState(100)
  const [needExplanation, setNeedExplanation] = useState(true)

  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 防重入：轮询 3s 触发时若上一次全量拉取仍在途则跳过，避免请求堆叠
  const refreshingRef = useRef(false)

  const refresh = useCallback(async (silent = false) => {
    if (refreshingRef.current) return
    refreshingRef.current = true
    try {
      // 出卷页需要全部可选文件：循环拉取个人库全部分页
      const all: LibraryFile[] = []
      let current = 1
      for (;;) {
        const res = await listMyFiles(current, DASHBOARD_PAGE_SIZE)
        all.push(...res.files)
        if (all.length >= res.total || current >= MAX_FETCH_PAGES) break
        current += 1
      }
      setFiles(all)
      setLoadError(null)
      // 已删除/不可用的选中项自动移除
      setSelected((prev) => {
        const usable = new Set(
          all.filter((f) => f.status === 'available').map((f) => f.upload_id),
        )
        const next = new Set([...prev].filter((id) => usable.has(id)))
        return next.size === prev.size ? prev : next
      })
    } catch (err) {
      // 轮询失败静默；首屏/用户操作失败给出提示，避免把加载失败伪装成空库
      if (!silent) {
        const apiErr = extractApiError(err)
        setLoadError(
          apiErr ? errorMessage(apiErr.error_code) : '个人库加载失败，请稍后重试',
        )
      }
    } finally {
      refreshingRef.current = false
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  // 有解析中的文件时轮询（静默失败）
  const anyParsing = files.some((f) => f.status === 'parsing')
  useEffect(() => {
    if (!anyParsing) return
    const timer = setInterval(() => void refresh(true), 3000)
    return () => clearInterval(timer)
  }, [anyParsing, refresh])

  const toggleSelected = useCallback((id: string) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }, [])

  const handlePreview = useCallback((id: string) => setPreviewId(id), [])

  const readyCount =
    selected.size + (manualText.trim() ? 1 : 0) + (extraRequirement.trim() ? 1 : 0)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)

    if (readyCount === 0) {
      setError(errorMessage('INPUT_EMPTY'))
      return
    }
    if (duration < 5 || duration > 300) {
      setError(errorMessage('INVALID_DURATION'))
      return
    }

    setSubmitting(true)
    try {
      const uploadIds: string[] = [...selected]

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

      const job = await createJob({
        upload_ids: uploadIds,
        duration_minutes: duration,
        need_explanation: needExplanation,
      })
      navigate(`/jobs/${job.job_id}`)
    } catch (err) {
      const apiErr = extractApiError(err)
      setError(apiErr ? errorMessage(apiErr.error_code) : '创建任务失败，请稍后再试')
    } finally {
      setSubmitting(false)
    }
  }

  const listRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: files.length,
    getScrollElement: () => listRef.current,
    estimateSize: () => 76,
    overscan: 8,
  })

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-slate-900">生成试卷</h1>
        <p className="mt-1.5 text-[13.5px] text-slate-500">
          从个人库选择资料或输入文本，系统据此生成一份模拟试卷；检索只在你选中的文件范围内进行。
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6">
        {/* 从个人库选择 */}
        <Card>
          <CardHeader>
            <CardIcon>
              <LibraryIcon />
            </CardIcon>
            <CardTitle>选择资料（个人库）</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            {loading ? (
              <p className="py-6 text-center text-[13px] text-slate-400">加载中…</p>
            ) : loadError ? (
              <div className="space-y-3 py-6 text-center">
                <p className="text-[13px] text-red-600">{loadError}</p>
                <Button type="button" size="sm" variant="secondary" onClick={() => void refresh()}>
                  重试
                </Button>
              </div>
            ) : files.length === 0 ? (
              <p className="py-6 text-center text-[13px] text-slate-400">
                个人库还是空的，先去{' '}
                <Link
                  to="/library"
                  className="font-medium text-slate-900 underline decoration-slate-300 underline-offset-[3px] hover:decoration-slate-900"
                >
                  上传文件
                </Link>{' '}
                或从{' '}
                <Link
                  to="/pool"
                  className="font-medium text-slate-900 underline decoration-slate-300 underline-offset-[3px] hover:decoration-slate-900"
                >
                  公开池
                </Link>{' '}
                保存资料。
              </p>
            ) : (
              <>
                {/* 虚拟化列表：大库（可达数千条）只渲染可视窗口 */}
                <div ref={listRef} className="max-h-[520px] overflow-y-auto pr-1">
                  <div style={{ height: virtualizer.getTotalSize(), position: 'relative' }}>
                    {virtualizer.getVirtualItems().map((item) => {
                      const file = files[item.index]
                      return (
                        <div
                          key={file.upload_id}
                          data-index={item.index}
                          ref={(el) => virtualizer.measureElement(el)}
                          style={{
                            position: 'absolute',
                            top: 0,
                            left: 0,
                            width: '100%',
                            transform: `translateY(${item.start}px)`,
                          }}
                        >
                          <FileSelectRow
                            file={file}
                            checked={selected.has(file.upload_id)}
                            onToggle={toggleSelected}
                            onPreview={handlePreview}
                          />
                        </div>
                      )
                    })}
                  </div>
                </div>
                <p className="text-[12px] text-slate-400">
                  解析中 / 失效的文件不可选用；新上传的文件解析完成后会自动出现在这里。
                  <Link to="/library" className="ml-1 text-slate-700 underline decoration-slate-300">
                    去个人库管理
                  </Link>
                </p>
              </>
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
                学习笔记 / 重点内容{' '}
                <span className="font-normal text-slate-400">（manual_text）</span>
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
          <Button type="submit" disabled={submitting || readyCount === 0}>
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
          {readyCount > 0 ? (
            <span className="inline-flex items-center gap-1.5 text-[12.5px] text-slate-500">
              <Check className="h-3.5 w-3.5 text-emerald-600" />
              已选 {readyCount} 项输入，可提交生成
            </span>
          ) : (
            <span className="text-[12.5px] text-slate-400">至少选择一个文件或输入一段文本</span>
          )}
        </div>
      </form>

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
