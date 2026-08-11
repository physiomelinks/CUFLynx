"""Modifier parameters end-to-end (#208).

A JSON params doc carrying a scale modifier, through the real production path:
POST /api/params_for_id/upload (content-sniffed JSON, stored with a .json
suffix) -> POST /api/sensitivity/run -> the manager spawns the real
sensitivity_runner subprocess -> CA reads the .json file (suffix branching),
builds the modifier's param_id_info and samples θ.

The unit tiers pin each seam separately; this is the one place the seams are
proven to meet: the upload's anchor/θ shape, the analysisDict contract
(current_params carries θ at modifies[0]), and CA's own reader agreeing with
what the editor writes.
"""

from __future__ import annotations

import json
import time

import pytest

from conftest import RESOURCES_DIR, upload_model

C3_MODEL_PATH = RESOURCES_DIR / "3compartment_flat.cellml"
C3_OBS_DATA_PATH = RESOURCES_DIR / "3compartment_obs_data.json"

# Mirrors the CSV fixture's rows, with the two elastances joined under one scale
# modifier -- the modifier the editor's "Create scale modifier" would write.
PARAMS_DOC = {
    "version": 1,
    "defaults": {},
    "params": [
        {"name": "q_sbv", "targets": ["global/q_lv_init"],
         "min": 200e-6, "max": 1500e-6, "param_type": "const"},
        {"name": "C_ao", "targets": ["aortic_root/C"],
         "min": 1e-9, "max": 5e-8, "param_type": "const"},
        {"name": "E_lv_scale", "modifies": ["global/E_lv_A", "global/E_lv_B"],
         "modifier": "scale", "min": 0.5, "max": 2.0, "param_type": "const"},
    ],
}


def _setup(client) -> str:
    model_id = upload_model(client, C3_MODEL_PATH)["model_id"]
    obs = json.loads(C3_OBS_DATA_PATH.read_text())
    r = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert r.status_code == 200, r.text
    r = client.post(
        f"/api/params_for_id/upload?model_id={model_id}",
        content=json.dumps(PARAMS_DOC),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 200, r.text
    return model_id, r.json()["params"]


def test_the_upload_parses_the_modifier_to_a_theta_entry(client, requires_params_csv):
    """The response entry the sliders are seeded from: anchor key, θ identity,
    per-target baselines from the flat model's defaults."""
    _model_id, params = _setup(client)

    mod = next(p for p in params if p["modifies"])
    assert mod["name"] == "E_lv_scale"
    assert mod["qname"] == "global/E_lv_A"  # anchor = modifies[0]
    assert mod["qnames"] == ["global/E_lv_A", "global/E_lv_B"]
    assert mod["initial_value"] == 1.0 and mod["identity"] == 1.0
    # Baselines resolved via the flat-model gen-name fallback (E_lv_A lives in
    # parameters_global); both must be present or the live tier warns.
    assert set(mod["baselines"]) == {"global/E_lv_A", "global/E_lv_B"}
    assert all(v > 0 for v in mod["baselines"].values())
    assert mod["warning"] is None


def _wait(client, job_id, timeout=600):
    offset = 0
    lines: list[str] = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        s = client.get(f"/api/sensitivity/{job_id}/status?offset={offset}").json()
        lines += s["lines"]
        offset = s["next_offset"]
        if s["state"] != "running":
            return s, lines
        time.sleep(0.05)
    raise AssertionError("sensitivity did not finish; lines:\n" + "\n".join(lines))


@pytest.mark.integration
def test_local_fd_sa_runs_with_a_scale_modifier_via_api(client, requires_simulation):
    """Real short FD local SA with θ in the nominal: current_params carries θ at
    the anchor (what the frontend's analysisDict sends), CA reads the stored
    .json params file, and the modifier appears as one parameter in the result.
    Exercises PR A's suffix fix end-to-end: a .csv-named JSON doc would be fed
    to CA's CSV parser by the runner and die before any of this."""
    model_id, _params = _setup(client)
    settings = {
        "method": "local",
        "gradient_method": "FD",
        "nominal": "current",
        "rel_step": 0.05,
        "dt": 0.01,
        "num_cores": 1,
    }
    resp = client.post(
        "/api/sensitivity/run",
        json={
            "model_id": model_id,
            "settings": settings,
            # What the frontend's analysisDict sends: θ at the modifier's anchor,
            # physical values at the free anchors.
            "current_params": {"global/E_lv_A": 1.2},
        },
    )
    assert resp.status_code == 200, resp.text

    status, lines = _wait(client, resp.json()["job_id"])
    assert status["state"] == "done", "\n".join(lines)
    assert status.get("indices"), "no indices; log:\n" + "\n".join(lines)
    # θ=1.2 landed in the modifier's slot of the nominal point.
    anchor_idx = status["param_names"].index("global/E_lv_A")
    assert status["nominal"][anchor_idx] == pytest.approx(1.2)
    assert "sliders" in status["nominal_source"]
