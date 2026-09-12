import { useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useAuth } from '@/hooks/useAuth'
import { AuthShell } from '@/components/AuthShell'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { extractApiError } from '@/api/client'
import { errorMessage } from '@/lib/constants'

export function Register() {
  const { register } = useAuth()
  const navigate = useNavigate()

  const [email, setEmail] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    if (password.length < 8) {
      setError('密码至少 8 位')
      return
    }
    setSubmitting(true)
    try {
      await register({ email, username, password })
      // 注册成功跳转登录页（注册不自动登录）。
      navigate('/login')
    } catch (err) {
      const apiErr = extractApiError(err)
      setError(apiErr ? errorMessage(apiErr.error_code) : '注册失败，请稍后重试')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <AuthShell title="注册" subtitle="创建新账号">
      <form onSubmit={handleSubmit} className="space-y-3.5">
        <div>
          <Label htmlFor="email">邮箱</Label>
          <Input
            id="email"
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="user@example.com"
            autoComplete="email"
            required
          />
        </div>
        <div>
          <Label htmlFor="username">用户名</Label>
          <Input
            id="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            placeholder="1~32 字符"
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
            autoComplete="new-password"
            required
          />
        </div>
        {error && <p className="text-[13px] text-red-600">{error}</p>}
        <Button type="submit" className="w-full" disabled={submitting}>
          {submitting ? '注册中…' : '注册'}
        </Button>
      </form>
      <p className="mt-4 text-center text-[13px] text-slate-600">
        已有账号？{' '}
        <Link
          to="/login"
          className="font-medium text-slate-900 underline decoration-slate-300 underline-offset-[3px] transition-colors hover:decoration-slate-900"
        >
          登录
        </Link>
      </p>
    </AuthShell>
  )
}
