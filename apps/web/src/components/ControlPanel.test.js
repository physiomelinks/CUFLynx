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

  it('test_import_csv_button_emits', async () => {
    const wrapper = mount(ControlPanel, {
      props: { sliders: {} },
      global: { stubs: { ...stubs, Button: false } },
    })
    await wrapper.find('[data-testid="import-csv"]').trigger('click')
    expect(wrapper.emitted('import-csv')).toBeTruthy()
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

  it('test_save_snapshot_emits', async () => {
    const wrapper = mount(ControlPanel, {
      props: { sliders: sliderState(1) },
      global: { stubs: { ...stubs, Button: false } },
    })
    await wrapper.find('[data-testid="save-snapshot"]').trigger('click')
    expect(wrapper.emitted('save-snapshot')).toBeTruthy()
  })

  it('test_reset_saved_and_export_gated_on_hasSaved', async () => {
    const wrapper = mount(ControlPanel, {
      props: { sliders: sliderState(1), hasSaved: false },
      global: { stubs: { ...stubs, Button: false } },
    })
    // No saved snapshot yet -> both gated buttons disabled.
    expect(
      wrapper.find('[data-testid="reset-saved"]').attributes('disabled'),
    ).toBeDefined()
    expect(
      wrapper.find('[data-testid="export-snapshot"]').attributes('disabled'),
    ).toBeDefined()

    await wrapper.setProps({ hasSaved: true })
    expect(
      wrapper.find('[data-testid="reset-saved"]').attributes('disabled'),
    ).toBeUndefined()
    await wrapper.find('[data-testid="reset-saved"]').trigger('click')
    expect(wrapper.emitted('reset-saved')).toBeTruthy()
    await wrapper.find('[data-testid="export-snapshot"]').trigger('click')
    expect(wrapper.emitted('export-snapshot')).toBeTruthy()
  })

  it('test_save_snapshot_disabled_without_sliders', () => {
    const wrapper = mount(ControlPanel, {
      props: { sliders: {} },
      global: { stubs: { ...stubs, Button: false } },
    })
    expect(
      wrapper.find('[data-testid="save-snapshot"]').attributes('disabled'),
    ).toBeDefined()
  })
})
