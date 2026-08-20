/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** 后端 API 基础地址（仅在直接连后端、不走 Vite 代理时生效） */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
