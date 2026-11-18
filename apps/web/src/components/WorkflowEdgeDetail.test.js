import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../lib/api', () => ({
  getWorkflowEdge: vi.fn(),
  errorMessage: (e) => String(e?.message ?? e),
}))

import { getWorkflowEdge } from '../lib/api'
import WorkflowEdgeDetail from './WorkflowEdgeDetail.vue'

const DialogStub = {
  props: ['visible', 'header'],
  template: '<div v-if="visible"><h2>{{ header }}</h2><slot /></div>',
}
const mountIt = (props) => mount(WorkflowEdgeDetail, { props, global: { stubs: { Dialog: DialogStub } } })

describe('WorkflowEdgeDetail', () => {
  it('shows the values one step fixes in the next', async () => {
    getWorkflowEdge.mockResolvedValueOnce({
      what: "values fixed in the receiving step's model", recorded: true,
      rows: [{ name: 'SGLT1/K_Glc_i', value: 0.23799961, units: 'per_fmol', note: '' }],
    })
    const d = mountIt({ edge: { source: 'sglt1', target: 'whole_cell', key: 'fixed_from' } })
    await flushPromises()
    expect(getWorkflowEdge).toHaveBeenCalledWith('sglt1', 'whole_cell', 'fixed_from')
    expect(d.text()).toContain('sglt1 → whole_cell')
    expect(d.find('[data-testid="workflow-edge-rows"]').text()).toContain('SGLT1/K_Glc_i')
    expect(d.find('[data-testid="workflow-edge-rows"]').text()).toContain('0.23800')
  })

  it('shows a prior as its kind and the distribution it is fitted to', async () => {
    getWorkflowEdge.mockResolvedValueOnce({
      what: 'a distribution, used as the prior', recorded: true, rows: [],
      prior: { kind: 'mvnormal', inflate: 1, parameters: ['k_heat'] },
      distribution: { k_heat: { mean: 0.2508, sd: 0.01, q025: 0.232, q975: 0.27 } },
    })
    const d = mountIt({ edge: { source: 'fit-k', target: 'refine', key: 'priors_from' } })
    await flushPromises()
    expect(d.text()).toContain('mvnormal')
    expect(d.find('[data-testid="workflow-edge-distribution"]').text()).toContain('0.23200 – 0.27000')
  })

  it('shows what a figure reads and writes without asking the server', () => {
    getWorkflowEdge.mockClear()
    const d = mountIt({ figure: { id: 'paper_plots', command: 'make paper-plots',
                                  reads: ['workflow_result.json'], writes: ['Fig9.png'] } })
    expect(d.text()).toContain('make paper-plots')
    expect(d.text()).toContain('Fig9.png')
    expect(getWorkflowEdge).not.toHaveBeenCalled()
  })
})
