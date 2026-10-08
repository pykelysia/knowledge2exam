import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { getPreview } from '@/api/files'
import { extractApiError } from '@/api/client'
import type { UploadPreview } from '@/api/types'
import { MarkdownContent } from '@/components/MarkdownContent'
import { Badge } from '@/components/ui/badge'
import { Spinner } from '@/components/ui/spinner'
import { SOURCE_TYPE_META, VISIBILITY_META, errorMessage } from '@/lib/constants'

interface FilePreviewProps {
  uploadId: string
  onClose: () => void
}

/** 文件预览弹层：基于解析产物文本渲染（个人库与公开池共用）。 */
export function FilePreview({ uploadId, onClose }: FilePreviewProps) {
  const [preview, setPreview] = useState<UploadPreview | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    getPreview(uploadId)
      .then((data) => {
        if (!cancelled) setPreview(data)
      })
      .catch((err) => {
        if (cancelled) return
        const apiErr = extractApiError(err)
        setError(apiErr ? errorMessage(apiErr.error_code) : '预览加载失败')
      })
    return () => {
      cancelled = true
    }
  }, [uploadId])

  // Esc 关闭
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 p-4 backdrop-blur-[2px]"
      onClick={onClose}
    >
      <div
        className="flex max-h-[85vh] w-full max-w-3xl flex-col rounded-2xl border border-slate-200 bg-white shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-start justify-between gap-4 border-b border-slate-100 px-5 py-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="truncate text-[15px] font-semibold text-slate-900">
                {preview?.filename || '文件预览'}
              </h3>
              {preview && (
                <>
                  <Badge>{SOURCE_TYPE_META[preview.source_type].label}</Badge>
                  <Badge>{VISIBILITY_META[preview.visibility].label}</Badge>
                </>
              )}
            </div>
            {preview && (
              <p className="mt-1 text-[12.5px] text-slate-500">
                {[preview.school_name, preview.course_name].filter(Boolean).join(' · ') ||
                  '未设置学校/课程标签'}
                {preview.char_count != null && ` · 约 ${preview.char_count} 字`}
              </p>
            )}
          </div>
          <button
            onClick={onClose}
            className="rounded-lg p-1.5 text-slate-500 transition-colors hover:bg-slate-100 hover:text-slate-900"
            aria-label="关闭预览"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        <div className="min-h-[200px] flex-1 overflow-y-auto px-5 py-4">
          {error && <p className="text-[13px] text-red-600">{error}</p>}
          {!error && !preview && (
            <div className="flex h-full items-center justify-center py-10">
              <Spinner />
            </div>
          )}
          {preview && (
            <>
              <MarkdownContent content={preview.text} />
              {preview.truncated && (
                <p className="mt-4 rounded-lg bg-amber-50 px-3 py-2 text-[12.5px] text-amber-700">
                  内容过长，预览已截断——完整内容请在出卷时由系统检索使用。
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
