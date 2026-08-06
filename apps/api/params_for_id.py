"""Parsing for circulatory_autogen ``*_params_for_id.csv`` files.

Reproduces the subset of
``PrimitiveParsers._build_param_id_info_from_df`` that the slider-seeding API
needs: required-column validation, ``min < max`` checks, and whitespace-split
multi-vessel expansion into ``vessel/param`` qualified names.  Implemented with
pandas only (no libCellML/Myokit) so the upload path stays in the unit tier.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import pandas as pd

REQUIRED_COLUMNS = ("vessel_name", "param_name", "min", "max")

# Components a circulatory_autogen *flat* model puts its constants in. When a
# params_for_id row's ``vessel/param`` name doesn't exist directly (flat models
# rename constants), the value lives here under the CA "gen name".
_PARAM_COMPONENTS = ("parameters", "parameters_global")


class ParamsForIdError(ValueError):
    """Raised for a malformed params_for_id CSV (maps to HTTP 422)."""


def _prior_param_names() -> tuple:
    """The prior hyper-parameter column names to recognise in a params_for_id.

    Taken from the same introspect-with-fallback vocabulary the editor renders
    from, so CA decides what a prior may take and a value it adds is carried
    through without a change in this file.

    Deliberately *not* conditional on CA being importable. These names decide
    whether a column in the user's file is read at all, and dropping them when CA
    is unreachable would silently discard the hyper-parameters -- the same data
    loss this column support exists to fix. Only the validation below degrades;
    the values still round-trip.
    """
    try:
        from solver_options import get_param_prior_types  # noqa: PLC0415

        return tuple(
            spec["name"]
            for t in get_param_prior_types().get("types", [])
            for spec in (t.get("params") or [])
            if spec.get("name")
        )
    except Exception:  # noqa: BLE001
        return ()


def _validate_prior_params(prior: str | None, values: dict, row_idx: int) -> None:
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
    try:
        normalise_prior_params(prior or "uniform", values, row_idx=row_idx)
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


@dataclass
class ParamEntry:
    qname: str
    min: float
    max: float
    name_for_plotting: str | None
    param_type: str | None
    initial_value: float | None = None
    comment: str | None = None
    prior: str | None = None
    # The values this row's prior takes (prior_mean, prior_std, prior_lambda...),
    # keyed by CA's column name. A dict rather than fields, because which values
    # exist is CA's vocabulary and grows there.
    prior_params: dict | None = None

    def as_dict(self) -> dict:
        return {
            "qname": self.qname,
            "min": self.min,
            "max": self.max,
            "name_for_plotting": self.name_for_plotting,
            "param_type": self.param_type,
            "initial_value": self.initial_value,
            "comment": self.comment,
            "prior": self.prior,
            "prior_params": self.prior_params or {},
        }


def parse_params_for_id(
    data: bytes | str,
    initial_values: dict[str, float] | None = None,
) -> list[ParamEntry]:
    """Parse params_for_id CSV bytes/text into a flat list of slider entries.

    One :class:`ParamEntry` is produced per resolved qname, so a row with a
    space-separated ``vessel_name`` ("a b") expands to multiple entries.
    """
    if isinstance(data, bytes):
        data = data.decode("utf-8-sig", errors="replace")

    try:
        df = pd.read_csv(io.StringIO(data), skipinitialspace=True)
    except Exception as exc:  # pandas raises many flavours of error
        raise ParamsForIdError(f"could not parse CSV: {exc}") from exc

    # Normalise column names (the fixtures use "vessel_name, param_name, ...").
    df.columns = [str(c).strip() for c in df.columns]

    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ParamsForIdError(
            f"missing required column(s): {', '.join(missing)}"
        )

    has_plotting = "name_for_plotting" in df.columns
    has_type = "param_type" in df.columns
    has_comment = "comment" in df.columns
    has_prior = "prior" in df.columns
    prior_param_columns = [n for n in _prior_param_names() if n in df.columns]
    initial_values = initial_values or {}
    gen_index = _build_gen_index(initial_values)

    entries: list[ParamEntry] = []
    for idx, row in df.iterrows():
        param_name = str(row["param_name"]).strip()
        try:
            pmin = float(row["min"])
            pmax = float(row["max"])
        except (TypeError, ValueError) as exc:
            raise ParamsForIdError(
                f"row {idx}: min/max must be numeric"
            ) from exc
        if pmin > pmax:
            raise ParamsForIdError(
                f"row {idx} ({param_name}): min ({pmin}) > max ({pmax})"
            )

        name_for_plotting = (
            str(row["name_for_plotting"]).strip() if has_plotting else None
        )
        param_type = str(row["param_type"]).strip() if has_type else None
        # `comment` is a free-text annotation (issue #25); rows may leave it
        # blank, so treat NaN/empty as "no comment" rather than the string "nan".
        comment = None
        if has_comment and not pd.isna(row["comment"]):
            comment_str = str(row["comment"]).strip()
            comment = comment_str or None

        # `prior` selects the MCMC/UQ prior for this parameter (CA's
        # PARAM_PRIOR_TYPES). Carried through rather than dropped: the editor used
        # to rewrite the CSV without this column, which silently reverted every
        # non-uniform prior to uniform. Left verbatim -- CA canonicalises and
        # validates it, and duplicating that here is how the two drift apart.
        prior = None
        if has_prior and not pd.isna(row["prior"]):
            prior_str = str(row["prior"]).strip()
            prior = prior_str or None

        # The values that prior takes, for the columns CA recognises. Kept as
        # strings-in / floats-out only where stated: an absent cell means "not
        # stated", which CA turns into its documented default.
        prior_params: dict = {}
        for name in prior_param_columns:
            if pd.isna(row[name]):
                continue
            text = str(row[name]).strip()
            if text:
                prior_params[name] = text
        _validate_prior_params(prior, prior_params, idx)

        vessels = str(row["vessel_name"]).split()
        if not vessels:
            raise ParamsForIdError(f"row {idx}: empty vessel_name")
        for vessel in vessels:
            qname = f"{vessel}/{param_name}"
            entries.append(
                ParamEntry(
                    qname=qname,
                    min=pmin,
                    max=pmax,
                    name_for_plotting=name_for_plotting,
                    param_type=param_type,
                    initial_value=_resolve_initial_value(
                        vessel, param_name, initial_values, gen_index
                    ),
                    comment=comment,
                    prior=prior,
                    prior_params=dict(prior_params),
                )
            )

    if not entries:
        raise ParamsForIdError("no parameter rows found")
    return entries
