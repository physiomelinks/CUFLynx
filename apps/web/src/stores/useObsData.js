import { ref, computed } from 'vue'
import { derivePlotVariables } from '../lib/plot'

/** A protocol's times are user-authored JSON; anything unreadable counts as 0. */
function num(v) {
  const n = Number(v)
  return Number.isFinite(n) ? n : 0
}

/**
 * Holds the uploaded obs_data summary/content.
 *
 * The `protocol_info` it carries is the **single source of the run window**: the
 * top bar has no t1/pre spinners any more, because one quantity with two owners
 * is how a calibration and the live cost came to run over different windows and
 * disagree about the same parameters. Everything that needs the window reads it
 * from here.
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

  /** The protocol document itself, or null for a data-only (or absent) obs_data. */
  const protocolInfo = computed(() => obsData.value?.protocol_info ?? null)

  /**
   * The run window, read off the protocol and from nowhere else.
   *
   * `protocolPreTime` is the total warm-up (sum of `pre_times`) and
   * `protocolSimTime` the total simulated duration (every sub-experiment of
   * every experiment in `sim_times`). Both are null when there is no protocol
   * document — "there is no run window", which the caller has to answer for
   * rather than being handed a number that means nothing.
   */
  const protocolPreTime = computed(() => {
    const pi = protocolInfo.value
    if (!pi) return null
    return (pi.pre_times ?? []).reduce((acc, p) => acc + num(p), 0)
  })

  const protocolSimTime = computed(() => {
    const pi = protocolInfo.value
    if (!pi) return null
    return (pi.sim_times ?? []).reduce(
      (acc, subs) =>
        acc + (Array.isArray(subs) ? subs.reduce((a, d) => a + num(d), 0) : num(subs)),
      0,
    )
  })

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
    protocolInfo,
    protocolPreTime,
    protocolSimTime,
    experimentCount,
    experimentLabels,
    dataItems,
    predictionItems,
    plotVariables,
  }
}
