import { ref, computed } from 'vue'
import { derivePlotVariables } from '../lib/plot'

/**
 * Holds the uploaded obs_data summary/content. While obs_data is loaded the
 * protocol drives the run, so the manual t0/t1/N controls are hidden.
 */
export function useObsData() {
  const obsData = ref(null)

  function setObsData(data) {
    obsData.value = data
  }

  function clearObsData() {
    obsData.value = null
  }

  const hasObsData = computed(() => obsData.value !== null)

  /** Manual time controls are shown only when no obs_data is loaded. */
  const useManualTime = computed(() => obsData.value === null)

  const experimentCount = computed(() => {
    const d = obsData.value
    if (!d) return 0
    if (typeof d.n_experiments === 'number') return d.n_experiments
    return d.protocol_info?.sim_times?.length ?? 0
  })

  const experimentLabels = computed(() => obsData.value?.experiment_labels ?? [])

  const dataItems = computed(() => obsData.value?.data_items ?? [])
  const predictionItems = computed(() => obsData.value?.prediction_items ?? [])

  /** Variables to plot, one column per entry in the (experiment x variable) grid. */
  const plotVariables = computed(() => derivePlotVariables(obsData.value))

  return {
    obsData,
    setObsData,
    clearObsData,
    hasObsData,
    useManualTime,
    experimentCount,
    experimentLabels,
    dataItems,
    predictionItems,
    plotVariables,
  }
}
