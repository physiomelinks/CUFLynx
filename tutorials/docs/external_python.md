# External Python models

Everything here is optional reading — the app runs without any of it. See the
[README](../../README.md) to download and start it, and [Using CUFLynx](basic.md)
for the CellML and Myokit paths.

## What they are

CUFLynx normally takes a *model description* — a CellML file, or a Myokit `.mmt` —
and generates a solver from it. An **external python model** inverts that: you
bring the solver. It is a single `.py` file holding a class that owns its own time
stepping, and CUFLynx drives it the same way it drives a generated model — sliders
change its parameters, plots redraw, and calibration, sensitivity and UQ all work
against it unchanged.

That is the right shape for a model that already has a solver and would be ruined
by being rewritten as a right-hand side:

- a finite-element code (the worked example below is FEniCSx/dolfinx),
- a compiled library behind a thin Python binding,
- a scheme whose particular time stepping *is* the model.

> Not to be confused with circulatory_autogen's `python_user_defined` model type,
> where you write the RHS and CA integrates it with scipy `solve_ivp`. Here CA
> integrates nothing: it hands over `dt` / `sim_time` / `pre_time`, asks for a run,
> and reads the traces back. In CA's vocabulary this is
> `model_type: external_python` with `solver: external`.

Drop the `.py` on the model box, exactly where a `.cellml` goes. CUFLynx reads the
class's declared parameters and outputs, gives you a slider per parameter and a
plot per output, and locks **Settings → Generated model format** to
`external_python` — the file *is* the solver, so there is nothing to generate and
no other backend that could run it. Load a CellML model and the choice comes back.

## The contract

Your file declares one class and registers it at module level:

```python
class MyModel:
    parameters = {"heat/k": 0.05, "heat/u_D": 0.25}  # literal dict: name -> default
    output_names = ["heat/T_p1", "heat/T_p2", "heat/T_p3"]

    def init_solver(self, config): ...   # once; config: dt, sim_time, pre_time, start_time, solver_info
    def update_times(self, dt, start_time, sim_time, pre_time): ...
    def set_param_vals(self, param_dict): ...
    def run(self): ...                   # return False if diverged
    def get_results(self): ...           # {name: 1D numpy array, length N+1}
    def extra_plots(self): ...           # optional; list of matplotlib Figures

SIM_HELPER = MyModel
```

**Every name is `component/variable`.** That is not decoration: a
`params_for_id` row addresses a parameter as `vessel_name` + `param_name`, and an
obs_data operand names an output the same qualified way. An unqualified `k` cannot
be referred to by either, so it is rejected up front rather than at your first
calibration.

### The declarations

| Attribute | Rule |
|---|---|
| `parameters` | A **literal** dict of `"component/variable" -> number`. The values are the defaults: they seed the sliders and are what a parameter is reset to. |
| `output_names` | A **literal** list of `"component/variable"` names. Anything you want to plot, observe or calibrate against must be here. Empty is an error — the model would produce nothing observable. |
| `SIM_HELPER` | The class object itself at module level. Not an instance, not the name as a string. |

Literal matters because CUFLynx reads both attributes **by parsing the file, not by
importing it**. That is what lets the parameter table appear the moment you drop
the `.py`, on a machine that may not even have your solver's dependencies
installed, and without running any of your code as a side effect of a drop.

`SIM_HELPER` is an explicit registration rather than "the only class in the file"
so your file is free to define helper classes.

### The methods

**`init_solver(self, config)`** — called once. Put the expensive setup here: build
the mesh, compile the forms, open the library. `config` is a dict with `dt`,
`sim_time`, `pre_time`, `start_time` and `solver_info`.
`solver_info["user_config"]` is a free-form block that is passed through
untouched — a mesh resolution, a tolerance, a device. It is free-form because what
an external solver needs to be told is not something CA can enumerate, and it is
edited as JSON in **Settings → User config (JSON)**: `{"nx": 32}` on the example
below doubles the mesh resolution without touching the file.

**`update_times(self, dt, start_time, sim_time, pre_time)`** — set the output
grid, and nothing else. It is called whenever the run window changes, so it must
be cheap: re-assembling here means paying for it on every protocol experiment.
`run()` then produces samples at `start_time + i*dt` for `i = 0..N`, where

```
N = int(pre_time/dt) + int(sim_time/dt)
```

Use exactly that arithmetic, not your own rounding: CA uses it to size the trace,
and the two must agree exactly rather than approximately.

**`set_param_vals(self, param_dict)`** — `{name: value}` for the parameters being
changed. This runs on every slider drag and on every calibration sample, so it
must never re-initialise: keep the calibratable quantities somewhere you can write
in place (in FEniCSx, a `fem.Constant` already baked into the compiled form).
Raise on a name you do not know rather than silently ignoring it.

**`run(self)`** — solve the whole grid, including the pre-time. **Return `False`
if the solve diverged** (or produced non-finite values) instead of raising:
calibration explores parameter values that do not work, and a `False` is a sample
scored as bad, while an exception is a run that stops. Make it repeatable — reset
to your initial condition at the top — because one instance is reused for
thousands of samples.

**`get_results(self)`** — `{output_name: 1D numpy array}`, each of length `N + 1`,
covering the whole grid **including the pre-time samples**. CA discards the leading
`int(pre_time/dt)` of them itself; do not shift or trim them yourself.

**`extra_plots(self)`** *(optional)* — a list of `matplotlib.figure.Figure`
objects. This is the escape hatch for everything a time series cannot show: a 2D
field, a mesh, a phase portrait over the domain. CUFLynx renders them to PNG after
every completed run and shows them as extra cells at the end of the **Output
plots** tab, beside the traces, refreshed each run. Build `Figure` objects
directly rather than going through `pyplot` — no global state and no backend to
configure, which is what keeps it safe on a headless machine.

Three more are optional and substituted for when absent:
`get_init_param_vals(self, names)` (the declared defaults, in the order asked for),
`reset(self)` (back to the initial condition) and `close(self)` (release
resources).

## Installing the model's dependencies

**This is the step to get right.** CUFLynx bundles what a *CellML* model needs —
Myokit, libCellML, CasADi, numpy, scipy, pandas. It cannot bundle what *your*
model needs, and FEniCSx in particular is a conda-forge stack that no application
can carry around inside itself.

So an external python model runs in **the interpreter you pick in Settings**, and
that interpreter has to be one where `import` of your model's dependencies works.
One choice covers both tiers — the live sliders and the calibration / sensitivity /
UQ runs — so there is exactly one environment to get right.

### 1. Build the environment

For the FEniCSx example below:

```bash
conda create -n fenicsx -c conda-forge fenics-dolfinx python=3.11
conda activate fenicsx
```

`fenics-dolfinx` brings dolfinx, petsc4py, mpi4py and numpy. Add what the plots
and the pipeline need on top:

```bash
pip install matplotlib numpy
```

`python=3.11` is not incidental: it is inside the `>=3.10,<3.13` window the
emulator's `autoemulate` requires (step 2), so this environment can run every tab
of the app. A 3.9 or 3.13 environment would work for everything except emulation.

### 2. Put circulatory_autogen's dependencies in the same environment

That environment is also where CUFLynx's runs execute, so it needs what
circulatory_autogen imports (numpy, pandas, pyyaml, matplotlib, scipy, emcee,
SALib, nevergrad, mpi4py, tqdm, …). From your circulatory_autogen checkout:

```bash
conda activate fenicsx
pip install -e ".[emulation]"
```

Installing CA itself is the easiest way to get its dependency list right; CUFLynx
still uses the checkout you point it at, so an editable install is the friendly
option. If you would rather not install CA, install its dependencies by hand — but
a missing one shows up as a failed run rather than as a warning.

**Take the `[emulation]` extra.** It is what the **Emulator** tab needs, and it is
*not* part of the plain install: it pulls `autoemulate`, and with it torch,
gpytorch, pyro-ppl and lightgbm, plus a Python floor of 3.10 that the rest of CA
does not have — which is exactly why circulatory_autogen keeps it optional rather
than making every calibration carry a deep-learning stack. Without it, CUFLynx's
**Emulator** tab is drawn in orange and, when opened, shows only an explanation of
how to enable it — no settings, no Train button. Everything else keeps working.

The other extras circulatory_autogen offers, so you can tell which you want:

| Extra | Brings | Take it if |
|---|---|---|
| `emulation` | `autoemulate` (surrogate models) | You want the **Emulator** tab. Needs Python `>=3.10,<3.13`. |
| `uq` | `pymc` | You want the **UQ** tab's pyMC sampler. The default sampler, emcee, is a core dependency and needs nothing extra. |
| `dev` | pytest, black, flake8, mypy | You are *developing* circulatory_autogen and running its test suite. Not needed to use CUFLynx. |
| `docs` | mkdocs-material, mkdocstrings | You are building CA's documentation. |

They combine, so a contributor who also wants surrogates writes
`pip install -e ".[dev,emulation]"`. A user of CUFLynx wants `[emulation]` alone;
`dev` adds nothing the app uses.

Do **not** install these into the app's own bundled environment and expect it to
help: that is not where an external python model runs.

### 3. Point CUFLynx at it

Open **Settings** (the gear icon):

- **Python** — choose the `fenicsx` environment's interpreter (browse to
  `…/miniconda3/envs/fenicsx/bin/python`, or `Scripts\python.exe` on Windows).
  This is the interpreter that will import your model file. Choosing an
  environment without your dependencies is the one failure this tutorial exists to
  prevent: the error you get is your own `ImportError`, reported back through the
  app.
- **CA dir** — the circulatory_autogen checkout to run through, if it is not the
  default.
- **Generated model format** — nothing to do. With an external python model loaded
  it reads `external_python` and is locked, and the solver is `external`.

Changing the interpreter restarts the simulation worker, so the next run picks it
up. The **Emulator** tab is re-checked against the new interpreter at the same
moment — it turns orange if the environment you just chose has no `autoemulate`,
without a restart.

## The worked example: a FEniCSx heat equation

This is the flagship example of the backend and it ships with circulatory_autogen
under **`funcs_user/heat_fenics/`** — model, obs_data and params_for_id. The
version below is the same model, trimmed to what a tutorial needs; the shipped
file adds version-drift handling for the handful of dolfinx calls whose names have
moved between releases (`FunctionSpace` → `functionspace`, `BoundingBoxTree` →
`bb_tree`, `fem.set_bc` → `fem.petsc.set_bc`), which is worth reading if you are
on a different dolfinx than 0.8.x / 0.9.x.

**What it solves.** Backward Euler for `u_t = k Δu` on the unit square, P1
Lagrange on a 16×16 mesh: a uniformly hot plate (`u = 1` everywhere) quenched
through its boundary, with the **left edge held at the calibratable `u_D`** and the
bottom, top and right edges held at a fixed `0`. Weak form, with `u_n` the previous
step:

```
∫ u v dx + dt·k ∫ ∇u·∇v dx  =  ∫ u_n v dx
```

**Two parameters**, both `fem.Constant` so that changing them is an in-place write
to `.value` and never a recompilation of the form:

| Parameter | Meaning | Default |
|---|---|---|
| `heat/k` | diffusivity, in the stiffness term | 0.05 |
| `heat/u_D` | the Dirichlet value on the **left** edge | 0.25 |

**Three outputs**, the field sampled at three probe points every step:
`heat/T_p1` at (0.25, 0.25), `heat/T_p2` at (0.5, 0.5) and `heat/T_p3` at
(0.75, 0.75). Because only the left edge is driven, the three carry independent
information: p1 is nearest it and answers mostly to `u_D`, p3 is furthest and
answers mostly to `k`. p1 must run warmer than p3 whenever `u_D > 0` — a free
correctness check every time you run it. (`u_D` defaults to 0.25 rather than 0 for
the same reason: at `u_D = 0` every edge is identical, p1 and p3 coincide, and the
structure the example exists to show disappears.)

**Time scales.** The slowest mode decays at `λ = 2kπ² ≈ 19.7k`, so across the
`k ∈ [0.001, 0.2]` calibration box the plate's time constant runs from about 51 s
down to 0.25 s. Hence `dt = 0.02` and `sim_time = 2.0`: 100 steps, about two time
constants at the default `k = 0.05`, with `k = 0.01` leaving the plate partly
cooled and `k = 0.2` fully relaxing it — and still milliseconds per run once the
forms are compiled.

Below about `k = 0.005` the plate barely cools on this window (at `k = 0.001` it
keeps ~96% of its heat) and every observable saturates at the initial temperature.
That is what a lower bound should say — "no diffusion" — and a calibration rules it
out at once, but it is a flat patch of the cost surface, so lengthen `sim_time` if
you want that end of the box to be informative.

**MPI.** The mesh is built on `MPI.COMM_SELF`, not `COMM_WORLD`. CA parallelises
over *independent simulations* — each rank runs its own parameter sample — so every
rank needs a complete serial mesh. On `COMM_WORLD` one mesh would be distributed
across the ranks and it would deadlock the moment two ranks asked for different
parameters.

### heat_fenics_model.py

```python
"""A FEniCSx (dolfinx) heat-equation solver as a CUFLynx external python model."""
import numpy as np

from mpi4py import MPI
import ufl
import dolfinx
from dolfinx import fem
from dolfinx import mesh as dmesh
from dolfinx import geometry as dgeometry
from dolfinx.fem import petsc as fem_petsc
from petsc4py import PETSc

#: Where the three probes sit, in the order they appear in output_names.
PROBE_POINTS = ((0.25, 0.25), (0.5, 0.5), (0.75, 0.75))
INITIAL_TEMP = 1.0     # uniformly hot plate
FIXED_TEMP = 0.0       # bottom, top and right edges
DEFAULT_NX = 16


class HeatFEniCSxModel:
    """Transient heat conduction on the unit square, solved with dolfinx."""

    # Literal values only: CUFLynx reads these by parsing the file, without
    # importing it, so the parameter table appears before dolfinx is ever loaded.
    parameters = {"heat/k": 0.05, "heat/u_D": 0.25}
    output_names = ["heat/T_p1", "heat/T_p2", "heat/T_p3"]

    def __init__(self):
        self._samples = None
        self._snapshot_mid = None
        self._snapshot_final = None
        self._snapshot_mid_time = None

    # --- required: one-off setup -------------------------------------------
    def init_solver(self, config):
        """Mesh, function space, forms and probe cells -- built once."""
        user_config = (config.get('solver_info') or {}).get('user_config') or {}
        nx = int(user_config.get('nx', DEFAULT_NX))
        ny = int(user_config.get('ny', nx))

        # COMM_SELF: one complete mesh per rank -- CA parallelises over samples.
        self._mesh = dmesh.create_unit_square(MPI.COMM_SELF, nx, ny)
        self._V = fem.functionspace(self._mesh, ('Lagrange', 1))

        scalar = dolfinx.default_scalar_type
        self._k_const = fem.Constant(self._mesh, scalar(self.parameters['heat/k']))
        self._uD_const = fem.Constant(self._mesh, scalar(self.parameters['heat/u_D']))
        # dt is a Constant in the form too, so update_times is an in-place write
        # and never triggers a recompilation of the generated kernels.
        self._dt_const = fem.Constant(self._mesh, scalar(1.0))

        u = ufl.TrialFunction(self._V)
        v = ufl.TestFunction(self._V)
        self._u_n = fem.Function(self._V, name='u_n')
        self._uh = fem.Function(self._V, name='u')

        a = (u * v * ufl.dx
             + self._dt_const * self._k_const * ufl.inner(ufl.grad(u), ufl.grad(v)) * ufl.dx)
        self._a_form = fem.form(a)
        self._L_form = fem.form(self._u_n * v * ufl.dx)

        self._bcs = self._make_boundary_conditions()
        self._set_initial_condition()

        self._dof_coords = self._V.tabulate_dof_coordinates()[:, :2].copy()
        self._locate_probes()

        self._solver = PETSc.KSP().create(self._mesh.comm)
        self._solver.setType(PETSc.KSP.Type.PREONLY)
        self._solver.getPC().setType(user_config.get('petsc_pc', PETSc.PC.Type.LU))
        self._matrix = None
        self._rhs_vector = None

        self.update_times(config['dt'], config.get('start_time', 0.0),
                          config['sim_time'], config.get('pre_time', 0.0))

    def _make_boundary_conditions(self):
        """Left edge at the calibratable u_D; bottom, top and right at FIXED_TEMP.

        Facets are located geometrically -- on a unit square "x == 0" is exactly
        what distinguishes the driven edge, and that needs no MeshTags. The two
        corners at (0, 0) and (0, 1) lie on both sets: the boundary value there is
        genuinely discontinuous, so the fixed dofs are filtered to exclude the
        left-edge ones, giving the corners to u_D. Arbitrary, but deterministic
        and written down.
        """
        tdim = self._mesh.topology.dim
        fdim = tdim - 1
        self._mesh.topology.create_connectivity(fdim, tdim)

        left_facets = dmesh.locate_entities_boundary(
            self._mesh, fdim, lambda x: np.isclose(x[0], 0.0))
        rest_facets = dmesh.locate_entities_boundary(
            self._mesh, fdim,
            lambda x: (np.isclose(x[0], 1.0) | np.isclose(x[1], 0.0)
                       | np.isclose(x[1], 1.0)))

        left_dofs = fem.locate_dofs_topological(self._V, fdim, left_facets)
        rest_dofs = fem.locate_dofs_topological(self._V, fdim, rest_facets)
        rest_dofs = np.setdiff1d(rest_dofs, left_dofs)   # corners belong to u_D

        scalar = dolfinx.default_scalar_type
        self._fixed_const = fem.Constant(self._mesh, scalar(FIXED_TEMP))
        return [fem.dirichletbc(self._uD_const, left_dofs, self._V),
                fem.dirichletbc(self._fixed_const, rest_dofs, self._V)]

    def _set_initial_condition(self):
        """A uniform plate, quenched through its boundary.

        Uniform rather than a bump so every probe starts at the same known value
        and decays monotonically -- which is what makes `min` an informative
        observable (the temperature reached by the end of the window) instead of a
        constant.
        """
        self._u_n.x.array[:] = INITIAL_TEMP
        self._uh.x.array[:] = self._u_n.x.array

    def _locate_probes(self):
        """Find, once, which cell each probe point falls in."""
        points = np.zeros((len(PROBE_POINTS), 3), dtype=np.float64)
        for idx, (px, py) in enumerate(PROBE_POINTS):
            points[idx, 0], points[idx, 1] = px, py

        tree = dgeometry.bb_tree(self._mesh, self._mesh.topology.dim)
        candidates = dgeometry.compute_collisions_points(tree, points)
        colliding = dgeometry.compute_colliding_cells(self._mesh, candidates, points)
        self._probe_points = points
        self._probe_cells = np.asarray(
            [int(colliding.links(i)[0]) for i in range(len(points))], dtype=np.int32)

    # --- required: the record grid -----------------------------------------
    def update_times(self, dt, start_time, sim_time, pre_time):
        """Set the output grid. Cheap by construction: nothing is reassembled."""
        dt = float(dt)
        if dt <= 0.0:
            raise ValueError(f'dt must be positive, got {dt}')
        self.dt = dt
        self.start_time = float(start_time)
        self.sim_time = float(sim_time)
        self.pre_time = float(pre_time)
        # CA's own arithmetic, so the two agree on the length exactly.
        self.num_steps = int(self.pre_time / dt) + int(self.sim_time / dt)
        self._dt_const.value = dolfinx.default_scalar_type(dt)
        self._times = self.start_time + dt * np.arange(self.num_steps + 1, dtype=np.float64)
        self._samples = None

    # --- required: parameters ----------------------------------------------
    def set_param_vals(self, param_dict):
        """Write new values in place. Never requires a re-init."""
        scalar = dolfinx.default_scalar_type
        for name, value in param_dict.items():
            if name == 'heat/k':
                value = float(value)
                if value <= 0.0:
                    raise ValueError(f'heat/k must be positive, got {value}')
                self._k_const.value = scalar(value)
            elif name == 'heat/u_D':
                self._uD_const.value = scalar(float(value))
            else:
                raise ValueError(f'unknown parameter "{name}"; this model knows '
                                 f'{sorted(self.parameters)}')

    def get_init_param_vals(self, names):
        """The declared defaults, in the order asked for."""
        return [self.parameters[name] for name in names]

    # --- required: solve ----------------------------------------------------
    def run(self):
        """Solve the whole grid from the initial condition. Repeatable.

        Returns False rather than raising when the solve fails: calibration
        explores parameter values that do not work, and a False is a bad sample
        while an exception is a stopped run.
        """
        try:
            self._solve()
        except Exception as error:  # noqa: BLE001 - reported, not raised
            print(f'[heat_fenics] diverged: {type(error).__name__}: {error}')
            return False
        for name, trace in self._samples.items():
            if not np.all(np.isfinite(trace)):
                print(f'[heat_fenics] {name} is not finite; reporting a diverged run')
                return False
        return True

    def _solve(self):
        self.reset()
        # k changes between runs, so the matrix is rebuilt here rather than in
        # init_solver -- noise next to the form compilation already paid for.
        self._destroy_matrix()
        self._matrix = fem_petsc.assemble_matrix(self._a_form, bcs=self._bcs)
        self._matrix.assemble()
        self._rhs_vector = self._matrix.createVecRight()
        self._solver.setOperators(self._matrix)

        self._record(0)
        mid_step = max(1, self.num_steps // 2)
        for step in range(1, self.num_steps + 1):
            with self._rhs_vector.localForm() as local:
                local.set(0.0)
            fem_petsc.assemble_vector(self._rhs_vector, self._L_form)
            fem_petsc.apply_lifting(self._rhs_vector, [self._a_form], [self._bcs])
            self._rhs_vector.ghostUpdate(addv=PETSc.InsertMode.ADD,
                                         mode=PETSc.ScatterMode.REVERSE)
            fem_petsc.set_bc(self._rhs_vector, self._bcs)

            self._solver.solve(self._rhs_vector, self._uh.x.petsc_vec)
            self._uh.x.scatter_forward()
            self._u_n.x.array[:] = self._uh.x.array
            self._record(step)

            if step == mid_step:
                self._snapshot_mid = self._uh.x.array.copy()
                self._snapshot_mid_time = float(self._times[step])
        self._snapshot_final = self._uh.x.array.copy()

    def _record(self, step):
        values = np.asarray(self._u_n.eval(self._probe_points, self._probe_cells),
                            dtype=float).reshape(-1)
        for idx, name in enumerate(self.output_names):
            self._samples[name][step] = values[idx]

    # --- required: results --------------------------------------------------
    def get_results(self):
        """{output_name: 1D array} of length N+1, pre_time samples included.

        CA discards the leading int(pre_time/dt) samples itself.
        """
        if self._samples is None:
            raise RuntimeError('get_results() was called before run()')
        return {name: trace.copy() for name, trace in self._samples.items()}

    # --- optional -----------------------------------------------------------
    def reset(self):
        """Back to the initial condition, with an empty set of samples."""
        self._set_initial_condition()
        self._samples = {name: np.zeros(self.num_steps + 1, dtype=float)
                         for name in self.output_names}
        self._snapshot_mid = self._snapshot_final = self._snapshot_mid_time = None

    def extra_plots(self):
        """Two field heatmaps: mid-time and final time.

        Figures are returned, never shown or saved, so CUFLynx decides where they
        go. matplotlib.figure.Figure directly rather than pyplot: no global state
        and no backend to configure, which is what makes it safe headless.
        """
        from matplotlib.figure import Figure  # lazy: not needed to simulate

        if self._snapshot_final is None:
            raise RuntimeError('extra_plots() was called before a successful run()')
        panels = ((self._snapshot_mid, self._snapshot_mid_time, 'mid-time'),
                  (self._snapshot_final, float(self._times[-1]), 'final time'))
        return [self._field_figure(Figure, field, time, label)
                for field, time, label in panels if field is not None]

    def _field_figure(self, figure_class, field, time, label):
        figure = figure_class(figsize=(5.0, 4.2))
        axes = figure.add_subplot(111)
        # No explicit triangle list: the domain is convex, so matplotlib's own
        # Delaunay triangulation of the P1 dof coordinates is the mesh -- which
        # keeps this clear of the dolfinx cell-connectivity API entirely.
        mappable = axes.tripcolor(self._dof_coords[:, 0], self._dof_coords[:, 1],
                                  field, shading='gouraud')
        figure.colorbar(mappable, ax=axes, label='u', format='%.2g')
        for idx, (px, py) in enumerate(PROBE_POINTS):
            axes.plot(px, py, 'o', markersize=7, markerfacecolor='none',
                      markeredgecolor='white', markeredgewidth=1.8)
            axes.annotate(f'p{idx + 1}', (px, py), textcoords='offset points',
                          xytext=(8, 6), color='white', fontsize=9)
        axes.set_xlabel('x')
        axes.set_ylabel('y')
        axes.set_aspect('equal')
        axes.set_title(f'u at {label} (t = {time:.4g} s, '
                       f'k = {float(self._k_const.value):.4g})')
        figure.tight_layout()
        return figure

    def close(self):
        """Release the PETSc objects. Safe to call more than once."""
        self._destroy_matrix()
        if self._solver is not None:
            self._solver.destroy()
            self._solver = None

    def _destroy_matrix(self):
        for attr in ('_rhs_vector', '_matrix'):
            obj = getattr(self, attr, None)
            if obj is not None:
                obj.destroy()
                setattr(self, attr, None)


#: What CUFLynx looks for when it loads this file.
SIM_HELPER = HeatFEniCSxModel
```

### Running it

1. Save the file (say `heat_fenics_model.py`) and drop it on the **model** box.
2. Check **Settings → Python** is the `fenicsx` environment. Nothing imports your
   file until the first run, so a wrong interpreter shows up as an `ImportError`
   on that run, not on the drop.
3. Set the run window: **t₁** to `2.0` in the time controls, and **Time step
   (dt)** to `0.02` in Settings. Those are the 100 steps the model is sized for.
4. Add sliders for `heat/k` and `heat/u_D`, and drag them.

`heat/T_p1`, `heat/T_p2` and `heat/T_p3` plot as ordinary traces — they come
through the same channel a CellML model's outputs do, so everything the plots can
do (overlays, added variables, unit conversion, saved-run comparison) works here
too. Below them, in the **Output plots** tab, are the two field heatmaps from
`extra_plots()`: `u` at mid-time and at the final time, with the three probes
marked. They are redrawn after every completed run, so raising `heat/k` shows the
plate cooling faster in the picture and the probe traces falling in step.

Raise `heat/u_D` and the left edge warms while the other three stay put — the
heatmap shows the whole asymmetric profile in one glance, which is the case for
`extra_plots()` in the first place.

## Calibrating it

From here nothing is specific to external python models — it is the ordinary
CUFLynx workflow, because the parameters and outputs arrived through the ordinary
channels.

**Define what to fit.** Click **Edit** beside the obs_data box. Add a data_item
per measurement: pick the operand from the dropdown (your `output_names` are all
there), an operation (`mean`, `min`, `max`, a series comparison…), the measured
value and its standard deviation. The example that ships with CA fits the `mean`
and the `min` of **each** of the three probes — six constants, so every probe is
scored rather than just the centre one (two of them shown here):

```json
[
  { "variable": "near probe mean",
    "name_for_plotting": "mean(T_{p1})",
    "data_type": "constant", "operation": "mean",
    "operands": ["heat/T_p1"], "unit": "dimensionless",
    "weight": 1.0, "value": 0.470, "std": 0.02,
    "cost_type": "gaussian_MLE", "plot_type": "horizontal" },
  { "variable": "near probe minimum",
    "name_for_plotting": "min(T_{p1})",
    "data_type": "constant", "operation": "min",
    "operands": ["heat/T_p1"], "unit": "dimensionless",
    "weight": 1.0, "value": 0.215, "std": 0.015,
    "cost_type": "gaussian_MLE", "plot_type": "horizontal" }
]
```

`min` is informative here because the plate cools monotonically from a uniform
start: it is the temperature the probe reaches by the end of the window. `max`
would be the initial `1.0` for every parameter set — a constant feature that
contributes nothing to the cost.

Saving writes a dated `*_obs_data.json` into your outputs directory and loads it.
The measured values then appear as reference lines on the plots, and the **cost**
of the current slider values is shown beside them.

**Choose what to calibrate.** Click **Edit** beside the params box and tick the
parameters, with a range each. For the example:

| vessel_name | param_name | param_type | min | max | name_for_plotting |
|---|---|---|---|---|---|
| heat | k | const | 0.2 | 5.0 | k |
| heat | u_D | const | -0.5 | 0.5 | u_{D} |

The `vessel_name`/`param_name` split is the `component/variable` name of your
declared parameter, cut at the `/` — so `heat/k` is `heat` + `k`. Every name you
can put here is one from your class's `parameters` dict.

**Run it.** The **Calibration** tab runs a genetic-algorithm parameter
identification through circulatory_autogen; **Progress** plots the cost and the
normalised parameter history live. When it finishes, the best fit is written into
the sliders and re-run, so the traces and the field heatmaps you are looking at
are the fitted ones.

**Sensitivity** works the same way: Sobol indices over the parameter ranges, or a
local `d ln Y / d ln P` about the current point. With two parameters and a run in
the milliseconds, a full Sobol sweep of the heat example is quick — a good way to
confirm your `set_param_vals` really is in-place before pointing the machinery at
something expensive. **UQ** gives posteriors over the same parameters — by
default with emcee, which is a core circulatory_autogen dependency; switching its
**Library** to pyMC needs the `[uq]` extra (`pip install -e ".[uq]"`) in the same
environment.

The **Emulator** tab trains a surrogate of the fitted features and lets
sensitivity, calibration and UQ evaluate *that* instead of the model — which is
the reason to bother on a model where a single run is a finite-element solve. It
needs the `[emulation]` extra from step 2; without it the tab is orange and says
so rather than offering settings that cannot work.

All of these run in the interpreter from **Settings → Python**, i.e. the same
environment as the live plots — which is exactly why there is one environment to
set up rather than two.

## Exporting the study

**Export pipeline to python** writes a circulatory_autogen study you can run
outside the app, and **your `.py` travels with it**: the model file is copied into
the exported study, alongside the obs_data and params_for_id, with the
`model_type: external_python` / `solver: external` configuration already set. That
is the point — a study is not reproducible without the code that defines the
model, and for an external python model the code *is* the model.

**Export python plotting script** writes `plot_outputs.py`, which regenerates the
app's plots from a run's data (see [Using CUFLynx](basic.md#replotting-a-run-outside-the-app)).
It replots the traces; the `extra_plots()` figures come from your own
`extra_plots()`, which is already portable code you can call yourself.

What the exported study does **not** carry is the environment. Whoever runs it
needs the same dependencies you installed above — so if you are handing the study
to someone else, hand them the conda line too.
