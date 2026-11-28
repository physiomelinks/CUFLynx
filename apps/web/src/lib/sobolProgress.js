import { ref } from 'vue'

/**
 * Follows a Sobol run's design and convergence (`GET .../sobol-progress`) while it runs.
 *
 * Slower than the status poll: the design is up to a couple of thousand rows, and the pairs
 * plot only needs to keep up with failures as they appear. `finish(jobId)` stops following
 * and reads once more, since CA writes the convergence as the run's last step.
 *
 * `fetch(jobId)` returns `{ design, convergence }`; `data` holds the last answer.
 */
export function useSobolFollow(fetch, options = {}) {
  const intervalMs = options.sobolIntervalMs ?? 3000
  const data = ref(null)
  let timer = null
  // The job whose replies are shown, and the one still read on a timer.
  let following = null
  let live = null

  async function read(jobId) {
    try {
      const found = await fetch(jobId)
      if (following === jobId) data.value = found
    } catch {
      /* the next read, or the status poll, says what went wrong */
    }
  }

  function stop() {
    if (timer) clearTimeout(timer)
    timer = null
    live = null
  }

  function follow(jobId) {
    stop()
    following = jobId
    live = jobId
    const tick = async () => {
      await read(jobId)
      if (live === jobId) timer = setTimeout(tick, intervalMs)
    }
    tick()
  }

  async function finish(jobId) {
    stop()
    await read(jobId)
  }

  function reset() {
    stop()
    following = null
    data.value = null
  }

  return { data, follow, finish, stop, reset }
}
