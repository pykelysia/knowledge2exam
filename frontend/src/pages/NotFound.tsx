import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'

export function NotFound() {
  return (
    <div className="grid min-h-screen place-items-center px-5">
      <div className="text-center">
        <p className="font-mono text-5xl font-semibold tracking-tight text-slate-900">404</p>
        <p className="mt-3 text-sm text-slate-500">页面不存在或已被移除</p>
        <Link to="/" className="mt-6 inline-block">
          <Button>返回首页</Button>
        </Link>
      </div>
    </div>
  )
}
