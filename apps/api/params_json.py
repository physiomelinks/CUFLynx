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

**This module duplicates a conversion CA also owns**
(``ObsAndParamDataParser.params_for_id_csv_to_json``). Deliberately, and
temporarily: the params editor must work with no CA on ``sys.path`` at all --
the packaged app with no CA directory chosen is a supported state -- and
silently refusing to read a user's CSV there would be worse than the
duplication. CA's version is preferred whenever it is importable, so the two
cannot disagree in the configuration that matters. Remove this fallback only
when CA is a hard dependency.
"""

from __future__ import annotations

import io
import json

import pandas as pd

SCHEMA_VERSION = 1

# The columns a CSV may carry that map to a JSON key of the same name. Bounds and
# names are handled separately because they are required or composite.
_PASSTHROUGH_COLUMNS = ("param_type", "name_for_plotting", "comment", "prior")

REQUIRED_CSV_COLUMNS = ("vessel_name", "param_name", "min", "max")


class ParamsJsonError(ValueError):
    """Raised for a malformed params_for_id document (maps to HTTP 422)."""


def prior_param_names() -> tuple:
    """The prior hyper-parameter names CA recognises.

    Introspected from CA's vocabulary with a fallback, so a hyper-parameter CA
    adds is carried through without a change here. Deliberately *not* conditional
    on CA being importable: these names decide whether a column is read at all,
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


def _is_blank(value) -> bool:
    if value is None:
        return True
    try:
        if pd.isna(value):
            return True
    except (TypeError, ValueError):  # arrays/lists aren't NaN-checkable
        pass
    return str(value).strip() == ""


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "1.0", "true", "yes", "y")


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
    """Parse and normalise a params_for_id JSON payload."""
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

    return {
        "version": doc.get("version", SCHEMA_VERSION),
        "defaults": defaults,
        "params": [_resolved(entry, defaults, idx) for idx, entry in enumerate(params)],
    }


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
    """A params_for_id CSV as the canonical JSON structure.

    Prefers CA's own converter so the two repositories cannot disagree about the
    mapping; falls back to the local one below when CA is not importable.
    """
    try:
        from engine import _ensure_ca_on_path  # noqa: PLC0415

        _ensure_ca_on_path()
        from parsers.PrimitiveParsers import ObsAndParamDataParser  # noqa: PLC0415

        convert = getattr(ObsAndParamDataParser, "params_for_id_csv_to_json", None)
        if convert is not None:
            text = data.decode("utf-8-sig", errors="replace") if isinstance(data, bytes) else data
            return load_doc(convert(text))
    except Exception:  # noqa: BLE001 - no CA, or a CA too old to have it
        pass
    return _local_csv_to_json(data)


def _local_csv_to_json(data: bytes | str) -> dict:
    """The CSV -> JSON mapping, implemented without CA.

    Kept faithful to CA's column semantics:

    - ``vessel_name`` is whitespace-split and each vessel is joined to the single
      ``param_name``, giving one qname per vessel (CA builds
      ``vessel_name[i] + '/' + param_name`` the same way).
    - prior hyper-parameter columns collapse into a ``prior_params`` object.
    - blank cells mean "not stated" and are omitted, not written as empty
      strings, so CA's own defaulting still applies downstream.
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig", errors="replace")

    try:
        df = pd.read_csv(io.StringIO(data), skipinitialspace=True)
    except Exception as exc:  # pandas raises many flavours of error
        raise ParamsJsonError(f"could not parse CSV: {exc}") from exc

    df.columns = [str(c).strip() for c in df.columns]
    missing = [c for c in REQUIRED_CSV_COLUMNS if c not in df.columns]
    if missing:
        raise ParamsJsonError(f"missing required column(s): {', '.join(missing)}")

    prior_columns = [n for n in prior_param_names() if n in df.columns]
    has_unbounded = "unbounded" in df.columns

    params = []
    for idx, row in df.iterrows():
        param_name = str(row["param_name"]).strip()
        vessels = str(row["vessel_name"]).split()
        if not vessels:
            raise ParamsJsonError(f"row {idx}: empty vessel_name")

        entry: dict = {"targets": [f"{vessel}/{param_name}" for vessel in vessels]}
        # The name is the row's identity for a modifier to reference later. The
        # first target keeps it stable and unique for the single-parameter case,
        # which is what nearly every row is.
        entry["name"] = entry["targets"][0]

        for column in _PASSTHROUGH_COLUMNS:
            if column in df.columns and not _is_blank(row[column]):
                entry[column] = str(row[column]).strip()

        for bound in ("min", "max"):
            if _is_blank(row[bound]):
                continue
            # Coerced here so the document is JSON-serialisable (pandas hands
            # back numpy scalars). A value that will not convert is passed
            # through untouched so the numeric complaint is raised once,
            # downstream, where the row's identity is known.
            try:
                entry[bound] = float(row[bound])
            except (TypeError, ValueError):
                entry[bound] = str(row[bound]).strip()

        prior_params = {
            column: str(row[column]).strip()
            for column in prior_columns
            if not _is_blank(row[column])
        }
        if prior_params:
            entry["prior_params"] = prior_params

        # Only written when true: an absent key reads as "not unbounded", and
        # writing `false` on every row would add noise to every converted file.
        if has_unbounded and not _is_blank(row["unbounded"]) and _truthy(row["unbounded"]):
            entry["unbounded"] = True

        params.append(entry)

    if not params:
        raise ParamsJsonError("no parameter rows found")
    return {"version": SCHEMA_VERSION, "defaults": {}, "params": params}


def entries_to_json(entries, defaults: dict | None = None) -> dict:
    """Canonical JSON for a list of :class:`params_for_id.ParamEntry`.

    What the editor writes back. ``targets`` order is preserved exactly as read:
    CA's ``baselines[i]`` are index-aligned with ``targets[i]``, so reordering
    here would silently pair a scale factor with the wrong parameter's baseline.
    """
    params = []
    for entry in entries:
        item: dict = {
            "name": getattr(entry, "name", None) or entry.qnames[0],
            "targets": list(entry.qnames),
        }
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
