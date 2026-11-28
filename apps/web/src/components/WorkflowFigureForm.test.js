import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import WorkflowFigureForm from './WorkflowFigureForm.vue'

// what /api/workflow/figure_functions answers, trimmed to what the form reads
const OFFERED = {
  functions: [
    { function: 'figures/standard_figures.py:best_fit', standard: true, name: 'best_fit',
      parameters: ['fit'], doc: 'each data_item against the calibrated model' },
    { function: 'figures/standard_figures.py:mcmc_diagnostics', standard: true,
      name: 'mcmc_diagnostics', parameters: ['chain'], doc: '' },
    { function: 'figures/mine.py:draw', standard: false, name: 'draw',
      parameters: ['scan', 'fit'], doc: '' },
  ],
  kinds: {
    best_fit: { step_kinds: ['calibration'], description: 'the best fit' },
    mcmc_chain: { step_kinds: ['calibration'], description: 'the chain' },
    values: { step_kinds: ['calibration', 'set', 'combine'], description: 'values' },
    sweep: { step_kinds: ['sweep', 'simulate'], description: 'the sweep' },
  },
}
const STEPS = [{ id: 'fit-a' }, { id: 'fit-b', kind: 'calibration' }, { id: 'scan', kind: 'sweep' },
  { id: 'old-fig', kind: 'figure' }]

async function form(props) {
  const f = mount(WorkflowFigureForm, {
    props: { draft: 'new-1', steps: STEPS, load: vi.fn(async () => OFFERED),
      save: vi.fn(async () => ''), ...props },
    global: { plugins: [PrimeVue], stubs: { Dialog: { template: '<div><slot /><slot name="footer" /></div>' } } },
  })
  await flushPromises()
  return f
}
const pick = (f, id, value) => f.find(`[data-testid="${id}"]`).setValue(value)

describe('WorkflowFigureForm', () => {
  it('groups the standard figures apart from the scripts beside the workflow', async () => {
    const f = await form()
    const groups = f.findAll('[data-testid="workflow-figure-function"] optgroup')
    expect(groups.map((g) => g.attributes('label'))).toEqual(
      ['Standard figures (libcuflynx)', 'Scripts beside the workflow'])
    expect(groups[1].text()).toContain('figures/mine.py:draw')
  })

  it('reads an input named like a kind (or like its one-input function) from the latest step giving it', async () => {
    const f = await form()
    await pick(f, 'workflow-figure-function', 'figures/standard_figures.py:best_fit')
    expect(f.find('[data-testid="workflow-figure-doc"]').text()).toContain('calibrated model')
    expect(f.find('[data-testid="workflow-figure-step-fit"]').element.value).toBe('fit-b')
    expect(f.find('[data-testid="workflow-figure-kind-fit"]').element.value).toBe('best_fit')
    // a figure step is never offered as one to read
    expect(f.find('[data-testid="workflow-figure-step-fit"]').text()).not.toContain('old-fig')
  })

  it('offers only the kinds the chosen step gives, and waits until every input has both', async () => {
    const save = vi.fn(async () => '')
    const f = await form({ save })
    await pick(f, 'workflow-figure-function', 'figures/mine.py:draw')
    const button = () => f.find('[data-testid="workflow-figure-save"]')
    expect(button().attributes('disabled')).toBeDefined()
    // a sweep gives one kind, so it is chosen
    await pick(f, 'workflow-figure-step-scan', 'scan')
    expect(f.find('[data-testid="workflow-figure-kind-scan"]').element.value).toBe('sweep')
    // a calibration gives several: none is guessed for a parameter not named like one
    await pick(f, 'workflow-figure-step-fit', 'fit-a')
    const kinds = f.findAll('[data-testid="workflow-figure-kind-fit"] option').map((o) => o.element.value)
    expect(kinds).toEqual(['', 'best_fit', 'mcmc_chain', 'values'])
    expect(button().attributes('disabled')).toBeDefined()
    await pick(f, 'workflow-figure-kind-fit', 'values')
    await pick(f, 'workflow-figure-description', 'scan and fit')
    await button().trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledWith({ draft: 'new-1', function: 'figures/mine.py:draw',
      inputs: { scan: { step: 'scan', kind: 'sweep' }, fit: { step: 'fit-a', kind: 'values' } },
      description: 'scan and fit' })
    expect(f.emitted('close')).toHaveLength(1)
  })

  it("shows CA's reason when it refuses, and stays open", async () => {
    const f = await form({ save: async () => 'the parameters of "draw" are [...]' })
    await pick(f, 'workflow-figure-function', 'figures/standard_figures.py:best_fit')
    await f.find('[data-testid="workflow-figure-save"]').trigger('click')
    await flushPromises()
    expect(f.find('[data-testid="workflow-figure-why"]').text()).toContain('parameters of')
    expect(f.emitted('close')).toBeUndefined()
  })

  it('says why when the figures cannot be listed', async () => {
    const f = await form({ load: async () => { throw new Error('no workflow is open') } })
    expect(f.find('[data-testid="workflow-figure-load-error"]').text()).toContain('no workflow')
  })
})
