import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import TourOverlay from './TourOverlay.vue'

/**
 * The tour engine, tested against a local fixture rather than the real step
 * list: the steps are data written elsewhere, and a test that read them would
 * fail whenever a sentence is reworded.
 *
 * jsdom gives every element a zero rect and an empty getClientRects(), which is
 * exactly the state the overlay reads as "not on screen" -- so every anchor
 * here is created with a mocked box.
 */

// -- anchors ---------------------------------------------------------------

const rectOf = (top, left, width, height) => ({
  top,
  left,
  width,
  height,
  right: left + width,
  bottom: top + height,
  x: left,
  y: top,
  toJSON: () => {},
})

function anchor(testid, { top = 200, left = 100, width = 80, height = 24 } = {}) {
  const el = document.createElement('button')
  el.setAttribute('data-testid', testid)
  el.textContent = testid
  document.body.appendChild(el)
  const r = rectOf(top, left, width, height)
  el.getBoundingClientRect = () => r
  // A real element that has been laid out reports one client rect.
  el.getClientRects = () => [r]
  return el
}

// The inactive-`v-show`-pane case: present in the DOM, no box at all.
function zeroAnchor(testid) {
  const el = anchor(testid)
  const r = rectOf(0, 0, 0, 0)
  el.getBoundingClientRect = () => r
  el.getClientRects = () => [r]
  return el
}

// jsdom has no ResizeObserver; the overlay observes its current target, so a
// stub keeps that path exercised (and its disconnect assertable).
class FakeResizeObserver {
  constructor(cb) {
    this.cb = cb
  }
  observe(el) {
    this.el = el
  }
  unobserve() {}
  disconnect() {
    FakeResizeObserver.disconnects += 1
  }
}
FakeResizeObserver.disconnects = 0

// -- fixture ---------------------------------------------------------------

const FIXTURE = () => [
  { id: 'a', target: '[data-testid="a"]', title: 'First', text: 'Click A.', side: 'bottom' },
  { id: 'b', target: '[data-testid="b"]', text: 'Then B.', side: 'top' },
  { id: 'c', target: '[data-testid="c"]', text: 'Finally C.', side: 'right' },
]

const wrappers = []
const mountTour = async (props = {}) => {
  const w = mount(TourOverlay, { props: { steps: FIXTURE(), step: 0, ctx: {}, ...props } })
  wrappers.push(w)
  // The first step resolves in onMounted, i.e. after the initial render.
  await nextTick()
  return w
}

// Everything the overlay draws is teleported to <body>, i.e. outside the
// wrapper -- read it where it really is.
const bubble = () => document.querySelector('[data-testid="tour-bubble"]')
const ring = () => document.querySelector('[data-testid="tour-ring"]')
const text = () => document.querySelector('[data-testid="tour-text"]')?.textContent.trim()
const count = () => document.querySelector('[data-testid="tour-count"]')?.textContent.trim()
const button = (name) => document.querySelector(`[data-testid="tour-${name}"]`)

const clickOn = async (el) => {
  el.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await nextTick()
}
// The 200 ms tick and the <=350 ms grace period both run on timers.
const wait = async (ms) => {
  vi.advanceTimersByTime(ms)
  await nextTick()
}

beforeEach(() => {
  // requestAnimationFrame is not in vitest's default toFake list, and the
  // grace period is driven by it.
  vi.useFakeTimers({
    toFake: [
      'setTimeout',
      'clearTimeout',
      'setInterval',
      'clearInterval',
      'Date',
      'requestAnimationFrame',
      'cancelAnimationFrame',
    ],
  })
  FakeResizeObserver.disconnects = 0
  globalThis.ResizeObserver = FakeResizeObserver
})

afterEach(() => {
  while (wrappers.length) wrappers.pop().unmount()
  // Teleported nodes outlive their wrapper, so a leftover bubble would be
  // found by the next test.
  document.body.innerHTML = ''
  delete globalThis.ResizeObserver
  vi.useRealTimers()
})

describe('TourOverlay', () => {
  it('renders the current step and where it sits in the run', async () => {
    anchor('a')
    await mountTour()
    expect(text()).toBe('Click A.')
    expect(count()).toBe('1 / 3')
    expect(bubble().getAttribute('role')).toBe('dialog')
  })

  it("draws the ring on the target's own box", async () => {
    anchor('a', { top: 120, left: 40, width: 90, height: 30 })
    await mountTour()
    const r = ring()
    expect(r.style.top).toBe('120px')
    expect(r.style.left).toBe('40px')
    expect(r.style.width).toBe('90px')
    expect(r.style.height).toBe('30px')
  })

  it('keeps the asked-for side when it fits', async () => {
    anchor('b', { top: 400 })
    await mountTour({ steps: [FIXTURE()[1]], step: 0 })
    expect(bubble().dataset.side).toBe('top')
    expect(bubble().querySelector('.tour-caret').className).toContain('tour-caret-top')
  })

  it('flips to the opposite side when the asked-for one runs off the viewport', async () => {
    // side: 'top' on a control 2px from the top edge would put the bubble
    // off-screen; the arrow has to move with it.
    anchor('b', { top: 2 })
    await mountTour({ steps: [FIXTURE()[1]], step: 0 })
    expect(bubble().dataset.side).toBe('bottom')
    expect(bubble().querySelector('.tour-caret').className).toContain('tour-caret-bottom')
  })

  it('skips a step whose anchor is not in the DOM', async () => {
    anchor('b')
    const w = await mountTour()
    await wait(400) // the first candidate's grace period
    expect(text()).toBe('Then B.')
    expect(w.emitted('update:step')).toEqual([[1]])
  })

  it('skips a step whose anchor has no box (an inactive v-show pane)', async () => {
    // No tab bookkeeping: a hidden pane's controls are in the DOM with a 0x0
    // rect, and that alone is enough to know the step cannot be shown.
    zeroAnchor('a')
    anchor('b')
    const w = await mountTour()
    await wait(400)
    expect(text()).toBe('Then B.')
    expect(w.emitted('update:step')).toEqual([[1]])
  })

  it('honours when(ctx), reading app state without touching it', async () => {
    anchor('a')
    anchor('b')
    const steps = FIXTURE()
    steps[1].when = (ctx) => ctx.hasModel
    const w = await mountTour({ steps, ctx: { hasModel: false } })
    await clickOn(button('next'))
    await wait(400)
    // b was skipped by `when`; c has no anchor either, so the run finishes.
    expect(w.emitted('close')).toEqual([['finish']])
  })

  it('emits update:step once for a run of skipped steps, not once per skip', async () => {
    // A run of skips must be one synchronous loop and one emit, not one
    // render and one emit per skipped step.
    anchor('a')
    anchor('d')
    const steps = [
      ...FIXTURE(),
      { id: 'd', target: '[data-testid="d"]', text: 'Reached D.', side: 'bottom' },
    ]
    const w = await mountTour({ steps })
    await clickOn(button('next'))
    await wait(400)
    expect(text()).toBe('Reached D.')
    expect(w.emitted('update:step')).toEqual([[3]])
  })

  it("advances on the user's real click, even when the handler stops propagation", async () => {
    // Capture phase: the obs_data dialog's row handlers call stopPropagation,
    // so a bubbling listener would never hear the click.
    const a = anchor('a')
    anchor('b')
    a.addEventListener('click', (e) => e.stopPropagation())
    const steps = FIXTURE()
    steps[0].advanceOn = { event: 'click' } // target defaults to step.target
    const w = await mountTour({ steps })
    await clickOn(a)
    expect(w.emitted('update:step')).toEqual([[1]])
    expect(text()).toBe('Then B.')
  })

  it('ignores clicks on elements the step is not about', async () => {
    anchor('a')
    anchor('b')
    const other = anchor('other')
    const steps = FIXTURE()
    steps[0].advanceOn = { target: '[data-testid="b"]', event: 'click' }
    const w = await mountTour({ steps })
    await clickOn(other)
    expect(w.emitted('update:step')).toBeFalsy()
    expect(text()).toBe('Click A.')
  })

  it('ignores clicks inside its own bubble, however broad the advanceOn selector', async () => {
    // A selector as broad as `button` matches the bubble's own controls; the
    // guard has to come first or Skip would also advance the tour.
    anchor('a')
    anchor('b')
    const steps = FIXTURE()
    steps[0].advanceOn = { target: 'button', event: 'click' }
    const w = await mountTour({ steps })
    await clickOn(button('skip'))
    expect(w.emitted('update:step')).toBeFalsy()
    expect(w.emitted('close')).toEqual([['skip']])
  })

  it('advances when waitFor(ctx) turns true on the tick', async () => {
    anchor('a')
    anchor('b')
    const state = { ready: false }
    const steps = FIXTURE()
    steps[0].waitFor = (ctx) => ctx.ready
    const w = await mountTour({ steps, ctx: state })
    await wait(600)
    expect(w.emitted('update:step')).toBeFalsy()
    state.ready = true
    await wait(250)
    expect(w.emitted('update:step')).toEqual([[1]])
    expect(text()).toBe('Then B.')
  })

  it('runs onNext when the user presses Next, before moving on', async () => {
    anchor('a')
    anchor('b')
    const closed = []
    const steps = FIXTURE()
    steps[0].onNext = (ctx) => closed.push(ctx.who)
    const w = await mountTour({ steps, ctx: { who: 'settings' } })
    await clickOn(button('next'))
    expect(closed).toEqual(['settings'])
    expect(w.emitted('update:step')).toEqual([[1]])
  })

  it('does not run onNext when the step advances by itself', async () => {
    // advanceOn and waitFor both mean the user already did the thing; running
    // onNext then would undo it.
    const a = anchor('a')
    anchor('b')
    anchor('c')
    const ran = []
    const steps = FIXTURE()
    steps[0].advanceOn = { target: '[data-testid="a"]' }
    steps[0].onNext = () => ran.push('a')
    steps[1].waitFor = () => true
    steps[1].onNext = () => ran.push('b')
    await mountTour({ steps })
    await clickOn(a)
    await wait(250)
    expect(text()).toBe('Finally C.')
    expect(ran).toEqual([])
  })

  it('offers no Next on a step that is waiting for the user to click something', async () => {
    // The accident this prevents: Next sits under the pointer, so it is the
    // easiest thing to press -- and pressing it walks past the click the step
    // exists for, onto a bubble pointing at UI that click was going to produce.
    anchor('a')
    anchor('b')
    const steps = FIXTURE()
    steps[0].advanceOn = { target: '[data-testid="a"]' }
    await mountTour({ steps })
    expect(button('next')).toBeNull()
    // Never a dead end: Back and Skip are always there.
    expect(button('skip')).not.toBeNull()
    expect(button('back')).not.toBeNull()
  })

  it('offers no Next on a step that is waiting on app state either', async () => {
    anchor('a')
    anchor('b')
    const steps = FIXTURE()
    steps[0].waitFor = () => false
    await mountTour({ steps })
    expect(button('next')).toBeNull()
  })

  it('does offer Next when the step can do the waiting-for itself', async () => {
    // `onNext` means pressing Next performs the action rather than skipping it.
    anchor('a')
    anchor('b')
    const steps = FIXTURE()
    steps[0].waitFor = (ctx) => ctx.done
    steps[0].onNext = (ctx) => {
      ctx.done = true
    }
    const ctx = { done: false }
    const w = await mountTour({ steps, ctx })
    expect(button('next')).not.toBeNull()
    await clickOn(button('next'))
    expect(ctx.done).toBe(true)
    expect(w.emitted('update:step')).toEqual([[1]])
  })

  it('offers Next on an ordinary explanatory step', async () => {
    anchor('a')
    await mountTour()
    expect(button('next')).not.toBeNull()
  })

  it('re-resolves forward when its target disappears', async () => {
    const a = anchor('a')
    anchor('b')
    const w = await mountTour()
    a.remove()
    // Two consecutive misses, so a dialog being rebuilt is not mistaken for one.
    await wait(200)
    expect(w.emitted('update:step')).toBeFalsy()
    await wait(200)
    expect(w.emitted('update:step')).toEqual([[1]])
    expect(text()).toBe('Then B.')
  })

  it('re-queries the selector rather than holding on to the element', async () => {
    // PrimeVue rebuilds dialog rows on every open: a cached handle points at a
    // detached node the moment the dialog is reopened.
    const a = anchor('a', { top: 200 })
    const w = await mountTour()
    a.remove()
    anchor('a', { top: 300 })
    await wait(400)
    expect(w.emitted('update:step')).toBeFalsy()
    expect(ring().style.top).toBe('300px')
  })

  it('goes back to the previous available step', async () => {
    anchor('a')
    anchor('c')
    const w = await mountTour({ step: 2 })
    await clickOn(button('back'))
    await wait(400)
    expect(text()).toBe('Click A.')
    expect(w.emitted('update:step').at(-1)).toEqual([0])
  })

  it('stays put when Back runs off the start', async () => {
    anchor('a')
    const w = await mountTour()
    await clickOn(button('back'))
    await wait(400)
    expect(text()).toBe('Click A.')
    expect(w.emitted('close')).toBeFalsy()
  })

  it('emits close("skip") from the Skip button', async () => {
    anchor('a')
    const w = await mountTour()
    await clickOn(button('skip'))
    expect(w.emitted('close')).toEqual([['skip']])
  })

  it('emits close("finish") on Next past the last available step', async () => {
    anchor('c')
    const w = await mountTour({ step: 2 })
    await clickOn(button('next'))
    await wait(400)
    expect(w.emitted('close')).toEqual([['finish']])
    expect(bubble()).toBeNull()
  })

  it('renders nothing at all when no step resolves', async () => {
    // A fully skipped run must not flash on screen before it closes.
    const w = await mountTour()
    expect(bubble()).toBeNull()
    await wait(400)
    expect(w.emitted('close')).toEqual([['finish']])
    expect(bubble()).toBeNull()
  })

  it('leaves no listeners, observer or timer behind after unmount', async () => {
    anchor('a')
    anchor('b')
    const add = vi.spyOn(document, 'addEventListener')
    const remove = vi.spyOn(document, 'removeEventListener')
    const clear = vi.spyOn(globalThis, 'clearInterval')
    const steps = FIXTURE()
    steps[0].advanceOn = { event: 'click' }
    const w = await mountTour({ steps })
    expect(add.mock.calls.length).toBeGreaterThan(0)
    wrappers.pop()
    w.unmount()
    expect(remove.mock.calls.length).toBe(add.mock.calls.length)
    expect(clear).toHaveBeenCalled()
    expect(FakeResizeObserver.disconnects).toBeGreaterThan(0)
    add.mockRestore()
    remove.mockRestore()
    clear.mockRestore()
  })
})
