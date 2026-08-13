import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import MCMCProgress from './MCMCProgress.vue'
import ProgressPanel from './ProgressPanel.vue'

/** A payload shaped like /api/uq/{job}/progress. */
function payload(overrides = {}) {
  const steps = [0, 1, 2, 3]
  return {
    steps: 4,
    walkers: 20,
    walkers_shown: 2,
    num_params: 1,
    param_labels: ['\\alpha'],
    trace_steps: steps,
    traces: [[[0.1, 0.2, 0.3, 0.4], [0.5, 0.4, 0.3, 0.2]]],
    cumulative_mean: {
      steps: [0, 1, 2, 3],
      burn_in: 2,
      series: [{ from_start: [0.1, 0.15, 0.2, 0.25], from_burn_in: [null, null, 0.2, 0.22] }],
    },
    autocorrelation: { lags: steps, series: [[[1, 0.5, 0.1, 0.0], [1, 0.6, 0.2, 0.05]]],
      bounded: true },
    ...overrides,
  }
}

describe('MCMCProgress', () => {
  it('distinguishes "not started" from "started but no chain yet"', () => {
    // The two look identical on screen unless it says so, and a user watching a run that has
    // written nothing for a minute needs to know which of the two they are looking at.
    const idle = mount(MCMCProgress, { props: { progress: null, running: false } })
    expect(idle.find('[data-testid="mcmc-empty"]').text()).toContain('Run an MCMC analysis')

    const waiting = mount(MCMCProgress, { props: { progress: null, running: true } })
    expect(waiting.find('[data-testid="mcmc-empty"]').text()).toContain('first chain checkpoint')
  })

  it('draws one panel per parameter, one path per walker shown', () => {
    const w = mount(MCMCProgress, { props: { progress: payload() } })
    const panels = w.findAll('[data-testid="mcmc-panel"]')
    expect(panels).toHaveLength(1)
    expect(panels[0].findAll('path.walker')).toHaveLength(2)
  })

  it('says how many walkers ran, not just how many are drawn', () => {
    // Drawing a sample keeps the payload small, but a plot implying only 2 chains ran when 20
    // did would misrepresent the run.
    const w = mount(MCMCProgress, { props: { progress: payload() } })
    expect(w.find('[data-testid="mcmc-steps"]').text()).toContain('2')
    expect(w.find('[data-testid="mcmc-steps"]').text()).toContain('20')
  })

  it('switches between the three views the issue asks for', async () => {
    const w = mount(MCMCProgress, { props: { progress: payload() } })
    for (const view of ['trace', 'cumulative', 'autocorrelation']) {
      await w.find(`[data-testid="mcmc-view-${view}"]`).trigger('click')
      expect(w.findAll('[data-testid="mcmc-panel"]').length).toBe(1)
    }
  })

  it('draws the ±0.1 band only on the autocorrelation, where it means something', async () => {
    const w = mount(MCMCProgress, { props: { progress: payload() } })
    expect(w.findAll('line.guide')).toHaveLength(0)

    await w.find('[data-testid="mcmc-view-autocorrelation"]').trigger('click')
    // +0.1, 0 and -0.1: the thresholds the plot is read against.
    expect(w.findAll('line.guide')).toHaveLength(3)
  })

  it('reports whether the chain is producing independent draws', async () => {
    const w = mount(MCMCProgress, { props: { progress: payload() } })
    await w.find('[data-testid="mcmc-view-autocorrelation"]').trigger('click')
    expect(w.find('[data-testid="mcmc-bounded"]').text()).toContain('within')

    const stuck = mount(MCMCProgress, {
      props: { progress: payload({ autocorrelation: { ...payload().autocorrelation,
        bounded: false } }) },
    })
    await stuck.find('[data-testid="mcmc-view-autocorrelation"]').trigger('click')
    expect(stuck.find('[data-testid="mcmc-bounded"]').text()).toContain('still correlated')
  })

  it('explains an empty cumulative mean instead of showing a blank panel', async () => {
    const w = mount(MCMCProgress, { props: { progress: payload({ cumulative_mean: null }) } })
    await w.find('[data-testid="mcmc-view-cumulative"]').trigger('click')
    expect(w.find('[data-testid="mcmc-empty"]').text()).toContain('Not enough steps')
  })

  it('draws both cumulative lines, labelled, with the burn-in named', async () => {
    const w = mount(MCMCProgress, { props: { progress: payload() } })
    await w.find('[data-testid="mcmc-view-cumulative"]').trigger('click')

    expect(w.findAll('[data-testid="mcmc-panel"]')[0].findAll('path.walker')).toHaveLength(2)
    const legend = w.find('[data-testid="mcmc-legend"]').text()
    expect(legend).toContain('from step 0')
    expect(legend).toContain('burn-in (step 2)')
  })

  it('breaks the burn-in line rather than joining across the steps it does not cover', async () => {
    // null is a gap, not a zero: drawing through it would show a slope nobody sampled.
    const w = mount(MCMCProgress, { props: { progress: payload() } })
    await w.find('[data-testid="mcmc-view-cumulative"]').trigger('click')
    const paths = w.findAll('[data-testid="mcmc-panel"]')[0].findAll('path.walker')
    const burnInPath = paths[1].attributes('d')
    // Starts at the burn-in, not at step 0: one move command, and only two points.
    expect((burnInPath.match(/M/g) || []).length).toBe(1)
    expect((burnInPath.match(/[ML]/g) || []).length).toBe(2)
  })

  it('gives every panel a labelled, ticked axis', () => {
    const w = mount(MCMCProgress, { props: { progress: payload() } })
    const panel = w.find('[data-testid="mcmc-panel"]')
    expect(panel.findAll('.tick').length).toBeGreaterThan(0)
    expect(panel.text()).toContain('step')
  })
})

describe('ProgressPanel with a running MCMC', () => {
  it('shows the chain while sampling, where calibration progress would be', () => {
    // An MCMC run writes no cost history, so without this the Progress tab said "run a
    // calibration" for the entire run.
    const w = mount(ProgressPanel, { props: { uqRunning: true, uqProgress: payload() } })
    expect(w.find('[data-testid="mcmc-progress"]').exists()).toBe(true)
    expect(w.text()).not.toContain('Run a calibration')
  })

  it('still says to run a calibration when nothing at all is going on', () => {
    const w = mount(ProgressPanel)
    expect(w.text()).toContain('Run a calibration')
    expect(w.find('[data-testid="mcmc-progress"]').exists()).toBe(false)
  })
})

describe('the section outlives the run', () => {
  it('keeps the MCMC section after the run finishes', () => {
    // It used to vanish the moment `running` went false, which is exactly when a user turns to
    // look at it. The calibration charts persist; so does this.
    const w = mount(ProgressPanel, {
      props: { uqRunning: false, uqState: 'done', uqProgress: payload() },
    })
    expect(w.find('[data-testid="mcmc-progress"]').exists()).toBe(true)
    expect(w.findAll('[data-testid="mcmc-panel"]').length).toBe(1)
  })

  it('stays put and explains itself when a finished run wrote no chain', () => {
    // A Laplace run writes none; an MCMC run that failed early leaves none. Telling someone
    // who just ran one to "run an MCMC analysis" sends them looking in the wrong place.
    const w = mount(ProgressPanel, {
      props: { uqRunning: false, uqState: 'done', uqProgress: null },
    })
    expect(w.find('[data-testid="mcmc-progress"]').exists()).toBe(true)
    expect(w.find('[data-testid="mcmc-empty"]').text()).toContain('without writing a chain')
  })

  it('keeps a cancelled run’s partial chain on screen', () => {
    const w = mount(ProgressPanel, {
      props: { uqRunning: false, uqState: 'cancelled', uqProgress: payload() },
    })
    expect(w.findAll('[data-testid="mcmc-panel"]').length).toBe(1)
  })
})
