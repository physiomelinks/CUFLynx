"""The settings dict the UI actually sends, through the runners' option filters.

App.vue attaches ``python_path`` and ``config_outputs_dir`` to every analysis
run, and folds the calibration panel's GA settings into the sensitivity
settings when ``run_calibration_first`` is set (``onRunSensitivity`` /
``onRunUQ``). The runners forward any non-reserved settings key into CA's
``sa_options`` / ``UQ_options`` — deliberately, so new CA options flow
through without a runner change — which means every CUFLynx-level key the UI
attaches must be in the reserved sets or it leaks into CA's options. The
earlier filter tests used hand-built settings that never included these keys,
which is how the leak went unnoticed.
"""

import sensitivity_runner
import uq_runner


def _ui_sa_settings() -> dict:
    """Mirrors App.vue onRunSensitivity: the panel's settings, the folded
    calibration block (run_calibration_first), and the always-attached keys."""
    return {
        "method": "local",
        "gradient_method": "AUTO",
        "nominal": "current",
        "rel_step": 0.01,
        "run_calibration_first": True,
        "param_id_method": "genetic_algorithm",
        "num_calls_to_function": 100,
        "max_patience": 10,
        "cost_convergence": 0.0001,
        "python_path": "/usr/bin/python3",
        "config_outputs_dir": "/home/user/cuflynx_outputs",
        "dt": 0.01,
        "num_cores": 1,
        # A genuine CA sensitivity option, which must keep flowing through.
        "num_samples": 128,
    }


def test_sa_options_exclude_every_key_the_ui_attaches():
    sa = sensitivity_runner._sa_options(_ui_sa_settings(), "/out")
    for key in (
        "config_outputs_dir",
        "python_path",
        "param_id_method",
        "num_calls_to_function",
        "max_patience",
        "cost_convergence",
    ):
        assert key not in sa, f"{key} leaked into CA's sa_options"
    # Forward-compatibility is the reason unknown keys flow at all — a real CA
    # option must still arrive.
    assert sa["num_samples"] == 128


def test_uq_options_exclude_every_key_the_ui_attaches():
    settings = {
        "method": "mcmc",
        "num_steps": 200,
        "num_walkers": 16,
        "python_path": "/usr/bin/python3",
        "config_outputs_dir": "/home/user/cuflynx_outputs",
        "dt": 0.01,
        "num_cores": 1,
    }
    opts = uq_runner._uq_options(settings)
    for key in ("config_outputs_dir", "python_path"):
        assert key not in opts, f"{key} leaked into CA's UQ_options"
    assert opts["num_steps"] == 200
    # cost_convergence stays: for MCMC it is a genuine UQ_options value.
    assert "cost_convergence" in opts
