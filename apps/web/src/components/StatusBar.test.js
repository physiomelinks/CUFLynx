import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import StatusBar from './StatusBar.vue'

// Message renders a slot, so stub it transparently rather than installing PrimeVue.
const stubs = {
  Message: { template: '<div class="msg" v-bind="$attrs"><slot /></div>' },
  ProgressSpinner: true,
  Button: {
    props: ['label'],
    emits: ['click'],
    template: '<button v-bind="$attrs" @click="$emit(\'click\')">{{ label }}</button>',
  },
}

const mountBar = (props) => mount(StatusBar, { props, global: { stubs } })

describe('StatusBar', () => {
  it('shows Ready when idle and Done when ok', () => {
    expect(mountBar({ status: 'idle' }).text()).toContain('Ready')
    expect(mountBar({ status: 'ok', lastRunMs: 42 }).text()).toContain('Done')
  })

  // Issue #138: a simulation failure is now several lines — the solver's reason,
  // the settings it failed under, then what to change. Collapsing those into one
  // run of text is what the old single-line bar did.
  it('keeps the line breaks of a multi-line failure', () => {
    const message = [
      'Simulation failed: CVode() failed with flag CV_TOO_MUCH_ACC.',
      'Settings in force: solver=CVODE_myokit, MaximumStep=100.0.',
      'Raise rtol/atol in Settings.',
    ].join('\n')
    const bar = mountBar({ status: 'error', message })
    const el = bar.find('[data-testid="status-error"]')
    expect(el.exists()).toBe(true)
    expect(el.text()).toContain('CV_TOO_MUCH_ACC')
    expect(el.text()).toContain('MaximumStep=100.0')
    // pre-line is what preserves them; a plain <span> would not.
    expect(el.classes()).toContain('status-error')
  })

  it('shows no error element when the run succeeded', () => {
    expect(mountBar({ status: 'ok' }).find('[data-testid="status-error"]').exists()).toBe(
      false,
    )
  })
})

// A whole-cell model's live run can take minutes: the bar says how long it has
// been running and offers a way out, but not for a run that ends in a moment.
describe('StatusBar slow runs', () => {
  afterEach(() => vi.useRealTimers())

  it('shows the elapsed time and Cancel once a run is slow', async () => {
    vi.useFakeTimers()
    const bar = mountBar({ status: 'running', startedAt: Date.now() })
    expect(bar.find('[data-testid="status-cancel"]').exists()).toBe(false)
    await vi.advanceTimersByTimeAsync(5000)
    expect(bar.find('[data-testid="status-running"]').text()).toMatch(/5 s/)
    await bar.find('[data-testid="status-cancel"]').trigger('click')
    expect(bar.emitted('cancel')).toHaveLength(1)
  })

  it('says why a cancel did not stop the run', () => {
    const bar = mountBar({
      status: 'running',
      startedAt: Date.now(),
      cancelNote: 'Live runs in the app\'s own interpreter cannot be stopped',
    })
    expect(bar.find('[data-testid="status-cancel-note"]').text()).toContain('cannot be stopped')
  })

  it('says a cancelled run was cancelled', () => {
    expect(mountBar({ status: 'cancelled' }).find('[data-testid="status-cancelled"]').exists()).toBe(true)
  })
})
