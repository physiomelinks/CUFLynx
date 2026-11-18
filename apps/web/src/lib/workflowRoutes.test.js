import { describe, expect, it } from 'vitest'
import { routeEdges } from './workflowRoutes'

const W = 250
const H = 78
const GAP = 96
const X = (col) => 8 + col * (W + GAP)

// The enterocyte workflow's shape: five transporter calibrations (column 1) fixed into
// whole_cell (column 2); all six merged into the target (column 3), the five skipping the
// whole_cell column; the target out to five sweeps (column 4).
const rows = (n, height) => Array.from({ length: n }, (_, i) => (height - (n * H + (n - 1) * 16)) / 2 + i * (H + 16))
const HEIGHT = 28 * 2 + 5 * H + 4 * 16
const box = (col, y) => ({ col, x: X(col), y, w: W, h: H })
const cal = rows(5, HEIGHT).map((y) => box(1, y))
const whole = box(2, (HEIGHT - H) / 2)
const target = box(3, (HEIGHT - H) / 2)
const sweeps = rows(5, HEIGHT).map((y) => box(4, y))
const model = box(0, (HEIGHT - H) / 2)
const BOXES = [model, ...cal, whole, target, ...sweeps]

const edge = (id, from, to) => ({ id, fromCol: from.col, toCol: to.col, from, to })
const EDGES = [
  ...cal.map((b, i) => edge(`model->c${i}`, model, b)),
  ...cal.map((b, i) => edge(`c${i}->whole`, b, whole)),
  ...cal.map((b, i) => edge(`c${i}->target`, b, target)),
  edge('whole->target', whole, target),
  ...sweeps.map((b, i) => edge(`target->s${i}`, target, b)),
]

function segments(points) {
  return points.slice(1).map((p, i) => [points[i], p])
}

/** Whether a horizontal or vertical segment passes through a box's inside. */
function crosses([[x1, y1], [x2, y2]], b) {
  const [lx, hx] = [Math.min(x1, x2), Math.max(x1, x2)]
  const [ly, hy] = [Math.min(y1, y2), Math.max(y1, y2)]
  return lx < b.x + b.w - 0.5 && hx > b.x + 0.5 && ly < b.y + b.h - 0.5 && hy > b.y + 0.5
}

describe('routeEdges', () => {
  const routes = routeEdges(EDGES, BOXES, { colGap: GAP, height: HEIGHT })

  it('never takes an arrow through a box it does not start or end at', () => {
    for (const e of EDGES) {
      for (const seg of segments(routes[e.id].points)) {
        for (const b of BOXES) {
          if (b === e.from || b === e.to) continue
          expect(crosses(seg, b), `${e.id} crosses the box at column ${b.col}, y ${b.y}`).toBe(false)
        }
      }
    }
  })

  it('gives every arrow its own lane and its own port at each end', () => {
    const lanes = new Set(EDGES.map((e) => routes[e.id].lane))
    expect(lanes.size).toBe(EDGES.length)
    const ins = {}
    const outs = {}
    for (const e of EDGES) {
      const pts = routes[e.id].points
      ;(outs[`${e.from.x},${e.from.y}`] ??= []).push(pts[0][1])
      ;(ins[`${e.to.x},${e.to.y}`] ??= []).push(pts[pts.length - 1][1])
    }
    for (const ys of [...Object.values(ins), ...Object.values(outs)]) {
      expect(new Set(ys).size).toBe(ys.length)
    }
  })

  it('starts on the source box and ends at the target box', () => {
    const r = routes['c0->target']
    expect(r.points[0][0]).toBe(cal[0].x + W)
    expect(r.points[r.points.length - 1][0]).toBe(target.x - 3)
    // it skips whole_cell's column, so it runs a corridor clear of whole_cell
    expect(r.points.length).toBe(6)
  })
})
