"""The Identifiability tab's backend: parameter reduction over a Sobol run, and
CA's identifiability analysis through the UQ runner.

The unit tier reads hand-written files in CA's formats and drives the endpoints
with fake runners. The integration tier runs a small real Sobol run with a ``pca``
reduction on the Lotka-Volterra fixture, then a reduction over a directory that
lacks the Sobol files a ``pca`` reduction reads.
"""

from __future__ import annotations

import json
import os
import shutil
import time

import pytest

import ca_run_history
import sensitivity as sensitivity_mod
import uq as uq_mod
from conftest import RESOURCES_DIR, upload_model

LV_MODEL = RESOURCES_DIR / "Lotka_Volterra_forced.cellml"
LV_OBS = RESOURCES_DIR / "Lotka_Volterra_obs_data.json"
LV_PARAMS = RESOURCES_DIR / "Lotka_Volterra_params_for_id.csv"

#: A reduced params_for_id in CA's JSON form, one entry kept.
REDUCED_PARAMS = {
    "version": 1,
    "params": [{"targets": ["Lotka_Volterra_module/alpha"], "min": 0.1, "max": 7,
                "name_for_plotting": "alpha", "prior": "uniform"}],
}


def _write_report(report_dir, *, heatmap=True, method="pca"):
    """A param_reduction.json as CA's ParamReduction.reduce writes it."""
    os.makedirs(report_dir, exist_ok=True)
    reduced = os.path.join(report_dir, "reduced_params_for_id.json")
    with open(reduced, "w") as fh:
        json.dump(REDUCED_PARAMS, fh)
    report = {
        "reduction": {"method": method, "n_params": None},
        "sensitivity_dir": os.path.dirname(report_dir),
        "params_for_id": "candidates.json",
        "reduced_params_for_id": reduced,
        "selected": [{"parameter": "Lotka_Volterra_module/alpha", "score": 0.7, "mode": 1,
                      "new_effect": 4.2}],
    }
    if method == "pca":
        report.update({
            "modes": [{"mode": 1, "variance": 16.0, "std": 4.0, "variance_share": 0.9,
                       "informative": True},
                      {"mode": 2, "variance": 0.25, "std": 0.5, "variance_share": 0.1,
                       "informative": False}],
            "n_informative_modes": 1,
            "unexplained_effects": {"Lotka_Volterra_module/beta": 0.3},
            "failed_samples": 0,
            "files": {"heatmap": "pca_mode_sobol_ST.png"},
        })
        if heatmap:
            with open(os.path.join(report_dir, "pca_mode_sobol_ST.png"), "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")
    with open(os.path.join(report_dir, "param_reduction.json"), "w") as fh:
        json.dump(report, fh)
    return report


# ---------------------------------------------------------------------------
# Readers (ca_run_history)
# ---------------------------------------------------------------------------
def test_param_reduction_reader_returns_the_report_and_its_files(tmp_path):
    report_dir = tmp_path / "sa" / "param_reduction"
    _write_report(str(report_dir))

    report = ca_run_history.param_reduction(str(report_dir))

    assert report["selected"][0]["parameter"] == "Lotka_Volterra_module/alpha"
    assert report["modes"][1]["informative"] is False
    assert report["unexplained_effects"] == {"Lotka_Volterra_module/beta": 0.3}
    assert report["report_dir"] == str(report_dir)
    assert report["reduced_params_for_id"] == str(report_dir / "reduced_params_for_id.json")
    assert report["heatmap"] == str(report_dir / "pca_mode_sobol_ST.png")


def test_param_reduction_reader_tolerates_missing_and_partial_reports(tmp_path):
    assert ca_run_history.param_reduction(str(tmp_path)) is None
    (tmp_path / "param_reduction.json").write_text("{not json")
    assert ca_run_history.param_reduction(str(tmp_path)) is None
    # A heatmap the report names but CA could not draw is None, not a dead link.
    _write_report(str(tmp_path / "r"), heatmap=False)
    assert ca_run_history.param_reduction(str(tmp_path / "r"))["heatmap"] is None


def test_find_param_reduction_takes_the_newest_report_after_the_job_started(tmp_path):
    old = tmp_path / "param_reduction"
    _write_report(str(old))
    started = time.time() + 5
    assert ca_run_history.find_param_reduction(str(tmp_path)) == str(old)
    # An earlier run's report is never this job's result.
    assert ca_run_history.find_param_reduction(str(tmp_path), newer_than=started) is None


def test_sobol_runs_lists_directories_with_indices_and_whether_pca_can_read_them(tmp_path):
    full = tmp_path / "a"
    full.mkdir()
    for suffix in ("indices", "samples", "outputs"):
        (full / f"all_outputs_n8_Sobol_{suffix}.csv").write_text("Parameter\n")
    (full / "sobol_problem.json").write_text("{}")
    bare = tmp_path / "b" / "c"
    bare.mkdir(parents=True)
    (bare / "all_outputs_n16_Sobol_indices.csv").write_text("Parameter\n")

    runs = {r["dir"]: r for r in ca_run_history.sobol_runs(str(tmp_path))}

    assert runs[str(full)]["num_samples"] == 8
    assert runs[str(full)]["has_samples"] is True
    assert runs[str(bare)]["num_samples"] == 16
    assert runs[str(bare)]["has_samples"] is False


def test_profile_likelihood_reader_follows_the_summary_order(tmp_path):
    results = tmp_path / "profile_likelihood"
    results.mkdir()
    (results / "summary.json").write_text(json.dumps({"parameters": ["m/b", "m/a"]}))
    for i, name in enumerate(["m/a", "m/b"]):
        (results / f"param_{i}_{name.replace('/', '_')}.json").write_text(json.dumps({
            "param_name": name, "best_val": 1.0 + i, "best_cost": 0.1,
            "ci_95": [0.5, 2.0] if i else None, "param_values": [1, 2], "costs": [0.2, 0.1],
            "optima": [], "other_param_names": []}))

    profiles = ca_run_history.profile_likelihood(str(tmp_path))

    assert [p["param_name"] for p in profiles] == ["m/b", "m/a"]
    assert profiles[0]["ci_95"] == [0.5, 2.0]
    assert ca_run_history.profile_likelihood(str(tmp_path / "none")) is None


# ---------------------------------------------------------------------------
# The option descriptors come from CA
# ---------------------------------------------------------------------------
def test_identifiability_defaults_serve_cas_descriptors_marked_by_method(client):
    import solver_options

    if not solver_options.analysis_options_introspected():
        pytest.skip("circulatory_autogen not importable")
    body = client.get("/api/identifiability/defaults").json()
    if not body["param_reduction_available"]:
        pytest.skip("this circulatory_autogen has no parameter reduction")
    options = {o["name"]: o for o in body["param_reduction"]["options"]}
    assert body["param_reduction"]["enable_flag"] == "do_param_reduction"
    # CA's REDUCTIONS registry says which method reads which option.
    assert options["criterion"]["methods"] == ["sobol"]
    assert options["min_mode_std"]["methods"] == ["pca"]
    assert set(options["n_params"]["methods"]) == {"pca", "sobol"}
    assert "methods" not in options["method"]
    assert "methods" not in options["sensitivity_dir"]
    assert [o["name"] for o in body["ia_options"]][0] == "method"


# ---------------------------------------------------------------------------
# Endpoints (fake runner)
# ---------------------------------------------------------------------------
#: Stands in for the sensitivity runner's reduce-only arm: writes what CA writes.
FAKE_REDUCE_RUNNER = """
import json, os, sys
cfg = json.load(open(sys.argv[1]))
opts = cfg["settings"]["param_reduction_options"]
out = os.path.join(opts["sensitivity_dir"], "param_reduction")
os.makedirs(out, exist_ok=True)
json.dump({"version": 1, "params": [{"targets": ["Lotka_Volterra_module/alpha"],
           "min": 0.1, "max": 7}]},
          open(os.path.join(out, "reduced_params_for_id.json"), "w"))
json.dump({"reduction": opts, "selected": [{"parameter": "Lotka_Volterra_module/alpha",
           "score": 0.7}], "reduced_params_for_id": os.path.join(out,
           "reduced_params_for_id.json")}, open(os.path.join(out, "param_reduction.json"), "w"))
print("__SENSITIVITY_META__ " + json.dumps({"method": "param_reduction"}), flush=True)
print("__SENSITIVITY_DONE__", flush=True)
"""

#: CA's SensitivityFilesError, as the runner reports it.
FAKE_MISSING_FILES_RUNNER = """
print("__SENSITIVITY_FAILED__ a \\"pca\\" reduction reads the Sobol samples and outputs, "
      "and the run that wrote x saved no ['samples']; rerun the Sobol analysis.", flush=True)
raise SystemExit(1)
"""


def _install(tmp_path, src):
    path = tmp_path / "fake_runner.py"
    path.write_text(src)
    sensitivity_mod.param_reduction.runner_path = str(path)


def _lv_study(client):
    model_id = upload_model(client, LV_MODEL)["model_id"]
    r = client.post("/api/obs_data/upload",
                    json={"model_id": model_id, "obs_data": json.loads(LV_OBS.read_text())})
    assert r.status_code == 200, r.text
    with open(LV_PARAMS, "rb") as fh:
        r = client.post(f"/api/params_for_id/upload?model_id={model_id}",
                        files={"file": (LV_PARAMS.name, fh, "text/csv")})
    assert r.status_code == 200, r.text
    return model_id


def _wait(client, base, job_id, timeout=60):
    deadline, offset, lines = time.time() + timeout, 0, []
    while time.time() < deadline:
        s = client.get(f"{base}/{job_id}/status?offset={offset}").json()
        lines += s["lines"]
        offset = s["next_offset"]
        if s["state"] != "running":
            return s, lines
        time.sleep(0.05)
    raise AssertionError("job did not finish:\n" + "\n".join(lines))


def test_reducing_an_existing_sobol_run_returns_the_report(client, tmp_path):
    _install(tmp_path, FAKE_REDUCE_RUNNER)
    model_id = _lv_study(client)
    sa_dir = tmp_path / "sa"
    sa_dir.mkdir()

    r = client.post("/api/param_reduction/run", json={"model_id": model_id, "settings": {
        "source": "existing",
        "param_reduction_options": {"method": "sobol", "n_params": 1,
                                    "sensitivity_dir": str(sa_dir)}}})
    assert r.status_code == 200, r.text
    status, lines = _wait(client, "/api/param_reduction", r.json()["job_id"])

    assert status["state"] == "done", "\n".join(lines)
    assert status["reduction"]["selected"][0]["parameter"] == "Lotka_Volterra_module/alpha"
    assert status["reduction"]["report_dir"] == str(sa_dir / "param_reduction")


def test_a_missing_sobol_file_reaches_the_user_in_cas_words(client, tmp_path):
    _install(tmp_path, FAKE_MISSING_FILES_RUNNER)
    model_id = _lv_study(client)
    r = client.post("/api/param_reduction/run", json={"model_id": model_id, "settings": {
        "source": "existing",
        "param_reduction_options": {"method": "pca", "sensitivity_dir": str(tmp_path)}}})
    status, _ = _wait(client, "/api/param_reduction", r.json()["job_id"])

    assert status["state"] == "error"
    assert "rerun the Sobol analysis" in status["error"]


def test_reducing_needs_a_sobol_run_directory(client, tmp_path):
    model_id = _lv_study(client)
    r = client.post("/api/param_reduction/run", json={"model_id": model_id, "settings": {
        "source": "existing",
        "param_reduction_options": {"sensitivity_dir": str(tmp_path / "nope")}}})
    assert r.status_code == 422


def test_sobol_first_runs_the_sensitivity_runner_with_the_reduction_on(client, tmp_path,
                                                                        monkeypatch):
    model_id = _lv_study(client)
    started = {}
    monkeypatch.setattr(sensitivity_mod.param_reduction, "start",
                        lambda config: started.update(config) or "job")
    r = client.post("/api/param_reduction/run", json={"model_id": model_id, "settings": {
        "source": "sobol_first", "num_samples": 8,
        "param_reduction_options": {"method": "pca"}}})

    assert r.status_code == 200, r.text
    assert started["settings"]["method"] == "sobol"
    assert started["settings"]["do_param_reduction"] is True
    assert started["settings"]["param_reduction_options"] == {"method": "pca"}
    assert "source" not in started["settings"]
    assert not started.get("reduce_only")


def test_heatmap_is_served_from_the_api(client, tmp_path):
    _write_report(str(tmp_path))
    r = client.get("/api/param_reduction/heatmap", params={"report_dir": str(tmp_path)})
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    _write_report(str(tmp_path / "s"), method="sobol")
    r = client.get("/api/param_reduction/heatmap", params={"report_dir": str(tmp_path / "s")})
    assert r.status_code == 404


def test_calibrate_these_adopts_the_reduced_params_for_id(client, tmp_path):
    import main

    model_id = _lv_study(client)
    _write_report(str(tmp_path))

    r = client.post("/api/param_reduction/adopt",
                    json={"model_id": model_id, "report_dir": str(tmp_path)})

    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["qnames"] for p in body["params"]] == [["Lotka_Volterra_module/alpha"]]
    assert body["filename"] == "reduced_params_for_id.json"
    stored = json.loads(main._models[model_id].params_path.read_text())
    assert stored == REDUCED_PARAMS


def test_sobol_runs_endpoint_scans_the_outputs_directory(client, tmp_path):
    (tmp_path / "all_outputs_n8_Sobol_indices.csv").write_text("Parameter\n")
    runs = client.get("/api/param_reduction/sobol_runs",
                      params={"config_outputs_dir": str(tmp_path)}).json()["runs"]
    assert [r["dir"] for r in runs] == [str(tmp_path)]


def test_sobol_runs_endpoint_finds_a_study_workflow_steps_run(client, tmp_path):
    """A Sobol run from the Sensitivity tab is a study workflow step: its results are in
    <W>/workflows/study/sobol-1/sensitivity, and a reopened working dir must offer it."""
    step = tmp_path / "workflows" / "study" / "sobol-1" / "sensitivity"
    step.mkdir(parents=True)
    for suffix in ("indices", "samples", "outputs"):
        (step / f"all_outputs_n64_Sobol_{suffix}.csv").write_text("Parameter\n")
    (step / "sobol_problem.json").write_text("{}")
    runs = client.get("/api/param_reduction/sobol_runs",
                      params={"config_outputs_dir": str(tmp_path)}).json()["runs"]
    assert [(r["dir"], r["has_samples"]) for r in runs] == [(str(step), True)]


# ---------------------------------------------------------------------------
# Identifiability analysis through the UQ runner
# ---------------------------------------------------------------------------
def test_ia_options_reach_cas_identifiability_analysis():
    import uq_runner

    assert uq_runner._ia_options({}) == {"method": "Laplace"}
    assert uq_runner._ia_options({"ia_options": {
        "method": "profile_likelihood", "num_points": 5, "sub_method": None}}) == {
        "method": "profile_likelihood", "num_points": 5}
    # Not forwarded into the MCMC UQ_options.
    assert "ia_options" not in uq_runner._uq_options({"ia_options": {"method": "Laplace"}})


FAKE_PROFILE_RUNNER = """
import json, os, sys
cfg = json.load(open(sys.argv[1]))
out = os.path.join(cfg["output_dir"], "profile_likelihood")
os.makedirs(out, exist_ok=True)
json.dump({"parameters": ["m/a"]}, open(os.path.join(out, "summary.json"), "w"))
json.dump({"param_name": "m/a", "best_val": 1.0, "ci_95": [0.5, 1.5], "param_values": [1],
           "costs": [0]}, open(os.path.join(out, "param_0_m_a.json"), "w"))
print("__UQ_META__ " + json.dumps({"method": "laplace", "ia_method": "profile_likelihood"}),
      flush=True)
print("__UQ_DONE__", flush=True)
"""


def test_a_profile_likelihood_run_reports_its_profiles(client, tmp_path, monkeypatch):
    path = tmp_path / "fake_uq_runner.py"
    path.write_text(FAKE_PROFILE_RUNNER)
    uq_mod.uq.runner_path = str(path)
    model_id = _lv_study(client)
    import calibration

    monkeypatch.setattr(calibration.calibration, "last_completed_best_params",
                        lambda _id: {"Lotka_Volterra_module/alpha": 1.0})
    r = client.post("/api/uq/run", json={"model_id": model_id, "settings": {
        "method": "laplace", "ia_options": {"method": "profile_likelihood"}}})
    assert r.status_code == 200, r.text
    status, lines = _wait(client, "/api/uq", r.json()["job_id"])

    assert status["state"] == "done", "\n".join(lines)
    assert status["ia_method"] == "profile_likelihood"
    assert status["profiles"][0]["ci_95"] == [0.5, 1.5]


# ---------------------------------------------------------------------------
# Integration: a real Sobol run and pca reduction on Lotka-Volterra
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_sobol_then_pca_reduction_on_lotka_volterra(client, requires_simulation, tmp_path):
    import solver_options

    if not any(o["name"] == "redundancy_check"
               for o in solver_options.analysis_mode_options("param_reduction")):
        pytest.skip("this circulatory_autogen has no pca parameter reduction")
    model_id = _lv_study(client)
    out = tmp_path / "outputs"
    r = client.post("/api/param_reduction/run", json={"model_id": model_id, "settings": {
        "source": "sobol_first", "num_samples": 8, "sample_type": "saltelli",
        "config_outputs_dir": str(out),
        "param_reduction_options": {"method": "pca"}}})
    assert r.status_code == 200, r.text
    status, lines = _wait(client, "/api/param_reduction", r.json()["job_id"], timeout=480)

    assert status["state"] == "done", "\n".join(lines[-40:])
    reduction = status["reduction"]
    assert reduction["reduction"]["method"] == "pca"
    assert reduction["modes"]
    assert reduction["reduced_params_for_id"]

    # The same reduction over a copy of that run without its samples: CA's
    # SensitivityFilesError reaches the user.
    bare = tmp_path / "bare"
    bare.mkdir()
    for path in out.glob("*_Sobol_indices.csv"):
        shutil.copy(path, bare / path.name)
    r = client.post("/api/param_reduction/run", json={"model_id": model_id, "settings": {
        "source": "existing",
        "param_reduction_options": {"method": "pca", "sensitivity_dir": str(bare)}}})
    status, lines = _wait(client, "/api/param_reduction", r.json()["job_id"], timeout=120)
    assert status["state"] == "error", "\n".join(lines)
    assert "rerun the Sobol analysis" in status["error"]
