"""The saved decisions: ``obs_extraction_config.json``.

Choosing which of several hundred recordings contribute, and what to measure on
each, is an afternoon's work. The point of this file is that the afternoon
happens once: reload it, change one feature, extract again.

**One document, where the CLI has two.** It keeps a ``{name}_selection.json`` per
run *and* a global ``subprotocol_io_config.json``, and the split causes three
problems still visible in that repo:

1. The I/O config is keyed on the **subprotocol name alone**, lowercased, while
   the feature configs are keyed on ``protocol|subprotocol``. So ``4AP`` and
   ``Verapamil`` recordings of ``Currentsteps`` are forced to share one stimulus
   kind and one modulated parameter, though nothing else about them is shared.
   The two halves of one decision are keyed differently.
2. They drift. ``subprotocol_io_config.json`` still names
   ``peak_inward_current_in_range``, an operation that no longer exists, and
   nothing noticed -- because the selection file that would have been checked
   against it is regenerated separately.
3. Neither carries a version, so a reload cannot migrate.

**Unknown keys are rejected.** The same discipline CA applies to obs_data, for
the same reason: a mistyped key that loads cleanly and does nothing is the worst
failure mode this file has. It produces an extraction that runs, succeeds, and
quietly ignores the setting you came to change.
"""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone

from .binding import ModelBinding, suggested_binding
from .discovery import group_key
from .errors import ObsExtractError
from .modifiers import load_modifiers
from .readers import SUPPORTED_SUFFIXES

SCHEMA_VERSION = 1

#: Every key allowed at each level. A dict value means "recurse"; a tuple means
#: "a leaf, these are the allowed values"; None means "a leaf, any value".
_SOURCE = {"id": None, "root": None, "recurse": None, "suffixes": None,
           "exclude": None}
_MODIFIER = {"name": None, "target": None, "modifier": None}
_PREPROCESS = {"clamp_output_hz": None, "savgol_window_seconds": None,
               "voltage_peak_preserve_ratio": None, "stim_detect": None,
               "sim_dt": None}
_BINDING = {"clamp_mode_param": None, "voltage_command_param": None,
            "current_command_param": None, "measured_voltage_variable": None,
            "measured_current_variable": None, "current_command_scale": None,
            "voltage_command_scale": None, "units": None, "model_name": None}
_RANGE = {"basis": None, "start_s": None, "end_s": None}
_STD = {"mode": None, "value": None, "fallback": None}
_FEATURE = {"id": None, "operation": None, "operation_kwargs": None,
            "range": _RANGE, "unit": None, "unit_confirmed": None, "std": _STD,
            "weight": None, "cost_type": None, "cost_kwargs": None,
            "plot_type": None, "name_suffix": None}
_TIMELINE = {"pre_time_s": None, "settle_time_s": None,
             "stim_subexperiment_index": None}
_SUBPROTOCOL = {"used": None, "study_role": None, "input": None,
                "sweep_limit": None, "modulated_parameter": None,
                "param_pre_value": None, "param_stim_value": None,
                "include_pre_stim_zerofrequency": None,
                "emit_ground_truth_series": None, "plot_time_window": None,
                "timeline": _TIMELINE, "features": _FEATURE}
_READER = {"format": None, "sample_rate_hz": None, "transpose": None,
           "delimiter": None, "has_header": None, "sweep_column": None,
           "roles": None, "units": None, "channels": None}
_DATASET = {"source_id": None, "path": None, "case_name": None, "protocol": None,
            "subprotocol": None, "used": None, "study_role": None,
            "sweep_limit": None, "sweep_indices": None, "features_override": None,
            "reader": _READER, "condition": None, "pair_id": None, "notes": None}
_PROVENANCE = {"source_text": None, "species": None, "location": None}
_REPORT = {"title": None, "author": None, "compile_pdf": None}
_OUTPUTS = {"obs_data_filename": None, "series_subdir": None, "docs_subdir": None}

_TOP = {
    "obs_extraction_config_version": None, "name": None, "created": None,
    "updated": None, "cuflynx_version": None, "source": _SOURCE,
    "data_modifiers": _MODIFIER, "preprocess": _PREPROCESS,
    "model_binding": _BINDING, "channel_map": None, "subprotocols": _SUBPROTOCOL,
    "datasets": _DATASET, "provenance": _PROVENANCE, "report": _REPORT,
    "outputs": _OUTPUTS,
}

#: What a value means when it is absent. Kept separate from the allow-list so
#: adding a key is one edit in each and a mismatch is a test failure.
DEFAULT_PREPROCESS = {
    "clamp_output_hz": 1000.0,
    "savgol_window_seconds": {"voltage": 5e-3 / 3, "current": 5e-3 * 6},
    "voltage_peak_preserve_ratio": 0.95,
    "stim_detect": {"current_threshold": 10.0, "voltage_threshold": 5.0},
    "sim_dt": None,
}

#: Voltage clamp pins the membrane from the first sample, so it needs no settling
#: phase; current clamp must not begin at the holding current, so it gets one --
#: and that settle is a sub-experiment as well as a pre-time, which is why
#: ``stim_subexperiment_index`` is 1 there and 0 under voltage clamp.
DEFAULT_TIMELINE = {
    "voltage": {"pre_time_s": 0.0, "settle_time_s": None,
                "stim_subexperiment_index": 0},
    "current": {"pre_time_s": 1.0, "settle_time_s": 1.0,
                "stim_subexperiment_index": 1},
}


def new_config(name: str = "extraction", root: str = "", *,
               variables: dict | None = None, cuflynx_version: str = "") -> dict:
    """A valid, empty config -- what the dialog starts from.

    ``cuflynx_version`` is passed in rather than read here. Importing the app's
    ``version`` module would be the first crack in the isolation contract, for a
    string that only ends up in the report -- so the route that has it supplies
    it (see ``obs_extract/__init__``).
    """
    now = _now()
    binding = suggested_binding(variables)
    return {
        "obs_extraction_config_version": SCHEMA_VERSION,
        "name": name,
        "created": now,
        "updated": now,
        "cuflynx_version": str(cuflynx_version or ""),
        "source": {"id": "0", "root": root, "recurse": True,
                   "suffixes": list(SUPPORTED_SUFFIXES), "exclude": []},
        "data_modifiers": [],
        "preprocess": copy.deepcopy(DEFAULT_PREPROCESS),
        "model_binding": _binding_to_config(binding),
        "channel_map": {},
        "subprotocols": {},
        "datasets": [],
        "provenance": {"source_text": "", "species": "", "location": ""},
        "report": {"title": None, "author": None, "compile_pdf": True},
        "outputs": {"obs_data_filename": f"{name}_obs_data.json",
                    "series_subdir": "series_data",
                    "docs_subdir": f"{name}_docs"},
    }


def _binding_to_config(binding: ModelBinding) -> dict:
    return {
        "clamp_mode_param": {"qname": binding.clamp_mode_param,
                             "voltage_value": binding.clamp_voltage_value,
                             "current_value": binding.clamp_current_value},
        "voltage_command_param": binding.voltage_command_param,
        "current_command_param": binding.current_command_param,
        "measured_voltage_variable": binding.measured_voltage_variable,
        "measured_current_variable": binding.measured_current_variable,
        "current_command_scale": binding.current_command_scale,
        "voltage_command_scale": binding.voltage_command_scale,
        "units": dict(binding.units or {}),
    }


def default_subprotocol(stimulus: str = "current") -> dict:
    return {
        "used": False,
        "study_role": "calibration",
        "input": stimulus,
        "sweep_limit": None,
        "modulated_parameter": None,
        "param_pre_value": "auto",
        "param_stim_value": "auto",
        "include_pre_stim_zerofrequency": False,
        "emit_ground_truth_series": True,
        "plot_time_window": {"time_start": None, "time_end": None},
        # None means "derive from `input`". Storing the concrete timeline here
        # would freeze the clamp direction the group was created with: switching
        # a group from current to voltage in the GUI would keep the settling
        # sub-experiment and go on measuring in sub-experiment 1, which for a
        # voltage clamp does not exist. Seen against a real recording.
        "timeline": None,
        "features": [],
    }


def default_dataset(entry: dict) -> dict:
    """A config row for one discovered recording."""
    return {
        "source_id": "0",
        "path": entry.get("path"),
        "case_name": entry.get("case_name"),
        "protocol": entry.get("protocol"),
        "subprotocol": entry.get("subprotocol"),
        "used": False,
        "study_role": None,
        "sweep_limit": None,
        "sweep_indices": None,
        "features_override": None,
        "reader": {"format": entry.get("format")},
        "condition": None,
        "pair_id": None,
        "notes": "",
    }


def merge_scan(config: dict, scan: dict) -> dict:
    """Fold a fresh directory scan into an existing config.

    A rescan must not undo the afternoon. Recordings already in the config keep
    every setting and only have their probe-derived facts refreshed; new files
    arrive unused; files that have disappeared are **kept**, marked
    ``"missing": True`` in the returned scan info rather than silently dropped,
    because a config that quietly forgets a dataset when a drive is unmounted is
    worse than one that says so.
    """
    out = copy.deepcopy(config)
    existing = {d.get("case_name"): d for d in out.get("datasets") or []}
    seen: set[str] = set()

    for entry in scan.get("datasets") or []:
        case = entry.get("case_name")
        seen.add(case)
        if case in existing:
            row = existing[case]
            # The path can move with the directory; the labels may have been
            # edited by hand and must not be overwritten by inference.
            row["path"] = entry.get("path")
            row.setdefault("reader", {})["format"] = entry.get("format")
        else:
            out.setdefault("datasets", []).append(default_dataset(entry))

    for group in scan.get("groups") or []:
        out.setdefault("subprotocols", {}).setdefault(
            group["group"], default_subprotocol())

    missing = [c for c in existing if c not in seen]
    out["_missing"] = missing  # stripped by save(); surfaced by the API
    return out


def validate(config: dict) -> list[str]:
    """Check a config. Returns warnings; raises :class:`ObsExtractError` on error.

    Unknown keys are an error, everywhere. See the module docstring.
    """
    if not isinstance(config, dict):
        raise ObsExtractError("an extraction config must be a JSON object")
    version = config.get("obs_extraction_config_version")
    if version is not None and int(version) > SCHEMA_VERSION:
        raise ObsExtractError(
            f"this config is version {version}; this CUFLynx understands "
            f"version {SCHEMA_VERSION}. Update CUFLynx to open it.")

    _reject_unknown(config, _TOP, "")
    warnings: list[str] = []

    # Compile every expression now, so a typo is reported before an extraction
    # starts writing output rather than part-way through it.
    load_modifiers(config.get("data_modifiers"))

    groups = config.get("subprotocols") or {}
    for key, group in groups.items():
        if not isinstance(group, dict):
            raise ObsExtractError(f"subprotocols[{key!r}] is not an object")
        kind = group.get("input")
        if kind not in (None, "current", "voltage"):
            raise ObsExtractError(
                f"subprotocols[{key!r}].input is {kind!r}; expected 'current' or "
                f"'voltage'")
        for i, feature in enumerate(group.get("features") or []):
            where = f"subprotocols[{key!r}].features[{i}]"
            if not isinstance(feature, dict):
                raise ObsExtractError(f"{where} is not an object")
            if not feature.get("operation"):
                raise ObsExtractError(f"{where} has no operation")
            rng = feature.get("range") or {}
            if rng.get("start_s") is not None and rng.get("end_s") is not None:
                if float(rng["start_s"]) >= float(rng["end_s"]):
                    raise ObsExtractError(
                        f"{where} has an empty time range "
                        f"({rng['start_s']}..{rng['end_s']} s)")
            if group.get("used") and not feature.get("unit_confirmed"):
                warnings.append(
                    f"{where} ({feature['operation']}) has an unconfirmed unit; "
                    f"extraction will refuse until it is set.")

    used_groups = {k for k, g in groups.items() if g.get("used")}
    used_datasets = [d for d in config.get("datasets") or [] if d.get("used")]
    for d in used_datasets:
        key = group_key(d.get("protocol") or "", d.get("subprotocol") or "")
        if key not in groups:
            warnings.append(
                f"dataset {d.get('case_name')!r} is in group {key!r}, which the "
                f"config has no settings for; it will be skipped.")
    if used_groups and not used_datasets:
        warnings.append(
            "groups are marked used but no dataset is; nothing would be extracted.")
    return warnings


def _reject_unknown(node, allowed, path: str) -> None:
    """Depth-first key check. ``allowed`` mirrors the document's shape."""
    if not isinstance(node, dict):
        return
    for key, value in node.items():
        if key.startswith("_"):
            continue  # transient, stripped on save
        if key not in allowed:
            near = _nearest(key, allowed)
            hint = f" Did you mean {near!r}?" if near else ""
            raise ObsExtractError(
                f"unknown key {key!r} in {path or 'the config'}.{hint}")
        child = allowed[key]
        if isinstance(child, dict):
            here = f"{path}.{key}" if path else key
            if isinstance(value, list):
                for i, item in enumerate(value):
                    _reject_unknown(item, child, f"{here}[{i}]")
            elif isinstance(value, dict) and key in ("subprotocols",):
                for k, item in value.items():
                    _reject_unknown(item, child, f"{here}[{k!r}]")
            else:
                _reject_unknown(value, child, here)


def _nearest(key: str, allowed) -> str | None:
    """The closest allowed key, for a typo. Cheap prefix/substring match only."""
    low = key.lower()
    for candidate in allowed:
        if candidate.lower().startswith(low[:4]) or low in candidate.lower():
            return candidate
    return None


def migrate(config: dict) -> dict:
    """Bring an older document up to the current schema.

    There is one version so far, so this only stamps an unversioned document.
    It exists now rather than later because the alternative -- adding it when
    version 2 arrives -- means version 1 files were never migratable.
    """
    out = copy.deepcopy(config)
    if not out.get("obs_extraction_config_version"):
        out["obs_extraction_config_version"] = SCHEMA_VERSION
    return out


def load(path: str) -> dict:
    if not os.path.isfile(path):
        raise ObsExtractError(f"{path}: no such config file")
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ObsExtractError(f"{os.path.basename(path)}: not valid JSON ({exc})") from exc
    data = migrate(data)
    validate(data)
    return data


def save(config: dict, path: str) -> str:
    """Write the config, dropping the transient keys and stamping ``updated``."""
    out = {k: v for k, v in copy.deepcopy(config).items() if not k.startswith("_")}
    out["obs_extraction_config_version"] = SCHEMA_VERSION
    out["updated"] = _now()
    out.setdefault("created", out["updated"])
    validate(out)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, sort_keys=False)
        fh.write("\n")
    return path


def group_settings(config: dict, protocol: str, subprotocol: str) -> dict | None:
    return (config.get("subprotocols") or {}).get(group_key(protocol, subprotocol))


def features_for(config: dict, dataset: dict) -> list[dict]:
    """The features one dataset contributes -- its override, else its group's."""
    if dataset.get("features_override") is not None:
        return list(dataset["features_override"])
    group = group_settings(config, dataset.get("protocol") or "",
                           dataset.get("subprotocol") or "")
    return list((group or {}).get("features") or [])


def sweep_limit_for(config: dict, dataset: dict) -> int | None:
    if dataset.get("sweep_limit") is not None:
        return int(dataset["sweep_limit"])
    group = group_settings(config, dataset.get("protocol") or "",
                           dataset.get("subprotocol") or "")
    limit = (group or {}).get("sweep_limit")
    return None if limit is None else int(limit)


def preprocess_settings(config: dict) -> dict:
    """``preprocess`` with defaults filled, so callers never guess."""
    out = copy.deepcopy(DEFAULT_PREPROCESS)
    for key, value in (config.get("preprocess") or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key].update(value)
        elif value is not None:
            out[key] = value
    return out


def timeline_for(group: dict) -> dict:
    """A group's timeline: derived from its clamp direction unless overridden.

    The direction is the authority. A voltage clamp pins the membrane from the
    first sample and has one sub-experiment; a current clamp needs a settling
    phase and so has two, with the stimulus in the second. Those are not
    preferences, they are what each protocol *is* -- so a group whose ``input``
    changes gets the matching timeline, and only a key the user set explicitly
    survives the switch.
    """
    kind = group.get("input") or "current"
    out = dict(DEFAULT_TIMELINE.get(kind, DEFAULT_TIMELINE["current"]))
    override = group.get("timeline")
    if isinstance(override, dict):
        out.update({k: v for k, v in override.items() if v is not None})
    return out


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
