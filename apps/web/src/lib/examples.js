// PhLynx — the sibling model-builder web app. "Create" links here to build a
// model from scratch; "Edit" opens the current model there.
export const PHLYNX_URL = 'https://www.phlynx.com'

// Example CellML models the "Start" dialog offers. Data-driven so PMR models
// (and other bundled examples) slot in later without touching the UI. Each
// `name` maps to a backend `GET /api/examples/{name}` route; `filename` is the
// display name given to the fetched File.
export const EXAMPLE_MODELS = [
  {
    name: '3compartment',
    label: '3-compartment circulation',
    filename: '3compartment_flat.cellml',
  },
]
