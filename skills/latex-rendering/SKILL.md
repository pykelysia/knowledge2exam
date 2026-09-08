---
description: LaTeX 公式可渲染规范。题目含公式时遵守，避免 PDF 渲染失败。
---

# LaTeX 可渲染规范（latex-rendering）

最终试卷经由 pandoc + XeLaTeX 渲染为 PDF。题目中的公式必须使用以下安全子集。

## 基本写法

- 行内公式用 `$...$`，块级公式用 `$$...$$`。
- 数学模式内的字母、运算符、上下标使用标准 LaTeX 命令：
  `\alpha \beta \int \sum \frac{a}{b} \sqrt{x} x^{2} a_{n} \lim_{n \to \infty}`。

## 安全的宏与环境

- 结构：`\frac \sqrt \sum \int \prod \lim \partial \nabla \infty`
- 关系符：`\leq \geq \neq \approx \sim \propto \equiv`
- 逻辑：`\forall \exists \in \notin \subset \subseteq \cup \cap`
- 箭头：`\to \rightarrow \Rightarrow \leftrightarrow \mapsto`
- 定界符自适应：`\left( ... \right)`、`\left[ ... \right]`
- 多行推导：`\begin{aligned} ... \end{aligned}`（块级公式内）
- 矩阵：`\begin{pmatrix} ... \end{pmatrix}`、`\begin{bmatrix} ... \end{bmatrix}`
- 上下大括号：`\underbrace{}_{}`、`\overbrace{}^{}`（少量使用）

## 禁止或慎用

- `\substack`（用 `\begin{aligned}` 或上下标并列替代）
- `\begin{align}`（编号环境，用 `aligned` 替代）
- `\text{中文}`——中文放进 `\text` 常导致字体回退问题，中文说明写在公式外
- 自定义宏、`\def`、`\newcommand`——渲染环境不会执行定义
- `cases` 之外的花哨环境（`split` 可用，`empheq` 等扩展包禁止）
- 手写多行公式时不要用 `\\` 结尾的最后一条换行（pandoc 对尾部 `\\` 敏感）

## 示例

行内：信号 $x(t)$ 的傅里叶变换为 $X(f) = \int_{-\infty}^{\infty} x(t) e^{-j2\pi ft}\,dt$。

块级：

$$
X(f) = \int_{-\infty}^{\infty} x(t) e^{-j2\pi ft} \, dt
$$

多行推导：

$$
\begin{aligned}
y(t) &= h(t) * x(t) \\
&= \int_{-\infty}^{\infty} h(\tau) x(t - \tau) \, d\tau
\end{aligned}
$$
