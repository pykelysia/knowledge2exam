import { cn } from '@/lib/utils'

export function Input({ className, ...props }: React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <input
      className={cn(
        'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm transition-[border-color,box-shadow]',
        'placeholder:text-slate-400 hover:border-slate-400 focus:outline-none focus:border-slate-500 focus:ring-4 focus:ring-slate-900/[0.07]',
        className,
      )}
      {...props}
    />
  )
}
