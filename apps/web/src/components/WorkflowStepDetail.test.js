import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import WorkflowStepDetail from './WorkflowStepDetail.vue'

const DialogStub = {
  props: ['visible', 'header'],
  template: '<div v-if="visible"><h2>{{ header }}</h2><slot /><slot name="footer" /></div>',
}
const ButtonStub = {
  props: ['label', 'disabled'],
  emits: ['click'],
  template: '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
}

const steps = [
  { id: 'sliders-1', kind: 'set', point: true, set: [{ parameter: 'm/a', value: 2, reason: 'slider values in CUFLynx' }] },
  { id: 'calibration-1', start_from: 'sliders-1', obs_data: '../../inputs/obs_data/s/o.json',
    params_for_id: '../../inputs/params_for_id/s/p.json',
    settings: { param_id_method: 'CMA-ES', optimiser_options: { num_calls_to_function: 30 } } },
]

function detail(step, extra = {}) {
  return mount(WorkflowStepDetail, {
    props: { step, steps, detail: { viewable: true, calibrates: [{ model_name: 'm/a' }] },
             result: { status: 'done', best_cost: 0.25, duration_s: 9.4, finished: '2026-11-18T12:00' },
             state: 'done', ...extra },
    global: { stubs: { Dialog: DialogStub, Button: ButtonStub } },
  })
}

describe('WorkflowStepDetail', () => {
  it('shows what set the values, the data, what it takes from, the result and settings', () => {
    const d = detail(steps[1])
    const text = d.text()
    expect(text).toContain('Step calibration-1')
    expect(text).toContain('Calibrated: an optimiser fits these values')
    expect(text).toContain('../../inputs/obs_data/s/o.json')
    expect(text).toContain('start_from sliders-1')
    expect(d.find('[data-testid="workflow-step-detail-result"]').text()).toContain('cost 0.250000')
    expect(text).toContain('"num_calls_to_function": 30')
  })

  it('changes the settings, opens the model, or deletes', async () => {
    const d = detail(steps[1])
    await d.find('[data-testid="workflow-step-detail-edit"]').trigger('click')
    await d.find('[data-testid="workflow-step-detail-open"]').trigger('click')
    await d.find('[data-testid="workflow-step-detail-delete"]').trigger('click')
    expect(d.emitted('select')).toEqual([['calibration-1']])
    expect(d.emitted('open-view')).toEqual([['calibration-1']])
    expect(d.emitted('delete')).toEqual([['calibration-1']])
  })

  it('renames the step, and says why when the name is refused', async () => {
    const rename = vi.fn().mockResolvedValueOnce("another step is already called 'sliders-1'")
      .mockResolvedValueOnce('')
    const d = detail(steps[1], { rename })
    const input = d.find('[data-testid="workflow-step-rename-input"]')
    expect(input.element.value).toBe('calibration-1')
    // unchanged: nothing to rename
    expect(d.find('[data-testid="workflow-step-rename"]').attributes('disabled')).toBeDefined()
    await input.setValue('sliders-1')
    await d.find('form').trigger('submit')
    await flushPromises()
    expect(rename).toHaveBeenLastCalledWith('calibration-1', 'sliders-1')
    expect(d.find('[data-testid="workflow-step-rename-error"]').text()).toContain('already called')
    await input.setValue(' fit_lv ')
    await d.find('form').trigger('submit')
    await flushPromises()
    expect(rename).toHaveBeenLastCalledWith('calibration-1', 'fit_lv')
    expect(d.find('[data-testid="workflow-step-rename-error"]').exists()).toBe(false)
  })

  it('a set step lists its values and has no panel to change them in', () => {
    const d = detail(steps[0])
    expect(d.text()).toContain('Set by hand')
    expect(d.text()).toContain('A point only: not merged into the target.')
    expect(d.find('[data-testid="workflow-step-detail-edit"]').exists()).toBe(false)
  })

  it('a figure step shows its function, what it reads and what it drew, and no model', () => {
    const fig = { id: 'traces', kind: 'figure', function: 'figures/standard_figures.py:sweep_traces',
                  inputs: { sweep: { sweep: 'run' } } }
    const d = detail(fig, { detail: { viewable: false }, result: { status: 'done',
      finished: 't1', figures: ['figure/traces_exp_0.png', 'figure/scores.csv'] } })
    expect(d.text()).toContain("Figures drawn from other steps' results")
    expect(d.text()).not.toContain('Model')
    expect(d.find('[data-testid="workflow-step-detail-inputs"]').text()).toBe('sweep ← sweep of run')
    const drawn = d.find('[data-testid="workflow-step-detail-figures"]')
    expect(drawn.find('img').attributes('src')).toContain('name=figure%2Ftraces_exp_0.png')
    expect(drawn.text()).toContain('scores.csv')
    expect(d.find('[data-testid="workflow-step-detail-open"]').exists()).toBe(false)
  })
})
