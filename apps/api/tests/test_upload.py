import io

import main
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


def test_model_recovered_from_disk_when_registry_lost_it(client):
    """If the in-memory registry is wiped (e.g. a dev-server reload), a model is
    re-derived from its persisted upload so a parameter change / new plot still
    resolves it instead of failing with 'model not found'."""
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    # Simulate the reload: drop the in-memory record but keep the uploaded file.
    main._models.clear()
    resp = client.get(f"/api/models/{model_id}/variables")
    assert resp.status_code == 200, resp.text
    assert "Lotka_Volterra_module/x" in resp.json()["odes"]
    assert model_id in main._models  # re-registered for subsequent requests


def test_unknown_model_still_404s(client):
    assert client.get("/api/models/does-not-exist/variables").status_code == 404
