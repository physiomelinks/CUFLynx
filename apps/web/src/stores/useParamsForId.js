import { ref } from 'vue'

/**
 * Seeds sliders from a parsed params_for_id response. Each entry becomes one
 * slider keyed by its qname; initial value uses the model default when present,
 * otherwise the range midpoint.
 *
 * @param {object} slidersStore - a useSliders() instance.
 */
export function useParamsForId(slidersStore) {
  const filename = ref(null)
  const importedKeys = ref([])

  function importParams(params, name = null) {
    clear()
    filename.value = name
    for (const p of params) {
      const initial =
        p.initial_value != null ? p.initial_value : (p.min + p.max) / 2
      slidersStore.addSlider(p.qname, {
        min: p.min,
        max: p.max,
        value: initial,
        name_for_plotting: p.name_for_plotting ?? p.qname,
      })
      importedKeys.value.push(p.qname)
    }
    return importedKeys.value.length
  }

  function clear() {
    for (const key of importedKeys.value) slidersStore.removeSlider(key)
    importedKeys.value = []
    filename.value = null
  }

  return { filename, importedKeys, importParams, clear }
}
