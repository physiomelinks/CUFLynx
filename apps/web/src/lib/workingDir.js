/**
 * The working dir: the one folder that holds every input and output of a study.
 *
 * Kept in localStorage under `cuflynx-working-dir`. A value stored under the
 * previous key, `cuflynx-outputs-dir`, is moved to the new key the first time it
 * is read. Storage can be missing or refuse access (private windows, previews), so
 * every access is guarded and falls back to "no working dir".
 */

export const WORKING_DIR_KEY = 'cuflynx-working-dir'
export const LEGACY_WORKING_DIR_KEY = 'cuflynx-outputs-dir'

/** Why a run is refused before it starts: there is nowhere to keep it. */
export const NO_WORKING_DIR_MESSAGE =
  'Set a working dir first: every input and output of a run is kept there.'

/** The stored working dir, or '' when none is set. Migrates the legacy key once. */
export function readStoredWorkingDir() {
  try {
    const current = localStorage.getItem(WORKING_DIR_KEY)
    const legacy = localStorage.getItem(LEGACY_WORKING_DIR_KEY)
    if (legacy !== null) {
      if (current === null) localStorage.setItem(WORKING_DIR_KEY, legacy)
      localStorage.removeItem(LEGACY_WORKING_DIR_KEY)
      return current ?? legacy
    }
    return current || ''
  } catch {
    return ''
  }
}

/** Store the working dir ('' clears it). */
export function storeWorkingDir(dir) {
  try {
    localStorage.setItem(WORKING_DIR_KEY, dir || '')
  } catch {
    // Storage unavailable: the value lives only for this page.
  }
}

/** The message to show when a run needs a working dir and none is set, else null. */
export function workingDirRefusal(dir) {
  return String(dir || '').trim() ? null : NO_WORKING_DIR_MESSAGE
}
