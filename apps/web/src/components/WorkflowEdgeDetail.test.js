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

  it('lists the values set by hand with their reasons', () => {
    getWorkflowEdge.mockClear()
    const d = mountIt({ origin: { title: 'Values set by hand', mark: 'set_by_hand', values: [
      { name: 'Scale_met_Glc_sink', value: 0, units: 'dim', note: 'no cytosolic glucose data identify it' }] } })
    expect(d.text()).toContain('Values set by hand')
    expect(d.find('[data-testid="workflow-origin-values"]').text()).toContain('no cytosolic glucose data identify it')
    expect(getWorkflowEdge).not.toHaveBeenCalled()
  })

  it('names the step that calibrates a value, and opens it', async () => {
    const d = mountIt({ origin: { title: 'Values from literature', mark: 'measured', values: [
      { name: 'm/K', value: 1, units: 'mM', note: 'smith2001', calibrated_by: ['fit-a', 'fit-b'] },
      { name: 'm/V', value: 2, units: 'mM', note: 'smith2001', calibrated_by: [] }] } })
    const rows = d.find('[data-testid="workflow-origin-values"]')
    expect(rows.text()).toContain('calibrated in')
    await d.find('[data-testid="workflow-origin-step-fit-b"]').trigger('click')
    expect(d.emitted('step')).toEqual([['fit-b']])
  })

  const KINDS = ['measured', 'inherited', 'set_by_hand', 'derived', 'calibrated']
  const UNRECORDED = { id: 'unrecorded', title: 'Origin not recorded', mark: 'unclassified', values: [
    { name: 'm/delta', value: 0.2, units: '', note: '', origin: null, data_reference: '', reason: '', calibrated_by: [] }] }

  it("edits a value's origin, and says which box it has moved to", async () => {
    const saveOrigin = vi.fn().mockResolvedValue('')
    const d = mountIt({ origin: UNRECORDED, boxes: [UNRECORDED], originKinds: KINDS, saveOrigin })
    await d.find('[data-testid="workflow-origin-edit-m/delta"]').trigger('click')
    const options = d.findAll('[data-testid="workflow-origin-kind"] option').map((o) => o.text())
    expect(options).toEqual(['not recorded', 'from literature', 'inherited', 'set by hand',
                             'from data', 'calibrated before'])
    await d.find('[data-testid="workflow-origin-kind"]').setValue('set_by_hand')
    await d.find('[data-testid="workflow-origin-reason"]').setValue('no data identify it')
    await d.find('[data-testid="workflow-origin-form"]').trigger('submit')
    await flushPromises()
    expect(saveOrigin).toHaveBeenCalledWith({ name: 'm/delta', origin: 'set_by_hand',
                                              dataReference: '', reason: 'no data identify it' })
    expect(d.find('[data-testid="workflow-origin-form"]').exists()).toBe(false)
    // the workflow described again: the value has left this box for the set-by-hand one
    const hand = { id: 'set_by_hand', title: 'Values set by hand', mark: 'set_by_hand',
                   values: [{ ...UNRECORDED.values[0], origin: 'set_by_hand', note: 'no data identify it' }] }
    await d.setProps({ origin: { ...UNRECORDED, values: [] }, boxes: [hand] })
    expect(d.text()).toContain('No values are left in this box.')
    const moved = d.find('[data-testid="workflow-origin-moved"]')
    expect(moved.text()).toContain('m/delta is now in Values set by hand')
    await moved.find('a').trigger('click')
    expect(d.emitted('open-origin')).toEqual([['set_by_hand']])
  })

  it("shows why an origin was not recorded, and keeps the form", async () => {
    const saveOrigin = vi.fn().mockResolvedValue('the target names no parameters file')
    const d = mountIt({ origin: UNRECORDED, boxes: [UNRECORDED], originKinds: KINDS, saveOrigin })
    await d.find('[data-testid="workflow-origin-edit-m/delta"]').trigger('click')
    await d.find('[data-testid="workflow-origin-form"]').trigger('submit')
    await flushPromises()
    expect(d.find('[data-testid="workflow-origin-error"]').text()).toContain('no parameters file')
    expect(d.find('[data-testid="workflow-origin-form"]').exists()).toBe(true)
  })

  it('offers no edit when origins cannot be recorded', () => {
    const d = mountIt({ origin: UNRECORDED, boxes: [UNRECORDED] })
    expect(d.find('[data-testid="workflow-origin-edit-m/delta"]').exists()).toBe(false)
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
