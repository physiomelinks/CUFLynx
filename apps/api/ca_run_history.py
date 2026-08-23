"""Reading a circulatory_autogen run directory.

**The one place in CUFLynx that knows CA's on-disk output format.** Everything a
finished (or in-progress) run produced is already on disk, written by CA itself:
``best_param_vals.npy`` / ``best_cost.npy``, ``param_names.csv``,
``param_modifiers.json`` with resolved baselines and affine chain-rule weights,
the ``percent_error_vec.npy`` / ``std_error_vec.npy`` / ``error_vec_names.npy``
triple, and the history CSVs that drive the live plots.

CUFLynx used to have the runner serialise a summary of all that into its own
``results.json`` and hand it back to the manager. That put a file in the user's
outputs directory that is no part of the study, and made CUFLynx the author of a
format that duplicated CA's -- so the two could disagree, and the same history
parsing existed twice (``calibration.py`` and ``export_pipeline.py``, with
different tolerance rules). Reading CA's own files removes both problems: CA is
the single source of truth, and a run directory produced by CA's own scripts is
just as readable as one produced through the GUI (issue #210).

CA #392 added ``param_id.run_history`` -- ``read_run_history`` /
``clear_run_history`` / ``find_run_dir`` -- so the history half is a delegation
rather than a parser. The rest is read here, because CA publishes no reader for
it. Everything is best-effort and tolerant of partial files: a run directory may
be polled mid-run, and a missing file means "not written yet", never an error.
"""

from __future__ import annotations

import csv
import json
import os
from pathlib import Path

#: CA lays a run down in ``<output_dir>/<case_type>_<obs_prefix>/``. Its own
#: ``find_run_dir`` locates that; this is the fallback for a CA that predates it.
_RESULT_FILES = ("best_param_vals.npy", "best_cost.npy", "param_names.csv")


def _ca_run_history():
    """CA's ``param_id.run_history`` module, or None if it cannot be imported.

    Puts CA on ``sys.path`` first. A bare import works in the API process only
    because something else imported CA earlier, which makes the progress endpoint
    one import-order change away from going quietly blank -- and it already fails
    that way inside a git worktree, where the sibling-clone guess misses.
    """
    from ca_imports import ca_import, ensure_ca_path  # noqa: PLC0415

    try:
        ensure_ca_path()
    except Exception:  # noqa: BLE001 - try the import anyway; CA may already be there
        pass
    try:
        return ca_import("param_id.run_history")
    except ImportError:
        return None


def find_run_dir(output_dir: str) -> str | None:
    """The directory CA actually wrote this run into, or None.

    CA names it ``<param_id_method>_<file_prefix>_<obs_prefix>`` under the
    configured output dir, so the caller cannot know it without repeating CA's
    naming rule -- which is exactly the kind of duplicated knowledge this module
    exists to avoid. Delegates to CA when it can.
    """
    if not output_dir or not os.path.isdir(output_dir):
        return None
    mod = _ca_run_history()
    if mod is not None:
        try:
            found = mod.find_run_dir(output_dir)
        except Exception:  # noqa: BLE001 - a reader must never fail a finished run
            found = None
        if found:
            return str(found)
    # Fallback: the dir itself if it holds a result file, else the newest subdir
    # that does.
    if any(os.path.isfile(os.path.join(output_dir, n)) for n in _RESULT_FILES):
        return output_dir
    candidates = []
    for entry in os.scandir(output_dir):
        if not entry.is_dir():
            continue
        for name in _RESULT_FILES:
            path = os.path.join(entry.path, name)
            if os.path.isfile(path):
                candidates.append((os.path.getmtime(path), entry.path))
                break
    if not candidates:
        return None
    return max(candidates)[1]


def has_results(output_dir: str, newer_than: float | None = None) -> bool:
    """Whether CA wrote the files a finished run is read from.

    Replaces the old ``os.path.exists(results.json)`` gate. More robust in the
    way that matters: it asks whether the *run's own* outputs are there rather
    than whether CUFLynx managed to serialise a copy of them, which is what an
    MPI teardown abort could interrupt.

    ``newer_than`` is the moment the run started. Reading through
    :func:`find_run_dir` can reach a ``<case_type>`` subdirectory left by an
    *earlier* run in the same outputs directory -- something the old direct read
    could not do -- so a run whose own results are missing would otherwise be
    reported with someone else's numbers. A second of slack absorbs coarse
    filesystem timestamps.
    """
    run_dir = find_run_dir(output_dir)
    if not run_dir:
        return False
    path = os.path.join(run_dir, "best_param_vals.npy")
    if not os.path.isfile(path):
        return False
    if newer_than is None:
        return True
    try:
        return os.path.getmtime(path) >= newer_than - 1.0
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Small readers. Each returns None/[] rather than raising: a run directory may
# be read while it is still being written.
# ---------------------------------------------------------------------------
def _npy(run_dir: str, name: str, allow_pickle: bool = False):
    """A ``.npy`` from the run directory, or None.

    ``allow_pickle`` is for the label arrays: CA saves them as ``<U`` on a
    current numpy and as an object array on older ones, and only the latter
    needs unpickling.
    """
    path = os.path.join(run_dir, name)
    if not os.path.isfile(path):
        return None
    try:
        import numpy as np  # noqa: PLC0415

        return np.load(path, allow_pickle=allow_pickle)
    except Exception:  # noqa: BLE001
        return None


def _json(run_dir: str, name: str):
    path = os.path.join(run_dir, name)
    if not os.path.isfile(path):
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def param_names(run_dir: str) -> list[list[str]]:
    """``param_names.csv`` as CA writes it: one row per entry, member qnames.

    A row is one calibrated variable, which may name several model constants
    (issue #193) -- the list-of-lists shape ``param_id_info["param_names"]`` has
    in memory, so the mapping back to slider qnames is the same one the runner
    used to do.
    """
    path = os.path.join(run_dir, "param_names.csv")
    if not os.path.isfile(path):
        return []
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return [[c.strip() for c in row if c.strip()] for row in csv.reader(fh) if row]
    except OSError:
        return []


def best_param_values(output_dir: str) -> dict:
    """``{"params": {qname: value}, "cost": float | None}`` for a finished run.

    The value is the raw slot value per member qname. For a modifier that is θ at
    every member, anchor included -- deliberately, because best-fit reuse
    (start-from-best-fit, the SA nominal) matches by anchor and needs θ there,
    not a physical value. :func:`modifiers` carries what is needed to expand it.
    """
    run_dir = find_run_dir(output_dir)
    if not run_dir:
        return {"params": {}, "cost": None}
    vals = _npy(run_dir, "best_param_vals.npy")
    cost_arr = _npy(run_dir, "best_cost.npy")
    cost = None
    if cost_arr is not None:
        try:
            cost = float(cost_arr.reshape(-1)[0])
        except (ValueError, IndexError):
            cost = None
    params: dict[str, float] = {}
    if vals is not None:
        flat = [float(v) for v in vals.reshape(-1)]
        for i, members in enumerate(param_names(run_dir)):
            if i >= len(flat):
                break
            for qname in members:
                params[qname] = flat[i]
    return {"params": params, "cost": cost}


def modifiers(output_dir: str) -> list:
    """Modifier metadata for the frontend, from CA's ``param_modifiers.json``.

    ``{name, anchor, targets, operation, baselines, theta}`` per modifier. CA
    writes the file twice -- once at parse time with baselines still None, then
    again once they are resolved -- so a finished run's copy carries the
    resolved values and the probed affine coefficients.

    ``modifier`` is CA's current key and ``operation`` the pre-#385 one; both are
    accepted, because a run directory written by an older CA is still a run
    directory someone may open.
    """
    run_dir = find_run_dir(output_dir)
    if not run_dir:
        return []
    records = _json(run_dir, "param_modifiers.json")
    if not isinstance(records, list):
        return []
    vals = _npy(run_dir, "best_param_vals.npy")
    flat = [float(v) for v in vals.reshape(-1)] if vals is not None else []

    out = []
    for record in records:
        if not isinstance(record, dict):
            continue
        targets = list(record.get("targets") or [])
        idx = record.get("index")
        theta = None
        if idx is not None and 0 <= int(idx) < len(flat):
            theta = flat[int(idx)]
        baselines = record.get("baselines")
        out.append({
            "name": record.get("name"),
            "anchor": targets[0] if targets else None,
            "targets": targets,
            "operation": record.get("modifier") or record.get("operation"),
            "baselines": None if baselines is None else [float(b) for b in baselines],
            "theta": theta,
        })
    return out


def error_vectors(output_dir: str) -> dict:
    """Per-observable fit errors for the Analysis tab's bar charts.

    ``error_vec_names.npy`` exists precisely so the vectors self-identify (CA
    #341); entry *i* of each vector belongs to observable *i*. Without it the
    labels had to be guessed from obs_data, which is how a mislabelled bar chart
    used to happen.
    """
    run_dir = find_run_dir(output_dir)
    if not run_dir:
        return {"percent_error": None, "std_error": None, "error_labels": []}
    percent = _npy(run_dir, "percent_error_vec.npy")
    std = _npy(run_dir, "std_error_vec.npy")
    names = _npy(run_dir, "error_vec_names.npy", allow_pickle=True)
    return {
        "percent_error": None if percent is None else [float(v) for v in percent.reshape(-1)],
        "std_error": None if std is None else [float(v) for v in std.reshape(-1)],
        "error_labels": [] if names is None else [str(v) for v in names.reshape(-1)],
    }


def calibrated_model_path(output_dir: str, file_prefix: str | None) -> str | None:
    """The calibrated CellML the run wrote, if it wrote one (#114).

    Derived rather than round-tripped: the runner writes
    ``<prefix>_calibrated.cellml`` into the same directory the manager chose, so
    the manager already knows both halves of the name. It is best-effort in the
    runner (a model with nothing resolvable is not written), hence the existence
    check rather than an assumption.
    """
    if not output_dir or not file_prefix:
        return None
    path = os.path.join(output_dir, f"{file_prefix}_calibrated.cellml")
    return path if os.path.isfile(path) else None


# ---------------------------------------------------------------------------
# Sensitivity
# ---------------------------------------------------------------------------
#: CA writes local sensitivities as one CSV per scaling, indexed by observable
#: with one column per calibrated variable (``sensitivityAnalysis.py``). CUFLynx's
#: own local-SA arm writes the same two files, so the outputs directory holds one
#: format whichever arm produced them -- and so the exported plotting script has
#: one reader (#210).
LOCAL_SA_FILES = {
    "relative": "local_sensitivity_relative.csv",
    "absolute": "local_sensitivity_absolute.csv",
}


def write_local_sensitivity(output_dir: str, kind: str, local: dict, output_names) -> str:
    """Write ``{output: {param: value}}`` in CA's local-sensitivity CSV format.

    Index ``output`` (observable labels, in ``output_names`` order), one column
    per calibrated variable. Column order follows the first row's keys, which is
    the parameter order the caller built the mapping in.
    """
    rows = [str(name) for name in output_names]
    params: list[str] = []
    for name in rows:
        for pname in (local.get(name) or {}):
            if pname not in params:
                params.append(pname)
    path = os.path.join(output_dir, LOCAL_SA_FILES[kind])
    os.makedirs(output_dir, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["output", *params])
        for name in rows:
            row = local.get(name) or {}
            writer.writerow([name, *["" if row.get(p) is None else row[p] for p in params]])
    return path


def _read_matrix_csv(path: str) -> tuple[list[str], list[str], dict]:
    """``(row_labels, col_labels, {row: {col: float|None}})`` from a labelled CSV."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = [r for r in csv.reader(fh) if r]
    if not rows:
        return [], [], {}
    header = [c.strip() for c in rows[0]][1:]
    labels, table = [], {}
    for row in rows[1:]:
        label = row[0].strip()
        labels.append(label)
        values = {}
        for col, cell in zip(header, row[1:]):
            text = (cell or "").strip()
            try:
                values[col] = float(text)
            except ValueError:
                # Empty or NaN: CA marks a failed evaluation as NaN deliberately,
                # to keep it distinct from a real zero.
                values[col] = None
        table[label] = values
    return labels, header, table


def local_sensitivity(output_dir: str) -> dict | None:
    """The local sensitivities CA (or CUFLynx's arm) wrote, or None."""
    path = os.path.join(output_dir, LOCAL_SA_FILES["relative"])
    if not os.path.isfile(path):
        return None
    output_names, param_names, table = _read_matrix_csv(path)
    return {
        "indices": {"local": table},
        "param_names": param_names,
        "output_names": output_names,
    }


def sobol_indices(output_dir: str) -> dict | None:
    """CA's Sobol indices, or None.

    CA writes ``all_outputs_n<samples>_Sobol_indices.csv``: a ``Parameter``
    column, then ``S1_<output>`` and ``ST_<output>`` per output. The sample count
    is in the filename, so it is globbed rather than reconstructed -- the manager
    should not have to know CA's naming to read CA's file.
    """
    import glob  # noqa: PLC0415

    matches = sorted(glob.glob(os.path.join(output_dir, "*Sobol_indices.csv")))
    matches = [m for m in matches if "2nd_order" not in os.path.basename(m)]
    if not matches:
        return None
    params, columns, table = _read_matrix_csv(matches[0])
    indices: dict = {}
    output_names: list[str] = []
    for column in columns:
        kind, _, output = column.partition("_")
        if kind not in ("S1", "ST") or not output:
            continue
        if output not in output_names:
            output_names.append(output)
        indices.setdefault(kind, {}).setdefault(output, {})
        for param in params:
            indices[kind][output][param] = table[param].get(column)
    if not indices:
        return None
    return {"indices": indices, "param_names": params, "output_names": output_names}


# ---------------------------------------------------------------------------
# UQ posteriors
# ---------------------------------------------------------------------------
#: The posterior samples a UQ run settled on, one row per sample, one column per
#: calibrated variable -- numeric, in CA's own ``.npy`` idiom, labelled by the
#: ``param_names.csv`` CA writes beside it.
#:
#: Not read straight from CA's ``mcmc_chain.npy``, which is the *raw* chain: the
#: burn-in and stuck-walker rules CA applies on read (``get_mcmc_samples``) need a
#: live param-id object, so a manager reading the raw chain would report a
#: different posterior from the one the run reported. Laplace persists only a mean
#: and a covariance, and in the parent directory at that. So the run writes what
#: it actually concluded. (CA persisting post-burn-in samples itself would let
#: this go -- see the handover.)
UQ_SAMPLES_FILE = "uq_posterior_samples.npy"

#: Histogram resolution for the posterior plots. Shared, so the value the runner
#: sampled at and the value the manager bins at cannot drift apart.
NUM_BINS = 40

#: Where CA's emulator trainer puts a bundle, relative to the outputs directory,
#: and the file that describes it (CA #333). Mirrors CA's ``resolve_emulator_dir``:
#: ``<param_id_output_dir>/emulators/<file_prefix>_<obs_prefix>``.
EMULATOR_SUBDIR = "emulators"
EMULATOR_METADATA_FILE = "emulator_metadata.json"
#: The held-out points CA keeps beside the emulator: the parameters, the
#: simulator's answer at each and the emulator's. The statistics say how wrong
#: the emulator is; only these say *where* (CA #333).
EMULATOR_VALIDATION_FILE = "emulator_validation.npz"
#: The design and the simulated features CA keeps beside a bundle, so a later run
#: can refit them without paying for the simulations again
#: (``emulator_settings.reuse_samples``). CA's ``emulator_bundle.TRAINING_DATA_FILE``.
EMULATOR_TRAINING_DATA_FILE = "training_data.npz"


def emulator_reusable(emu_dir: str) -> bool:
    """Whether ``emulator_settings.reuse_samples`` has anything to reuse here.

    CA's trainer raises ``EmulatorReuseError`` unless **both** the metadata and
    the saved samples are in the resolved emulator directory, so both are checked
    -- a bundle trained by a circulatory_autogen that predates
    ``training_data.npz`` has the first and not the second, and "an emulator
    exists" is therefore not the same question.
    """
    return all(
        os.path.isfile(os.path.join(emu_dir, name))
        for name in (EMULATOR_METADATA_FILE, EMULATOR_TRAINING_DATA_FILE)
    )


def emulator_dir(output_dir: str, file_prefix: str, obs_path: str | None) -> str:
    """The directory CA's trainer will write this study's emulator into.

    Computed the same way on both sides rather than passed around, so a run that
    trains and a run that uses agree on where the bundle is without a second
    setting to keep in step.
    """
    obs_prefix = "obs"
    if obs_path:
        obs_prefix = os.path.splitext(os.path.basename(obs_path))[0]
    return os.path.join(output_dir, EMULATOR_SUBDIR, f"{file_prefix}_{obs_prefix}")


def emulator_error_points(emu_dir: str) -> dict | None:
    """The emulator's held-out points, as the Analysis view plots them.

    ``{theta, y_true, y_pred, residual, feature_labels, param_entry_labels}`` in
    real units, or None when the bundle predates CA writing them. The residual is
    **prediction minus truth** -- CA's sign convention, kept rather than recomputed
    so a positive residual means the same thing here as it does there.

    These are the points a parity plot and a residual-against-parameter plot are
    drawn from, and no statistic replaces them: R2 says how well the emulator does
    over the whole design, and these say where it does not.
    """
    path = os.path.join(emu_dir, EMULATOR_VALIDATION_FILE)
    if not os.path.isfile(path):
        return None
    try:
        import numpy as np  # noqa: PLC0415

        with np.load(path, allow_pickle=True) as data:
            y_true = np.asarray(data["y_true"], dtype=float)
            y_pred = np.asarray(data["y_pred"], dtype=float)
            return {
                "theta": np.asarray(data["theta"], dtype=float).tolist(),
                "y_true": y_true.tolist(),
                "y_pred": y_pred.tolist(),
                "residual": (y_pred - y_true).tolist(),
                "feature_labels": [str(v) for v in data["feature_labels"]],
                "param_entry_labels": [str(v) for v in data["param_entry_labels"]],
            }
    except Exception:  # noqa: BLE001 - a damaged extra is not a damaged emulator
        return None


def emulator_metadata(emu_dir: str) -> dict | None:
    """A trained emulator's metadata, or ``None`` if there is no emulator there.

    This is the file the user has to read before trusting anything downstream:
    held-out R2 and RMSE per feature, the parameter box the emulator is valid in,
    the design that produced it, and the fingerprint that decides whether it is
    still about this model. Returned verbatim -- CUFLynx displays CA's numbers and
    does not compute its own.
    """
    meta = _json(emu_dir, EMULATOR_METADATA_FILE)
    if not isinstance(meta, dict):
        return None
    worst = None
    for value in meta.get("feature_r2") or []:
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if worst is None or value < worst:
            worst = value
    return {
        "dir": emu_dir,
        "feature_labels": meta.get("feature_labels") or [],
        "feature_r2": meta.get("feature_r2") or [],
        "feature_rmse": meta.get("feature_rmse") or [],
        "worst_r2": worst,
        "param_entry_labels": meta.get("param_entry_labels") or [],
        "param_mins": meta.get("param_mins") or [],
        "param_maxs": meta.get("param_maxs") or [],
        "model_name": meta.get("model_name"),
        "design": meta.get("design") or {},
        "provenance": meta.get("provenance") or {},
        "fingerprint": meta.get("fingerprint") or {},
        # The rest of what the held-out set said, per feature. R2 alone cannot
        # rank features -- a feature can score well and still read systematically
        # high (bias), and RMSE in one feature's units says nothing against
        # another's (nrmse can). Absent on a bundle trained before CA wrote them.
        "feature_mae": meta.get("feature_mae") or [],
        "feature_bias": meta.get("feature_bias") or [],
        "feature_max_abs_error": meta.get("feature_max_abs_error") or [],
        "feature_nrmse": meta.get("feature_nrmse") or [],
    }


def write_uq_samples(output_dir: str, flat, qnames) -> str:
    """Persist the posterior samples plus their labels."""
    import numpy as np  # noqa: PLC0415

    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, UQ_SAMPLES_FILE)
    np.save(path, np.asarray(flat, dtype=float))
    with open(os.path.join(output_dir, "uq_param_names.csv"), "w", newline="",
              encoding="utf-8") as fh:
        csv.writer(fh).writerows([[name] for name in qnames])
    return path


def _chain_samples(output_dir: str):
    """``(draws, params)`` and their names from CA's own chain, or ``(None, None)``.

    ``mcmc_chain.npy`` is ``(steps, walkers, params)``. The first half is dropped:
    the walkers start scattered over the prior box, so the early steps describe
    where they were initialised rather than the posterior, and a histogram drawn
    over them is not the posterior at all.
    """
    import numpy as np  # noqa: PLC0415

    run_dir = find_run_dir(output_dir) or output_dir
    chain_path = os.path.join(run_dir, "mcmc_chain.npy")
    if not os.path.isfile(chain_path):
        return None, None
    try:
        chain = np.load(chain_path, allow_pickle=False)
    except Exception:  # noqa: BLE001 - a reader must never fail a finished run
        return None, None
    if getattr(chain, "ndim", 0) != 3 or chain.shape[0] == 0:
        return None, None

    names = [row[0] for row in param_names(run_dir) or []]
    if len(names) < chain.shape[2]:
        names = names + ["parameter %d" % (i + 1)
                         for i in range(len(names), chain.shape[2])]

    burn_in = chain.shape[0] // 2
    return chain[burn_in:].reshape(-1, chain.shape[2]), names[: chain.shape[2]]


def uq_distributions(output_dir: str) -> list | None:
    """Per-parameter posterior summary + histogram, or None if nothing was written.

    ``{qname, mean, std, q05, q50, q95, bins, counts}`` per parameter -- the shape
    the UQ panel plots.
    """
    import numpy as np  # noqa: PLC0415

    path = os.path.join(output_dir, UQ_SAMPLES_FILE)
    names_path = os.path.join(output_dir, "uq_param_names.csv")
    if os.path.isfile(path) and os.path.isfile(names_path):
        flat = np.load(path, allow_pickle=False)
        with open(names_path, newline="", encoding="utf-8") as fh:
            qnames = [row[0].strip() for row in csv.reader(fh) if row]
    else:
        # A run this app did not produce. uq_posterior_samples.npy is written by
        # our own runner; circulatory_autogen writes the chain itself, and a run
        # from cuflynx-param-id or a generated run_pipeline.py has only that --
        # so without this the UQ panel was empty for every run made outside the
        # app, with the samples sitting right there (#255).
        flat, qnames = _chain_samples(output_dir)
        if flat is None:
            return None

    out = []
    for i, qname in enumerate(qnames):
        if i >= flat.shape[1]:
            break
        col = np.asarray(flat[:, i], dtype=float)
        col = col[np.isfinite(col)]
        if col.size == 0:
            continue
        counts, edges = np.histogram(col, bins=NUM_BINS)
        q05, q50, q95 = (float(x) for x in np.percentile(col, [5, 50, 95]))
        out.append({
            "qname": qname,
            "mean": float(np.mean(col)),
            "std": float(np.std(col)),
            "q05": q05,
            "q50": q50,
            "q95": q95,
            "bins": [float(x) for x in edges],
            "counts": [int(x) for x in counts],
        })
    return out


# ---------------------------------------------------------------------------
# History (the live progress plots)
# ---------------------------------------------------------------------------
def read_run_history(output_dir: str, param_id_info: dict | None = None) -> dict | None:
    """CA's own reader for the history files, or None on a CA without it.

    Returns CA's shape: ``param_labels``, ``cost_history``, ``param_history_norm``
    (as written -- normalised to each parameter's [min, max]), ``param_history``
    (denormalised, or None when bounds are unavailable), ``grad_history``,
    ``starts``, ``best_param_vals``, ``best_cost``, ``run_dir``.

    Safe to poll mid-run: CA skips partial trailing rows.
    """
    mod = _ca_run_history()
    if mod is None:
        return None
    try:
        return mod.read_run_history(output_dir, param_id_info)
    except Exception:  # noqa: BLE001 - polling a half-written dir must not fail
        return None


#: CA writes the best-so-far parameter vector here incrementally, so it exists
#: even for a run stopped early -- which is what lets a cancelled calibration be
#: continued from (#83). Deliberately spared by ``clear_run_history``.
BEST_PARAM_VALS_FILE = "best_param_vals.npy"

#: The empty progress payload. Its shape is the contract with the Progress tab,
#: so it is written once here rather than reconstructed at each early return.
_EMPTY_PROGRESS = {
    "param_names": [],
    "cost_history": [],
    "param_history": [],
    "start_costs": [],
    "start_params": {"param_names": [], "starts": []},
    "grad_history": [],
    "start_grads": {"param_names": [], "starts": []},
}


def _full_width(rows: list) -> list:
    """``rows`` less any torn trailing row.

    CA keeps a row at whatever width it parsed to -- it guards against
    *unparseable* rows, not short ones -- and a run is polled while it is being
    written, so the last row is routinely half-flushed. Left in, it puts a
    phantom point on the plot that appears and moves on every poll.
    """
    if not rows:
        return []
    width = max(len(r) for r in rows)
    return [r for r in rows if len(r) == width]


def progress_history(output_dir: str) -> dict:
    """The live progress payload, from CA's own reader (issue #210).

    ``param_history`` is CA's ``param_history_norm`` -- **normalised**, never its
    denormalised ``param_history``. The Progress plot pins its y-axis to [0, 1],
    titles it "normalised value" and denormalises in the tooltip, so handing it
    physical values would plot them on a normalised axis and then convert them a
    second time. The trap is live rather than theoretical: CA writes
    ``param_bounds.json`` on every real run, so the denormalised series is
    populated in production and ``None`` in most fixtures -- wrong in the app,
    green in CI.

    Returns :data:`_EMPTY_PROGRESS` when CA cannot be imported, which is what the
    Progress tab already renders as "run a calibration to see progress".
    """
    hist = read_run_history(output_dir)
    if hist is None:
        return dict(_EMPTY_PROGRESS)

    labels = list(hist.get("param_labels") or [])
    starts = hist.get("starts") or []
    start_params = [_full_width(s.get("params") or []) for s in starts]
    start_grads = [_full_width(s.get("grad") or []) for s in starts]
    return {
        "param_names": labels,
        # Deliberately unfiltered: a genetic algorithm writes its whole sorted
        # top-10 per generation and the Progress plot draws that band, so rows
        # here are variable-width by design.
        "cost_history": hist.get("cost_history") or [],
        "param_history": _full_width(hist.get("param_history_norm") or []),
        "start_costs": [s.get("cost") or [] for s in starts],
        "start_params": {
            "param_names": labels if any(start_params) else [],
            "starts": start_params,
        },
        "grad_history": _full_width(hist.get("grad_history") or []),
        "start_grads": {
            "param_names": labels if any(start_grads) else [],
            "starts": start_grads,
        },
    }


def clear_run_history(output_dir: str) -> bool:
    """Delete the transient history files so a new run does not append onto an
    old one, or show an old one's plots.

    **CA owns the file list; CUFLynx owns the scope.** CA's clearer locates *one*
    run directory and clears that, which is not enough here: an outputs directory
    reused across methods accumulates a ``<case_type>_<prefix>`` subdir per run,
    and clearing only the newest leaves the reader free to fall back to an older
    one and serve its history as this run's. That is exactly the stale-plot bug
    this function was written for, so it is applied to ``output_dir`` and to every
    immediate subdirectory.

    The result files (``best_param_vals.npy`` / ``best_cost.npy``) are deliberately
    spared by CA -- a cancelled run's best-so-far is worth keeping until a new one
    replaces it (CA #300), which is what continuing a stopped calibration reads.

    Returns whether CA's own clearer ran.
    """
    mod = _ca_run_history()
    if mod is None:
        return False
    targets = [output_dir]
    try:
        targets += [e.path for e in os.scandir(output_dir) if e.is_dir()]
    except OSError:
        pass
    ran = False
    for target in targets:
        try:
            mod.clear_run_history(target)
            ran = True
        except Exception:  # noqa: BLE001 - one unreadable dir must not stop the rest
            continue
    return ran
