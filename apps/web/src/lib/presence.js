// Tell the server this tab is open, and when it goes away.
//
// The desktop shell falls back to the system browser when it cannot open a
// native window, and then has no window whose closing ends the process. Without
// these reports it never learns the user has gone, and keeps running (with its
// unpacked /tmp/_MEI* directory) until logout. See apps/api/presence.py.
//
// Plain HTTP on purpose: no pywebview API, so a served web app behaves the same
// (the server just records beats nobody reads).

const baseURL = import.meta.env?.VITE_API_URL ?? ''

// Well inside the server's stale_after (150 s). A hidden tab's timers may be
// throttled to once a minute, which that margin already allows for.
export const BEAT_INTERVAL_MS = 20_000

function newClientId() {
  const c = globalThis.crypto
  if (c?.randomUUID) return c.randomUUID()
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
}

/**
 * Start reporting. Returns a function that stops it (for tests).
 *
 * `win` and `nav` are injectable so the lifecycle can be tested without a real
 * page; in the app they are `window` and `navigator`.
 */
export function startPresence({ win = window, nav = navigator, interval = BEAT_INTERVAL_MS } = {}) {
  const client = encodeURIComponent(newClientId())
  const beatUrl = `${baseURL}/api/presence/beat?client=${client}`
  const leaveUrl = `${baseURL}/api/presence/leave?client=${client}`

  const beat = () => {
    // keepalive so a beat in flight while the page unloads is not cancelled.
    win.fetch(beatUrl, { method: 'POST', keepalive: true }).catch(() => {})
  }
  // sendBeacon is the one request a closing page is guaranteed to get out.
  const leave = () => {
    if (!nav.sendBeacon?.(leaveUrl)) {
      win.fetch(leaveUrl, { method: 'POST', keepalive: true }).catch(() => {})
    }
  }
  // A page restored from the back/forward cache said goodbye on the way out.
  const onShow = (e) => {
    if (e.persisted) beat()
  }

  beat()
  const timer = win.setInterval(beat, interval)
  win.addEventListener('pagehide', leave)
  win.addEventListener('pageshow', onShow)

  return () => {
    win.clearInterval(timer)
    win.removeEventListener('pagehide', leave)
    win.removeEventListener('pageshow', onShow)
  }
}
