import { cn } from '@/lib/utils'

export function Card({
  className,
  hover,
  ...props
}: React.HTMLAttributes<HTMLDivElement> & { hover?: boolean }) {
  return (
    <div
      className={cn(
        'rounded-[14px] border border-slate-200 bg-white shadow-card',
        hover &&
          'transition-[border-color,box-shadow,transform] duration-200 hover:-translate-y-px hover:border-slate-300 hover:shadow-card-hover',
        className,
      )}
      {...props}
    />
  )
}

export function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('flex items-center gap-2.5 px-5 pt-[18px] pb-3.5', className)} {...props} />
}

export function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return <h3 className={cn('text-[15px] font-semibold tracking-[-0.01em] text-slate-900', className)} {...props} />
}

export function CardIcon({ className, ...props }: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      className={cn(
        'grid h-[27px] w-[27px] flex-none place-items-center rounded-lg bg-slate-100 text-slate-600',
        '[&>svg]:h-[14.5px] [&>svg]:w-[14.5px]',
        className,
      )}
      {...props}
    />
  )
}

export function CardContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn('px-5 pb-5', className)} {...props} />
}
