import axios from 'axios'

// Default to same-origin: in production the FastAPI server serves this built app
// and the API under /api; in dev the Vite proxy forwards /api to :8000. Override
// with VITE_API_URL only for a split/remote backend.
const baseURL = import.meta.env?.VITE_API_URL ?? ''

function url(path) {
  return `${baseURL}${path}`
}

export async function checkHealth() {
  const { data } = await axios.get(url('/api/health'))
  return data.status === 'ok'
}

export async function listDir(path = null, dirsOnly = false) {
  const { data } = await axios.get(url('/api/fs/list'), {
    params: { ...(path ? { path } : {}), dirs_only: dirsOnly },
  })
  return data
}

export async function uploadCellML(file) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await axios.post(url('/api/models/upload'), form)
  return data
}

export async function getVariables(modelId) {
  const { data } = await axios.get(url(`/api/models/${modelId}/variables`))
  return data
}

export async function simulate(modelId, params, options = {}) {
  const body = { model_id: modelId, params }
  if (options.simTime != null) body.sim_time = options.simTime
  if (options.preTime != null) body.pre_time = options.preTime
  if (options.outputs != null) body.outputs = options.outputs
  const { data } = await axios.post(url('/api/simulate'), body)
  return data
}

export async function runProtocol(modelId, params, options = {}) {
  const body = { model_id: modelId, params }
  if (options.protocolInfo != null) body.protocol_info = options.protocolInfo
  if (options.outputs != null) body.outputs = options.outputs
  const { data } = await axios.post(url('/api/protocol/run'), body)
  return data
}

export async function uploadObsData(modelId, obsData) {
  const { data } = await axios.post(url('/api/obs_data/upload'), {
    model_id: modelId,
    obs_data: obsData,
  })
  return data
}

export async function uploadParamsForId(file, modelId) {
  const form = new FormData()
  form.append('file', file)
  if (modelId) form.append('model_id', modelId)
  const { data } = await axios.post(url('/api/params_for_id/upload'), form)
  return data
}

export async function getCalibrationDefaults() {
  const { data } = await axios.get(url('/api/calibration/defaults'))
  return data
}

export async function getCalibrationPythons(refresh = false) {
  const { data } = await axios.get(
    url(`/api/calibration/pythons${refresh ? '?refresh=true' : ''}`),
  )
  return data
}

export async function startCalibration(modelId, settings) {
  const { data } = await axios.post(url('/api/calibration/run'), {
    model_id: modelId,
    settings,
  })
  return data
}

export async function getCalibrationStatus(jobId, offset = 0) {
  const { data } = await axios.get(
    url(`/api/calibration/${jobId}/status?offset=${offset}`),
  )
  return data
}

export async function getCalibrationProgress(jobId) {
  const { data } = await axios.get(url(`/api/calibration/${jobId}/progress`))
  return data
}

export async function cancelCalibration(jobId) {
  const { data } = await axios.post(url(`/api/calibration/${jobId}/cancel`))
  return data
}

export async function getSensitivityDefaults() {
  const { data } = await axios.get(url('/api/sensitivity/defaults'))
  return data
}

export async function startSensitivity(modelId, settings) {
  const { data } = await axios.post(url('/api/sensitivity/run'), {
    model_id: modelId,
    settings,
  })
  return data
}

export async function getSensitivityStatus(jobId, offset = 0) {
  const { data } = await axios.get(
    url(`/api/sensitivity/${jobId}/status?offset=${offset}`),
  )
  return data
}

export async function cancelSensitivity(jobId) {
  const { data } = await axios.post(url(`/api/sensitivity/${jobId}/cancel`))
  return data
}

export async function getUQDefaults() {
  const { data } = await axios.get(url('/api/uq/defaults'))
  return data
}

export async function startUQ(modelId, settings) {
  const { data } = await axios.post(url('/api/uq/run'), {
    model_id: modelId,
    settings,
  })
  return data
}

export async function getUQStatus(jobId, offset = 0) {
  const { data } = await axios.get(url(`/api/uq/${jobId}/status?offset=${offset}`))
  return data
}

export async function cancelUQ(jobId) {
  const { data } = await axios.post(url(`/api/uq/${jobId}/cancel`))
  return data
}
