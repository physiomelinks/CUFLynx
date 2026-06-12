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


class ParamsForIdError(ValueError):
    """Raised for a malformed params_for_id CSV (maps to HTTP 422)."""


@dataclass
class ParamEntry:
    qname: str
    min: float
    max: float
    name_for_plotting: str | None
    param_type: str | None
    initial_value: float | None = None

    def as_dict(self) -> dict:
        return {
            "qname": self.qname,
            "min": self.min,
            "max": self.max,
            "name_for_plotting": self.name_for_plotting,
            "param_type": self.param_type,
            "initial_value": self.initial_value,
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
    initial_values = initial_values or {}

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
                    initial_value=initial_values.get(qname),
                )
            )

    if not entries:
        raise ParamsForIdError("no parameter rows found")
    return entries
