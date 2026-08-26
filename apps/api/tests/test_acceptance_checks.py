"""The acceptance checks, run against the app in this repo.

`acceptance.py` holds checks that must hold of a *running* CUFLynx; the release
runs them against the built executable (`scripts/analysis_smoke.py`). Running them
here too is what keeps the two honest: a check that quietly stops holding for the
source app is a check the release is about to fail on the artifact, and this tier
runs on every push while the release runs on a tag.
"""
from __future__ import annotations

import json
import urllib.parse

import acceptance
import pytest
from conftest import RESOURCES_DIR


class TestClientTransport:
    """`acceptance`'s transport over FastAPI's TestClient (in-process, no server)."""

    def __init__(self, client):
        self.client = client

    def get(self, path):
        resp = self.client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}: {resp.text[:300]}"
        return resp.json()

    def get_raw(self, path):
        resp = self.client.get(path)
        assert resp.status_code == 200, f"{path} -> {resp.status_code}"
        return resp.content, dict(resp.headers)

    def post(self, path, payload=None):
        resp = self.client.post(path, json=payload or {})
        assert resp.status_code == 200, f"{path} -> {resp.status_code}: {resp.text[:300]}"
        return resp.json()

    def upload(self, path, filename, blob, field="file", content_type="application/zip"):
        resp = self.client.post(path, files={field: (filename, blob, content_type)})
        assert resp.status_code == 200, f"{path} -> {resp.status_code}: {resp.text[:300]}"
        return resp.json()

    @staticmethod
    def quote(value):
        return urllib.parse.quote(value)


@pytest.fixture
def app(client):
    return TestClientTransport(client)


def test_the_engine_answers_the_obs_data_vocabulary(app, requires_ca):
    assert acceptance.check_engine_vocabulary(app)


def test_the_example_is_served_current_and_uncacheable(app):
    assert acceptance.check_example_is_current(app)


def test_the_example_loads_whole(app, requires_ca):
    assert acceptance.check_example_loads_whole(app)


def test_a_pre_466_archive_is_refused_with_the_migrator_named(app, requires_ca, tmp_path):
    """Built here rather than shipped: the point is the *vocabulary*, and a fixture
    of it would have to be exempted from every check that keeps `resources/` current."""
    import io
    import zipfile

    obs = {
        "protocol_info": {"pre_times": [0.0], "sim_times": [[1.0]], "params_to_change": {}},
        "data_items": [
            {"variable": "pressure", "name_for_plotting": "u", "data_type": "constant",
             "operation": op, "operands": ["a/u"], "unit": "J_per_m3", "weight": 1.0,
             "value": 1.0, "std": 0.1, "cost_type": "gaussian_MLE"}
            for op in ("mean", "max", "min")
        ],
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("m.cellml", (RESOURCES_DIR / "Lotka_Volterra_forced.cellml").read_bytes())
        zf.writestr("obs_data.json", json.dumps(obs))

    assert acceptance.check_old_obs_data_is_refused_with_the_fix(app, buf.getvalue())


def test_a_finished_directory_reopens(app, tmp_path):
    """The directory is built here from the artefacts a run leaves, so this holds
    without needing a real run to have happened on the machine under test."""
    import csv

    import numpy as np

    run = tmp_path / "genetic_algorithm_study"
    run.mkdir()
    np.save(run / "best_param_vals.npy", np.array([1.0, 2.0]))
    np.save(run / "best_cost.npy", np.array(0.5))
    with open(run / "param_names.csv", "w", newline="") as handle:
        csv.writer(handle).writerows([["Lotka_Volterra_module/alpha"],
                                      ["Lotka_Volterra_module/beta"]])
    (tmp_path / "study_calibrated.cellml").write_bytes(
        (RESOURCES_DIR / "Lotka_Volterra_forced.cellml").read_bytes())
    (run / "abc_obs_data_260825_120000.json").write_text(
        (RESOURCES_DIR / "Lotka_Volterra_obs_data.json").read_text(), encoding="utf-8")
    (run / "abc_params_for_id_260825_120000.json").write_text(json.dumps(
        {"version": 1, "defaults": {}, "params": [
            {"targets": ["Lotka_Volterra_module/alpha"], "name": "Lotka_Volterra_module/alpha",
             "param_type": "const", "name_for_plotting": "alpha", "min": "0.1", "max": "3.0"}]}),
        encoding="utf-8")

    assert acceptance.check_directory_reopens(app, str(tmp_path))


@pytest.mark.integration
def test_every_backend_simulates(app, requires_simulation):
    model = RESOURCES_DIR / "Lotka_Volterra_forced.cellml"
    assert acceptance.check_simulates_on_every_backend(
        app, model.read_bytes(), model.name)
