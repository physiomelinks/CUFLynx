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

  it('reads "[Editing] sglt1": the chip, then the step it changes, and nothing else', () => {
    const bar = mount(WorkflowBar, { props: {
      name: 'enterocyte',
      summary: { mode: 'edit', step: 'sglt1', text: 'Changing step sglt1 of enterocyte' },
    } })
    expect(bar.find('[data-testid="workflow-mode"]').text()).toBe('Editing')
    expect(bar.find('[data-testid="workflow-mode-step"]').text()).toBe('sglt1')
    // no model name, no step tabs: the steps are in the Workflow view's chart
    expect(bar.element.children).toHaveLength(3)
    expect(bar.find('[data-testid="workflow-toggle"]').text()).toBe('Workflow: enterocyte')
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
