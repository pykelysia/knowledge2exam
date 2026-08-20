import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import {
  getAccessToken,
  getRefreshToken,
  setAccessToken,
  setRefreshToken,
  clearTokens,
} from '@/lib/token'
import type { AuthTokens, ErrorResponse } from '@/api/types'

// 所有端点前缀 /api/v1（api.md 第 1 节）。开发环境经 Vite 代理转发（见 vite.config.ts）。
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

export const client = axios.create({
  baseURL: BASE_URL,
  timeout: 30_000,
})

// 请求拦截：自动附带 access token。
client.interceptors.request.use((config) => {
  const token = getAccessToken()
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

/** 后端统一错误结构（openapi.yaml ErrorResponse） */
export interface ApiError {
  error_code: string
  message: string
  detail?: Record<string, unknown>
}

/** 从 axios 错误中提取后端错误体，非后端错误返回 null。 */
export function extractApiError(err: unknown): ApiError | null {
  if (axios.isAxiosError<ErrorResponse>(err) && err.response?.data) {
    return err.response.data as ApiError
  }
  return null
}

type RefreshSubscriber = (token: string) => void

let isRefreshing = false
let refreshWaiters: RefreshSubscriber[] = []

function onRefreshed(token: string) {
  refreshWaiters.forEach((cb) => cb(token))
  refreshWaiters = []
}

function onRefreshFailed() {
  refreshWaiters.forEach((cb) => cb(''))
  refreshWaiters = []
}

/** 通知外部（React 层）登出——由 useAuth 挂载，避免 API 层直接依赖 React。 */
let onSessionExpired: (() => void) | null = null
export function setSessionExpiredHandler(handler: (() => void) | null) {
  onSessionExpired = handler
}

/**
 * 用 refresh token 换取新 access token，并轮换 refresh token。
 * 失败时清除本地令牌并通知登出。
 */
async function doRefresh(): Promise<string> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) {
    throw new Error('NO_REFRESH_TOKEN')
  }
  try {
    const { data } = await axios.post<AuthTokens>(`${BASE_URL}/auth/refresh`, {
      refresh_token: refreshToken,
    })
    setAccessToken(data.access_token)
    setRefreshToken(data.refresh_token)
    return data.access_token
  } catch (err) {
    clearTokens()
    onSessionExpired?.()
    throw err
  }
}

// 响应拦截：401（TOKEN_EXPIRED / TOKEN_MISSING）时自动续期并重放原请求。
// 并发 401 只触发一次刷新（单飞），其余请求排队等待新 token。
client.interceptors.response.use(
  (res) => res,
  async (error: AxiosError) => {
    const config = error.config as
      | (InternalAxiosRequestConfig & { _retried?: boolean })
      | undefined

    if (!config || error.response?.status !== 401) {
      return Promise.reject(error)
    }

    // 刷新端点自身失败时不再重试（避免死循环），交由上层处理。
    if (config.url?.includes('/auth/refresh')) {
      return Promise.reject(error)
    }

    // 已是重放后的请求仍失败，直接抛出，避免无限重试。
    if (config._retried) {
      return Promise.reject(error)
    }
    config._retried = true

    if (isRefreshing) {
      // 等待正在进行的刷新完成。
      const token = await new Promise<string>((resolve) => {
        refreshWaiters.push(resolve)
      })
      if (!token) return Promise.reject(error)
      config.headers.Authorization = `Bearer ${token}`
      return client(config)
    }

    isRefreshing = true
    try {
      const token = await doRefresh()
      onRefreshed(token)
      config.headers.Authorization = `Bearer ${token}`
      return client(config)
    } catch (refreshErr) {
      onRefreshFailed()
      return Promise.reject(refreshErr)
    } finally {
      isRefreshing = false
    }
  },
)
