/**
 * The guided tour's step list: pure data plus two pure DOM predicates.
 *
 * `App.vue` wires this generically -- it hands the list and a `ctx` of getters
 * to `TourOverlay.vue` and never grows a `switch` over step ids. The overlay
 * *observes*: there is deliberately no `action`/`prep` field, so "wait for the
 * user to do the thing" is structural rather than a convention someone forgets.
 *
 * Step shape (only `id`, `target`, `text` and `side` are required):
 *   {
 *     id:        'obs-edit',                   // stable; the tests key off it
 *     target:    '[data-testid="obs-edit"]',   // ALWAYS a data-testid selector
 *     title:     'Observed data',              // optional heading
 *     text:      '…',
 *     side:      'left',                       // top | right | bottom | left
 *     advanceOn: { target, event },            // delegated listener; target
 *                                              //   defaults to step.target
 *     waitFor:   (ctx) => boolean,             // polled every 200 ms
 *     when:      (ctx) => boolean,             // false => step is skipped
 *   }
 *
 * **`target` is always a `data-testid` selector** -- never a class or a tag.
 * The testids are the anchors the rest of the suite already asserts on, and
 * `tourSteps.test.js` reads every `.vue` under `src/` to prove each one still
 * exists. A class selector would make the tour silently drift instead.
 *
 * A step needs no "is that tab open?" bookkeeping: the overlay skips any step
 * whose anchor is missing, `visibility: hidden`, or has a 0x0 rect -- which is
 * exactly what an inactive `v-show` pane's contents have.
 *
 * ---------------------------------------------------------------------------
 * Renumbering
 * ---------------------------------------------------------------------------
 * The list the user wrote had 28 entries and was renumbered into a clean 1..37.
 * `5.1`-`5.4` became 6-10: the gear plus three Settings popups, plus a dedicated
 * *close Settings* step so the tour is never left pointing at UI sitting behind
 * a modal. The duplicated `14`/`15` (save obs_data, edit params_for_id) became
 * 22-23. Each analysis section gained an explicit "click the tab" step, because
 * those panes are `v-show`n and the tour has to *ask* the user to open what it
 * is about to describe -- it cannot open it itself. **Nothing the user listed
 * was dropped.**
 *
 * Mapping: user 1-4 -> 1-4 · 5 -> 5 · 5.1 -> 6 · 5.2 -> 7 · 5.3-5.4 -> 8-9
 * (+10 close) · 6 -> 11 · 7 -> 12 · 8 -> 13 · 9,10 -> 14,15 · 11,12,13 ->
 * 16,17,18 · 14,15 (user-defined ops) -> 19,20,21 · 14dup -> 22 · 15dup -> 23 ·
 * 16,17,18 -> 24,25,26 · 19 -> 27 · 20 -> 28 · 21 -> 29 · 22 -> 30 · 23,24 ->
 * 31,32 · 25,26 -> 33,34 · 27,28 -> 35,36 · +37 closing step.
 */

/** Is an anchor on the page right now? Queries `document`, not `#app`: every
 *  PrimeVue dialog is portalled to `document.body`, outside the app root. */
export const present = (sel) => !!document.querySelector(sel)

/** The inverse, spelled out so a step reads as "wait until it is gone". */
export const gone = (sel) => !document.querySelector(sel)

const START_DIALOG = '[data-testid="start-dialog"]'
const EDIT_OBS = '[data-testid="edit-obs"]'
const EDIT_PARAMS = '[data-testid="edit-params"]'
const EDIT_OP_FUNCS = '[data-testid="edit-op-funcs"]'

export const TOUR_STEPS = Object.freeze([
  /* ---------------------------------------------------------------- *
   * 1-5  Getting a model
   * ---------------------------------------------------------------- */
  {
    id: 'create',
    target: '[data-testid="start-edit"]',
    side: 'left',
    title: 'Start with a model',
    text: 'Everything here hangs off a model. Click Create to see the ways to get one.',
    // With a model already loaded this button reads "Edit source" and does
    // something else entirely, so 1 guards itself -- and 2-5 evaporate with it,
    // because the Start dialog they live in never opens.
    when: (ctx) => !ctx.hasModel(),
    advanceOn: { event: 'click' },
    waitFor: () => present(START_DIALOG),
  },
  {
    id: 'phlynx',
    target: '[data-testid="start-build-your-own"]',
    side: 'right',
    title: 'Build your own',
    text: 'ODE models can be built in PhLynx, the model builder. It opens in a browser tab; the CellML it gives you is dropped back onto the model box here.',
  },
  {
    id: 'pmr',
    target: '[data-testid="start-pmr"]',
    side: 'right',
    title: 'Published models',
    text: 'Look for models to try out here. The Physiome Model Repository holds curated CellML — download one and drop it on the model box.',
  },
  {
    id: 'external',
    target: '[data-testid="start-external-python"]',
    side: 'right',
    title: 'External models',
    text: 'A PDE, an ODE with its own special solver — anything you already solve and want to calibrate, as long as Python can call it. Drop a .py with SIM_HELPER = MyClass at the bottom. The External Python tutorial has the whole contract.',
  },
  {
    id: 'example',
    target: '[data-testid="start-example-3compartment"]',
    side: 'right',
    text: "For now let's start from a model that is already prepared. Click 3-compartment circulation — it brings the model, its obs_data and its params_for_id in one archive.",
    advanceOn: { event: 'click' },
    waitFor: (ctx) => ctx.hasModel(),
  },

  /* ---------------------------------------------------------------- *
   * 6-10  Settings
   * ---------------------------------------------------------------- */
  {
    id: 'settings-open',
    target: '[data-testid="settings-open"]',
    side: 'bottom',
    title: 'Simulation settings live here',
    text: 'Open the gear.',
    advanceOn: { event: 'click' },
    waitFor: (ctx) => ctx.settingsOpen(),
  },
  {
    id: 'ca-dir',
    target: '[data-testid="ca-browse"]',
    side: 'right',
    title: 'CA dir',
    text: 'Simulations run through circulatory_autogen, and CUFLynx brings its own copy — there is nothing to set up here. This box is for development: if you are working on your own circulatory_autogen against libCUFLynx, point it at your checkout and runs use it from their next launch.',
  },
  {
    id: 'model-format',
    target: '[data-testid="model-format-select"]',
    side: 'right',
    title: 'Generated model format',
    text: 'This is the backend the model runs through: cellml → Myokit CVODE (needs a C compiler), python → scipy, casadi_python → CasADi. Changing it regenerates and re-runs the model.',
  },
  {
    id: 'solver',
    target: '[data-testid="solver-select"]',
    side: 'right',
    title: 'The solver and its settings',
    // The run window is the protocol's, not Settings' -- there is no pre-time
    // or sim-time control anywhere in the app to describe here.
    text: "Step size, tolerances, the random seed. Each field says what it does when you hover it. Time unit is the unit the protocol's times are in, and it labels the plots' time axis.",
  },
  {
    id: 'settings-close',
    target: '[data-testid="settings-dialog"]',
    side: 'left',
    // Exists purely so the tour is never left pointing at the sliders from
    // behind a modal mask. Either way out closes Settings: the user closes it
    // themselves (waitFor), or they press Next and it closes for them (onNext)
    // -- pressing Next and being walked on to a control behind the mask is the
    // one thing this step must not do.
    text: 'That is the lot. Close Settings to carry on.',
    waitFor: (ctx) => !ctx.settingsOpen(),
    onNext: (ctx) => ctx.closeSettings(),
  },

  /* ---------------------------------------------------------------- *
   * 11-12  The main screen
   * ---------------------------------------------------------------- */
  {
    id: 'sliders',
    target: '[data-testid="control-panel"]',
    side: 'right',
    title: 'Parameters',
    text: 'Here parameters can be changed easily in the model — one slider per row of params_for_id. Move one and the model re-runs. There is no Run button: runs fire on their own whenever a parameter changes.',
  },
  {
    id: 'plots',
    target: '[data-testid="plot-groups"]',
    side: 'left',
    text: 'Outputs of the model are shown here, one panel per plotted variable, with your observed data drawn over it.',
    when: (ctx) => ctx.centerTab() === 'plots',
  },

  /* ---------------------------------------------------------------- *
   * 13-22  obs_data: the protocol and the ground truth
   * ---------------------------------------------------------------- */
  {
    id: 'obs-edit',
    target: '[data-testid="obs-edit"]',
    side: 'left',
    text: 'This is where you edit the experimental protocol and the observable (ground truth) data you calibrate to. Click Edit.',
    advanceOn: { event: 'click' },
    waitFor: () => present(EDIT_OBS),
  },
  {
    id: 'protocol',
    target: '[data-testid="eo-protocol"]',
    side: 'right',
    title: 'The protocol',
    text: 'This is what the model is put through: one tab per experiment, and inside it the subexperiments run back to back. It is also the only source of the run window — nothing else in CUFLynx says how long a simulation is, and without a protocol nothing runs at all.',
  },
  {
    id: 'protocol-detail',
    target: '[data-testid="pre-time"]',
    side: 'right',
    text: 'Each subexperiment has a pre-time (run to settle, not recorded) and a duration. Add a controlled parameter and you can step, pulse or ramp it inside that window — the shape is drawn as you type it. These are the numbers every run uses, so the simulation reproduces your bench protocol rather than some fixed length picked elsewhere.',
  },
  {
    id: 'data-items',
    target: '[data-testid="eo-data-items"]',
    side: 'right',
    title: 'data_items are the ground truth',
    text: 'One row is one number (or one series) the model is scored against — the calibration cost is built from exactly these rows.',
    when: (ctx) => ctx.hasObsData(),
  },
  {
    id: 'data-item-row',
    target: '[data-testid="eo-value"]',
    side: 'right',
    text: 'On each row: the variable it produces, the measured value, and its std — the uncertainty, which is what weights the row in the cost. Then which experiment and subexperiment it came from.',
  },
  {
    id: 'data-item-detail',
    target: '[data-testid="eo-operation"]',
    side: 'right',
    text: 'The operation turns the simulated trace into that number — max, mean, a series comparison. Open a row with the chevron for its operands, its weight and its cost_type (how the mismatch is scored).',
  },
  {
    id: 'custom-funcs',
    target: '[data-testid="eo-add-op-func"]',
    side: 'left',
    text: 'If no built-in operation matches your measurement, write your own. Click Custom funcs.',
    advanceOn: { event: 'click' },
    waitFor: () => present(EDIT_OP_FUNCS),
  },
  {
    id: 'op-funcs-templates',
    target: '[data-testid="of-templates"]',
    side: 'right',
    title: 'User-defined operations',
    text: 'Two kinds: an operation (trace → number) and a cost (predicted vs observed → cost). Start from a template — the plain one, a multi-operand one, one with keyword arguments, a robust cost, a differentiable one.',
  },
  {
    id: 'op-funcs-save',
    target: '[data-testid="of-save"]',
    side: 'right',
    text: 'Edit the Python in the box and Save — it lands in your circulatory_autogen user files and appears in the operation list next to the built-ins. Mark it @differentiable if a gradient-based calibration should be allowed to use it. Close this dialog when you are done.',
    waitFor: () => gone(EDIT_OP_FUNCS),
  },
  {
    id: 'obs-save',
    target: '[data-testid="eo-save"]',
    side: 'right',
    title: 'Save',
    text: 'This writes a new dated obs_data.json and loads it — the plots pick up the new items straight away. Save, or close the dialog, to carry on.',
    waitFor: () => gone(EDIT_OBS),
  },

  /* ---------------------------------------------------------------- *
   * 23-27  params_for_id: which parameters are free
   * ---------------------------------------------------------------- */
  {
    id: 'params-edit',
    target: '[data-testid="params-edit"]',
    side: 'left',
    text: 'Now the other half: which parameters are free, and how far they may move. Click Edit on params_for_id.',
    advanceOn: { event: 'click' },
    waitFor: () => present(EDIT_PARAMS),
  },
  {
    id: 'params-choose',
    target: '[data-testid="ep-include"]',
    side: 'right',
    text: 'Tick Use for each parameter you want identified. Search to find them — every constant in the model is listed, and only the ticked ones go into the file.',
  },
  {
    id: 'params-range',
    target: '[data-testid="ep-min"]',
    side: 'right',
    text: 'min / max are the bounds the calibration searches in, and the range a UQ prior spans. Keep them wide enough to contain the answer and narrow enough to be physiological. Where the backend offers priors, pick the distribution here.',
  },
  {
    id: 'params-modifier',
    target: '[data-testid="ep-create-modifier"]',
    side: 'right',
    title: 'Modifier functions',
    text: 'These let one identified parameter drive several model constants — select the rows, then create a modifier (a scale, an offset) so the calibration fits one handle instead of five.',
  },
  {
    id: 'params-save',
    target: '[data-testid="ep-save"]',
    side: 'right',
    text: 'Save writes a new dated params_for_id JSON and loads it — the sliders on the left rebuild from it. Save, or close the dialog, to carry on.',
    waitFor: () => gone(EDIT_PARAMS),
  },

  /* ---------------------------------------------------------------- *
   * 28-37  Emulator, sensitivity, calibration, UQ, analysis
   *
   * Every pane below is `v-show`n, so each section opens with a step that asks
   * the user to click its tab -- the tour cannot click it for them. Those steps
   * skip themselves when the tab is already the open one.
   * ---------------------------------------------------------------- */
  {
    id: 'emulator-tab',
    target: '[data-testid="tab-emulator"]',
    side: 'right',
    title: 'Emulator',
    text: 'Click the tab.',
    when: (ctx) => ctx.leftTab() !== 'emulator',
    advanceOn: { event: 'click' },
  },
  {
    id: 'emulator-what',
    target: '[data-testid="emu-settings"]',
    side: 'right',
    text: 'An emulator is a cheap stand-in for the model: it is trained on a sample of runs and then predicts the data_items in milliseconds instead of seconds. Choose how many samples to spend and press Train. It reports a held-out R² per output, which is how you decide whether to trust it.',
  },
  {
    id: 'emulator-use',
    target: '[data-testid="emu-use-row"]',
    side: 'right',
    // Not gated on ctx.hasEmulator(): the greyed-out state is exactly what the
    // copy explains, so the step is worth seeing before anything is trained.
    text: 'This button makes all other simulations use the created emulator — sensitivity, calibration and UQ evaluate it instead of the solver. It stays greyed out until an emulator has been trained, and the sliders keep using the real solver so you can always see the gap.',
  },
  {
    id: 'sensitivity-tab',
    target: '[data-testid="tab-sensitivity"]',
    side: 'right',
    title: 'Sensitivity',
    text: 'This answers "which parameters actually matter?" before you spend a calibration finding out. Click the tab.',
    when: (ctx) => ctx.leftTab() !== 'sensitivity',
    advanceOn: { event: 'click' },
  },
  {
    id: 'sensitivity-run',
    target: '[data-testid="sa-settings"]',
    side: 'right',
    text: 'Pick the method and the sample count, then Run. The result lands in Analysis as a per-output, per-parameter table — parameters that move nothing are candidates for unticking in params_for_id.',
  },
  {
    id: 'calibration-tab',
    target: '[data-testid="tab-calibration"]',
    side: 'right',
    title: 'Calibration',
    text: 'This fits the ticked parameters to your data_items. Click the tab.',
    when: (ctx) => ctx.leftTab() !== 'calibration',
    advanceOn: { event: 'click' },
  },
  {
    id: 'calibration-run',
    target: '[data-testid="calib-settings"]',
    side: 'right',
    text: 'Choose the method, the budget and (for the gradient methods) where the gradient comes from, then Run. Progress shows the cost coming down live; when it finishes the best-fit parameters are pushed onto the sliders so you can see the fit.',
  },
  {
    id: 'uq-tab',
    target: '[data-testid="tab-uq"]',
    side: 'right',
    title: 'UQ',
    text: 'This goes past a single best fit to the distribution of parameters the data supports. Click the tab.',
    when: (ctx) => ctx.leftTab() !== 'uq',
    advanceOn: { event: 'click' },
  },
  {
    id: 'uq-run',
    target: '[data-testid="uq-settings"]',
    side: 'right',
    text: 'Set the chains and samples, then Run. Long runs are what the emulator is for. The posterior and its intervals appear in Analysis.',
  },
  {
    id: 'analysis',
    target: '[data-testid="tab-analysis"]',
    side: 'bottom',
    title: 'Analysis',
    text: "This collects the results of all three. That's the tour — press the Tutorial button any time to run it again.",
  },
])
