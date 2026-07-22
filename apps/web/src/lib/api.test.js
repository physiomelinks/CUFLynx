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
