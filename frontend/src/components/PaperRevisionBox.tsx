import { useState } from 'react'
import { SendHorizonal, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Spinner } from '@/components/ui/spinner'
import { Textarea } from '@/components/ui/textarea'
import type { TextSelectionAnchor } from '@/hooks/useTextSelection'

interface PaperRevisionBoxProps {
  anchor: TextSelectionAnchor
  rect: DOMRect
  running: boolean
  onClose: () => void
  onSubmit: (feedback: string) => void
}

/** 选区预览的最大长度 */
const PREVIEW_CHARS = 120

/**
 * 划选反馈框：出现在所选位置下方，可关闭。
 * 用户在其中撰写对所选部分的要求，与划选锚点一并提交给完成任务的 agent。
 */
export function PaperRevisionBox({
  anchor,
  rect,
  running,
  onClose,
  onSubmit,
}: PaperRevisionBoxProps) {
  const [feedback, setFeedback] = useState('')

  // 视口内夹取，避免反馈框溢出屏幕
  const width = 460
  const left = Math.min(Math.max(rect.left, 16), Math.max(window.innerWidth - width - 16, 16))
  const top = Math.min(rect.bottom + 8, Math.max(window.innerHeight - 240, 16))

  const preview =
    anchor.text.length > PREVIEW_CHARS ? `${anchor.text.slice(0, PREVIEW_CHARS)}…` : anchor.text

  return (
    <div
      className="fixed z-50 rounded-xl border border-slate-200 bg-white p-3.5 shadow-card-hover"
      style={{ left, top, width }}
      role="form"
      aria-label="划选反馈"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[13px] font-semibold text-slate-900">修订所选内容</span>
        <button
          type="button"
          onClick={onClose}
          className="grid h-6 w-6 place-items-center rounded-md text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600"
          aria-label="关闭反馈框"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      </div>

      <p className="mt-2 line-clamp-2 rounded-lg bg-slate-50 px-2.5 py-1.5 text-[12.5px] leading-[1.6] text-slate-500">
        「{preview}」
      </p>

      <Textarea
        autoFocus
        rows={3}
        value={feedback}
        onChange={(e) => setFeedback(e.target.value)}
        disabled={running}
        placeholder="对选中部分的要求，如：这道题太简单，换一个更综合的考查角度"
        className="mt-2.5 text-[13px]"
        onKeyDown={(e) => {
          if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && feedback.trim() && !running) {
            onSubmit(feedback.trim())
          }
        }}
      />

      <div className="mt-2.5 flex items-center justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={onClose} disabled={running}>
          取消
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={() => onSubmit(feedback.trim())}
          disabled={!feedback.trim() || running}
        >
          {running ? <Spinner className="h-3.5 w-3.5" /> : <SendHorizonal className="h-3.5 w-3.5" />}
          {running ? '修订中…' : '提交修订'}
        </Button>
      </div>
    </div>
  )
}
