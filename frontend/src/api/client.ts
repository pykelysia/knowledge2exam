import axios, { AxiosError, type InternalAxiosRequestConfig } from 'axios'
import type { ErrorResponse } from '@/api/types'

// 所有端点前缀 /api/v1（api.md 第 1 节）。开发环境经 Vite 代理转发（见 vite.config.ts）。
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

/** 从 axios 错误中提取后端错误体，非后端错误返回 null。 */
export function extractApiError(err: unknown): ApiError | null {
  if (axios.isAxiosError<ErrorResponse>(err) && err.response?.data) {
    return err.response.data as ApiError
  }
  return null
}

// 响应拦截：统一错误处理（无需自动刷新——Cookie 由浏览器自动管理）。
client.interceptors.response.use(
  (res) => res,
  (error: AxiosError) => {
    // 不再处理 401 自动刷新（Cookie 模式下刷新由后端透明处理）
    return Promise.reject(error)
  },
)
