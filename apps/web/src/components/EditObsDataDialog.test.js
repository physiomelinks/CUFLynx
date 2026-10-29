import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import PrimeVue from 'primevue/config'

vi.mock('../lib/api', () => ({
  errorMessage: (e) => e?.response?.data?.detail?.message ?? e?.response?.data?.detail ?? String(e),
  uploadObsData: vi.fn(),
  getObsDataOptions: vi.fn(),
  getObsDataDocument: vi.fn(),
  // Used by the embedded EditOperationFuncsDialog ("Custom funcs").
  getUserFuncs: vi.fn(),
  saveUserFunc: vi.fn(),
  deleteUserFunc: vi.fn(),
  // Used by the embedded AddFromDatasetDialog ("Add from dataset").
  scanDatasets: vi.fn(),
  startObsExtract: vi.fn(),
  getObsExtractStatus: vi.fn(),
  cancelObsExtract: vi.fn(),
  saveObsExtractConfig: vi.fn(),
  loadObsExtractConfig: vi.fn(),
  listDir: vi.fn(async () => ({ path: '/', parent: null, entries: [] })),
  makeDir: vi.fn(),
}))

import EditObsDataDialog from './EditObsDataDialog.vue'
import { getObsDataDocument, getObsDataOptions, uploadObsData } from '../lib/api'

const DialogStub = {
  props: ['visible'],
  template: '<div v-if="visible"><slot /><slot name="footer" /></div>',
}
const ButtonStub = {
  props: ['label', 'icon', 'disabled', 'size', 'text', 'loading', 'title'],
  emits: ['click'],
  template:
    '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\', $event)">{{ label }}</button>',
}
// protocol-kit's editor, recording what it is given and passing edits on as it does.
const EditorStub = {
  name: 'ObsDataEditor',
  props: ['document', 'variables', 'getValue', 'vocabulary', 'dt', 'preset'],
  emits: ['update:document'],
  template: '<div class="editor-stub"><slot name="data-items-actions" /></div>',
}
const stubs = {
  Dialog: DialogStub,
  Button: ButtonStub,
  Message: { template: '<div class="msg"><slot /></div>' },
  ObsDataEditor: EditorStub,
  EditOperationFuncsDialog: true,
  AddFromDatasetDialog: true,
}

const STUDY_OBS = {
  protocol_info: { pre_times: [0], sim_times: [[5]], params_to_change: {} },
  data_items: [
    { data_item_name: 'x_max', data_type: 'constant', operation: 'max', operands: ['m/x'], unit: 'dimensionless', value: 30, std: 3, experiment_idx: 0, subexperiment_idx: 0 },
  ],
  prediction_items: [],
  prediction_plots: [],
  note: 'a key CUFLynx does not read',
}

const MODEL_VARIABLES = {
  params: ['m/k'],
  odes: ['m/x'],
  algebraic: ['m/y'],
  all_names: ['m/k', 'm/x', 'm/y', 'environment/time'],
  initial_values: { 'm/k': 2, 'm/x': 1 },
  units: { 'm/k': 'per_second', 'm/x': 'dimensionless' },
}

function mountDialog(props = {}) {
  return mount(EditObsDataDialog, {
    props: {
      visible: true,
      modelId: 'abc',
      modelVariables: MODEL_VARIABLES,
      modelName: 'M',
      loadedFilename: 'obs.json',
      outputsDir: '/out',
      ...props,
    },
    global: { stubs },
  })
}

const editor = (wrapper) => wrapper.findComponent(EditorStub)
const saveButton = (wrapper) => wrapper.find('[data-testid="eo-save"]')

beforeEach(() => {
  vi.clearAllMocks()
  getObsDataOptions.mockResolvedValue({ operations: ['max', 'min'], cost_types: ['gaussian_MLE'] })
  getObsDataDocument.mockResolvedValue(structuredClone(STUDY_OBS))
  uploadObsData.mockResolvedValue({ n_data_items: 1 })
})

describe('EditObsDataDialog', () => {
  it("edits the study's whole obs_data in protocol-kit's editor", async () => {
    const wrapper = mountDialog()
    await flushPromises()
    expect(getObsDataDocument).toHaveBeenCalledWith('abc')
    expect(editor(wrapper).props('document')).toEqual(STUDY_OBS)
    expect(editor(wrapper).props('preset')).toBe('cuflynx')
    // CA's vocabulary, the user's funcs included, read as protocol-kit reads it
    expect(editor(wrapper).props('vocabulary').operations.map((o) => o.name)).toEqual(['max', 'min'])
  })

  it("gives the editor the model's variables by kind, unit and value", async () => {
    const wrapper = mountDialog()
    await flushPromises()
    const variables = Object.fromEntries(editor(wrapper).props('variables').map((v) => [v.name, v]))
    expect(variables['m/k']).toEqual({ name: 'm/k', kind: 'constant', unit: 'per_second', value: 2 })
    expect(variables['m/x'].kind).toBe('state')
    expect(variables['m/y'].kind).toBe('variable')
    expect(variables['environment/time'].kind).toBe('variable')
    expect(editor(wrapper).props('getValue')('m/k')).toBe(2)
  })

  it('saves the edited document whole, as a dated copy, and says so', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    const edited = { ...STUDY_OBS, data_items: [{ ...STUDY_OBS.data_items[0], value: 31 }] }
    editor(wrapper).vm.$emit('update:document', edited)
    await flushPromises()
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    const [modelId, document, save] = uploadObsData.mock.calls[0]
    expect(modelId).toBe('abc')
    // every key, those protocol-kit edits and those it only keeps
    expect(document).toEqual(edited)
    expect(save.filename).toMatch(/^obs_\d{6}\.json$/)
    expect(save.outputsDir).toBe('/out')
    expect(wrapper.emitted('saved')[0][0]).toMatchObject({ n_data_items: 1 })
    expect(wrapper.emitted('update:visible').at(-1)).toEqual([false])
  })

  it('starts a study without obs_data from a protocol of one experiment', async () => {
    getObsDataDocument.mockResolvedValue(null)
    const wrapper = mountDialog()
    await flushPromises()
    expect(editor(wrapper).props('document')).toEqual({
      protocol_info: { pre_times: [0], sim_times: [[1]], params_to_change: {} },
    })
  })

  it('opens on a given document with its unset parameters already in the protocol', async () => {
    const save = vi.fn().mockResolvedValue({ path: '/wf/left_obs_data.json' })
    const wrapper = mountDialog({
      document: structuredClone(STUDY_OBS),
      preselect: ['C/x'],
      save,
      header: 'Protocol for step left',
    })
    await flushPromises()
    expect(getObsDataDocument).not.toHaveBeenCalled()
    const document = editor(wrapper).props('document')
    expect(document.protocol_info.params_to_change).toEqual({ 'C/x': [[null]] })
    expect(document.note).toBe(STUDY_OBS.note)
    // a parameter the step's model may not name is still offered, as one to set
    expect(editor(wrapper).props('variables').find((v) => v.name === 'C/x').kind).toBe('constant')
    // it has no value yet, so there is nothing CA would run: Save waits
    expect(wrapper.find('[data-testid="eo-problems"]').text()).toContain('1 problem')
    expect(saveButton(wrapper).attributes('disabled')).toBeDefined()

    const filled = JSON.parse(JSON.stringify(document))
    filled.protocol_info.params_to_change['C/x'] = [[0.5]]
    editor(wrapper).vm.$emit('update:document', filled)
    await flushPromises()
    expect(saveButton(wrapper).attributes('disabled')).toBeUndefined()
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(save).toHaveBeenCalledWith(filled)
    expect(uploadObsData).not.toHaveBeenCalled()
    expect(wrapper.emitted('saved')[0][0]).toEqual({ path: '/wf/left_obs_data.json' })
  })

  it("shows the server's reason when a save is refused, and stays open", async () => {
    const save = vi.fn().mockRejectedValue({ response: { data: { detail: 'the obs_data is not JSON' } } })
    const wrapper = mountDialog({ document: structuredClone(STUDY_OBS), save })
    await flushPromises()
    await saveButton(wrapper).trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="eo-error"]').text()).toBe('the obs_data is not JSON')
    expect(wrapper.emitted('update:visible')).toBeUndefined()
  })

  it('holds Save while CA would refuse a data item', async () => {
    const { data_type, ...untyped } = STUDY_OBS.data_items[0]
    getObsDataDocument.mockResolvedValue({ ...STUDY_OBS, data_items: [untyped] })
    const wrapper = mountDialog()
    await flushPromises()
    expect(data_type).toBe('constant')
    expect(wrapper.find('[data-testid="eo-problems"]').exists()).toBe(true)
    expect(saveButton(wrapper).attributes('disabled')).toBeDefined()
  })

  it('offers Custom funcs beside the data items', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    expect(wrapper.find('.editor-stub [data-testid="eo-add-op-func"]').exists()).toBe(true)
  })

  it("mounts protocol-kit's own editor on the document", async () => {
    const wrapper = mount(EditObsDataDialog, {
      props: { visible: true, modelId: 'abc', modelVariables: MODEL_VARIABLES },
      global: {
        plugins: [PrimeVue],
        stubs: { Dialog: DialogStub, EditOperationFuncsDialog: true },
      },
    })
    await flushPromises()
    expect(wrapper.find('[data-testid="od-data-items"]').exists()).toBe(true)
    expect(wrapper.findAll('[data-testid="od-data-row"]')).toHaveLength(1)
  })
})

// --- "Add from dataset" (the extraction dialog) -----------------------------
const EXTRACTION = {
  protocol_info: { pre_times: [0], sim_times: [[1]], params_to_change: {} },
  data_items: [{ data_item_name: 'x', data_type: 'constant', operation: 'max',
                 operands: ['a/b'], unit: 'mV', value: 1, std: 1 }],
  prediction_items: [],
}

describe('EditObsDataDialog add-from-dataset', () => {
  it('the button sits beside "Custom funcs" in the data items actions', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    const ids = editor(wrapper)
      .findAll('[data-testid]')
      .map((el) => el.attributes('data-testid'))
    expect(ids).toEqual(['eo-add-op-func', 'obs-add-from-dataset'])
  })

  it('an extraction into an empty document is adopted without asking', async () => {
    // There is nothing to lose, so a confirmation would be a question with one
    // sensible answer.
    getObsDataDocument.mockResolvedValue({ protocol_info: null, data_items: [], prediction_items: [] })
    const wrapper = mountDialog()
    await flushPromises()
    wrapper.vm.onExtracted({ obsData: structuredClone(EXTRACTION), texPath: '/out/r.tex' })
    await flushPromises()
    expect(editor(wrapper).props('document')).toEqual(EXTRACTION)
    expect(wrapper.find('[data-testid="obs-extract-offer"]').exists()).toBe(false)
  })

  it('an extraction over existing work is offered, not applied', async () => {
    // The items' experiment_idx only mean anything against the protocol_info
    // they came with, so appending them to a different one is the outcome that
    // would look like it worked.
    const wrapper = mountDialog()
    await flushPromises()
    wrapper.vm.onExtracted({ obsData: structuredClone(EXTRACTION), texPath: '/out/r.tex' })
    await flushPromises()
    expect(wrapper.find('[data-testid="obs-extract-offer"]').exists()).toBe(true)
    expect(editor(wrapper).props('document').data_items[0].data_item_name).toBe('x_max')

    await wrapper.find('[data-testid="obs-extract-adopt"]').trigger('click')
    await flushPromises()
    expect(editor(wrapper).props('document')).toEqual(EXTRACTION)
  })

  it('keeping mine discards the extraction and leaves the document alone', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    wrapper.vm.onExtracted({ obsData: structuredClone(EXTRACTION), texPath: '/out/r.tex' })
    await flushPromises()
    await wrapper.find('[data-testid="obs-extract-discard"]').trigger('click')
    expect(wrapper.find('[data-testid="obs-extract-offer"]').exists()).toBe(false)
    expect(editor(wrapper).props('document').data_items[0].data_item_name).toBe('x_max')
  })

  it('the offer names where the report went', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    wrapper.vm.onExtracted({
      obsData: structuredClone(EXTRACTION), texPath: '/out/r.tex', pdfPath: '/out/r.pdf',
    })
    await flushPromises()
    expect(wrapper.find('[data-testid="obs-extract-offer"]').text()).toContain('/out/r.pdf')
  })
})
