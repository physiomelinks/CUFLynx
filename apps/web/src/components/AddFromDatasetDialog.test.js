import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('../lib/api', () => ({
  getObsDataOptions: vi.fn(),
  scanDatasets: vi.fn(),
  startObsExtract: vi.fn(),
  getObsExtractStatus: vi.fn(),
  cancelObsExtract: vi.fn(),
  saveObsExtractConfig: vi.fn(),
  loadObsExtractConfig: vi.fn(),
  listDir: vi.fn(async () => ({ path: '/', parent: null, entries: [] })),
  makeDir: vi.fn(),
}))

import {
  getObsDataOptions,
  scanDatasets,
  startObsExtract,
  getObsExtractStatus,
  saveObsExtractConfig,
} from '../lib/api'
import AddFromDatasetDialog from './AddFromDatasetDialog.vue'

const DialogStub = {
  props: ['visible'],
  template: '<div v-if="visible"><slot/><slot name="footer"/></div>',
}
const ButtonStub = {
  props: ['label', 'disabled'],
  emits: ['click'],
  template: '<button :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
}
const MessageStub = { template: '<div><slot/></div>' }
const SelectStub = {
  props: ['modelValue', 'options'],
  emits: ['update:modelValue'],
  template:
    '<select :value="modelValue" @change="$emit(\'update:modelValue\', $event.target.value)">' +
    '<option v-for="o in options" :key="o" :value="o">{{ o }}</option></select>',
}
const stubs = {
  Dialog: DialogStub,
  Button: ButtonStub,
  Message: MessageStub,
  SearchableSelect: SelectStub,
  FileBrowserDialog: true,
}

const SCAN = {
  root: '/data/Wistar',
  datasets: [
    {
      path: '/data/Wistar/4AP/a.1.Kv-90.1.wcp', case_name: '4AP_a.1.Kv-90.1.wcp',
      protocol: '4AP', subprotocol: 'Kv-90', format: 'wcp', readable: true,
      sweep_count: 4, channels: [], needs: [],
    },
    {
      path: '/data/Wistar/4AP/b.1.Kv-90.1.wcp', case_name: '4AP_b.1.Kv-90.1.wcp',
      protocol: '4AP', subprotocol: 'Kv-90', format: 'wcp', readable: false,
      error: 'neither reader could open it', needs: [],
    },
  ],
  groups: [{ group: '4AP|Kv-90', protocol: '4AP', subprotocol: 'Kv-90', n_datasets: 2 }],
  warnings: ['1 of 2 file(s) could not be read; see each row for the reason.'],
  suggested_binding: { current_command_param: 'soma_SN/I_in' },
}

const OPTIONS = {
  operations: ['max_in_range', 'calc_spike_count_windowed', 'first_peak_time'],
  operation_kwargs_schema: {
    // No `_in_range` suffix, but it takes the fractions -- the case a name rule misses.
    calc_spike_count_windowed: [
      { name: 'start_frac' }, { name: 'end_frac' }, { name: 'spike_min_thresh' },
    ],
    max_in_range: [{ name: 'start_frac' }, { name: 'end_frac' }],
    first_peak_time: [{ name: 'spike_min_thresh' }],
  },
}

beforeEach(() => {
  vi.clearAllMocks()
  getObsDataOptions.mockResolvedValue(OPTIONS)
  scanDatasets.mockResolvedValue(SCAN)
})

async function mounted(props = {}) {
  const wrapper = mount(AddFromDatasetDialog, {
    props: {
      visible: true, modelId: 'm1', outputsDir: '/out',
      modelVariables: {
        all_names: ['soma_SN/I_in', 'soma_SN/V_sensed'],
        params: ['soma_SN/I_in'],
      },
      ...props,
    },
    global: { stubs },
  })
  await flushPromises()
  return wrapper
}

async function scanned() {
  const wrapper = await mounted()
  await wrapper.find('[data-testid="obsx-root"]').setValue('/data/Wistar')
  await wrapper.find('[data-testid="obsx-scan"]').trigger('click')
  await flushPromises()
  return wrapper
}

describe('AddFromDatasetDialog', () => {
  it('scans a folder and groups what it finds', async () => {
    const wrapper = await scanned()
    expect(scanDatasets).toHaveBeenCalledWith(
      expect.objectContaining({ root: '/data/Wistar', model_id: 'm1' }),
    )
    expect(wrapper.findAll('[data-testid="obsx-group"]').length).toBe(1)
    expect(wrapper.text()).toContain('4AP|Kv-90')
  })

  it('surfaces a scan warning rather than hiding it', async () => {
    const wrapper = await scanned()
    expect(wrapper.find('[data-testid="obsx-scan-warning"]').text()).toContain(
      'could not be read',
    )
  })

  it('marks a file neither reader could open', async () => {
    const wrapper = await scanned()
    await wrapper.find('[data-testid="obsx-group-used"]').setValue(true)
    await wrapper.find('.obsx-group-name').trigger('click')
    const bad = wrapper.find('[data-testid="obsx-dataset-error"]')
    expect(bad.text()).toBe('unreadable')
    expect(bad.attributes('title')).toContain('neither reader')
  })

  it('ticking a group selects its recordings', async () => {
    const wrapper = await scanned()
    await wrapper.find('[data-testid="obsx-group-used"]').setValue(true)
    await wrapper.find('.obsx-group-name').trigger('click')
    const boxes = wrapper.findAll('[data-testid="obsx-dataset-used"]')
    expect(boxes.length).toBe(2)
    expect(boxes.every((b) => b.element.checked)).toBe(true)
  })

  it('offers a range only when the operation actually takes one', async () => {
    // The regression: `calc_spike_count_windowed` has no `_in_range` suffix but
    // does take start_frac/end_frac, so a name rule would deny it a range and
    // silently apply the defaults.
    const wrapper = await scanned()
    await wrapper.find('[data-testid="obsx-group-used"]').setValue(true)
    await wrapper.find('.obsx-group-name').trigger('click')
    await wrapper.find('[data-testid="obsx-feature-add"]').trigger('click')
    expect(wrapper.find('[data-testid="obsx-range-start"]').exists()).toBe(true)

    await wrapper.find('[data-testid="obsx-feature-op"]').setValue('calc_spike_count_windowed')
    expect(wrapper.find('[data-testid="obsx-range-start"]').exists()).toBe(true)

    await wrapper.find('[data-testid="obsx-feature-op"]').setValue('first_peak_time')
    expect(wrapper.find('[data-testid="obsx-range-start"]').exists()).toBe(false)
  })

  it('renders a kwarg input per schema entry, minus the range ones', async () => {
    const wrapper = await scanned()
    await wrapper.find('[data-testid="obsx-group-used"]').setValue(true)
    await wrapper.find('.obsx-group-name').trigger('click')
    await wrapper.find('[data-testid="obsx-feature-add"]').trigger('click')
    await wrapper.find('[data-testid="obsx-feature-op"]').setValue('calc_spike_count_windowed')
    expect(wrapper.find('[data-testid="obsx-kwarg-spike_min_thresh"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="obsx-kwarg-start_frac"]').exists()).toBe(false)
  })

  it('will not extract until every unit is confirmed', async () => {
    const wrapper = await scanned()
    await wrapper.find('[data-testid="obsx-group-used"]').setValue(true)
    await wrapper.find('.obsx-group-name').trigger('click')
    await wrapper.find('[data-testid="obsx-feature-add"]').trigger('click')

    expect(wrapper.find('[data-testid="obsx-extract"]').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[data-testid="obsx-unit-block"]').text()).toContain('max_in_range')

    await wrapper.find('[data-testid="obsx-feature-unit-confirm"]').setValue(true)
    expect(wrapper.find('[data-testid="obsx-extract"]').attributes('disabled')).toBeUndefined()
  })

  it('cannot extract with nothing selected', async () => {
    const wrapper = await scanned()
    expect(wrapper.find('[data-testid="obsx-extract"]').attributes('disabled')).toBeDefined()
  })

  it('runs the job, polls it, and emits what it produced', async () => {
    startObsExtract.mockResolvedValue({ job_id: 'j1' })
    getObsExtractStatus
      .mockResolvedValueOnce({
        state: 'running', lines: ['[info] extracting'], next_offset: 1, result: null,
        error: '', warning: '',
      })
      .mockResolvedValueOnce({
        state: 'done', lines: ['[info] 4 data item(s)'], next_offset: 2, error: '',
        warning: '',
        result: {
          obs_data: { protocol_info: {}, data_items: [{ data_item_name: 'a' }] },
          n_data_items: 1, n_experiments: 1, config_path: '/out/c.json',
          tex_path: '/out/r.tex', pdf_path: null, warnings: [],
        },
      })

    const wrapper = await scanned()
    await wrapper.find('[data-testid="obsx-group-used"]').setValue(true)
    await wrapper.find('.obsx-group-name').trigger('click')
    await wrapper.find('[data-testid="obsx-feature-add"]').trigger('click')
    await wrapper.find('[data-testid="obsx-feature-unit-confirm"]').setValue(true)
    await wrapper.find('[data-testid="obsx-extract"]').trigger('click')
    // The store polls on a 1 s timer, so allow more than waitFor's default.
    await vi.waitFor(() => expect(wrapper.emitted('extracted')).toBeTruthy(), {
      timeout: 5000,
    })

    const payload = wrapper.emitted('extracted')[0][0]
    expect(payload.obsData.data_items).toHaveLength(1)
    expect(payload.texPath).toBe('/out/r.tex')
  })

  it('saves the config to the outputs directory', async () => {
    saveObsExtractConfig.mockResolvedValue({ path: '/out/obs_extraction_config.json' })
    const wrapper = await scanned()
    await wrapper.find('[data-testid="obsx-save-config"]').trigger('click')
    await flushPromises()
    expect(saveObsExtractConfig).toHaveBeenCalledWith(
      expect.objectContaining({ source: expect.objectContaining({ root: '/data/Wistar' }) }),
      { outputsDir: '/out' },
    )
    expect(wrapper.find('[data-testid="obsx-saved"]').text()).toContain('/out/')
  })

  it('shows the reason a save was refused', async () => {
    saveObsExtractConfig.mockRejectedValue({ response: { data: { detail: "unknown key 'uzed'" } } })
    const wrapper = await scanned()
    await wrapper.find('[data-testid="obsx-save-config"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="obsx-error"]').text()).toContain('uzed')
  })

  it('a rescan keeps the selections already made', async () => {
    const wrapper = await scanned()
    await wrapper.find('[data-testid="obsx-group-used"]').setValue(true)
    await wrapper.find('[data-testid="obsx-scan"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="obsx-group-used"]').element.checked).toBe(true)
  })

  it('reports a failed scan', async () => {
    scanDatasets.mockRejectedValue({ response: { data: { detail: 'not a directory' } } })
    const wrapper = await mounted()
    await wrapper.find('[data-testid="obsx-root"]').setValue('/nope')
    await wrapper.find('[data-testid="obsx-scan"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="obsx-error"]').text()).toContain('not a directory')
  })
})
