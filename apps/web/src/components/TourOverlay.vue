<script setup>
/**
 * The engine for the guided tour: a bubble with an arrow pointing at a real
 * control, plus a highlight ring around it.
 *
 * It **observes**; it does not drive the app. There is no `action` or `prep`
 * field that runs on entering a step, so "wait for the user to do the thing"
 * stays structural rather than a convention someone can forget. A step advances
 * when the user really clicks the control (`advanceOn`), when app state says so
 * (`waitFor`), or when they press Next.
 *
 * A step that declares either of the first two **does not offer Next** (see
 * `showNext`): it is waiting on an action the step after it describes the
 * result of, so a Next there is a way to walk past the subject of the tour and
 * land on a control that is not on screen. Back and Skip are always offered.
 *
 * The **one** narrowing of that rule is `onNext`, which fires only from the
 * Next button and never from `advanceOn`/`waitFor`. It exists for the steps
 * whose whole subject is a modal the user is standing behind: pressing Next on
 * "close Settings to carry on" has to actually close Settings, or the tour
 * walks on to a control the mask is covering. Next is the user acting, so this
 * is still their click doing the thing -- it is not the tour moving on its own.
 * Keep it to that: a step that uses `onNext` to *skip work the user should do*
 * is the failure mode this design exists to prevent.
 *
 * Step shape (the step list itself lives elsewhere -- this file only consumes it):
 *   { id, target: '[data-testid="…"]', title?, text, side,   // 'top'|'bottom'|'left'|'right'
 *     advanceOn?: { target, event },   // delegated listener; target defaults to step.target
 *     waitFor?:  (ctx) => boolean,     // polled on the tick; true => advance
 *     when?:     (ctx) => boolean,     // false => step is skipped
 *     onNext?:   (ctx) => void }       // Next button only; see above
 *
 * `ctx` is a plain object of *getters* over app state, plus the few writers the
 * `onNext` steps need. Read it; write only through those.
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

const props = defineProps({
  steps: { type: Array, default: () => [] },
  // v-model:step -- App owns the index, this only ever asks for it to move.
  step: { type: Number, default: 0 },
  ctx: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:step', 'close'])

// The step we actually landed on: { index, step }. Nothing renders until this
// resolves, which is what stops a fully-skipped run flashing on screen.
const current = ref(null)
// The target's viewport rect, as measured. Fixed coordinates, see below.
const box = ref({ top: 0, left: 0, width: 0, height: 0 })
// Dim the rest of the page? Suppressed inside a PrimeVue dialog, whose own
// modal mask already dims -- doubling it makes the dialog read as disabled.
const dim = ref(true)
const bubbleEl = ref(null)
const pos = ref({ side: 'bottom', top: 0, left: 0, caret: 0 })

const MARGIN = 8 // keep the bubble this far inside the viewport
const GAP = 10 // between target edge and bubble
const CARET_INSET = 14 // how close the caret may get to a bubble corner
const CARET = 10 // caret square, px
const TICK_MS = 200
const GRACE_MS = 350

let alive = true
let timer = null
let frame = 0
let misses = 0 // consecutive ticks on which the target was unavailable
let observer = null
let advanceHandler = null
let advanceType = ''

/* ------------------------------------------------------------------ *
 * Availability
 * ------------------------------------------------------------------ */

// Never cache element handles: PrimeVue destroys and rebuilds dialog rows on
// every open, so a handle kept from one step points at a detached node by the
// next. Always re-query by selector.
const find = (s) => (s && s.target ? document.querySelector(s.target) : null)

const whenOk = (s) => !!s && (typeof s.when !== 'function' || s.when(props.ctx) !== false)

function domOk(s) {
  const el = find(s)
  if (!el) return false
  // getClientRects() is empty, or the rect is 0x0: this is the inactive
  // `v-show` pane case -- a hidden tab's controls are in the DOM with no boxes.
  // Testing for boxes means the tour needs *no* separate tab bookkeeping.
  if (!el.getClientRects().length) return false
  const r = el.getBoundingClientRect()
  if (!r.width && !r.height) return false
  if (typeof getComputedStyle === 'function' && getComputedStyle(el).visibility === 'hidden') {
    return false
  }
  // Explicitly NOT reasons to skip:
  //  - being scrolled off-screen: that is handled with scrollIntoView below,
  //    not by pretending the control does not exist;
  //  - offsetParent === null: always null for `position: fixed`, i.e. for
  //    every target inside a PrimeVue dialog -- it would skip the whole tour.
  return true
}

const available = (s) => whenOk(s) && domOk(s)

/* ------------------------------------------------------------------ *
 * Resolution
 * ------------------------------------------------------------------ */

/**
 * Walk from `from` in direction `dir` until a step is available, and assign
 * `current` *once* -- a run of eight skips is one synchronous loop in a single
 * frame, not eight renders and not eight emits.
 *
 * Only the *first* candidate gets a <=350 ms grace period: a dialog opened by
 * the very click that advanced the tour is not mounted yet, and the ctx read
 * by a capture-phase handler is a frame stale. Later candidates are judged
 * synchronously, so a long skip run does not cost 8 x 350 ms.
 */
function resolve(from, dir = 1) {
  cancelFrame()
  walk(from, dir, true)
}

function walk(from, dir, allowGrace) {
  if (!alive) return
  let i = from
  while (i >= 0 && i < props.steps.length) {
    if (available(props.steps[i])) {
      land(i)
      return
    }
    if (allowGrace && i === from) {
      grace(i, dir)
      return
    }
    i += dir
  }
  // Off the end: forward finishes the tour, backward stays where it was.
  if (dir > 0) {
    current.value = null
    teardownStep()
    emit('close', 'finish')
  }
}

function grace(i, dir) {
  if (typeof requestAnimationFrame !== 'function') {
    walk(i + dir, dir, false)
    return
  }
  const started = Date.now()
  const again = () => {
    if (!alive) return
    if (available(props.steps[i])) {
      land(i)
      return
    }
    if (Date.now() - started >= GRACE_MS) {
      walk(i + dir, dir, false)
      return
    }
    frame = requestAnimationFrame(again)
  }
  frame = requestAnimationFrame(again)
}

function land(i) {
  misses = 0
  current.value = { index: i, step: props.steps[i] }
  bindAdvance()
  observe()
  reposition()
  scrollIntoViewIfNeeded()
  // Once per resolution, with the index actually landed on -- and only when it
  // differs, so mounting on the index App already holds is not an echo.
  if (i !== props.step) emit('update:step', i)
}

function next() {
  if (current.value) resolve(current.value.index + 1, 1)
}

/**
 * Next, pressed by the user. Separate from `next()` because `onNext` must fire
 * from the button and *only* from the button -- `advanceOn` and `waitFor` mean
 * the user already did the thing themselves, and running it again would undo
 * their action (close a dialog they just reopened, say).
 *
 * The mutation lands before `resolve`, but Vue applies it asynchronously, so
 * the next step is judged against a DOM that has not caught up yet. That is
 * what the first candidate's requestAnimationFrame grace period is for: it
 * re-checks availability each frame until the app has settled.
 */
function nextFromButton() {
  const s = current.value && current.value.step
  if (s && typeof s.onNext === 'function') s.onNext(props.ctx)
  next()
}

function back() {
  if (current.value) resolve(current.value.index - 1, -1)
}

/**
 * Whether to offer Next at all.
 *
 * A step that declares `advanceOn` or `waitFor` is waiting for the user to *do*
 * something -- click Create, open the tab, close the dialog -- and the step
 * after it usually describes what that action produces. Offering Next there
 * offers a way to walk straight past the thing the tour is about, and the next
 * bubble then points at a control that is not on screen or is behind a modal
 * mask. It is also very easy to press by accident, since Next is where the
 * pointer already is.
 *
 * The exception is a step that can do the thing itself: with `onNext`, pressing
 * Next *is* the action, so it is offered.
 *
 * Back and Skip are never hidden -- this narrows how the tour goes forward, and
 * must not become a way to trap someone in it.
 */
const showNext = computed(() => {
  const s = current.value && current.value.step
  if (!s) return false
  if (typeof s.onNext === 'function') return true
  return !s.advanceOn && typeof s.waitFor !== 'function'
})

/* ------------------------------------------------------------------ *
 * Measurement and placement
 * ------------------------------------------------------------------ */

// Everything is drawn `position: fixed` from the target's own
// getBoundingClientRect(), teleported to <body> -- exactly SearchableSelect's
// pattern, and for its reason: `position: fixed` inside a transformed ancestor
// resolves against *that ancestor*, and PrimeVue portals every dialog to
// document.body, so targets like [data-testid="edit-obs"] live outside #app.
//
// ProtocolInfoEditor's `editorBounds()` ancestor walk is deliberately NOT
// copied here: teleported to <body> there is no clipping ancestor to fit
// inside, only the viewport. Don't add it back.
function reposition() {
  const s = current.value && current.value.step
  const el = find(s)
  if (!el) return
  const r = el.getBoundingClientRect()
  box.value = { top: r.top, left: r.left, width: r.width, height: r.height }
  // PrimeVue's modal mask already dims; keep the ring, drop the second dim.
  dim.value = !(el.closest && el.closest('.p-dialog'))
  // Place with whatever the bubble measures now (zero on the very first
  // render, since v-if has not produced it yet), then refine on the next
  // frame once it has its real size -- the clear/measure/shift dance from
  // ProtocolInfoEditor.fitEditor.
  place(r)
  if (typeof requestAnimationFrame === 'function') {
    requestAnimationFrame(() => {
      if (!alive || !current.value) return
      const again = find(current.value.step)
      if (again) place(again.getBoundingClientRect())
    })
  }
}

function place(rect) {
  const b = bubbleEl.value ? bubbleEl.value.getBoundingClientRect() : null
  pos.value = compute(rect, b ? b.width : 0, b ? b.height : 0)
}

const OPPOSITE = { top: 'bottom', bottom: 'top', left: 'right', right: 'left' }
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), Math.max(lo, hi))

function compute(rect, bw, bh) {
  const vw = window.innerWidth
  const vh = window.innerHeight
  const fits = (side) => {
    if (side === 'top') return rect.top - GAP - bh >= MARGIN
    if (side === 'bottom') return rect.bottom + GAP + bh <= vh - MARGIN
    if (side === 'left') return rect.left - GAP - bw >= MARGIN
    return rect.right + GAP + bw <= vw - MARGIN
  }
  const asked = current.value ? current.value.step.side : 'bottom'
  const pref = OPPOSITE[asked] ? asked : 'bottom'
  // Preferred side, then its opposite, then bottom, then top.
  const side = [pref, OPPOSITE[pref], 'bottom', 'top'].find(fits) || pref

  let top
  let left
  if (side === 'top' || side === 'bottom') {
    top = side === 'top' ? rect.top - GAP - bh : rect.bottom + GAP
    left = rect.left + rect.width / 2 - bw / 2
  } else {
    left = side === 'left' ? rect.left - GAP - bw : rect.right + GAP
    top = rect.top + rect.height / 2 - bh / 2
  }
  left = clamp(left, MARGIN, vw - MARGIN - bw)
  top = clamp(top, MARGIN, vh - MARGIN - bh)

  // The caret is pinned to the target's centre along the shared edge and
  // clamped away from the bubble's corners, so a bubble that had to be clamped
  // to the viewport still visibly points at the thing.
  const caret =
    side === 'top' || side === 'bottom'
      ? clamp(rect.left + rect.width / 2 - left, CARET_INSET, bw - CARET_INSET)
      : clamp(rect.top + rect.height / 2 - top, CARET_INSET, bh - CARET_INSET)
  return { side, top, left, caret }
}

function scrollIntoViewIfNeeded() {
  const el = find(current.value && current.value.step)
  if (!el || typeof el.scrollIntoView !== 'function') return
  const r = el.getBoundingClientRect()
  const off = r.bottom < 0 || r.top > window.innerHeight || r.right < 0 || r.left > window.innerWidth
  // Scrolled out of view is *not* a reason to skip a step -- bring it back.
  if (off) el.scrollIntoView({ block: 'center', inline: 'center' })
}

/* ------------------------------------------------------------------ *
 * Staying attached
 * ------------------------------------------------------------------ */

function onReflow() {
  if (current.value) reposition()
}

function onTick() {
  const c = current.value
  if (!c) return
  if (available(c.step)) {
    misses = 0
    reposition()
  } else {
    misses += 1
    // Two consecutive misses, not one: a dialog being swapped is momentarily
    // absent between teardown and rebuild.
    if (misses >= 2) {
      resolve(c.index + 1, 1)
      return
    }
  }
  if (typeof c.step.waitFor === 'function' && c.step.waitFor(props.ctx)) next()
}

function observe() {
  if (observer) observer.disconnect()
  observer = null
  if (typeof ResizeObserver === 'undefined') return
  const el = find(current.value && current.value.step)
  if (!el) return
  observer = new ResizeObserver(() => onReflow())
  observer.observe(el)
}

/* ------------------------------------------------------------------ *
 * Advancing on the user's real click
 * ------------------------------------------------------------------ */

function bindAdvance() {
  unbindAdvance()
  const s = current.value && current.value.step
  const spec = s && s.advanceOn
  if (!spec) return
  const selector = spec.target || s.target
  advanceType = spec.event || 'click'
  advanceHandler = (e) => {
    const t = e.target
    if (!t || typeof t.closest !== 'function') return
    // 1. The tour's own UI is checked FIRST: a broad selector (say
    //    `button`) would otherwise match the bubble's own Next button and
    //    advance twice, or advance on Skip.
    if (t.closest('.tour-bubble')) return
    if (!t.closest(selector)) return
    next()
  }
  // 2. Delegated on `document`, not bound to the element: dialog targets are
  //    destroyed and rebuilt, so a direct listener dies with the node.
  // 3. Capture phase: the obs_data dialog's row handlers call
  //    stopPropagation(), so a bubbling listener never hears the click.
  document.addEventListener(advanceType, advanceHandler, true)
}

function unbindAdvance() {
  if (!advanceHandler) return
  document.removeEventListener(advanceType, advanceHandler, true)
  advanceHandler = null
  advanceType = ''
}

/* ------------------------------------------------------------------ *
 * Lifecycle
 * ------------------------------------------------------------------ */

function cancelFrame() {
  if (frame && typeof cancelAnimationFrame === 'function') cancelAnimationFrame(frame)
  frame = 0
}

function teardownStep() {
  unbindAdvance()
  if (observer) observer.disconnect()
  observer = null
}

// No Escape handler, on purpose. Escape belongs to whichever PrimeVue dialog
// the user is standing in; stealing it would close the tour instead of the
// thing they meant to close. Skip is the exit.
onMounted(() => {
  // Capture phase, because inner scrollers do not bubble their scroll events.
  document.addEventListener('scroll', onReflow, true)
  window.addEventListener('resize', onReflow)
  timer = setInterval(onTick, TICK_MS)
  resolve(props.step || 0, 1)
})

onBeforeUnmount(() => {
  alive = false
  document.removeEventListener('scroll', onReflow, true)
  window.removeEventListener('resize', onReflow)
  if (timer) clearInterval(timer)
  timer = null
  cancelFrame()
  teardownStep()
})

// App owns the index; if it moves it somewhere we are not, follow.
watch(
  () => props.step,
  (v) => {
    if (!current.value || current.value.index !== v) resolve(v, 1)
  },
)
</script>

<template>
  <!-- One Teleport, two fixed nodes. Teleported because the anchors themselves
       are teleported (PrimeVue dialogs live on <body>). -->
  <Teleport to="body">
    <template v-if="current">
      <!--
        The highlight is an outline ring, not a four-div cut-out: the spread
        shadow *is* both the ring and the dim, so there is no geometry to
        recompute on scroll.

        `pointer-events: none` (in the stylesheet) is load-bearing: the tour
        advances on the user's real click, so the highlight must never
        intercept it.
      -->
      <div
        class="tour-ring"
        :class="{ 'tour-ring-nodim': !dim }"
        data-testid="tour-ring"
        :style="{
          top: `${box.top}px`,
          left: `${box.left}px`,
          width: `${box.width}px`,
          height: `${box.height}px`,
        }"
      />
      <div
        ref="bubbleEl"
        class="tour-bubble"
        data-testid="tour-bubble"
        role="dialog"
        :aria-label="current.step.title || 'Guided tour'"
        :data-side="pos.side"
        :style="{ top: `${pos.top}px`, left: `${pos.left}px` }"
      >
        <!-- Counter uses the RAW index: the denominator is the step list's
             length, so it does not jump about as `when` conditions flip
             underneath the user. -->
        <div class="tour-count" data-testid="tour-count">{{ current.index + 1 }} / {{ steps.length }}</div>
        <h3 v-if="current.step.title" class="tour-title">{{ current.step.title }}</h3>
        <p class="tour-text" data-testid="tour-text">{{ current.step.text }}</p>
        <div class="tour-actions">
          <button
            type="button"
            class="tour-btn"
            data-testid="tour-back"
            :disabled="current.index <= 0"
            @click="back"
          >
            Back
          </button>
          <!-- Absent, not disabled: a greyed Next reads as "the tour is stuck",
               when in fact it is the user's turn. See showNext. -->
          <button
            v-if="showNext"
            type="button"
            class="tour-btn tour-primary"
            data-testid="tour-next"
            @click="nextFromButton"
          >
            Next
          </button>
          <button
            type="button"
            class="tour-btn tour-quiet"
            data-testid="tour-skip"
            @click="emit('close', 'skip')"
          >
            Skip
          </button>
        </div>
        <span
          class="tour-caret"
          :class="`tour-caret-${pos.side}`"
          :style="
            pos.side === 'top' || pos.side === 'bottom'
              ? { left: `${pos.caret - CARET / 2}px` }
              : { top: `${pos.caret - CARET / 2}px` }
          "
        />
      </div>
    </template>
  </Teleport>
</template>

<!-- Teleported to <body>, so these styles are outside the component's scope and
     have to be global. Named with a `tour-` prefix for that reason. -->
<style>
.tour-ring {
  position: fixed;
  z-index: 4000;
  border-radius: 4px;
  /* The spread shadow is both the ring and the dim. */
  box-shadow:
    0 0 0 2px var(--p-primary-color, #3b82f6),
    0 0 0 9999px rgba(0, 0, 0, 0.3);
  /* Load-bearing: the user's click has to reach the control underneath. */
  pointer-events: none;
  transition: all 0.12s ease-out;
}
/* Inside a PrimeVue dialog: keep the ring, drop the dim -- the modal mask
   already dims, and doubling it makes the dialog read as disabled. */
.tour-ring-nodim {
  box-shadow: 0 0 0 2px var(--p-primary-color, #3b82f6);
}

.tour-bubble {
  position: fixed;
  /* Above PrimeVue's modal (1100) and above the 3000 a teleported
     SearchableSelect list uses, so a picker opened mid-step cannot cover the
     instruction. */
  z-index: 4001;
  box-sizing: border-box;
  width: max-content;
  max-width: 22rem;
  padding: 0.6rem 0.75rem;
  border: 1px solid var(--p-content-border-color, #ccc);
  border-radius: 6px;
  background: var(--p-content-background, #fff);
  color: var(--p-text-color, #222);
  box-shadow: 0 6px 22px rgba(0, 0, 0, 0.22);
  font-size: 0.85rem;
}
.tour-count {
  font-size: 0.72rem;
  opacity: 0.65;
}
.tour-title {
  margin: 0.1rem 0 0.25rem;
  font-size: 0.95rem;
  font-weight: 600;
}
.tour-text {
  margin: 0 0 0.55rem;
  line-height: 1.35;
  white-space: pre-line;
}
.tour-actions {
  display: flex;
  gap: 0.35rem;
  align-items: center;
}
.tour-btn {
  font: inherit;
  padding: 0.15rem 0.6rem;
  border: 1px solid var(--p-content-border-color, #ccc);
  border-radius: 3px;
  background: var(--p-content-background, #fff);
  color: inherit;
  cursor: pointer;
}
.tour-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.tour-primary {
  border-color: var(--p-primary-color, #3b82f6);
  background: var(--p-primary-color, #3b82f6);
  color: var(--p-primary-contrast-color, #fff);
}
.tour-quiet {
  margin-left: auto;
  border-color: transparent;
  opacity: 0.75;
}

/* A rotated square, on whichever edge of the bubble faces the target. */
.tour-caret {
  position: absolute;
  width: 10px;
  height: 10px;
  background: var(--p-content-background, #fff);
  border: 1px solid var(--p-content-border-color, #ccc);
  transform: rotate(45deg);
}
/* `side` is where the bubble sits relative to the target, so the caret is on
   the opposite edge of the bubble. */
.tour-caret-bottom {
  top: -6px;
  border-right: 0;
  border-bottom: 0;
}
.tour-caret-top {
  bottom: -6px;
  border-left: 0;
  border-top: 0;
}
.tour-caret-right {
  left: -6px;
  border-right: 0;
  border-top: 0;
}
.tour-caret-left {
  right: -6px;
  border-left: 0;
  border-bottom: 0;
}
</style>
