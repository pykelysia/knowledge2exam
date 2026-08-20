import { client } from '@/api/client'
import type {
  AuthTokens,
  LoginRequest,
  LogoutRequest,
  RegisterRequest,
  RefreshRequest,
  UserResponse,
} from '@/api/types'

export async function register(payload: RegisterRequest): Promise<UserResponse> {
  const { data } = await client.post<UserResponse>('/auth/register', payload)
  return data
}

export async function login(payload: LoginRequest): Promise<AuthTokens> {
  const { data } = await client.post<AuthTokens>('/auth/login', payload)
  return data
}

export async function refresh(payload: RefreshRequest): Promise<AuthTokens> {
  const { data } = await client.post<AuthTokens>('/auth/refresh', payload)
  return data
}

export async function logout(payload: LogoutRequest): Promise<void> {
  await client.post('/auth/logout', payload)
}
