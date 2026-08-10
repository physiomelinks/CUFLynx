import { ref } from 'vue'

/**
 * Seeds sliders from a parsed params_for_id response. Each entry -- one CSV row,
 * so one parameter -- becomes one slider keyed by its qname; initial value uses
 * the model default when present, otherwise the range midpoint.
 *
 * A row naming several vessels is a single parameter present in several
 * components (#193), so it gets a single slider that writes to all of them.
 *
 * @param {object} slidersStore - a useSliders() instance.
 */
export function useParamsForId(slidersStore) {
  const filename = ref(null)
  const importedKeys = ref([])
  // qname -> { min, max, name_for_plotting, qnames, primary } for calibration
  // write-back of any param that no longer has a slider. Every *member* qname is
  // indexed, not just the row's representative: CA's best fit names each member
  // separately, and without the `primary` back-pointer a grouped row's other
  // members would each be handed their own new slider -- re-creating the very
  // split this parameter exists to avoid.
  const paramSpecs = ref({})

  function importParams(params, name = null) {
    clear()
    filename.value = name
    for (const p of params) {
      const initial =
        p.initial_value != null ? p.initial_value : (p.min + p.max) / 2
      const qnames = p.qnames?.length ? p.qnames : [p.qname]
      const isModifier = !!(p.modifies?.length)
      slidersStore.addSlider(p.qname, {
        min: p.min,
        max: p.max,
        // For a modifier this is the operation's identity θ (the backend sets
        // initial_value to it), so a fresh slider leaves every target at its
        // baseline.
        value: initial,
        name_for_plotting: p.name_for_plotting ?? p.name ?? p.qname,
        qnames,
        warning: p.warning ?? null,
        kind: isModifier ? 'modifier' : 'free',
        operation: p.operation ?? null,
        baselines: p.baselines ?? null,
      })
      importedKeys.value.push(p.qname)
      const spec = {
        min: p.min,
        max: p.max,
        name_for_plotting: p.name_for_plotting ?? p.name ?? p.qname,
        qnames,
        primary: p.qname,
        kind: isModifier ? 'modifier' : 'free',
      }
      for (const qname of qnames) paramSpecs.value[qname] = spec
    }
    return importedKeys.value.length
  }

  function clear() {
    for (const key of importedKeys.value) slidersStore.removeSlider(key)
    importedKeys.value = []
    paramSpecs.value = {}
    filename.value = null
  }

  return { filename, importedKeys, paramSpecs, importParams, clear }
}
