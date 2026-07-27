import { watch, unref } from 'vue'
import { notify, runNotification, isTerminalRunState } from '../lib/notify'

/**
 * Fire a browser notification when a long run leaves 'running' for a terminal
 * state (issue #105).
 *
 * The stores stay pure — this only watches their `state` refs, so no
 * notification logic leaks into the polling internals.
 *
 * @param {Array<{kind: string, state: import('vue').Ref<string>, detail?: () => object}>} runs
 *        one entry per job; `kind` keys lib/notify's RUN_LABELS, `detail()` is
 *        read at fire time (e.g. calibration's final cost).
 * @param {import('vue').Ref<boolean>} enabled  the Settings toggle (default OFF).
 */
export function useRunNotifications(runs, enabled) {
  for (const { kind, state, detail } of runs) {
    watch(state, (next, prev) => {
      // Only the running -> terminal edge; not idle -> done on a restored result,
      // and not repeated writes of the same terminal value.
      if (prev !== 'running' || !isTerminalRunState(next)) return
      const msg = runNotification(kind, next, detail ? detail() : {})
      if (msg) notify(msg.title, msg.body, { enabled: !!unref(enabled) })
    })
  }
}
