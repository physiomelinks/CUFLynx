import { describe, it, expect } from 'vitest'
import { reactive } from 'vue'
import { mount } from '@vue/test-utils'
import ProtocolInfoEditor from './ProtocolInfoEditor.vue'
import { emptyModel } from '../lib/protocolInfo'

const ButtonStub = {
  props: ['label', 'icon', 'disabled', 'size', 'text', 'rounded', 'severity'],
  emits: ['click'],
  template:
    '<button v-bind="$attrs" :disabled="disabled" @click="$emit(\'click\')">{{ label }}</button>',
}
const PlotStub = { name: 'ParamInputPlot', props: ['series', 'preTime', 'totalSim', 'boundaries', 'title'], template: '<div class="plot-stub" />' }
const stubs = { Button: ButtonStub, ParamInputPlot: PlotStub }

function mountEditor(model) {
  return mount(ProtocolInfoEditor, {
    props: { model, allNames: ['a/x', 'a/y'], activeExp: 0 },
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
})
