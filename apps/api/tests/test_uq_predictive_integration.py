"""The direct UQ runner's posterior predictive check, on the Lotka-Volterra fixture.

Every arm must score its posterior: MCMC from its chain, whether its engine was the
calibration's (``run_calibration_first``) or one built for MCMC alone (reusing a best fit,
which has no ``param_id``), and Laplace from its draws (it has no chain). A check that
cannot run is only a log line, so these tests ask for the coverage and the predictive band
themselves.
"""

from __future__ import annotations

import pytest

from test_study_workflow_integration import _run, _setup

pytestmark = pytest.mark.integration

CALIBRATION = {"param_id_method": "CMA-ES", "num_calls_to_function": 20}


@pytest.mark.parametrize("settings", [
    {"method": "mcmc", "num_steps": 12, "num_walkers": 8},
    {"method": "mcmc", "num_steps": 12, "num_walkers": 8, "run_calibration_first": True},
    {"method": "laplace"},
], ids=["mcmc-at-best-fit", "mcmc-calibration-first", "laplace-at-best-fit"])
def test_a_direct_uq_run_scores_its_posterior(client, requires_simulation, tmp_path,
                                              settings):
    model_id = _setup(client, tmp_path)
    common = {"config_outputs_dir": str(tmp_path / "work"), "dt": 0.01}
    if not settings.get("run_calibration_first"):
        _run(client, "calibration", {"model_id": model_id,
                                     "settings": {**common, **CALIBRATION}})
    status, lines = _run(client, "uq", {"model_id": model_id, "settings": {
        **common, **CALIBRATION, "posterior_predictive_samples": 6, **settings}})
    log = "\n".join(lines)
    assert "posterior predictive check failed" not in log, log[-4000:]
    assert (status["coverage"] or {}).get("coverage", {}).get("levels"), log[-4000:]
    band = client.get(f"/api/uq/{status['job_id']}/posterior-predictive").json()
    assert band["available"], band
    assert len(band["lo"]) == len(band["labels"]) == len(band["hi"]) > 0
