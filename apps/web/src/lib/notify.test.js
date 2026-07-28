import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  setNotificationCtor,
  notificationsSupported,
  notificationPermission,
  requestNotificationPermission,
  notify,
  runNotification,
  isTerminalRunState,
  setTitleAlert,
} from './notify'

// Fake Notification constructor: records every notification constructed, and
// lets each test dictate the permission / requestPermission outcome.
function fakeNotification(permission = 'granted', requestResult = permission) {
  const shown = []
  const Ctor = function (title, options) {
    shown.push({ title, ...options })
  }
  Ctor.permission = permission
  Ctor.requestPermission = vi.fn().mockResolvedValue(requestResult)
  Ctor.shown = shown
  return Ctor
}

afterEach(() => setNotificationCtor(undefined))

describe('notify (Web Notifications wrapper, #105)', () => {
  it('is a silent no-op where the API is unsupported (jsdom, embedded webviews)', () => {
    setNotificationCtor(null)
    expect(notificationsSupported()).toBe(false)
    expect(notificationPermission()).toBe('unsupported')
    expect(() => notify('t', 'b', { enabled: true })).not.toThrow()
    expect(notify('t', 'b', { enabled: true })).toBe(false)
  })

  it('jsdom itself has no Notification, so the real global path is also a no-op', () => {
    // Guards the capability check: no injection, no window.Notification.
    expect('Notification' in window).toBe(false)
    expect(notificationsSupported()).toBe(false)
    expect(notify('t', 'b', { enabled: true })).toBe(false)
  })

  it('constructs a Notification with the expected title/body when granted', () => {
    const Ctor = fakeNotification('granted')
    setNotificationCtor(Ctor)
    expect(notify('CUFLynx — Calibration finished', 'Calibration completed successfully.', {
      enabled: true,
    })).toBe(true)
    expect(Ctor.shown).toEqual([
      {
        title: 'CUFLynx — Calibration finished',
        body: 'Calibration completed successfully.',
        requireInteraction: true,
      },
    ])
  })

  it('does not notify when permission is denied', () => {
    const Ctor = fakeNotification('denied')
    setNotificationCtor(Ctor)
    expect(notify('t', 'b', { enabled: true })).toBe(false)
    expect(Ctor.shown).toEqual([])
  })

  it('does not notify when the setting is disabled, even if granted', () => {
    const Ctor = fakeNotification('granted')
    setNotificationCtor(Ctor)
    expect(notify('t', 'b', { enabled: false })).toBe(false)
    // Disabled is also the default when no options object is passed.
    expect(notify('t', 'b')).toBe(false)
    expect(Ctor.shown).toEqual([])
  })

  it('never throws if the constructor itself blows up', () => {
    const Ctor = function () {
      throw new Error('webview refused')
    }
    Ctor.permission = 'granted'
    setNotificationCtor(Ctor)
    expect(notify('t', 'b', { enabled: true })).toBe(false)
  })
})

describe('requestNotificationPermission', () => {
  it('returns "unsupported" without touching the API', async () => {
    setNotificationCtor(null)
    await expect(requestNotificationPermission()).resolves.toBe('unsupported')
  })

  it('asks the browser when the permission is still "default"', async () => {
    const Ctor = fakeNotification('default', 'granted')
    setNotificationCtor(Ctor)
    await expect(requestNotificationPermission()).resolves.toBe('granted')
    expect(Ctor.requestPermission).toHaveBeenCalled()
  })

  it('reports a denial from the prompt', async () => {
    const Ctor = fakeNotification('default', 'denied')
    setNotificationCtor(Ctor)
    await expect(requestNotificationPermission()).resolves.toBe('denied')
  })

  it('does not re-prompt once already granted or denied', async () => {
    const granted = fakeNotification('granted')
    setNotificationCtor(granted)
    await expect(requestNotificationPermission()).resolves.toBe('granted')
    expect(granted.requestPermission).not.toHaveBeenCalled()

    const denied = fakeNotification('denied')
    setNotificationCtor(denied)
    await expect(requestNotificationPermission()).resolves.toBe('denied')
    expect(denied.requestPermission).not.toHaveBeenCalled()
  })

  it('treats a rejected request as a denial rather than throwing', async () => {
    const Ctor = fakeNotification('default')
    Ctor.requestPermission = vi.fn().mockRejectedValue(new Error('nope'))
    setNotificationCtor(Ctor)
    await expect(requestNotificationPermission()).resolves.toBe('denied')
  })
})

describe('runNotification message content', () => {
  it('names the run and how it ended', () => {
    expect(runNotification('calibration', 'done').title).toBe('CUFLynx — Calibration finished')
    expect(runNotification('sensitivity', 'error').title).toBe(
      'CUFLynx — Sensitivity analysis failed',
    )
    expect(runNotification('uq', 'cancelled').title).toBe(
      'CUFLynx — Uncertainty quantification cancelled',
    )
  })

  it('includes the final cost for calibration when available', () => {
    expect(runNotification('calibration', 'done', { cost: 0.125 }).body).toContain(
      'Final cost: 0.125',
    )
    expect(runNotification('calibration', 'done', { cost: null }).body).not.toContain('Final cost')
    expect(runNotification('calibration', 'done').body).not.toContain('Final cost')
  })

  it('returns null for non-terminal states', () => {
    expect(runNotification('calibration', 'running')).toBeNull()
    expect(runNotification('calibration', 'idle')).toBeNull()
    expect(isTerminalRunState('running')).toBe(false)
    expect(isTerminalRunState('done')).toBe(true)
    expect(isTerminalRunState('error')).toBe(true)
    expect(isTerminalRunState('cancelled')).toBe(true)
  })
})

// A notification that auto-hides after a few seconds is useless to someone who
// walked away, which is the entire use case (#105 follow-up).
describe('staying visible', () => {
  it('asks the browser to keep the notification up until dismissed', () => {
    const Ctor = fakeNotification('granted')
    setNotificationCtor(Ctor)
    notify('t', 'b', { enabled: true })
    expect(Ctor.shown[0].requireInteraction).toBe(true)
  })

  it('collapses repeat runs of one kind onto a single re-alerting notification', () => {
    const Ctor = fakeNotification('granted')
    setNotificationCtor(Ctor)
    notify('t', 'b', { enabled: true, tag: 'calibration' })
    expect(Ctor.shown[0].tag).toBe('calibration')
    expect(Ctor.shown[0].renotify).toBe(true)
  })

  it('omits renotify without a tag, which throws in some browsers', () => {
    const Ctor = fakeNotification('granted')
    setNotificationCtor(Ctor)
    notify('t', 'b', { enabled: true })
    expect('renotify' in Ctor.shown[0]).toBe(false)
    expect('tag' in Ctor.shown[0]).toBe(false)
  })

  it('offers a short tab-title form alongside the notification text', () => {
    const msg = runNotification('calibration', 'done')
    expect(msg.tab).toBe('Calibration finished')
    expect(msg.tag).toBe('calibration')
    // The full title keeps the app prefix; the tab is already CUFLynx.
    expect(msg.title).toContain('CUFLynx')
  })
})

// The tab title outlasts any OS notification, so it is the fallback when the
// desktop expires (or never shows) one.
describe('setTitleAlert', () => {
  const original = 'CUFLynx'
  let hidden = false
  let focused = true

  beforeEach(() => {
    document.title = original
    hidden = false
    focused = true
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden })
    vi.spyOn(document, 'hasFocus').mockImplementation(() => focused)
  })
  afterEach(() => vi.restoreAllMocks())

  it('flags the title when the tab is hidden', () => {
    hidden = true
    expect(setTitleAlert('Calibration finished')).toBe(true)
    expect(document.title).toContain('Calibration finished')
  })

  it('flags it for a visible window that does not have focus', () => {
    focused = false
    expect(setTitleAlert('Calibration finished')).toBe(true)
    expect(document.title).toContain('Calibration finished')
  })

  // Looking at the app already shows the run state; rewriting the title is noise.
  it('leaves the title alone when the user is right there', () => {
    expect(setTitleAlert('Calibration finished')).toBe(false)
    expect(document.title).toBe(original)
  })

  it('restores the real title once the user comes back', () => {
    hidden = true
    setTitleAlert('Calibration finished')
    hidden = false
    document.dispatchEvent(new Event('visibilitychange'))
    expect(document.title).toBe(original)
  })

  it('restores on window focus too, for a merely unfocused window', () => {
    focused = false
    setTitleAlert('Calibration finished')
    focused = true
    window.dispatchEvent(new Event('focus'))
    expect(document.title).toBe(original)
  })

  // visibilitychange fires on the way out as well; that must not "restore" the
  // alert away while the user is still gone.
  it('keeps the flag when the tab is hidden again before the user returns', () => {
    hidden = true
    setTitleAlert('Calibration finished')
    document.dispatchEvent(new Event('visibilitychange'))
    expect(document.title).toContain('Calibration finished')
  })

  // Two runs finishing while away must not leave the flagged title saved as the
  // "real" one, which would make it permanent.
  it('survives a second alert before the user returns', () => {
    hidden = true
    setTitleAlert('Calibration finished')
    setTitleAlert('Sensitivity analysis finished')
    expect(document.title).toContain('Sensitivity analysis finished')
    hidden = false
    document.dispatchEvent(new Event('visibilitychange'))
    expect(document.title).toBe(original)
  })
})
