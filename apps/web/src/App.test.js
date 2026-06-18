import { describe, it, expect, vi } from 'vitest'
import { shallowMount, flushPromises } from '@vue/test-utils'

// Mock the API so the onMounted bootstrap doesn't hit the network. shallowMount
// stubs every child component, so this test exercises App's own <script setup>
// — which is exactly where a "used before declaration" (TDZ) bug throws,
// blanking the whole app (the black screen). A setup-time error propagates out
// of mount(), so this test fails if such a bug is reintroduced.
vi.mock('./lib/api', () => ({
  getVariables: vi.fn().mockResolvedValue({}),
  simulate: vi.fn().mockResolvedValue({ time: [], outputs: {} }),
  runProtocol: vi.fn().mockResolvedValue({ experiments: [] }),
  getCalibrationDefaults: vi.fn().mockResolvedValue({}),
  getCalibrationPythons: vi.fn().mockResolvedValue({ pythons: [] }),
  getSensitivityDefaults: vi.fn().mockResolvedValue({}),
  getUQDefaults: vi.fn().mockResolvedValue({}),
  getConfig: vi.fn().mockResolvedValue({
    ca_dir: '',
    ca_exists: true,
    generated_model_format: 'cellml_only',
    solver: 'CVODE_myokit',
    solver_info: {},
    differentiable_operations: {},
  }),
  setConfig: vi.fn().mockResolvedValue({}),
}))

import App from './App.vue'

describe('App.vue', () => {
  it('mounts without a setup-time error (guards against TDZ / use-before-declare)', () => {
    const wrapper = shallowMount(App)
    // Reaching here means <script setup> ran end-to-end; the layout rendered.
    expect(wrapper.find('.layout').exists()).toBe(true)
  })

  it('asks where outputs should go on open, and persists the choice', async () => {
    localStorage.removeItem('cuflynx-outputs-dir')
    const wrapper = shallowMount(App)
    await flushPromises() // onMounted bootstrap finishes, then opens the prompt
    const setup = wrapper
      .findAllComponents({ name: 'FileBrowserDialog' })
      .find((d) => d.props('title') === 'Where should outputs be saved?')
    expect(setup).toBeTruthy()
    expect(setup.props('visible')).toBe(true)
    setup.vm.$emit('select', '/data/outputs')
    await flushPromises()
    expect(localStorage.getItem('cuflynx-outputs-dir')).toBe('/data/outputs')
  })
})
