"""Tests for saving a calibrated CellML (issue #114)."""

from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from calibrated_model import calibrated_cellml
from cellml_meta import parse_cellml
from conftest import RESOURCES_DIR


def _reparse(text):
    return parse_cellml(text).initial_values


def test_substitutes_best_fit_into_flat_model_parameters():
    text = (RESOURCES_DIR / "3compartment_flat.cellml").read_text()
    best = {"aortic_root/C": 9.99e-09, "global/E_lv_A": 3.33e8, "global/q_lv_init": 1.234e-4}
    new_text, report = calibrated_cellml(text, best)

    assert report == {"updated": list(best), "unresolved": []}
    iv = _reparse(new_text)
    # Written onto the right flat-model constants (parameters / parameters_global).
    assert iv["parameters/C_aortic_root"] == pytest.approx(9.99e-09)
    assert iv["parameters_global/E_lv_A"] == pytest.approx(3.33e8)
    assert iv["parameters_global/q_lv_init"] == pytest.approx(1.234e-4)
    # An untouched parameter keeps its original value.
    assert iv["parameters/R_pvn"] == pytest.approx(1333000.0)
    # Still valid XML.
    ET.fromstring(new_text)


def test_only_the_targeted_lines_change():
    text = (RESOURCES_DIR / "3compartment_flat.cellml").read_text()
    new_text, _ = calibrated_cellml(text, {"aortic_root/C": 1.0, "global/E_lv_A": 2.0})
    changed = sum(1 for a, b in zip(text.splitlines(), new_text.splitlines()) if a != b)
    assert changed == 2  # exactly the two substituted initial_value lines
    assert len(text.splitlines()) == len(new_text.splitlines())


def test_direct_qname_model_substitutes_too():
    """Non-flat model (Lotka-Volterra): best fit written by the direct vessel/param
    name."""
    text = (RESOURCES_DIR / "Lotka_Volterra_forced.cellml").read_text()
    new_text, report = calibrated_cellml(text, {"Lotka_Volterra_module/alpha": 7.5})
    assert report["updated"] == ["Lotka_Volterra_module/alpha"]
    assert _reparse(new_text)["Lotka_Volterra_module/alpha"] == pytest.approx(7.5)


def test_unresolved_params_are_reported_not_dropped():
    text = (RESOURCES_DIR / "3compartment_flat.cellml").read_text()
    new_text, report = calibrated_cellml(text, {"nonexistent_vessel/nope": 1.0})
    assert report["unresolved"] == ["nonexistent_vessel/nope"]
    assert report["updated"] == []
    assert new_text == text  # nothing changed


def test_roundtrip_matches_reloaded_slider_values():
    """The saved calibrated model, reloaded via the same params_for_id path the UI
    uses, yields slider initial values equal to the best fit (the #114 loop)."""
    from params_for_id import parse_params_for_id

    text = (RESOURCES_DIR / "3compartment_flat.cellml").read_text()
    best = {"global/q_lv_init": 8.0e-4, "aortic_root/C": 2.0e-8,
            "global/E_lv_A": 4.0e8, "global/E_lv_B": 2.0e7}
    new_text, _ = calibrated_cellml(text, best)

    meta = parse_cellml(new_text)
    csv = (RESOURCES_DIR / "3compartment_params_for_id.csv").read_bytes()
    loaded = {e.qname: e.initial_value for e in parse_params_for_id(csv, meta.initial_values)}
    for name, val in best.items():
        assert loaded[name] == pytest.approx(val)
