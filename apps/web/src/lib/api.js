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
  // A refusal that carries more than its message (a workflow step's unset parameters).
  if (typeof detail?.message === 'string' && detail.message.trim()) return detail.message
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
 * Runtime config. `workingDir` is optional and only affects
 * `param_modifier_operations`: a modifier the user wrote themselves lives under
 * the working dir, so without it their own modifiers are missing from the
 * params editor's list.
 */
export async function getConfig(workingDir = '') {
  const { data } = await axios.get(url('/api/config'), {
    params: workingDir ? { working_dir: workingDir } : {},
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
    // Where "Edit" sends a study: a URL sets it, '' restores production PhLynx.
    if (opts.phlynxUrl != null) body.phlynx_url = opts.phlynxUrl
    // Module libraries a calibration workflow's library modules are found in; [] clears.
    if (opts.moduleLibraryDirs != null) body.module_library_dirs = opts.moduleLibraryDirs
  }
  const { data } = await axios.post(url('/api/config'), body)
  return data
}

// Accepts a single File or an array of Files (a non-flattened model + its sister
// files). Multiple files go under the `files` field, which the server flattens
// to one CellML 2.0 model; a single file uses `file` (back-compatible).
export async function uploadCellML(fileOrFiles, workingDir = '') {
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
    params: workingDir ? { working_dir: workingDir } : {},
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
 * `workingDir` makes it serve the study's copy when there is one, so the tab and
 * the editor never show different versions of the same model.
 *
 * 404s for a plain CellML model, which is edited in PhLynx instead.
 */
export function modelSourceUrl(modelId, workingDir = '') {
  const base = url(`/api/models/${encodeURIComponent(modelId)}/source`)
  return workingDir ? `${base}?working_dir=${encodeURIComponent(workingDir)}` : base
}

/**
 * Put the model's source under the working dir and open it in the user's
 * editor, on the machine the backend runs on.
 *
 * Returns `{path, filename, opened, editor, reason, runs}`. `opened: false` is a
 * normal answer, not an error — a headless backend has no editor to launch, and
 * the caller should still tell the user where `path` is. `runs` says whether
 * that copy is the file CUFLynx simulates (true for a `.py`; a `.mmt` is the
 * source of a CellML that runs in its place).
 *
 * 422s when no working dir is set: there is nowhere the edit could safely
 * live, and a temp directory is exactly what this replaced.
 */
export async function editModelSource(modelId, workingDir = '') {
  const { data } = await axios.post(url(`/api/models/${encodeURIComponent(modelId)}/edit`), {
    working_dir: workingDir || '',
  })
  return data
}

// Fetch a bundled example as a File, so it can be fed straight through the
// normal upload flow (same path as a dropped file).
//
// As a blob, not text: an example ships as a .omex, and decoding a zip through
// the text codec mangles every byte of it.
export async function fetchExampleModel(name, filename) {
  // Fetched here and posted straight back to the upload route, so the browser
  // cache -- not the server -- decides which version of the example is imported.
  // The route sends `Cache-Control: no-cache`, but an entry cached *before* it
  // did is still reusable without revalidation: a user who once loaded a stale
  // example goes on loading it, from a server that is serving the current one.
  // A URL that never repeats cannot match any of those entries.
  const { data } = await axios.get(url(`/api/examples/${name}`), {
    params: { _: Date.now() },
    responseType: 'blob',
    headers: { 'Cache-Control': 'no-cache' },
  })
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
  if (options.workingDir) body.working_dir = options.workingDir
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
  if (options.workingDir) body.working_dir = options.workingDir
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
  if (options.workingDir) body.working_dir = options.workingDir
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
  if (options.workingDir) body.working_dir = options.workingDir
  const { data } = await axios.post(url('/api/cost_at_params'), body, {
    signal: options.signal,
  })
  return data
}

// Load a whole COMBINE archive (.omex): model + obs_data + params_for_id (#149).
// One request, because an archive is the study rather than any one of its files.
export async function uploadOmex(file, workingDir = '') {
  const form = new FormData()
  form.append('file', file)
  const { data } = await axios.post(url('/api/omex/upload'), form, {
    params: workingDir ? { working_dir: workingDir } : {},
  })
  return data
}

// Adopt a libcuflynx user_inputs.yaml's solver settings (solver, solver_info,
// dt). Returns the config payload plus `solver_settings` and `warnings`.
export async function uploadUserInputs(file, workingDir = '') {
  const form = new FormData()
  form.append('file', file)
  const { data } = await axios.post(url('/api/user_inputs/upload'), form, {
    params: workingDir ? { working_dir: workingDir } : {},
  })
  return data
}

// PhLynx hands a study to a running CUFLynx by posting it to the inbox (#287).
// It is *staged*, not imported: CORS stops a page reading our responses, not
// sending requests, so the confirmation in the UI is the security control and
// these three calls are what drives it.
//
// `peekInbox` throws an error with `deliveryFailed: true` when a cuflynx:// link
// could not be opened (the server reports that once, on the next poll).
export async function peekInbox() {
  const { data } = await axios.get(url('/api/inbox'))
  if (data.error) {
    const err = new Error(data.error.message || 'A cuflynx:// link could not be opened')
    err.deliveryFailed = true
    throw err
  }
  return data.pending
}

export async function acceptInbox(workingDir = '') {
  const { data } = await axios.post(url('/api/inbox/accept'), null, {
    params: workingDir ? { working_dir: workingDir } : {},
  })
  return data
}

export async function rejectInbox() {
  const { data } = await axios.post(url('/api/inbox/reject'))
  return data
}

// `save` (#215) asks the server to also write the dated copy where the study
// lives — { workingDir, filename } — instead of the browser downloading it.
// Omitted for a plain upload, which already has a file on disk.
// Build the study as a COMBINE archive for PhLynx (#290). The server assembles
// and base64s it, so the writer lives in one place and the frontend keeps
// assuming nothing about a local backend. What it does with the result is render
// `base64` into an anchor's href and `download_url` into a second one — it never
// *scripts* a navigation. pywebview's macOS backend forwards a new window only
// for `WKNavigationTypeLinkActivated`, so a scripted `window.open` was silently
// dropped and the packaged Mac app sent nothing (#340).
// `source` is 'current' | 'best_fit' | 'as_imported'.
export async function sendToPhlynx(modelId, { source = 'current', values = {}, workingDir = '' } = {}) {
  const { data } = await axios.post(url('/api/phlynx/send'), {
    model_id: modelId,
    source,
    values,
    working_dir: workingDir,
  })
  return data
}

export async function uploadObsData(modelId, obsData, save = null) {
  const { data } = await axios.post(
    url('/api/obs_data/upload'),
    { model_id: modelId, obs_data: obsData },
    { params: save?.filename ? { filename: save.filename, working_dir: save.workingDir || '' } : {} },
  )
  return data
}

/** The study's obs_data as its file holds it, every key (null when it has none). */
export async function getObsDataDocument(modelId) {
  const { data } = await axios.get(url('/api/obs_data/document'), { params: { model_id: modelId } })
  return data.document
}

/**
 * A workflow step's unset parameters, from a refusal ({step_id, names}), or null: the
 * parameters nothing gives a value, which a protocol in the step's obs_data sets.
 */
export function unsetParameters(e) {
  return e?.response?.data?.detail?.unset_parameters ?? null
}

// Operation (obs_funcs) + cost_type (cost_func) option lists, sourced from
// circulatory_autogen — used to populate the obs_data editor dropdowns. Pass
// refresh=true to re-introspect CA (e.g. after adding a custom operation), and
// workingDir so the user's custom funcs (stored there) appear in the lists.
export async function getObsDataOptions(refresh = false, workingDir = '') {
  const params = {}
  if (refresh) params.refresh = true
  if (workingDir) params.working_dir = workingDir
  const { data } = await axios.get(url('/api/obs_data/options'), { params })
  return data
}

// User-authored observable operation & cost funcs (issues #58 / #104). CUFLynx
// saves them to a file in the user's output directory and points CA at it (CA
// #303). `kind` is 'operation' or 'cost' -> /api/{operation,cost}_funcs;
// `workingDir` is where the funcs live (working_dir).
const FUNC_ENDPOINT = { operation: 'operation_funcs', cost: 'cost_funcs' }

export async function getUserFuncs(kind = 'operation', workingDir = '') {
  const { data } = await axios.get(url(`/api/${FUNC_ENDPOINT[kind]}`), {
    params: workingDir ? { working_dir: workingDir } : {},
  })
  return data
}

export async function saveUserFunc(kind, name, source, workingDir = '') {
  const { data } = await axios.post(url(`/api/${FUNC_ENDPOINT[kind]}`), {
    name,
    source,
    working_dir: workingDir || '',
  })
  return data
}

export async function deleteUserFunc(kind, name, workingDir = '') {
  const { data } = await axios.delete(
    url(`/api/${FUNC_ENDPOINT[kind]}/${encodeURIComponent(name)}`),
    { params: workingDir ? { working_dir: workingDir } : {} },
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
    params: save?.filename ? { filename: save.filename, working_dir: save.workingDir || '' } : {},
  })
  return data
}

// Save the current slider values to a named file (issue #106). Format follows the
// filename extension: `.csv` -> CSV, else numpy `.npy` (default). `order` is the
// qname order for the npy array. Returns { path }.
export async function saveParams(values, order, filename, workingDir = '', result = null) {
  const { data } = await axios.post(url('/api/params/save'), {
    values,
    order,
    filename,
    working_dir: workingDir || '',
    // The traces these values produced, stored under the same prefix so the run
    // can be shown again later without re-running it (#126).
    ...(result ? { result } : {}),
  })
  return data
}

// Saved runs in an output directory (metadata only — no series). Issue #126.
export async function listSavedRuns(workingDir = '') {
  const { data } = await axios.get(url('/api/runs'), {
    params: workingDir ? { dir: workingDir } : {},
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

// --- Add from dataset: build obs_data from raw recordings -------------------

export async function getObsExtractFormats() {
  const { data } = await axios.get(url('/api/obs_extract/formats'))
  return data
}

// POST rather than GET: it carries an exclude list and per-dataset reader
// settings, which do not belong in a query string, and a scan must not be
// served from the browser cache after the directory has changed.
export async function scanDatasets(payload) {
  const { data } = await axios.post(url('/api/obs_extract/scan'), payload)
  return data
}

export async function loadObsExtractConfig(path) {
  const { data } = await axios.get(url('/api/obs_extract/config'), {
    params: { path },
  })
  return data
}

export async function saveObsExtractConfig(config, { workingDir = '', filename } = {}) {
  const { data } = await axios.post(url('/api/obs_extract/config'), {
    config,
    working_dir: workingDir,
    ...(filename ? { filename } : {}),
  })
  return data
}

export async function startObsExtract(config, { workingDir = '', modelId = '' } = {}) {
  const { data } = await axios.post(url('/api/obs_extract/run'), {
    config,
    working_dir: workingDir,
    model_id: modelId,
  })
  return data
}

export async function getObsExtractStatus(jobId, offset = 0) {
  const { data } = await axios.get(
    url(`/api/obs_extract/${jobId}/status?offset=${offset}`),
  )
  return data
}

export async function cancelObsExtract(jobId) {
  const { data } = await axios.post(url(`/api/obs_extract/${jobId}/cancel`))
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
export async function getEmulatorInfo(modelId, configWorkingDir = '') {
  const { data } = await axios.get(
    url(
      `/api/emulator/info?model_id=${encodeURIComponent(modelId)}` +
        `&working_dir=${encodeURIComponent(configWorkingDir || '')}`,
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
 * Posterior draws pushed back through the model, against the measurements.
 *
 * Fetched once when a run finishes rather than polled: it is a whole sweep of the model, so it
 * does not exist until the run is over and does not change afterwards. Everything comes back in
 * units of each measurement's own standard deviation -- scaled on the server, so two clients
 * cannot draw two different figures from one run.
 */
/**
 * Everything a finished run left in a working dir (#255).
 *
 * The panels are filled by job polls, so without this a run made outside the app -- by
 * cuflynx-param-id, by a generated run_pipeline.py, or by this app yesterday -- is invisible
 * even though every file is there. Returns `found` and `missing` alongside the data, so the
 * caller can say what it loaded rather than leaving empty panels to be interpreted.
 */
/**
 * Open the model, obs_data and params_for_id a finished run was made from (#255).
 *
 * Answers in the same shape as an archive upload, because the server loads them
 * through the same importer -- so the caller applies it exactly as it applies a
 * dropped .omex, rather than growing a second way to install a study.
 */
export async function openStudyFromOutputs(dir, runDir = null, filePrefix = null) {
  const { data } = await axios.post(url('/api/working-dir/study'), {
    dir,
    run_dir: runDir,
    file_prefix: filePrefix,
  })
  return data
}

/**
 * Which working dir a dropped `<study>_cuflynx_study.json` opens. `text` is
 * the file's contents; `path` is its location when the shell knows it (a browser
 * drop does not, and the manifest's own `study_dir` answers instead).
 */
export async function openStudyManifest(text, path) {
  const body = { content: text }
  if (path) body.path = path
  const { data } = await axios.post(url('/api/working-dir/manifest'), body)
  return data
}

// What a dropped .zip is: `{kind: 'omex'}`, or a zipped working dir staged on the
// server, `{kind: 'working_dir', token, study_name, default_dir}`. `path` is where the
// zip is, when the desktop shell exposes it: the proposed folder is then beside it.
export async function inspectWorkingDirZip(file, path) {
  const form = new FormData()
  form.append('file', file)
  const { data } = await axios.post(url('/api/working-dir/zip'), form, {
    params: path ? { zip_path: path } : {},
  })
  return data
}

// Extract a staged working-dir zip into `dest`. Answers like openStudyManifest, plus
// `warnings`. A 409 means `dest` already holds files: ask, then pass `overwrite`.
export async function extractWorkingDirZip(token, dest, overwrite = false) {
  const { data } = await axios.post(url('/api/working-dir/zip/extract'), {
    token,
    dest,
    overwrite,
  })
  return data
}

export async function loadWorkingDir(dir, filePrefix, runDir) {
  const params = new URLSearchParams({ dir })
  if (filePrefix) params.set('file_prefix', filePrefix)
  if (runDir) params.set('run_dir', runDir)
  const { data } = await axios.get(url(`/api/working-dir/load?${params.toString()}`))
  return data
}

export async function getUQPosteriorPredictive(jobId) {
  const { data } = await axios.get(url(`/api/uq/${jobId}/posterior-predictive`))
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

// --- Calibration workflows (circulatory_autogen's libcuflynx.calibration_workflow) ---

/** The open workflow: its file, which parameter of each step lands where, each step's state. */
export async function getWorkflow() {
  const { data } = await axios.get(url('/api/workflow'))
  return data
}

/** Open a calibration_workflow.json by path, or reopen a workflow run directory. */
export async function loadWorkflow({ path = '', runDir = '', workingDir = '' } = {}) {
  const { data } = await axios.post(url('/api/workflow/load'), {
    path,
    run_dir: runDir,
    working_dir: workingDir,
  })
  return data
}

/** Open an uploaded calibration_workflow.json (a browser cannot name its path). */
export async function uploadWorkflow(file, workingDir = '') {
  const form = new FormData()
  form.append('file', file)
  const { data } = await axios.post(url('/api/workflow/upload'), form, {
    params: workingDir ? { working_dir: workingDir } : {},
  })
  return data
}

export async function closeWorkflow() {
  const { data } = await axios.post(url('/api/workflow/close'))
  return data
}

/**
 * One tab of the workflow -- a step id, or 'target' -- as a study: the same response a
 * dropped .omex gives, plus `workflow_view` (fixed values, own results, what it waits for).
 */
export async function openWorkflowView(view) {
  const { data } = await axios.post(url('/api/workflow/view'), { view })
  return data
}

/** A workflow step's obs_data or params_for_id as the workflow names it: {path, content}. */
export async function getWorkflowStepInput(stepId, role) {
  const { data } = await axios.get(url('/api/workflow/step_input'), {
    params: { step_id: stepId, role },
  })
  return data
}

/**
 * Write a workflow step's obs_data (an object) or params_for_id (JSON, or CSV text the
 * server converts to JSON); a step naming
 * none is given a file. Returns {path, workflow}: the workflow described again.
 */
export async function saveWorkflowStepInput(stepId, role, content) {
  const { data } = await axios.post(url('/api/workflow/step_input'), {
    step_id: stepId,
    role,
    content: typeof content === 'string' ? content : JSON.stringify(content, null, 2),
  })
  return data
}

export async function runWorkflow({ fromStep = '', only = '', numCores = 1 } = {}) {
  const { data } = await axios.post(url('/api/workflow/run'), {
    from_step: fromStep,
    only,
    num_cores: numCores,
  })
  return data
}

export async function getWorkflowStatus(jobId, offset = 0) {
  const { data } = await axios.get(
    url(`/api/workflow/${encodeURIComponent(jobId)}/status?offset=${offset}`),
  )
  return data
}

/**
 * The open workflow as a COMBINE archive that reruns it: the target model, the workflow
 * packed by CA (every step's files and the modules it uses, no results) and a study
 * manifest naming it. Returns `{filename, download_url, member_count}`, for a link.
 */
export async function exportWorkflow(workingDir = '') {
  const { data } = await axios.post(url('/api/workflow/export'), { working_dir: workingDir })
  return data
}

export async function cancelWorkflow(jobId) {
  const { data } = await axios.post(url(`/api/workflow/${encodeURIComponent(jobId)}/cancel`))
  return data
}

// --- Variable mapping: the LaTeX symbol of every variable (libcuflynx.reporting) ---

/** Every variable of the model with its symbol, the file it is kept in, and its problems. */
export async function getVariableMapping(modelId, workingDir = '') {
  const { data } = await axios.get(
    url(`/api/models/${encodeURIComponent(modelId)}/variable_mapping`),
    { params: workingDir ? { working_dir: workingDir } : {} },
  )
  return data
}

/** Save edited symbols ([{variable_name, latex}]); the server writes the file through CA. */
export async function saveVariableMapping(modelId, rows, workingDir = '') {
  const { data } = await axios.put(
    url(`/api/models/${encodeURIComponent(modelId)}/variable_mapping`),
    { rows, working_dir: workingDir },
  )
  return data
}
