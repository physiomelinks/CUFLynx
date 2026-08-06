// Evaluate a prior hyper-parameter's `default_expr` — the expression CA declares
// for what a blank field resolves to, e.g. "(min + max) / 2".
//
// CA states the formula once and computes its own defaults from it; this
// evaluates the same string so the placeholder shows the number the field will
// actually take. Restating the arithmetic here instead is how the displayed
// default and the used one drift apart.
//
// Deliberately tiny, and a parser rather than `eval`: numbers, names, unary
// minus and the four operators, with precedence. The strings come from CA's own
// schema, but a parser that can only do arithmetic cannot become anything else.

/** Tokenise; returns null on any character the grammar does not allow. */
function tokenize(src) {
  const tokens = []
  let i = 0
  while (i < src.length) {
    const c = src[i]
    if (/\s/.test(c)) { i += 1; continue }
    if ('+-*/()'.includes(c)) { tokens.push({ t: c }); i += 1; continue }
    let m = /^\d+(\.\d+)?([eE][+-]?\d+)?/.exec(src.slice(i))
    if (m) { tokens.push({ t: 'num', v: Number(m[0]) }); i += m[0].length; continue }
    m = /^[A-Za-z_][A-Za-z0-9_]*/.exec(src.slice(i))
    if (m) { tokens.push({ t: 'name', v: m[0] }); i += m[0].length; continue }
    return null
  }
  return tokens
}

/**
 * @param {string} expr  CA's default_expr
 * @param {Record<string, number|null|undefined>} names  min/max and sibling params
 * @returns {number|null} the value, or null when it cannot be computed
 */
export function evalPriorDefault(expr, names = {}) {
  if (expr == null || expr === '') return null
  const tokens = tokenize(String(expr))
  if (!tokens) return null

  let pos = 0
  const peek = () => tokens[pos]
  let failed = false

  function primary() {
    const tk = peek()
    if (!tk) { failed = true; return 0 }
    if (tk.t === 'num') { pos += 1; return tk.v }
    if (tk.t === 'name') {
      pos += 1
      const v = names[tk.v]
      if (v == null || !Number.isFinite(Number(v))) { failed = true; return 0 }
      return Number(v)
    }
    if (tk.t === '-') { pos += 1; return -primary() }
    if (tk.t === '+') { pos += 1; return primary() }
    if (tk.t === '(') {
      pos += 1
      const v = additive()
      if (peek()?.t !== ')') { failed = true; return 0 }
      pos += 1
      return v
    }
    failed = true
    return 0
  }

  function multiplicative() {
    let v = primary()
    while (!failed && (peek()?.t === '*' || peek()?.t === '/')) {
      const op = peek().t
      pos += 1
      const rhs = primary()
      if (op === '/' && rhs === 0) { failed = true; return 0 }
      v = op === '*' ? v * rhs : v / rhs
    }
    return v
  }

  function additive() {
    let v = multiplicative()
    while (!failed && (peek()?.t === '+' || peek()?.t === '-')) {
      const op = peek().t
      pos += 1
      const rhs = multiplicative()
      v = op === '+' ? v + rhs : v - rhs
    }
    return v
  }

  const value = additive()
  if (failed || pos !== tokens.length || !Number.isFinite(value)) return null
  return value
}

/** Round for display without pretending to more precision than is meaningful. */
export function formatPriorDefault(value) {
  if (value == null) return null
  const abs = Math.abs(value)
  if (abs !== 0 && (abs < 1e-3 || abs >= 1e6)) return value.toExponential(3)
  return String(Number(value.toPrecision(4)))
}
