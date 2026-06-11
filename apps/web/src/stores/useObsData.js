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

  /** True when the obs_data carries a protocol_info (drives the run). */
  const hasProtocol = computed(
    () =>
      obsData.value?.has_protocol === true ||
      obsData.value?.protocol_info != null,
  )

  /**
   * Manual t0/t1/N controls are shown unless a protocol drives the run. A
   * data-only obs_data file (bare array, e.g. 3compartment) has no protocol, so
   * it overlays its data_items but still runs with manual time.
   */
  const useManualTime = computed(() => !hasProtocol.value)

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
    hasProtocol,
    useManualTime,
    experimentCount,
    experimentLabels,
    dataItems,
    predictionItems,
    plotVariables,
  }
}
