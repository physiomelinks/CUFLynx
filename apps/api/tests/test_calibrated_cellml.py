"""Tests for saving a calibrated CellML (issue #114).

The substitution tests run on both writers: libcuflynx's
``substitute_parameter_values`` (what ``substitute_values`` uses when the
configured CA has it) and the built-in fallback, so the two agree on what a
calibrated model is.
"""

from __future__ import annotations

from xml.etree import ElementTree as ET

import pytest

from calibrated_cellml import calibrated_cellml
from cellml_meta import parse_cellml
from conftest import RESOURCES_DIR


@pytest.fixture(params=["ca", "fallback"])
def writer(request):
    if request.param == "fallback":
        return calibrated_cellml
    import calibrated_cellml as module

    found = module.ca_substitute_parameter_values()
    if found is None:
        pytest.skip("the configured circulatory_autogen has no substitute_parameter_values")
    return found


def _reparse(text):
    return parse_cellml(text).initial_values


def test_no_app_module_shares_a_name_with_one_on_cas_paths():
    """The runners put circulatory_autogen's directories ahead of their own on
    ``sys.path`` (``ca_imports.ensure_ca_path``), so an app module named like one
    of CA's imports CA's instead: ``from calibrated_model import ...`` in the
    calibration runner reached ``libcuflynx/param_id/calibrated_model.py``."""
    import os

    import ca_imports

    app_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    mine = {name for name in os.listdir(app_dir) if name.endswith(".py")}
    dirs = [path for path in ca_imports.ca_paths() if os.path.isdir(path)]
    if not dirs:
        pytest.skip("no circulatory_autogen directories on this machine")
    clashes = {path: sorted(mine & set(os.listdir(path))) for path in dirs}
    assert not any(clashes.values()), clashes


def test_substitutes_best_fit_into_flat_model_parameters(writer):
    text = (RESOURCES_DIR / "3compartment_flat.cellml").read_text()
    best = {"aortic_root/C": 9.99e-09, "global/E_lv_A": 3.33e8, "global/q_lv_init": 1.234e-4}
    new_text, report = writer(text, best)

    assert report["updated"] == list(best)
    assert report["unresolved"] == []
    # Where each name landed, so a caller can say which component was written
    # without redoing the resolution (the PhLynx send warns on anything outside
    # `parameters` / `parameters_global`, #287).
    assert report["resolved"] == {
        "aortic_root/C": "parameters/C_aortic_root",
        "global/E_lv_A": "parameters_global/E_lv_A",
        "global/q_lv_init": "parameters_global/q_lv_init",
    }
    iv = _reparse(new_text)
    # Written onto the right flat-model constants (parameters / parameters_global).
    assert iv["parameters/C_aortic_root"] == pytest.approx(9.99e-09)
    assert iv["parameters_global/E_lv_A"] == pytest.approx(3.33e8)
    assert iv["parameters_global/q_lv_init"] == pytest.approx(1.234e-4)
    # An untouched parameter keeps its original value.
    assert iv["parameters/R_pvn"] == pytest.approx(1333000.0)
    # Still valid XML.
    ET.fromstring(new_text)


def test_only_the_targeted_lines_change(writer):
    text = (RESOURCES_DIR / "3compartment_flat.cellml").read_text()
    new_text, _ = writer(text, {"aortic_root/C": 1.0, "global/E_lv_A": 2.0})
    changed = sum(1 for a, b in zip(text.splitlines(), new_text.splitlines()) if a != b)
    assert changed == 2  # exactly the two substituted initial_value lines
    assert len(text.splitlines()) == len(new_text.splitlines())


def test_direct_qname_model_substitutes_too(writer):
    """Non-flat model (Lotka-Volterra): best fit written by the direct vessel/param
    name."""
    text = (RESOURCES_DIR / "Lotka_Volterra_forced.cellml").read_text()
    new_text, report = writer(text, {"Lotka_Volterra_module/alpha": 7.5})
    assert report["updated"] == ["Lotka_Volterra_module/alpha"]
    assert _reparse(new_text)["Lotka_Volterra_module/alpha"] == pytest.approx(7.5)


def test_unresolved_params_are_reported_not_dropped(writer):
    text = (RESOURCES_DIR / "3compartment_flat.cellml").read_text()
    new_text, report = writer(text, {"nonexistent_vessel/nope": 1.0})
    assert report["unresolved"] == ["nonexistent_vessel/nope"]
    assert report["updated"] == []
    assert new_text == text  # nothing changed


def test_roundtrip_matches_reloaded_slider_values(writer):
    """The saved calibrated model, reloaded via the same params_for_id path the UI
    uses, yields slider initial values equal to the best fit (the #114 loop)."""
    from params_for_id import parse_params_for_id

    text = (RESOURCES_DIR / "3compartment_flat.cellml").read_text()
    best = {"global/q_lv_init": 8.0e-4, "aortic_root/C": 2.0e-8,
            "global/E_lv_A": 4.0e8, "global/E_lv_B": 2.0e7}
    new_text, _ = writer(text, best)

    meta = parse_cellml(new_text)
    csv = (RESOURCES_DIR / "3compartment_params_for_id.csv").read_bytes()
    loaded = {e.qname: e.initial_value for e in parse_params_for_id(csv, meta.initial_values)}
    for name, val in best.items():
        assert loaded[name] == pytest.approx(val)


# ---------------------------------------------------------------------------
# Every caller writes through CA's writer when the configured CA has it
# ---------------------------------------------------------------------------


class _SpyWriter:
    """A fake ``param_id.calibrated_model`` whose writer records its calls."""

    def __init__(self):
        self.calls = []

    def substitute_parameter_values(self, text, values):
        self.calls.append(dict(values))
        return calibrated_cellml(text, values)


@pytest.fixture
def ca_writer(monkeypatch):
    import ca_imports
    from conftest import set_ca_module

    spy = _SpyWriter()
    ca_imports.reset_cache()
    set_ca_module(monkeypatch, "param_id.calibrated_model", spy)
    yield spy
    ca_imports.reset_cache()


@pytest.fixture
def old_ca(monkeypatch):
    """A CA whose ``param_id.calibrated_model`` predates the writer."""
    import types

    import ca_imports
    from conftest import set_ca_module

    ca_imports.reset_cache()
    set_ca_module(monkeypatch, "param_id.calibrated_model",
                  types.ModuleType("calibrated_model"))
    yield
    ca_imports.reset_cache()


LV_TEXT = (RESOURCES_DIR / "Lotka_Volterra_forced.cellml").read_text()
LV_ALPHA = {"Lotka_Volterra_module/alpha": 7.5}


def test_substitute_values_uses_cas_writer(ca_writer):
    import calibrated_cellml as module

    _, report = module.substitute_values(LV_TEXT, LV_ALPHA)
    assert ca_writer.calls == [LV_ALPHA]
    assert report["resolved"] == {"Lotka_Volterra_module/alpha": "Lotka_Volterra_module/alpha"}


def test_substitute_values_falls_back_on_an_old_ca(old_ca, monkeypatch):
    import calibrated_cellml as module

    calls = []
    real = module.calibrated_cellml
    monkeypatch.setattr(module, "calibrated_cellml",
                        lambda text, values: calls.append(values) or real(text, values))
    new_text, report = module.substitute_values(LV_TEXT, LV_ALPHA)
    assert module.ca_substitute_parameter_values() is None
    assert calls == [LV_ALPHA]
    assert _reparse(new_text)["Lotka_Volterra_module/alpha"] == pytest.approx(7.5)


def test_the_phlynx_send_writes_through_cas_writer(ca_writer):
    import omex_export

    omex_export.substitute_values(LV_TEXT, LV_ALPHA)
    assert ca_writer.calls == [LV_ALPHA]


def test_a_library_calibration_writes_through_cas_writer(ca_writer):
    import json

    import library_calibration

    members = [("c.json", json.dumps({"calibrated_parameters": {"alpha": 0.5}}).encode())]
    params = (RESOURCES_DIR / "Lotka_Volterra_params_for_id.csv").read_bytes()
    text, _ = library_calibration.apply(LV_TEXT.encode(), members, params)
    assert ca_writer.calls == [{"Lotka_Volterra_module/alpha": 0.5}]
    assert _reparse(text.decode())["Lotka_Volterra_module/alpha"] == pytest.approx(0.5)


def test_a_component_inside_an_import_does_not_hide_the_next_one():
    """An import's self-closing ``<component .../>`` has no body: a match
    starting there ran on to the next ``</component>`` and swallowed it, so a
    value in the first real component was reported written and never was."""
    text = (
        '<model xmlns="http://www.cellml.org/cellml/2.0#" name="m">\n'
        '  <import xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="decay.cellml">\n'
        '    <component component_ref="decay" name="decay_1"/>\n'
        "  </import>\n"
        '  <component name="instance_parameters">\n'
        '    <variable name="decay_1_k" units="dimensionless" initial_value="0.5"'
        ' interface="public"/>\n'
        "  </component>\n"
        "</model>\n"
    )
    new_text, report = calibrated_cellml(text, {"instance_parameters/decay_1_k": 0.25})
    assert report["updated"] == ["instance_parameters/decay_1_k"]
    assert parse_cellml(new_text).initial_values["instance_parameters/decay_1_k"] == 0.25
