"""The global random seed (Settings popup) must reach CA's options under the key
each engine reads, and be omitted entirely when no seed is set (non-deterministic).

These are pure option-assembly unit tests on the runner scripts — no Myokit / CA
needed (the runner modules only import circulatory_autogen lazily, inside run())."""

from __future__ import annotations

import calibration_runner
import sensitivity_runner
import uq_runner


# ---------------------------------------------------------------------------
# Calibration -> optimiser_options['seed'] (CA's multi-start start-sampler key).
# ---------------------------------------------------------------------------
def test_calibration_seed_forwarded_to_optimiser_options():
    opts = calibration_runner._optimiser_options({"num_starts": 5}, seed=123)
    assert opts["seed"] == 123
    assert opts["num_starts"] == 5


def test_calibration_seed_omitted_when_none():
    opts = calibration_runner._optimiser_options({"num_starts": 5}, seed=None)
    assert "seed" not in opts


def test_calibration_optimiser_options_drop_reserved_and_cuflynx_keys():
    opts = calibration_runner._optimiser_options(
        {"num_calls_to_function": 10, "num_cores": 4, "dt": 0.01, "solver": "x"},
        seed=None,
    )
    assert opts == {"num_calls_to_function": 10}


# ---------------------------------------------------------------------------
# Sensitivity -> sa_options['seed'].
# ---------------------------------------------------------------------------
def test_sensitivity_seed_forwarded_to_sa_options():
    sa = sensitivity_runner._sa_options({"method": "sobol"}, "/out", seed=7)
    assert sa["seed"] == 7
    assert sa["output_dir"] == "/out"


def test_sensitivity_seed_omitted_when_none():
    sa = sensitivity_runner._sa_options({"method": "sobol"}, "/out", seed=None)
    assert "seed" not in sa


# ---------------------------------------------------------------------------
# UQ -> mcmc_options['seed'] and optimiser_options['seed'].
# ---------------------------------------------------------------------------
def test_uq_seed_forwarded_to_mcmc_and_optimiser_options():
    assert uq_runner._mcmc_options({}, seed=99)["seed"] == 99
    assert uq_runner._optimiser_options({}, seed=99)["seed"] == 99


def test_uq_seed_omitted_when_none():
    assert "seed" not in uq_runner._mcmc_options({}, seed=None)
    assert "seed" not in uq_runner._optimiser_options({}, seed=None)
