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
import { setSessionExpiredHandler } from '@/api/client'
import {
  clearTokens,
  getRefreshToken,
  setAccessToken,
  setRefreshToken,
} from '@/lib/token'
import type { LoginRequest, RegisterRequest, User } from '@/api/types'

interface AuthContextValue {
  /** 当前登录用户；未登录或刷新后尚未恢复时为 null。 */
  user: User | null
  /** 是否有 access token（内存）。 */
  isAuthenticated: boolean
  /** 正在尝试恢复/续期会话。 */
  initializing: boolean
  login: (payload: LoginRequest) => Promise<void>
  register: (payload: RegisterRequest) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [initializing, setInitializing] = useState(true)

  const logout = useCallback(async () => {
    const refreshToken = getRefreshToken()
    // 尽力通知后端吊销；失败也照常清理本地状态。
    if (refreshToken) {
      try {
        await apiLogout({ refresh_token: refreshToken })
      } catch {
        // 忽略——本地登出优先。
      }
    }
    clearTokens()
    setUser(null)
  }, [])

  // 会话过期（refresh 失败 / 重放）时的兜底登出。
  useEffect(() => {
    setSessionExpiredHandler(() => {
      clearTokens()
      setUser(null)
    })
    return () => setSessionExpiredHandler(null)
  }, [])

  // 应用启动：若有 refresh token，说明此前登录过；但 user 信息无法从 token 中可靠还原，
  // 这里仅清空 access token，让首次受保护请求触发透明续期。
  // 更稳妥的做法是启动时主动 refresh 一次以恢复 user 信息——见下方 effect。
  useEffect(() => {
    const refreshToken = getRefreshToken()
    if (!refreshToken) {
      setInitializing(false)
      return
    }
    // 主动续期一次，既能恢复 user，又能尽早发现 refresh token 已失效。
    let cancelled = false
    ;(async () => {
      try {
        const tokens = await apiRefresh({ refresh_token: refreshToken })
        if (cancelled) return
        setAccessToken(tokens.access_token)
        setRefreshToken(tokens.refresh_token)
        if (tokens.user) setUser(tokens.user)
      } catch {
        if (!cancelled) {
          clearTokens()
          setUser(null)
        }
      } finally {
        if (!cancelled) setInitializing(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [])

  const login = useCallback(async (payload: LoginRequest) => {
    const tokens = await apiLogin(payload)
    setAccessToken(tokens.access_token)
    setRefreshToken(tokens.refresh_token)
    if (tokens.user) setUser(tokens.user)
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
