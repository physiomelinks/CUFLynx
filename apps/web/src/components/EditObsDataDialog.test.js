import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../lib/api', () => ({
  uploadObsData: vi.fn(),
  getObsDataOptions: vi.fn(),
  // Used by the embedded EditOperationFuncsDialog ("Custom funcs").
  getUserFuncs: vi.fn(),
  saveUserFunc: vi.fn(),
  deleteUserFunc: vi.fn(),
}))

import EditObsDataDialog from './EditObsDataDialog.vue'
import EditOperationFuncsDialog from './EditOperationFuncsDialog.vue'
import { uploadObsData, getObsDataOptions, getUserFuncs } from '../lib/api'

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
  operation_kwargs_schema: {
    peak_above: [
      { name: 'threshold', default: 0.5, type: 'number' },
      { name: 'window', default: 10, type: 'integer' },
      { name: 'invert', default: false, type: 'boolean' },
    ],
  },
}

function mountDialog(props = {}) {
  return mount(EditObsDataDialog, { props: { ...baseProps, ...props }, global: { stubs } })
}

beforeEach(() => {
  uploadObsData.mockReset()
  getUserFuncs.mockReset().mockResolvedValue({ kind: 'operation', functions: [], templates: { basic: 'def f(x):\n    return x\n' }, template: 'def f(x):\n    return x\n', available: true })
  getObsDataOptions.mockReset().mockResolvedValue(FETCH)
  globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock')
  globalThis.URL.revokeObjectURL = vi.fn()
})

// The operand/operation dropdowns are SearchableSelect (#160): a button showing
// the current value, which opens a filter box and a list of matches. These drive
// it the way a user does -- open, then choose -- so the tests keep testing the
// behaviour rather than the markup that happens to implement it.
async function openSelect(wrapper, testid, index = 0) {
  const triggers = wrapper.findAll(`[data-testid="${testid}"]`)
  await triggers[index].trigger('click')
  return wrapper.findAll(`[data-testid="${testid}-option"]`)
}

async function chooseIn(wrapper, testid, value, index = 0) {
  const options = await openSelect(wrapper, testid, index)
  const target = options.find((o) => o.text() === value)
  if (!target) throw new Error(`no option "${value}" in ${testid}: ${options.map((o) => o.text())}`)
  await target.trigger('mousedown')
}

function selectedValue(wrapper, testid, index = 0) {
  return wrapper.findAll(`[data-testid="${testid}"]`)[index].text()
}

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
    const options = await openSelect(wrapper, 'eo-operation')
    expect(options.map((o) => o.text())).toContain('calc_spike_frequency_windowed') // CA user op
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
    const options = await openSelect(wrapper, 'eo-operation')
    const byText = (v) => options.find((o) => o.text() === v)
    expect(byText('calc_spike_period').classes()).toContain('non-diff-option')
    expect(byText('max').classes()).not.toContain('non-diff-option')
  })

  it('falls back when getObsDataOptions rejects', async () => {
    getObsDataOptions.mockRejectedValueOnce(new Error('offline'))
    const wrapper = mountDialog()
    await flushPromises()
    const options = (await openSelect(wrapper, 'eo-operation')).map((o) => o.text())
    expect(options).toContain('max') // fallback list
    expect(options).not.toContain('calc_spike_frequency_windowed')
  })

  const KWARG_FETCH = {
    ...FETCH,
    operations: ['', 'max', 'peak_above'],
  }
  const kwargItem = {
    variable: 'p', data_type: 'constant', operation: 'peak_above', operands: ['m/x'],
    unit: 'dimensionless', value: 1, std: 0.1, experiment_idx: 0, plot_type: 'horizontal',
  }

  it('renders an input per operation kwarg, prefilled with its default', async () => {
    getObsDataOptions.mockResolvedValueOnce(KWARG_FETCH)
    const wrapper = mountDialog({ currentDataItems: [kwargItem] })
    await flushPromises()
    await wrapper.find('button[aria-label="details"]').trigger('click') // expand detail
    const threshold = wrapper.find('[data-testid="eo-kwarg-threshold"] input')
    const window = wrapper.find('[data-testid="eo-kwarg-window"] input')
    const invert = wrapper.find('[data-testid="eo-kwarg-invert"] input')
    expect(threshold.element.value).toBe('0.5')
    expect(window.element.value).toBe('10')
    expect(invert.element.type).toBe('checkbox')
    expect(invert.element.checked).toBe(false)
  })

  it('persists edited kwarg values into the saved obs_data', async () => {
    getObsDataOptions.mockResolvedValueOnce(KWARG_FETCH)
    uploadObsData.mockResolvedValue({})
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const wrapper = mountDialog({ currentDataItems: [kwargItem] })
    await flushPromises()
    await wrapper.find('button[aria-label="details"]').trigger('click')
    await wrapper.find('[data-testid="eo-kwarg-threshold"] input').setValue('0.9')
    await wrapper.find('[data-testid="eo-kwarg-invert"] input').setValue(true)
    await wrapper.find('[data-testid="eo-save"]').trigger('click')
    await flushPromises()
    const obsArg = uploadObsData.mock.calls[0][1]
    expect(obsArg.data_items[0].operation_kwargs).toEqual({ threshold: 0.9, invert: true })
  })

  it('shows no kwarg inputs for an operation without kwargs', async () => {
    getObsDataOptions.mockResolvedValueOnce(KWARG_FETCH)
    const wrapper = mountDialog({
      currentDataItems: [{ ...kwargItem, operation: 'max' }],
    })
    await flushPromises()
    await wrapper.find('button[aria-label="details"]').trigger('click')
    expect(wrapper.find('[data-testid="eo-kwarg-threshold"]').exists()).toBe(false)
  })

  it('drops stale kwargs when the operation changes away', async () => {
    getObsDataOptions.mockResolvedValueOnce(KWARG_FETCH)
    uploadObsData.mockResolvedValue({})
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    const wrapper = mountDialog({
      currentDataItems: [{ ...kwargItem, operation_kwargs: { threshold: 0.7 } }],
    })
    await flushPromises()
    // switch peak_above -> max (no kwargs): the stored threshold must not persist
    await chooseIn(wrapper, 'eo-operation', 'max')
    await wrapper.find('[data-testid="eo-save"]').trigger('click')
    await flushPromises()
    const obsArg = uploadObsData.mock.calls[0][1]
    expect('operation_kwargs' in obsArg.data_items[0]).toBe(false)
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

  it('opens the custom-funcs dialog and re-introspects options after a save', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    // The "Custom funcs" affordance opens the authoring dialog.
    await wrapper.find('[data-testid="eo-add-op-func"]').trigger('click')
    await flushPromises()
    expect(getUserFuncs).toHaveBeenCalled()

    // When a custom func is saved, the operation options are refreshed (refresh=true).
    getObsDataOptions.mockClear()
    wrapper.findComponent(EditOperationFuncsDialog).vm.$emit('saved', { kind: 'operation', functions: [] })
    await flushPromises()
    expect(getObsDataOptions).toHaveBeenCalledWith(true, '')
  })
})

// Issue #147: the number of operand fields is a property of the operation --
// `division` takes two, `max` takes one -- not something the user should have to
// add by hand and get right.
describe('EditObsDataDialog operand count (#147)', () => {
  const OPERANDS = {
    max: { count: 1, names: ['x'], variadic: false },
    division: { count: 2, names: ['x1', 'x2'], variadic: false },
    spread: { count: 1, names: ['x'], variadic: true },
  }

  const mountWithOperands = async (props = {}) => {
    getObsDataOptions.mockResolvedValue({
      ...FETCH,
      operations: ['', 'max', 'division', 'spread'],
      operation_operands: OPERANDS,
    })
    const wrapper = mountDialog(props)
    await flushPromises()
    await wrapper.find('button[aria-label="details"]').trigger('click')
    return wrapper
  }

  const operandFields = (w) => w.findAll('[data-testid="eo-operand"]')
  const setOperation = (w, value) => chooseIn(w, 'eo-operation', value)

  it('grows the fields when switching to a two-operand operation', async () => {
    const wrapper = await mountWithOperands()
    expect(operandFields(wrapper)).toHaveLength(1)

    await setOperation(wrapper, 'division')
    expect(operandFields(wrapper)).toHaveLength(2)
  })

  // Switching operation should not throw away the operand already chosen.
  it('keeps the operand already entered when the count grows', async () => {
    const wrapper = await mountWithOperands()
    await setOperation(wrapper, 'division')
    expect(selectedValue(wrapper, 'eo-operand')).toBe('m/x')
  })

  it('shrinks back when switching to a one-operand operation', async () => {
    const wrapper = await mountWithOperands()
    await setOperation(wrapper, 'division')
    await setOperation(wrapper, 'max')
    expect(operandFields(wrapper)).toHaveLength(1)
  })

  // Removing one would just make the row invalid, so the control is only offered
  // where the count is genuinely the user's to choose.
  it('hides add/remove for a fixed-arity operation', async () => {
    const wrapper = await mountWithOperands()
    expect(wrapper.find('[data-testid="eo-operand-add"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="eo-operand-remove"]').exists()).toBe(false)
  })

  it('keeps add/remove for a variadic operation, which has no fixed count', async () => {
    const wrapper = await mountWithOperands()
    await setOperation(wrapper, 'spread')
    expect(wrapper.find('[data-testid="eo-operand-add"]').exists()).toBe(true)
  })

  it('names what each field fills, so a two-operand row says which is which', async () => {
    const wrapper = await mountWithOperands()
    await setOperation(wrapper, 'division')
    const names = wrapper.findAll('.eo-operand-name').map((n) => n.text())
    expect(names).toEqual(['x1', 'x2'])
  })

  // A row added from scratch starts on the default operation with no operands.
  // Because that operation has a fixed arity, the add/remove controls are hidden
  // -- so an unsynced new row rendered ZERO operand fields and no way to make
  // one, and the item could not be filled in at all. Reachable on any model, but
  // it became the first thing you hit once a .mmt's protocol produced an
  // obs_data with no data_items to copy from.
  // Starts empty, as an obs_data derived from a .mmt protocol does, so the row
  // under test is one the user added rather than one loaded from a file.
  const mountEmptyThenAddRow = async () => {
    getObsDataOptions.mockResolvedValue({
      ...FETCH,
      operations: ['', 'max', 'division', 'spread'],
      operation_operands: OPERANDS,
    })
    const wrapper = mountDialog({ currentDataItems: [] })
    await flushPromises()
    await wrapper.find('[data-testid="obs-add-row"]').trigger('click')
    await wrapper.find('button[aria-label="details"]').trigger('click')
    return wrapper
  }

  it('gives a newly added data item its operand field', async () => {
    const wrapper = await mountEmptyThenAddRow()
    expect(operandFields(wrapper)).toHaveLength(1)
  })

  it('offers the model variables in a new row, so it can actually be set', async () => {
    const wrapper = await mountEmptyThenAddRow()
    const options = await openSelect(wrapper, 'eo-operand')
    expect(options.map((o) => o.text())).toContain('m/x')
  })

  // A hand-written obs_data can carry fewer operands than the operation takes;
  // the row still has to be completable.
  it('grows a loaded row that is short of operands', async () => {
    const wrapper = await mountWithOperands({
      currentDataItems: [{ data_type: 'constant', operation: 'division', operands: ['m/x'], value: 1 }],
    })
    expect(operandFields(wrapper)).toHaveLength(2)
    expect(selectedValue(wrapper, 'eo-operand')).toBe('m/x')
  })

  // ...but never by silently dropping operands the user wrote. Truncation is
  // right when they change the operation, not when the file is merely opened.
  it('does not drop extra operands from a loaded row', async () => {
    const wrapper = await mountWithOperands({
      currentDataItems: [
        { data_type: 'constant', operation: 'max', operands: ['m/x', 'm/y'], value: 1 },
      ],
    })
    expect(operandFields(wrapper)).toHaveLength(2)
  })

  // An older CA without the introspection must not lock the fields down.
  it('leaves the fields hand-managed when the schema is unavailable', async () => {
    getObsDataOptions.mockResolvedValue({ ...FETCH, operation_operands: undefined })
    const wrapper = mountDialog()
    await flushPromises()
    await wrapper.find('button[aria-label="details"]').trigger('click')
    expect(wrapper.find('[data-testid="eo-operand-add"]').exists()).toBe(true)
  })
})
