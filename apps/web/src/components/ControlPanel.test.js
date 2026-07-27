import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ControlPanel from './ControlPanel.vue'

const stubs = { Slider: true, Button: true }

function sliderState(n) {
  const out = {}
  for (let i = 0; i < n; i++) {
    out[`m/p${i}`] = {
      qname: `m/p${i}`,
      min: 0,
      max: 10,
      value: 5,
      log: false,
      name_for_plotting: `m/p${i}`,
    }
  }
  return out
}

describe('ControlPanel', () => {
  it('test_renders_slider_for_each_active_param', () => {
    const wrapper = mount(ControlPanel, {
      props: { sliders: sliderState(3) },
      global: { stubs },
    })
    expect(wrapper.findAll('[data-testid="slider-row"]').length).toBe(3)
  })

  it('test_slider_change_emits_update', async () => {
    const wrapper = mount(ControlPanel, {
      props: { sliders: sliderState(1) },
      global: { stubs },
    })
    await wrapper.find('[data-testid="value-input"]').setValue('7')
    const events = wrapper.emitted('update')
    expect(events).toBeTruthy()
    expect(events[0][0]).toEqual({ qname: 'm/p0', value: 7 })
  })

  it('test_reset_init_emits', async () => {
    const wrapper = mount(ControlPanel, {
      props: { sliders: sliderState(1) },
      global: { stubs: { ...stubs, Button: false } },
    })
    await wrapper.find('[data-testid="reset-init"]').trigger('click')
    expect(wrapper.emitted('reset-init')).toBeTruthy()
  })

  it('test_reset_best_gated_on_hasBestFit', async () => {
    const wrapper = mount(ControlPanel, {
      props: { sliders: sliderState(1), hasBestFit: false },
      global: { stubs: { ...stubs, Button: false } },
    })
    // No best-fit yet -> disabled.
    expect(
      wrapper.find('[data-testid="reset-best"]').attributes('disabled'),
    ).toBeDefined()

    await wrapper.setProps({ hasBestFit: true })
    expect(
      wrapper.find('[data-testid="reset-best"]').attributes('disabled'),
    ).toBeUndefined()
    await wrapper.find('[data-testid="reset-best"]').trigger('click')
    expect(wrapper.emitted('reset-best')).toBeTruthy()
  })

  it('test_save_current_emits (issue #106)', async () => {
    const wrapper = mount(ControlPanel, {
      props: { sliders: sliderState(1) },
      global: { stubs: { ...stubs, Button: false } },
    })
    await wrapper.find('[data-testid="save-current"]').trigger('click')
    expect(wrapper.emitted('save-current')).toBeTruthy()
  })

  it('test_reset_saved_emits (opens the file browser in App)', async () => {
    const wrapper = mount(ControlPanel, {
      props: { sliders: sliderState(1) },
      global: { stubs: { ...stubs, Button: false } },
    })
    await wrapper.find('[data-testid="reset-saved"]').trigger('click')
    expect(wrapper.emitted('reset-saved')).toBeTruthy()
  })

  it('test_save_and_reset_saved_disabled_without_sliders', () => {
    const wrapper = mount(ControlPanel, {
      props: { sliders: {} },
      global: { stubs: { ...stubs, Button: false } },
    })
    expect(
      wrapper.find('[data-testid="save-current"]').attributes('disabled'),
    ).toBeDefined()
    expect(
      wrapper.find('[data-testid="reset-saved"]').attributes('disabled'),
    ).toBeDefined()
  })

  it('test_commands_are_below_the_parameters_two_per_row', () => {
    // Layout (#106): commands live in a grid container after the slider rows, not
    // in the header; Export/Import are gone.
    const wrapper = mount(ControlPanel, {
      props: { sliders: sliderState(2) },
      global: { stubs: { ...stubs, Button: false } },
    })
    const cmds = wrapper.find('[data-testid="param-commands"]')
    expect(cmds.exists()).toBe(true)
    // Four commands, two per row.
    for (const id of ['reset-init', 'reset-best', 'save-current', 'reset-saved']) {
      expect(cmds.find(`[data-testid="${id}"]`).exists()).toBe(true)
    }
    // Removed affordances.
    expect(wrapper.find('[data-testid="import-csv"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="export-snapshot"]').exists()).toBe(false)
    // The command grid comes after the last slider row in document order.
    const rows = wrapper.findAll('[data-testid="slider-row"]')
    const lastRow = rows[rows.length - 1].element
    expect(lastRow.compareDocumentPosition(cmds.element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
  })
})
