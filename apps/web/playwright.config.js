import { defineConfig, devices } from '@playwright/test'

/**
 * Browser-engine tests, run against the built app with the API stubbed.
 *
 * **What this tier is and is not for.** It exists to catch the *browser* half of
 * how the app leaves the page: WebKit enforces transient user activation across
 * an `await` far more strictly than Chromium, so a scripted open reintroduced
 * into the send path fails here.
 *
 * It would **not** have caught #340. That bug is in the *embedder*: pywebview's
 * macOS backend forwards a new window only for `WKNavigationTypeLinkActivated`,
 * and Playwright's WebKit — like every other DOM runner — implements popups
 * properly and has no such filter. The test that guards #340 is
 * `src/lib/externalLinks.test.js`, a source contract, and that is deliberate.
 * Do not let this job's existence be a reason to weaken that one.
 *
 * No Python here: `page.route` stubs `/api/**`, so the whole tier is the built
 * frontend and nothing else.
 */
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? 'github' : 'list',
  use: {
    baseURL: 'http://127.0.0.1:4173',
    trace: 'on-first-retry',
  },
  projects: [
    // WebKit first: it is the engine this tier exists for.
    { name: 'webkit', use: { ...devices['Desktop Safari'] } },
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
  ],
  webServer: {
    // `npx vite` rather than `yarn preview`: the flags reach vite directly, with
    // no package-manager argument forwarding in between, and --host pins the
    // interface so it matches `url` below. The first CI run timed out waiting for
    // the server, which is the failure this shape removes the ambiguity from.
    command: 'npx vite preview --port 4173 --strictPort --host 127.0.0.1',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
    stdout: 'pipe',
    stderr: 'pipe',
  },
})
