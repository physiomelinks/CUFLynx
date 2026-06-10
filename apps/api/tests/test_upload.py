import io

from conftest import BG_MODEL_PATH, LV_MODEL_PATH, upload_model


def test_upload_bg_model_returns_metadata(client):
    data = upload_model(client, BG_MODEL_PATH)
    assert isinstance(data["model_id"], str) and data["model_id"]
    assert data["name"] == "my_model"
    assert data["variable_count"] > 0
    assert "main/p_o2" in data["odes"]
    assert "main/alpha_o2" in data["params"]


def test_upload_lotka_volterra_returns_metadata(client):
    data = upload_model(client, LV_MODEL_PATH)
    assert data["name"] == "Lotka_Volterra_forced"
    assert "Lotka_Volterra_module/alpha" in data["params"]
    assert "Lotka_Volterra_module/x" in data["odes"]


def test_upload_invalid_file_returns_422(client):
    resp = client.post(
        "/api/models/upload",
        files={"file": ("notes.txt", io.BytesIO(b"hello world"), "text/plain")},
    )
    assert resp.status_code == 422
