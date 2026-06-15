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
