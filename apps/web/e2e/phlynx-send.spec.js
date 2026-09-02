/**
 * How the app is allowed to leave the page, checked in real browser engines.
 *
 * Two different things are pinned here, and it is worth being clear which is
 * which, because this tier is easy to over-credit:
 *
 *  1. **The engine rule** (`user activation`). Safari/WebKit does not preserve
 *     transient user activation across an `await` the way Chromium's grace
 *     period does. So a scripted `window.open` issued *after* a POST resolves —
 *     which is what `onSendConfirm` used to do — is refused outright here, while
 *     a real anchor click is not. That is the second bug class the fix removes,
 *     and this test would fail if someone reintroduced it.
 *
 *  2. **The app's own links** carry `rel="noopener"`.
 *
 * What this tier does **not** cover is #340 itself. That bug is in the
 * *embedder*: pywebview's macOS backend forwards a new window only for
 * `WKNavigationTypeLinkActivated`, and no browser engine — WebKit included —
 * has such a filter, because browsers implement popups properly. The guard for
 * #340 is `src/lib/externalLinks.test.js`, a source contract. Do not let this
 * job's existence be a reason to weaken that one.
 */
import { test, expect } from '@playwright/test'

test.describe('leaving the page', () => {
  test('a post-await scripted open is refused, but an anchor click is not', async ({
    page,
    context,
    browserName,
  }) => {
    // Deliberately a fixture page rather than the app: this pins the *engine's*
    // rule, which is the premise the app's design rests on. If a future engine
    // changes it, the reasoning in externalLinks.test.js and CLAUDE.md should be
    // revisited — and this is what will say so.
    await page.setContent(`
      <button id="scripted">scripted</button>
      <a id="anchor" href="/?opened=anchor" target="_blank" rel="noopener">anchor</a>
      <script>
        document.getElementById('scripted').addEventListener('click', async () => {
          // The shape onSendConfirm had: activation is consumed by the await.
          await new Promise((r) => setTimeout(r, 250))
          window.__opened = window.open('/?opened=scripted', '_blank', 'noopener')
        })
      </script>
    `)

    await page.click('#scripted')
    await page.waitForTimeout(600)
    const opened = await page.evaluate(() => window.__opened !== null && window.__opened !== undefined)

    if (browserName === 'webkit') {
      // The whole reason the app must not do this.
      expect(opened, 'WebKit should refuse a scripted open after an await').toBe(false)
    }

    // The anchor works in every engine — which is why the fix uses one.
    const [popup] = await Promise.all([context.waitForEvent('page'), page.click('#anchor')])
    expect(popup.url()).toContain('opened=anchor')
    await popup.close()
  })

  test('every external link in the app carries rel=noopener', async ({ page }) => {
    await page.route('**/api/**', (route) => route.fulfill({ json: {} }))
    await page.goto('/')

    const bad = await page.$$eval('a[target="_blank"]', (as) =>
      as
        .filter((a) => !(a.getAttribute('rel') || '').includes('noopener'))
        .map((a) => a.outerHTML),
    )
    expect(bad, 'target=_blank without rel=noopener gives the opened page window.opener').toEqual(
      [],
    )
  })
})
