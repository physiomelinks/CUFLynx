import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import WorkflowSweepForm from './WorkflowSweepForm.vue'

const DialogStub = {
  props: ['visible', 'header'],
  template: '<div v-if="visible"><h2>{{ header }}</h2><slot /><slot name="footer" /></div>',
}
const ButtonStub = {
  props: ['label', 'disabled'],
  emits: ['click'],
  template: '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
}

function form(save) {
  return mount(WorkflowSweepForm, {
    props: { draft: 'new-2', save, outputsOffered: ['m/x', 'm/y', 'n/z'],
             parametersOffered: ['m/alpha', 'm/beta'] },
    global: { stubs: { Dialog: DialogStub, Button: ButtonStub } },
  })
}

describe('WorkflowSweepForm', () => {
  it("writes a simulation: the study's protocol, what it records, what it sweeps", async () => {
    const save = vi.fn().mockResolvedValue('')
    const f = form(save)
    expect(f.text()).toContain('Simulation new-2')
    await f.find('[data-testid="workflow-sweep-filter"]').setValue('m/')
    const outs = f.findAll('input[type="checkbox"]')
    expect(outs).toHaveLength(2)
    await outs[0].setValue(true)
    await f.find('[data-testid="workflow-sweep-add"]').trigger('click')
    const row = f.find('[data-testid="workflow-sweep-row-0"]')
    await row.find('input').setValue('m/alpha')
    await row.find('input:not([list])').setValue('0.5, 1 2')
    await f.find('[data-testid="workflow-sweep-add"]').trigger('click')
    const row2 = f.find('[data-testid="workflow-sweep-row-1"]')
    await row2.find('input').setValue('m/beta')
    await row2.find('select').setValue('range')
    const nums = row2.findAll('input[type="number"]')
    await nums[0].setValue(1)
    await nums[1].setValue(100)
    await nums[2].setValue(3)
    await row2.findAll('select')[1].setValue('log')
    await f.find('[data-testid="workflow-sweep-design"]').setValue('one_at_a_time')
    await f.find('[data-testid="workflow-sweep-save"]').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledWith({
      draft: 'new-2', obsData: null, outputs: ['m/x'], design: 'one_at_a_time', dt: null,
      parameters: { 'm/alpha': { values: [0.5, 1, 2] },
                    'm/beta': { min: 1, max: 100, points: 3, scale: 'log' } } })
    expect(f.emitted('close')).toHaveLength(1)
  })

  it('says what is missing, and shows the server’s reason', async () => {
    const save = vi.fn().mockResolvedValue('sweep step "new-2" needs ...')
    const f = form(save)
    await f.find('[data-testid="workflow-sweep-add"]').trigger('click')
    const row = f.find('[data-testid="workflow-sweep-row-0"]')
    await row.find('input').setValue('m/alpha')
    await row.find('input:not([list])').setValue('one, two')
    await f.find('[data-testid="workflow-sweep-save"]').trigger('click')
    expect(f.find('[data-testid="workflow-sweep-error"]').text()).toContain('give numbers')
    expect(save).not.toHaveBeenCalled()
    await row.find('input:not([list])').setValue('1, 2')
    await f.find('[data-testid="workflow-sweep-save"]').trigger('click')
    await flushPromises()
    expect(f.find('[data-testid="workflow-sweep-error"]').text()).toContain('needs')
    expect(f.emitted('close')).toBeUndefined()
  })
})
