import { useState, type FormEvent } from 'react'
import { Link, useLocation, useNavigate } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { AuthShell } from '@/components/AuthShell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { extractApiError } from '@/api/client'
import { errorMessage } from '@/lib/constants'

export function Login() {
  const { login } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()
  const from = (location.state as { from?: { pathname: string } } | null)?.from?.pathname ?? '/'

  const [account, setAccount] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      // 账号字段含 @ 视为邮箱，否则视为用户名（email 优先）。
      const isEmail = account.includes('@')
      await login({
        email: isEmail ? account : null,
        username: isEmail ? null : account,
        password,
      })
      navigate(from, { replace: true })
    } catch (err) {
      const apiErr = extractApiError(err)
      setError(apiErr ? errorMessage(apiErr.error_code) : '登录失败，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthShell title="登录" subtitle="登录后开始生成试卷">
      <form onSubmit={handleSubmit} className="space-y-3.5">
        <div>
          <Label htmlFor="account">邮箱或用户名</Label>
          <Input
            id="account"
            value={account}
            onChange={(e) => setAccount(e.target.value)}
            placeholder="user@example.com"
            autoComplete="username"
            required
          />
        </div>
        <div>
          <Label htmlFor="password">密码</Label>
          <Input
            id="password"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="至少 8 位"
            autoComplete="current-password"
            required
          />
        </div>
        {error && <p className="text-[13px] text-red-600">{error}</p>}
        <Button type="submit" className="w-full" disabled={submitting}>
          {submitting ? '登录中…' : '登录'}
        </Button>
      </form>
      <p className="mt-4 text-center text-[13px] text-slate-600">
        还没有账号？{' '}
        <Link
          to="/register"
          className="font-medium text-slate-900 underline decoration-slate-300 underline-offset-[3px] transition-colors hover:decoration-slate-900"
        >
          注册
        </Link>
      </p>
    </AuthShell>
  )
}
