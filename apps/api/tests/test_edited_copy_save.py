"""Save writes the dated copy where the study lives (issue #215).

The obs_data and params_for_id editors used to hand their dated file to the
browser as a download and separately apply it. A download is not a save: the
file lands wherever the browser was told to put things, which is not where the
study is, and on a remotely served app is not even the same machine. Save now
asks the server to write it, and the download is gone.

Two rules the tests hold: Save never silently loses an edit (no outputs
directory falls back to the app's own config dir rather than refusing), and the
client names the file but never chooses where it goes.
"""

from __future__ import annotations

import json

from conftest import LV_MODEL_PATH, LV_OBS_DATA_PATH, upload_model

PARAMS_DOC = {"params": [{"targets": ["Lotka_Volterra_module/alpha"], "min": 0.1, "max": 2.0}]}


def _upload_params(client, model_id, tmp_dir, filename="p_260811.json"):
    return client.post(
        "/api/params_for_id/upload",
        params={"model_id": model_id, "output_dir": str(tmp_dir), "filename": filename},
        content=json.dumps(PARAMS_DOC),
        headers={"content-type": "application/json"},
    )


def test_params_save_writes_the_dated_file_into_the_outputs_dir(client, tmp_path):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = _upload_params(client, model_id, tmp_path)

    assert resp.status_code == 200, resp.text
    written = tmp_path / "p_260811.json"
    assert written.is_file()
    assert json.loads(written.read_text())["params"][0]["targets"] == [
        "Lotka_Volterra_module/alpha"
    ]
    # The panel reports where it went, so a save is not a silent one.
    assert resp.json()["saved_path"] == str(written)


def test_obs_save_writes_the_dated_file_into_the_outputs_dir(client, tmp_path):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    resp = client.post(
        "/api/obs_data/upload",
        params={"filename": "obs_260811.json", "output_dir": str(tmp_path)},
        json={"model_id": model_id, "obs_data": obs},
    )

    assert resp.status_code == 200, resp.text
    written = tmp_path / "obs_260811.json"
    assert written.is_file()
    assert json.loads(written.read_text())["data_items"]
    assert resp.json()["saved_path"] == str(written)


def test_no_outputs_dir_falls_back_to_the_config_dir(client, tmp_path, monkeypatch):
    """Save must never lose an edit because no directory was chosen."""
    import settings_store

    monkeypatch.setattr(settings_store, "config_dir", lambda: tmp_path / "cfg")
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]

    resp = client.post(
        "/api/params_for_id/upload",
        params={"model_id": model_id, "output_dir": "", "filename": "p_260811.json"},
        content=json.dumps(PARAMS_DOC),
        headers={"content-type": "application/json"},
    )

    assert resp.status_code == 200, resp.text
    assert (tmp_path / "cfg" / "p_260811.json").is_file()


def test_a_relative_outputs_dir_is_a_422(client, tmp_path):
    """The client picked the directory, so it is the client's to fix (#135)."""
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = client.post(
        "/api/params_for_id/upload",
        params={"model_id": model_id, "output_dir": "relative/dir", "filename": "p.json"},
        content=json.dumps(PARAMS_DOC),
        headers={"content-type": "application/json"},
    )

    assert resp.status_code == 422
    assert "absolute" in resp.json()["detail"]


def test_the_filename_cannot_walk_out_of_the_directory(client, tmp_path):
    """The editors name the file; they do not choose where it goes."""
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = _upload_params(client, model_id, tmp_path, filename="../escaped.json")

    assert resp.status_code == 200, resp.text
    assert not (tmp_path.parent / "escaped.json").exists()
    assert (tmp_path / "escaped.json").is_file()


def test_a_plain_upload_writes_nothing_extra(client, tmp_path):
    """Only the editors' Save carries a filename; loading a file from disk must
    not scatter a second copy."""
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = client.post(
        "/api/params_for_id/upload",
        params={"model_id": model_id},
        content=json.dumps(PARAMS_DOC),
        headers={"content-type": "application/json"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["saved_path"] is None
    assert not list(tmp_path.iterdir())
