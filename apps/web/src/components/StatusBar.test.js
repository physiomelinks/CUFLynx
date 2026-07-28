import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StatusBar from './StatusBar.vue'

// Message renders a slot, so stub it transparently rather than installing PrimeVue.
const stubs = {
  Message: { template: '<div class="msg" v-bind="$attrs"><slot /></div>' },
  ProgressSpinner: true,
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
