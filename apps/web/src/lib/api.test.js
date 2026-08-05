import { describe, it, expect, vi, beforeEach } from 'vitest'

vi.mock('axios', () => ({
  default: { get: vi.fn(), post: vi.fn() },
}))

import axios from 'axios'
import {
  checkHealth,
  uploadCellML,
  simulate,
  uploadParamsForId,
  startCalibration,
  startSensitivity,
  errorMessage,
  fetchExampleModel,
} from './api'

beforeEach(() => {
  axios.get.mockReset()
  axios.post.mockReset()
})

describe('api client', () => {
  it('test_health_endpoint_called', async () => {
    axios.get.mockResolvedValue({ data: { status: 'ok' } })
    const ok = await checkHealth()
    expect(ok).toBe(true)
    expect(axios.get).toHaveBeenCalledOnce()
  })

  it('fetches an example as a blob, so a .omex survives the trip', async () => {
    // Text would run the zip through a character codec and corrupt it (#180).
    const blob = new Blob([new Uint8Array([0x50, 0x4b, 3, 4])], { type: 'application/zip' })
    axios.get.mockResolvedValue({ data: blob })
    const file = await fetchExampleModel('3compartment', '3compartment.omex')
    expect(axios.get.mock.calls[0][1]).toMatchObject({ responseType: 'blob' })
    expect(file.name).toBe('3compartment.omex')
    expect(file.type).toBe('application/zip')
  })

  it('test_upload_cellml_resolves_model_id', async () => {
    axios.post.mockResolvedValue({ data: { model_id: 'abc123', name: 'm' } })
    const file = new File(['<model/>'], 'm.cellml')
    const data = await uploadCellML(file)
    expect(data.model_id).toBe('abc123')
    expect(axios.post).toHaveBeenCalledOnce()
  })

  it('test_simulate_called_with_params', async () => {
    axios.post.mockResolvedValue({ data: { time: [], outputs: {} } })
    const params = { 'Lotka_Volterra_module/alpha': 3 }
    await simulate('mid', params, { simTime: 5 })
    const [, body] = axios.post.mock.calls[0]
    expect(body.model_id).toBe('mid')
    expect(body.params).toEqual(params)
    expect(body.sim_time).toBe(5)
  })

  it('test_upload_params_for_id_posts_file', async () => {
    axios.post.mockResolvedValue({ data: { params: [] } })
    const file = new File(['a,b'], 'p.csv')
    await uploadParamsForId(file, 'mid')
    const [, body] = axios.post.mock.calls[0]
    expect(body).toBeInstanceOf(FormData)
  })

  it('test_start_calibration_sends_current_params', async () => {
    axios.post.mockResolvedValue({ data: { job_id: 'j1' } })
    const cur = { 'm/a': 1.5, 'm/b': 2.5 }
    await startCalibration('mid', { param_id_method: 'sp_minimize' }, cur)
    const [, body] = axios.post.mock.calls[0]
    expect(body.model_id).toBe('mid')
    expect(body.current_params).toEqual(cur)
  })

  it('test_start_calibration_omits_current_params_when_absent', async () => {
    axios.post.mockResolvedValue({ data: { job_id: 'j1' } })
    await startCalibration('mid', {})
    const [, body] = axios.post.mock.calls[0]
    expect('current_params' in body).toBe(false)
  })

  it('test_start_sensitivity_sends_current_params', async () => {
    axios.post.mockResolvedValue({ data: { job_id: 'j2' } })
    const cur = { 'm/a': 3 }
    await startSensitivity('mid', { method: 'local', nominal: 'current' }, cur)
    const [, body] = axios.post.mock.calls[0]
    expect(body.current_params).toEqual(cur)
  })
})

// Issue #138: a failed simulation showed only "AxiosError: Request failed with
// status code 500" — the server's own explanation never made it to the user.
describe('errorMessage', () => {
  const axiosError = (response) =>
    Object.assign(new Error('Request failed with status code 500'), {
      name: 'AxiosError',
      response,
    })

  it('prefers the detail the backend sent', () => {
    const detail = 'Simulation failed: CV_TOO_MUCH_ACC\nSettings in force: ...'
    expect(errorMessage(axiosError({ status: 500, data: { detail } }))).toBe(detail)
  })

  it('accepts a plain-text body too', () => {
    expect(errorMessage(axiosError({ status: 500, data: 'boom' }))).toBe('boom')
  })

  it('joins the list FastAPI sends for a validation error', () => {
    const detail = [{ loc: ['body', 'sim_time'], msg: 'must be > 0' }, { msg: 'and this' }]
    expect(errorMessage(axiosError({ status: 422, data: { detail } }))).toBe(
      'must be > 0; and this',
    )
  })

  // The reported symptom: an unhandled server error has no body at all, so
  // there is no `detail` to read and String(e) is the bare Axios text.
  it('attributes a body-less failure instead of parroting Axios', () => {
    const msg = errorMessage(axiosError({ status: 500, statusText: 'Internal Server Error' }))
    expect(msg).toContain('HTTP 500')
    expect(msg).toContain('server log')
    expect(msg).not.toContain('AxiosError')
  })

  it('ignores an empty detail rather than showing a blank error', () => {
    expect(errorMessage(axiosError({ status: 500, data: { detail: '   ' } }))).toContain(
      'HTTP 500',
    )
  })

  // No response at all: the server is down / the request never left.
  it('falls back to the error text when there was no response', () => {
    expect(errorMessage(new Error('Network Error'))).toBe('Network Error')
  })
})
