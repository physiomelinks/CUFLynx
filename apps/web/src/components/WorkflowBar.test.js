import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import WorkflowBar from './WorkflowBar.vue'

const tabs = [
  { id: 'fit_a', kind: 'step', label: 'fit_a', detail: 'lin_a · fit_a', place: 'A' },
  { id: 'rest', kind: 'step', label: 'rest', detail: 'chain · rest', place: '' },
  { id: 'target', kind: 'target', label: 'chain', detail: 'supermodule · rest' },
]

describe('WorkflowBar', () => {
  it('has a tab per step and one for the supermodule, with state dots', () => {
    const bar = mount(WorkflowBar, {
      props: { name: 'chain_rest', tabs, selected: 'rest',
               states: { fit_a: 'done', rest: 'stale' } },
    })
    expect(bar.find('[data-testid="workflow-tab-fit_a"]').text()).toContain('→ A')
    expect(bar.find('[data-testid="workflow-tab-rest"]').classes()).toContain('active')
    expect(bar.find('[data-testid="workflow-tab-target"]').classes()).toContain('target')
    expect(bar.find('[data-testid="workflow-tab-fit_a"] .wf-dot').attributes('data-state'))
      .toBe('done')
    expect(bar.find('[data-testid="workflow-tab-rest"]').attributes('title')).toContain('rerun')
    // the supermodule has no run of its own to show
    expect(bar.find('[data-testid="workflow-tab-target"] .wf-dot').exists()).toBe(false)
  })

  it('has no model to show for a reduction', () => {
    const bar = mount(WorkflowBar, { props: { tabs: [
      ...tabs.slice(0, 2),
      { id: 'reduce', kind: 'step', stepKind: 'reduction', viewable: false, label: 'reduce',
        detail: 'keeps the top 1 by Sobol ST' },
    ] } })
    const tab = bar.find('[data-testid="workflow-tab-reduce"]')
    expect(tab.attributes('disabled')).toBeDefined()
    expect(tab.attributes('title')).toContain('no model to show')
  })

  it('says why a set step has no tab', () => {
    const bar = mount(WorkflowBar, { props: { tabs: [
      { id: 'boundary', kind: 'step', stepKind: 'set', viewable: false, label: 'boundary',
        detail: 'heat/u_D = 0.1',
        noViewReason: 'a set step gives values to the target and has no model to show' },
    ] } })
    const tab = bar.find('[data-testid="workflow-tab-boundary"]')
    expect(tab.attributes('disabled')).toBeDefined()
    expect(tab.attributes('title')).toBe(
      'heat/u_D = 0.1: a set step gives values to the target and has no model to show')
  })

  it('switches tabs, and not while a tab is loading', async () => {
    const bar = mount(WorkflowBar, { props: { tabs, selected: null } })
    await bar.find('[data-testid="workflow-tab-target"]').trigger('click')
    expect(bar.emitted('select')).toEqual([['target']])
    await bar.setProps({ busy: true })
    expect(bar.find('[data-testid="workflow-tab-fit_a"]').attributes('disabled')).toBeDefined()
  })
})

describe('WorkflowBar: the Workflow view', () => {
  it('is labelled Workflow, and toggles the view', async () => {
    const bar = mount(WorkflowBar, { props: { name: 'study', tabs: [] } })
    const toggle = bar.find('[data-testid="workflow-toggle"]')
    expect(toggle.text()).toContain('Workflow: study')
    await toggle.trigger('click')
    expect(bar.emitted('toggle')).toHaveLength(1)
  })

  it('highlights what the next run is', () => {
    for (const [mode, step, on] of [
      ['new_workflow', null, 'New workflow'],
      ['new_step', null, 'New step'],
      ['edit', 'calibration-1', 'Changing a step calibration-1'],
    ]) {
      const bar = mount(WorkflowBar, { props: { tabs: [], summary: { mode, step, text: '' } } })
      expect(bar.find('[data-testid="workflow-mode"]').attributes('data-mode')).toBe(mode)
      const lit = bar.findAll('.wf-mode.on')
      expect(lit).toHaveLength(1)
      expect(lit[0].text().replace(/\s+/g, ' ')).toBe(on)
    }
  })
})
