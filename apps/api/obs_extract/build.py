"""Turn an extraction config into an obs_data document.

One **included sweep becomes one obs_data experiment**. That is the pivot the
whole shape turns on: a recording of twelve current steps contributes twelve
experiments, each with its own protocol row and its own observables, because the
model has to be run once per step to be compared against it.

Per sweep the work is:

1. read it, and apply the config's data modifiers to the recorded channels;
2. find the stimulus window;
3. build the clamp command as a ``protocol_traces`` entry and point
   ``params_to_change`` at it, in whichever direction the group is clamped;
4. evaluate each configured feature over its range and emit a ``data_item``;
5. optionally emit the sweep itself as a weight-0 ground-truth series.

Everything model-specific -- which parameter is the command, which variable is
observed -- arrives through :class:`~obs_extract.binding.ModelBinding`, so the
same code serves any CUFLynx model.

The document is handed to CUFLynx's own ``obs_data.parse_obs_data`` before it is
returned, so CA's parser is what says whether it is valid, rather than this
module's opinion of the schema.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import numpy as np

from . import config as config_mod
from .binding import ModelBinding, validate as validate_binding
from .discovery import group_key
from .errors import ObsExtractError
from .features import accepts_range, evaluate, plan_call
from .modifiers import apply_modifiers, load_modifiers
from .preprocess import command_trace
from .readers import CURRENT, VOLTAGE, open_recording
from .windows import detect_stim_window, resolve_range

#: A feature named this is the recorded sweep itself rather than an operation.
SERIES_FEATURE = "series"


@dataclass
class Outcome:
    """What an extraction did, for the report and the job result."""

    n_experiments: int = 0
    n_data_items: int = 0
    datasets_used: int = 0
    sweeps_used: int = 0
    skipped: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def skip(self, case: str, reason: str, **extra) -> None:
        self.skipped.append({"case_name": case, "reason": reason, **extra})


def build_obs_data(
    config: dict,
    *,
    operation_funcs: dict,
    variables: dict | None = None,
    output_dir: str | None = None,
    log=None,
    cancelled=None,
) -> tuple[dict, Outcome]:
    """Config -> ``(obs_data document, Outcome)``.

    ``operation_funcs`` is CA's registry (``obs_options.get_operation_funcs()``).
    ``cancelled`` is an optional predicate checked between datasets and sweeps,
    so a long extraction can be stopped; a thread cannot be terminated, so
    cancellation has to be cooperative.
    """
    log = log or (lambda _msg: None)
    cancelled = cancelled or (lambda: False)
    outcome = Outcome()

    binding = ModelBinding.from_dict(config.get("model_binding"))
    modifiers = load_modifiers(config.get("data_modifiers"))
    prep = config_mod.preprocess_settings(config)
    provenance = config.get("provenance") or {}

    if not operation_funcs:
        # Every feature would be skipped for the same reason, and the resulting
        # "no data items" would send the user to look at their feature list --
        # which is fine. The fault is that CA could not be reached.
        raise ObsExtractError(
            "circulatory_autogen's operation registry is unavailable, so no "
            "observable can be computed. Check the circulatory_autogen directory "
            "in Settings.")

    datasets = [d for d in (config.get("datasets") or []) if d.get("used")]
    if not datasets:
        raise ObsExtractError(
            "no datasets are marked used, so there is nothing to extract.")

    kinds = set()
    for d in datasets:
        group = _group_for(config, d)
        if group and group.get("used"):
            kinds.add(group.get("input") or "current")
    outcome.warnings.extend(validate_binding(binding, variables, stimulus_kinds=kinds))

    doc = {
        "protocol_info": {
            "pre_times": [], "sim_times": [], "params_to_change": {},
            "protocol_traces": {}, "experiment_labels": [], "experiment_ids": [],
        },
        "prediction_items": [],
        "data_items": [],
    }
    names_used: set[str] = set()
    series_dir = None
    if output_dir:
        series_dir = os.path.join(
            output_dir,
            (config.get("outputs") or {}).get("series_subdir") or "series_data")

    for dataset in datasets:
        if cancelled():
            outcome.notes.append("cancelled")
            break
        case = dataset.get("case_name") or os.path.basename(dataset.get("path") or "")
        group = _group_for(config, dataset)
        if not group or not group.get("used"):
            outcome.skip(case, "its protocol|subprotocol group is not included")
            continue
        features = config_mod.features_for(config, dataset)
        if not features:
            outcome.skip(case, "no features are configured for its group")
            continue

        try:
            recording = open_recording(dataset.get("path"),
                                       **_reader_opts(config, dataset))
        except ObsExtractError as exc:
            outcome.skip(case, str(exc))
            log(f"[skip] {case}: {exc}")
            continue

        used = _extract_dataset(
            doc, config, dataset, group, features, recording, binding, modifiers,
            prep, provenance, operation_funcs, names_used, outcome, log, cancelled,
            series_dir)
        if used:
            outcome.datasets_used += 1

    was_cancelled = cancelled()
    if not doc["data_items"]:
        # A cancelled run and an unextractable one are different problems, and
        # reporting the first as the second sends the user looking for a fault
        # in a config that was fine.
        if was_cancelled:
            raise ObsExtractError(
                f"extraction was cancelled before any data item was produced "
                f"({outcome.sweeps_used} sweep(s) read).")
        raise ObsExtractError(
            "extraction produced no data items. Every included dataset was "
            "skipped or every feature returned nothing -- see the log for which.")
    if was_cancelled:
        # Partial, but real. Keeping what was extracted matches how a cancelled
        # UQ run keeps the chain it had already sampled -- and the alternative,
        # throwing away minutes of work because the user stopped it a moment
        # early, is worse. Labelled, so nobody mistakes it for the whole set.
        outcome.warnings.append(
            f"cancelled part-way: {outcome.datasets_used} dataset(s) and "
            f"{outcome.sweeps_used} sweep(s) were extracted, not the whole "
            f"selection.")
        log("[warning] " + outcome.warnings[-1])

    _finalise_params_to_change(doc, outcome)
    _validate_with_ca(doc, outcome, log)
    outcome.n_experiments = len(doc["protocol_info"]["sim_times"])
    outcome.n_data_items = len(doc["data_items"])
    return doc, outcome


# ---------------------------------------------------------------------------
def _group_for(config: dict, dataset: dict) -> dict | None:
    return (config.get("subprotocols") or {}).get(
        group_key(dataset.get("protocol") or "", dataset.get("subprotocol") or ""))


def _reader_opts(config: dict, dataset: dict) -> dict:
    opts = dict(dataset.get("reader") or {})
    opts.pop("format", None)
    if config.get("channel_map"):
        patterns = {}
        for role, spec in (config["channel_map"] or {}).items():
            names = tuple((spec or {}).get("name_patterns") or ())
            if names:
                patterns[role] = names
        if patterns:
            opts["name_patterns"] = patterns
    return opts


def _sweep_indices(dataset: dict, config: dict, recording) -> list[int]:
    if dataset.get("sweep_indices"):
        return [i for i in dataset["sweep_indices"] if 0 <= i < recording.sweep_count]
    limit = config_mod.sweep_limit_for(config, dataset)
    n = recording.sweep_count if limit is None else min(limit, recording.sweep_count)
    return list(range(n))


def _extract_dataset(doc, config, dataset, group, features, recording, binding,
                     modifiers, prep, provenance, operation_funcs, names_used,
                     outcome, log, cancelled, series_dir=None) -> bool:
    case = dataset.get("case_name")
    stimulus = group.get("input") or "current"
    timeline = config_mod.timeline_for(group)
    roles = {c.name: c.role for c in recording.channels}
    measured_role = VOLTAGE if stimulus == "current" else CURRENT
    command_role = CURRENT if stimulus == "current" else VOLTAGE

    measured_name = recording.name_for_role(measured_role)
    command_name = recording.name_for_role(command_role)
    if measured_name is None:
        outcome.skip(case, f"no {measured_role} channel to measure")
        return False
    if command_name is None:
        # Under current clamp the injected current is usually recorded; without
        # it there is no command trace and no stimulus window. Say which channel
        # is missing rather than failing later on an empty protocol row.
        outcome.skip(case, f"no {command_role} channel to build the clamp command from")
        return False

    used_any = False
    for sweep_index in _sweep_indices(dataset, config, recording):
        if cancelled():
            return used_any
        try:
            t, signals = recording.sweep(sweep_index)
        except ObsExtractError as exc:
            outcome.skip(case, f"sweep {sweep_index}: {exc}", sweep=sweep_index)
            continue

        signals, notes = apply_modifiers(signals, roles, modifiers)
        for note in notes:
            if "not applied" in note:
                outcome.warnings.append(f"{case}: {note}")

        window = detect_stim_window(
            t, signals[command_name], stimulus,
            current_threshold=(prep["stim_detect"] or {}).get("current_threshold", 10.0),
            voltage_threshold=(prep["stim_detect"] or {}).get("voltage_threshold", 5.0))
        if not window.detected:
            outcome.warnings.append(
                f"{case} sweep {sweep_index}: no stimulus was detected, so the "
                f"whole sweep is treated as the window and every fractional "
                f"range is a fraction of the entire sweep.")

        experiment = len(doc["protocol_info"]["sim_times"])
        _append_experiment(doc, config, dataset, group, timeline, binding, stimulus,
                           t, signals[command_name], window, sweep_index, prep,
                           experiment, outcome)

        emitted = _emit_features(
            doc, features, operation_funcs, binding, stimulus, t, signals,
            measured_name, window, experiment, timeline, case, sweep_index,
            provenance, names_used, outcome, log, recording, group, series_dir)
        if emitted:
            used_any = True
            outcome.sweeps_used += 1
        else:
            log(f"[info] {case} sweep {sweep_index}: no feature produced a value")
    return used_any


def _append_experiment(doc, config, dataset, group, timeline, binding, stimulus,
                       t, command_values, window, sweep_index, prep, experiment,
                       outcome) -> None:
    """One protocol_info row: the timing, the command trace, the parameters."""
    info = doc["protocol_info"]
    t_win = window.slice(t)
    y_win = window.slice(command_values) * binding.command_scale(stimulus)
    t_rel = t_win - (t_win[0] if t_win.size else 0.0)

    windows = prep.get("savgol_window_seconds") or {}
    t_out, y_out, notes = command_trace(
        t_rel, y_win, kind=stimulus,
        output_hz=float(prep.get("clamp_output_hz") or 1000.0),
        window_seconds=float(windows.get(stimulus, 5e-3)),
        peak_preserve_ratio=float(prep.get("voltage_peak_preserve_ratio") or 0.95))
    for note in notes:
        outcome.notes.append(f"{dataset.get('case_name')} sweep {sweep_index}: {note}")

    trace_id = _trace_name(stimulus, experiment, dataset.get("case_name"), sweep_index)
    info["protocol_traces"][trace_id] = {
        "t": [float(v) for v in t_out],
        "values": [float(v) for v in y_out],
    }

    settle = timeline.get("settle_time_s")
    duration = max(window.duration, 1e-6)
    info["pre_times"].append(float(timeline.get("pre_time_s") or 0.0))
    info["sim_times"].append([float(settle), float(duration)] if settle
                             else [float(duration)])
    n_sub = len(info["sim_times"][-1])
    stim_sub = int(timeline.get("stim_subexperiment_index") or 0)
    stim_sub = min(stim_sub, n_sub - 1)

    params = info["params_to_change"]
    command_param = binding.command_param(stimulus)
    other_param = binding.command_param(
        "voltage" if stimulus == "current" else "current")

    def row(value_at_stim, default=0.0):
        out = [default] * n_sub
        out[stim_sub] = value_at_stim
        return out

    if command_param:
        params.setdefault(command_param, {})[experiment] = row(trace_id)
    if other_param and other_param != command_param:
        params.setdefault(other_param, {})[experiment] = row(0.0)
    if binding.clamp_mode_param:
        value = binding.clamp_value(stimulus)
        params.setdefault(binding.clamp_mode_param, {})[experiment] = [value] * n_sub

    modulated = group.get("modulated_parameter")
    if modulated and modulated not in (command_param, other_param,
                                       binding.clamp_mode_param):
        pre = _auto(group.get("param_pre_value"))
        stim = _auto(group.get("param_stim_value"))
        values = [pre] * n_sub
        values[stim_sub] = stim
        params.setdefault(modulated, {})[experiment] = values

    info["experiment_labels"].append(
        f"{dataset.get('subprotocol') or ''} {dataset.get('case_name')} sw{sweep_index}".strip())
    info["experiment_ids"].append(f"{dataset.get('case_name')}_sw{sweep_index}")


def _auto(value, default: float = 1.0) -> float:
    """``"auto"`` and absent both mean "leave the model's own value alone"."""
    if value is None or (isinstance(value, str) and value.strip().lower() == "auto"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ObsExtractError(
            f"{value!r} is not a number or 'auto' for a modulated parameter") from exc


def _trace_name(stimulus: str, experiment: int, case: str, sweep: int) -> str:
    prefix = "Ic" if stimulus == "current" else "Vc"
    safe = "".join(c if c.isalnum() else "_" for c in str(case))
    return f"{prefix}_exp{experiment}_{safe}_sw{sweep}"


# ---------------------------------------------------------------------------
def _emit_features(doc, features, operation_funcs, binding, stimulus, t, signals,
                   measured_name, window, experiment, timeline, case, sweep_index,
                   provenance, names_used, outcome, log, recording, group,
                   series_dir=None) -> bool:
    measured_variable = binding.measured_variable(stimulus)
    x = signals[measured_name]
    stim_sub = int(timeline.get("stim_subexperiment_index") or 0)
    n_sub = len(doc["protocol_info"]["sim_times"][experiment])
    stim_sub = min(stim_sub, n_sub - 1)
    emitted = False

    for feature in features:
        operation = feature.get("operation")
        if operation == SERIES_FEATURE:
            item = _series_item(feature, measured_variable, t, x, window,
                                experiment, stim_sub, case, sweep_index,
                                provenance, names_used, recording, series_dir)
            if item is not None:
                doc["data_items"].append(item)
                emitted = True
            continue
        fn = (operation_funcs or {}).get(operation)
        if fn is None:
            outcome.skip(case, f"operation {operation!r} is not available",
                         sweep=sweep_index)
            continue

        kwargs = dict(feature.get("operation_kwargs") or {})
        if accepts_range(fn):
            fracs = resolve_range(window, feature.get("range"), kwargs)
            if fracs is not None:
                kwargs["start_frac"], kwargs["end_frac"] = fracs

        try:
            plan = plan_call(operation, fn, measured_variable, kwargs=kwargs)
        except ObsExtractError as exc:
            outcome.skip(case, str(exc), sweep=sweep_index)
            continue
        if plan.dropped:
            outcome.notes.append(
                f"{case} sweep {sweep_index}: {operation} does not accept "
                f"{', '.join(plan.dropped)}; not passed.")

        t_win, x_win = window.slice(t), window.slice(x)
        try:
            value = evaluate(plan, t_win, x_win)
        except ObsExtractError as exc:
            outcome.skip(case, str(exc), sweep=sweep_index)
            continue
        except Exception as exc:  # noqa: BLE001 - a user operation may raise
            outcome.skip(case, f"{operation} raised: {exc}", sweep=sweep_index)
            log(f"[warning] {case} sweep {sweep_index}: {operation} raised: {exc}")
            continue

        if not np.isfinite(value):
            log(f"[info] {case} sweep {sweep_index}: {operation} returned "
                f"{value}; not emitted")
            continue

        item = _data_item(feature, plan, value, experiment, stim_sub, case,
                          sweep_index, provenance, names_used, recording, group)
        doc["data_items"].append(item)
        emitted = True
    return emitted


def _series_item(feature, measured_variable, t, x, window, experiment,
                 subexperiment, case, sweep_index, provenance, names_used,
                 recording, series_dir) -> dict | None:
    """The recorded sweep itself, as a series data_item.

    Written as ``.npy`` sidecars rather than inline: a sweep is tens of
    thousands of samples and an obs_data carrying a hundred of them inline is a
    file no editor can open. ``weight`` defaults to 0 -- the usual reason to
    include a trace is to see it plotted against the simulation, and a weighted
    series would quietly dominate a cost made of a dozen scalars.

    Returns None when there is nowhere to write the sidecars, with the caller
    left to carry on: losing a plot-only trace is not worth failing a run for.
    """
    if not series_dir:
        return None
    os.makedirs(series_dir, exist_ok=True)
    stem = f"{_slug(case)}_sw{sweep_index}"
    t_win, x_win = window.slice(t), window.slice(x)
    t_path = os.path.join(series_dir, f"{stem}_t.npy")
    v_path = os.path.join(series_dir, f"{stem}_v.npy")
    np.save(t_path, np.asarray(t_win, dtype=float) - float(t_win[0]) if t_win.size
            else np.asarray(t_win, dtype=float))
    np.save(v_path, np.asarray(x_win, dtype=float))

    dt = float(np.median(np.diff(t_win))) if t_win.size > 1 else 0.0
    name = _unique_name({"name_suffix": feature.get("name_suffix") or "gt"},
                        type("P", (), {"operation": "series"})(), case,
                        sweep_index, names_used)
    return {
        "data_item_name": name,
        "trace_name_for_plotting": f"{case} sw{sweep_index}",
        "item_name_for_plotting": name,
        "data_type": "series",
        "operands": ["time", measured_variable],
        "unit": feature.get("unit") or "dimensionless",
        "weight": float(feature.get("weight", 0.0)),
        "std": float(_std_for(feature, float(np.max(np.abs(x_win))) if x_win.size else 1.0)),
        "obs_dt": dt,
        "t_path": t_path,
        "value_path": v_path,
        "experiment_idx": int(experiment),
        "subexperiment_idx": int(subexperiment),
        "plot_type": "series",
        "source": _source(recording, case, sweep_index, provenance),
    }


def _data_item(feature, plan, value, experiment, subexperiment, case, sweep_index,
               provenance, names_used, recording, group) -> dict:
    name = _unique_name(feature, plan, case, sweep_index, names_used)
    item = {
        "data_item_name": name,
        "trace_name_for_plotting": f"{case} sw{sweep_index}",
        "item_name_for_plotting": name,
        "data_type": "constant",
        "operation": plan.operation,
        "operands": list(plan.operands),
        "operation_kwargs": {k: v for k, v in plan.kwargs.items()
                             if k != "series_output"},
        "unit": feature.get("unit") or "dimensionless",
        "weight": float(feature.get("weight", 1.0)),
        "value": float(value),
        "experiment_idx": int(experiment),
        "subexperiment_idx": int(subexperiment),
        "plot_type": feature.get("plot_type") or "horizontal",
        # Where this number came from, to the sweep. The editor shows it and it
        # round-trips on save, so a document keeps its provenance.
        "source": _source(recording, case, sweep_index, provenance),
    }
    std = _std_for(feature, value)
    if std is not None:
        item["std"] = float(std)
    if feature.get("cost_type"):
        item["cost_type"] = feature["cost_type"]
    if feature.get("cost_kwargs"):
        item["cost_kwargs"] = dict(feature["cost_kwargs"])
    for key in ("species", "location"):
        if provenance.get(key):
            item[key] = provenance[key]
    return item


def _source(recording, case: str, sweep_index: int, provenance: dict) -> str:
    text = provenance.get("source_text") or ""
    where = f"{case} sweep {sweep_index} ({recording.format})"
    return f"{text} [{where}]".strip() if text else where


def _unique_name(feature, plan, case: str, sweep_index: int, used: set) -> str:
    """A ``data_item_name`` unique across the document.

    CA requires uniqueness across data_items and prediction_items together
    (#466), and reports a violation as a duplicate name -- which names the
    consequence, not the cause. Enforcing it here, where the colliding pair is
    still in hand, turns that into an error nobody has to trace back.
    """
    suffix = feature.get("name_suffix") or plan.operation
    base = f"{_slug(case)}_sw{sweep_index}_{_slug(suffix)}"
    name, n = base, 2
    while name in used:
        name = f"{base}_{n}"
        n += 1
    used.add(name)
    return name


def _slug(text: str) -> str:
    out = "".join(c if (c.isalnum() or c == "_") else "_" for c in str(text))
    while "__" in out:
        out = out.replace("__", "_")
    return out.strip("_")


def _std_for(feature: dict, value: float) -> float | None:
    """The item's sigma, from the feature's declared mode.

    Modes rather than a heuristic on the operation's name. The CLI infers sigma
    from substrings ("does the name contain 'freq'?"), which is policy about one
    model's observables dressed up as a rule, and cannot be right for a model it
    has never seen. ``sweep_spread`` -- the sample spread across a dataset's own
    sweeps -- is filled in by the caller when it has the sweeps; here it falls
    back, because a single sweep has no spread.
    """
    spec = feature.get("std")
    if spec is None:
        return max(0.1 * abs(value), 1e-6)
    if isinstance(spec, (int, float)):
        return float(spec)
    mode = str(spec.get("mode") or "fraction")
    if mode == "absolute":
        return float(spec.get("value", 1.0))
    if mode == "fraction":
        return max(float(spec.get("value", 0.1)) * abs(value), 1e-6)
    if mode == "sweep_spread":
        fallback = spec.get("fallback") or {"mode": "fraction", "value": 0.1}
        return _std_for({"std": fallback}, value)
    raise ObsExtractError(f"unknown std mode {mode!r}")


# ---------------------------------------------------------------------------
def _finalise_params_to_change(doc: dict, outcome: Outcome) -> None:
    """Turn the per-experiment maps into CA's dense lists.

    Every key must carry one row per experiment with one value per
    sub-experiment. A key only some experiments drive is back-filled with the
    neutral value, because a ragged ``params_to_change`` is the one way this
    pipeline can silently corrupt a run -- CA indexes it positionally.
    """
    info = doc["protocol_info"]
    n_exp = len(info["sim_times"])
    dense: dict[str, list] = {}
    for key, by_exp in (info["params_to_change"] or {}).items():
        rows = []
        for exp in range(n_exp):
            n_sub = len(info["sim_times"][exp])
            row = by_exp.get(exp)
            if row is None:
                row = [0.0 if _is_command_row(by_exp) else 1.0] * n_sub
            elif len(row) != n_sub:
                raise ObsExtractError(
                    f"params_to_change[{key!r}] experiment {exp} has {len(row)} "
                    f"values for {n_sub} sub-experiments")
            rows.append(list(row))
        dense[key] = rows
    info["params_to_change"] = dense

    lengths = {len(v) for v in dense.values()}
    if lengths and lengths != {n_exp}:
        raise ObsExtractError(
            f"params_to_change rows disagree with {n_exp} experiments: {lengths}")


def _is_command_row(by_exp: dict) -> bool:
    """A command row back-fills with 0.0; a modulated parameter with 1.0.

    Zero is "no stimulus" for a command; one is "unmodulated" for a scalar. Using
    the wrong neutral would either inject current into an experiment that had
    none, or scale a conductance to nothing.
    """
    for row in by_exp.values():
        if any(isinstance(v, str) for v in row):
            return True
    return False


def _validate_with_ca(doc: dict, outcome: Outcome, log) -> None:
    """Let CUFLynx's own validator (and so CA's parser) have the last word.

    Better here than at the editor's Save: the config that produced it is still
    on screen, and the message can be traced to a feature.
    """
    try:
        import obs_data  # noqa: PLC0415
    except ImportError:  # pragma: no cover - obs_data is a sibling module
        return
    try:
        parsed = obs_data.parse_obs_data(doc)
    except Exception as exc:  # noqa: BLE001 - ObsDataError and anything CA raises
        raise ObsExtractError(
            f"the extracted obs_data was rejected: {exc}") from exc
    for warning in getattr(parsed, "warnings", None) or []:
        outcome.warnings.append(str(warning))
        log(f"[warning] {warning}")
