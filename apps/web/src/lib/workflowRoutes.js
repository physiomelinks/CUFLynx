/**
 * Orthogonal routes for the Workflow view's arrows, laid side by side so each can be followed:
 *
 * - **Ports.** A box's outgoing arrows leave from points spread down its right edge, its
 *   incoming ones arrive at points spread down its left edge, each in the order of the box at
 *   the other end, so arrows into or out of one box do not cross or share a line.
 * - **Lanes.** Each arrow turns in its own vertical lane in the gap after its source's column
 *   (and, when it skips columns, in its own lane in the gap before its target's column).
 * - **Corridors.** An arrow that skips columns crosses them in a horizontal corridor clear of
 *   every box in those columns, its own height within the corridor.
 *
 * Input: `edges` [{id, fromCol, toCol, from: {x, y, w, h}, to: {x, y, w, h}}] and `boxes`
 * [{col, x, y, w, h}] (every box in the chart), with `colGap` and the chart's `height`.
 * Output: {id: {d, points, end: {x, y}}}, `points` the corners in order.
 */
const LANE_MIN = 6
const CORRIDOR_STEP = 7
const BOX_CLEAR = 6

function spread(n, from, to) {
  // n points evenly inside (from, to)
  return Array.from({ length: n }, (_, i) => from + ((i + 1) * (to - from)) / (n + 1))
}

/** The free stretches of y in columns `cols`, inside [lo, hi], clear of their boxes. */
function freeIntervals(boxes, cols, lo, hi) {
  const busy = boxes
    .filter((b) => cols.has(b.col))
    .map((b) => [b.y - BOX_CLEAR, b.y + b.h + BOX_CLEAR])
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

export function routeEdges(edges, boxes, { colGap, height }) {
  // ports: per box side, in the order of the other end (then by column, then id)
  const outs = {}
  const ins = {}
  for (const e of edges) {
    ;(outs[boxKey(e.from)] ??= []).push(e)
    ;(ins[boxKey(e.to)] ??= []).push(e)
  }
  const y1 = {}
  const y2 = {}
  for (const list of Object.values(outs)) {
    list.sort((a, b) => a.to.y - b.to.y || a.toCol - b.toCol || cmp(a.id, b.id))
    const ys = spread(list.length, list[0].from.y, list[0].from.y + list[0].from.h)
    list.forEach((e, i) => { y1[e.id] = ys[i] })
  }
  for (const list of Object.values(ins)) {
    list.sort((a, b) => a.from.y - b.from.y || a.fromCol - b.fromCol || cmp(a.id, b.id))
    const ys = spread(list.length, list[0].to.y, list[0].to.y + list[0].to.h)
    list.forEach((e, i) => { y2[e.id] = ys[i] })
  }

  // corridors for the arrows that skip columns
  const corridorY = {}
  const used = {}
  const long = edges.filter((e) => e.toCol - e.fromCol > 1)
    .sort((a, b) => (a.toCol - a.fromCol) - (b.toCol - b.fromCol) || cmp(a.id, b.id))
  for (const e of long) {
    const cols = new Set()
    for (let c = e.fromCol + 1; c < e.toCol; c += 1) cols.add(c)
    const free = freeIntervals(boxes, cols, 2, height - 2)
    const want = (y1[e.id] + y2[e.id]) / 2
    // the free stretch nearest the arrow's own height, then a height in it no other has
    free.sort((a, b) => dist(want, a) - dist(want, b))
    let chosen = null
    for (const [s, en] of free) {
      const mid = Math.min(Math.max(want, s + CORRIDOR_STEP / 2), en - CORRIDOR_STEP / 2)
      for (let k = 0; k < 40 && chosen === null; k += 1) {
        const y = mid + (k % 2 ? 1 : -1) * Math.ceil(k / 2) * CORRIDOR_STEP
        if (y <= s || y >= en) continue
        const clash = (used[Math.round(y)] ?? []).some(([a, b]) => !(e.toCol <= a || e.fromCol >= b))
        if (!clash) chosen = y
      }
      if (chosen !== null) break
    }
    corridorY[e.id] = chosen ?? want
    ;(used[Math.round(corridorY[e.id])] ??= []).push([e.fromCol, e.toCol])
  }

  // lanes: every arrow turns in the gap after its source; one that skips columns also in
  // the gap before its target. Each gap's lanes are spread across it, ordered to avoid crossings.
  const gaps = {}
  for (const e of edges) {
    ;(gaps[e.fromCol] ??= []).push({ e, end: 'start' })
    if (e.toCol - e.fromCol > 1) (gaps[e.toCol - 1] ??= []).push({ e, end: 'finish' })
  }
  const laneX = {}
  for (const [col, list] of Object.entries(gaps)) {
    // a turn going down sits left of one going up when both leave the same height: order by
    // where the vertical run ends, so runs nest instead of crossing
    list.sort((a, b) => key(a) - key(b) || cmp(a.e.id, b.e.id))
    const left = list[0].e.fromCol === Number(col) && list[0].end === 'start'
      ? list[0].e.from.x + list[0].e.from.w : list[0].e.to.x - colGap
    const xs = spread(list.length, left, left + colGap)
    const step = list.length > 1 ? xs[1] - xs[0] : colGap
    if (step < LANE_MIN && list.length > 1) {
      // too many to space evenly: pack them from the middle at the least gap
      const mid = left + colGap / 2
      list.forEach((item, i) => {
        laneX[`${item.e.id}:${item.end}`] = mid + (i - (list.length - 1) / 2) * LANE_MIN
      })
    } else {
      list.forEach((item, i) => { laneX[`${item.e.id}:${item.end}`] = xs[i] })
    }
  }
  function key(item) {
    const e = item.e
    return item.end === 'start' ? (corridorY[e.id] ?? y2[e.id]) - y1[e.id] : y2[e.id] - corridorY[e.id]
  }

  const out = {}
  for (const e of edges) {
    const x1 = e.from.x + e.from.w
    const x2 = e.to.x - 3
    const a = laneX[`${e.id}:start`]
    const points = [[x1, y1[e.id]], [a, y1[e.id]]]
    if (e.toCol - e.fromCol > 1) {
      const b = laneX[`${e.id}:finish`]
      const cy = corridorY[e.id]
      points.push([a, cy], [b, cy], [b, y2[e.id]])
    } else {
      points.push([a, y2[e.id]])
    }
    points.push([x2, y2[e.id]])
    out[e.id] = {
      points,
      d: points.map(([x, y], i) => `${i ? 'L' : 'M'} ${round(x)} ${round(y)}`).join(' '),
      end: { x: x2, y: y2[e.id] },
      lane: a,
    }
  }
  return out
}

function boxKey(b) {
  return `${b.x},${b.y}`
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
