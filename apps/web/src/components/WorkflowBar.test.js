import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import WorkflowBar from './WorkflowBar.vue'

describe('WorkflowBar', () => {
  it('is labelled Workflow, and toggles the view', async () => {
    const bar = mount(WorkflowBar, { props: { name: 'study' } })
    const toggle = bar.find('[data-testid="workflow-toggle"]')
    expect(toggle.text()).toContain('Workflow: study')
    await toggle.trigger('click')
    expect(bar.emitted('toggle')).toHaveLength(1)
  })

  it('shows only the step the next run changes and the model', () => {
    const bar = mount(WorkflowBar, { props: {
      name: 'study', model: 'Lotka_Volterra_forced.cellml',
      summary: { mode: 'edit', step: 'calibration-1', text: 'Changing step calibration-1 of study' },
    } })
    expect(bar.find('[data-testid="workflow-mode"]').text()).toBe('Editing calibration-1')
    expect(bar.find('[data-testid="workflow-model"]').text()).toBe('Lotka_Volterra_forced.cellml')
    // no step tabs: the steps are in the Workflow view's chart
    expect(bar.findAll('button')).toHaveLength(1)
  })

  it('says when the next run adds a step or starts a workflow', () => {
    for (const [mode, words] of [['new_step', 'New step'], ['new_workflow', 'New workflow']]) {
      const bar = mount(WorkflowBar, { props: { summary: { mode, step: null, text: '' } } })
      expect(bar.find('[data-testid="workflow-mode"]').attributes('data-mode')).toBe(mode)
      expect(bar.find('[data-testid="workflow-mode"]').text()).toBe(words)
    }
  })
})
