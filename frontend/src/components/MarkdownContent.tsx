import ReactMarkdown from 'react-markdown'
import remarkMath from 'remark-math'
import rehypeKatex from 'rehype-katex'
import 'katex/dist/katex.min.css'

/**
 * 剥离 HTML 注释（如旧版试卷的 `<!-- plan_item: ... -->` 元信息）。
 * react-markdown 默认把原始 HTML 当作字面文本渲染，注释会直接显示在页面上。
 */
function stripHtmlComments(text: string): string {
  return text.replace(/<!--[\s\S]*?-->/g, '')
}

/**
 * 归一化数学定界符。remark-math 只识别 $...$ / $$...$$，
 * 而模型偶尔会输出 \(...\) / \[...\]，这里统一转成 $ 定界符。
 */
function normalizeMathDelimiters(text: string): string {
  return text
    .replace(/\\\[([\s\S]*?)\\\]/g, (_m, math: string) => `$$${math}$$`)
    .replace(/\\\(([\s\S]*?)\\\)/g, (_m, math: string) => `$${math}$`)
}

interface MarkdownContentProps {
  content: string
  className?: string
}

/** 渲染题目文本：Markdown + LaTeX（KaTeX）。排版样式见 index.css 的 .markdown-body。 */
export function MarkdownContent({ content, className }: MarkdownContentProps) {
  return (
    <div className={className ? `markdown-body ${className}` : 'markdown-body'}>
      <ReactMarkdown
        remarkPlugins={[remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false }]]}
      >
        {normalizeMathDelimiters(stripHtmlComments(content))}
      </ReactMarkdown>
    </div>
  )
}
