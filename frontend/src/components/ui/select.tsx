import { cn } from '@/lib/utils'

export function Select({ className, ...props }: React.SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select
      className={cn(
        'select-chevron w-full cursor-pointer appearance-none rounded-lg border border-slate-300 bg-white px-3 py-2 pr-9 text-sm transition-[border-color,box-shadow]',
        'hover:border-slate-400 focus:outline-none focus:border-slate-500 focus:ring-4 focus:ring-slate-900/[0.07]',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  )
}
