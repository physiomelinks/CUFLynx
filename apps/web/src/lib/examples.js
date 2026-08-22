// PhLynx — the sibling model-builder web app. "Create" links here to build a
// model from scratch; "Edit" sends the current study there.
export const PHLYNX_URL = 'https://www.phlynx.com'

// How PhLynx takes a model from a link. Its `useLoadFromUrl` reads `?open=<keyword>`
// to pick a loader and hands that loader the raw URL fragment; `urlLoaders.js`
// registers the keyword below for a COMBINE archive.
//
// The fragment is **bare base64**, not a data URI: PhLynx's `base64ToBlob` builds
// `data:application/zip;base64,<payload>` itself, so a data URI would arrive
// double-prefixed and fail to decode. (Upstream's answer on #290 says a data URI
// will be passed — that is not what the loader does today, so this is the one
// place to change if they land the tolerant version.)
export function phlynxOpenUrl(base64) {
  return `${PHLYNX_URL}/?open=omex#${base64}`
}

// The Physiome Model Repository — browse/download existing CellML models to drop in.
export const PMR_URL = 'https://models.physiomeproject.org'

// The External Python tutorial: how to write the solver class CUFLynx drives,
// where its dependencies have to be installed, and the FEniCSx worked example.
// Linked from the "Start" dialog rather than duplicated in the UI — the contract
// is a page of code, not a tooltip.
export const EXTERNAL_PYTHON_TUTORIAL_URL =
  'https://github.com/physiomelinks/CUFLynx/blob/main/tutorials/docs/external_python.md'

// The section of that tutorial that lists what has to be installed into the
// chosen interpreter — including the `[emulation]` extra the Emulator tab needs.
// Linked from the Emulator tab when autoemulate is missing, so the fix is one
// click from the place the problem is reported.
export const EXTERNAL_PYTHON_INSTALL_URL = `${EXTERNAL_PYTHON_TUTORIAL_URL}#installing-the-models-dependencies`

// Example studies the "Start" dialog offers. Data-driven so PMR models (and
// other bundled examples) slot in later without touching the UI. Each `name`
// maps to a backend `GET /api/examples/{name}` route; `filename` is the display
// name given to the fetched File.
//
// They are COMBINE archives (.omex), not loose CellML: an example is a study —
// model, obs_data and params_for_id — and the archive is what carries all three
// through one click (#180).
export const EXAMPLE_MODELS = [
  {
    name: '3compartment',
    label: '3-compartment circulation',
    filename: '3compartment.omex',
  },
]
