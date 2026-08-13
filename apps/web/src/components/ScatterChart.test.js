import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ScatterChart from './ScatterChart.vue'

const mountChart = (props) =>
  mount(ScatterChart, {
    props: { xDomain: [0, 10], yDomain: [0, 10], ...props },
  })

describe('ScatterChart', () => {
  // The bug this component exists to fix: the parity line used to be a
  // CSS-rotated div, a true diagonal only when the box happened to be square.
  // Drawn in the same coordinates as the points, "on the line" means "equal"
  // whatever the box is, because both are scaled by the same transform.
  it('puts an exact prediction exactly on the diagonal', () => {
    const w = mountChart({
      points: [{ x: 3, y: 3 }],
      guide: 'diagonal',
    })
    const pt = w.find('.parity-point')
    const guide = w.find('[data-testid="chart-guide"]')
    // The guide spans the shared range, so a point with x === y sits on the
    // segment: same parameter along it in both axes.
    const [x1, y1, x2, y2] = ['x1', 'y1', 'x2', 'y2'].map((a) =>
      Number(guide.attributes(a)),
    )
    const cx = Number(pt.attributes('cx'))
    const cy = Number(pt.attributes('cy'))
    const t = (cx - x1) / (x2 - x1)
    expect(cy).toBeCloseTo(y1 + t * (y2 - y1), 6)
  })

  it('scales points into the plot area, inside the axes', () => {
    const w = mountChart({ points: [{ x: 0, y: 0 }, { x: 10, y: 10 }] })
    const pts = w.findAll('.parity-point')
    expect(pts).toHaveLength(2)
    const xs = pts.map((p) => Number(p.attributes('cx')))
    const ys = pts.map((p) => Number(p.attributes('cy')))
    // Left margin leaves room for the y tick labels; x grows right, y grows up
    // (so the *smaller* data y has the larger SVG y).
    expect(xs[0]).toBeGreaterThan(0)
    expect(xs[1]).toBeGreaterThan(xs[0])
    expect(ys[0]).toBeGreaterThan(ys[1])
  })

  it('draws labelled axes, which is what makes the numbers readable', () => {
    const w = mountChart({
      points: [{ x: 1, y: 2 }],
      xLabel: 'simulated',
      yLabel: 'emulated',
    })
    const text = w.text()
    expect(text).toContain('simulated')
    expect(text).toContain('emulated')
    // Ticks on both axes, with values on them.
    expect(w.findAll('.tick').length).toBeGreaterThanOrEqual(4)
    expect(text).toContain('10')
  })

  it('draws the zero line for residuals, and omits it when zero is off-scale', () => {
    const spans = mountChart({
      yDomain: [-1, 1],
      points: [{ x: 5, y: 0.5 }],
      guide: 'zero',
    })
    expect(spans.find('[data-testid="chart-guide"]').exists()).toBe(true)

    // A guide drawn outside the axes would be a line the data cannot reach.
    const excludes = mountChart({
      yDomain: [2, 4],
      points: [{ x: 5, y: 3 }],
      guide: 'zero',
    })
    expect(excludes.find('[data-testid="chart-guide"]').exists()).toBe(false)
  })

  it('still plots a constant column instead of dividing by a zero span', () => {
    // Every held-out point predicting the same value is a degenerate but real
    // case -- it must not render NaN coordinates.
    const w = mountChart({
      xDomain: [4, 4],
      yDomain: [4, 4],
      points: [{ x: 4, y: 4 }],
    })
    const pt = w.find('.parity-point')
    expect(Number.isFinite(Number(pt.attributes('cx')))).toBe(true)
    expect(Number.isFinite(Number(pt.attributes('cy')))).toBe(true)
  })

  it('keeps its geometry uniform so a square plot stays square', () => {
    // preserveAspectRatio="none" would stretch the diagonal away from 45
    // degrees and distort the tick text with it.
    const w = mountChart({ points: [], square: true })
    expect(w.attributes('preserveAspectRatio')).toBe('xMidYMid meet')
    expect(w.classes()).toContain('square')
  })
})
