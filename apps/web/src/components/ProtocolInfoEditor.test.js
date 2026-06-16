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

  it('adds a controlled param and switches a cell shape', async () => {
    const model = reactive(emptyModel())
    const wrapper = mountEditor(model)

    await wrapper.find('[data-testid="param-select"]').setValue('a/x')
    await wrapper.find('[data-testid="add-param"]').trigger('click')
    expect(model.params['a/x']).toBeTruthy()
    expect(model.params['a/x'][0][0]).toEqual({ shape: 'constant', value: 0 })

    await wrapper.find('[data-testid="cell-shape"]').setValue('ramp')
    expect(model.params['a/x'][0][0].shape).toBe('ramp')
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
    await wrapper.find('[data-testid="param-select"]').setValue('a/x')
    await wrapper.find('[data-testid="add-param"]').trigger('click')
    expect(wrapper.findAllComponents(PlotStub)).toHaveLength(1)
    expect(wrapper.findAllComponents(PlotStub)[0].props('title')).toBe('a/x')
  })

  it('edits a subexp duration through the timeline header', async () => {
    const model = reactive(emptyModel())
    const wrapper = mountEditor(model)
    await wrapper.find('[data-testid="subexp-dur"]').setValue('7')
    expect(model.experiments[0].subexps[0].duration).toBe(7)
  })

  it('lightly highlights the subexp given by highlightSubexp', async () => {
    const model = reactive(emptyModel())
    addSubexp(model, 0) // now 2 subexps
    // add a controlled param so the value-row .tt-seg cells render too
    const wrapper = mountEditor(model, { highlightSubexp: 1 })
    await wrapper.find('[data-testid="param-select"]').setValue('a/x')
    await wrapper.find('[data-testid="add-param"]').trigger('click')

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
