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

/**
 * Runtime config. `outputsDir` is optional and only affects
 * `param_modifier_operations`: a modifier the user wrote themselves lives under
 * the outputs directory, so without it their own modifiers are missing from the
 * params editor's list.
 */
export async function getConfig(outputsDir = '') {
  const { data } = await axios.get(url('/api/config'), {
    params: outputsDir ? { output_dir: outputsDir } : {},
  })
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
export async function uploadCellML(fileOrFiles, outputDir = '') {
  const files = Array.isArray(fileOrFiles) ? fileOrFiles : [fileOrFiles]
  const form = new FormData()
  if (files.length === 1) {
    form.append('file', files[0])
  } else {
    for (const f of files) form.append('files', f)
  }
  // A dropped Myokit model is converted to CellML server-side (#27); the output
  // dir is where the converted file is kept for the user.
  const { data } = await axios.post(url('/api/models/upload'), form, {
    params: outputDir ? { output_dir: outputDir } : {},
  })
  return data
}

/**
 * Direct URL to the file the user wrote, for a model that has one: the `.py` of
 * an external python model, or the `.mmt` a CellML model was converted from
 * (#27). Served inline as text, so it is opened in a tab rather than fetched.
 *
 * Not what the Edit button does any more — that opens the file in the user's own
 * editor, which only a local backend can do. This is the read-only half, and the
 * one a remote or headless deployment can still offer.
 *
 * `outputsDir` makes it serve the study's copy when there is one, so the tab and
 * the editor never show different versions of the same model.
 *
 * 404s for a plain CellML model, which is edited in PhLynx instead.
 */
export function modelSourceUrl(modelId, outputsDir = '') {
  const base = url(`/api/models/${encodeURIComponent(modelId)}/source`)
  return outputsDir ? `${base}?config_outputs_dir=${encodeURIComponent(outputsDir)}` : base
}

/**
 * Put the model's source under the outputs directory and open it in the user's
 * editor, on the machine the backend runs on.
 *
 * Returns `{path, filename, opened, editor, reason, runs}`. `opened: false` is a
 * normal answer, not an error — a headless backend has no editor to launch, and
 * the caller should still tell the user where `path` is. `runs` says whether
 * that copy is the file CUFLynx simulates (true for a `.py`; a `.mmt` is the
 * source of a CellML that runs in its place).
 *
 * 422s when no outputs directory is set: there is nowhere the edit could safely
 * live, and a temp directory is exactly what this replaced.
 */
export async function editModelSource(modelId, outputsDir = '') {
  const { data } = await axios.post(url(`/api/models/${encodeURIComponent(modelId)}/edit`), {
    config_outputs_dir: outputsDir || '',
  })
  return data
}

// Fetch a bundled example as a File, so it can be fed straight through the
// normal upload flow (same path as a dropped file).
//
// As a blob, not text: an example ships as a .omex, and decoding a zip through
// the text codec mangles every byte of it.
export async function fetchExampleModel(name, filename) {
  const { data } = await axios.get(url(`/api/examples/${name}`), { responseType: 'blob' })
  return new File([data], filename, { type: data?.type || 'application/octet-stream' })
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

// How sensitive the displayed cost is to each parameter (#188): d ln(cost)/d ln(p),
// by central differences about the current slider values. Opt-in, because it
// costs 2M+1 simulations for M parameters — hence `paramNames`, which narrows it
// to the sliders actually on screen, and `bounds`, which only matters where a
// parameter sits at exactly 0 and has no scale of its own.
//
// The run description (outputs / protocolInfo / times) is the same one the live
// run used, so the base cost is the number the panel is showing.
export async function costSensitivity(modelId, params, options = {}) {
  const body = { model_id: modelId, params }
  if (options.paramNames != null) body.param_names = options.paramNames
  if (options.bounds != null) body.bounds = options.bounds
  // Modifier sliders, differenced in θ server-side (#208). Sent only when
  // non-empty so older backends never see an unknown key.
  if (options.modifiers?.length) body.modifiers = options.modifiers
  if (options.relStep != null) body.rel_step = options.relStep
  if (options.simTime != null) body.sim_time = options.simTime
  if (options.preTime != null) body.pre_time = options.preTime
  if (options.outputs != null) body.outputs = options.outputs
  if (options.protocolInfo != null) body.protocol_info = options.protocolInfo
  if (options.outputsDir) body.config_outputs_dir = options.outputsDir
  const { data } = await axios.post(url('/api/cost_sensitivity'), body, {
    signal: options.signal,
  })
  return data
}

/**
 * One parameter set, scored by the model and by the emulator (#333).
 *
 * `{ cost, emulator_cost }`, both in the shape a run's `cost` comes back in, and
 * both from the one CA-backed scorer — so the difference between them is the
 * surrogate's error and nothing else. Both are asked for in a single request so
 * they cannot end up describing two different points.
 *
 * `analysisParams` is the same point written as θ (one value per params_for_id
 * row, at a modifier's anchor rather than its expansion): the emulator was
 * trained on θ and must be given θ. Omit it where the two coincide.
 *
 * `emulator_cost` is null whenever the emulator cannot answer — no bundle, a
 * stale one, no autoemulate in the configured interpreter. That is a silence,
 * not an error.
 */
export async function costAtParams(modelId, params, options = {}) {
  const body = { model_id: modelId, params }
  if (options.analysisParams != null) body.analysis_params = options.analysisParams
  if (options.simTime != null) body.sim_time = options.simTime
  if (options.preTime != null) body.pre_time = options.preTime
  if (options.outputs != null) body.outputs = options.outputs
  if (options.protocolInfo != null) body.protocol_info = options.protocolInfo
  if (options.outputsDir) body.config_outputs_dir = options.outputsDir
  const { data } = await axios.post(url('/api/cost_at_params'), body, {
    signal: options.signal,
  })
  return data
}

// Load a whole COMBINE archive (.omex): model + obs_data + params_for_id (#149).
// One request, because an archive is the study rather than any one of its files.
export async function uploadOmex(file, outputDir = '') {
  const form = new FormData()
  form.append('file', file)
  const { data } = await axios.post(url('/api/omex/upload'), form, {
    params: outputDir ? { output_dir: outputDir } : {},
  })
  return data
}

// `save` (#215) asks the server to also write the dated copy where the study
// lives — { outputsDir, filename } — instead of the browser downloading it.
// Omitted for a plain upload, which already has a file on disk.
// Build the study as a COMBINE archive for PhLynx (#290). The server assembles
// and base64s it, so the writer lives in one place and the frontend keeps
// assuming nothing about a local backend — all it does with the result is
// `window.open`. `source` is 'current' | 'best_fit' | 'as_imported'.
export async function sendToPhlynx(modelId, { source = 'current', values = {}, outputDir = '' } = {}) {
  const { data } = await axios.post(url('/api/phlynx/send'), {
    model_id: modelId,
    source,
    values,
    output_dir: outputDir,
  })
  return data
}

// The same archive as a file, for when it is too big to survive a URL fragment.
export function phlynxDownloadRequest(modelId, { source = 'current', values = {}, outputDir = '' } = {}) {
  return axios.post(
    url('/api/phlynx/send'),
    { model_id: modelId, source, values, output_dir: outputDir, download: true },
    { responseType: 'blob' },
  )
}

export async function uploadObsData(modelId, obsData, save = null) {
  const { data } = await axios.post(
    url('/api/obs_data/upload'),
    { model_id: modelId, obs_data: obsData },
    { params: save?.filename ? { filename: save.filename, output_dir: save.outputsDir || '' } : {} },
  )
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

export async function uploadParamsForId(file, modelId, save = null) {
  const form = new FormData()
  form.append('file', file)
  if (modelId) form.append('model_id', modelId)
  const { data } = await axios.post(url('/api/params_for_id/upload'), form, {
    params: save?.filename ? { filename: save.filename, output_dir: save.outputsDir || '' } : {},
  })
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

// --- Emulator (surrogate model, CA #333) -------------------------------------

/** The emulator settings form, from circulatory_autogen's `emulation` schema. */
export async function getEmulatorDefaults() {
  const { data } = await axios.get(url('/api/emulator/defaults'))
  return data
}

/**
 * The trained emulator for this study, if any: `{ emulator_dir, metadata }`.
 * `metadata` is null when nothing has been trained yet, which is the normal
 * starting state rather than an error.
 */
export async function getEmulatorInfo(modelId, configOutputsDir = '') {
  const { data } = await axios.get(
    url(
      `/api/emulator/info?model_id=${encodeURIComponent(modelId)}` +
        `&config_outputs_dir=${encodeURIComponent(configOutputsDir || '')}`,
    ),
  )
  return data
}

export async function startEmulatorTraining(modelId, settings) {
  const { data } = await axios.post(url('/api/emulator/train'), {
    model_id: modelId,
    settings,
  })
  return data
}

export async function getEmulatorStatus(jobId, offset = 0) {
  const { data } = await axios.get(
    url(`/api/emulator/${jobId}/status?offset=${offset}`),
  )
  return data
}

export async function cancelEmulatorTraining(jobId) {
  const { data } = await axios.post(url(`/api/emulator/${jobId}/cancel`))
  return data
}

/**
 * The emulator's predicted features at the given parameter values.
 * `{ labels, values, in_box, cost }` — drawn beside the model's own features so
 * the two can be compared against the ground truth while a slider moves.
 *
 * `cost` is what those predicted features cost against the loaded obs_data,
 * scored by the same code that scores the solver's (#333). It rides on this
 * request because this request is already made whenever the parameters settle,
 * so the emulator's cost and the model's describe one parameter set rather than
 * two. Null when there is no obs_data or CA cannot score it.
 */
export async function predictEmulator(modelId, params, settings = {}) {
  const { data } = await axios.post(url('/api/emulator/predict'), {
    model_id: modelId,
    params,
    settings,
  })
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

/**
 * The growing MCMC chain, as the three views the Progress tab draws (#244).
 *
 * Separate from getUQStatus because it is the heaviest thing a run produces: status is polled
 * for log lines at a rate that suits text, this at one that suits a plot.
 */
export async function getUQProgress(jobId) {
  const { data } = await axios.get(url(`/api/uq/${jobId}/progress`))
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
