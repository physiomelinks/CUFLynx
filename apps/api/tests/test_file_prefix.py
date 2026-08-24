"""One study, one name on disk: circulatory_autogen's ``file_prefix``.

CA builds everything it writes out of ``file_prefix`` -- the run directory
``<method>_<file_prefix>_<obs_prefix>``, ``generated_models/<file_prefix>/``,
``<file_prefix>_calibrated.cellml``, ``emulators/<file_prefix>_<obs_prefix>``.

CUFLynx used to hand it the **CellML ``<model name>``** instead, which is a
different string: a study loaded from ``3compartment_flat.cellml`` ran as
``CardiovascularSystem``, the name written inside the file. So one study had two
names -- the file stem in the title bar, in ``generated_models/`` and in every
export, and the model name in every result CA produced -- and reopening the
directory could not match them up.
"""
from __future__ import annotations

import json

import pytest
from conftest import (
    LV_MODEL_PATH,
    LV_OBS_DATA_PATH,
    LV_PARAMS_CSV_PATH,
    RESOURCES_DIR,
    upload_model,
)

import main


def _prefix_of(client, model_id) -> str:
    return main._record_prefix(main._models[model_id])


def _start_and_capture(client, monkeypatch, model_id, route, manager, settings=None):
    """The run config the manager was handed, without running anything."""
    captured: dict = {}

    def _fake_start(config):
        captured["config"] = config
        return "job-1"

    monkeypatch.setattr(manager, "start", _fake_start)
    resp = client.post(route, json={"model_id": model_id, "settings": settings or {}})
    assert resp.status_code == 200, resp.text
    return captured["config"]


@pytest.mark.unit
def test_the_prefix_is_the_file_the_user_loaded_not_the_name_inside_it(client):
    """The 3compartment model calls itself ``CardiovascularSystem``. The study is
    still ``3compartment_flat`` -- that is the name the user has for it."""
    blob = (RESOURCES_DIR / "3compartment.omex").read_bytes()
    body = client.post(
        "/api/omex/upload", files={"file": ("3compartment.omex", blob, "application/zip")}
    ).json()

    record = main._models[body["model_id"]]
    assert record.meta.name == "CardiovascularSystem", "the name inside the file"
    assert record.file_prefix == "3compartment_flat", "the name of the file"


@pytest.mark.unit
def test_every_analysis_runs_under_that_prefix(client, monkeypatch):
    """All four run configs, because a study calibrated under one name and
    emulated under another has its results scattered across two directories."""
    import calibration as calibration_mod
    import emulator as emulator_mod
    import sensitivity as sensitivity_mod
    import uq as uq_mod

    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    with open(LV_PARAMS_CSV_PATH, "rb") as fh:
        client.post(f"/api/params_for_id/upload?model_id={model_id}",
                    files={"file": (LV_PARAMS_CSV_PATH.name, fh, "text/csv")})

    expected = _prefix_of(client, model_id)
    assert expected == "Lotka_Volterra_forced"

    for route, manager, settings in (
        ("/api/calibration/run", calibration_mod.calibration, None),
        ("/api/sensitivity/run", sensitivity_mod.sensitivity, None),
        # UQ otherwise wants a finished calibration to start from; which run it
        # begins at is not what this is about.
        ("/api/uq/run", uq_mod.uq, {"run_calibration_first": True}),
        ("/api/emulator/train", emulator_mod.emulator, None),
    ):
        config = _start_and_capture(client, monkeypatch, model_id, route, manager, settings)
        assert config["file_prefix"] == expected, f"{route} named the study something else"


@pytest.mark.unit
def test_the_models_own_directory_uses_the_same_prefix(client, tmp_path):
    """`generated_models/<file_prefix>/` is where CA resolves a model path against,
    and where PhLynx's editor state is kept. It has to be the directory the runs
    name, or a reopened study looks in a folder nothing wrote to."""
    blob = (RESOURCES_DIR / "3compartment.omex").read_bytes()
    body = client.post(
        "/api/omex/upload",
        params={"output_dir": str(tmp_path)},
        files={"file": ("3compartment.omex", blob, "application/zip")},
    ).json()

    saved = body["module_config_path"]
    assert saved is not None
    assert f"generated_models/{main._models[body['model_id']].file_prefix}/" in saved


@pytest.mark.unit
def test_the_prefix_survives_the_registry_being_lost(client):
    """A dev-server reload empties the in-memory registry and `_get_model`
    re-derives the record from the uploaded file -- which is named by model_id.
    Without the sidecar the study would silently change prefix mid-session and
    its next run would land in a second directory beside the first."""
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    before = _prefix_of(client, model_id)

    main._models.clear()
    recovered = main._get_model(model_id)

    assert recovered.file_prefix == before == "Lotka_Volterra_forced"


@pytest.mark.unit
def test_a_myokit_model_keeps_the_stem_of_the_file_that_was_dropped(client):
    """A .mmt becomes CellML at the door (#27), and the CellML it becomes is named
    by the converter -- but the study is still the file the user has."""
    mmt = RESOURCES_DIR / "br-1977.mmt"
    if not mmt.is_file():
        pytest.skip("no .mmt fixture in resources/")
    with open(mmt, "rb") as fh:
        body = client.post(
            "/api/models/upload", files={"file": (mmt.name, fh, "text/plain")}
        ).json()

    assert main._models[body["model_id"]].file_prefix == "br-1977"


@pytest.mark.unit
def test_an_explicit_prefix_from_the_client_still_wins_for_an_export(client, tmp_path):
    """The export bundle names the study, and the user may rename it there."""
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = client.post(
        "/api/export/pipeline",
        json={"model_id": model_id, "file_prefix": "my_study",
              "config_outputs_dir": str(tmp_path)},
    )
    if resp.status_code != 200:
        pytest.skip(f"export unavailable in this environment: {resp.text[:120]}")
    exports = sorted(tmp_path.glob("export_*/generated_models/my_study"))
    assert exports, sorted(p.name for p in tmp_path.iterdir())
