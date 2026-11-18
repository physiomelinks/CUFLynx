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
const routes = routeEdges(EDGES, BOXES, { colGap: GAP, height: HEIGHT })

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

  it('is straight where the boxes overlap in height, and has at most one elbow otherwise', () => {
    // level boxes: no corner at all
    expect(routes['whole->target'].corners).toBe(0)
    expect(routes['c2->whole'].corners).toBe(0)
    expect(routes['model->c2'].corners).toBe(0)
    // one elbow (two corners) for the rest, the arrows that skip the whole_cell column too:
    // they run between its neighbours' rows -- all but c2's, level with whole_cell, which has
    // to leave its row to get round it (and come back): the only four-corner route
    for (const e of EDGES) {
      expect(routes[e.id].corners, e.id).toBeLessThanOrEqual(e.id === 'c2->target' ? 4 : 2)
    }
    expect(routes['c2->target'].type).toBe('corridor')
  })

  it("lines up the corners: a gap's vertical runs share an x unless they would overlap", () => {
    const runs = {}
    for (const e of EDGES) {
      for (const [[ax, ay], [bx, by]] of segments(routes[e.id].points)) {
        if (Math.abs(ax - bx) < 0.01 && Math.abs(ay - by) > 0.01) {
          ;(runs[Math.round(ax)] ??= []).push([Math.min(ay, by), Math.max(ay, by), e.id])
        }
      }
    }
    // no two runs at one x overlap: the lines never lie on top of each other
    for (const list of Object.values(runs)) {
      for (let i = 0; i < list.length; i += 1) {
        for (let j = i + 1; j < list.length; j += 1) {
          const [a, b] = [list[i], list[j]]
          expect(a[0] < b[1] && b[0] < a[1], `${a[2]} and ${b[2]} share a run`).toBe(false)
        }
      }
    }
    // the gap from the target out to the sweeps: the runs above the target's middle share one
    // x, and so do the ones below it -- two turning lines, not a staircase of five
    const out = sweeps.map((_, i) => routes[`target->s${i}`]).filter((r) => r.corners === 2)
    const xs = new Set(out.map((r) => Math.round(r.points[1][0])))
    expect(xs.size).toBeLessThanOrEqual(3)
  })

  it('never puts two arrows on one box edge closer than 8 px (no doubled lines)', () => {
    const ends = {}
    for (const e of EDGES) {
      const pts = routes[e.id].points
      ;(ends[`out ${e.from.x},${e.from.y}`] ??= []).push(pts[0][1])
      ;(ends[`in ${e.to.x},${e.to.y}`] ??= []).push(pts[pts.length - 1][1])
    }
    for (const [side, ys] of Object.entries(ends)) {
      const sorted = [...ys].sort((a, b) => a - b)
      for (let i = 1; i < sorted.length; i += 1) {
        expect(sorted[i] - sorted[i - 1], side).toBeGreaterThanOrEqual(8)
      }
    }
    // whole_cell, level with the target, goes straight; c2, level with both, goes round
    expect(routes['whole->target'].corners).toBe(0)
  })

  it('orders a box\'s entry points as its sources are ordered, top to top', () => {
    const ends = cal.map((_, i) => routes[`c${i}->whole`].end.y)
    expect([...ends].sort((a, b) => a - b)).toEqual(ends)
    const intoTarget = cal.map((_, i) => routes[`c${i}->target`].end.y)
    expect([...intoTarget].sort((a, b) => a - b)).toEqual(intoTarget)
  })
})
