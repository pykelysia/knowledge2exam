import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import type { ErrorResponse, UserResponse } from '@/api/types'

// 所有端点前缀 /api/v1。开发环境经 Vite 代理转发（见 vite.config.ts）。
const BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1'

export const client = axios.create({
  baseURL: BASE_URL,
  timeout: 30_000,
  withCredentials: true,  // Cookie 认证必须携带凭证
})

/** 后端统一错误结构（openapi.yaml ErrorResponse） */
export interface ApiError {
  error_code: string
  message: string
  detail?: Record<string, unknown>
}

/** 会话彻底失效（refresh 也失败）时广播的事件名，AuthProvider 负责清理本地状态。 */
export const SESSION_EXPIRED_EVENT = 'k2e:session-expired'

/** 从 axios 错误中提取后端错误体，非后端错误返回 null。 */
export function extractApiError(err: unknown): ApiError | null {
  if (axios.isAxiosError<ErrorResponse>(err) && err.response?.data) {
    return err.response.data as ApiError
  }
  return null
}

// 并发的 401 只触发一次 refresh，其余调用共享同一在途请求
// （避免 refresh token 轮换期间被并发重放触发整链吊销）。
let refreshInFlight: Promise<UserResponse> | null = null

/** 用 refresh_token Cookie 续期 access token；返回当前用户信息。 */
export function refreshSession(): Promise<UserResponse> {
  refreshInFlight =
    refreshInFlight ??
    client
      .post<UserResponse>('/auth/refresh')
      .then((res) => res.data)
      .finally(() => {
        refreshInFlight = null
      })
  return refreshInFlight
}

type RetriableConfig = InternalAxiosRequestConfig & { _retried?: boolean }

// 响应拦截：access token 过期（TOKEN_EXPIRED）时自动续期并重放原请求；
// 会话彻底失效时广播 SESSION_EXPIRED_EVENT 让应用清理本地登录状态。
client.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const apiError = extractApiError(error)
    const config = error.config as RetriableConfig | undefined
    const url = config?.url ?? ''
    const isAuthEndpoint = url.includes('/auth/')

    if (
      error.response?.status === 401 &&
      apiError?.error_code === 'TOKEN_EXPIRED' &&
      config &&
      !config._retried &&
      !isAuthEndpoint
    ) {
      config._retried = true
      try {
        await refreshSession()
      } catch (refreshError) {
        window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT))
        return Promise.reject(refreshError)
      }
      return client.request(config)
    }

    // 非 TOKEN_EXPIRED 的 401（如 token 无效/被吊销）出现在业务接口上，
    // 意味着会话已不可用
    if (error.response?.status === 401 && !isAuthEndpoint) {
      window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT))
    }
    return Promise.reject(error)
  },
)
