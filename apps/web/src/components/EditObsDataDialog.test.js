import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../lib/api', () => ({
  uploadObsData: vi.fn(),
  getObsDataOptions: vi.fn(),
  // Used by the embedded EditOperationFuncsDialog ("Custom operation").
  getUserOperations: vi.fn(),
  saveUserOperation: vi.fn(),
  deleteUserOperation: vi.fn(),
}))

import EditObsDataDialog from './EditObsDataDialog.vue'
import EditOperationFuncsDialog from './EditOperationFuncsDialog.vue'
import { uploadObsData, getObsDataOptions, getUserOperations } from '../lib/api'

const DialogStub = {
  props: ['visible'],
  template: '<div v-if="visible"><slot /><slot name="footer" /></div>',
}
const ButtonStub = {
  props: ['label', 'icon', 'disabled', 'size', 'text', 'rounded', 'title'],
  emits: ['click'],
  template:
    '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\', $event)">{{ label }}</button>',
}
const MessageStub = { template: '<div class="msg"><slot /></div>' }
// Records the props it receives so tests can assert active-exp / highlight-subexp.
const ProtocolEditorStub = {
  name: 'ProtocolInfoEditor',
  props: ['model', 'allNames', 'activeExp', 'highlightSubexp', 'highlightExp'],
  emits: ['update:activeExp'],
  template: '<div class="pie-stub" />',
}
// The embedded EditOperationFuncsDialog uses InputText; stub it so it renders
// without the PrimeVue plugin.
const InputTextStub = {
  props: ['modelValue', 'invalid'],
  emits: ['update:modelValue'],
  template:
    '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
}
const stubs = {
  Dialog: DialogStub,
  Button: ButtonStub,
  Message: MessageStub,
  InputText: InputTextStub,
  ProtocolInfoEditor: ProtocolEditorStub,
}

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
  getUserOperations.mockReset().mockResolvedValue({ functions: [], template: 'def f(x):\n    return x\n', available: true })
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

  it('annotates cost_type options with CA cost_func_metadata flags', async () => {
    getObsDataOptions.mockResolvedValueOnce({
      ...FETCH,
      cost_func_metadata: { gaussian_MLE: { is_MLE: true, differentiable: true } },
    })
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.find('button[aria-label="details"]').trigger('click') // expand row detail
    // Flagged cost function is labelled; an unflagged one stays bare.
    expect(wrapper.text()).toContain('gaussian_MLE — MLE, AD')
    expect(wrapper.text()).toContain('my_custom_cost')
  })

  it('flags a data_item whose operation is not @differentiable', async () => {
    getObsDataOptions.mockResolvedValueOnce({
      ...FETCH,
      differentiable_operations: { max: false, min: true },
    })
    const wrapper = mountDialog()
    await flushPromises()
    const row = wrapper.find('[data-testid="eo-row"]')
    expect(row.classes()).toContain('non-diff')
    const warn = wrapper.find('[data-testid="eo-nondiff-warn"]')
    expect(warn.exists()).toBe(true)
    expect(warn.text()).toContain('max')
    expect(warn.text()).toContain('not differentiable')
  })

  it('does not flag a differentiable operation, nor when CA reports no map', async () => {
    // Differentiable -> no warning.
    getObsDataOptions.mockResolvedValueOnce({
      ...FETCH,
      differentiable_operations: { max: true },
    })
    let wrapper = mountDialog()
    await flushPromises()
    expect(wrapper.find('[data-testid="eo-row"]').classes()).not.toContain('non-diff')
    expect(wrapper.find('[data-testid="eo-nondiff-warn"]').exists()).toBe(false)

    // No map at all (older CA) -> never flag, avoiding false warnings.
    getObsDataOptions.mockResolvedValueOnce({ ...FETCH })
    wrapper = mountDialog()
    await flushPromises()
    expect(wrapper.find('[data-testid="eo-row"]').classes()).not.toContain('non-diff')
    expect(wrapper.find('[data-testid="eo-nondiff-warn"]').exists()).toBe(false)
  })

  it('colours non-differentiable operation options in the operation dropdown', async () => {
    getObsDataOptions.mockResolvedValueOnce({
      ...FETCH,
      operations: ['', 'max', 'calc_spike_period'],
      differentiable_operations: { max: true, calc_spike_period: false },
    })
    const wrapper = mountDialog()
    await flushPromises()
    const options = wrapper.findAll('[data-testid="eo-row"] select option')
    const byValue = (v) => options.find((o) => o.attributes('value') === v)
    expect(byValue('calc_spike_period').classes()).toContain('non-diff-option')
    expect(byValue('max').classes()).not.toContain('non-diff-option')
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
    // object form: protocol_info rebuilt from the model (pre/sim preserved) +
    // edited constant + preserved series item.
    expect(obsArg.protocol_info).toMatchObject({ pre_times: [0], sim_times: [[5]] })
    expect(obsArg.data_items).toHaveLength(2)
    expect(obsArg.data_items[1]).toMatchObject({ variable: 's', data_type: 'series' })

    const saved = wrapper.emitted('saved')[0][0]
    expect(saved.filename).toMatch(/^obs_\d{6}\.json$/)
    expect(wrapper.emitted('update:visible').at(-1)).toEqual([false])

    clickSpy.mockRestore()
  })

  it('selecting a data_item row points the protocol editor at its exp/subexp', async () => {
    const wrapper = mountDialog({
      protocolInfo: { pre_times: [0, 0], sim_times: [[5, 5], [5, 5]] },
      experimentCount: 2,
      currentDataItems: [
        {
          variable: 'x_max', data_type: 'constant', operation: 'max', operands: ['m/x'],
          value: 1, std: 1, experiment_idx: 1, subexperiment_idx: 1,
        },
      ],
    })
    await flushPromises()
    const pie = wrapper.findComponent(ProtocolEditorStub)
    // defaults before any selection
    expect(pie.props('activeExp')).toBe(0)
    expect(pie.props('highlightSubexp')).toBe(null)
    expect(pie.props('highlightExp')).toBe(null)

    // collapsed before selection: the detail (source note) is not rendered
    expect(wrapper.find('[data-testid="eo-source"]').exists()).toBe(false)

    await wrapper.find('[data-testid="eo-main"]').trigger('click')

    expect(pie.props('activeExp')).toBe(1)
    expect(pie.props('highlightSubexp')).toBe(1)
    // highlight is pinned to the item's experiment so it shows only there
    expect(pie.props('highlightExp')).toBe(1)
    // the clicked row is marked selected (distinct from the others)
    expect(wrapper.find('[data-testid="eo-row"]').classes()).toContain('selected')
    // clicking a box in the row also un-minimises it (details now visible)
    expect(wrapper.find('[data-testid="eo-source"]').exists()).toBe(true)
  })

  it('down-chevron expands+highlights; up-chevron just collapses (no re-highlight)', async () => {
    const wrapper = mountDialog({
      protocolInfo: { pre_times: [0, 0], sim_times: [[5, 5], [5, 5]] },
      experimentCount: 2,
      currentDataItems: [
        {
          variable: 'x_max', data_type: 'constant', operation: 'max', operands: ['m/x'],
          value: 1, std: 1, experiment_idx: 1, subexperiment_idx: 1,
        },
      ],
    })
    await flushPromises()
    const pie = wrapper.findComponent(ProtocolEditorStub)
    const chevron = () => wrapper.find('[data-testid="eo-row"]').findAll('button')[0]

    // down-chevron (collapsed) -> expands AND highlights
    await chevron().trigger('click')
    expect(wrapper.find('[data-testid="eo-source"]').exists()).toBe(true)
    expect(pie.props('highlightExp')).toBe(1)
    expect(wrapper.find('[data-testid="eo-row"]').classes()).toContain('selected')

    // up-chevron (expanded) -> collapses, selection/highlight unchanged
    await chevron().trigger('click')
    expect(wrapper.find('[data-testid="eo-source"]').exists()).toBe(false)
    expect(pie.props('highlightExp')).toBe(1)
  })

  it('focusing a row dropdown also selects/highlights that data_item', async () => {
    const wrapper = mountDialog({
      protocolInfo: { pre_times: [0, 0], sim_times: [[5, 5], [5, 5]] },
      experimentCount: 2,
      currentDataItems: [
        {
          variable: 'x_max', data_type: 'constant', operation: 'max', operands: ['m/x'],
          value: 1, std: 1, experiment_idx: 1, subexperiment_idx: 1,
        },
      ],
    })
    await flushPromises()
    const pie = wrapper.findComponent(ProtocolEditorStub)
    expect(pie.props('highlightExp')).toBe(null)

    await wrapper.find('[data-testid="eo-subexp"]').trigger('focus')

    expect(pie.props('activeExp')).toBe(1)
    expect(pie.props('highlightExp')).toBe(1)
    expect(pie.props('highlightSubexp')).toBe(1)
    expect(wrapper.find('[data-testid="eo-row"]').classes()).toContain('selected')
  })

  it('data-only: "Add protocol_info" → save emits object form', async () => {
    uploadObsData.mockResolvedValue({})
    const clickSpy = vi
      .spyOn(HTMLAnchorElement.prototype, 'click')
      .mockImplementation(() => {})
    const wrapper = mountDialog({
      protocolInfo: null,
      experimentCount: 0,
      currentDataItems: [],
      currentPredictionItems: [],
    })
    await flushPromises()
    await wrapper.find('[data-testid="add-protocol"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="eo-save"]').trigger('click')
    await flushPromises()

    const obsArg = uploadObsData.mock.calls[0][1]
    expect(Array.isArray(obsArg)).toBe(false) // object form now
    expect(obsArg.protocol_info).toMatchObject({ pre_times: [0], sim_times: [[1]] })
    clickSpy.mockRestore()
  })

  it('opens the custom-operation dialog and re-introspects options after a save', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    // The "Custom operation" affordance opens the authoring dialog.
    await wrapper.find('[data-testid="eo-add-op-func"]').trigger('click')
    await flushPromises()
    expect(getUserOperations).toHaveBeenCalled()

    // When a custom op is saved, the operation options are refreshed (refresh=true).
    getObsDataOptions.mockClear()
    wrapper.findComponent(EditOperationFuncsDialog).vm.$emit('saved', [])
    await flushPromises()
    expect(getObsDataOptions).toHaveBeenCalledWith(true)
  })
})
