import { client, refreshSession } from '@/api/client'
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

// access token 过期由 client 的响应拦截器调用 refreshSession 自动续期并重放。
export { refreshSession as refresh }

export async function logout(): Promise<void> {
  await client.post('/auth/logout')
}
