import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'

import ImagePanel from './ImagePanel.vue'

// Workstream D: an external python model's `extra_plots()` figures are rendered
// to PNG server-side and shown as output plot cells. ImagePanel is the first
// image renderer in a Chart.js-only app, so what it owes the user is the same
// chrome as a chart plus an honest broken state.

const URL = '/api/models/m1/solver_plots/tok1/0.png'

describe('ImagePanel', () => {
  it('renders the title and the image', () => {
    const wrapper = mount(ImagePanel, { props: { title: 'Temperature field', url: URL } })
    expect(wrapper.find('[data-testid="image-title"]').text()).toBe('Temperature field')
    const img = wrapper.find('[data-testid="image-img"]')
    expect(img.attributes('src')).toBe(URL)
    // Alt text, so the figure is named even when it cannot be drawn.
    expect(img.attributes('alt')).toBe('Temperature field')
  })

  it('offers maximize only when maximizable, and reports the toggle', async () => {
    const plain = mount(ImagePanel, { props: { title: 'f', url: URL } })
    expect(plain.find('[data-testid="image-maximize"]').exists()).toBe(false)

    const wrapper = mount(ImagePanel, {
      props: { title: 'f', url: URL, maximizable: true },
    })
    const btn = wrapper.find('[data-testid="image-maximize"]')
    expect(btn.attributes('aria-pressed')).toBe('false')
    await btn.trigger('click')
    expect(wrapper.emitted('toggle-maximize')).toHaveLength(1)
  })

  // Same expanded state as PlotPanel's, so a maximized figure fills the window
  // the same way a maximized chart does.
  it('takes the maximized class and offers to restore', () => {
    const wrapper = mount(ImagePanel, {
      props: { title: 'f', url: URL, maximizable: true, maximized: true },
    })
    expect(wrapper.find('[data-testid="image-panel"]').classes()).toContain('maximized')
    const btn = wrapper.find('[data-testid="image-maximize"]')
    expect(btn.attributes('aria-pressed')).toBe('true')
    expect(btn.attributes('title')).toBe('Restore plot')
  })

  // A run completed and the figure is on the server, so a grey browser
  // placeholder is the wrong answer: name it and offer the one action.
  it('shows a broken-image state with a retry', async () => {
    const wrapper = mount(ImagePanel, { props: { title: 'Temperature field', url: URL } })
    await wrapper.find('[data-testid="image-img"]').trigger('error')
    const broken = wrapper.find('[data-testid="image-broken"]')
    expect(broken.exists()).toBe(true)
    expect(broken.text()).toContain('Temperature field')

    await wrapper.find('[data-testid="image-retry"]').trigger('click')
    expect(wrapper.find('[data-testid="image-broken"]').exists()).toBe(false)
    // Retry re-requests the same url: the token is the run's, not a cache-buster.
    expect(wrapper.find('[data-testid="image-img"]').attributes('src')).toBe(URL)
  })

  // Each run gets a new url (the token changes), so a figure that failed once
  // must not keep the next run's from being drawn.
  it('gives a new run’s figure its own chance to load', async () => {
    const wrapper = mount(ImagePanel, { props: { title: 'f', url: URL } })
    await wrapper.find('[data-testid="image-img"]').trigger('error')
    expect(wrapper.vm.failed).toBe(true)

    await wrapper.setProps({ url: '/api/models/m1/solver_plots/tok2/0.png' })
    expect(wrapper.vm.failed).toBe(false)
    expect(wrapper.find('[data-testid="image-broken"]').exists()).toBe(false)
  })
})
