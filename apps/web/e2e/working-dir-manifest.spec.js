/**
 * Dropping the study manifest of a working dir that has been moved.
 *
 * A working dir holds every input and output of a study with relative paths, so it
 * can be zipped, sent and unpacked anywhere. The manifest the user drops is then the
 * only handle on where the study now is: the desktop shell gives the dropped file a
 * `path`, the app asks `/api/working-dir/manifest` which folder that opens, sets the
 * Working dir field to it and reloads it through `/api/working-dir/load`.
 *
 * The API is stubbed (`page.route`), as for the rest of this tier: what is checked
 * is the browser half -- which routes are called, with what, and what the field shows.
 */
import { test, expect } from '@playwright/test'

const MOVED = '/home/u/moved/BVC_RAAS6'
const MANIFEST_PATH = `${MOVED}/RAAS6_cuflynx_study.json`
const MANIFEST = {
  schema: 1,
  study_name: 'RAAS6',
  file_prefix: 'RAAS6',
  model: 'inputs/model/RAAS6.cellml',
  obs_data: 'inputs/obs_data/RAAS6_obs_data.json',
  params_for_id: 'inputs/params_for_id/RAAS6_params_for_id.json',
  user_funcs: { operation: 'inputs/funcs/operation_funcs_user.py' },
  runs: [{ dir: 'genetic_algorithm_RAAS6_RAAS6_obs_data', method: 'genetic_algorithm' }],
}

test('dropping a moved working dir\'s manifest opens that folder', async ({ page }) => {
  const calls = []
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = new URL(request.url())
    calls.push({ path: url.pathname, search: url.searchParams, body: request.postData() })
    if (url.pathname === '/api/working-dir/manifest') {
      return route.fulfill({
        json: { dir: MOVED, study_name: 'RAAS6', file_prefix: 'RAAS6' },
      })
    }
    if (url.pathname === '/api/working-dir/load') {
      return route.fulfill({ json: { found: [], missing: [] } })
    }
    if (url.pathname === '/api/health') {
      return route.fulfill({ json: { status: 'ok', app: 'CUFLynx' } })
    }
    return route.fulfill({ json: {} })
  })
  // A working dir from an earlier session, so the startup prompt stays shut.
  await page.addInitScript(() => {
    localStorage.setItem('cuflynx-working-dir', '/home/u/somewhere-else')
  })
  await page.goto('/')

  const field = page.getByTestId('config-working-dir')
  await expect(field).toHaveValue('/home/u/somewhere-else')

  // The desktop shell's drop: a File carrying the `path` it was dropped from.
  await page.evaluate(
    ({ text, path }) => {
      const file = new File([text], 'RAAS6_cuflynx_study.json', { type: 'application/json' })
      Object.defineProperty(file, 'path', { value: path })
      const drop = new Event('drop', { bubbles: true, cancelable: true })
      Object.defineProperty(drop, 'dataTransfer', { value: { files: [file], types: ['Files'] } })
      document.querySelector('[data-testid="cellml-drop"]').dispatchEvent(drop)
    },
    { text: JSON.stringify(MANIFEST), path: MANIFEST_PATH },
  )

  await expect(field).toHaveValue(MOVED)

  const asked = calls.find((c) => c.path === '/api/working-dir/manifest')
  expect(asked, 'the manifest is resolved through /api/working-dir/manifest').toBeTruthy()
  const body = JSON.parse(asked.body)
  expect(body.path).toBe(MANIFEST_PATH)
  expect(JSON.parse(body.content).model).toBe('inputs/model/RAAS6.cellml')

  await expect.poll(
    () => calls.find((c) => c.path === '/api/working-dir/load')?.search.get('dir'),
  ).toBe(MOVED)
  // Only the new routes: the old /api/outputs/* names are backend aliases.
  expect(calls.some((c) => c.path.startsWith('/api/outputs/'))).toBe(false)
  // The new localStorage key keeps the opened folder.
  expect(await page.evaluate(() => localStorage.getItem('cuflynx-working-dir'))).toBe(MOVED)
})
