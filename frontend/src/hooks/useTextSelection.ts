import { useCallback, useEffect, useRef, useState } from 'react'

/** 划选锚点：选中文本 + 前后文（内容定位，提交给 agent 语义定位源码片段）。 */
export interface TextSelectionAnchor {
  text: string
  before: string
  after: string
}

/** 一次有效划选：锚点 + 选区在视口中的位置（用于定位反馈框）。 */
export interface TextSelectionBox {
  anchor: TextSelectionAnchor
  rect: DOMRect
}

/** 前后文截取长度 */
const CONTEXT_CHARS = 80

/** 取 container 开头到 (node, offset) 的纯文本。 */
function textUpTo(container: Node, node: Node, offset: number): string {
  const range = document.createRange()
  range.selectNodeContents(container)
  try {
    range.setEnd(node, offset)
  } catch {
    return ''
  }
  return range.toString()
}

/** 取 (node, offset) 到 container 结尾的纯文本。 */
function textFrom(container: Node, node: Node, offset: number): string {
  const range = document.createRange()
  range.selectNodeContents(container)
  try {
    range.setStart(node, offset)
  } catch {
    return ''
  }
  return range.toString()
}

/**
 * 监听容器内的文本划选，产出「选中文本 + 前后文锚点 + 视口位置」。
 *
 * 划选文本来自渲染后的 DOM，与 Markdown 源码可能有细微排版差异
 * （加粗符号、列表标记等），由后端 agent 结合上下文语义定位。
 * `enabled` 为 false 时不采集（如任务尚未完成时）。
 *
 * 监听器常驻 document、在事件发生时才解析 container/enabled：
 * 若在 effect 中提前 return（容器尚未挂载），试卷加载后依赖不变、
 * effect 不会重跑，监听器会永久缺失。
 */
export function useTextSelection(
  containerRef: React.RefObject<HTMLElement | null>,
  enabled = true,
) {
  const [selectionBox, setSelectionBox] = useState<TextSelectionBox | null>(null)
  // 事件回调内读取最新值，避免闭包捕获过期状态
  const enabledRef = useRef(enabled)
  enabledRef.current = enabled

  useEffect(() => {
    const onMouseUp = () => {
      if (!enabledRef.current) return
      const container = containerRef.current
      if (!container) return

      const sel = window.getSelection()
      if (!sel || sel.isCollapsed || sel.rangeCount === 0) return
      const range = sel.getRangeAt(0)
      if (!container.contains(range.commonAncestorContainer)) return

      const text = sel.toString().trim()
      if (!text) return

      const rect = range.getBoundingClientRect()
      if (!rect || (rect.width === 0 && rect.height === 0)) return

      const before = textUpTo(container, range.startContainer, range.startOffset)
        .slice(-CONTEXT_CHARS)
        .trim()
      const after = textFrom(container, range.endContainer, range.endOffset)
        .slice(0, CONTEXT_CHARS)
        .trim()

      setSelectionBox({ anchor: { text, before, after }, rect })
    }

    document.addEventListener('mouseup', onMouseUp)
    return () => document.removeEventListener('mouseup', onMouseUp)
  }, [containerRef])

  // 禁用时清掉残留的选区框
  useEffect(() => {
    if (!enabled) setSelectionBox(null)
  }, [enabled])

  const clearSelection = useCallback(() => setSelectionBox(null), [])

  return { selectionBox, clearSelection }
}
