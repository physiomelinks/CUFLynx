import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import WorkflowBar from './WorkflowBar.vue'

const tabs = [
  { id: 'fit_a', kind: 'step', label: 'fit_a', detail: 'lin_a · fit_a', submodulePath: 'A' },
  { id: 'rest', kind: 'step', label: 'rest', detail: 'chain · rest', submodulePath: '' },
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

  it('switches tabs, and not while a tab is loading', async () => {
    const bar = mount(WorkflowBar, { props: { tabs, selected: null } })
    await bar.find('[data-testid="workflow-tab-target"]').trigger('click')
    expect(bar.emitted('select')).toEqual([['target']])
    await bar.setProps({ busy: true })
    expect(bar.find('[data-testid="workflow-tab-fit_a"]').attributes('disabled')).toBeDefined()
  })
})
