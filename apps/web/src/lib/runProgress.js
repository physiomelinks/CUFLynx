/**
 * Puts the progress files of a reopened working dir's runs (`load_outputs`' `run_progress`:
 * `{ sobol: { progress, design, convergence }, emulator, profile }`) into the stores the
 * Progress tab reads, so a reopened study shows how its runs went, as a live run does.
 *
 * A store whose own run is going keeps its live progress: its poll is newer than any file.
 */
export function applyReopenedProgress(runProgress, { sa, emu, ia }) {
  const found = runProgress ?? {}
  if (found.sobol?.progress && !sa.running.value) {
    sa.progress.value = found.sobol.progress
    sa.sobolProgress.value = { design: found.sobol.design, convergence: found.sobol.convergence }
  }
  if (found.emulator && !emu.running.value) emu.progress.value = found.emulator
  if (found.profile && !ia.running.value) ia.progress.value = found.profile
}
