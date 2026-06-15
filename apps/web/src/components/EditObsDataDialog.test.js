import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../lib/api', () => ({ uploadObsData: vi.fn(), getObsDataOptions: vi.fn() }))

import EditObsDataDialog from './EditObsDataDialog.vue'
import { uploadObsData, getObsDataOptions } from '../lib/api'

const DialogStub = {
  props: ['visible'],
  template: '<div v-if="visible"><slot /><slot name="footer" /></div>',
}
const ButtonStub = {
  props: ['label', 'icon', 'disabled', 'size', 'text', 'rounded', 'title'],
  emits: ['click'],
  template:
    '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
}
const MessageStub = { template: '<div class="msg"><slot /></div>' }
const stubs = { Dialog: DialogStub, Button: ButtonStub, Message: MessageStub }

const baseProps = {
  visible: true,
  modelId: 'abc',
  currentDataItems: [
    { variable: 'x_max', data_type: 'constant', operation: 'max', operands: ['m/x'], unit: 'dimensionless', value: 30, std: 3, experiment_idx: 0, plot_type: 'horizontal' },
    { variable: 's', data_type: 'series', obs_dt: 0.1, value: [1, 2], std: 0.1, experiment_idx: 0 },
  ],
  currentPredictionItems: [],
  protocolInfo: { pre_times: [0], sim_times: [[5]] },
  experimentCount: 1,
  modelVariables: { all_names: ['m/x', 'm/y'] },
  modelName: 'M',
  loadedFilename: 'obs.json',
}

const FETCH = {
  operations: ['', 'max', 'min', 'calc_spike_frequency_windowed'],
  cost_types: ['MSE', 'gaussian_MLE', 'my_custom_cost'],
  data_types: ['constant', 'series', 'frequency', 'prob_dist'],
  plot_types: ['', 'horizontal', 'pulse_plot'],
}

function mountDialog(props = {}) {
  return mount(EditObsDataDialog, { props: { ...baseProps, ...props }, global: { stubs } })
}

beforeEach(() => {
  uploadObsData.mockReset()
  getObsDataOptions.mockReset().mockResolvedValue(FETCH)
  globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock')
  globalThis.URL.revokeObjectURL = vi.fn()
})

describe('EditObsDataDialog', () => {
  it('splits items: one editable constant row, the series preserved', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    expect(wrapper.findAll('[data-testid="eo-row"]')).toHaveLength(1)
    expect(wrapper.find('[data-testid="obs-preserved"]').text()).toContain('1 non-editable')
  })

  it('operation select is populated from the fetched (CA) options', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    const opSelect = wrapper.find('[data-testid="eo-row"] select')
    expect(opSelect.text()).toContain('calc_spike_frequency_windowed') // CA user op, not in fallback
  })

  it('plot_type select uses the fetched (CA) plot_types', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.find('button[aria-label="details"]').trigger('click') // expand row detail
    expect(wrapper.html()).toContain('pulse_plot') // only appears in plot_type options
  })

  it('falls back when getObsDataOptions rejects', async () => {
    getObsDataOptions.mockRejectedValueOnce(new Error('offline'))
    const wrapper = mountDialog()
    await flushPromises()
    const opSelect = wrapper.find('[data-testid="eo-row"] select')
    expect(opSelect.text()).toContain('max') // fallback list
    expect(opSelect.text()).not.toContain('calc_spike_frequency_windowed')
  })

  it('adds a data item row', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.find('[data-testid="obs-add-row"]').trigger('click')
    expect(wrapper.findAll('[data-testid="eo-row"]')).toHaveLength(2)
  })

  it('on Save: downloads, applies (preserving the series item), emits saved, closes', async () => {
    uploadObsData.mockResolvedValue({ n_data_items: 2, has_protocol: true })
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {})

    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.find('[data-testid="eo-save"]').trigger('click')
    await flushPromises()

    expect(globalThis.URL.createObjectURL).toHaveBeenCalledOnce()
    expect(clickSpy).toHaveBeenCalledOnce()

    expect(uploadObsData).toHaveBeenCalledOnce()
    const [idArg, obsArg] = uploadObsData.mock.calls[0]
    expect(idArg).toBe('abc')
    // object form: protocol_info verbatim + edited constant + preserved series
    expect(obsArg.protocol_info).toEqual(baseProps.protocolInfo)
    expect(obsArg.data_items).toHaveLength(2)
    expect(obsArg.data_items[1]).toMatchObject({ variable: 's', data_type: 'series' })

    const saved = wrapper.emitted('saved')[0][0]
    expect(saved.filename).toMatch(/^obs_\d{6}\.json$/)
    expect(wrapper.emitted('update:visible').at(-1)).toEqual([false])

    clickSpy.mockRestore()
  })
})
