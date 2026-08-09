"""Slider entries from a circulatory_autogen ``params_for_id`` document.

Reads the canonical JSON form; a legacy CSV is converted to it by
:mod:`params_json` first, so the semantics below are stated once rather than
implemented twice. Neither libCellML nor Myokit is imported, so the upload path
stays in the unit tier.

Reproduces the subset of ``PrimitiveParsers._build_param_id_info_from_df`` that
the slider-seeding API needs: bounds validation, prior hyper-parameters, and the
``vessel/param`` qualified names -- including CA's flat-model fallback, where a
constant is renamed to a bare ``param_vessel`` in a ``parameters`` component.

One entry is one parameter, exactly as CA reads it: its ``param_names`` is a list
*per row* of qualified names, and its optimiser carries one value for the whole
list. An entry naming several targets therefore describes one quantity present in
several components, not several quantities -- which is why this module emits a
single :class:`ParamEntry` carrying every member in ``qnames`` (issue #193).
Splitting them apart gave each component its own slider, and moving one without
the others put the model in a state it never has.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import params_json

# Components a circulatory_autogen *flat* model puts its constants in. When a
# params_for_id row's ``vessel/param`` name doesn't exist directly (flat models
# rename constants), the value lives here under the CA "gen name".
_PARAM_COMPONENTS = ("parameters", "parameters_global")


class ParamsForIdError(ValueError):
    """Raised for a malformed params_for_id CSV (maps to HTTP 422)."""


def _derive_bounds(prior: str | None, values: dict, row_idx: int) -> tuple:
    """The range an unbounded row gets, from circulatory_autogen.

    CA owns the rule (the prior's centre plus or minus a span of its scale), so
    the sliders cover exactly the range the calibration will search. A file that
    uses `unbounded` cannot be interpreted without it, so an absent or too-old CA
    is an error naming what is missing rather than a guess at the span.
    """
    try:
        from parsers.PrimitiveParsers import derive_bounds_from_prior  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise ParamsForIdError(
            f"row {row_idx}: 'unbounded' needs a circulatory_autogen that supports it, "
            f"so the parameter's range can be derived from its prior. Point the CA "
            f"directory at a newer checkout, or give min and max."
        ) from exc
    try:
        return derive_bounds_from_prior(prior or "uniform", values, row_idx=row_idx)
    except ValueError as exc:
        raise ParamsForIdError(str(exc)) from exc


def _validate_prior_params(prior: str | None, values: dict, row_idx: int,
                           row_min=None, row_max=None) -> None:
    """Let CA judge this row's prior hyper-parameters, if it can.

    CA owns the rules -- which prior takes which value, that scales are positive
    and finite -- and re-implementing them here is how the two drift apart. Its
    complaint is raised as-is so the message at upload is the message the
    calibration would have given. Silent when CA is unreachable.
    """
    try:
        from parsers.PrimitiveParsers import normalise_prior_params  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return
    row = dict(values)
    if row_min is not None and row_max is not None:
        # So CA can check a centre against the parameter's own range, which is one
        # of its rules; without them it runs every other check.
        row["min"], row["max"] = row_min, row_max
    try:
        normalise_prior_params(prior or "uniform", row, row_idx=row_idx)
    except ValueError as exc:
        raise ParamsForIdError(str(exc)) from exc
    except Exception:  # noqa: BLE001 - a CA problem, not the user's file
        return


def _gen_name(vessel: str, param_name: str) -> str:
    """CA's ``param_names_for_gen`` name for a ``vessel``/``param`` pair — the bare
    constant name a flat model uses. Mirrors ``PrimitiveParsers`` #298 exactly:
    ``global`` -> just ``param``; otherwise ``param_vessel``."""
    return param_name if vessel == "global" else f"{param_name}_{vessel}"


def _build_gen_index(initial_values: dict[str, float]) -> dict[str, dict[str, float]]:
    """Index the model's initial values by *bare* variable name (last path segment)
    so a flat model's ``parameters/<gen>`` constants can be found by ``<gen>``."""
    idx: dict[str, dict[str, float]] = {}
    for qname, val in initial_values.items():
        idx.setdefault(qname.rsplit("/", 1)[-1], {})[qname] = val
    return idx


def resolve_model_qname(
    vessel: str,
    param_name: str,
    initial_values: dict[str, float],
    gen_index: dict[str, dict[str, float]],
) -> str | None:
    """The model variable qname (``component/variable``) a params_for_id
    ``vessel``/``param`` entry refers to, or None if it can't be resolved.

    Tries the direct ``vessel/param`` name first (non-flat models, e.g.
    Lotka-Volterra). If that isn't in the model, falls back to CA's flat-model
    convention: the constant is named ``_gen_name(vessel, param)`` and lives in a
    ``parameters`` component (issue #114). The fallback is only used when it
    resolves unambiguously, so a coincidental bare-name clash never picks a wrong
    variable — for both reading the loaded value and writing a calibrated one.
    """
    direct = f"{vessel}/{param_name}"
    if direct in initial_values:
        return direct

    hits = gen_index.get(_gen_name(vessel, param_name))
    if not hits:
        return None
    if len(hits) == 1:
        return next(iter(hits))
    # Ambiguous bare name -> prefer the flat model's parameters component.
    preferred = [q for q in hits if q.split("/", 1)[0] in _PARAM_COMPONENTS]
    if len(preferred) == 1:
        return preferred[0]
    return None


def _resolve_initial_value(
    vessel: str,
    param_name: str,
    initial_values: dict[str, float],
    gen_index: dict[str, dict[str, float]],
) -> float | None:
    """The model's initial value for a params_for_id ``vessel``/``param`` entry."""
    key = resolve_model_qname(vessel, param_name, initial_values, gen_index)
    return None if key is None else initial_values[key]


def _target_initial_value(
    targets: list[str],
    initial_values: dict[str, float],
    gen_index: dict[str, dict[str, float]],
) -> tuple[float | None, str | None]:
    """The one initial value a grouped entry starts from, plus any warning about it.

    The members of a group are the *same* quantity written into several
    components, so the model should already give them the same number. When it
    doesn't, the row is asking for a state the model was never in, and the first
    member's value is a guess -- so it is used (something has to seed the slider)
    and the disagreement is reported rather than swallowed, because the moment the
    slider is touched every member is overwritten with it and the evidence is
    gone. Members the model has no variable for contribute nothing either way:
    that is the pre-existing "unresolved parameter" case, not a conflict.
    """
    resolved: list[tuple[str, float]] = []
    for target in targets:
        vessel, _, param_name = target.rpartition("/")
        val = _resolve_initial_value(vessel, param_name, initial_values, gen_index)
        if val is not None:
            resolved.append((target, val))
    if not resolved:
        return None, None

    value = resolved[0][1]
    differing = [
        (q, v)
        for q, v in resolved
        if not math.isclose(v, value, rel_tol=1e-9, abs_tol=0.0)
    ]
    if not differing:
        return value, None

    # Named by the shared parameter when there is one; a group of differently
    # named parameters has no single name to report, so it is described by its
    # members instead.
    names = {t.rpartition("/")[2] for t in targets}
    described = f"parameter '{next(iter(names))}'" if len(names) == 1 else "parameter group"
    shown = ", ".join(f"{q} = {v:g}" for q, v in resolved)
    return value, (
        f"grouped {described} starts from different values in its "
        f"components ({shown}); the slider uses {value:g} and will set them all."
    )


@dataclass
class ParamEntry:
    # The group's representative -- the first member -- so a single-vessel row is
    # the qname it always was and every downstream lookup keeps working.
    qname: str
    min: float
    max: float
    name_for_plotting: str | None
    param_type: str | None
    initial_value: float | None = None
    comment: str | None = None
    prior: str | None = None
    # No min/max of its own: the prior says where it lives, and CA derives the
    # range it needs for the search box and normalisation from that prior.
    unbounded: bool = False
    # The values this row's prior takes (prior_mean, prior_std, prior_lambda...),
    # keyed by CA's column name. A dict rather than fields, because which values
    # exist is CA's vocabulary and grows there.
    prior_params: dict | None = None
    # Every qname this row names, in file order, ``qname`` first (issue #193).
    # A one-vessel row has exactly one, so consumers can treat this as the truth
    # and never special-case the grouped form.
    qnames: list[str] = field(default_factory=list)
    # Something the user should know about this row that is not an error -- so far
    # only a group whose components disagree on their initial value.
    warning: str | None = None

    def __post_init__(self) -> None:
        if not self.qnames:
            self.qnames = [self.qname]

    def as_dict(self) -> dict:
        return {
            "qname": self.qname,
            "qnames": list(self.qnames),
            "warning": self.warning,
            "min": self.min,
            "max": self.max,
            "name_for_plotting": self.name_for_plotting,
            "param_type": self.param_type,
            "initial_value": self.initial_value,
            "comment": self.comment,
            "prior": self.prior,
            "unbounded": self.unbounded,
            "prior_params": self.prior_params or {},
        }


def parse_params_for_id(
    data: bytes | str | dict,
    initial_values: dict[str, float] | None = None,
) -> list[ParamEntry]:
    """Parse a params_for_id document into a list of slider entries.

    Accepts the canonical JSON form (:mod:`params_json`) or a legacy CSV. **A CSV
    is converted to that JSON first**, so there is a single code path after the
    front door rather than two parsers to keep in step -- which is the same shape
    CA reads these files with.

    One :class:`ParamEntry` per parameter, as CA reads it: an entry's targets are
    one quantity present in several components, and the optimiser carries one
    value for the whole list, so they share one slider (issue #193).
    """
    try:
        if isinstance(data, (dict, list)) or params_json.looks_like_json(data):
            doc = params_json.load_doc(data)
        else:
            doc = params_json.csv_to_json(data)
    except params_json.ParamsJsonError as exc:
        # One error type reaches the API, which maps it to 422. The JSON layer
        # cannot import this one without a cycle, so it is translated here.
        raise ParamsForIdError(str(exc)) from exc

    return _entries_from_doc(doc, initial_values)


def _bounds(item: dict, idx: int, label: str) -> tuple:
    """This entry's authored ``min``/``max``, or ``(None, None)`` if not stated.

    Left as None when absent rather than defaulted: an ``unbounded`` entry
    legitimately omits both and has its range derived from the prior instead.
    """
    raw_min, raw_max = item.get("min"), item.get("max")
    if raw_min is None or raw_max is None or str(raw_min).strip() == "" or str(raw_max).strip() == "":
        return None, None
    try:
        pmin, pmax = float(raw_min), float(raw_max)
    except (TypeError, ValueError) as exc:
        raise ParamsForIdError(f"row {idx}: min/max must be numeric") from exc
    if pmin > pmax:
        raise ParamsForIdError(f"row {idx} ({label}): min ({pmin}) > max ({pmax})")
    return pmin, pmax


def _text(item: dict, key: str) -> str | None:
    value = item.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _entries_from_doc(
    doc: dict,
    initial_values: dict[str, float] | None = None,
) -> list[ParamEntry]:
    """Slider entries from the canonical JSON form.

    Everything semantic lives here -- bounds, prior validation, unbounded
    derivation, initial-value resolution -- so that reading a CSV and reading a
    JSON cannot disagree about any of it.
    """
    initial_values = initial_values or {}
    gen_index = _build_gen_index(initial_values)
    prior_names = params_json.prior_param_names()

    entries: list[ParamEntry] = []
    for idx, item in enumerate(doc.get("params") or []):
        targets = [str(t).strip() for t in (item.get("targets") or []) if str(t).strip()]
        if not targets:
            raise ParamsForIdError(f"row {idx}: no targets")
        # The entry's identity in messages and, later, for a modifier to
        # reference. Falls back to the first target, which is what a converted
        # CSV row carries.
        label = _text(item, "name") or targets[0]

        pmin, pmax = _bounds(item, idx, label)
        name_for_plotting = _text(item, "name_for_plotting")
        param_type = _text(item, "param_type")
        # `comment` is a free-text annotation (issue #25).
        comment = _text(item, "comment")

        # `prior` selects the MCMC/UQ prior (CA's PARAM_PRIOR_TYPES). Carried
        # through rather than dropped: the editor used to rewrite the file
        # without it, silently reverting every non-uniform prior to uniform.
        # Left verbatim -- CA canonicalises and validates it, and duplicating
        # that here is how the two drift apart.
        prior = _text(item, "prior")

        # The values that prior takes, restricted to the names CA recognises so
        # an unrelated key cannot masquerade as a hyper-parameter. An absent one
        # means "not stated", which CA turns into its documented default.
        raw_prior_params = item.get("prior_params") or {}
        prior_params = {}
        for name in prior_names:
            if name not in raw_prior_params:
                continue
            text = str(raw_prior_params[name]).strip()
            if text:
                prior_params[name] = text

        unbounded = bool(item.get("unbounded"))
        _validate_prior_params(prior, prior_params, idx, row_min=pmin, row_max=pmax)

        if unbounded:
            # CA owns the derivation (centre +/- a span of the scale) so the
            # sliders cover the same range the calibration will search.
            pmin, pmax = _derive_bounds(prior, prior_params, idx)
        elif pmin is None:
            raise ParamsForIdError(
                f"row {idx} ({label}): min and max are required unless "
                f"'unbounded' is set."
            )

        initial_value, warning = _target_initial_value(
            targets, initial_values, gen_index
        )
        entries.append(
            ParamEntry(
                qname=targets[0],
                qnames=targets,
                min=pmin,
                max=pmax,
                name_for_plotting=name_for_plotting,
                param_type=param_type,
                initial_value=initial_value,
                comment=comment,
                prior=prior,
                unbounded=unbounded,
                prior_params=dict(prior_params),
                warning=warning,
            )
        )

    if not entries:
        raise ParamsForIdError("no parameter rows found")
    return entries
