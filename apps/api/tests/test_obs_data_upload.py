import json

import numpy as np
import pytest

from conftest import (
    BG_MODEL_PATH,
    LV_MODEL_PATH,
    LV_OBS_DATA_PATH,
    upload_model,
)


# ---------------------------------------------------------------------------
# Unit tier
# ---------------------------------------------------------------------------
def test_upload_valid_obs_data_returns_summary(client):
    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["n_experiments"] == 1
    assert body["n_data_items"] == 2


def test_upload_missing_protocol_info_returns_422(client):
    resp = client.post("/api/obs_data/upload", json={})
    assert resp.status_code == 422


def test_upload_series_without_obs_dt_returns_422(client):
    obs = {
        "protocol_info": {"pre_times": [0.0], "sim_times": [[5]]},
        "data_items": [
            {"data_item_name": "x", "data_type": "series", "experiment_idx": 0}
        ],
    }
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 422
    assert "obs_dt" in resp.json()["detail"]


def test_upload_experiment_idx_out_of_range_returns_422(client):
    obs = {
        "protocol_info": {"pre_times": [0.0], "sim_times": [[5]]},
        "data_items": [
            {"data_item_name": "x", "data_type": "constant", "experiment_idx": 5}
        ],
    }
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 422


def test_obs_editor_object_form_round_trips(client):
    # Shape the obs Edit dialog emits: object form with protocol_info verbatim,
    # an edited constant + a preserved series item (with obs_dt). Guards format drift.
    obs = {
        "protocol_info": {"pre_times": [0.0], "sim_times": [[5]]},
        "prediction_items": [],
        "data_items": [
            {
                "data_item_name": "x_max", "data_type": "constant", "operation": "max",
                "operands": ["m/x"], "unit": "dimensionless", "value": 30, "std": 3,
                "experiment_idx": 0, "plot_type": "horizontal",
            },
            {
                "data_item_name": "s", "data_type": "series", "obs_dt": 0.1,
                "value": [1, 2], "std": 0.1, "experiment_idx": 0,
            },
        ],
    }
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 200, resp.text
    assert resp.json()["n_data_items"] == 2


def test_protocol_info_with_generated_ramp_and_pulse_traces(client):
    # Shape the protocol_info editor emits: params_to_change referencing generated
    # ramp/pulse traces present in protocol_traces.
    obs = {
        "protocol_info": {
            "pre_times": [0.0, 0.0],
            "sim_times": [[5], [4]],
            "experiment_labels": ["e0", "e1"],
            "params_to_change": {
                "m/I": [["m_I_e0s0"], [0.2]],
                "m/g": [[0.1], ["m_g_e1s0"]],
            },
            "protocol_traces": {
                "m_I_e0s0": {"t": [0, 5], "values": [0, 1]},
                "m_g_e1s0": {"t": [0, 1, 1.001, 3, 3.001, 4], "values": [0, 0, 0.5, 0.5, 0, 0]},
            },
        },
        "prediction_items": [],
        "data_items": [],
    }
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 200, resp.text
    assert resp.json()["n_experiments"] == 2


def test_protocol_info_missing_trace_key_returns_422(client):
    obs = {
        "protocol_info": {
            "pre_times": [0.0],
            "sim_times": [[5]],
            "params_to_change": {"m/I": [["nonexistent_trace"]]},
            "protocol_traces": {},
        },
        "data_items": [],
    }
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 422
    assert "trace" in resp.json()["detail"].lower()


def test_obs_editor_data_only_array_form(client):
    # Data-only files round-trip as a bare array (no protocol_info).
    obs = [
        {
            "data_item_name": "x_max", "data_type": "constant", "operation": "max",
            "operands": ["m/x"], "unit": "dimensionless", "value": 30, "std": 3,
            "experiment_idx": 0,
        },
    ]
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_protocol"] is False


# ---------------------------------------------------------------------------
# Integration tier
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_protocol_run_uses_uploaded_obs_data(client, requires_simulation):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    obs = json.loads(LV_OBS_DATA_PATH.read_text())

    up = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert up.status_code == 200, up.text
    assert up.json()["n_experiments"] == 1

    # No protocol_info in the body -> server uses the uploaded obs_data.
    resp = client.post(
        "/api/protocol/run",
        json={
            "model_id": model_id,
            "params": {},
            "outputs": ["Lotka_Volterra_module/x"],
        },
    )
    assert resp.status_code == 200, resp.text
    experiments = resp.json()["experiments"]
    assert len(experiments) == 1
    assert "Lotka_Volterra_module/x" in experiments[0]["outputs"]
    time = np.array(experiments[0]["time"])
    assert np.all(np.diff(time) > 0)


@pytest.mark.integration
def test_protocol_run_bg_model_with_minimal_obs_data(client, requires_simulation):
    model_id = upload_model(client, BG_MODEL_PATH)["model_id"]
    obs = {
        "protocol_info": {"pre_times": [0.0], "sim_times": [[20]], "params_to_change": {}},
        "data_items": [],
    }
    up = client.post("/api/obs_data/upload", json={"model_id": model_id, "obs_data": obs})
    assert up.status_code == 200, up.text

    resp = client.post(
        "/api/protocol/run",
        json={"model_id": model_id, "params": {}, "outputs": ["main/p_o2"]},
    )
    assert resp.status_code == 200, resp.text
    assert "main/p_o2" in resp.json()["experiments"][0]["outputs"]


# ---------------------------------------------------------------------------
# operation_kwargs survive the upload (regression: they must reach CA)
# ---------------------------------------------------------------------------
def test_upload_preserves_operation_kwargs_in_stored_file(client):
    """Per-data_item ``operation_kwargs`` (#112/#113) must survive the upload into
    the obs_data.json handed to circulatory_autogen.

    The editor writes the values, but they only *do* anything if they reach CA's
    parser, which reads the field off the stored file. A whitelist-style refactor
    of the data_item parsing would silently drop them and every other kwargs test
    would still pass, so pin it here: response payload *and* file on disk.
    """
    import main

    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    obs = json.loads(LV_OBS_DATA_PATH.read_text())
    obs["data_items"][0]["operation"] = "peak_above"
    obs["data_items"][0]["operation_kwargs"] = {"threshold": 0.9, "invert": True}

    resp = client.post(f"/api/obs_data/upload?model_id={model_id}", json=obs)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data_items"][0]["operation_kwargs"] == {
        "threshold": 0.9,
        "invert": True,
    }

    # The file CA actually reads.
    stored = json.loads(main._models[model_id].obs_path.read_text())
    assert stored["data_items"][0]["operation_kwargs"] == {
        "threshold": 0.9,
        "invert": True,
    }
    # An item without kwargs stays clean (no empty map injected).
    assert "operation_kwargs" not in stored["data_items"][1]


# Issue #27 / CA#339: the editor writes protocol_shapes -- Myokit-style event
# declarations -- rather than expanded point tables, because a declaration can be
# read back and edited and a table cannot. Either must be accepted.
def test_a_protocol_shape_is_accepted_in_place_of_a_trace(client):
    obs = {
        "protocol_info": {
            "pre_times": [0.0],
            "sim_times": [[2000.0]],
            "params_to_change": {"engine/pace": [["stim"]]},
            "protocol_shapes": {
                "stim": {
                    "events": [
                        {"level": 1.0, "start": 100, "length": 2, "period": 1000, "multiplier": 0}
                    ]
                }
            },
        },
        "data_items": [],
    }
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 200, resp.text
    assert resp.json()["n_experiments"] == 1


def test_shapes_and_traces_can_be_mixed_across_parameters(client):
    obs = {
        "protocol_info": {
            "pre_times": [0.0],
            "sim_times": [[5.0]],
            "params_to_change": {"m/I": [["by_shape"]], "m/g": [["by_table"]]},
            "protocol_shapes": {"by_shape": {"type": "ramp", "from": 0, "to": 1}},
            "protocol_traces": {"by_table": {"t": [0, 5], "values": [0, 1]}},
        },
        "data_items": [],
    }
    assert client.post("/api/obs_data/upload", json=obs).status_code == 200


def test_a_name_in_both_is_refused_as_ambiguous(client):
    obs = {
        "protocol_info": {
            "pre_times": [0.0],
            "sim_times": [[5.0]],
            "params_to_change": {"m/I": [["both"]]},
            "protocol_shapes": {"both": {"type": "ramp", "from": 0, "to": 1}},
            "protocol_traces": {"both": {"t": [0, 5], "values": [0, 1]}},
        },
        "data_items": [],
    }
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 422
    assert "both" in resp.json()["detail"]


def test_a_dangling_name_still_says_where_it_was_looked_for(client):
    obs = {
        "protocol_info": {
            "pre_times": [0.0],
            "sim_times": [[5.0]],
            "params_to_change": {"m/I": [["typo"]]},
            "protocol_shapes": {"stim": {"type": "ramp", "from": 0, "to": 1}},
        },
        "data_items": [],
    }
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "typo" in detail
    assert "protocol_shapes" in detail


# ---------------------------------------------------------------------------
# CA's schema is consulted at upload, not at calibration time
#
# CUFLynx's own checks are structural -- enough to load a protocol and draw
# overlays. CA's decide whether a calibration can run at all: it marks
# `variable`, `data_type`, `unit`, `operands`, `value` and `std` REQUIRED and
# rejects keys outside its schema. A document failing those used to upload
# cleanly, plot, and show a cost, then fail in the calibration terminal.
# ---------------------------------------------------------------------------
def _obs(**item_over):
    item = {
        "data_item_name": "a/x",
        "data_type": "constant",
        "unit": "dimensionless",
        "operands": ["a/x"],
        "value": 1.0,
        "std": 0.1,
        "weight": 1.0,
    }
    item.update(item_over)
    return {"protocol_info": {"pre_times": [0.0], "sim_times": [[1.0]]}, "data_items": [item]}


def test_a_conforming_document_still_uploads(client, requires_ca):
    resp = client.post("/api/obs_data/upload", json=_obs())
    assert resp.status_code == 200, resp.text


def test_a_mis_spelled_key_is_rejected_at_upload(client, requires_ca):
    """The exact shape that used to survive: `opperation` is not a schema key,
    so CA refuses the document -- but only once a calibration started."""
    resp = client.post("/api/obs_data/upload", json=_obs(opperation="max"))
    assert resp.status_code == 422
    assert "opperation" in resp.json()["detail"]


def test_missing_required_keys_are_rejected_at_upload(client, requires_ca):
    """A data_item missing a required key is refused at upload, and the key is named.

    Only ``unit`` is deleted. This used to delete ``std`` as well and assert both were
    named, which stopped being true: circulatory_autogen gave ``std`` a NaN default in
    #421 ("a distribution is a ground truth, not a data_type") because a distribution
    cost supplies it through ``prob_dist_params`` instead. So ``std`` is optional now
    and CA rightly names only ``unit``.

    Do not restore ``std`` here -- the assertion would be pinning a requirement the
    engine deliberately dropped. What this test is for is the *upload-time* surfacing of
    CA's verdict, which is unchanged; the particular key is incidental.
    """
    obs = _obs()
    del obs["data_items"][0]["unit"]
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "unit" in detail, detail


def test_the_message_is_cas_own(client, requires_ca):
    """So what the user reads at upload is what the calibration would have said,
    rather than a paraphrase that drifts from it."""
    resp = client.post("/api/obs_data/upload", json=_obs(opperation="max"))
    assert "circulatory_autogen rejected this obs_data" in resp.json()["detail"]


def test_validation_does_not_rewrite_the_document():
    """CA's parser materialises protocol shapes and normalises series std in
    place. Validation must not quietly change the obs_data the app then runs."""
    import copy

    import obs_data as obs_mod

    obs = _obs()
    before = copy.deepcopy(obs)
    obs_mod.ca_schema_error(obs)
    assert obs == before


def test_upload_still_works_when_ca_cannot_be_consulted(client, monkeypatch):
    """No CA clone configured (the frozen app's first run) must not make every
    obs_data unloadable -- it degrades to the structural checks."""
    import obs_data as obs_mod

    monkeypatch.setattr(obs_mod, "ca_schema_error", lambda _obj: None)
    obs = _obs()
    del obs["data_items"][0]["std"]
    resp = client.post("/api/obs_data/upload", json=obs)
    assert resp.status_code == 200, resp.text


def test_a_ca_crash_does_not_block_the_upload(monkeypatch):
    """Only a schema complaint (ValueError) is the user's to answer for; anything
    else is a CA problem and must not make their document unloadable."""
    import obs_data as obs_mod

    class _Boom:
        def parse_obs_data_json(self, **_kw):
            raise RuntimeError("some CA internal failure")

    monkeypatch.setattr(obs_mod, "_ca_parser", lambda: _Boom())
    assert obs_mod.ca_schema_error(_obs()) is None
