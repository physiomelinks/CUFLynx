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

/** Render a LaTeX label to HTML, falling back to escaped text. */
export function renderMath(s) {
  if (!s) return ''
  if (!looksLikeLatex(s)) return escapeHtml(s)
  try {
    return katex.renderToString(String(s), { throwOnError: false })
  } catch {
    return escapeHtml(s)
  }
}
