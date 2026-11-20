import { describe, expect, it, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import PrimeVue from 'primevue/config'
import WorkflowChooseParamsForm from './WorkflowChooseParamsForm.vue'
import WorkflowDraftChoice from './WorkflowDraftChoice.vue'

const dialog = { template: '<div><slot /><slot name="footer" /></div>' }
const CANDIDATES = { params_for_id: 'lv_params_for_id.json', candidates: [
  { name: 'm/alpha', min: 0.1, max: 7 }, { name: 'm/beta', min: 0.01, max: 2 }] }

function form(props) {
  return mount(WorkflowChooseParamsForm, {
    props: { draft: 'new-1', load: vi.fn(async () => CANDIDATES), save: vi.fn(async () => ''), ...props },
    global: { plugins: [PrimeVue], stubs: { Dialog: dialog } },
  })
}

describe('WorkflowChooseParamsForm', () => {
  it("lists the study's params_for_id entries, named as CA labels them, with their bounds", async () => {
    const f = form()
    await flushPromises()
    const list = f.find('[data-testid="workflow-choose-params-list"]').text()
    expect(list).toContain('m/alpha')
    expect(list).toContain('0.1 … 7')
    expect(f.text()).toContain('lv_params_for_id.json')
  })

  it('makes the step only with a parameter ticked and a reason, and sends both', async () => {
    const save = vi.fn(async () => '')
    const f = form({ save })
    await flushPromises()
    const button = () => f.find('[data-testid="workflow-choose-params-save"]')
    expect(button().attributes('disabled')).toBeDefined()
    await f.find('[data-testid="workflow-choose-param-m/beta"]').setValue(true)
    expect(button().attributes('disabled')).toBeDefined()
    await f.find('[data-testid="workflow-choose-params-reason"]').setValue(' only beta is constrained ')
    await button().trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledWith({ draft: 'new-1', parameters: ['m/beta'], reason: 'only beta is constrained' })
    expect(f.emitted('close')).toHaveLength(1)
  })

  it("shows why it cannot list them (no params_for_id) or CA's refusal", async () => {
    const f = form({ load: async () => { throw new Error('load the params_for_id to choose from') } })
    await flushPromises()
    expect(f.find('[data-testid="workflow-choose-params-why"]').text()).toContain('load the params_for_id')
  })
})

describe('WorkflowDraftChoice: Choose parameters', () => {
  function choice(manualReduction) {
    return mount(WorkflowDraftChoice, {
      props: { draft: { id: 'new-1', model: 'm.cellml' }, manualReduction },
      global: { plugins: [PrimeVue], stubs: { Dialog: dialog } },
    })
  }
  it('is offered where CA has a reduction chosen by hand (#686), and chooses it', async () => {
    const off = choice(false).find('[data-testid="workflow-draft-choice-params"]')
    expect(off.attributes('disabled')).toBeDefined()
    const c = choice(true)
    await c.find('[data-testid="workflow-draft-choice-params"]').trigger('click')
    expect(c.emitted('choose')[0][0]).toEqual({ chooseParams: true })
  })
})
