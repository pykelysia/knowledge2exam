import { cn } from '@/lib/utils'

export function Badge({
  variant = 'default',
  pulse = false,
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & {
  variant?: 'default' | 'success' | 'warning' | 'danger'
  /** 运行态状态点呼吸动画（预处理/出题中/渲染中等）。 */
  pulse?: boolean
}) {
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-0.5 text-xs font-medium',
        variant === 'default' && 'bg-slate-100 text-slate-600',
        variant === 'success' && 'bg-emerald-100 text-emerald-700',
        variant === 'warning' && 'bg-amber-100 text-amber-700',
        variant === 'danger' && 'bg-red-100 text-red-700',
        className,
      )}
      {...props}
    >
      <span
        aria-hidden
        className={cn(
          'h-1.5 w-1.5 flex-none rounded-full bg-current opacity-75',
          pulse && 'animate-dot-pulse',
        )}
      />
      {children}
    </span>
  )
}
