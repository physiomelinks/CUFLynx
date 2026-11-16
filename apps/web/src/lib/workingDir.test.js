import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('axios', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn() },
}))

import axios from 'axios'
import {
  WORKING_DIR_KEY,
  LEGACY_WORKING_DIR_KEY,
  NO_WORKING_DIR_MESSAGE,
  readStoredWorkingDir,
  storeWorkingDir,
  workingDirRefusal,
} from './workingDir'
import {
  getConfig,
  uploadCellML,
  uploadOmex,
  saveParams,
  getUserFuncs,
  loadWorkflow,
  getEmulatorInfo,
  openStudyFromOutputs,
  openStudyManifest,
  loadWorkingDir,
} from './api'

beforeEach(() => {
  localStorage.clear()
  axios.get.mockReset()
  axios.post.mockReset()
  axios.get.mockResolvedValue({ data: {} })
  axios.post.mockResolvedValue({ data: {} })
})

describe('stored working dir', () => {
  it('uses the cuflynx-working-dir key', () => {
    expect(WORKING_DIR_KEY).toBe('cuflynx-working-dir')
    storeWorkingDir('/studies/heart')
    expect(localStorage.getItem('cuflynx-working-dir')).toBe('/studies/heart')
    expect(readStoredWorkingDir()).toBe('/studies/heart')
  })

  it('moves a value saved under the old outputs-dir key, once', () => {
    localStorage.setItem(LEGACY_WORKING_DIR_KEY, '/old/outputs')
    expect(readStoredWorkingDir()).toBe('/old/outputs')
    expect(localStorage.getItem(WORKING_DIR_KEY)).toBe('/old/outputs')
    expect(localStorage.getItem(LEGACY_WORKING_DIR_KEY)).toBeNull()
    // A later change is not undone by the migration running again.
    storeWorkingDir('/new')
    expect(readStoredWorkingDir()).toBe('/new')
  })

  it('keeps the new key when both are present, and drops the old one', () => {
    localStorage.setItem(WORKING_DIR_KEY, '/new')
    localStorage.setItem(LEGACY_WORKING_DIR_KEY, '/old')
    expect(readStoredWorkingDir()).toBe('/new')
    expect(localStorage.getItem(LEGACY_WORKING_DIR_KEY)).toBeNull()
  })

  it('answers empty when storage refuses access', () => {
    const spy = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('denied')
    })
    expect(readStoredWorkingDir()).toBe('')
    spy.mockRestore()
  })

  it('refuses a run with no working dir, and only then', () => {
    expect(workingDirRefusal('')).toBe(NO_WORKING_DIR_MESSAGE)
    expect(workingDirRefusal('   ')).toBe(NO_WORKING_DIR_MESSAGE)
    expect(workingDirRefusal('/w')).toBeNull()
    expect(NO_WORKING_DIR_MESSAGE).toMatch(/^Set a working dir/)
  })
})

describe('api: working dir on the wire', () => {
  it('sends working_dir as a query parameter', async () => {
    await getConfig('/w')
    expect(axios.get.mock.calls[0][1].params).toEqual({ working_dir: '/w' })
    await uploadCellML(new File(['x'], 'm.cellml'), '/w')
    expect(axios.post.mock.calls[0][2].params).toEqual({ working_dir: '/w' })
    await uploadOmex(new File(['x'], 's.omex'), '/w')
    expect(axios.post.mock.calls[1][2].params).toEqual({ working_dir: '/w' })
    await getUserFuncs('operation', '/w')
    expect(axios.get.mock.calls[1][1].params).toEqual({ working_dir: '/w' })
    await getEmulatorInfo('m1', '/w')
    expect(axios.get.mock.calls[2][0]).toContain('working_dir=%2Fw')
  })

  it('sends working_dir in JSON bodies, never the old names', async () => {
    await saveParams({ 'a/b': 1 }, ['a/b'], 'p.npy', '/w')
    await loadWorkflow({ path: '/w/wf.json', workingDir: '/w' })
    for (const [, body] of axios.post.mock.calls) {
      expect(body.working_dir).toBe('/w')
      expect(body).not.toHaveProperty('output_dir')
      expect(body).not.toHaveProperty('config_outputs_dir')
    }
  })

  it('reads a study through the /api/working-dir routes', async () => {
    await loadWorkingDir('/w', 'heart')
    expect(axios.get.mock.calls[0][0]).toContain('/api/working-dir/load?dir=%2Fw&file_prefix=heart')
    await openStudyManifest('{"schema":1}', '/w/heart_cuflynx_study.json')
    expect(axios.post.mock.calls[0][0]).toContain('/api/working-dir/manifest')
    expect(axios.post.mock.calls[0][1]).toEqual({
      content: '{"schema":1}', path: '/w/heart_cuflynx_study.json',
    })
    await openStudyFromOutputs('/w')
    expect(axios.post.mock.calls[1][0]).toContain('/api/working-dir/study')
  })
})
