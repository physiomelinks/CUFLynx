"""The canonical JSON form of a circulatory_autogen ``params_for_id``.

CSV is the format every existing study is written in; JSON is the format the
feature set has outgrown. A CSV row can only say "one parameter name, in these
vessels", because its qualified names are built as ``vessel_name[i] + '/' +
param_name`` -- so a group is forced to share a single ``param_name`` and there
is nowhere to put a parameter that *modifies* other parameters.

So CSV keeps working and is **converted to this JSON on read**: one code path
after the front door, rather than two parsers drifting apart. Everything
downstream sees only the JSON form.

    {
      "version": 1,
      "defaults": {"prior": "uniform"},
      "params": [
        {"name": "C_ao", "targets": ["aortic_root/C"], "min": 1e-9, "max": 5e-8}
      ]
    }

``params`` rather than ``parameters`` because that is already CA's key --
``get_param_id_info_from_entries`` unwraps ``{"params": [...]}``.

``targets`` is a list of full ``component/param`` qualified names, and is the
whole reason for the format: a list of qnames has no reason to share a
``param_name``, so arbitrary parameters can be grouped. One target is the
ordinary single-parameter case; several mean one value set on all of them.

**The CSV -> JSON conversion itself is CA's**
(``ObsAndParamDataParser.params_for_id_csv_to_json``) -- there is exactly one
converter, so the two repositories cannot disagree about the mapping. When CA
is not importable (the packaged app before a circulatory_autogen directory is
chosen in Settings), reading a CSV is an error that says so; the JSON form
still parses without CA. CUFLynx used to carry a duplicate converter for that
state, and retiring it is the #208 close condition: a fallback that only runs
where CA is absent is a fallback that drifts unobserved.
"""

from __future__ import annotations

import json

SCHEMA_VERSION = 1


class ParamsJsonError(ValueError):
    """Raised for a malformed params_for_id document (maps to HTTP 422)."""


def prior_param_names() -> tuple:
    """The prior hyper-parameter names CA recognises.

    Introspected from CA's vocabulary with a fallback, so a hyper-parameter CA
    adds is carried through without a change here. Deliberately *not* conditional
    on CA being importable: these names decide whether a key is read at all,
    and dropping them when CA is unreachable would silently discard the
    hyper-parameters rather than merely fail to validate them.
    """
    try:
        from solver_options import get_param_prior_types  # noqa: PLC0415

        names = tuple(
            spec["name"]
            for t in get_param_prior_types().get("types", [])
            for spec in (t.get("params") or [])
            if spec.get("name")
        )
        if names:
            return names
    except Exception:  # noqa: BLE001 - any CA import/shape problem falls back
        pass
    return ("prior_mean", "prior_std", "prior_lambda")


def looks_like_json(data: bytes | str) -> bool:
    """Whether this payload should be read as JSON rather than CSV.

    Sniffed from the first non-space character rather than from a filename: the
    upload path receives bytes, and a user who renames a file should still get
    the parser their content needs.
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig", errors="replace")
    return data.lstrip()[:1] in ("{", "[")


def load_doc(data: bytes | str | dict) -> dict:
    """Parse and normalise a params_for_id JSON payload.

    Validation and defaults-folding are **CA's** whenever it is importable
    (``resolve_params_for_id_doc``): unknown keys, duplicate names,
    targets-XOR-modifies and the modifier relationship rules, with CA's own
    wording -- the same verdict a calibration would give later. Without CA the
    local fold runs with the minimal structural checks the sliders need;
    everything deeper re-runs at calibration time anyway.
    """
    if isinstance(data, (dict, list)):
        doc = data
    else:
        if isinstance(data, bytes):
            data = data.decode("utf-8-sig", errors="replace")
        try:
            doc = json.loads(data)
        except ValueError as exc:
            raise ParamsJsonError(f"could not parse JSON: {exc}") from exc

    # A bare list is accepted because CA's own entry point does
    # (`get_param_id_info_from_entries` takes a list of dicts), and rejecting
    # what CA accepts would make the two formats subtly different.
    if isinstance(doc, list):
        doc = {"params": doc}
    if not isinstance(doc, dict):
        raise ParamsJsonError("params_for_id JSON must be an object or a list of parameters")

    params = doc.get("params")
    if params is None:
        raise ParamsJsonError("params_for_id JSON has no 'params' list")
    if not isinstance(params, list):
        raise ParamsJsonError("'params' must be a list of parameter objects")

    defaults = doc.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ParamsJsonError("'defaults' must be an object")

    version = doc.get("version", SCHEMA_VERSION)
    resolve = _ca_doc_resolver()
    if resolve is not None and params:
        try:
            resolved = resolve({"version": version, "defaults": defaults, "params": params})
        except ValueError as exc:
            raise ParamsJsonError(str(exc)) from exc
        return {"version": version, "defaults": defaults, "params": resolved}

    return {
        "version": version,
        "defaults": defaults,
        "params": [_resolved(entry, defaults, idx) for idx, entry in enumerate(params)],
    }


def _ca_doc_resolver():
    """CA's ``resolve_params_for_id_doc``, or None when CA is unreachable."""
    try:
        from engine import _ensure_ca_on_path  # noqa: PLC0415

        _ensure_ca_on_path()
        from parsers.PrimitiveParsers import ObsAndParamDataParser  # noqa: PLC0415

        return ObsAndParamDataParser.resolve_params_for_id_doc
    except (ImportError, AttributeError):
        return None


def _resolved(entry, defaults: dict, idx: int) -> dict:
    """One entry with the file's ``defaults`` filled in underneath it.

    Shallow per key, *including* inside ``prior_params``: a defaults block that
    sets ``prior_std`` for the whole file must not wipe an entry's own
    ``prior_mean``. Setting a family-wide prior in one place is the reason the
    block exists, so it has to compose rather than replace.
    """
    if not isinstance(entry, dict):
        raise ParamsJsonError(f"params[{idx}] must be an object")

    merged = dict(defaults)
    merged.update(entry)

    default_priors = defaults.get("prior_params") or {}
    entry_priors = entry.get("prior_params") or {}
    if default_priors or entry_priors:
        merged["prior_params"] = {**default_priors, **entry_priors}
    return merged


def csv_to_json(data: bytes | str) -> dict:
    """A params_for_id CSV as the canonical JSON structure, via CA's converter.

    There is no local fallback: the conversion is CA's alone. Without CA the
    error says how to fix that, because the packaged app starts in exactly this
    state. CA's parse failures are translated into :class:`ParamsJsonError` --
    it raises bare ``ValueError``/pandas errors, and anything that is not a
    ``ParamsJsonError`` escapes ``parse_params_for_id`` as an HTTP 500 instead
    of a 422 naming the problem.
    """
    try:
        from engine import _ensure_ca_on_path  # noqa: PLC0415

        _ensure_ca_on_path()
        from parsers.PrimitiveParsers import ObsAndParamDataParser  # noqa: PLC0415

        convert = ObsAndParamDataParser.params_for_id_csv_to_json
    except (ImportError, AttributeError) as exc:
        raise ParamsJsonError(
            "reading a params_for_id CSV requires circulatory_autogen, which is "
            "not available. Set the circulatory_autogen directory in Settings "
            "(gear icon), or upload the params_for_id JSON form instead."
        ) from exc

    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    try:
        doc = convert(text)
    except Exception as exc:  # noqa: BLE001 - CA raises bare ValueError / pandas errors
        raise ParamsJsonError(f"could not read params_for_id CSV: {exc}") from exc
    return load_doc(doc)


def entries_to_json(entries, defaults: dict | None = None) -> dict:
    """Canonical JSON for a list of :class:`params_for_id.ParamEntry`.

    What the editor writes back. ``targets`` order is preserved exactly as read:
    CA's ``baselines[i]`` are index-aligned with ``targets[i]``, so reordering
    here would silently pair a scale factor with the wrong parameter's baseline.
    """
    params = []
    for entry in entries:
        item: dict = {"name": entry.name or entry.qnames[0]}
        # A modifier writes `modifies` + `operation` and never `targets` -- CA
        # refuses an entry carrying both. Keys outside CA's closed entry-key set
        # must never be invented here, or the file stops being CA-readable.
        if entry.modifies:
            item["modifies"] = list(entry.modifies)
            # CA renamed this key: a modifier acts on parameters, an operation
            # acts on outputs (CA #385). ``operation`` still reads, with a
            # deprecation warning, so files written before this keep working --
            # but nothing new should be written under the old name.
            item["modifier"] = entry.operation or "scale"
        else:
            item["targets"] = list(entry.qnames)
        if entry.param_type:
            item["param_type"] = entry.param_type
        # An unbounded row's bounds are derived, not authored; writing them back
        # would freeze a derived value into the file and stop it tracking the
        # prior it came from.
        if not entry.unbounded:
            if entry.min is not None:
                item["min"] = entry.min
            if entry.max is not None:
                item["max"] = entry.max
        else:
            item["unbounded"] = True
        if entry.name_for_plotting:
            item["name_for_plotting"] = entry.name_for_plotting
        if entry.prior:
            item["prior"] = entry.prior
        if entry.prior_params:
            item["prior_params"] = dict(entry.prior_params)
        if entry.comment:
            item["comment"] = entry.comment
        params.append(item)

    return {"version": SCHEMA_VERSION, "defaults": dict(defaults or {}), "params": params}
