"""Reading a circulatory_autogen run directory.

**The one place in CUFLynx that knows CA's on-disk output format.** Everything a
finished (or in-progress) run produced is already on disk, written by CA itself:
``best_param_vals.npy`` / ``best_cost.npy``, ``param_names.csv``,
``param_modifiers.json`` with resolved baselines and affine chain-rule weights,
the ``percent_error_vec.npy`` / ``std_error_vec.npy`` / ``error_vec_names.npy``
triple, and the history CSVs that drive the live plots.

CUFLynx used to have the runner serialise a summary of all that into its own
``results.json`` and hand it back to the manager. That put a file in the user's
working dir that is no part of the study, and made CUFLynx the author of a
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


#: CA's ``paramID.UQ_CASE_METHOD`` and ``MCMC_CHAIN_FILE``, for a CA that predates them.
_UQ_CASE_METHOD = "mcmc"
MCMC_CHAIN_FILE = "mcmc_chain.npy"


def uq_case_method() -> str:
    """What CA names an MCMC run's directory for: ``<this>_<prefix>_<obs>``
    (``paramID.case_dir_name``), beside the calibration's ``<method>_<prefix>_<obs>``."""
    from ca_imports import ca_from, ensure_ca_path  # noqa: PLC0415

    try:
        ensure_ca_path()
        return str(ca_from("param_id.paramID", "UQ_CASE_METHOD"))
    except Exception:  # noqa: BLE001 - a CA without it, or none at all
        return _UQ_CASE_METHOD


def is_uq_run_dir(path: str) -> bool:
    return os.path.basename(os.path.normpath(path)).startswith(uq_case_method() + "_")


def _newest_holding(output_dir: str, names, skip_uq: bool = False) -> str | None:
    """``output_dir`` if it holds one of ``names``, else its subdir holding the newest."""
    if any(os.path.isfile(os.path.join(output_dir, n)) for n in names):
        return output_dir
    candidates = []
    for entry in os.scandir(output_dir):
        if not entry.is_dir() or (skip_uq and is_uq_run_dir(entry.path)):
            continue
        stamps = [os.path.getmtime(os.path.join(entry.path, n)) for n in names
                  if os.path.isfile(os.path.join(entry.path, n))]
        if stamps:
            candidates.append((max(stamps), entry.path))
    return max(candidates)[1] if candidates else None


def find_run_dir(output_dir: str) -> str | None:
    """The directory CA wrote this study's calibration into, or None.

    CA names it ``<param_id_method>_<file_prefix>_<obs_prefix>`` under the
    configured output dir, so the caller cannot know it without repeating CA's
    naming rule -- which is exactly the kind of duplicated knowledge this module
    exists to avoid. Delegates to CA when it can.

    An MCMC run's directory (:func:`uq_run_dir`) is passed over while a
    calibration's is there: it holds a ``best_param_vals.npy`` only as the
    posterior median of a chain with no calibration behind it.
    """
    if not output_dir or not os.path.isdir(output_dir):
        return None
    found = None
    mod = _ca_run_history()
    if mod is not None:
        try:
            found = mod.find_run_dir(output_dir)
        except Exception:  # noqa: BLE001 - a reader must never fail a finished run
            found = None
    if not found:
        found = _newest_holding(output_dir, _RESULT_FILES)
    if found and is_uq_run_dir(found):
        found = _newest_holding(output_dir, _RESULT_FILES, skip_uq=True) or found
    return str(found) if found else None


def uq_run_dir(output_dir: str) -> str | None:
    """The directory CA wrote this study's MCMC run into, or None.

    CA gives an MCMC run a directory of its own, ``<uq_case_method>_<prefix>_<obs>``,
    holding the chain, ``param_names.csv`` and the posterior predictive check; a
    folder written by an older CA has the chain in the calibration's directory. The
    directory holding the newest chain wins, else the newest MCMC directory (a run
    that has not written its chain yet).
    """
    if not output_dir or not os.path.isdir(output_dir):
        return None
    found = _newest_holding(output_dir, (MCMC_CHAIN_FILE,))
    if found:
        return found
    named = [(os.path.getmtime(entry.path), entry.path) for entry in os.scandir(output_dir)
             if entry.is_dir() and is_uq_run_dir(entry.path)]
    return max(named)[1] if named else None


def uq_source_dir(output_dir: str) -> str:
    """Where to read this study's MCMC artefacts: :func:`uq_run_dir`, else the
    calibration's directory, else ``output_dir`` itself."""
    return uq_run_dir(output_dir) or find_run_dir(output_dir) or output_dir


def has_results(output_dir: str, newer_than: float | None = None) -> bool:
    """Whether CA wrote the files a finished run is read from.

    Replaces the old ``os.path.exists(results.json)`` gate. More robust in the
    way that matters: it asks whether the *run's own* outputs are there rather
    than whether CUFLynx managed to serialise a copy of them, which is what an
    MPI teardown abort could interrupt.

    ``newer_than`` is the moment the run started. Reading through
    :func:`find_run_dir` can reach a ``<case_type>`` subdirectory left by an
    *earlier* run in the same working dir -- something the old direct read
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


#: The suffix CUFLynx's runner gives a calibrated model. **The one definition.**
#: It was spelled out in four places -- the runner that writes it, the reader
#: below, the loader that hunts for one, and the loader's prefix parser -- which
#: is three places for it to drift from the file on disk.
#:
#: The name is CUFLynx's own: circulatory_autogen does not write this artefact.
#: CA's equivalent flow (``do_generation_with_fit_parameters``) writes the fitted
#: model as ``generated_models/<file_prefix>/<file_prefix>.cellml``, replacing the
#: generated model rather than sitting beside the run; :func:`ca_calibrated_model`
#: reads that layout, so a directory produced by CA's own scripts is as loadable
#: as one produced here.
CALIBRATED_SUFFIX = "_calibrated.cellml"


def calibrated_model_name(file_prefix: str) -> str:
    """``<file_prefix>_calibrated.cellml`` -- the name, in one place."""
    return f"{file_prefix}{CALIBRATED_SUFFIX}"


def prefix_from_calibrated_model(path: str) -> str | None:
    """The study prefix a calibrated model's name carries, or None.

    The inverse of :func:`calibrated_model_name`, and the only unambiguous way
    back to a prefix: run directories are ``<method>_<prefix>_<obs_prefix>`` and a
    prefix may itself contain underscores, so splitting those has no single
    answer. This does.
    """
    name = os.path.basename(str(path or ""))
    if not name.endswith(CALIBRATED_SUFFIX):
        return None
    return name[: -len(CALIBRATED_SUFFIX)] or None


def calibrated_model_path(output_dir: str, file_prefix: str | None) -> str | None:
    """The calibrated CellML the run wrote, if it wrote one (#114).

    Derived rather than round-tripped: the runner writes
    :func:`calibrated_model_name` into the same directory the manager chose, so
    the manager already knows both halves of the name. It is best-effort in the
    runner (a model with nothing resolvable is not written), hence the existence
    check rather than an assumption.
    """
    if not output_dir or not file_prefix:
        return None
    path = os.path.join(output_dir, calibrated_model_name(file_prefix))
    return path if os.path.isfile(path) else None


def ca_calibrated_model(output_dir: str, file_prefix: str | None) -> str | None:
    """The fitted model CA's own generation step writes, if there is one.

    ``generated_models/<file_prefix>/<file_prefix>.cellml``, which is where CA
    puts the model *with the best fit substituted in* when a run is configured to
    generate with fit parameters. A run produced by ``cuflynx-param-id`` or by a
    generated ``run_pipeline.py`` leaves it there and no ``*_calibrated.cellml``
    anywhere -- so a reader that only knows CUFLynx's own name reports "no
    calibrated model" for a directory that has one.
    """
    if not output_dir or not file_prefix:
        return None
    path = os.path.join(output_dir, "generated_models", file_prefix, f"{file_prefix}.cellml")
    return path if os.path.isfile(path) else None


# ---------------------------------------------------------------------------
# Validation against held-out data (CA #535)
# ---------------------------------------------------------------------------
#: CA's ``param_id.validation.VALIDATION_RESULTS_FILE``: ``{"items": [...]}``, one
#: entry per prediction_item carrying data, written into the run directory.
VALIDATION_RESULTS_FILE = "validation_results.json"


def validation_results(output_dir: str) -> dict | None:
    """The validation of the best fit against held-out data, or None.

    None -- never an empty validation -- when the run had nothing to validate,
    and when the file predates the run's best fit: a study whose held-out data
    was since removed would otherwise keep showing the last run's scores.
    """
    run_dir = find_run_dir(output_dir)
    if not run_dir:
        return None
    found = _json(run_dir, VALIDATION_RESULTS_FILE)
    if not isinstance(found, dict) or not found.get("items"):
        return None
    best = os.path.join(run_dir, "best_param_vals.npy")
    try:
        if os.path.isfile(best) and (
                os.path.getmtime(os.path.join(run_dir, VALIDATION_RESULTS_FILE))
                < os.path.getmtime(best) - 1.0):
            return None
    except OSError:
        return None
    return {"items": list(found["items"])}


# ---------------------------------------------------------------------------
# Sensitivity
# ---------------------------------------------------------------------------
#: CA writes local sensitivities as one CSV per scaling, indexed by observable
#: with one column per calibrated variable (``sensitivityAnalysis.py``). CUFLynx's
#: own local-SA arm writes the same two files, so the working dir holds one
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
                value = float(text)
                # NaN (as numpy writes it) reads as missing, like an empty cell
                values[col] = None if value != value else value
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


#: libcuflynx's ``sensitivityAnalysis.LOCAL_SIGNED_FILE``: the signed relative local
#: sensitivity d ln Y / d ln p = (p / Y) dY/dp (NaN where |Y| <= 1e-30), its rows labelled
#: as the Sobol indices' outputs (``sobolSA.output_labels``) and its columns by the
#: parameters' entry labels, so it shares the Sobol heatmap's axes.
LOCAL_SIGNED_FILE = "local_sensitivity_signed.csv"


def local_sensitivity_signed(output_dir: str) -> dict | None:
    """The signed local sensitivities libcuflynx wrote (:data:`LOCAL_SIGNED_FILE`), in
    :func:`local_sensitivity`'s shape, or None when there are none. A NaN (undefined at
    Y = 0) reads as None."""
    path = os.path.join(output_dir or "", LOCAL_SIGNED_FILE)
    if not output_dir or not os.path.isfile(path):
        return None
    output_names, param_names, table = _read_matrix_csv(path)
    return {
        "indices": {"local": table},
        "param_names": param_names,
        "output_names": output_names,
    }


def step_local_sensitivity(result: dict | None) -> dict | None:
    """A workflow local-SA step's sensitivities, from its ``step_result.json``: the signed
    file it names (``local_sensitivity_signed``), else, from a libcuflynx that predates it,
    the unsigned ``local_sensitivity_relative.csv`` in its ``sensitivity_dir``."""
    result = result or {}
    signed = result.get("local_sensitivity_signed")
    if signed:
        found = local_sensitivity_signed(os.path.dirname(signed))
        if found:
            return found
    directory = result.get("sensitivity_dir")
    return local_sensitivity(directory) if directory and os.path.isdir(directory) else None


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
    return {"indices": indices, "param_names": params, "output_names": output_names,
            # Only this CSV's columns: a run without prediction items writes no JSON,
            # so one left by an earlier run in the directory must not tag anything.
            "prediction_outputs": [o for o in sobol_prediction_outputs(output_dir)
                                   if o in output_names]}


#: libcuflynx's ``sobol_SA.SOBOL_OUTPUT_FEATURES_FILE``: what each Sobol output column
#: is (``kind``: data_item / prediction_item / cost), written with include_prediction_items.
SOBOL_OUTPUT_FEATURES_FILE = "sobol_output_features.json"


def sobol_prediction_outputs(output_dir: str) -> list[str]:
    """The Sobol outputs that are prediction features, from libcuflynx's own record.

    Read, never inferred from the labels: a data_item's label is free text. ``[]``
    when the run did not include prediction items (libcuflynx then writes no file).
    """
    found = _json(output_dir, SOBOL_OUTPUT_FEATURES_FILE)
    outputs = found.get("outputs") if isinstance(found, dict) else None
    return [str(o["output"]) for o in outputs or []
            if isinstance(o, dict) and o.get("kind") == "prediction_item" and o.get("output")]


# ---------------------------------------------------------------------------
# Sobol runs and parameter reductions
# ---------------------------------------------------------------------------
#: CA's Sobol run files: the indices, and beside them the samples, the outputs and
#: the local Jacobian a ``pca`` reduction reads, plus ``sobol_problem.json``.
SOBOL_INDICES_GLOB = "all_outputs_n*_Sobol_indices.csv"
SOBOL_PROBLEM_FILE = "sobol_problem.json"
#: A reduction's report and its params_for_id, written by CA's ``ParamReduction``.
PARAM_REDUCTION_FILE = "param_reduction.json"
REDUCED_PARAMS_FOR_ID_FILE = "reduced_params_for_id.json"


def _sobol_indices_files(directory: str) -> list[str]:
    import glob  # noqa: PLC0415

    return sorted(glob.glob(os.path.join(directory, SOBOL_INDICES_GLOB)))


def sobol_runs(root: str, max_depth: int = 4) -> list[dict]:
    """The Sobol runs under ``root``, newest first.

    ``max_depth`` 4 reaches a study workflow step's run,
    ``<working dir>/workflows/<name>/<step>/sensitivity``.

    One entry per directory holding CA's ``all_outputs_n<N>_Sobol_indices.csv``:
    ``{dir, num_samples, mtime, has_samples}``. ``has_samples`` says whether the run
    also saved the samples, outputs and ``sobol_problem.json`` a ``pca`` reduction
    reads; a run without them can only be reduced by ``sobol``.
    """
    import re  # noqa: PLC0415

    runs: list[dict] = []
    if not root or not os.path.isdir(root):
        return runs
    base_depth = root.rstrip(os.sep).count(os.sep)
    for directory, subdirs, _files in os.walk(root):
        if directory.count(os.sep) - base_depth >= max_depth:
            subdirs[:] = []
        for path in _sobol_indices_files(directory):
            stem = path[: -len("_indices.csv")]
            match = re.search(r"_n(\d+)_Sobol_indices\.csv$", path)
            runs.append({
                "dir": directory,
                "indices": os.path.basename(path),
                "num_samples": int(match.group(1)) if match else None,
                "mtime": os.path.getmtime(path),
                "has_samples": all(os.path.isfile(p) for p in (
                    f"{stem}_samples.csv", f"{stem}_outputs.csv",
                    os.path.join(directory, SOBOL_PROBLEM_FILE))),
            })
    runs.sort(key=lambda r: r["mtime"], reverse=True)
    return runs


def find_param_reduction(sensitivity_dir: str, newer_than: float | None = None,
                         max_depth: int = 2) -> str | None:
    """The directory of the newest ``param_reduction.json`` under ``sensitivity_dir``.

    CA writes a reduction into ``param_reduction_options.output_dir`` (by default
    ``<sensitivity_dir>/param_reduction``), so the report is found rather than its
    location derived a second time here. ``newer_than`` (a job's start time) skips
    an earlier run's report, so a run whose own report is missing fails.
    """
    if not sensitivity_dir or not os.path.isdir(sensitivity_dir):
        return None
    best, best_mtime = None, None
    base_depth = sensitivity_dir.rstrip(os.sep).count(os.sep)
    for directory, subdirs, files in os.walk(sensitivity_dir):
        if directory.count(os.sep) - base_depth >= max_depth:
            subdirs[:] = []
        if PARAM_REDUCTION_FILE not in files:
            continue
        mtime = os.path.getmtime(os.path.join(directory, PARAM_REDUCTION_FILE))
        if newer_than is not None and mtime < newer_than:
            continue
        if best_mtime is None or mtime > best_mtime:
            best, best_mtime = directory, mtime
    return best


def param_reduction(report_dir: str) -> dict | None:
    """CA's parameter-reduction report in ``report_dir``, or None.

    The ``param_reduction.json`` as CA wrote it (``reduction``, ``selected`` and, for
    ``pca``, ``modes``, ``unexplained_effects`` and the rest), plus three resolved
    paths: ``report_dir``, ``reduced_params_for_id`` (the file a calibration
    adopts) and ``heatmap`` (the ``pca`` mode heatmap, or None).
    """
    if not report_dir:
        return None
    path = os.path.join(report_dir, PARAM_REDUCTION_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            report = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(report, dict) or not isinstance(report.get("selected"), list):
        return None
    reduced = report.get("reduced_params_for_id")
    if not reduced or not os.path.isfile(reduced):
        reduced = os.path.join(report_dir, REDUCED_PARAMS_FOR_ID_FILE)
    heatmap = (report.get("files") or {}).get("heatmap")
    heatmap = os.path.join(report_dir, heatmap) if heatmap else None
    return {
        **report,
        "report_dir": report_dir,
        "reduced_params_for_id": reduced if os.path.isfile(reduced) else None,
        "heatmap": heatmap if heatmap and os.path.isfile(heatmap) else None,
    }


# ---------------------------------------------------------------------------
# Profile likelihood
# ---------------------------------------------------------------------------
#: CA's ``ProfileLikelihood`` writes ``profile_likelihood/summary.json`` and one JSON
#: per profiled entry into the identifiability run's output directory.
PROFILE_LIKELIHOOD_SUBDIR = "profile_likelihood"


def profile_likelihood(output_dir: str) -> list | None:
    """The profiles CA's profile-likelihood analysis wrote, or None.

    One ``{param_name, best_val, ci_95, param_values, costs}`` per profiled entry,
    in the order of CA's ``summary.json``.
    """
    results = os.path.join(output_dir or "", PROFILE_LIKELIHOOD_SUBDIR)
    summary = _json(results, "summary.json")
    if not isinstance(summary, dict):
        return None
    profiles = []
    for name in sorted(os.listdir(results)):
        if not name.endswith(".json") or name == "summary.json":
            continue
        profile = _json(results, name)
        if not isinstance(profile, dict) or "param_name" not in profile:
            continue
        profiles.append({key: profile.get(key) for key in (
            "param_name", "best_val", "best_cost", "ci_95", "param_values", "costs")})
    order = [str(p) for p in summary.get("parameters") or []]
    profiles.sort(key=lambda p: order.index(str(p["param_name"]))
                  if str(p["param_name"]) in order else len(order))
    return profiles


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

#: Where CA's emulator trainer puts a bundle, relative to the working dir,
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

    **Asked of CA rather than recomputed.** ``emulator_trainer.resolve_emulator_dir``
    is the function the trainer itself calls, so the answer cannot drift from what
    the trainer does. This used to rebuild the same join here, which is a copy of
    upstream's rule living in another repo -- and the failure mode of such a copy
    is that training writes to one directory and using looks in another.

    A study that points ``emulator_settings.emulator_dir`` elsewhere is not
    resolved here (the callers do not carry that setting); that case is
    :func:`find_emulator_dir`'s, which searches for the bundle that is actually
    there.

    The local composition remains as the fallback for a CA too old to expose the
    helper: an unconfigured study is exactly the case the convention describes,
    and refusing to answer would take the emulator panel down with it.
    """
    inp_data_dict = {
        "param_id_output_dir": output_dir,
        "file_prefix": file_prefix,
        "param_id_obs_path": obs_path or "",
    }
    try:
        from ca_imports import ca_from, ensure_ca_path  # noqa: PLC0415

        ensure_ca_path()
        resolve = ca_from("emulators.emulator_trainer", "resolve_emulator_dir")
    except Exception:  # noqa: BLE001 - CA absent or too old; use the convention
        resolve = None
    if resolve is not None:
        try:
            resolved = resolve(inp_data_dict)
        except Exception:  # noqa: BLE001 - a CA that raises here must not lose the panel
            resolved = None
        if resolved:
            return str(resolved)

    obs_prefix = "obs"
    if obs_path:
        obs_prefix = os.path.splitext(os.path.basename(obs_path))[0]
    return os.path.join(output_dir, EMULATOR_SUBDIR, f"{file_prefix}_{obs_prefix}")


def workflow_emulator_bundles(output_dir: str) -> list[str]:
    """The emulators the working dir's calibration workflows trained: every done
    ``emulation`` step's ``emulator_dir``, as CA records it in the step's
    ``step_result.json`` (``<W>/workflows/<name>/<step>/``)."""
    runs = os.path.join(output_dir or "", "workflows")
    if not os.path.isdir(runs):
        return []
    out = []
    for name in sorted(os.listdir(runs)):
        run = os.path.join(runs, name)
        if not os.path.isdir(run):
            continue
        for entry in sorted(os.listdir(run)):
            result = step_result(os.path.join(run, entry)) or {}
            bundle = result.get("emulator_dir")
            if result.get("kind") == "emulation" and result.get("status") == "done" \
                    and isinstance(bundle, str) and os.path.isfile(
                        os.path.join(bundle, EMULATOR_METADATA_FILE)):
                out.append(bundle)
    return out


def find_emulator_dir(output_dir: str, file_prefix: str | None,
                      obs_path: str | None) -> str | None:
    """Where this run's emulator actually is, or None.

    :func:`emulator_dir` computes where CA *would* put one, which is the right
    question when this app is the thing training: both sides derive the path and
    neither has to pass it. It is only a guess when reading a run someone else
    produced, because ``emulator_settings.emulator_dir`` is a setting, and a study
    that trains one emulator and reuses it across several obs_data has to set it
    -- the conventional name embeds the obs file, so the three runs would
    otherwise each look in a different directory and only the first would find a
    bundle.

    So: the convention first, since that is what an unconfigured run does, and
    otherwise whatever bundle is actually sitting under ``emulators/``. When
    several are, one whose name starts with the model prefix wins over one that
    does not, and the most recently written wins among equals -- the same rule
    ``list_run_dirs`` uses for run directories, and for the same reason.
    """
    if not output_dir or not os.path.isdir(output_dir):
        return None

    if file_prefix:
        conventional = emulator_dir(output_dir, file_prefix, obs_path)
        if emulator_reusable(conventional):
            return conventional

    root = os.path.join(output_dir, EMULATOR_SUBDIR)
    candidates = []
    if os.path.isdir(root):
        try:
            candidates = [entry.path for entry in os.scandir(root) if entry.is_dir()]
        except OSError:
            candidates = []
    # and the emulators the working dir's workflows trained, as emulation steps
    candidates += workflow_emulator_bundles(output_dir)

    bundles = [path for path in candidates if emulator_reusable(path)]
    if not bundles:
        return None

    def rank(path):
        name = os.path.basename(path)
        matches_prefix = bool(file_prefix) and name.startswith(file_prefix)
        return (matches_prefix, os.path.getmtime(
            os.path.join(path, EMULATOR_METADATA_FILE)))

    return max(bundles, key=rank)


def emulator_bundles(root: str) -> list[str]:
    """Every trained bundle at ``root``, whether it *is* one or *contains* them.

    A study that trains several emulators on one design -- the same simulations
    fitted by RBF, MLP and a forest -- leaves them side by side under
    ``emulators/``, and there is then no single right answer to "the emulator for
    this study". So a declaration may name the container instead of one bundle,
    and this returns what is actually in it.

    Both shapes are accepted because both are real: a manifest written before this
    existed names a bundle directly, and a study sharing one jointly-trained
    emulator still wants to name exactly that one.
    """
    if not root or not os.path.isdir(root):
        return []
    if emulator_reusable(root) or os.path.isfile(
            os.path.join(root, EMULATOR_METADATA_FILE)):
        return [root]
    try:
        entries = sorted(entry.path for entry in os.scandir(root) if entry.is_dir())
    except OSError:
        return []
    return [path for path in entries
            if os.path.isfile(os.path.join(path, EMULATOR_METADATA_FILE))]


def emulator_summary(emu_dir: str) -> dict | None:
    """One bundle, as a row a chooser can be built from.

    Deliberately not the whole metadata: picking between emulators needs the family
    and how well it did, and shipping every per-feature array for every candidate
    would send megabytes to render a list.

    ``worst_r2`` leads because it is the number that decides trust -- an emulator
    misleads a calibration wherever it is weakest, not where it is average -- but
    the median is carried too, since one pathological feature out of thirty-six is
    a different situation from a bundle that is uniformly poor.
    """
    meta = _json(emu_dir, EMULATOR_METADATA_FILE)
    if not isinstance(meta, dict):
        return None
    scores = []
    for value in meta.get("feature_r2") or []:
        try:
            scores.append(float(value))
        except (TypeError, ValueError):
            continue
    finite = [value for value in scores if value == value and abs(value) != float("inf")]
    ordered = sorted(finite)
    median = None
    if ordered:
        middle = len(ordered) // 2
        median = (ordered[middle] if len(ordered) % 2
                  else (ordered[middle - 1] + ordered[middle]) / 2.0)
    design = meta.get("design") or {}
    try:
        modified = os.path.getmtime(os.path.join(emu_dir, EMULATOR_METADATA_FILE))
    except OSError:
        modified = None
    return {
        "dir": emu_dir,
        "name": os.path.basename(emu_dir.rstrip(os.sep)),
        "model_name": meta.get("model_name"),
        "num_features": len(scores),
        "worst_r2": ordered[0] if ordered else None,
        "median_r2": median,
        "best_r2": ordered[-1] if ordered else None,
        "num_samples": design.get("num_used"),
        "num_stages": design.get("num_stages"),
        "reused_samples": bool(design.get("reused_samples")),
        "reusable": emulator_reusable(emu_dir),
        "modified": modified,
    }


def emulator_choices(output_dir: str, file_prefix: str | None,
                     obs_path: str | None, declared: str | None = None) -> list[dict]:
    """Every emulator this study could be read with, best-first.

    ``declared`` is a manifest's word and is believed: whatever it names, bundle or
    container, is the candidate set and nothing is inferred. Without one the same
    search :func:`find_emulator_dir` does supplies the candidates, so the list can
    never omit the bundle that function would have chosen.

    Sorted by ``worst_r2`` descending so the safest emulator is first, with the name
    breaking ties -- a stable order matters because this drives a list a person picks
    from, and a list that reshuffles between reads is one they cannot learn.
    """
    roots: list[str] = []
    if declared:
        roots.append(declared)
    else:
        if file_prefix:
            roots.append(emulator_dir(output_dir, file_prefix, obs_path))
        roots.append(os.path.join(output_dir or "", EMULATOR_SUBDIR))

    seen: set[str] = set()
    summaries: list[dict] = []
    found = [bundle for root in roots for bundle in emulator_bundles(root)]
    if not declared:
        # and the emulators the working dir's workflows trained, as emulation steps
        found += workflow_emulator_bundles(output_dir)
    for bundle in found:
        key = os.path.abspath(bundle)
        if key in seen:
            continue
        seen.add(key)
        summary = emulator_summary(bundle)
        if summary:
            summaries.append(summary)

    def rank(row):
        worst = row.get("worst_r2")
        # None sorts last rather than as negative infinity by accident: a bundle whose
        # R2 could not be read is not known to be bad, it is unknown, and putting it
        # under the ones that are known-bad would be a claim this cannot support.
        return (worst is not None, worst if worst is not None else 0.0,
                row.get("name") or "")

    return sorted(summaries, key=rank, reverse=True)


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
        # The prediction features (emulator_settings.include_prediction_items), which
        # come last in feature_labels; absent on a bundle trained without them.
        "prediction_feature_labels": meta.get("prediction_feature_labels") or [],
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

    run_dir = uq_source_dir(output_dir)
    chain_path = os.path.join(run_dir, MCMC_CHAIN_FILE)
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
    return _sample_marginals(flat, qnames)


def _sample_marginals(flat, qnames) -> list:
    import numpy as np  # noqa: PLC0415

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


#: What a calibration workflow writes into each step's directory.
STEP_RESULT_FILE = "step_result.json"
#: CA's stored distribution (``calibration_workflow.distributions``): the samples and the
#: manifest naming their parameters.
DISTRIBUTION_MANIFEST = "distribution.json"
DISTRIBUTION_SAMPLES = "samples.npy"


def step_result(step_dir: str) -> dict | None:
    """A workflow step's ``step_result.json``, or None when the step has not finished."""
    found = _json(step_dir, STEP_RESULT_FILE) if step_dir else None
    return found if isinstance(found, dict) else None


def stored_distribution(directory: str) -> list | None:
    """The marginals (:func:`uq_distributions`' shape) of a distribution a workflow step
    stored (``<step>/distribution/``: an MCMC chain after CA's burn-in, or a Laplace
    approximation's draws), or None. Each parameter is named by its first target,
    ``vessel/param``."""
    import numpy as np  # noqa: PLC0415

    manifest = _json(directory, DISTRIBUTION_MANIFEST) if directory else None
    samples = _npy(directory, DISTRIBUTION_SAMPLES) if directory else None
    if not isinstance(manifest, dict) or samples is None:
        return None
    names = []
    for parameter in manifest.get("parameters") or []:
        targets = parameter.get("targets") or []
        names.append("/".join(str(t) for t in targets[0]) if targets else
                     "parameter %d" % (len(names) + 1))
    flat = np.asarray(samples, dtype=float)
    flat = flat.reshape(-1, 1) if flat.ndim == 1 else flat
    if flat.ndim != 2 or flat.shape[0] == 0 or flat.shape[1] != len(names):
        return None
    return _sample_marginals(flat, names)


def step_uq_marginals(result: dict | None) -> list | None:
    """A workflow uq step's posterior, from its ``step_result.json``: a Laplace
    approximation's normal marginals (``laplace``: mean, covariance, CA's std), or an MCMC's
    stored distribution (``distribution_dir``: the chain after CA's burn-in). None when it
    has neither."""
    result = result or {}
    laplace = result.get("laplace") or {}
    if laplace.get("mean") is not None and laplace.get("covariance") is not None:
        return laplace_marginals(laplace["mean"], laplace["covariance"],
                                 result.get("parameters"), laplace.get("std"))
    return stored_distribution(result.get("distribution_dir") or "")


def stored_sample_count(directory: str) -> int | None:
    """How many draws a stored distribution (``<step>/distribution/samples.npy``) holds, or
    None when it has none."""
    samples = _npy(directory, DISTRIBUTION_SAMPLES) if directory else None
    if samples is None or getattr(samples, "ndim", 0) == 0:
        return None
    return int(samples.shape[0])


#: libcuflynx's posterior predictive check (``param_id.posterior_predictive``): the draws
#: pushed through the model (``SAMPLES_FILE``), the coverage summary (``COVERAGE_FILE``) and
#: the traces of a few draws (``SERIES_FILE``). A direct MCMC run saves them in its run dir,
#: a direct Laplace run in its output dir, a workflow uq step in
#: ``<step>/posterior_predictive`` (its ``step_result.json`` ``posterior_predictive_dir``).
PREDICTIVE_FILE = "posterior_predictive.npz"
PREDICTIVE_COVERAGE_FILE = "posterior_predictive_coverage.json"
PREDICTIVE_SERIES_FILE = "posterior_predictive_series.npz"


def predictive_coverage(directory: str | None) -> dict | None:
    """The coverage summary of the posterior predictive check saved in ``directory``."""
    found = _json(directory, PREDICTIVE_COVERAGE_FILE) if directory else None
    return found if isinstance(found, dict) else None


def step_predictive_dir(result: dict | None) -> str | None:
    """Where a workflow uq step saved its posterior predictive check, or None (none run,
    or it failed: see :func:`step_predictive_error`)."""
    directory = (result or {}).get("posterior_predictive_dir")
    return directory if directory and os.path.isdir(directory) else None


def step_predictive_error(result: dict | None) -> str | None:
    """Why a workflow uq step's posterior predictive check failed, or None."""
    summary = (result or {}).get("posterior_predictive_summary")
    return str(summary["error"]) if isinstance(summary, dict) and summary.get("error") else None


def posterior_predictive_band(directory: str | None, coverage: dict | None = None) -> dict:
    """The posterior predictive check saved in ``directory``, for plotting: per observable,
    the 2.5 / 50 / 97.5 percentiles of the draws' predictions, in units of the measurement's
    standard deviation from the measured value (so observables on different scales share
    one axis). ``{"available": False, ...}`` when there is none, or it cannot be read."""
    import numpy as np  # noqa: PLC0415

    path = os.path.join(directory or "", PREDICTIVE_FILE)
    if not directory or not os.path.isfile(path):
        return {"available": False, "coverage": coverage}
    try:
        with np.load(path, allow_pickle=True) as data:
            preds = np.asarray(data["predictions"], dtype=float)
            truth = np.asarray(data["ground_truth"], dtype=float)
            std = np.asarray(data["std"], dtype=float)
            labels = [str(x) for x in data["labels"]]
    except Exception as exc:  # noqa: BLE001
        # numpy's failure modes for a damaged .npz are varied -- OSError, ValueError,
        # KeyError and UnpicklingError have all been seen -- and a file that cannot be
        # read is a missing figure, not a 500.
        return {"available": False, "error": str(exc), "coverage": coverage}

    keep = ~np.all(np.isnan(preds), axis=0)
    idx = np.where(keep)[0]
    if idx.size == 0:
        return {"available": False, "coverage": coverage}
    # A zero std would divide the whole observable away; leave those in real units.
    scale = np.where(np.abs(std[idx]) > 0, np.abs(std[idx]), 1.0)
    centred = lambda values: ((values - truth[idx]) / scale).tolist()  # noqa: E731
    return {
        "available": True,
        "coverage": coverage,
        "labels": [labels[i] for i in idx],
        "lo": centred(np.nanpercentile(preds[:, idx], 2.5, axis=0)),
        "median": centred(np.nanmedian(preds[:, idx], axis=0)),
        "hi": centred(np.nanpercentile(preds[:, idx], 97.5, axis=0)),
        "num_samples": int(preds.shape[0]),
        "units": "measurement standard deviations from the measured value",
    }


#: The Laplace approximation CA's identifiability analysis writes (a workflow's
#: identifiability step: ``<step dir>/<prefix>_laplace_{mean,covariance}.npy``).
LAPLACE_MEAN_SUFFIX = "_laplace_mean.npy"
LAPLACE_COVARIANCE_SUFFIX = "_laplace_covariance.npy"
#: The z-scores of the 5th and 95th percentiles of a normal distribution.
_Z95 = 1.6448536269514722
#: What a Laplace marginal says in place of a std it does not have: the covariance (the
#: inverse Hessian of the cost) is not positive definite along that parameter, so the data
#: do not pin it down. CA stores that std as NaN.
NOT_IDENTIFIABLE = "not identifiable (covariance not positive definite)"


def laplace_distributions(mean_path: str, covariance_path: str,
                          names: list | None = None) -> list | None:
    """The Laplace posterior (a normal distribution about ``mean`` with ``covariance``) in
    the shape :func:`uq_distributions` returns, or None when either file is missing or
    unreadable.

    Each parameter's marginal, ``{qname, mean, std, q05, q50, q95, bins, counts}``, with the
    density binned over mean +- 4 std (counts per 100000 draws, from the normal density
    rather than a sample, so the same files always give the same plot). ``names`` labels
    the parameters in the files' order; missing names are numbered.
    """
    import numpy as np  # noqa: PLC0415

    if not (os.path.isfile(mean_path) and os.path.isfile(covariance_path)):
        return None
    try:
        mean = np.load(mean_path, allow_pickle=False)
        cov = np.load(covariance_path, allow_pickle=False)
    except Exception:  # noqa: BLE001 - a reader must never fail a finished run
        return None
    return laplace_marginals(mean, cov, names)


def laplace_marginals(mean, covariance, names: list | None = None,
                      std: list | None = None) -> list | None:
    """:func:`laplace_distributions` of a mean and covariance given as arrays (a workflow uq
    step's ``step_result.json`` ``laplace``), or None when their shapes disagree.

    A parameter whose variance is not a positive finite number, or whose ``std`` (CA's, when
    given) is NaN, has no normal marginal: its entry keeps ``qname`` and ``mean`` (and
    ``q50``), has ``std``, ``q05`` and ``q95`` None and no bins, and says why under
    ``not_identifiable`` -- never a std of 0, which would read as perfectly determined.
    """
    import math  # noqa: PLC0415

    import numpy as np  # noqa: PLC0415

    try:
        mean = np.atleast_1d(np.asarray(mean, dtype=float))
        cov = np.atleast_2d(np.asarray(covariance, dtype=float))
    except (TypeError, ValueError):
        return None
    if cov.shape != (mean.size, mean.size):
        return None
    names = list(names or [])
    given_std = list(std) if std is not None else []
    out = []
    for i, mu in enumerate(mean):
        qname = names[i] if i < len(names) else "parameter %d" % (i + 1)
        variance = float(cov[i, i])
        reported = given_std[i] if i < len(given_std) else 0.0
        reported = float("nan") if reported is None else float(reported)
        if not (math.isfinite(variance) and variance > 0) or math.isnan(reported):
            out.append({"qname": qname, "mean": float(mu), "std": None, "q05": None,
                        "q50": float(mu), "q95": None, "bins": [], "counts": [],
                        "not_identifiable": NOT_IDENTIFIABLE})
            continue
        std = math.sqrt(variance)
        if std > 0:
            edges = np.linspace(mu - 4 * std, mu + 4 * std, NUM_BINS + 1)
            cdf = np.array([0.5 * (1 + math.erf((e - mu) / (std * math.sqrt(2))))
                            for e in edges])
            counts = np.rint(np.diff(cdf) * 100000).astype(int)
        else:
            edges, counts = np.array([mu, mu]), np.array([100000])
        out.append({
            "qname": qname,
            "mean": float(mu),
            "std": std,
            "q05": float(mu - _Z95 * std),
            "q50": float(mu),
            "q95": float(mu + _Z95 * std),
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
    run directory and clears that, which is not enough here: a working dir
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
