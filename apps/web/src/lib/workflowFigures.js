/**
 * The files a workflow's figure steps drew (CA #705), as the described status lists them:
 * each done figure step's `figures`, paths relative to its step directory, served by
 * `GET /api/workflow/figure`. An image is shown; any other file (a CSV of scores) is linked.
 */

const IMAGE = /\.(png|jpe?g|gif|svg|webp)$/i

/** The URL of the file `name` that figure step `step` drew; `version` (when it finished)
 * makes a figure drawn again load anew. */
export function figureUrl(step, name, version = '') {
  const q = new URLSearchParams({ step, name })
  if (version) q.set('v', version)
  return `/api/workflow/figure?${q}`
}

/**
 * The function a figure step draws with, `<script>:<function>`, as its name alone
 * (`sweep_traces`): the file's `function`, else CA's plan's `figure.function`.
 */
export function figureFunction(step) {
  const spec = step?.function ?? step?.figure?.function ?? ''
  return String(spec).split(':').pop()
}

/** Whether a figure file is shown as an image (else linked). */
export function isImage(name) {
  return IMAGE.test(String(name))
}

/**
 * Every figure step that has drawn, in the workflow's order:
 * `[{step, function, files: [{name, url, image}]}]`. `steps` are the file's, `results` CA's
 * status rows by step id.
 */
export function workflowFigures(steps, results) {
  const out = []
  for (const s of steps ?? []) {
    const row = results?.[s.id]
    if (s.kind !== 'figure' || !row?.figures?.length) continue
    out.push({
      step: s.id,
      function: figureFunction(s),
      files: row.figures.map((name) => ({
        name: name.split('/').pop(), url: figureUrl(s.id, name, row.finished ?? ''), image: isImage(name),
      })),
    })
  }
  return out
}
