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

// Saved runs, ticked to overlay them on the plots (issue #126). The tick box
// carries that run's trace colour, so the list doubles as the legend, and each
// shown run marks where it had every parameter.
describe('ControlPanel saved runs (#126)', () => {
  const RUNS = [
    { prefix: 'run_a', shown: false, color: '', params: { 'm/p0': 2.5 } },
    { prefix: 'run_b', shown: true, color: '#70ad47', params: { 'm/p0': 7.5 } },
  ]

  const mountWith = (savedRuns, sliders = sliderState(1)) =>
    mount(ControlPanel, { props: { sliders, savedRuns }, global: { stubs } })

  it('shows nothing when nothing has been saved', () => {
    expect(mountWith([]).find('[data-testid="saved-runs"]').exists()).toBe(false)
  })

  it('lists one tick box per saved run, ticked when shown', () => {
    const boxes = mountWith(RUNS).findAll('[data-testid="saved-run-check"]')
    expect(boxes).toHaveLength(2)
    expect(boxes[0].element.checked).toBe(false)
    expect(boxes[1].element.checked).toBe(true)
  })

  it('emits toggle-saved with the prefix when a box is clicked', async () => {
    const wrapper = mountWith(RUNS)
    await wrapper.findAll('[data-testid="saved-run-check"]')[0].setValue(true)
    expect(wrapper.emitted('toggle-saved')[0]).toEqual(['run_a'])
  })

  it('colours a shown box with that run trace colour', () => {
    const boxes = mountWith(RUNS).findAll('[data-testid="saved-run-check"]')
    expect(boxes[1].attributes('style')).toContain('#70ad47')
    // An unshown run has no trace, so no colour to borrow.
    expect(boxes[0].attributes('style') ?? '').not.toContain('#')
  })

  it('reveals the file prefix on hover', () => {
    const rows = mountWith(RUNS).findAll('[data-testid="saved-run"]')
    expect(rows[0].attributes('title')).toBe('run_a')
  })

  describe('slider markers', () => {
    it('marks only the shown runs, in their colour', () => {
      const marks = mountWith(RUNS).findAll('[data-testid="saved-marker"]')
      expect(marks).toHaveLength(1)
      // jsdom normalises the hex to rgb() in the style attribute.
      expect(marks[0].attributes('style')).toContain('rgb(112, 173, 71)')
      expect(marks[0].text()).toBe('×')
    })

    it('places the mark where the handle would sit for that value', () => {
      // 7.5 on a [0, 10] linear slider -> 75%.
      const mark = mountWith(RUNS).find('[data-testid="saved-marker"]')
      expect(mark.attributes('style')).toContain('75%')
    })

    it('uses the log mapping on a log slider, not a linear one', () => {
      const sliders = {
        'm/p0': { qname: 'm/p0', min: 1, max: 1000, value: 10, log: true, name_for_plotting: 'p' },
      }
      const runs = [{ prefix: 'r', shown: true, color: '#000', params: { 'm/p0': 10 } }]
      // log: 10 is one third of the way from 1 to 1000; linear would be ~1%.
      const style = mountWith(runs, sliders).find('[data-testid="saved-marker"]').attributes('style')
      expect(style).toMatch(/left: 33\./)
    })

    it('names the run and value on hover', () => {
      const mark = mountWith(RUNS).find('[data-testid="saved-marker"]')
      expect(mark.attributes('title')).toBe('run_b: 7.5')
    })

    it('flags a saved value that lies outside the current range', () => {
      const runs = [{ prefix: 'r', shown: true, color: '#000', params: { 'm/p0': 99 } }]
      const mark = mountWith(runs).find('[data-testid="saved-marker"]')
      expect(mark.classes()).toContain('out-of-range')
      expect(mark.attributes('title')).toContain('outside the current range')
      // Pinned to the end it lies beyond rather than drawn off-track.
      expect(mark.attributes('style')).toContain('100%')
    })

    it('skips a parameter the run has no value for', () => {
      const runs = [{ prefix: 'r', shown: true, color: '#000', params: {} }]
      expect(mountWith(runs).findAll('[data-testid="saved-marker"]')).toHaveLength(0)
    })

    // It records where a run *was*; dragging it would imply it could be edited.
    it('does not let the marker intercept drags aimed at the handle', () => {
      const marker = mountWith(RUNS).find('[data-testid="saved-marker"]')
      expect(marker.attributes('aria-hidden')).toBe('true')
      expect(marker.classes()).toContain('saved-marker')
    })
  })
})

// The best fit rides in the same list but is derived, not saved (#126).
describe('ControlPanel virtual (best fit) run', () => {
  const RUNS = [
    {
      prefix: 'best fit',
      title: 'Latest calibration best fit',
      virtual: true,
      shown: true,
      color: '#7f7f7f',
      params: { 'm/p0': 4 },
    },
    { prefix: 'run_a', shown: false, color: '', params: { 'm/p0': 2 } },
  ]

  const mountWith = () =>
    mount(ControlPanel, {
      props: { sliders: sliderState(1), savedRuns: RUNS },
      global: { stubs },
    })

  it('is flagged live, since ticking it runs the model', () => {
    const rows = mountWith().findAll('[data-testid="saved-run"]')
    expect(rows[0].find('[data-testid="saved-run-tag"]').text()).toBe('live')
    expect(rows[1].find('[data-testid="saved-run-tag"]').exists()).toBe(false)
  })

  it('explains itself on hover rather than showing a bare prefix', () => {
    const rows = mountWith().findAll('[data-testid="saved-run"]')
    expect(rows[0].attributes('title')).toBe('Latest calibration best fit')
  })

  it('marks the sliders exactly as a saved run does', () => {
    const mark = mountWith().find('[data-testid="saved-marker"]')
    expect(mark.attributes('title')).toBe('best fit: 4')
    expect(mark.attributes('style')).toContain('40%')
  })

  it('toggles through the same event', async () => {
    const wrapper = mountWith()
    await wrapper.findAll('[data-testid="saved-run-check"]')[0].setValue(false)
    expect(wrapper.emitted('toggle-saved')[0]).toEqual(['best fit'])
  })
})
