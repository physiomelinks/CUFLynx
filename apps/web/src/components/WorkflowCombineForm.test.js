import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import WorkflowCombineForm from './WorkflowCombineForm.vue'

function form(props) {
  return mount(WorkflowCombineForm, {
    props: { draft: 'new-1', inputs: [], save: vi.fn(async () => ''), ...props },
    global: { plugins: [PrimeVue], stubs: { Dialog: { template: '<div><slot /><slot name="footer" /></div>' } } },
  })
}

describe('WorkflowCombineForm', () => {
  it('asks for two or more steps drawn into it before it can be made', () => {
    const f = form({ inputs: ['fit-a'] })
    expect(f.find('[data-testid="workflow-combine-hint"]').exists()).toBe(true)
    expect(f.find('[data-testid="workflow-combine-save"]').attributes('disabled')).toBeDefined()
  })

  it('makes the step with the method chosen, and closes', async () => {
    const save = vi.fn(async () => '')
    const f = form({ inputs: ['fit-a', 'fit-b'], save })
    expect(f.find('[data-testid="workflow-combine-inputs"]').text()).toContain('fit-a, fit-b')
    await f.find('[data-testid="workflow-combine-method-mean"]').setValue(true)
    await f.find('[data-testid="workflow-combine-save"]').trigger('click')
    await new Promise((r) => setTimeout(r))
    expect(save).toHaveBeenCalledWith({ draft: 'new-1', method: 'mean' })
    expect(f.emitted('close')).toHaveLength(1)
  })

  it("shows CA's reason when it refuses", async () => {
    const f = form({ inputs: ['fit-a', 'fit-b'], save: async () => 'best_fit needs the same obs_data' })
    await f.find('[data-testid="workflow-combine-save"]').trigger('click')
    await new Promise((r) => setTimeout(r))
    expect(f.find('[data-testid="workflow-combine-why"]').text()).toContain('same obs_data')
    expect(f.emitted('close')).toBeUndefined()
  })
})
