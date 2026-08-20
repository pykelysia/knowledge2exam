import { cn } from '@/lib/utils'

export function Spinner({ className }: { className?: string }) {
  return (
    <div
      className={cn(
        'h-5 w-5 animate-spin rounded-full border-2 border-slate-300 border-t-slate-900',
        className,
      )}
      role="status"
      aria-label="加载中"
    />
  )
}
