import axios from 'axios'

// Default to same-origin: in production the FastAPI server serves this built app
// and the API under /api; in dev the Vite proxy forwards /api to :8000. Override
// with VITE_API_URL only for a split/remote backend.
const baseURL = import.meta.env?.VITE_API_URL ?? ''

function url(path) {
  return `${baseURL}${path}`
}

/**
 * The most informative text available for a failed request (issue #138).
 *
 * The backend puts the real reason in `detail`, but an unhandled server error
 * has no JSON body at all — and `String(e)` then yields "AxiosError: Request
 * failed with status code 500", which is what the issue was about. Fall through
 * the body shapes FastAPI can produce before giving up on Axios's own text, and
 * keep the status alongside it so a bare failure is at least attributable.
 */
export function errorMessage(e) {
  const res = e?.response
  const data = res?.data
  const detail = typeof data === 'string' ? data : data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  // 422s from FastAPI validation arrive as a list of {loc, msg} objects.
  if (Array.isArray(detail) && detail.length) {
    return detail.map((d) => d?.msg || JSON.stringify(d)).join('; ')
  }
  if (res?.status) {
    return `Request failed (HTTP ${res.status}${res.statusText ? ` ${res.statusText}` : ''}). The server did not say why — check the server log.`
  }
  return String(e?.message || e)
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
  // Locates the user's custom operation funcs so a data_item's series_output
  // overlay can be computed (issue #111).
  if (options.outputsDir) body.config_outputs_dir = options.outputsDir
  // "Everything you can" rather than a specific list: unresolvable names are
  // skipped instead of failing the run (#150).
  if (options.bestEffortOutputs) body.best_effort_outputs = true
  const { data } = await axios.post(url('/api/simulate'), body)
  return data
}

export async function runProtocol(modelId, params, options = {}) {
  const body = { model_id: modelId, params }
  if (options.protocolInfo != null) body.protocol_info = options.protocolInfo
  if (options.outputs != null) body.outputs = options.outputs
  if (options.outputsDir) body.config_outputs_dir = options.outputsDir
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
// refresh=true to re-introspect CA (e.g. after adding a custom operation), and
// outputDir so the user's custom funcs (stored there) appear in the lists.
export async function getObsDataOptions(refresh = false, outputDir = '') {
  const params = {}
  if (refresh) params.refresh = true
  if (outputDir) params.output_dir = outputDir
  const { data } = await axios.get(url('/api/obs_data/options'), { params })
  return data
}

// User-authored observable operation & cost funcs (issues #58 / #104). CUFLynx
// saves them to a file in the user's output directory and points CA at it (CA
// #303). `kind` is 'operation' or 'cost' -> /api/{operation,cost}_funcs;
// `outputDir` is where the funcs live (config_outputs_dir).
const FUNC_ENDPOINT = { operation: 'operation_funcs', cost: 'cost_funcs' }

export async function getUserFuncs(kind = 'operation', outputDir = '') {
  const { data } = await axios.get(url(`/api/${FUNC_ENDPOINT[kind]}`), {
    params: outputDir ? { output_dir: outputDir } : {},
  })
  return data
}

export async function saveUserFunc(kind, name, source, outputDir = '') {
  const { data } = await axios.post(url(`/api/${FUNC_ENDPOINT[kind]}`), {
    name,
    source,
    output_dir: outputDir || '',
  })
  return data
}

export async function deleteUserFunc(kind, name, outputDir = '') {
  const { data } = await axios.delete(
    url(`/api/${FUNC_ENDPOINT[kind]}/${encodeURIComponent(name)}`),
    { params: outputDir ? { output_dir: outputDir } : {} },
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

// Save the current slider values to a named file (issue #106). Format follows the
// filename extension: `.csv` -> CSV, else numpy `.npy` (default). `order` is the
// qname order for the npy array. Returns { path }.
export async function saveParams(values, order, filename, outputDir = '', result = null) {
  const { data } = await axios.post(url('/api/params/save'), {
    values,
    order,
    filename,
    output_dir: outputDir || '',
    // The traces these values produced, stored under the same prefix so the run
    // can be shown again later without re-running it (#126).
    ...(result ? { result } : {}),
  })
  return data
}

// Saved runs in an output directory (metadata only — no series). Issue #126.
export async function listSavedRuns(outputDir = '') {
  const { data } = await axios.get(url('/api/runs'), {
    params: outputDir ? { dir: outputDir } : {},
  })
  return data
}

// One saved run, with its series, to overlay on the plots.
export async function loadSavedRun(path) {
  const { data } = await axios.get(url('/api/runs/load'), { params: { path } })
  return data
}

// Load slider values from a .npy or .csv file. For npy, `order` (the current
// qnames) names the bare array. Returns { values: { qname: value } }.
export async function loadParams(path, order = []) {
  const { data } = await axios.post(url('/api/params/load'), { path, order })
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

// Direct URL to download the calibrated CellML saved when a run finishes (#114).
export function calibratedModelUrl(jobId) {
  return url(`/api/calibration/${encodeURIComponent(jobId)}/calibrated_model`)
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
