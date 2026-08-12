import { computed, ref } from 'vue'
import {
  cancelEmulatorTraining,
  getEmulatorInfo,
  getEmulatorStatus,
  startEmulatorTraining,
} from '../lib/api'

/**
 * Drives emulator training and holds the trained emulator's metadata (CA #333).
 *
 * Shaped like useSensitivity — start, poll, streamed log — with one difference
 * that matters: a trained emulator **outlives the job**. It is a file in the
 * outputs directory, so the panel loads whatever is already there on open
 * (`refresh`) rather than showing nothing until the user trains one in this
 * session. That is also what makes an emulator trained by circulatory_autogen's
 * own script usable from the GUI.
 *
 * `useEmulator` (the tick box) is held here rather than in the panel because it
 * governs the *other* tabs: sensitivity, calibration and UQ read it when they
 * build their run settings, and the parameter sliders read it to decide whether
 * to draw the emulator's prediction beside the model's.
 */
export function useEmulator(options = {}) {
  const intervalMs = options.intervalMs ?? 1000
  const state = ref('idle') // idle | running | done | error | cancelled
  const lines = ref([])
  const error = ref('')

  /** CA's emulator_metadata.json for the trained emulator, or null. */
  const metadata = ref(null)
  /**
   * CA's held-out points for it: {theta, y_true, y_pred, residual, labels}.
   * Null on an emulator trained before circulatory_autogen saved them -- which
   * is why the Analysis view distinguishes "no emulator" from "no points".
   */
  const errorPoints = ref(null)
  const emulatorDir = ref('')
  /** The tick box: evaluate the emulator in SA / calibration / UQ. */
  const useEmulator = ref(false)

  let jobId = null
  let offset = 0
  let timer = null

  /** True once there is an emulator to use. */
  const trained = computed(() => metadata.value != null)
  const running = computed(() => state.value === 'running')

  /** The worst held-out R2 across features — the number that decides trust. */
  const worstR2 = computed(() => metadata.value?.worst_r2 ?? null)

  /** Per-feature rows for the panel's table. */
  const features = computed(() => {
    const meta = metadata.value
    if (!meta) return []
    return (meta.feature_labels ?? []).map((label, i) => ({
      label,
      r2: meta.feature_r2?.[i] ?? null,
      rmse: meta.feature_rmse?.[i] ?? null,
    }))
  })

  /**
   * The emulator can only be *used* when one has been trained. Ticking the box
   * with nothing trained would put every downstream run into a state CA refuses,
   * so the box unticks itself rather than the run failing later.
   */
  const canUse = computed(() => trained.value)

  async function refresh(modelId, configOutputsDir = '') {
    if (!modelId) return
    try {
      const info = await getEmulatorInfo(modelId, configOutputsDir)
      emulatorDir.value = info.emulator_dir ?? ''
      metadata.value = info.metadata ?? null
      errorPoints.value = info.error_points ?? null
      if (!metadata.value) useEmulator.value = false
    } catch (e) {
      // Not an error state for the panel: no emulator is the normal start.
      metadata.value = null
    }
  }

  function reset() {
    if (timer) clearTimeout(timer)
    timer = null
    jobId = null
    offset = 0
    state.value = 'idle'
    lines.value = []
    error.value = ''
  }

  async function train(modelId, settings) {
    reset()
    state.value = 'running'
    try {
      const { job_id } = await startEmulatorTraining(modelId, settings)
      jobId = job_id
      await poll(modelId, settings?.config_outputs_dir ?? '')
    } catch (e) {
      state.value = 'error'
      error.value = e?.response?.data?.detail || String(e)
    }
  }

  async function poll(modelId, configOutputsDir) {
    if (!jobId) return
    try {
      const s = await getEmulatorStatus(jobId, offset)
      if (s.lines?.length) {
        lines.value = lines.value.concat(s.lines)
        offset = s.next_offset
      }
      state.value = s.state
      if (s.state === 'running') {
        timer = setTimeout(() => poll(modelId, configOutputsDir), intervalMs)
      } else {
        if (s.state === 'done' && s.metadata) {
          metadata.value = s.metadata
          emulatorDir.value = s.metadata.dir ?? emulatorDir.value
          // The points are not on the job status (they are a file, and a large
          // one); re-read them so the Analysis view has the new run's, not the
          // previous emulator's.
          await refresh(modelId, configOutputsDir)
        } else {
          // A failed training leaves whatever was there before: an older
          // emulator is still a real emulator, and silently forgetting it
          // would be worse than leaving it with its own (older) scores shown.
          await refresh(modelId, configOutputsDir)
        }
        error.value = s.error || ''
      }
    } catch (e) {
      state.value = 'error'
      error.value = e?.response?.data?.detail || String(e)
    }
  }

  async function cancel() {
    if (timer) clearTimeout(timer)
    timer = null
    if (jobId) {
      try {
        await cancelEmulatorTraining(jobId)
      } catch {
        /* best effort */
      }
      state.value = 'cancelled'
    }
  }

  return {
    state,
    lines,
    error,
    running,
    metadata,
    errorPoints,
    emulatorDir,
    features,
    worstR2,
    trained,
    canUse,
    useEmulator,
    refresh,
    train,
    cancel,
    reset,
  }
}
