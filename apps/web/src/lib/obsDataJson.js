// Helpers for the "Edit obs_data" dialog that protocol-kit does not hold: the
// date-versioned name a saved copy gets, and the lanes a protocol must have.
// Reading, editing and checking an obs_data document is protocol-kit's.

import { addParameter, ensureProtocol } from '@physiomelinks/protocol-kit'

function yymmdd(date = new Date()) {
  const yy = String(date.getFullYear()).slice(-2)
  const mm = String(date.getMonth() + 1).padStart(2, '0')
  const dd = String(date.getDate()).padStart(2, '0')
  return `${yy}${mm}${dd}`
}

/**
 * `<stem>_<yymmdd>.json`. Stem = loaded obs filename minus `.json`, else
 * `<modelName>_obs_data`. The date suffix keeps the original from being
 * overwritten.
 */
export function versionedJsonName(loadedFilename, modelName, date = new Date()) {
  const stem = loadedFilename
    ? loadedFilename.replace(/\.json$/i, '')
    : `${modelName ?? 'model'}_obs_data`
  return `${stem}_${yymmdd(date)}.json`
}

/**
 * A copy of an obs_data document (null for none) with a protocol_info that sets every
 * parameter in `names`. One it does not set yet gets a lane with no value in every
 * sub-experiment, which protocol-kit reports until a number or an input is given: how a
 * workflow step's unset parameters are put in front of the user.
 */
export function withParameters(document, names = []) {
  let out = ensureProtocol(document)
  for (const name of names) out = addParameter(out, name, null)
  return out
}
