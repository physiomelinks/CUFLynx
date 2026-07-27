import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { ref, nextTick, defineComponent } from 'vue'
import { mount } from '@vue/test-utils'
import { useRunNotifications } from './useRunNotifications'
import { setNotificationCtor } from '../lib/notify'

// Records notifications, standing in for the browser API (absent in jsdom).
function fakeNotification() {
  const shown = []
  const Ctor = function (title, options) {
    shown.push({ title, ...options })
  }
  Ctor.permission = 'granted'
  Ctor.requestPermission = async () => 'granted'
  Ctor.shown = shown
  return Ctor
}

// useRunNotifications registers watchers, which need an active component scope.
function harness(runs, enabled) {
  return mount(
    defineComponent({
      setup() {
        useRunNotifications(runs, enabled)
        return () => null
      },
    }),
  )
}

describe('useRunNotifications (#105)', () => {
  let Ctor
  let calib
  let sa
  let uq
  let enabled

  beforeEach(() => {
    Ctor = fakeNotification()
    setNotificationCtor(Ctor)
    calib = ref('idle')
    sa = ref('idle')
    uq = ref('idle')
    enabled = ref(true)
  })

  afterEach(() => setNotificationCtor(undefined))

  const mountAll = () =>
    harness(
      [
        { kind: 'calibration', state: calib, detail: () => ({ cost: 0.5 }) },
        { kind: 'sensitivity', state: sa },
        { kind: 'uq', state: uq },
      ],
      enabled,
    )

  it('fires once when a run goes running -> done', async () => {
    mountAll()
    calib.value = 'running'
    await nextTick()
    expect(Ctor.shown).toHaveLength(0) // nothing while still running

    calib.value = 'done'
    await nextTick()
    expect(Ctor.shown).toHaveLength(1)
    expect(Ctor.shown[0].title).toBe('CUFLynx — Calibration finished')
    expect(Ctor.shown[0].body).toContain('Final cost: 0.5')

    // Re-entering the same terminal state must not fire again.
    calib.value = 'done'
    await nextTick()
    expect(Ctor.shown).toHaveLength(1)
  })

  it('fires on error and on cancelled, not just done', async () => {
    mountAll()
    sa.value = 'running'
    await nextTick()
    sa.value = 'error'
    await nextTick()
    uq.value = 'running'
    await nextTick()
    uq.value = 'cancelled'
    await nextTick()

    expect(Ctor.shown.map((n) => n.title)).toEqual([
      'CUFLynx — Sensitivity analysis failed',
      'CUFLynx — Uncertainty quantification cancelled',
    ])
  })

  it('stays quiet while a run is still running', async () => {
    mountAll()
    calib.value = 'running'
    sa.value = 'running'
    uq.value = 'running'
    await nextTick()
    expect(Ctor.shown).toEqual([])
  })

  it('stays quiet when the setting is off', async () => {
    enabled.value = false
    mountAll()
    calib.value = 'running'
    await nextTick()
    calib.value = 'done'
    await nextTick()
    expect(Ctor.shown).toEqual([])
  })

  it('ignores terminal states not reached from running (e.g. a restored result)', async () => {
    mountAll()
    calib.value = 'done' // idle -> done, never ran in this session
    await nextTick()
    expect(Ctor.shown).toEqual([])
  })

  it('keeps the runs independent', async () => {
    mountAll()
    sa.value = 'running'
    await nextTick()
    sa.value = 'done'
    await nextTick()
    expect(Ctor.shown).toHaveLength(1)
    expect(Ctor.shown[0].title).toBe('CUFLynx — Sensitivity analysis finished')
  })
})
