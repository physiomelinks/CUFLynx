/**
 * Orthogonal routes for the Workflow view's arrows, as few corners as each needs and the
 * corners lined up:
 *
 * - **Straight** when the two boxes overlap in height (and, for an arrow that skips columns,
 *   the line is clear of the boxes it passes): no corner.
 * - **One elbow** otherwise (horizontal, vertical, horizontal: two corners). It turns in the
 *   gap after its source; an arrow that skips columns runs its long horizontal at a height
 *   clear of the skipped boxes, at its source's height (turning before its target) or its
 *   target's (turning after its source).
 * - **A corridor** only when no such height exists: around the skipped boxes, above or below.
 * - **Lanes.** In a gap, every vertical run turns at the same x unless runs would overlap;
 *   overlapping runs take neighbouring lanes, ordered so they nest instead of crossing.
 * - **Ports.** A box's arrows leave and arrive at points ordered by the box at the other end,
 *   so top goes to top and the lines do not cross there.
 *
 * Input: `edges` [{id, fromCol, toCol, from: {x, y, w, h}, to: {x, y, w, h}}] and `boxes`
 * [{col, x, y, w, h}] (every box in the chart), with `colGap` and the chart's `height`.
 * Output: {id: {d, points, end: {x, y}, corners}}.
 */
const MARGIN = 10 // ports keep this far from a box's corners
const SPACING = 9 // between lines that run side by side
const CLEAR = 8 // a line keeps this far from a box it passes

export function routeEdges(edges, boxes, { colGap, height }) {
  const inner = (b) => [b.y + MARGIN, b.y + b.h - MARGIN]
  const centre = (b) => b.y + b.h / 2

  // The order of each box's arrows on its edge: by the box at the other end, one rule
  // everywhere. Of two at one height the nearer column goes first, so a straight arrow keeps
  // the middle and one that has to go round a box takes the next place down, and its detour.
  const byOut = (a, b) => centre(a.to) - centre(b.to) || a.toCol - b.toCol || cmp(a.id, b.id)
  const byIn = (a, b) => centre(a.from) - centre(b.from) || b.fromCol - a.fromCol || cmp(a.id, b.id)
  // the order of each box's arrows on its edge: by the box at the other end
  const outOrder = {}
  const inOrder = {}
  for (const list of Object.values(group(edges, (e) => key(e.from)))) {
    list.sort(byOut)
    list.forEach((e, i) => { outOrder[e.id] = [i, list.length] })
  }
  for (const list of Object.values(group(edges, (e) => key(e.to)))) {
    list.sort(byIn)
    list.forEach((e, i) => { inOrder[e.id] = [i, list.length] })
  }
  /** The heights an arrow's end may take on a box edge and still leave room, SPACING apart,
   * for the arrows ordered before and after it there. */
  const room = (b, [i, n]) => {
    const [lo, hi] = inner(b)
    const step = Math.min(SPACING, (hi - lo) / Math.max(1, n - 1))
    return [lo + i * step, hi - (n - 1 - i) * step]
  }

  // 1. what each arrow can be
  const plan = {}
  for (const e of edges) {
    // an end is held to where its neighbours on the box edge leave it room, so a straight
    // line (or a long run) never squeezes the arrows beside it onto one height
    const S = room(e.from, outOrder[e.id])
    const T = room(e.to, inOrder[e.id])
    const skipped = new Set()
    for (let c = e.fromCol + 1; c < e.toCol; c += 1) skipped.add(c)
    const free = skipped.size ? freeIntervals(boxes, skipped, 2, height - 2) : [[-Infinity, Infinity]]
    const O = meet(S, T)
    let type
    let iv = O && fit(O, free, (centre(e.from) + centre(e.to)) / 2)
    if (iv) type = 'straight'
    else if (!skipped.size) type = 'elbow'
    else if ((iv = fit(S, free, centre(e.to)))) type = 'late' // long run at the source's height
    else if ((iv = fit(T, free, centre(e.from)))) type = 'early' // long run at the target's height
    else type = 'corridor'
    plan[e.id] = { e, type, iv, free }
  }

  // 2. ports: the source side first, then the target side around the straight arrows' ends.
  // A straight arrow's one height has to suit both ends, so it aims between its place among
  // its source's arrows and its place among its target's.
  const y1 = {}
  const y2 = {}
  const targetSlot = {}
  for (const list of Object.values(group(edges, (e) => key(e.to)))) {
    list.sort(byIn)
    spread(list.length, ...inner(list[0].to)).forEach((y, i) => { targetSlot[list[i].id] = y })
  }
  const bySource = group(edges, (e) => key(e.from))
  for (const list of Object.values(bySource)) {
    list.sort(byOut)
    const slots = spread(list.length, ...inner(list[0].from))
    place(list.map((e, i) => {
      const p = plan[e.id]
      const allowed = p.type === 'straight' || p.type === 'late' ? p.iv : inner(e.from)
      const ideal = p.type === 'straight' ? targetSlot[e.id] : slots[i]
      return { id: e.id, ideal, lo: allowed[0], hi: allowed[1] }
    }), y1)
  }
  const byTarget = group(edges, (e) => key(e.to))
  for (const list of Object.values(byTarget)) {
    list.sort(byIn)
    const slots = spread(list.length, ...inner(list[0].to))
    place(list.map((e, i) => {
      const p = plan[e.id]
      if (p.type === 'straight') return { id: e.id, fixed: y1[e.id] }
      const allowed = p.type === 'early' ? p.iv : inner(e.to)
      return { id: e.id, ideal: slots[i], lo: allowed[0], hi: allowed[1] }
    }), y2)
  }

  // 3. corridors, for what can go no other way: a height clear of the skipped boxes,
  // stacked where their spans overlap
  const corridorY = {}
  const taken = []
  for (const e of edges.filter((x) => plan[x.id].type === 'corridor')) {
    const want = (y1[e.id] + y2[e.id]) / 2
    const ivs = [...plan[e.id].free].sort((a, b) => dist(want, a) - dist(want, b))
    let y = null
    for (const [lo, hi] of ivs) {
      for (let k = 0; k < 60 && y === null; k += 1) {
        const c = clamp(want, lo + CLEAR, hi - CLEAR) + (k % 2 ? 1 : -1) * Math.ceil(k / 2) * SPACING
        if (c < lo + 2 || c > hi - 2) continue
        if (!taken.some((t) => Math.abs(t.y - c) < SPACING && t.a < e.toCol && e.fromCol < t.b)) y = c
      }
      if (y !== null) break
    }
    corridorY[e.id] = y ?? want
    taken.push({ y: corridorY[e.id], a: e.fromCol, b: e.toCol })
  }

  // 4. the vertical runs of each gap, and their lanes
  const runs = {}
  const addRun = (gap, e, end, a, b) =>
    (runs[gap] ??= []).push({ e, end, lo: Math.min(a, b), hi: Math.max(a, b), down: b > a, y: a })
  for (const e of edges) {
    const p = plan[e.id]
    if (p.type === 'elbow' || p.type === 'early') addRun(e.fromCol, e, 'start', y1[e.id], y2[e.id])
    else if (p.type === 'late') addRun(e.toCol - 1, e, 'finish', y1[e.id], y2[e.id])
    else if (p.type === 'corridor') {
      addRun(e.fromCol, e, 'start', y1[e.id], corridorY[e.id])
      addRun(e.toCol - 1, e, 'finish', corridorY[e.id], y2[e.id])
    }
  }
  const laneX = {}
  for (const [gap, list] of Object.entries(runs)) {
    // further right: a run going down from higher up, a run going up from lower down -- so
    // runs into or out of one place nest instead of crossing
    list.sort((a, b) => rightness(a) - rightness(b) || cmp(a.e.id, b.e.id))
    const lane = new Map()
    for (const r of list) {
      let l = 0
      for (const [o, ol] of lane) if (overlap(r, o)) l = Math.max(l, ol + 1)
      lane.set(r, l)
    }
    const n = Math.max(...lane.values()) + 1
    const left = gapLeft(list[0], Number(gap))
    const mid = left + colGap / 2
    const step = Math.min(SPACING, (colGap - 2 * CLEAR) / Math.max(1, n - 1))
    for (const [r, l] of lane) laneX[`${r.e.id}:${r.end}`] = mid + (l - (n - 1) / 2) * step
  }
  function gapLeft(r, gap) {
    // a gap starts at the right edge of the column before it
    return r.end === 'start' ? r.e.from.x + r.e.from.w : r.e.to.x - colGap
  }

  // 5. the routes
  const out = {}
  for (const e of edges) {
    const p = plan[e.id]
    const x1 = e.from.x + e.from.w
    const x2 = e.to.x - 3
    const a = y1[e.id]
    const b = y2[e.id]
    let points
    if (p.type === 'straight') points = [[x1, a], [x2, a]]
    else if (p.type === 'elbow' || p.type === 'early') {
      const lx = laneX[`${e.id}:start`]
      points = [[x1, a], [lx, a], [lx, b], [x2, b]]
    } else if (p.type === 'late') {
      const lx = laneX[`${e.id}:finish`]
      points = [[x1, a], [lx, a], [lx, b], [x2, b]]
    } else {
      const s = laneX[`${e.id}:start`]
      const f = laneX[`${e.id}:finish`]
      const c = corridorY[e.id]
      points = [[x1, a], [s, a], [s, c], [f, c], [f, b], [x2, b]]
    }
    points = tidy(points)
    out[e.id] = {
      points,
      corners: points.length - 2,
      d: points.map(([x, y], i) => `${i ? 'L' : 'M'} ${round(x)} ${round(y)}`).join(' '),
      end: { x: x2, y: b },
      type: p.type,
    }
  }
  return out
}

/** Place ports in order: each at its ideal clamped to what it may be, at least SPACING apart
 * where room allows; a fixed one stays where it is. */
function place(items, into) {
  const ys = items.map((it) => (it.fixed != null ? it.fixed : clamp(it.ideal, it.lo, it.hi)))
  // down past the one before, then up clear of the one after (a fixed one never moves)
  for (let i = 1; i < items.length; i += 1) {
    const it = items[i]
    if (it.fixed == null && ys[i] < ys[i - 1] + SPACING) ys[i] = clamp(ys[i - 1] + SPACING, it.lo, it.hi)
  }
  for (let i = items.length - 2; i >= 0; i -= 1) {
    const it = items[i]
    if (it.fixed == null && ys[i] > ys[i + 1] - SPACING) ys[i] = clamp(ys[i + 1] - SPACING, it.lo, it.hi)
  }
  items.forEach((it, i) => { into[it.id] = ys[i] })
}

/** Drop a corner that does not turn (two points at one height or x, in a row). */
function tidy(points) {
  const out = []
  for (const p of points) {
    const q = out[out.length - 1]
    if (q && Math.abs(q[0] - p[0]) < 0.01 && Math.abs(q[1] - p[1]) < 0.01) continue
    out.push(p)
  }
  for (let i = out.length - 2; i > 0; i -= 1) {
    const [a, b, c] = [out[i - 1], out[i], out[i + 1]]
    const straight = (Math.abs(a[0] - b[0]) < 0.01 && Math.abs(b[0] - c[0]) < 0.01)
      || (Math.abs(a[1] - b[1]) < 0.01 && Math.abs(b[1] - c[1]) < 0.01)
    if (straight) out.splice(i, 1)
  }
  return out
}

function rightness(r) {
  return r.down ? -r.y : r.y
}
function overlap(a, b) {
  return a.lo < b.hi + SPACING / 2 && b.lo < a.hi + SPACING / 2
}
/** The free stretches of y in columns `cols`, inside [lo, hi], clear of their boxes. */
function freeIntervals(boxes, cols, lo, hi) {
  const busy = boxes
    .filter((b) => cols.has(b.col))
    .map((b) => [b.y - CLEAR, b.y + b.h + CLEAR])
    .sort((a, b) => a[0] - b[0])
  const out = []
  let at = lo
  for (const [s, e] of busy) {
    if (s > at) out.push([at, s])
    at = Math.max(at, e)
  }
  if (hi > at) out.push([at, hi])
  return out
}
/** The part of `range` inside one free stretch, the stretch nearest `want`; null for none. */
function fit(range, free, want) {
  let best = null
  for (const f of free) {
    const m = meet(range, f)
    if (m && (!best || dist(want, m) < dist(want, best))) best = m
  }
  return best
}
function meet([a, b], [c, d]) {
  const lo = Math.max(a, c)
  const hi = Math.min(b, d)
  return hi >= lo ? [lo, hi] : null
}
function spread(n, from, to) {
  if (n === 1) return [(from + to) / 2]
  return Array.from({ length: n }, (_, i) => from + (i * (to - from)) / (n - 1))
}
function group(list, by) {
  const out = {}
  for (const x of list) (out[by(x)] ??= []).push(x)
  return out
}
function key(b) {
  return `${b.x},${b.y}`
}
function clamp(v, lo, hi) {
  return Math.min(Math.max(v, lo), hi)
}
function cmp(a, b) {
  return a < b ? -1 : a > b ? 1 : 0
}
function dist(y, [s, e]) {
  return y < s ? s - y : y > e ? y - e : 0
}
function round(v) {
  return Math.round(v * 10) / 10
}
