import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import ControlPanel from './ControlPanel.vue'

const stubs = { Slider: true, ToggleButton: true, Button: true }

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
})
