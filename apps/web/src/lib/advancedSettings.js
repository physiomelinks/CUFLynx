/**
 * Which settings each left-hand tab shows under "Advanced settings" (advancedSettings.json).
 *
 * The table lists the advanced ones; every other setting is standard. So an option
 * circulatory_autogen adds to a schema appears as a standard setting with no change here,
 * and moving it is one line in the JSON.
 *
 * An item is a CA option descriptor ({name, default, ...}) or one of this app's own fields
 * ({name, own: true}); the two are listed apart, as a tab's own field may share a name with
 * a CA option (the Sensitivity tab's local gradient source and CA's gradient_method).
 */
import TABLE from './advancedSettings.json'

export const ADVANCED = TABLE

/** Whether `item` is an advanced setting of `tab`. */
export function isAdvanced(tab, item) {
  const entry = TABLE[tab]
  if (!entry || !item?.name) return false
  return (item.own ? entry.cuflynx : entry.ca).includes(item.name)
}

/** `items` split into {standard, advanced}, each in its given order. */
export function splitSettings(tab, items) {
  const standard = []
  const advanced = []
  for (const item of items ?? []) (isAdvanced(tab, item) ? advanced : standard).push(item)
  return { standard, advanced }
}

/** Whether `value` differs from `def`: no value, an empty one and null are all "unset". */
export function differs(value, def) {
  const unset = (v) => v === undefined || v === null || v === ''
  if (unset(value) && unset(def)) return false
  if (typeof value === 'object' || typeof def === 'object') return JSON.stringify(value) !== JSON.stringify(def)
  return value !== def
}

/**
 * Whether a CA option applies to `method`: CA marks an option that only some methods read
 * with `methods` (as it marks pyMC-only ones with `libraries`). An unmarked option applies
 * to `unmarked` (the method the panel's form has always shown them for), or to every
 * method when `unmarked` is null.
 */
export function appliesToMethod(opt, method, unmarked = null) {
  if (Array.isArray(opt?.methods)) return opt.methods.includes(method)
  return unmarked == null || method === unmarked
}

/** The default an option has now: its DEBUG default when DEBUG is on and it has one. */
export function currentDefault(opt, debug = false) {
  return debug && opt?.debug_default != null ? opt.debug_default : opt?.default
}
