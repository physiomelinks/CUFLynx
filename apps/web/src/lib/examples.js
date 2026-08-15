// PhLynx — the sibling model-builder web app. "Create" links here to build a
// model from scratch; "Edit" opens the current model there.
export const PHLYNX_URL = 'https://www.phlynx.com'

// The Physiome Model Repository — browse/download existing CellML models to drop in.
export const PMR_URL = 'https://models.physiomeproject.org'

// The External Python tutorial: how to write the solver class CUFLynx drives,
// where its dependencies have to be installed, and the FEniCSx worked example.
// Linked from the "Start" dialog rather than duplicated in the UI — the contract
// is a page of code, not a tooltip.
export const EXTERNAL_PYTHON_TUTORIAL_URL =
  'https://github.com/physiomelinks/CUFLynx/blob/main/tutorials/docs/external_python.md'

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
