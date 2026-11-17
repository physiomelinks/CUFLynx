import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
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

  it('a set step lists its values and has no panel to change them in', () => {
    const d = detail(steps[0])
    expect(d.text()).toContain('Set by hand')
    expect(d.text()).toContain('A point only: not merged into the target.')
    expect(d.find('[data-testid="workflow-step-detail-edit"]').exists()).toBe(false)
  })
})
