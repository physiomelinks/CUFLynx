import katex from 'katex'
import 'katex/dist/katex.min.css'

export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

// Only treat labels with LaTeX markup (braces, backslash commands, carets) as
// math; plain qnames like "aortic_root/v" render as readable text.
export function looksLikeLatex(s) {
  return /[\\{}^]/.test(s)
}

/** Render a LaTeX label to HTML, falling back to escaped text.
 *
 * ``output: 'html'`` so KaTeX emits only its visual HTML layer — without it the
 * MathML accessibility layer renders too (browsers with native MathML show the
 * formula a second time) when the layer isn't hidden by CSS.
 */
export function renderMath(s) {
  if (!s) return ''
  if (!looksLikeLatex(s)) return escapeHtml(s)
  try {
    return katex.renderToString(String(s), { throwOnError: false, output: 'html' })
  } catch {
    return escapeHtml(s)
  }
}

/** Render a sensitivity output label `var^{e,s} [operation]`: the `var^{e,s}`
 * part is typeset as math, but the trailing `[operation]` is kept as plain text
 * (its underscores are operation-name separators, not LaTeX subscripts). */
export function renderOutputLabel(s) {
  if (!s) return ''
  const m = String(s).match(/^(.*?)\s*\[([^\]]*)\]\s*$/)
  if (!m) return renderMath(s)
  return `${renderMath(m[1])} <span class="op-label">[${escapeHtml(m[2])}]</span>`
}
