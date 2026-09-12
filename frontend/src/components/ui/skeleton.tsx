import { cn } from '@/lib/utils'

export function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        'animate-shimmer rounded-md',
        'bg-[linear-gradient(90deg,var(--color-slate-200)_25%,var(--color-slate-100)_45%,var(--color-slate-200)_65%)] bg-[length:200%_100%]',
        className,
      )}
      {...props}
    />
  )
}
