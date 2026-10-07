import { describe, it, expect, vi, afterEach } from 'vitest'
import { startPresence } from './presence.js'

function fakeWindow() {
  const target = new EventTarget()
  return {
    fetch: vi.fn(() => Promise.resolve()),
    setInterval: (fn, ms) => setInterval(fn, ms),
    clearInterval: (t) => clearInterval(t),
    addEventListener: target.addEventListener.bind(target),
    removeEventListener: target.removeEventListener.bind(target),
    fire(type, props = {}) {
      target.dispatchEvent(Object.assign(new Event(type), props))
    },
  }
}

describe('startPresence', () => {
  let stop
  afterEach(() => {
    stop?.()
    vi.useRealTimers()
  })

  it('beats at once and then on an interval, with one client id throughout', () => {
    vi.useFakeTimers()
    const win = fakeWindow()
    stop = startPresence({ win, nav: { sendBeacon: vi.fn(() => true) }, interval: 1000 })
    expect(win.fetch).toHaveBeenCalledTimes(1)
    vi.advanceTimersByTime(2500)
    expect(win.fetch).toHaveBeenCalledTimes(3)
    const urls = new Set(win.fetch.mock.calls.map(([u]) => u))
    expect(urls.size).toBe(1)
    expect([...urls][0]).toMatch(/\/api\/presence\/beat\?client=.+/)
  })

  it('says goodbye with sendBeacon on pagehide, for the same client', () => {
    const win = fakeWindow()
    const nav = { sendBeacon: vi.fn(() => true) }
    stop = startPresence({ win, nav })
    win.fire('pagehide')
    expect(nav.sendBeacon).toHaveBeenCalledTimes(1)
    const leave = nav.sendBeacon.mock.calls[0][0]
    const beat = win.fetch.mock.calls[0][0]
    expect(leave).toMatch(/\/api\/presence\/leave\?client=/)
    expect(leave.split('client=')[1]).toBe(beat.split('client=')[1])
  })

  it('falls back to a keepalive fetch when the beacon is refused', () => {
    const win = fakeWindow()
    stop = startPresence({ win, nav: { sendBeacon: () => false } })
    win.fire('pagehide')
    const [url, opts] = win.fetch.mock.calls.at(-1)
    expect(url).toMatch(/\/api\/presence\/leave/)
    expect(opts).toMatchObject({ method: 'POST', keepalive: true })
  })

  it('beats again when restored from the back/forward cache', () => {
    const win = fakeWindow()
    stop = startPresence({ win, nav: { sendBeacon: () => true } })
    win.fire('pageshow', { persisted: false })
    expect(win.fetch).toHaveBeenCalledTimes(1)
    win.fire('pageshow', { persisted: true })
    expect(win.fetch).toHaveBeenCalledTimes(2)
  })
})
