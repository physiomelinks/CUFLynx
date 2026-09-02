/**
 * How the app is allowed to leave the page — a source-level contract.
 *
 * This is a source test rather than a DOM test on purpose: **no DOM-level runner
 * can observe the thing that breaks it.** The app ships inside pywebview, and
 * pywebview's macOS backend forwards a new-window request only when the
 * navigation type is `WKNavigationTypeLinkActivated` — a genuine link click:
 *
 *     cocoa.py  if action.navigationType() == WKNavigationTypeLinkActivated: ...
 *               return nil          # everything else is dropped, silently
 *
 * A scripted `window.open()` is `WKNavigationTypeOther`, so it never reaches the
 * browser. That is issue #340: "Send to PhLynx" reported success and did
 * nothing, in the packaged Mac app only. Linux happened to work because the GTK
 * backend keys off the `_blank` frame name instead (`gtk.py`), and running from
 * source worked because that is a real browser. jsdom, happy-dom and even
 * Playwright's WebKit all implement popups properly, so none of them can catch
 * this — only the source can.
 *
 * The same reasoning covers blob downloads: pywebview's macOS download path
 * cancels the navigation and re-fetches the URL with `NSURLSession`, which has
 * never heard of `blob:`. A server URL works; an object URL cannot.
 */
import { describe, it, expect } from 'vitest'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const SRC = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')

/** Every `.vue` file under `src/`, read once. (Same walker as tourSteps.test.js.) */
function vueSources() {
  const out = []
  const walk = (dir) => {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) walk(full)
      else if (entry.name.endsWith('.vue'))
        out.push({ file: path.relative(SRC, full), text: fs.readFileSync(full, 'utf8') })
    }
  }
  walk(SRC)
  return out
}

// Deliberately empty. If a scripted open is ever genuinely unavoidable, add the
// file here *with the reason* — do not delete the assertion. An allowlist entry
// is a decision someone made; a deleted test is one nobody made.
const SCRIPTED_OPEN_ALLOWED = []
const OBJECT_URL_ALLOWED = []

describe('how the app leaves the page', () => {
  it('no component scripts window.open', () => {
    const offenders = vueSources()
      .filter(({ file }) => !SCRIPTED_OPEN_ALLOWED.includes(file))
      .filter(({ text }) => /\bwindow\.open\s*\(/.test(text))
      .map(({ file }) => file)

    expect(
      offenders,
      'window.open is silently dropped by pywebview on macOS (#340) — render an ' +
        '<a target="_blank" rel="noopener"> the user clicks instead',
    ).toEqual([])
  })

  it('no component builds a blob URL to download with', () => {
    const offenders = vueSources()
      .filter(({ file }) => !OBJECT_URL_ALLOWED.includes(file))
      .filter(({ text }) => /createObjectURL\s*\(/.test(text))
      .map(({ file }) => file)

    expect(
      offenders,
      "a blob: URL cannot be downloaded in the packaged app — pywebview's macOS " +
        'path re-fetches with NSURLSession, which cannot read blob:. Serve the ' +
        'bytes from the API and link to that URL instead',
    ).toEqual([])
  })

  it('every target="_blank" carries rel="noopener"', () => {
    // Generalises what StartDialog/EmulatorPanel/TourOverlay/App already do, so
    // the convention holds for links added later rather than only where someone
    // remembered.
    const offenders = []
    for (const { file, text } of vueSources()) {
      // Each element that opens a new tab, taken whole so rel= can be anywhere
      // in the tag regardless of attribute order.
      for (const tag of text.match(/<a\b[^>]*target="_blank"[^>]*>/g) || []) {
        if (!/\brel="[^"]*noopener/.test(tag)) offenders.push(`${file}: ${tag.slice(0, 80)}`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('the PhLynx send offers both links as real anchors', () => {
    // The positive half: #340 is only fixed if these exist and are anchors.
    const { text } = vueSources().find(({ file }) => file.endsWith('FileImport.vue'))
    for (const id of ['phlynx-open-link', 'phlynx-download-link']) {
      const anchor = new RegExp(`<a\\b[^<]*data-testid="${id}"`)
      expect(anchor.test(text), `${id} must be declared on an <a> element`).toBe(true)
    }
  })
})
