import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../lib/api', () => ({
  uploadCellML: vi.fn(),
  uploadObsData: vi.fn(),
  uploadParamsForId: vi.fn(),
}))

import FileImport from './FileImport.vue'
import { uploadCellML } from '../lib/api'

const stubs = { Message: true }

beforeEach(() => {
  uploadCellML.mockReset()
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
    })
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
})
