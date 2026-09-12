import { cn } from '@/lib/utils'

interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'ghost' | 'ghostDanger' | 'danger'
  size?: 'sm' | 'md'
}

export function Button({
  variant = 'primary',
  size = 'md',
  className,
  ...props
}: ButtonProps) {
  return (
    <button
      className={cn(
        'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg font-medium tracking-[0.01em] transition-all duration-150',
        'active:scale-[0.98] disabled:cursor-not-allowed disabled:opacity-45 disabled:shadow-none',
        'focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-slate-900/15',
        size === 'sm' ? 'h-[31px] rounded-[7px] px-3 text-[12.5px]' : 'h-[37px] px-4 text-[13.5px]',
        variant === 'primary' && 'bg-slate-900 text-white shadow-btn hover:bg-slate-800 active:bg-slate-900',
        variant === 'secondary' &&
          'border border-slate-300 bg-white text-slate-900 hover:border-slate-400 hover:bg-slate-50',
        variant === 'ghost' && 'text-slate-700 hover:bg-slate-100 hover:text-slate-900',
        variant === 'ghostDanger' && 'text-slate-600 hover:bg-red-50 hover:text-red-600',
        variant === 'danger' &&
          'bg-red-600 text-white shadow-[0_1px_2px_rgb(220_38_38/0.25)] hover:bg-red-700',
        className,
      )}
      {...props}
    />
  )
}
