import { client } from '@/api/client'
import type {
  LoginRequest,
  RegisterRequest,
  UserResponse,
} from '@/api/types'

export async function register(payload: RegisterRequest): Promise<UserResponse> {
  const { data } = await client.post<UserResponse>('/auth/register', payload)
  return data
}

export async function login(payload: LoginRequest): Promise<UserResponse> {
  const { data } = await client.post<UserResponse>('/auth/login', payload)
  return data
}

// Cookie 模式下不再需要主动 refresh——浏览器自动携带 refresh_token cookie
// 后端在 access_token 过期时自动轮换

export async function logout(): Promise<void> {
  await client.post('/auth/logout')
}
