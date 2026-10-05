"""An archive's own operation / cost / modifier funcs are installed on import.

A study whose obs_data names an operation the archive itself defines (a PhLynx
export carrying ``operation_funcs_user.py`` with ``ratio``, its obs_data using
``"operation": "ratio"``) used to load with the funcs file kept byte-for-byte and
never installed -- so the study could not be calibrated until the user pasted the
function into Custom funcs by hand. The import now installs each top-level ``def``
into the user funcs store under the upload's outputs directory, through the same
``save_user_func`` the ``POST /api/{kind}_funcs`` route uses.
"""

from __future__ import annotations

import io
import json
import math
import time
import zipfile

import pytest

import omex_import
import user_funcs as uf
from conftest import LV_MODEL_PATH, LV_PARAMS_CSV_PATH

RATIO_FUNCS = '''"""User operation funcs for the study (operation_funcs_external_path)."""
import numpy as np

from libcuflynx.param_id.operation_funcs import series_to_constant


@series_to_constant
def ratio(x, y, series_output=False):
    if series_output:
        return x / y
    return np.mean(x) / np.mean(y)
'''

OTHER_RATIO = (
    "def ratio(x, y, series_output=False):\n"
    "    if series_output:\n"
    "        return y / x\n"
    "    return float(np.max(y) / np.max(x))\n"
)


def _obs_data(operation: str = "ratio") -> dict:
    """The Lotka-Volterra obs_data with one item scored through ``operation``."""
    return {
        "protocol_info": {"pre_times": [0.0], "sim_times": [[5]], "params_to_change": {}},
        "prediction_items": [],
        "data_items": [
            {
                "data_item_name": "xy_ratio",
                "data_type": "constant",
                "operation": operation,
                "operands": ["Lotka_Volterra_module/x", "Lotka_Volterra_module/y"],
                "unit": "dimensionless",
                "weight": 1.0,
                "value": 1.0,
                "std": 0.1,
                "experiment_idx": 0,
                "subexperiment_idx": 0,
            }
        ],
    }


def _archive(funcs: dict | None = None, **extra) -> bytes:
    members = {
        "lv.cellml": LV_MODEL_PATH.read_bytes(),
        "lv_obs_data.json": json.dumps(_obs_data()),
        "lv_params_for_id.csv": LV_PARAMS_CSV_PATH.read_bytes(),
        **(funcs if funcs is not None else {"operation_funcs_user.py": RATIO_FUNCS}),
        **extra,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _upload(client, data: bytes, out_dir=None):
    params = {"output_dir": str(out_dir)} if out_dir else {}
    resp = client.post(
        "/api/omex/upload", params=params,
        files={"file": ("study.omex", data, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def cfg_dir(tmp_path, monkeypatch):
    """Keep the no-outputs-dir fallback store out of the shared test config dir."""
    monkeypatch.setattr(uf, "config_dir", lambda: tmp_path / "cfg")
    return tmp_path / "cfg"


# ---------------------------------------------------------------------------
# Which members count
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "name, kind",
    [
        ("operation_funcs_user.py", "operation"),
        ("funcs/my_operation_funcs.py", "operation"),
        ("Cost_Funcs_user.py", "cost"),
        ("modifier_funcs_user.py", "modifier"),
        # An external_python model is Python too, and must never be split into defs.
        ("user_model.py", None),
        ("operation_funcs_user.txt", None),
        ("operations.py", None),
    ],
)
def test_a_funcs_file_is_recognised_by_its_name(name, kind):
    assert omex_import.user_func_kind(name) == kind


def test_the_archive_kinds_are_the_editors_kinds():
    """omex_import mirrors the list rather than importing the engine-bound module."""
    assert omex_import.USER_FUNC_KINDS == uf.FUNC_KINDS


def test_unpack_returns_each_funcs_file_with_its_kind():
    parts = omex_import.unpack(_archive({
        "operation_funcs_user.py": RATIO_FUNCS,
        "cost_funcs_user.py": "def c(output, desired_mean, std, weight):\n    return 0.0\n",
    }))
    assert [(k, n) for k, n, _ in parts["user_funcs"]] == [
        ("operation", "operation_funcs_user.py"), ("cost", "cost_funcs_user.py")
    ]
    # Still carried whole, for the archive to round-trip.
    assert "operation_funcs_user.py" in parts["members"]


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------
def test_the_archives_operation_is_installed_into_the_outputs_store(client, cfg_dir, tmp_path):
    out = tmp_path / "out"
    body = _upload(client, _archive(), out)

    assert body["user_funcs"] == [
        {"kind": "operation", "name": "ratio", "origin": "operation_funcs_user.py",
         "status": "installed"}
    ]
    assert body["warnings"] == []
    # Where the route would have put it, in the route's canonical format.
    path = out / "user_funcs" / "operation_funcs_user.py"
    text = path.read_text()
    assert 'CUFLYNX_OPERATIONS = ["ratio"]' in text
    assert "@series_to_constant\ndef ratio(x, y, series_output=False):" in text
    listed = client.get("/api/operation_funcs", params={"output_dir": str(out)}).json()
    assert [f["name"] for f in listed["functions"]] == ["ratio"]
    # And the obs_data naming it loaded as usual.
    assert body["obs_data"]["data_items"][0]["operation"] == "ratio"


def test_without_an_outputs_dir_it_goes_where_the_dialog_would_save_it(client, cfg_dir):
    body = _upload(client, _archive())
    assert body["user_funcs"][0]["status"] == "installed"
    assert (cfg_dir / "user_funcs" / "operation_funcs_user.py").is_file()


def test_importing_the_same_archive_twice_is_quiet(client, cfg_dir, tmp_path):
    out = tmp_path / "out"
    _upload(client, _archive(), out)
    body = _upload(client, _archive(), out)
    assert body["user_funcs"][0]["status"] == "unchanged"
    assert body["warnings"] == []


def test_a_different_func_of_the_same_name_is_kept_not_clobbered(client, cfg_dir, tmp_path):
    out = tmp_path / "out"
    uf.save_user_func("operation", "", OTHER_RATIO, str(out))
    before = (out / "user_funcs" / "operation_funcs_user.py").read_text()

    body = _upload(client, _archive(), out)

    assert body["user_funcs"][0]["status"] == "conflict"
    assert (out / "user_funcs" / "operation_funcs_user.py").read_text() == before
    warning = " ".join(body["warnings"])
    assert "'ratio'" in warning and "already" in warning
    assert body["model_id"]  # the study still loaded


def test_existing_funcs_of_other_names_are_kept_beside_the_new_one(client, cfg_dir, tmp_path):
    out = tmp_path / "out"
    uf.save_user_func("operation", "", OTHER_RATIO.replace("def ratio", "def mine"), str(out))
    _upload(client, _archive(), out)
    listed = uf.read_user_funcs("operation", str(out))
    assert [f["name"] for f in listed["functions"]] == ["mine", "ratio"]


def test_a_func_the_dialog_would_reject_is_a_warning_not_a_failed_import(client, cfg_dir, tmp_path):
    out = tmp_path / "out"
    funcs = {"operation_funcs_user.py": RATIO_FUNCS + "\n\ndef _helper(x):\n    return x\n"}
    body = _upload(client, _archive(funcs), out)

    status = {f["name"]: f["status"] for f in body["user_funcs"]}
    assert status == {"ratio": "installed", "_helper": "invalid"}
    assert any("_helper" in w and "not installed" in w for w in body["warnings"])
    assert body["model_id"] and body["obs_data"]["data_items"]


def test_an_unparseable_funcs_file_is_a_warning(client, cfg_dir, tmp_path):
    out = tmp_path / "out"
    body = _upload(client, _archive({"operation_funcs_user.py": "def ratio(:\n"}), out)
    assert body["user_funcs"] == []
    assert any("invalid Python" in w for w in body["warnings"])
    assert not (out / "user_funcs" / "operation_funcs_user.py").exists()
    assert body["model_id"]


def test_module_level_names_the_stored_file_lacks_are_named(client, cfg_dir, tmp_path):
    """Only the defs travel; a func relying on the file's own imports is warned about."""
    funcs = {"operation_funcs_user.py": "import scipy.signal\nSCALE = 2\n\n" + RATIO_FUNCS}
    body = _upload(client, _archive(funcs), tmp_path / "out")
    assert body["user_funcs"][0]["status"] == "installed"
    warning = " ".join(body["warnings"])
    assert "SCALE" in warning and "scipy" in warning
    # np / series_to_constant are what the stored header provides: not named.
    assert "np," not in warning.split("provides")[0]


def test_cost_funcs_install_into_the_cost_store(client, cfg_dir, tmp_path):
    out = tmp_path / "out"
    cost = "def my_cost(output, desired_mean, std, weight):\n    return 0.0\n"
    body = _upload(client, _archive({"cost_funcs_user.py": cost}), out)
    assert body["user_funcs"] == [
        {"kind": "cost", "name": "my_cost", "origin": "cost_funcs_user.py",
         "status": "installed"}
    ]
    assert [f["name"] for f in uf.read_user_funcs("cost", str(out))["functions"]] == ["my_cost"]


def test_the_inbox_names_the_python_a_delivery_would_install():
    """Accepting a delivered study now installs code, so the dialog has to say so."""
    import inbox as inbox_mod

    summary = inbox_mod.Inbox().deliver(_archive(), "http://localhost:5173", "study.omex")
    assert summary["user_funcs"] == ["operation_funcs_user.py"]


# ---------------------------------------------------------------------------
# Usable: CA sees it, and a calibration scored through it runs
# ---------------------------------------------------------------------------
def test_the_installed_operation_is_offered_by_ca(client, cfg_dir, tmp_path, requires_ca_operations):
    import obs_options

    out = tmp_path / "out"
    _upload(client, _archive(), out)
    try:
        opts = client.get(
            "/api/obs_data/options", params={"output_dir": str(out)}
        ).json()
        assert "ratio" in opts["operations"]
    finally:
        obs_options.reset_cache()


@pytest.mark.integration
def test_a_calibration_scored_through_the_archives_operation_runs(
    client, cfg_dir, tmp_path, requires_simulation
):
    out = tmp_path / "out"
    body = _upload(client, _archive(), out)
    assert body["user_funcs"][0]["status"] == "installed"

    settings = {
        "param_id_method": "genetic_algorithm",
        "num_calls_to_function": 30,
        "DEBUG": True,
        "dt": 0.01,
        "config_outputs_dir": str(out),
    }
    resp = client.post(
        "/api/calibration/run", json={"model_id": body["model_id"], "settings": settings}
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    offset, lines = 0, []
    deadline = time.time() + 600
    while time.time() < deadline:
        s = client.get(f"/api/calibration/{job_id}/status?offset={offset}").json()
        lines += s["lines"]
        offset = s["next_offset"]
        if s["state"] != "running":
            break
        time.sleep(0.2)
    assert s["state"] == "done", "\n".join(lines)
    assert s["cost"] is not None and math.isfinite(s["cost"])
