"""The Advanced settings table (apps/web/src/lib/advancedSettings.json) names CA options that
CA still has.

The table lists only the advanced settings, so an option CA adds is a standard one with no
change here; what can go wrong is an option CA renames or removes, whose entry would then
match nothing and leave the new name standard without anyone noticing. This compares every
``ca`` name with CA's own schemas: PARAM_ID_METHODS options for calibration, and
ANALYSIS_OPTIONS for the analyses.

Integration tier: it reads the installed libcuflynx's schemas, and the unit tier's CA pin
can predate options the table names (jacobian_points, redundancy_statistic, ...).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import solver_options

TABLE = Path(__file__).resolve().parents[2] / "web" / "src" / "lib" / "advancedSettings.json"

#: the table's tab -> its ANALYSIS_OPTIONS mode
MODES = {"sensitivity": "sensitivity_analysis", "uq": "uq", "emulator": "emulation",
         "identifiability": "identifiability_analysis", "reduction": "param_reduction"}


def _names(options) -> set[str]:
    return {o.get("name") for o in options or [] if isinstance(o, dict)}


def _ca_names() -> dict[str, set[str]]:
    out = {"calibration": set()}
    for method in solver_options.get_param_id_methods(refresh=True):
        out["calibration"] |= _names(method.get("options"))
    analysis = solver_options.get_analysis_options(refresh=True)
    for tab, mode in MODES.items():
        out[tab] = _names((analysis.get(mode) or {}).get("options"))
    return out


@pytest.mark.integration
def test_every_advanced_ca_setting_is_still_one_of_cas_options():
    if not solver_options.analysis_options_introspected():
        pytest.skip("CA's analysis options could not be read: "
                    f"{solver_options.analysis_options_error()}")
    table = json.loads(TABLE.read_text())
    ca = _ca_names()
    stale = {tab: sorted(set(entry["ca"]) - ca.get(tab, set()))
             for tab, entry in table.items() if isinstance(entry, dict)}
    assert not {tab: names for tab, names in stale.items() if names}, (
        "advancedSettings.json names settings CA no longer has (renamed or removed): "
        f"{stale}. Use CA's new name, or drop the entry.")
