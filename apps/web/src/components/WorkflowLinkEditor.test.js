import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import WorkflowLinkEditor from './WorkflowLinkEditor.vue'
import { linkChoices, linkEntry } from '../lib/workflowTarget'

vi.mock('../lib/api', () => ({
  getWorkflowEdge: vi.fn().mockResolvedValue({
    what: 'values fixed', rows: [{ name: 'm/a', value: 1.5, units: 'mM', note: '' }] }),
  errorMessage: (e) => String(e?.message ?? e),
}))

const DialogStub = {
  props: ['visible', 'header'],
  template: '<div v-if="visible"><h2>{{ header }}</h2><slot /><slot name="footer" /></div>',
}
const ButtonStub = {
  props: ['label', 'disabled'],
  emits: ['click'],
  template: '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
}

// CA's SOURCE_KINDS and the kinds that take each key (describe's link_kinds)
const LINK_KINDS = {
  fixed_from: { sources: ['calibration', 'set'], users: ['calibration', 'sensitivity', 'uq'] },
  priors_from: { sources: ['calibration', 'uq'], users: ['calibration'] },
  start_from: { sources: ['calibration', 'set'], users: ['calibration'] },
  result_from: { sources: ['calibration', 'set'], users: ['sensitivity', 'uq'] },
  sensitivity_from: { sources: ['sensitivity'], users: ['reduction'] },
}
const steps = [
  { id: 'fit_a' },
  { id: 'mcmc-1', kind: 'uq', result_from: 'fit_a' },
  { id: 'fit_b', fixed_from: ['fit_a'],
    priors_from: [{ step: 'mcmc-1', kind: 'normal', parameters: ['m/a'] }] },
  { id: 'new-1', draft: true },
]
const details = { fit_a: { calibrates: [{ model_name: 'm/a' }, { model_name: 'm/b' }] },
                  'mcmc-1': { calibrates: [{ model_name: 'm/a' }, { model_name: 'm/b' }] } }

function editor(arrow, extra = {}) {
  return mount(WorkflowLinkEditor, {
    props: { arrow, steps, details, linkKinds: LINK_KINDS, save: vi.fn().mockResolvedValue(''),
             remove: vi.fn().mockResolvedValue(''), ...extra },
    global: { stubs: { Dialog: DialogStub, Button: ButtonStub } },
  })
}

describe('linkChoices / linkEntry', () => {
  it('offers what CA lets the source give and the target take; a new step takes anything', () => {
    const byId = Object.fromEntries(steps.map((s) => [s.id, s]))
    expect(linkChoices(LINK_KINDS, byId.fit_a, byId.fit_b))
      .toEqual(['fixed_from', 'priors_from', 'start_from'])
    expect(linkChoices(LINK_KINDS, byId['mcmc-1'], byId.fit_b)).toEqual(['priors_from'])
    expect(linkChoices(LINK_KINDS, byId.fit_a, byId['new-1']))
      .toEqual(['fixed_from', 'priors_from', 'start_from', 'result_from'])
  })

  it("reads an arrow's options from its target step", () => {
    expect(linkEntry(steps[2], 'fit_a', 'fixed_from')).toEqual({})
    expect(linkEntry(steps[2], 'mcmc-1', 'priors_from')).toEqual({ kind: 'normal', parameters: ['m/a'] })
    expect(linkEntry(steps[1], 'fit_a', 'result_from')).toEqual({})
    expect(linkEntry(steps[2], 'mcmc-1', 'fixed_from')).toBeNull()
  })
})

describe('WorkflowLinkEditor', () => {
  it('shows what passes now, read from the run, and offers to pass more', async () => {
    const save = vi.fn().mockResolvedValue('')
    const e = editor({ source: 'fit_a', target: 'fit_b' }, { save })
    await flushPromises()
    expect(e.text()).toContain('fit_a → fit_b')
    const fixed = e.find('[data-testid="workflow-arrow-key-fixed_from"]')
    expect(fixed.text()).toContain('Fixed values')
    // what passes, from the run
    expect(fixed.find('[data-testid="workflow-edge-rows"]').text()).toContain('1.5000')
    // this CA fixes every value of the source
    expect(fixed.find('[data-testid="workflow-link-fixed-all"]').exists()).toBe(true)
    // pass more: what fit_b can still take from fit_a
    const menu = e.find('[data-testid="workflow-link-more-select"]')
    expect(menu.findAll('option').map((o) => o.element.value).slice(1))
      .toEqual(['priors_from', 'start_from'])
    await menu.setValue('start_from')
    await e.find('[data-testid="workflow-link-add"]').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledWith(
      { source: 'fit_a', target: 'fit_b', key: 'start_from', options: null, oldKey: null })
  })

  it('a new arrow with one choice has it ready to pass', async () => {
    const save = vi.fn().mockResolvedValue('')
    const e = editor({ source: 'mcmc-1', target: 'new-1' }, { save,
      linkKinds: { priors_from: LINK_KINDS.priors_from } })
    expect(e.text()).toContain('Nothing passes')
    expect(e.find('[data-testid="workflow-link-more-select"]').element.value).toBe('priors_from')
    expect(e.find('[data-testid="workflow-link-prior-kind"]').exists()).toBe(true)
    await e.find('[data-testid="workflow-link-inflate"]').setValue(3)
    await e.find('[data-testid="workflow-link-add"]').trigger('click')
    await flushPromises()
    expect(save.mock.calls[0][0]).toMatchObject({ key: 'priors_from',
      options: { inflate: 3, objective: 'likelihood' } })
  })

  it("changes a prior's options and stops passing something", async () => {
    const save = vi.fn().mockResolvedValue('')
    const remove = vi.fn().mockResolvedValue('')
    const e = editor({ source: 'mcmc-1', target: 'fit_b' }, { save, remove })
    const prior = e.find('[data-testid="workflow-arrow-key-priors_from"]')
    expect(prior.find('[data-testid="workflow-link-prior-kind"]').element.value).toBe('normal')
    const boxes = prior.findAll('[data-testid="workflow-link-params-priors_from"] input')
    expect(boxes.map((b) => b.element.checked)).toEqual([true, false])
    await boxes[1].setValue(true)
    await prior.find('[data-testid="workflow-link-save-priors_from"]').trigger('click')
    await flushPromises()
    // every parameter chosen: none named; the objective was not set, so it stays off
    expect(save).toHaveBeenCalledWith({ source: 'mcmc-1', target: 'fit_b', key: 'priors_from',
      options: { kind: 'normal' }, oldKey: null })
    await prior.find('[data-testid="workflow-link-remove-priors_from"]').trigger('click')
    await flushPromises()
    expect(remove).toHaveBeenCalledWith({ source: 'mcmc-1', target: 'fit_b', key: 'priors_from' })
    // that was the only thing it passed: the dialog closes
    expect(e.emitted('close')).toHaveLength(1)
  })

  it("shows CA's reason when it refuses", async () => {
    const save = vi.fn().mockResolvedValue('step "fit_b" takes priors, but ...')
    const e = editor({ source: 'fit_a', target: 'fit_b' }, { save })
    await e.find('[data-testid="workflow-link-more-select"]').setValue('priors_from')
    await e.find('[data-testid="workflow-link-add"]').trigger('click')
    await flushPromises()
    expect(e.find('[data-testid="workflow-link-error"]').text()).toContain('takes priors')
    expect(e.emitted('close')).toBeUndefined()
  })

  it('a fixed arrow names its parameters where CA takes them', async () => {
    const save = vi.fn().mockResolvedValue('')
    const e = editor({ source: 'fit_a', target: 'fit_b' }, { save, fixedParameters: true })
    await flushPromises()
    const fixed = e.find('[data-testid="workflow-arrow-key-fixed_from"]')
    const params = fixed.find('[data-testid="workflow-link-params-fixed_from"]')
    // each parameter with its value now, as the run reports it
    expect(params.text()).toContain('m/a')
    expect(params.text()).toContain('1.5000 mM')
    expect(params.text()).toContain('2 of 2')
    const boxes = params.findAll('input')
    await boxes[0].setValue(false)
    await fixed.find('[data-testid="workflow-link-save-fixed_from"]').trigger('click')
    await flushPromises()
    expect(save.mock.calls[0][0].options).toEqual({ parameters: ['m/b'] })
  })
})
