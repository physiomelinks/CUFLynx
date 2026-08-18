import { describe, it, expect } from 'vitest'
import { reactive } from 'vue'
import { mount } from '@vue/test-utils'
import ProtocolInfoEditor from './ProtocolInfoEditor.vue'
import { emptyModel, addSubexp } from '../lib/protocolInfo'

const ButtonStub = {
  props: ['label', 'icon', 'disabled', 'size', 'text', 'rounded', 'severity'],
  emits: ['click'],
  template:
    '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
}
const PlotStub = { name: 'ParamInputPlot', props: ['series', 'preTime', 'totalSim', 'boundaries', 'title'], template: '<div class="plot-stub" />' }
const stubs = { Button: ButtonStub, ParamInputPlot: PlotStub }

function mountEditor(model, extraProps = {}) {
  return mount(ProtocolInfoEditor, {
    props: { model, allNames: ['a/x', 'a/y'], activeExp: 0, ...extraProps },
    global: { stubs },
  })
}

describe('ProtocolInfoEditor', () => {
  it('adds and removes experiments and subexperiments', async () => {
    const model = reactive(emptyModel())
    const wrapper = mountEditor(model)

    await wrapper.find('[data-testid="add-exp"]').trigger('click')
    expect(model.experiments).toHaveLength(2)

    await wrapper.find('[data-testid="add-subexp"]').trigger('click')
    expect(model.experiments[0].subexps).toHaveLength(2)

    await wrapper.find('[data-testid="remove-subexp"]').trigger('click')
    expect(model.experiments[0].subexps).toHaveLength(1)
  })

  // The picker is a search box whose matches are listed as you type; clicking a
  // match adds it. (#147 follow-up: it used to be a search box beside a separate
  // dropdown, so you typed blind and then opened the select to see what matched.)
  const addParamByName = async (wrapper, name) => {
    await wrapper.find('[data-testid="param-search"]').setValue(name)
    const option = wrapper
      .findAll('[data-testid="param-option"]')
      .find((o) => o.text() === name)
    await option.trigger('mousedown')
  }

  it('adds a controlled param and switches a cell shape', async () => {
    const model = reactive(emptyModel())
    const wrapper = mountEditor(model)

    await addParamByName(wrapper, 'a/x')
    expect(model.params['a/x']).toBeTruthy()
    expect(model.params['a/x'][0][0]).toEqual({ shape: 'constant', value: 0 })

    await wrapper.find('[data-testid="cell-shape"]').setValue('ramp')
    expect(model.params['a/x'][0][0].shape).toBe('ramp')
  })

  it('lists the matches as you type, and one click adds', async () => {
    const model = reactive(emptyModel())
    const wrapper = mountEditor(model, { allNames: ['a/x', 'a/y', 'b/z'] })

    // Typing filters, case-insensitively, by qname substring — and the matches
    // are visible without opening anything.
    await wrapper.find('[data-testid="param-search"]').setValue('B/')
    const names = wrapper.findAll('[data-testid="param-option"]').map((o) => o.text())
    expect(names).toEqual(['b/z'])

    await wrapper.findAll('[data-testid="param-option"]')[0].trigger('mousedown')
    expect(model.params['b/z']).toBeTruthy()
  })

  it('says how many of the parameters match', async () => {
    const wrapper = mountEditor(reactive(emptyModel()), { allNames: ['a/x', 'a/y', 'b/z'] })
    expect(wrapper.find('[data-testid="param-count"]').text()).toBe('3 of 3')
    await wrapper.find('[data-testid="param-search"]').setValue('a/')
    expect(wrapper.find('[data-testid="param-count"]').text()).toBe('2 of 3')
  })

  it('says so when nothing matches, rather than showing an empty list', async () => {
    const wrapper = mountEditor(reactive(emptyModel()), { allNames: ['a/x'] })
    await wrapper.find('[data-testid="param-search"]').setValue('zzz')
    expect(wrapper.find('[data-testid="param-options"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="param-empty"]').text()).toContain('zzz')
  })

  it('adds the only match on Enter, without arrowing to it first', async () => {
    const model = reactive(emptyModel())
    const wrapper = mountEditor(model, { allNames: ['a/x', 'b/z'] })
    const search = wrapper.find('[data-testid="param-search"]')
    await search.setValue('b/')
    await search.trigger('keydown', { key: 'Enter' })
    expect(model.params['b/z']).toBeTruthy()
  })

  it('arrows through the matches and adds the highlighted one', async () => {
    const model = reactive(emptyModel())
    const wrapper = mountEditor(model, { allNames: ['a/x', 'a/y'] })
    const search = wrapper.find('[data-testid="param-search"]')
    await search.setValue('a/')
    await search.trigger('keydown', { key: 'ArrowDown' })
    await search.trigger('keydown', { key: 'ArrowDown' })
    await search.trigger('keydown', { key: 'Enter' })
    expect(model.params['a/y']).toBeTruthy()
  })

  // Leaving a stale query would hide the rest behind a filter for something that
  // is no longer a candidate.
  it('clears the search once a parameter is added', async () => {
    const model = reactive(emptyModel())
    const wrapper = mountEditor(model, { allNames: ['a/x', 'a/y'] })
    await addParamByName(wrapper, 'a/x')
    expect(wrapper.find('[data-testid="param-search"]').element.value).toBe('')
  })

  it('drops a parameter from the candidates once it is controlled', async () => {
    const model = reactive(emptyModel())
    const wrapper = mountEditor(model, { allNames: ['a/x', 'a/y'] })
    await addParamByName(wrapper, 'a/x')
    await wrapper.find('[data-testid="param-search"]').setValue('a/')
    const names = wrapper.findAll('[data-testid="param-option"]').map((o) => o.text())
    expect(names).toEqual(['a/y'])
  })

  it('seeds a newly added param with its uploaded value as the baseline', async () => {
    const model = reactive(emptyModel())
    const wrapper = mountEditor(model, { initialValues: { 'a/x': 1.5e-8 } })

    await addParamByName(wrapper, 'a/x')
    // baseline = the uploaded value, not 0.
    expect(model.params['a/x'][0][0]).toEqual({ shape: 'constant', value: 1.5e-8 })
    // and it shows in scientific notation in the value field.
    expect(wrapper.find('[data-testid="pc-value"]').element.value).toBe('1.5e-8')
  })

  it('renders one empty plot when there are no controlled params', () => {
    const wrapper = mountEditor(reactive(emptyModel()))
    const plots = wrapper.findAllComponents(PlotStub)
    expect(plots).toHaveLength(1)
    expect(plots[0].props('series')).toBe(null) // empty timeline plot
  })

  it('renders one plot per controlled param', async () => {
    const model = reactive(emptyModel())
    const wrapper = mountEditor(model)
    await addParamByName(wrapper, 'a/x')
    expect(wrapper.findAllComponents(PlotStub)).toHaveLength(1)
    expect(wrapper.findAllComponents(PlotStub)[0].props('title')).toBe('a/x')
  })

  it('edits a subexp duration through the timeline header', async () => {
    const model = reactive(emptyModel())
    const wrapper = mountEditor(model)
    await wrapper.find('[data-testid="subexp-dur"]').setValue('7')
    expect(model.experiments[0].subexps[0].duration).toBe(7)
  })

  it('does not let a backspaced duration collapse the subexp out of reach', async () => {
    // Emptying the field leaves the value null while the user is mid-edit -- which is what
    // lets "0.5" be typed without the leading "0" being fought over. But the strip used to
    // be drawn at flexGrow 0.001 for a null, collapsing it to nothing: the sub-experiment
    // disappeared, taking its own duration field with it, so there was no way to finish
    // typing the number or to get it back.
    const model = reactive(emptyModel())
    const wrapper = mountEditor(model)
    const input = wrapper.find('[data-testid="subexp-dur"]')

    await input.setValue('')
    expect(model.experiments[0].subexps[0].duration).toBe(null)

    const seg = wrapper.find('.tt-seg.dim')
    expect(Number(seg.element.style.flexGrow)).toBeGreaterThanOrEqual(1)
  })

  it('makes an emptied duration a real 1 second once the field is left', async () => {
    // toProtocolInfo writes num(duration, 0) into sim_times, so a field left empty would
    // persist a zero-length sub-experiment -- not a sub-experiment at all.
    const model = reactive(emptyModel())
    const wrapper = mountEditor(model)
    const input = wrapper.find('[data-testid="subexp-dur"]')

    await input.setValue('')
    await input.trigger('blur')

    expect(model.experiments[0].subexps[0].duration).toBe(1)
  })

  it('still lets a fractional duration be typed digit by digit', async () => {
    // The reason the value is not coerced on input: "0.5" passes through "0" on the way.
    const model = reactive(emptyModel())
    const wrapper = mountEditor(model)
    const input = wrapper.find('[data-testid="subexp-dur"]')

    await input.setValue('0.5')
    await input.trigger('blur')

    expect(model.experiments[0].subexps[0].duration).toBe(0.5)
  })

  it('lightly highlights the subexp given by highlightSubexp', async () => {
    const model = reactive(emptyModel())
    addSubexp(model, 0) // now 2 subexps
    // add a controlled param so the value-row .tt-seg cells render too
    const wrapper = mountEditor(model, { highlightSubexp: 1 })
    await addParamByName(wrapper, 'a/x')

    const highlighted = wrapper.findAll('.tt-seg.tt-highlight')
    // one in the duration header + one in the value row
    expect(highlighted.length).toBe(2)
    // none highlighted when the prop is null
    const none = mountEditor(reactive(emptyModel()), { highlightSubexp: null })
    expect(none.findAll('.tt-seg.tt-highlight')).toHaveLength(0)
  })

  it('only highlights when highlightExp matches the active experiment', async () => {
    const make = (highlightExp) => {
      const model = reactive(emptyModel())
      addSubexp(model, 0)
      return mountEditor(model, { highlightSubexp: 1, highlightExp })
    }
    // active experiment is 0: a highlight pinned to exp 1 must not show
    expect(make(1).findAll('.tt-seg.tt-highlight')).toHaveLength(0)
    // pinned to the active exp -> shows (the duration-header cell at least)
    expect(make(0).findAll('.tt-seg.tt-highlight').length).toBeGreaterThan(0)
  })
})
