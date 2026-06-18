import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

vi.mock('../lib/api', () => ({ listDir: vi.fn(), makeDir: vi.fn() }))

import FileBrowserDialog from './FileBrowserDialog.vue'
import { listDir, makeDir } from '../lib/api'

// Render the PrimeVue Dialog inline (no teleport) and Button as a real button.
const ButtonStub = {
  props: ['disabled', 'label', 'icon', 'size', 'text', 'title'],
  emits: ['click'],
  template:
    '<button :disabled="disabled" v-bind="$attrs" @click="$emit(\'click\')">{{ label }}</button>',
}
const DialogStub = {
  props: ['visible'],
  template: '<div v-if="visible"><slot /><slot name="footer" /></div>',
}
const InputTextStub = {
  props: ['modelValue'],
  emits: ['update:modelValue'],
  template:
    '<input :value="modelValue" v-bind="$attrs" @input="$emit(\'update:modelValue\', $event.target.value)" />',
}
const stubs = { Dialog: DialogStub, Button: ButtonStub, Checkbox: true, InputText: InputTextStub }

beforeEach(() => {
  listDir.mockReset()
  makeDir.mockReset()
})

describe('FileBrowserDialog', () => {
  it('lists the home dir on open and navigates into folders', async () => {
    listDir
      .mockResolvedValueOnce({
        path: '/home/u',
        parent: '/home',
        entries: [{ name: 'proj', path: '/home/u/proj', is_dir: true }],
      })
      .mockResolvedValueOnce({
        path: '/home/u/proj',
        parent: '/home/u',
        entries: [{ name: 'run.py', path: '/home/u/proj/run.py', is_dir: false }],
      })
    const wrapper = mount(FileBrowserDialog, {
      props: { visible: true, mode: 'file' },
      global: { stubs },
    })
    await flushPromises()
    expect(listDir).toHaveBeenCalledWith(null, false) // file mode -> dirs_only false
    expect(wrapper.text()).toContain('proj')

    await wrapper.find('.fb-list li').trigger('click') // folder -> navigate
    await flushPromises()
    expect(listDir).toHaveBeenLastCalledWith('/home/u/proj', false)
    expect(wrapper.text()).toContain('run.py')
  })

  it('emits the selected file path and closes', async () => {
    listDir.mockResolvedValue({
      path: '/home/u',
      parent: '/home',
      entries: [{ name: 'run.py', path: '/home/u/run.py', is_dir: false }],
    })
    const wrapper = mount(FileBrowserDialog, {
      props: { visible: true, mode: 'file' },
      global: { stubs },
    })
    await flushPromises()
    await wrapper.find('.fb-list li').trigger('click') // select the file
    await wrapper.find('[data-testid="fb-confirm"]').trigger('click')
    expect(wrapper.emitted('select')[0]).toEqual(['/home/u/run.py'])
    expect(wrapper.emitted('update:visible').at(-1)).toEqual([false])
  })

  it('hides dotfiles/dot-dirs by default', async () => {
    listDir.mockResolvedValue({
      path: '/home/u',
      parent: '/home',
      entries: [
        { name: '.ssh', path: '/home/u/.ssh', is_dir: true },
        { name: 'proj', path: '/home/u/proj', is_dir: true },
        { name: '.bashrc', path: '/home/u/.bashrc', is_dir: false },
      ],
    })
    const wrapper = mount(FileBrowserDialog, {
      props: { visible: true, mode: 'file' },
      global: { stubs },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('proj')
    expect(wrapper.text()).not.toContain('.ssh')
    expect(wrapper.text()).not.toContain('.bashrc')
    expect(wrapper.findAll('.fb-list li')).toHaveLength(1)
  })

  it('in dir mode selects the current folder and requests dirs only', async () => {
    listDir.mockResolvedValue({ path: '/data/out', parent: '/data', entries: [] })
    const wrapper = mount(FileBrowserDialog, {
      props: { visible: true, mode: 'dir' },
      global: { stubs },
    })
    await flushPromises()
    expect(listDir).toHaveBeenCalledWith(null, true) // dir mode -> dirs_only true
    await wrapper.find('[data-testid="fb-confirm"]').trigger('click')
    expect(wrapper.emitted('select')[0]).toEqual(['/data/out'])
  })

  it('creates a new folder and steps into it', async () => {
    listDir
      .mockResolvedValueOnce({ path: '/data', parent: '/', entries: [] })
      .mockResolvedValueOnce({ path: '/data/runs', parent: '/data', entries: [] })
    makeDir.mockResolvedValue({ path: '/data/runs' })
    const wrapper = mount(FileBrowserDialog, {
      props: { visible: true, mode: 'dir' },
      global: { stubs },
    })
    await flushPromises()

    await wrapper.find('[data-testid="fb-new-folder"]').trigger('click')
    const input = wrapper.find('[data-testid="fb-new-folder-name"]')
    expect(input.exists()).toBe(true)
    await input.setValue('runs')
    await wrapper.find('[data-testid="fb-new-folder-create"]').trigger('click')
    await flushPromises()

    expect(makeDir).toHaveBeenCalledWith('/data', 'runs')
    expect(listDir).toHaveBeenLastCalledWith('/data/runs', true) // stepped into it
    // selecting now returns the freshly created folder
    await wrapper.find('[data-testid="fb-confirm"]').trigger('click')
    expect(wrapper.emitted('select')[0]).toEqual(['/data/runs'])
  })
})
