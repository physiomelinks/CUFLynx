import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

vi.mock('../lib/api', () => ({
  getVariableMapping: vi.fn(),
  saveVariableMapping: vi.fn(),
  errorMessage: (e) => e?.response?.data?.detail || String(e?.message || e),
}))

import { getVariableMapping, saveVariableMapping } from '../lib/api'
import EditVariableMappingDialog from './EditVariableMappingDialog.vue'

const ROWS = [
  { variable_name: 'environment/time', latex: 't', default_latex: 't', kind: 'time',
    component: 'environment', units: 's' },
  { variable_name: 'parameters/p_mod_A', latex: 'p_{A}', default_latex: 'p_{A}',
    kind: 'parameter', component: 'parameters', units: 'dimensionless' },
  { variable_name: 'mod_A/y', latex: 'y_{A}', default_latex: 'y_{A}', kind: 'algebraic',
    component: 'mod_A_module', units: 'dimensionless' },
]

// Dialog teleports and needs PrimeVue; stub it as a plain box with its slots.
const stubs = {
  Dialog: {
    props: ['visible'],
    template: '<div v-if="visible" v-bind="$attrs"><slot /><slot name="footer" /></div>',
  },
  Message: { template: '<div class="msg" v-bind="$attrs"><slot /></div>' },
  Button: {
    props: ['label', 'disabled'],
    emits: ['click'],
    template: '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
  },
  InputText: {
    props: ['modelValue', 'size', 'invalid'],
    emits: ['update:modelValue'],
    template: '<input v-bind="$attrs" :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)" />',
  },
}

async function openDialog(props = {}) {
  const wrapper = mount(EditVariableMappingDialog, {
    props: { visible: false, modelId: 'm1', outputsDir: '/out', ...props },
    global: { stubs },
  })
  await wrapper.setProps({ visible: true })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  vi.clearAllMocks()
  getVariableMapping.mockResolvedValue({
    path: '/out/model_variable_mapping.csv', exists: false,
    rows: ROWS.map((r) => ({ ...r })), problems: { empty: [], duplicates: {} },
  })
})

describe('EditVariableMappingDialog', () => {
  it('reads the model\'s symbols when opened and typesets each one', async () => {
    const w = await openDialog()
    expect(getVariableMapping).toHaveBeenCalledWith('m1', '/out')
    expect(w.text()).toContain('/out/model_variable_mapping.csv')
    const row = w.find('[data-testid="variable-mapping-row-parameters/p_mod_A"]')
    expect(row.find('input').element.value).toBe('p_{A}')
    expect(row.find('.vm-preview').html()).toContain('katex')
  })

  it('filters by name and by kind', async () => {
    const w = await openDialog()
    await w.find('[data-testid="variable-mapping-filter"]').setValue('mod_a/y')
    expect(w.findAll('tbody tr')).toHaveLength(1)
    await w.find('[data-testid="variable-mapping-filter"]').setValue('')
    await w.find('[data-testid="variable-mapping-kind-parameter"]').trigger('click')
    expect(w.findAll('tbody tr').map((r) => r.attributes('data-testid'))).toEqual([
      'variable-mapping-row-parameters/p_mod_A'])
  })

  it('warns while typing when two variables share a symbol, and resets to the default',
    async () => {
      const w = await openDialog()
      const input = w.find('[data-testid="variable-mapping-input-mod_A/y"]')
      await input.setValue('p_{A}')
      expect(w.find('[data-testid="variable-mapping-duplicates"]').text())
        .toContain('parameters/p_mod_A, mod_A/y')
      await w.find('[data-testid="variable-mapping-reset-mod_A/y"]').trigger('click')
      expect(w.find('[data-testid="variable-mapping-duplicates"]').exists()).toBe(false)
    })

  it('saves every row\'s symbol and reports where', async () => {
    saveVariableMapping.mockResolvedValue({
      path: '/out/model_variable_mapping.csv', exists: true,
      rows: ROWS.map((r) => (r.variable_name === 'mod_A/y' ? { ...r, latex: '\\psi' } : r)),
      problems: { empty: [], duplicates: {} },
    })
    const w = await openDialog()
    await w.find('[data-testid="variable-mapping-input-mod_A/y"]').setValue('\\psi')
    expect(w.text()).toContain('1 changed')
    await w.find('[data-testid="variable-mapping-save"]').trigger('click')
    await flushPromises()
    expect(saveVariableMapping).toHaveBeenCalledWith('m1', [
      { variable_name: 'environment/time', latex: 't' },
      { variable_name: 'parameters/p_mod_A', latex: 'p_{A}' },
      { variable_name: 'mod_A/y', latex: '\\psi' },
    ], '/out')
    expect(w.emitted('saved')).toHaveLength(1)
    expect(w.text()).toContain('Saved in')
    expect(w.text()).toContain('0 changed')
  })

  it('shows CA\'s refusal', async () => {
    getVariableMapping.mockRejectedValueOnce({
      response: { data: { detail: 'only CellML models can be mapped' } } })
    const w = await openDialog()
    expect(w.find('[data-testid="variable-mapping-error"]').text())
      .toContain('only CellML models can be mapped')
  })
})
