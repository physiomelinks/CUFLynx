import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../lib/api', () => ({
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
    "@click=\"$emit('select-example', { name: '3compartment', label: '3-compartment circulation', filename: '3compartment_flat.cellml' })\">" +
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

  // Issue #91: the box beside the CellML dropzone reads "Start" until a model
  // is loaded, then "Edit".
  it('shows Start with no model and Edit with a model', async () => {
    const wrapper = mount(FileImport, { global: { stubs } }) // no modelId
    const btn = wrapper.find('[data-testid="start-edit"]')
    expect(btn.exists()).toBe(true)
    expect(btn.text()).toBe('Start')
    await wrapper.setProps({ modelId: 'abc' })
    expect(wrapper.find('[data-testid="start-edit"]').text()).toBe('Edit')
  })

  it('Start opens the dialog, and picking the example runs the load flow', async () => {
    const file = new File(['<model/>'], '3compartment_flat.cellml', {
      type: 'application/xml',
    })
    fetchExampleModel.mockResolvedValue(file)
    uploadCellML.mockResolvedValue({ model_id: 'ex', name: 'CardiovascularSystem' })
    const wrapper = mount(FileImport, { global: { stubs } }) // no modelId

    // Dialog closed until Start is clicked.
    expect(wrapper.find('[data-testid="start-dialog"]').exists()).toBe(false)
    await wrapper.find('[data-testid="start-edit"]').trigger('click')
    expect(wrapper.find('[data-testid="start-dialog"]').exists()).toBe(true)

    // Choosing the example fetches it and feeds it through the upload flow.
    await wrapper.find('[data-testid="pick-example"]').trigger('click')
    await flushPromises()
    expect(fetchExampleModel).toHaveBeenCalledWith('3compartment', '3compartment_flat.cellml')
    expect(uploadCellML).toHaveBeenCalledOnce()
    expect(uploadCellML.mock.calls[0][0]).toEqual([file])
    expect(wrapper.emitted('model-loaded')[0][0]).toEqual({
      model_id: 'ex',
      name: 'CardiovascularSystem',
      filename: '3compartment_flat.cellml',
    })
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
