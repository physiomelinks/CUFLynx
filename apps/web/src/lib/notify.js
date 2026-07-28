// Browser notifications for long-running jobs (issue #105).
//
// A thin, testable wrapper over the Web Notifications API. The API is absent in
// jsdom and in some embedded webviews (the packaged pywebview shell being the
// obvious one), so every entry point degrades to a silent no-op rather than
// throwing: a notification that fails must never break a calibration/SA/UQ run.
//
// Per the discussion on #105 the feature is opt-in and defaults to OFF, so
// `notify()` takes the enabled flag explicitly — there is no ambient state here.

// Test seam: `setNotificationCtor(FakeNotification)` injects a fake constructor,
// `setNotificationCtor(null)` simulates an unsupported browser, and
// `setNotificationCtor(undefined)` restores the real global.
let ctorOverride

export function setNotificationCtor(ctor) {
  ctorOverride = ctor
}

function notificationCtor() {
  if (ctorOverride !== undefined) return ctorOverride
  if (typeof window !== 'undefined' && 'Notification' in window) return window.Notification
  return null
}

/** True when this browser exposes the Notifications API at all. */
export function notificationsSupported() {
  return notificationCtor() != null
}

/** 'granted' | 'denied' | 'default' | 'unsupported'. */
export function notificationPermission() {
  const Ctor = notificationCtor()
  if (!Ctor) return 'unsupported'
  return Ctor.permission ?? 'default'
}

/**
 * Ask the browser for permission. Must be called from a user gesture (browsers
 * ignore — or in Chrome, auto-deny — requests that aren't), which is why the
 * Settings toggle requests it on switch-on rather than at startup.
 * Resolves to the same values as notificationPermission(); never rejects.
 */
export async function requestNotificationPermission() {
  const Ctor = notificationCtor()
  if (!Ctor) return 'unsupported'
  if (Ctor.permission === 'granted' || Ctor.permission === 'denied') return Ctor.permission
  try {
    return (await Ctor.requestPermission()) ?? 'default'
  } catch {
    return 'denied'
  }
}

/**
 * Show a notification. Silently does nothing when disabled, unsupported, or not
 * granted. Returns true only if one was actually constructed (handy in tests).
 *
 * `requireInteraction` keeps it on screen until dismissed rather than letting it
 * auto-hide after a few seconds — the whole point is that the user walked away,
 * so a toast that expires before they look back tells them nothing. It is a
 * hint, not a guarantee: Firefox and Safari ignore it, and a Linux desktop's
 * notification daemon may expire it anyway, which is why the caller also flags
 * the tab (see `setTitleAlert`).
 *
 * `tag` collapses repeat runs of the same kind onto one notification instead of
 * stacking them; `renotify` makes that replacement still alert rather than
 * swapping silently. Passing renotify without a tag throws in some browsers, so
 * it is only set alongside one.
 */
export function notify(title, body, { enabled = false, tag = '' } = {}) {
  if (!enabled) return false
  const Ctor = notificationCtor()
  if (!Ctor || Ctor.permission !== 'granted') return false
  try {
    new Ctor(title, {
      body,
      requireInteraction: true,
      ...(tag ? { tag, renotify: true } : {}),
    })
    return true
  } catch {
    return false
  }
}

// Set once per alert run, so several alerts before the user returns don't stack
// up and lose the real title.
let savedTitle = null

/**
 * Flag the browser tab so a finished run is noticeable even when the OS hid the
 * notification (or never showed one — permission denied, unsupported webview).
 *
 * Only fires when the user is actually away: hidden tab, or a visible window
 * that doesn't have focus. If they are looking at CUFLynx the panel already
 * shows the run state and rewriting the title would just be noise.
 *
 * Returns true when the title was changed.
 */
export function setTitleAlert(text) {
  if (typeof document === 'undefined' || typeof window === 'undefined') return false
  const away = document.hidden || !document.hasFocus?.()
  if (!away || !text) return false

  if (savedTitle === null) savedTitle = document.title
  document.title = `● ${text}`

  const restore = () => {
    // visibilitychange also fires on the way *out*; only restore on return.
    if (document.hidden) return
    if (savedTitle !== null) {
      document.title = savedTitle
      savedTitle = null
    }
    window.removeEventListener('focus', restore)
    document.removeEventListener('visibilitychange', restore)
  }
  window.addEventListener('focus', restore)
  document.addEventListener('visibilitychange', restore)
  return true
}

// Human labels for the three long-running jobs.
export const RUN_LABELS = {
  calibration: 'Calibration',
  sensitivity: 'Sensitivity analysis',
  uq: 'Uncertainty quantification',
}

const TERMINAL_WORD = { done: 'finished', error: 'failed', cancelled: 'cancelled' }

/** True for the run states worth interrupting the user for. */
export function isTerminalRunState(state) {
  return state in TERMINAL_WORD
}

/**
 * Build the {title, body} for a finished run, or null if `state` isn't terminal.
 * `detail.cost` (calibration's final cost) is appended when available — a user
 * who walked away mostly wants to know whether it was worth coming back for.
 */
export function runNotification(kind, state, detail = {}) {
  if (!isTerminalRunState(state)) return null
  const label = RUN_LABELS[kind] ?? 'Run'
  const title = `CUFLynx — ${label} ${TERMINAL_WORD[state]}`
  let body =
    state === 'done'
      ? `${label} completed successfully.`
      : state === 'error'
        ? `${label} stopped with an error.`
        : `${label} was cancelled.`
  if (state === 'done' && Number.isFinite(detail?.cost)) body += ` Final cost: ${detail.cost}.`
  // `tab` drops the "CUFLynx — " prefix: the tab is already CUFLynx, and the
  // browser truncates a tab title hard, so the words that matter go first.
  return { title, body, tab: `${label} ${TERMINAL_WORD[state]}`, tag: kind }
}
