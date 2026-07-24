import numpy as np
import pytest

from conftest import BG_MODEL_PATH, LV_MODEL_PATH, LV_PARAMS_CSV_PATH, upload_model


def _post_csv_file(client, path, model_id=None):
    url = "/api/params_for_id/upload"
    if model_id:
        url += f"?model_id={model_id}"
    with open(path, "rb") as fh:
        return client.post(url, files={"file": (path.name, fh, "text/csv")})


def _post_csv_text(client, text, model_id=None):
    url = "/api/params_for_id/upload"
    if model_id:
        url += f"?model_id={model_id}"
    return client.post(url, content=text, headers={"content-type": "text/csv"})


# ---------------------------------------------------------------------------
# Unit tier
# ---------------------------------------------------------------------------
def test_upload_lv_csv_returns_four_params(client):
    resp = _post_csv_file(client, LV_PARAMS_CSV_PATH)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["params"]) == 4


def test_lv_qnames_correctly_formed(client):
    resp = _post_csv_file(client, LV_PARAMS_CSV_PATH)
    qnames = {p["qname"] for p in resp.json()["params"]}
    assert qnames == {
        "Lotka_Volterra_module/alpha",
        "Lotka_Volterra_module/beta",
        "Lotka_Volterra_module/delta",
        "Lotka_Volterra_module/gamma",
    }


def test_multi_vessel_name_expands_to_multiple_qnames(client):
    csv = "vessel_name, param_name, min, max\n" "aortic_root venous_root, C, 1, 2\n"
    resp = _post_csv_text(client, csv)
    assert resp.status_code == 200, resp.text
    qnames = {p["qname"] for p in resp.json()["params"]}
    assert qnames == {"aortic_root/C", "venous_root/C"}


def test_missing_required_column_returns_422(client):
    csv = "vessel_name, param_name, max\nmain, alpha_o2, 0.05\n"
    resp = _post_csv_text(client, csv)
    assert resp.status_code == 422


def test_min_greater_than_max_returns_422(client):
    csv = "vessel_name, param_name, min, max\nmain, alpha_o2, 0.05, 0.005\n"
    resp = _post_csv_text(client, csv)
    assert resp.status_code == 422


def test_edit_dialog_csv_format_round_trips(client):
    # Exact format the frontend Edit dialog emits (buildParamsCsv): no spaces
    # after commas, optional param_type column. Guards against format drift.
    csv = (
        "vessel_name,param_name,min,max,name_for_plotting,param_type\n"
        "Lotka_Volterra_module,alpha,0.09,0.11,\\alpha,global\n"
    )
    resp = _post_csv_text(client, csv)
    assert resp.status_code == 200, resp.text
    (p,) = resp.json()["params"]
    assert p["qname"] == "Lotka_Volterra_module/alpha"
    assert p["min"] == 0.09 and p["max"] == 0.11
    assert p["name_for_plotting"] == "\\alpha"
    assert p["param_type"] == "global"


def test_comment_annotation_round_trips(client):
    # Free-text annotation column (issue #25): parsed back into each entry, and
    # blank cells stay None rather than becoming the string "nan".
    csv = (
        "vessel_name,param_name,min,max,name_for_plotting,comment\n"
        "Lotka_Volterra_module,alpha,0.1,7,\\alpha,\"range from Dash 2016, tentative\"\n"
        "Lotka_Volterra_module,beta,0.01,2,\\beta,\n"
    )
    resp = _post_csv_text(client, csv)
    assert resp.status_code == 200, resp.text
    by_qname = {p["qname"]: p for p in resp.json()["params"]}
    assert by_qname["Lotka_Volterra_module/alpha"]["comment"] == "range from Dash 2016, tentative"
    assert by_qname["Lotka_Volterra_module/beta"]["comment"] is None


def test_comment_absent_when_column_missing(client):
    resp = _post_csv_file(client, LV_PARAMS_CSV_PATH)
    assert resp.status_code == 200, resp.text
    assert all(p["comment"] is None for p in resp.json()["params"])


def test_initial_value_from_model_default(client):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = _post_csv_file(client, LV_PARAMS_CSV_PATH, model_id=model_id)
    by_qname = {p["qname"]: p for p in resp.json()["params"]}
    # Lotka_Volterra_forced declares alpha initial_value="5".
    assert by_qname["Lotka_Volterra_module/alpha"]["initial_value"] == 5.0


# ---------------------------------------------------------------------------
# Integration tier
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_simulate_lv_at_alpha_min_and_max(client, requires_simulation):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]

    def max_x(alpha):
        resp = client.post(
            "/api/simulate",
            json={
                "model_id": model_id,
                "params": {"Lotka_Volterra_module/alpha": alpha},
                "sim_time": 5,
                "outputs": ["Lotka_Volterra_module/x"],
            },
        )
        assert resp.status_code == 200, resp.text
        return max(resp.json()["outputs"]["Lotka_Volterra_module/x"])

    low, high = max_x(0.1), max_x(7.0)
    assert abs(high - low) / abs(low) > 0.10


@pytest.mark.integration
def test_simulate_bg_model_alpha_o2_slider(client, requires_simulation):
    model_id = upload_model(client, BG_MODEL_PATH)["model_id"]
    csv = "vessel_name, param_name, min, max\nmain, alpha_o2, 0.005, 0.05\n"
    resp = _post_csv_text(client, csv, model_id=model_id)
    assert resp.status_code == 200, resp.text

    def mean_c_o2(alpha_o2):
        r = client.post(
            "/api/simulate",
            json={
                "model_id": model_id,
                "params": {"main/alpha_o2": alpha_o2},
                "sim_time": 20,
                "pre_time": 0,
                "outputs": ["main/c_o2"],
            },
        )
        assert r.status_code == 200, r.text
        return float(np.mean(r.json()["outputs"]["main/c_o2"]))

    assert mean_c_o2(0.005) != pytest.approx(mean_c_o2(0.05), rel=1e-3)


# ---------------------------------------------------------------------------
# Flat-model initial-value resolution (issue #114, DEFCON 1)
# ---------------------------------------------------------------------------
def test_flat_model_initial_values_resolve_via_gen_name():
    """A circulatory_autogen *flat* model renames constants (e.g. params_for_id
    `aortic_root/C` -> the model's `parameters/C_aortic_root`), so the direct
    `vessel/param` qname isn't in the model. The slider initial_value must still be
    the model's real value, not None (which the UI would replace with the range
    midpoint -> wrong sim, issue #114)."""
    from cellml_meta import parse_cellml
    from params_for_id import parse_params_for_id
    from conftest import RESOURCES_DIR

    meta = parse_cellml((RESOURCES_DIR / "3compartment_flat.cellml").read_bytes())
    csv = (RESOURCES_DIR / "3compartment_params_for_id.csv").read_bytes()
    entries = {e.qname: e.initial_value for e in parse_params_for_id(csv, meta.initial_values)}

    # global vessel -> bare gen name; other vessels -> param_vessel; both live in
    # the flat model's parameters* components.
    assert entries["global/q_lv_init"] == pytest.approx(0.00071536680911)
    assert entries["aortic_root/C"] == pytest.approx(1.674986287e-08)
    assert entries["global/E_lv_A"] == pytest.approx(248523797.83)
    assert entries["global/E_lv_B"] == pytest.approx(10268533.558)
    # None of them fell through to the "no value" case.
    assert all(v is not None for v in entries.values())


def test_direct_qname_still_wins_over_gen_name():
    """Non-flat models (Lotka-Volterra) name the constant `vessel/param` directly;
    that must resolve without the flat-model fallback kicking in."""
    from cellml_meta import parse_cellml
    from params_for_id import parse_params_for_id
    from conftest import RESOURCES_DIR

    meta = parse_cellml(LV_MODEL_PATH.read_bytes())
    csv = LV_PARAMS_CSV_PATH.read_bytes()
    entries = {e.qname: e.initial_value for e in parse_params_for_id(csv, meta.initial_values)}
    assert entries["Lotka_Volterra_module/alpha"] == pytest.approx(5.0)
    assert entries["Lotka_Volterra_module/beta"] == pytest.approx(0.2)


def test_gen_name_fallback_skips_ambiguous_bare_names():
    """If a bare gen name maps to multiple non-parameters components (a real clash),
    resolution returns None rather than guessing a wrong value."""
    from params_for_id import _resolve_initial_value, _build_gen_index

    initial = {"aortic_root_module/v": 0.0, "pvn_module/v": 1.0}  # bare 'v' clashes
    idx = _build_gen_index(initial)
    # vessel 'module' + param 'v'? gen name 'v_module' -> not present; but test the
    # ambiguous bare-name path directly with a gen name that clashes.
    assert _resolve_initial_value("global", "v", initial, idx) is None


def test_gen_name_fallback_prefers_parameters_component():
    """When the bare name clashes but exactly one hit is in a parameters* component,
    that one wins (the flat model's canonical source of the value)."""
    from params_for_id import _resolve_initial_value, _build_gen_index

    initial = {"parameters/R_x": 42.0, "some_module/R_x": 7.0}
    idx = _build_gen_index(initial)
    assert _resolve_initial_value("x", "R", initial, idx) == 42.0
