// 双 JWT 令牌存储。
// - access token：仅存内存（模块级变量），不落地，满足 NFR-8「前端内存不落地」。
// - refresh token：存 localStorage（30 天长时效），服务端仅存哈希。
//
// 页面刷新后 access token 丢失，但 refresh token 仍在；后续受保护请求触发
// 401 时会自动用 refresh token 透明续期，用户无需重新登录。

const REFRESH_TOKEN_KEY = 'k2e.refresh_token'

let accessToken: string | null = null

export function getAccessToken(): string | null {
  return accessToken
}

export function setAccessToken(token: string | null): void {
  accessToken = token
}

export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_TOKEN_KEY)
}

export function setRefreshToken(token: string | null): void {
  if (token) {
    localStorage.setItem(REFRESH_TOKEN_KEY, token)
  } else {
    localStorage.removeItem(REFRESH_TOKEN_KEY)
  }
}

export function clearTokens(): void {
  accessToken = null
  localStorage.removeItem(REFRESH_TOKEN_KEY)
}

export function hasRefreshToken(): boolean {
  return getRefreshToken() !== null
}
