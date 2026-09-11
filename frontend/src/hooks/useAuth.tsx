import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { login as apiLogin, logout as apiLogout, refresh as apiRefresh, register as apiRegister } from '@/api/auth'
import { SESSION_EXPIRED_EVENT } from '@/api/client'
import type { LoginRequest, RegisterRequest, User } from '@/api/types'

interface AuthContextValue {
  /** 当前登录用户；未登录时为 null。 */
  user: User | null
  /** 是否已认证。 */
  isAuthenticated: boolean
  /** 会话恢复进行中（刷新页面时用 refresh_token Cookie 探测）。 */
  initializing: boolean
  login: (payload: LoginRequest) => Promise<void>
  register: (payload: RegisterRequest) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [initializing, setInitializing] = useState(true)

  // 挂载时尝试用 refresh_token Cookie 恢复会话，避免刷新页面即被踢回登录页
  useEffect(() => {
    let cancelled = false
    apiRefresh()
      .then((data) => {
        if (!cancelled) setUser(data.user)
      })
      .catch(() => {
        // 无有效会话：保持未登录
      })
      .finally(() => {
        if (!cancelled) setInitializing(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  // 会话彻底失效（refresh 也失败、token 被吊销）时清理本地登录状态
  useEffect(() => {
    const onSessionExpired = () => setUser(null)
    window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired)
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onSessionExpired)
  }, [])

  const logout = useCallback(async () => {
    // 尽力通知后端清除 Cookie；失败也照常清理本地状态。
    try {
      await apiLogout()
    } catch {
      // 忽略——本地登出优先。
    }
    setUser(null)
  }, [])

  const login = useCallback(async (payload: LoginRequest) => {
    const data = await apiLogin(payload)
    setUser(data.user)
  }, [])

  const register = useCallback(async (payload: RegisterRequest) => {
    await apiRegister(payload)
    // 注册成功仅返回用户信息，不自动登录；由调用方决定是否跳转登录。
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isAuthenticated: user !== null,
      initializing,
      login,
      register,
      logout,
    }),
    [user, initializing, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
