import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from 'react'
import { login as apiLogin, logout as apiLogout, register as apiRegister } from '@/api/auth'
import type { LoginRequest, RegisterRequest, User } from '@/api/types'

interface AuthContextValue {
  /** 当前登录用户；未登录时为 null。 */
  user: User | null
  /** 是否已认证。 */
  isAuthenticated: boolean
  login: (payload: LoginRequest) => Promise<void>
  register: (payload: RegisterRequest) => Promise<void>
  logout: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)

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
    const user = await apiLogin(payload)
    setUser(user)
  }, [])

  const register = useCallback(async (payload: RegisterRequest) => {
    await apiRegister(payload)
    // 注册成功仅返回用户信息，不自动登录；由调用方决定是否跳转登录。
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      user,
      isAuthenticated: user !== null,
      login,
      register,
      logout,
    }),
    [user, login, register, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
