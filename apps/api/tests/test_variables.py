from conftest import BG_MODEL_PATH, LV_MODEL_PATH, upload_model


def test_bg_model_variables_contains_param_and_ode(client):
    model_id = upload_model(client, BG_MODEL_PATH)["model_id"]
    resp = client.get(f"/api/models/{model_id}/variables")
    assert resp.status_code == 200
    body = resp.json()
    assert "main/alpha_o2" in body["params"]
    assert "main/p_o2" in body["odes"]
    assert "main/c_o2" in body["algebraic"]


def test_lv_variables_contains_alpha(client):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    body = client.get(f"/api/models/{model_id}/variables").json()
    assert "Lotka_Volterra_module/alpha" in body["params"]
    assert "Lotka_Volterra_module/x" in body["odes"]


def test_variables_unknown_model_returns_404(client):
    assert client.get("/api/models/does-not-exist/variables").status_code == 404
