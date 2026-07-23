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

export async function makeDir(parent, name) {
  const { data } = await axios.post(url('/api/fs/mkdir'), { parent, name })
  return data
}

export async function getConfig() {
  const { data } = await axios.get(url('/api/config'))
  return data
}

/**
 * Update runtime config. Accepts a string (CA dir, back-compat) or an options
 * object: { caDir, generatedModelFormat, solver, solverInfo, pythonPath }.
 * Omitted fields are left unchanged server-side.
 *
 * The server persists these to a user config file, so they survive a restart —
 * which is what lets the packaged desktop app remember where circulatory_autogen
 * and the analysis interpreter are.
 */
export async function setConfig(opts = {}) {
  const body = {}
  if (typeof opts === 'string') {
    body.ca_dir = opts
  } else {
    if (opts.caDir != null) body.ca_dir = opts.caDir
    if (opts.generatedModelFormat != null)
      body.generated_model_format = opts.generatedModelFormat
    if (opts.solver != null) body.solver = opts.solver
    if (opts.solverInfo != null) body.solver_info = opts.solverInfo
    if (opts.pythonPath != null) body.python_path = opts.pythonPath
    // Global random seed: a number sets it, '' clears it; omit to leave unchanged.
    if (opts.seed !== undefined) body.seed = opts.seed
  }
  const { data } = await axios.post(url('/api/config'), body)
  return data
}

// Accepts a single File or an array of Files (a non-flattened model + its sister
// files). Multiple files go under the `files` field, which the server flattens
// to one CellML 2.0 model; a single file uses `file` (back-compatible).
export async function uploadCellML(fileOrFiles) {
  const files = Array.isArray(fileOrFiles) ? fileOrFiles : [fileOrFiles]
  const form = new FormData()
  if (files.length === 1) {
    form.append('file', files[0])
  } else {
    for (const f of files) form.append('files', f)
  }
  const { data } = await axios.post(url('/api/models/upload'), form)
  return data
}

// Fetch a bundled example CellML model as a File, so it can be fed straight
// through the normal uploadCellML flow (same path as a dropped file).
export async function fetchExampleModel(name, filename) {
  const { data } = await axios.get(url(`/api/examples/${name}`), { responseType: 'text' })
  return new File([data], filename, { type: 'application/xml' })
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

// Operation (obs_funcs) + cost_type (cost_func) option lists, sourced from
// circulatory_autogen — used to populate the obs_data editor dropdowns. Pass
// refresh=true to re-introspect CA (e.g. after adding a custom operation).
export async function getObsDataOptions(refresh = false) {
  const { data } = await axios.get(url('/api/obs_data/options'), {
    params: refresh ? { refresh: true } : {},
  })
  return data
}

// User-authored observable operation & cost funcs (issues #58 / #104). CUFLynx
// saves them to an external file it manages and points CA at it (CA #303). The
// `kind` is 'operation' or 'cost'; each maps to /api/{operation,cost}_funcs.
const FUNC_ENDPOINT = { operation: 'operation_funcs', cost: 'cost_funcs' }

export async function getUserFuncs(kind = 'operation') {
  const { data } = await axios.get(url(`/api/${FUNC_ENDPOINT[kind]}`))
  return data
}

export async function saveUserFunc(kind, name, source) {
  const { data } = await axios.post(url(`/api/${FUNC_ENDPOINT[kind]}`), { name, source })
  return data
}

export async function deleteUserFunc(kind, name) {
  const { data } = await axios.delete(
    url(`/api/${FUNC_ENDPOINT[kind]}/${encodeURIComponent(name)}`),
  )
  return data
}

// Back-compat wrappers (operation-only) for existing callers.
export const getUserOperations = () => getUserFuncs('operation')
export const saveUserOperation = (name, source) => saveUserFunc('operation', name, source)
export const deleteUserOperation = (name) => deleteUserFunc('operation', name)

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

export async function startCalibration(modelId, settings, currentParams = null) {
  const { data } = await axios.post(url('/api/calibration/run'), {
    model_id: modelId,
    settings,
    ...(currentParams ? { current_params: currentParams } : {}),
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

export async function startSensitivity(modelId, settings, currentParams = null) {
  const { data } = await axios.post(url('/api/sensitivity/run'), {
    model_id: modelId,
    settings,
    ...(currentParams ? { current_params: currentParams } : {}),
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

export async function exportPipeline(payload) {
  const { data } = await axios.post(url('/api/export/pipeline'), payload)
  return data
}

export async function exportPlotting(payload) {
  const { data } = await axios.post(url('/api/export/plotting'), payload)
  return data
}
