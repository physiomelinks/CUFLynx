import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import WorkflowLinkEditor from './WorkflowLinkEditor.vue'
import { linkChoices, linkEntry } from '../lib/workflowTarget'

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
const details = { 'fit_a': { calibrates: [{ model_name: 'm/a' }, { model_name: 'm/b' }] },
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
  it('a new arrow offers only what the two steps allow, and draws the chosen one', async () => {
    const save = vi.fn().mockResolvedValue('')
    const e = editor({ source: 'fit_a', target: 'fit_b', key: null }, { save })
    expect(e.text()).toContain('New arrow fit_a → fit_b')
    expect(e.findAll('input[type="radio"]').map((r) => r.element.value))
      .toEqual(['fixed_from', 'priors_from', 'start_from'])
    expect(e.find('[data-testid="workflow-link-save"]').attributes('disabled')).toBeDefined()
    await e.find('[data-testid="workflow-link-key-fixed_from"]').setValue()
    // this CA fixes every value of the source
    expect(e.find('[data-testid="workflow-link-fixed-all"]').exists()).toBe(true)
    await e.find('[data-testid="workflow-link-save"]').trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledWith(
      { source: 'fit_a', target: 'fit_b', key: 'fixed_from', options: null, oldKey: null })
    expect(e.emitted('close')).toHaveLength(1)
  })

  it('edits a prior: its parameters, kind, widening and objective', async () => {
    const save = vi.fn().mockResolvedValue('')
    const e = editor({ source: 'mcmc-1', target: 'fit_b', key: 'priors_from' }, { save })
    // the arrow's current options
    expect(e.find('[data-testid="workflow-link-prior-kind"]').element.value).toBe('normal')
    const boxes = e.findAll('[data-testid="workflow-link-params"] input')
    expect(boxes.map((b) => b.element.checked)).toEqual([true, false])
    await boxes[1].setValue(true)
    await e.find('[data-testid="workflow-link-inflate"]').setValue(2)
    await e.find('[data-testid="workflow-link-objective"]').setValue(true)
    await e.find('[data-testid="workflow-link-save"]').trigger('click')
    await flushPromises()
    // every parameter chosen: none named
    expect(save).toHaveBeenCalledWith({ source: 'mcmc-1', target: 'fit_b', key: 'priors_from',
      options: { kind: 'normal', inflate: 2, objective: 'likelihood' }, oldKey: null })
  })

  it("shows CA's reason when it refuses, and removes an arrow", async () => {
    const save = vi.fn().mockResolvedValue('step "fit_b" takes priors, but ...')
    const remove = vi.fn().mockResolvedValue('')
    const e = editor({ source: 'fit_a', target: 'fit_b', key: 'fixed_from' }, { save, remove })
    await e.find('[data-testid="workflow-link-key-start_from"]').setValue()
    await e.find('[data-testid="workflow-link-save"]').trigger('click')
    await flushPromises()
    expect(save.mock.calls[0][0]).toMatchObject({ key: 'start_from', oldKey: 'fixed_from' })
    expect(e.find('[data-testid="workflow-link-error"]').text()).toContain('takes priors')
    expect(e.emitted('close')).toBeUndefined()
    await e.find('[data-testid="workflow-link-remove"]').trigger('click')
    await flushPromises()
    expect(remove).toHaveBeenCalledWith({ source: 'fit_a', target: 'fit_b', key: 'fixed_from' })
    expect(e.emitted('close')).toHaveLength(1)
  })

  it('a fixed arrow names its parameters where CA takes them', async () => {
    const save = vi.fn().mockResolvedValue('')
    const e = editor({ source: 'fit_a', target: 'fit_b', key: 'fixed_from' },
      { save, fixedParameters: true })
    const boxes = e.findAll('[data-testid="workflow-link-params"] input')
    await boxes[0].setValue(false)
    await e.find('[data-testid="workflow-link-save"]').trigger('click')
    await flushPromises()
    expect(save.mock.calls[0][0].options).toEqual({ parameters: ['m/b'] })
  })
})
