/**
 * The PhLynx half of the CUFLynx -> PhLynx -> CUFLynx round trip.
 *
 * Runs PhLynx's *real* modules out of a checkout, rather than transcribing them:
 * a transcription is only true until PhLynx moves. Point PHLYNX_DIR at the
 * checkout (installed with `yarn install`); everything imported below is theirs,
 * loaded by PhLynx's own Vite.
 *
 * Reads the archive CUFLynx would send on argv[2], writes what PhLynx would send
 * back to argv[3], and prints a JSON report on stdout for the Python test to
 * assert against.
 *
 * DOM: jsdom, deliberately, and NOT happy-dom (which PhLynx's own vitest uses).
 * happy-dom's selector engine does not match tag names containing an underscore,
 * so `querySelectorAll('map_variables')` returns 0 where getElementsByTagName
 * returns the real count -- which silently reduces PhLynx's connection parser to
 * zero nodes for every model, including PhLynx's own. A test that ran under
 * happy-dom would "pass" while measuring nothing.
 */
import { readFile, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { pathToFileURL } from 'node:url'

const PHLYNX_DIR = process.env.PHLYNX_DIR
const WEB_DIR = process.env.CUFLYNX_WEB_DIR
const [, , inPath, outPath] = process.argv

// The two halves live in different installs: vite in PhLynx's, jsdom in
// CUFLynx's web app. ESM ignores NODE_PATH, so each is resolved from its own.
const fromPhlynx = createRequire(`${PHLYNX_DIR}/package.json`)
const fromWeb = createRequire(`${WEB_DIR}/package.json`)

// Load a package by its ESM entry point: `require.resolve` hands back the
// CommonJS entry, which an ESM-only package (vite) does not have.
async function load(req, name) {
  const pkgPath = req.resolve(`${name}/package.json`)
  const pkg = JSON.parse(await readFile(pkgPath, 'utf8'))
  const entry = pkg.exports?.['.']?.import?.default
    ?? pkg.exports?.['.']?.import
    ?? pkg.module
    ?? pkg.main
  const base = pathToFileURL(pkgPath)
  return import(typeof entry === 'string' ? new URL(entry, base).href : pathToFileURL(req.resolve(name)).href)
}

const { JSDOM } = await load(fromWeb, 'jsdom')
const dom = new JSDOM('<!doctype html><html></html>')
globalThis.DOMParser = dom.window.DOMParser
globalThis.Node = dom.window.Node

// PhLynx is a Vite app: extensionless imports, `.json` imports without an import
// attribute, `import.meta.glob`, its aliases and `define`s. So its modules are
// loaded through its own Vite with its own config, as `yarn dev` would load them,
// rather than by plain Node, which handles none of those. Its packages (pinia,
// jszip) come through a module of PhLynx's root importing them, so they are the
// instances PhLynx's code gets: a second pinia would leave PhLynx's store with
// "no active Pinia".
const PACKAGES = 'virtual:cuflynx-bridge-packages'
const packagesModule = {
  name: 'cuflynx-bridge-packages',
  resolveId: (id) => (id === PACKAGES ? `\0${PACKAGES}` : undefined),
  load: (id) => (id === `\0${PACKAGES}`
    ? "export { setActivePinia, createPinia } from 'pinia'\nexport { default as JSZip } from 'jszip'\n"
    : undefined),
}
const { createServer } = await load(fromPhlynx, 'vite')
const vite = await createServer({
  root: PHLYNX_DIR,
  configFile: `${PHLYNX_DIR}/vite.config.js`,
  plugins: [packagesModule],
  server: { middlewareMode: true, hmr: false, ws: false },
  appType: 'custom',
  logLevel: 'error',
  // Packages are Node's to load: Vite would inline a CommonJS one (jszip) as ESM.
  ssr: { external: true },
})
const phlynx = (path) => vite.ssrLoadModule(path)

const { extractOmexArchive, importOmexFile } = await phlynx('/src/services/import/omex.js')
const { parseCellMLConnections } = await phlynx('/src/services/import/parseCellmlConnections.js')
const { generateOmexArchive } = await phlynx('/src/services/compress.js')
const { useOmexStore } = await phlynx('/src/stores/omexStore.js')
const { setActivePinia, createPinia, JSZip } = await phlynx(PACKAGES)

setActivePinia(createPinia())

const buf = await readFile(inPath)
// Copy into this realm's ArrayBuffer: PhLynx's importer does an `instanceof`
// check, and a Buffer's own .buffer is not always the same realm's type.
const ab = new ArrayBuffer(buf.length)
new Uint8Array(ab).set(buf)

// --- PhLynx receives it ---
const payload = await extractOmexArchive(new Map([['omex', new Map([['study.omex', { isValid: true, payload: ab }]])]]))
const result = await importOmexFile(payload.omex, () => {})

// --- PhLynx puts it on the canvas ---
const zip = await JSZip.loadAsync(ab)
const cellmlText = await zip.file(result.files.cellml).async('string')
const parsed = parseCellMLConnections(cellmlText, result.files.cellml)

// --- PhLynx sends it back ---
// `preservedExtras` is what WorkspaceArea.vue keeps: every member that is not
// one of the four PhLynx owns.
const critical = [result.files.cellml, result.files.simulationJson, result.files.sedml, result.files.flowSnapshot].filter(Boolean)
const extras = []
for (const e of result.extras) {
  if (critical.includes(e.location)) continue
  const f = zip.file(e.location)
  if (f) extras.push({ location: e.location, format: e.format, payload: await f.async('arraybuffer') })
}
useOmexStore().preservedExtras = extras

const returned = await generateOmexArchive(
  { blob: cellmlText },
  // compress.js writes this straight into the zip, so it must already be text.
  JSON.stringify({ nodeData: parsed.nodes, edgeData: parsed.edges, mathLibrary: {} }),
  { simulationSettings: { initialPoint: 0, startingPoint: 0, endingPoint: 1, pointInterval: 0.01 } },
  {
    cellmlFileName: result.files.cellml,
    // buildSimulationJson reads `voi` before its own empty-selection guard.
    extractedData: { voi: { componentName: 'environment', name: 'time', units: 'second' }, mappedParameters: {} },
  },
)
const blob = returned?.blob ?? returned
const bytes = blob instanceof Uint8Array ? blob : new Uint8Array(await blob.arrayBuffer())
await writeFile(outPath, bytes)

console.log(JSON.stringify({
  opened: Boolean(result.files.cellml),
  cellml: result.files.cellml,
  flow_snapshot: result.files.flowSnapshot,
  extras_in: result.extras.map((e) => e.location),
  canvas_nodes: parsed.nodes.map((n) => n?.data?.name ?? n?.name ?? n?.id),
  canvas_edges: parsed.edges.length,
  returned_members: Object.keys((await JSZip.loadAsync(bytes)).files),
}))
await vite.close()
