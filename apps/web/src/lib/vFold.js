/**
 * `v-fold`: a section whose own heading (its first child h2 or h3) folds it away, so a long
 * page of results can be read a part at a time.
 *
 * The value is the section's default: open unless `false`. A click (or Enter / Space) on the
 * heading toggles it; the default is applied again only when the value changes (another
 * workflow step is selected), so a section the user opened or closed stays so meanwhile.
 *
 * State is the `data-folded` attribute, not a class: Vue rewrites a bound `class` on every
 * patch, which would undo a fold. The page's CSS hides a folded section's other children.
 */
function heading(el) {
  return [...el.children].find((c) => c.tagName === 'H2' || c.tagName === 'H3') ?? null
}

function setFolded(el, folded) {
  if (folded) el.dataset.folded = ''
  else delete el.dataset.folded
  heading(el)?.setAttribute('aria-expanded', folded ? 'false' : 'true')
}

export const vFold = {
  mounted(el, binding) {
    const head = heading(el)
    if (!head) return
    head.classList.add('fold-head')
    head.setAttribute('role', 'button')
    head.tabIndex = 0
    const toggle = () => setFolded(el, !('folded' in el.dataset))
    head.addEventListener('click', toggle)
    head.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault()
        toggle()
      }
    })
    setFolded(el, binding.value === false)
  },
  updated(el, binding) {
    if (binding.value !== binding.oldValue) setFolded(el, binding.value === false)
  },
}
