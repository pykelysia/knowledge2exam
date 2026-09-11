import { useEffect, useRef, useState } from 'react'
import type { JobEvent, JobEventType } from '@/api/types'

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? '/api/v1'

export type EventHandler = (type: JobEventType, data: JobEvent['data'], seq?: number) => void

interface UseJobStreamOptions {
  jobId: string | null
  onEvent: EventHandler
}

/**
 * 订阅任务 SSE 进度流（FR-10）。
 * 用原生 EventSource 读取，因为 axios 不解析流式响应。
 * 断连后依赖浏览器自动重连；Last-Event-ID 由 EventSource 在重连时自动携带。
 */
export function useJobStream({ jobId, onEvent }: UseJobStreamOptions) {
  const [connectionState, setConnectionState] = useState<'idle' | 'connecting' | 'open' | 'closed'>(
    'idle',
  )
  const [error, setError] = useState<string | null>(null)
  const onEventRef = useRef(onEvent)
  onEventRef.current = onEvent

  useEffect(() => {
    if (!jobId) {
      setConnectionState('idle')
      return
    }

    let es: EventSource | null = null
    let disposed = false
    const url = `${BASE_URL}/jobs/${jobId}/events`

    // Cookie 模式下浏览器自动携带认证 Cookie，无需附加 token 查询参数

    setConnectionState('connecting')
    setError(null)

    try {
      es = new EventSource(url)
    } catch {
      setConnectionState('closed')
      setError('无法建立进度连接')
      return
    }

    es.onopen = () => {
      if (!disposed) setConnectionState('open')
    }

    es.onerror = () => {
      if (!disposed) {
        setConnectionState('closed')
        setError('进度连接中断，将自动重连')
      }
    }

    // 每个事件类型各挂一个监听器；data 为 JSON 字符串。
    const eventTypes: JobEventType[] = [
      'stage_changed',
      'plan_ready',
      'question_completed',
      'question_retried',
      'question_replanned',
      'question_abandoned',
      'warning',
      'done',
      'error',
    ]

    const listeners: Array<[string, (e: MessageEvent) => void]> = eventTypes.map((type) => {
      const handler = (e: MessageEvent) => {
        try {
          const raw = JSON.parse(e.data as string) as JobEvent['data'] & { __close__?: boolean }
          // 优先处理关闭信号（不回调 onEvent）
          if (raw.__close__) {
            es?.close()
            return
          }
          const seq = (e as MessageEvent & { lastEventId?: string }).lastEventId
            ? Number((e as MessageEvent & { lastEventId?: string }).lastEventId)
            : undefined
          onEventRef.current(type, raw, Number.isFinite(seq) ? seq : undefined)

          // 收到 done 事件后主动断开连接（取消/完成/失败/部分完成均适用）
          if (type === 'done') {
            es?.close()
          }
        } catch {
          // 忽略无法解析的帧。
        }
      }
      es?.addEventListener(type, handler)
      return [type, handler]
    })

    return () => {
      disposed = true
      listeners.forEach(([type, handler]) => es?.removeEventListener(type, handler))
      es?.close()
      es = null
    }
    // lastEventSeq 不参与依赖，避免重建连接；仅作初始游标记录。
  }, [jobId])

  return { connectionState, error }
}
