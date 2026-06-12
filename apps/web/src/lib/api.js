import axios from 'axios'

const baseURL = import.meta.env?.VITE_API_URL || 'http://localhost:8000'

function url(path) {
  return `${baseURL}${path}`
}

export async function checkHealth() {
  const { data } = await axios.get(url('/api/health'))
  return data.status === 'ok'
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

export async function cancelCalibration(jobId) {
  const { data } = await axios.post(url(`/api/calibration/${jobId}/cancel`))
  return data
}
