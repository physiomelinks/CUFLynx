import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../lib/api', () => ({
  uploadOmex: vi.fn(),
  uploadCellML: vi.fn(),
  uploadObsData: vi.fn(),
  uploadParamsForId: vi.fn(),
  getObsDataOptions: vi.fn(),
  fetchExampleModel: vi.fn(),
}))

import FileImport from './FileImport.vue'
import {
  uploadCellML,
  uploadObsData,
  uploadParamsForId,
  fetchExampleModel,
  uploadOmex,
} from '../lib/api'

// Real <button> stub so the Edit button's disabled state + click are observable;
// EditParamsDialog stub renders only when opened.
const ButtonStub = {
  props: ['label', 'disabled', 'icon', 'size', 'text', 'title'],
  emits: ['click'],
  template:
    '<button :disabled="disabled" v-bind="$attrs" @click="$emit(\'click\')">{{ label }}</button>',
}
const EditParamsStub = {
  props: ['visible'],
  template: '<div v-if="visible" data-testid="edit-dialog">open</div>',
}
const EditObsStub = {
  props: ['visible'],
  template: '<div v-if="visible" data-testid="edit-obs-dialog">open</div>',
}
// Exposes the visible state + a button to emit select-example, so the load flow
// through FileImport is observable without PrimeVue's Dialog internals.
const StartDialogStub = {
  props: ['visible'],
  emits: ['select-example', 'update:visible'],
  template:
    '<div v-if="visible" data-testid="start-dialog">' +
    '<button data-testid="pick-example" ' +
    "@click=\"$emit('select-example', { name: '3compartment', label: '3-compartment circulation', filename: '3compartment.omex' })\">" +
    'pick</button></div>',
}
const stubs = {
  Message: true,
  InputText: true,
  Button: ButtonStub,
  FileBrowserDialog: true,
  EditParamsDialog: EditParamsStub,
  EditObsDataDialog: EditObsStub,
  StartDialog: StartDialogStub,
}

// jsdom's File has no .text(); browsers do. Stub it for obs_data JSON reads.
function jsonFile(name, text) {
  const f = new File([text], name, { type: 'application/json' })
  f.text = () => Promise.resolve(text)
  return f
}

beforeEach(() => {
  uploadCellML.mockReset()
  uploadObsData.mockReset()
  uploadParamsForId.mockReset()
  fetchExampleModel.mockReset()
  uploadOmex.mockReset()
})

describe('FileImport', () => {
  it('test_cellml_drop_calls_upload', async () => {
    uploadCellML.mockResolvedValue({ model_id: 'abc', name: 'm' })
    const wrapper = mount(FileImport, { global: { stubs } })
    const file = new File(['<model/>'], 'model.cellml', { type: 'application/xml' })
    await wrapper
      .find('[data-testid="cellml-drop"]')
      .trigger('drop', { dataTransfer: { files: [file] } })
    await flushPromises()
    expect(uploadCellML).toHaveBeenCalledOnce()
    expect(wrapper.emitted('model-loaded')[0][0]).toEqual({
      model_id: 'abc',
      name: 'm',
      filename: 'model.cellml',
    })
  })

  it('sends a whole bundle (main + sister files) when several are dropped', async () => {
    uploadCellML.mockResolvedValue({ model_id: 'abc', name: 'CardiovascularSystem' })
    const wrapper = mount(FileImport, { global: { stubs } })
    const files = [
      new File(['<model/>'], '3compartment.cellml', { type: 'application/xml' }),
      new File(['<model/>'], '3compartment_modules.cellml', { type: 'application/xml' }),
      new File(['<model/>'], '3compartment_units.cellml', { type: 'application/xml' }),
    ]
    await wrapper
      .find('[data-testid="cellml-drop"]')
      .trigger('drop', { dataTransfer: { files } })
    await flushPromises()
    // All files forwarded to the server (which flattens them).
    expect(uploadCellML).toHaveBeenCalledOnce()
    expect(uploadCellML.mock.calls[0][0]).toHaveLength(3)
    // The display name comes from the main .cellml.
    expect(wrapper.emitted('model-loaded')[0][0].filename).toBe('3compartment.cellml')
  })

  it('rejects a bundle if any file is not .cellml/.xml', async () => {
    const wrapper = mount(FileImport, { global: { stubs } })
    const files = [
      new File(['<model/>'], 'main.cellml', { type: 'application/xml' }),
      new File(['x'], 'notes.txt', { type: 'text/plain' }),
    ]
    await wrapper
      .find('[data-testid="cellml-drop"]')
      .trigger('drop', { dataTransfer: { files } })
    await flushPromises()
    expect(uploadCellML).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="import-error"]').exists()).toBe(true)
  })

  it('test_invalid_extension_shows_error', async () => {
    const wrapper = mount(FileImport, { global: { stubs } })
    const file = new File(['hello'], 'notes.txt', { type: 'text/plain' })
    await wrapper
      .find('[data-testid="cellml-drop"]')
      .trigger('drop', { dataTransfer: { files: [file] } })
    await flushPromises()
    expect(uploadCellML).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="import-error"]').exists()).toBe(true)
  })

  // Drop order should not matter (issue #16): obs/params dropped before a
  // model is loaded are queued and attached once a model_id arrives.
  it('test_obs_dropped_before_model_is_queued_then_attached', async () => {
    uploadObsData.mockResolvedValue({ model_id: 'abc', experiment_count: 1 })
    const wrapper = mount(FileImport, { global: { stubs } }) // no modelId
    const obs = jsonFile('obs.json', '{"protocol_info":{}}')
    await wrapper
      .find('[data-testid="obs-drop"]')
      .trigger('drop', { dataTransfer: { files: [obs] } })
    await flushPromises()
    expect(uploadObsData).not.toHaveBeenCalled()
    expect(wrapper.emitted('obs-data-loaded')).toBeFalsy()
    expect(wrapper.find('[data-testid="import-notice"]').exists()).toBe(true)

    await wrapper.setProps({ modelId: 'abc' })
    await flushPromises()
    expect(uploadObsData).toHaveBeenCalledWith('abc', { protocol_info: {} })
    expect(wrapper.emitted('obs-data-loaded')[0][0]).toMatchObject({
      model_id: 'abc',
      experiment_count: 1,
      filename: 'obs.json', // attachObs now carries the filename for versioning
    })
  })

  it('test_params_dropped_before_model_is_queued_then_attached', async () => {
    uploadParamsForId.mockResolvedValue({ params: [{ name: 'p' }] })
    const wrapper = mount(FileImport, { global: { stubs } })
    const csv = new File(['vessel_name,param_name\n'], 'p.csv', { type: 'text/csv' })
    await wrapper
      .find('[data-testid="params-drop"]')
      .trigger('drop', { dataTransfer: { files: [csv] } })
    await flushPromises()
    expect(uploadParamsForId).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="import-notice"]').exists()).toBe(true)

    await wrapper.setProps({ modelId: 'abc' })
    await flushPromises()
    expect(uploadParamsForId).toHaveBeenCalledWith(csv, 'abc')
    expect(wrapper.emitted('params-loaded')[0][0]).toMatchObject({
      params: [{ name: 'p' }],
      filename: 'p.csv',
    })
  })

  it('test_obs_drop_with_model_attaches_immediately', async () => {
    uploadObsData.mockResolvedValue({ model_id: 'abc' })
    const wrapper = mount(FileImport, { props: { modelId: 'abc' }, global: { stubs } })
    const obs = jsonFile('obs.json', '{"x":1}')
    await wrapper
      .find('[data-testid="obs-drop"]')
      .trigger('drop', { dataTransfer: { files: [obs] } })
    await flushPromises()
    expect(uploadObsData).toHaveBeenCalledWith('abc', { x: 1 })
    expect(wrapper.emitted('obs-data-loaded')).toBeTruthy()
  })

  it('test_edit_button_disabled_without_model', () => {
    const wrapper = mount(FileImport, { global: { stubs } }) // no modelId
    const edit = wrapper.find('[data-testid="params-edit"]')
    expect(edit.exists()).toBe(true)
    expect(edit.attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="edit-dialog"]').exists()).toBe(false)
  })

  it('test_edit_button_enabled_with_model_opens_dialog', async () => {
    const wrapper = mount(FileImport, { props: { modelId: 'abc' }, global: { stubs } })
    const edit = wrapper.find('[data-testid="params-edit"]')
    expect(edit.attributes('disabled')).toBeUndefined()
    await edit.trigger('click')
    expect(wrapper.find('[data-testid="edit-dialog"]').exists()).toBe(true)
  })

  it('test_obs_edit_button_disabled_without_model', () => {
    const wrapper = mount(FileImport, { global: { stubs } }) // no modelId
    const edit = wrapper.find('[data-testid="obs-edit"]')
    expect(edit.exists()).toBe(true)
    expect(edit.attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="edit-obs-dialog"]').exists()).toBe(false)
  })

  it('test_obs_edit_button_enabled_with_model_opens_dialog', async () => {
    const wrapper = mount(FileImport, { props: { modelId: 'abc' }, global: { stubs } })
    const edit = wrapper.find('[data-testid="obs-edit"]')
    expect(edit.attributes('disabled')).toBeUndefined()
    await edit.trigger('click')
    expect(wrapper.find('[data-testid="edit-obs-dialog"]').exists()).toBe(true)
  })

  // Issue #91: the box beside the CellML dropzone reads "Create" until a model
  // is loaded, then "Edit".
  it('shows Create with no model and Edit with a model', async () => {
    const wrapper = mount(FileImport, { global: { stubs } }) // no modelId
    const btn = wrapper.find('[data-testid="start-edit"]')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toBe('Create')
    await wrapper.setProps({ modelId: 'abc' })
    expect(wrapper.find('[data-testid="start-edit"]').text()).toBe('Edit')
  })

  it('Create opens the dialog, and picking the example loads the whole study', async () => {
    // The example ships as a COMBINE archive, so one click brings the model,
    // its obs_data and its params_for_id (#180).
    const file = new File(['PK'], '3compartment.omex', { type: 'application/zip' })
    fetchExampleModel.mockResolvedValue(file)
    uploadOmex.mockResolvedValue({
      model_id: 'ex',
      name: 'CardiovascularSystem',
      model_filename: '3compartment_flat.cellml',
      obs_data: { data_items: [{ name: 'x' }] },
      params_for_id: { params: [{ name: 'p' }], filename: '3compartment_params_for_id.csv' },
    })
    const wrapper = mount(FileImport, { global: { stubs } }) // no modelId

    // Dialog closed until Start is clicked.
    expect(wrapper.find('[data-testid="start-dialog"]').exists()).toBe(false)
    await wrapper.find('[data-testid="start-edit"]').trigger('click')
    expect(wrapper.find('[data-testid="start-dialog"]').exists()).toBe(true)

    // Choosing the example fetches it and feeds it through the archive flow.
    await wrapper.find('[data-testid="pick-example"]').trigger('click')
    await flushPromises()
    expect(fetchExampleModel).toHaveBeenCalledWith('3compartment', '3compartment.omex')
    expect(uploadOmex).toHaveBeenCalledOnce()
    expect(uploadOmex.mock.calls[0][0]).toBe(file)
    expect(uploadCellML).not.toHaveBeenCalled()
    expect(wrapper.emitted('model-loaded')[0][0]).toMatchObject({
      model_id: 'ex',
      filename: '3compartment_flat.cellml',
    })
    expect(wrapper.emitted('obs-data-loaded')[0][0]).toMatchObject({ model_id: 'ex' })
    expect(wrapper.emitted('params-loaded')[0][0]).toMatchObject({
      filename: '3compartment_params_for_id.csv',
    })
  })

  it('a non-archive example still loads through the plain CellML flow', async () => {
    // The archive is what examples ship as, not a requirement of the mechanism.
    const file = new File(['<model/>'], 'example.cellml', { type: 'application/xml' })
    fetchExampleModel.mockResolvedValue(file)
    uploadCellML.mockResolvedValue({ model_id: 'ex', name: 'm' })
    const wrapper = mount(FileImport, { global: { stubs } })

    await wrapper.find('[data-testid="start-edit"]').trigger('click')
    await wrapper.find('[data-testid="pick-example"]').trigger('click')
    await flushPromises()
    expect(uploadOmex).not.toHaveBeenCalled()
    expect(uploadCellML).toHaveBeenCalledOnce()
  })

  it('Edit opens PhLynx instead of the Start dialog when a model is loaded', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => null)
    const wrapper = mount(FileImport, { props: { modelId: 'abc' }, global: { stubs } })
    await wrapper.find('[data-testid="start-edit"]').trigger('click')
    expect(wrapper.find('[data-testid="start-dialog"]').exists()).toBe(false)
    expect(openSpy).toHaveBeenCalledOnce()
    openSpy.mockRestore()
  })

  it('export buttons are disabled until a model is loaded', () => {
    const wrapper = mount(FileImport, { global: { stubs } }) // canExport defaults false
    expect(wrapper.find('[data-testid="export-pipeline"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="export-plotting"]').attributes('disabled')).toBeDefined()
  })

  it('export buttons emit their events when exporting is enabled', async () => {
    const wrapper = mount(FileImport, { props: { canExport: true }, global: { stubs } })
    await wrapper.find('[data-testid="export-pipeline"]').trigger('click')
    await wrapper.find('[data-testid="export-plotting"]').trigger('click')
    expect(wrapper.emitted('export-pipeline')).toHaveLength(1)
    expect(wrapper.emitted('export-plotting')).toHaveLength(1)
  })
})

// Issue #137: nothing visibly happened when a file was dragged over or dropped,
// and the dropzone went on claiming most of the row long after it had said all
// it had to say.
describe('FileImport drop feedback (#137)', () => {
  const mountImport = (props = {}) =>
    mount(FileImport, { props, global: { stubs } })
  const zone = (w, id) => w.find(`[data-testid="${id}"]`)

  it('highlights the zone a file is dragged over, and only that one', async () => {
    const wrapper = mountImport()
    await zone(wrapper, 'obs-drop').trigger('dragover')
    expect(zone(wrapper, 'obs-drop').classes()).toContain('drag-over')
    expect(zone(wrapper, 'cellml-drop').classes()).not.toContain('drag-over')
  })

  it('clears the highlight when the file leaves again', async () => {
    const wrapper = mountImport()
    await zone(wrapper, 'obs-drop').trigger('dragover')
    await zone(wrapper, 'obs-drop').trigger('dragleave')
    expect(zone(wrapper, 'obs-drop').classes()).not.toContain('drag-over')
  })

  // A highlight left behind after the drop would say a drag was still in flight.
  it('clears the highlight on drop', async () => {
    const wrapper = mountImport()
    await zone(wrapper, 'obs-drop').trigger('dragover')
    await zone(wrapper, 'obs-drop').trigger('drop')
    expect(zone(wrapper, 'obs-drop').classes()).not.toContain('drag-over')
  })

  describe('once something is loaded', () => {
    it('stays full size while empty', () => {
      const wrapper = mountImport()
      expect(zone(wrapper, 'cellml-drop').classes()).not.toContain('compact')
      expect(zone(wrapper, 'obs-drop').classes()).not.toContain('compact')
      expect(zone(wrapper, 'params-drop').classes()).not.toContain('compact')
    })

    it('shrinks the zone whose file is in, leaving the others alone', () => {
      const wrapper = mountImport({ loadedObsFilename: 'obs_data.json' })
      expect(zone(wrapper, 'obs-drop').classes()).toContain('compact')
      expect(zone(wrapper, 'cellml-drop').classes()).not.toContain('compact')
    })

    it('names what is loaded instead of repeating the instructions', () => {
      const wrapper = mountImport({ loadedObsFilename: 'obs_data.json' })
      expect(wrapper.find('[data-testid="obs-loaded"]').text()).toBe('obs_data.json')
      expect(zone(wrapper, 'obs-drop').text()).not.toContain('or click to browse')
    })

    it('names the model for the CellML zone', () => {
      const wrapper = mountImport({ modelId: 'abc', modelName: '3compartment' })
      expect(wrapper.find('[data-testid="cellml-loaded"]').text()).toBe('3compartment')
    })

    it('still accepts a replacement', () => {
      const wrapper = mountImport({ loadedFilename: 'params.csv' })
      expect(zone(wrapper, 'params-drop').text()).toContain('drop another to replace')
      expect(zone(wrapper, 'params-drop').find('input[type="file"]').exists()).toBe(true)
    })
  })
})

// Issue #27: the CellML zone accepts a Myokit .mmt, converted server-side. The
// `accept` attribute alone was not enough — the drop handler had its own
// extension guard, which rejected the file before the upload was ever attempted.
describe('FileImport accepts .mmt (#27)', () => {
  const drop = async (name) => {
    const wrapper = mount(FileImport, { global: { stubs } })
    const file = new File(['[[model]]\n'], name, { type: 'text/plain' })
    await wrapper
      .find('[data-testid="cellml-drop"]')
      .trigger('drop', { dataTransfer: { files: [file] } })
    await flushPromises()
    return wrapper
  }

  beforeEach(() => {
    uploadCellML.mockReset().mockResolvedValue({ model_id: 'abc', name: 'm' })
    uploadOmex.mockReset()
  })

  it('uploads a dropped .mmt instead of rejecting it', async () => {
    const wrapper = await drop('br-1977.mmt')
    expect(uploadCellML).toHaveBeenCalledTimes(1)
    expect(wrapper.emitted('model-loaded')).toBeTruthy()
  })

  it('still uploads a .cellml', async () => {
    await drop('model.cellml')
    expect(uploadCellML).toHaveBeenCalledTimes(1)
  })

  // A .mmt is a loose file, not an archive: the omex path must let it through
  // rather than swallowing every drop now that both features share the zone.
  it('does not mistake a .mmt for an archive', async () => {
    await drop('br-1977.mmt')
    expect(uploadOmex).not.toHaveBeenCalled()
  })

  // The guard and the file picker must agree, or one path works and the other
  // does not — which is exactly how this slipped through.
  it('offers the same extensions the drop handler accepts', () => {
    const wrapper = mount(FileImport, { global: { stubs } })
    const accept = wrapper
      .find('[data-testid="cellml-drop"] input[type="file"]')
      .attributes('accept')
    for (const ext of ['.cellml', '.mmt', '.omex']) expect(accept).toContain(ext)
  })

  it('still rejects an unrelated file, naming what it wanted', async () => {
    const wrapper = await drop('notes.txt')
    expect(uploadCellML).not.toHaveBeenCalled()
    expect(wrapper.vm.error).toContain('.mmt')
  })
})

// Issue #27: a .mmt carries a [[protocol]] that the model import leaves behind,
// because in CUFLynx the protocol lives in obs_data. The server converts it and
// the UI adopts it — but only when the user has none of their own, since a
// derived protocol replacing a hand-written one would be worse than no
// conversion at all.
describe('FileImport adopts the .mmt protocol as obs_data (#27)', () => {
  const DERIVED = {
    filename: 'br-1977_obs_data.json',
    obs_data: { protocol_info: { sim_times: [[100, 2, 898]] }, data_items: [] },
    notes: ['the protocol repeats indefinitely, so it was cut to 2 beat(s).'],
    path: '/out/br-1977_obs_data.json',
    reason: null,
  }

  const dropMmt = async (payload = { model_id: 'abc', name: 'm', protocol_obs_data: DERIVED }) => {
    uploadCellML.mockResolvedValue(payload)
    const wrapper = mount(FileImport, { global: { stubs } }) // no model yet
    const file = new File(['[[model]]\n'], 'br-1977.mmt', { type: 'text/plain' })
    await wrapper
      .find('[data-testid="cellml-drop"]')
      .trigger('drop', { dataTransfer: { files: [file] } })
    await flushPromises()
    await wrapper.setProps({ modelId: 'abc' }) // what the parent does on model-loaded
    await flushPromises()
    return wrapper
  }

  beforeEach(() => {
    uploadCellML.mockReset()
    uploadObsData.mockReset().mockResolvedValue({ model_id: 'abc', experiment_count: 1 })
  })

  it('creates an obs_data from the protocol when there is none', async () => {
    const wrapper = await dropMmt()
    expect(uploadObsData).toHaveBeenCalledWith('abc', DERIVED.obs_data)
    expect(wrapper.emitted('obs-data-loaded')[0][0]).toMatchObject({
      filename: 'br-1977_obs_data.json',
    })
  })

  it('says what it made, where it went, and what is still missing', async () => {
    const wrapper = await dropMmt()
    expect(wrapper.vm.notice).toContain('br-1977_obs_data.json')
    expect(wrapper.vm.notice).toContain('/out/br-1977_obs_data.json')
    expect(wrapper.vm.notice).toContain('no data_items')
    // The truncation of an endless protocol is a choice, so it has to be shown.
    expect(wrapper.vm.notice).toContain('repeats indefinitely')
  })

  // The one that matters: a protocol the user wrote must survive.
  it('never overwrites an obs_data the user dropped', async () => {
    uploadCellML.mockResolvedValue({
      model_id: 'abc',
      name: 'm',
      protocol_obs_data: DERIVED,
    })
    const wrapper = mount(FileImport, { global: { stubs } })
    const obs = jsonFile('mine.json', '{"protocol_info":{"sim_times":[[1]]}}')
    await wrapper
      .find('[data-testid="obs-drop"]')
      .trigger('drop', { dataTransfer: { files: [obs] } })
    await flushPromises()

    const mmt = new File(['[[model]]\n'], 'br-1977.mmt', { type: 'text/plain' })
    await wrapper
      .find('[data-testid="cellml-drop"]')
      .trigger('drop', { dataTransfer: { files: [mmt] } })
    await flushPromises()
    await wrapper.setProps({ modelId: 'abc' })
    await flushPromises()

    expect(uploadObsData).toHaveBeenCalledTimes(1)
    expect(uploadObsData).toHaveBeenCalledWith('abc', { protocol_info: { sim_times: [[1]] } })
  })

  it('says why when the protocol could not be converted', async () => {
    const wrapper = await dropMmt({
      model_id: 'abc',
      name: 'm',
      protocol_obs_data: {
        filename: 'x_obs_data.json',
        obs_data: null,
        notes: [],
        reason: 'no variable in that .mmt is bound to `pace`',
      },
    })
    expect(uploadObsData).not.toHaveBeenCalled()
    expect(wrapper.vm.notice).toContain('bound to `pace`')
  })

  it('does nothing for a model that carries no protocol', async () => {
    const wrapper = await dropMmt({ model_id: 'abc', name: 'm', protocol_obs_data: null })
    expect(uploadObsData).not.toHaveBeenCalled()
    expect(wrapper.vm.notice).toBe('')
  })

  // A second model must not re-adopt the first one's protocol.
  it('does not re-apply the protocol on the next model load', async () => {
    const wrapper = await dropMmt()
    expect(uploadObsData).toHaveBeenCalledTimes(1)
    await wrapper.setProps({ modelId: 'def' })
    await flushPromises()
    expect(uploadObsData).toHaveBeenCalledTimes(1)
  })
})

// Issue #149: a COMBINE archive is the study, not any one of its files, so it is
// accepted on every import box rather than making the user unzip it.
describe('FileImport omex (#149)', () => {
  const omexFile = () => new File(['PK'], 'study.omex', { type: 'application/zip' })
  const RESPONSE = {
    model_id: 'abc',
    name: 'CardiovascularSystem',
    model_filename: '3compartment_flat.cellml',
    obs_data: { filename: 'obs.json', data_items: [{}], protocol_info: null },
    params_for_id: { filename: 'params.csv', params: [{ qname: 'a/b' }] },
    module_config_path: '/out/module_config.json',
  }

  const dropOn = async (testid, file = omexFile()) => {
    const wrapper = mount(FileImport, { global: { stubs } })
    await wrapper
      .find(`[data-testid="${testid}"]`)
      .trigger('drop', { dataTransfer: { files: [file] } })
    await flushPromises()
    return wrapper
  }

  beforeEach(() => uploadOmex.mockReset().mockResolvedValue(RESPONSE))

  it.each(['cellml-drop', 'obs-drop', 'params-drop'])(
    'accepts an archive dropped on %s',
    async (testid) => {
      const wrapper = await dropOn(testid)
      expect(uploadOmex).toHaveBeenCalledTimes(1)
      expect(wrapper.emitted('model-loaded')).toBeTruthy()
    },
  )

  it('loads all three parts from one drop', async () => {
    const wrapper = await dropOn('cellml-drop')
    expect(wrapper.emitted('model-loaded')[0][0].model_id).toBe('abc')
    expect(wrapper.emitted('obs-data-loaded')[0][0].data_items).toHaveLength(1)
    expect(wrapper.emitted('params-loaded')[0][0].params).toHaveLength(1)
  })

  it('says the PhLynx layout was kept, so the archive round-trips', async () => {
    const wrapper = await dropOn('cellml-drop')
    expect(wrapper.vm.notice).toContain('PhLynx layout kept')
  })

  // An archive with a bad obs_data still gave us a model worth having.
  it('reports a bad part without losing the rest', async () => {
    uploadOmex.mockResolvedValue({
      ...RESPONSE,
      obs_data: { filename: 'obs.json', error: 'invalid JSON' },
    })
    const wrapper = await dropOn('obs-drop')
    expect(wrapper.emitted('model-loaded')).toBeTruthy()
    expect(wrapper.emitted('obs-data-loaded')).toBeFalsy()
    expect(wrapper.vm.notice).toContain('invalid JSON')
  })

  it('leaves an ordinary file to its own dropzone', async () => {
    const json = new File(['{}'], 'obs_data.json', { type: 'application/json' })
    await dropOn('obs-drop', json)
    expect(uploadOmex).not.toHaveBeenCalled()
  })
})

// External python models: the user drops a .py holding the solver class itself.
// It goes up the same route as a CellML model, and the server answers with
// `model_format: "external_python"`, which the app needs in order to lock the
// backend to it — so the response must reach the parent intact.
describe('FileImport accepts an external python model (.py)', () => {
  const drop = async (name, content = 'SIM_HELPER = MyModel\n') => {
    const wrapper = mount(FileImport, { global: { stubs } })
    const file = new File([content], name, { type: 'text/x-python' })
    await wrapper
      .find('[data-testid="cellml-drop"]')
      .trigger('drop', { dataTransfer: { files: [file] } })
    await flushPromises()
    return wrapper
  }

  beforeEach(() => {
    uploadCellML.mockReset().mockResolvedValue({
      model_id: 'py1',
      name: 'heat_fenics',
      model_format: 'external_python',
    })
    uploadOmex.mockReset()
  })

  it('uploads a dropped .py instead of rejecting it', async () => {
    const wrapper = await drop('heat_fenics.py')
    expect(uploadCellML).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="import-error"]').exists()).toBe(false)
    expect(wrapper.emitted('model-loaded')[0][0].filename).toBe('heat_fenics.py')
  })

  // The whole format lock hangs off this field; dropping it would leave the app
  // trying to run a Python class through Myokit.
  it('passes model_format through to the parent', async () => {
    const wrapper = await drop('heat_fenics.py')
    expect(wrapper.emitted('model-loaded')[0][0].model_format).toBe('external_python')
  })

  it('does not mistake a .py for an archive', async () => {
    await drop('heat_fenics.py')
    expect(uploadOmex).not.toHaveBeenCalled()
  })

  // Accepting .py must not turn the zone into "any file at all".
  it('still rejects an unrelated file, naming what it wanted', async () => {
    const wrapper = await drop('notes.txt', 'hello')
    expect(uploadCellML).not.toHaveBeenCalled()
    expect(wrapper.vm.error).toContain('.py')
    expect(wrapper.vm.error).toContain('.mmt')
  })

  // The picker and the drop guard have to agree, or browsing for a .py greys it
  // out while dragging the same file works.
  it('offers .py in the file picker too', () => {
    const wrapper = mount(FileImport, { global: { stubs } })
    const accept = wrapper
      .find('[data-testid="cellml-drop"] input[type="file"]')
      .attributes('accept')
    for (const ext of ['.cellml', '.mmt', '.py', '.omex']) expect(accept).toContain(ext)
  })
})
