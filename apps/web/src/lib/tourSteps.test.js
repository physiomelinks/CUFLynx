import { describe, it, expect, afterEach } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

import { TOUR_STEPS, present, gone } from './tourSteps'

const SIDES = ['top', 'right', 'bottom', 'left']
const TESTID_SELECTOR = /^\[data-testid="[a-z0-9-]+"\]$/

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

/** Every `.vue` file under `src/`, read once. */
function vueSources() {
  const out = []
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) walk(full)
      else if (entry.name.endsWith('.vue')) out.push({ file: path.relative(SRC, full), text: fs.readFileSync(full, 'utf8') })
    }
  }
  walk(SRC)
  return out
}

const idOf = (selector) => selector.replace(/^\[data-testid="/, '').replace(/"\]$/, '')

/**
 * A testid is "declared" when it appears literally in a template, or when a
 * template literal builds it from a prefix -- `start-example-3compartment` is
 * written as `` :data-testid="`start-example-${ex.name}`" ``, so the literal
 * string never appears anywhere. Anything shorter than a whole `-`-separated
 * prefix is not accepted, so this cannot degenerate into "some substring matches".
 */
function declaredIn(testid, sources) {
  const hit = sources.find((s) => s.text.includes(`"${testid}"`))
  if (hit) return hit.file
  const parts = testid.split('-')
  for (let i = 1; i < parts.length; i += 1) {
    const prefix = `${parts.slice(0, i).join('-')}-\${`
    const dyn = sources.find((s) => s.text.includes(prefix))
    if (dyn) return dyn.file
  }
  return null
}

describe('tourSteps', () => {
  it('ships exactly 41 steps', () => {
    // A bare number, on purpose: an accidental deletion during an unrelated
    // edit is otherwise invisible -- the tour just gets shorter.
    expect(TOUR_STEPS.length).toBe(41)
  })

  it('gives every step a unique, non-empty id', () => {
    const ids = TOUR_STEPS.map((s) => s.id)
    for (const id of ids) expect(typeof id === 'string' && id.length > 0).toBe(true)
    expect(new Set(ids).size).toBe(ids.length)
  })

  it('anchors every step on a data-testid selector and nothing else', () => {
    // Class and tag selectors are what makes a hand-rolled tour rot: they are
    // not asserted anywhere else, so they break silently.
    for (const step of TOUR_STEPS) {
      expect(step.target, `step ${step.id}`).toMatch(TESTID_SELECTOR)
      if (step.advanceOn && step.advanceOn.target) {
        expect(step.advanceOn.target, `step ${step.id} advanceOn`).toMatch(TESTID_SELECTOR)
      }
    }
  })

  it('gives every step text and one of the four sides', () => {
    for (const step of TOUR_STEPS) {
      expect(typeof step.text === 'string' && step.text.trim().length > 0, `step ${step.id}`).toBe(true)
      expect(SIDES, `step ${step.id}`).toContain(step.side)
    }
  })

  it('points the Settings steps at the right of their control', () => {
    // The controls sit at the right-hand edge of the dialog's rows, so a bubble
    // on their left covers the labels the step is naming. `side` is only a
    // preference -- TourOverlay falls back to the opposite side, then bottom,
    // then top, when the asked-for one will not fit -- so this pins the ask,
    // not the outcome on any particular window width.
    for (const id of ['ca-dir', 'model-format', 'solver']) {
      expect(TOUR_STEPS.find((s) => s.id === id).side, `step ${id}`).toBe('right')
    }
  })

  it('declares predicates as functions when it declares them at all', () => {
    for (const step of TOUR_STEPS) {
      if ('when' in step) expect(typeof step.when, `step ${step.id} when`).toBe('function')
      if ('waitFor' in step) expect(typeof step.waitFor, `step ${step.id} waitFor`).toBe('function')
      if ('advanceOn' in step) {
        expect(typeof step.advanceOn.event, `step ${step.id} advanceOn.event`).toBe('string')
      }
      if ('onNext' in step) expect(typeof step.onNext, `step ${step.id} onNext`).toBe('function')
      if ('spanAll' in step) {
        expect(step.spanAll, `step ${step.id} spanAll`).toMatch(/^\[data-testid="[a-z0-9-]+"\]$/)
      }
      if ('bullets' in step) {
        expect(Array.isArray(step.bullets), `step ${step.id} bullets`).toBe(true)
        expect(step.bullets.length, `step ${step.id} bullets`).toBeGreaterThan(1)
        for (const b of step.bullets) expect(typeof b, `step ${step.id} bullet`).toBe('string')
      }
      if ('link' in step) {
        // https only, and never a bare href with no words on it: the bubble
        // renders the label, and an unlabelled URL in a sentence reads as noise.
        expect(step.link.href, `step ${step.id} link.href`).toMatch(/^https:\/\//)
        expect(step.link.label, `step ${step.id} link.label`).toBeTruthy()
      }
    }
  })

  // `onNext` is the only place a step may write to the app, so the set that
  // holds that permission is pinned rather than left to grow quietly. Both
  // members are the same case: a step describing a modal, where Next would
  // otherwise walk the tour on to something the mask is covering. A third entry
  // should have to be argued for here before it is added.
  it('lets exactly the two dialog steps act on Next', () => {
    const acting = TOUR_STEPS.filter((s) => 'onNext' in s).map((s) => s.id)
    expect(acting).toEqual(['settings-close', 'op-funcs-save'])
  })

  it('closes Settings from the close-Settings step', () => {
    const step = TOUR_STEPS.find((s) => s.id === 'settings-close')
    let closed = 0
    step.onNext({ closeSettings: () => (closed += 1) })
    expect(closed).toBe(1)
    // And the step still finishes on its own if the user closes Settings the
    // ordinary way, so Next is an alternative rather than the only exit.
    expect(step.waitFor({ settingsOpen: () => false })).toBe(true)
    expect(step.waitFor({ settingsOpen: () => true })).toBe(false)
  })

  it('closes the operation-funcs editor from its own step', () => {
    const step = TOUR_STEPS.find((s) => s.id === 'op-funcs-save')
    const closed = []
    step.onNext({ closeDialog: (sel) => closed.push(sel) })
    expect(closed).toEqual(['[data-testid="edit-op-funcs"]'])
  })

  it('sends the user-func step to the outputs directory', () => {
    // The funcs are written to <outputs>/user_funcs/ (apps/api/user_funcs.py),
    // and the copy used to read as though they landed inside circulatory_autogen
    // -- which would be someone else's repo, and would not travel with the study.
    // Naming the path is what makes that unambiguous; saying where it does *not*
    // go was answering a question nobody had asked.
    const step = TOUR_STEPS.find((s) => s.id === 'op-funcs-save')
    expect(step.text).toContain('outputs directory')
    expect(step.text).toContain('user_funcs/operation_funcs_user.py')
    expect(step.text).not.toContain('circulatory_autogen')
  })

  // The house style for these bubbles, pinned because it is the thing that
  // decays first: a step read on a small screen is a paragraph the user skims.
  it('keeps every bubble short enough to read', () => {
    for (const step of TOUR_STEPS) {
      expect(step.text.length, `step ${step.id} text`).toBeLessThan(420)
      for (const b of step.bullets ?? []) {
        expect(b.length, `step ${step.id} bullet`).toBeLessThan(140)
      }
    }
  })

  // A right-arrow in prose is the tell this copy was drafted by a machine, and
  // the places that wanted one wanted a list instead.
  it('uses no arrow glyphs in the copy', () => {
    for (const step of TOUR_STEPS) {
      const all = [step.text, step.title ?? '', ...(step.bullets ?? [])].join(' ')
      expect(all, `step ${step.id}`).not.toMatch(/[\u2190-\u21FF\u27F0-\u27FF]/)
    }
  })

  // The guard that matters. The tour points at testids from a separate file, so
  // renaming one (say `eo-value`) breaks a step in the app and *nothing else* --
  // no compile error, no other test. This is that error.
  it('points every anchor at a testid that still exists in a .vue file', () => {
    const sources = vueSources()
    expect(sources.length).toBeGreaterThan(0)
    const missing = []
    for (const step of TOUR_STEPS) {
      for (const selector of [step.target, step.advanceOn && step.advanceOn.target].filter(Boolean)) {
        const testid = idOf(selector)
        if (!declaredIn(testid, sources)) missing.push(`data-testid="${testid}" (tour step '${step.id}')`)
      }
    }
    expect(
      missing,
      `tour anchors no longer in any .vue under src/:\n  ${missing.join('\n  ')}\n` +
        'Either restore the testid or update the step in src/lib/tourSteps.js.',
    ).toEqual([])
  })
})

describe('present / gone', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('answer against the live document', () => {
    const sel = '[data-testid="tour-fixture"]'
    expect(present(sel)).toBe(false)
    expect(gone(sel)).toBe(true)

    const el = document.createElement('div')
    el.setAttribute('data-testid', 'tour-fixture')
    document.body.appendChild(el)
    expect(present(sel)).toBe(true)
    expect(gone(sel)).toBe(false)

    // Dialog anchors are portalled onto document.body, outside #app -- these
    // must query `document`, never an app root.
    el.remove()
    expect(present(sel)).toBe(false)
    expect(gone(sel)).toBe(true)
  })
})
