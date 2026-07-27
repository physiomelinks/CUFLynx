import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import {
  setNotificationCtor,
  notificationsSupported,
  notificationPermission,
  requestNotificationPermission,
  notify,
  runNotification,
  isTerminalRunState,
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
      { title: 'CUFLynx — Calibration finished', body: 'Calibration completed successfully.' },
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
