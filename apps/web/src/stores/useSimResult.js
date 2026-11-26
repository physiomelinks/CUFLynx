import { ref, shallowRef } from 'vue'

/** Stores the time series from the most recent simulate / protocol run. */
export function useSimResult() {
  // { time: number[], outputs: { qname: number[] } }
  const result = shallowRef(null)
  // protocol runs: [{ time, outputs }] per experiment
  const experiments = shallowRef([])
  // What the run's parameters cost against the loaded obs_data (#159), or null.
  // Its own ref because the two setters below are exclusive -- a protocol run
  // nulls `result` -- and the cost belongs to the run either way.
  const cost = shallowRef(null)
  const warnings = ref([])
  // Figures the *solver* drew for this run, rendered server-side to PNG:
  // [{index, title, url}]. An external python model's optional extra_plots()
  // returns matplotlib figures (e.g. a 2D FEM field) that no time series can
  // stand in for. The url carries a per-run token, so it is its own
  // cache-buster and nothing here has to version it.
  const solverPlots = ref([])
  const status = ref('idle') // idle | running | ok | error | cancelled
  const message = ref('')
  const lastRunMs = ref(null)
  // When the run in progress started (Date.now()), for the elapsed time beside
  // "Simulating..."; null when no run is in progress.
  const startedAt = ref(null)

  function setRunning() {
    status.value = 'running'
    message.value = ''
    warnings.value = []
    startedAt.value = Date.now()
  }

  // The user stopped the run: the last result stays on screen.
  function setCancelled(msg = '') {
    status.value = 'cancelled'
    message.value = msg
    startedAt.value = null
  }

  function setResult(data, elapsedMs = null) {
    result.value = data
    cost.value = data?.cost ?? null
    // Read off the payload rather than taken as an argument, like `cost` above:
    // a single run can now carry warnings too (the backend forwards CA's
    // stiffness check, and flags a partly-NaN trace, #175), and a caller that
    // forgot to pass them would silently drop the only explanation the user gets.
    warnings.value = data?.warnings ?? []
    // Read off the payload for the same reason, and reset when this run drew
    // none: a stale figure from the previous parameters is worse than no figure.
    solverPlots.value = data?.solver_plots ?? []
    experiments.value = []
    status.value = 'ok'
    lastRunMs.value = elapsedMs
    startedAt.value = null
  }

  function setExperiments(exps, warns = [], elapsedMs = null, runCost = null, plots = []) {
    experiments.value = exps ?? []
    cost.value = runCost
    result.value = null
    warnings.value = warns ?? []
    solverPlots.value = plots ?? []
    status.value = 'ok'
    lastRunMs.value = elapsedMs
    startedAt.value = null
  }

  // A new model (or a new obs_data, which regroups the plots) has nothing to do
  // with the last run's figures, and their URLs point at a token that run owns.
  function clearSolverPlots() {
    solverPlots.value = []
  }

  function setError(msg) {
    status.value = 'error'
    message.value = msg
    startedAt.value = null
  }

  return {
    result,
    experiments,
    warnings,
    solverPlots,
    clearSolverPlots,
    status,
    message,
    lastRunMs,
    startedAt,
    setRunning,
    setCancelled,
    setResult,
    setExperiments,
    setError,
    cost,
  }
}
