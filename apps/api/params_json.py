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

**The CSV -> JSON conversion is CA's whenever CA is importable**
(``ObsAndParamDataParser.params_for_id_csv_to_json``), so a column CA adds flows
through without an edit here and the two repositories cannot disagree about the
mapping.

When CA is *not* importable there is a local fallback,
``_csv_to_json_without_ca``. #208 removed an earlier one on the grounds that "a
fallback that only runs where CA is absent is a fallback that drifts
unobserved", and that objection was correct about *that* fallback: nothing
compared it with CA. It is reinstated because the state it serves turned out not
to be a corner -- a freshly downloaded release has no CA directory, so the app
could not open a params_for_id CSV at all, which is the first thing a study
needs -- and because the drift is now observed: ``test_params_csv_fallback_
matches_ca`` asserts the two produce the same document, and runs in CI, where CA
*is* importable. A change to CA's mapping fails there rather than silently
giving packaged users a different study.

Reproducing the mapping is also sanctioned by CA, whose converter is documented
as pure with the mapping written down "because a tool without circulatory_autogen
on sys.path has to be able to reproduce it".

The real fix is bundling CA (#18); this is what makes the app usable until then.
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


#: CSV columns that become ``prior_params`` entries rather than top-level keys.
#: CA derives this from its prior-type declarations; mirrored here for the
#: fallback below, and pinned to CA's list by
#: ``test_params_csv_fallback_matches_ca`` so a hyper-parameter added there
#: cannot go missing here unnoticed.
_CSV_PRIOR_COLUMNS = ("prior_lambda", "prior_origin", "prior_scale", "prior_mean", "prior_std")

#: Columns that map straight to a top-level key of the same name.
_CSV_PASSTHROUGH_COLUMNS = ("param_type", "name_for_plotting", "comment", "prior", "min", "max")

#: The spellings CA accepts for a boolean cell.
_CSV_TRUE = frozenset({"1", "1.0", "true", "yes", "y"})
_CSV_FALSE = frozenset({"0", "0.0", "false", "no", "n"})


def _truthy_cell(value: str, column: str) -> bool:
    """A params_for_id boolean cell, CA's spelling rules.

    Anything unrecognised is an error rather than a quiet False: a cell the user
    filled in must not be ignored.
    """
    text = str(value).strip().lower()
    if text in _CSV_TRUE:
        return True
    if text in _CSV_FALSE:
        return False
    raise ParamsJsonError(
        f"'{column}' must be true/false (or 1/0, yes/no), got {value!r}."
    )


def _csv_to_json_without_ca(text: str) -> dict:
    """The params_for_id CSV mapping, done locally.

    Used only when circulatory_autogen cannot be imported. That is the state a
    freshly downloaded release starts in -- CA is chosen at runtime and is not
    bundled -- and refusing there made the packaged app unable to open a
    params_for_id CSV at all, which is the first thing a study needs.

    Reproducing CA's mapping here is sanctioned by CA itself: its converter is
    documented as pure, with the mapping written down in the tutorial "because a
    tool without circulatory_autogen on sys.path has to be able to reproduce
    it". CA stays the authority whenever it is importable -- this runs only as
    the fallback -- and ``test_params_csv_fallback_matches_ca`` asserts the two
    agree, so a change to CA's mapping fails CI here rather than silently
    producing a different study.

    Deliberately uses ``csv``, not pandas: the fallback exists for an
    environment that is missing things, so it must not itself depend on one.
    """
    import csv as _csv  # noqa: PLC0415
    import io as _io  # noqa: PLC0415

    reader = _csv.DictReader(_io.StringIO(text))
    columns = [c.strip() for c in (reader.fieldnames or [])]
    if "vessel_name" not in columns or "param_name" not in columns:
        raise ParamsJsonError(
            "params_for_id CSV needs at least the vessel_name and param_name columns; "
            f"got {columns or 'no header row'}."
        )

    def cell(row, key):
        for raw_key, value in row.items():
            if raw_key is not None and raw_key.strip() == key:
                return value.strip() if isinstance(value, str) else value
        return None

    params = []
    for idx, row in enumerate(reader):
        vessels = [v for v in str(cell(row, "vessel_name") or "").split() if v]
        param_name = str(cell(row, "param_name") or "").strip()
        if not vessels or not param_name:
            raise ParamsJsonError(
                f"params_for_id CSV row {idx}: both vessel_name and param_name are "
                f"required (got vessel_name={cell(row, 'vessel_name')!r}, "
                f"param_name={param_name!r})."
            )

        entry = {"targets": [f"{v}/{param_name}" for v in vessels]}
        entry["name"] = entry["targets"][0]
        for key in _CSV_PASSTHROUGH_COLUMNS:
            value = cell(row, key)
            if key in columns and value not in (None, ""):
                entry[key] = str(value).strip()
        unbounded = cell(row, "unbounded")
        if "unbounded" in columns and unbounded not in (None, ""):
            entry["unbounded"] = _truthy_cell(unbounded, "unbounded")

        prior_params = {}
        for key in _CSV_PRIOR_COLUMNS:
            value = cell(row, key)
            if key in columns and value not in (None, ""):
                prior_params[key] = str(value).strip()
        if prior_params:
            entry["prior_params"] = prior_params

        params.append(entry)

    if not params:
        raise ParamsJsonError("params_for_id CSV has a header but no rows.")
    return {"version": SCHEMA_VERSION, "defaults": {}, "params": params}


def csv_to_json(data: bytes | str) -> dict:
    """A params_for_id CSV as the canonical JSON structure.

    CA's converter when CA is importable, because the conversion is CA's to
    define and a column it adds should flow through without an edit here. When
    it is not -- the state a freshly downloaded release starts in, since CA is
    chosen at runtime and not bundled -- :func:`_csv_to_json_without_ca` does
    the same mapping locally rather than the upload being refused.

    CA's parse failures are translated into :class:`ParamsJsonError` -- it
    raises bare ``ValueError``/pandas errors, and anything that is not a
    ``ParamsJsonError`` escapes ``parse_params_for_id`` as an HTTP 500 instead
    of a 422 naming the problem.
    """
    text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
    convert = _ca_csv_converter()
    if convert is None:
        return load_doc(_csv_to_json_without_ca(text))
    try:
        doc = convert(text)
    except Exception as exc:  # noqa: BLE001 - CA raises bare ValueError / pandas errors
        raise ParamsJsonError(f"could not read params_for_id CSV: {exc}") from exc
    return load_doc(doc)


def _ca_csv_converter():
    """CA's ``params_for_id_csv_to_json``, or None when CA cannot provide it."""
    try:
        from engine import _ensure_ca_on_path  # noqa: PLC0415

        _ensure_ca_on_path()
        from parsers.PrimitiveParsers import ObsAndParamDataParser  # noqa: PLC0415

        return ObsAndParamDataParser.params_for_id_csv_to_json
    except (ImportError, AttributeError):
        return None


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
            # The model constants the modifier function declared as inputs, as
            # this entry names them. Written back verbatim: dropping a key the
            # user authored is data loss, and without them CA cannot call a
            # modifier that takes any (`remainder`'s ``subtract``, CA #383).
            if entry.inputs:
                item["inputs"] = dict(entry.inputs)
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
