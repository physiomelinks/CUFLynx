"""Tests for user-authored operation & cost funcs (issues #58 / #104 rework).

Covers the ``user_funcs`` module (validation, upsert, delete, external-file
layout under the output dir, templates for both kinds) and the
``/api/{operation,cost}_funcs`` routes. A gated integration test confirms a saved
op surfaces in the real CA-sourced options.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

import user_funcs as uf

VALID_OP = (
    "def spread(x, series_output=False):\n"
    "    if series_output:\n"
    "        return x\n"
    "    return float(np.max(x) - np.min(x))\n"
)
VALID_COST = (
    "def my_cost(output, desired_mean, std, weight):\n"
    "    return float(np.sum(((output - desired_mean) / std) ** 2 * weight))\n"
)


@pytest.fixture
def tmp_cfg(tmp_path, monkeypatch):
    """Point the config dir (where external func files live) at a tmp dir, and
    pretend CA is configured so ``available`` is True."""
    monkeypatch.setattr(uf, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(uf, "_circulatory_autogen_src", lambda: str(tmp_path / "ca" / "src"))
    return tmp_path


# ---------------------------------------------------------------------------
# save / read / external-file layout
# ---------------------------------------------------------------------------
def test_save_operation_writes_external_file_and_exposes_path(tmp_cfg):
    result = uf.save_user_func("operation", "spread", VALID_OP)
    assert [f["name"] for f in result["functions"]] == ["spread"]

    path = tmp_cfg / "user_funcs" / "operation_funcs_user.py"
    assert path.is_file()
    text = path.read_text()
    assert 'CUFLYNX_OPERATIONS = ["spread"]' in text
    assert "def spread(" in text
    # Imported (not defined) decorators so CA registers only the user func.
    assert "from param_id.differentiable import differentiable" in text
    assert "def differentiable(" not in text
    # The path CUFLynx hands CA (config key operation_funcs_external_path; CA #303).
    assert uf.external_path("operation") == str(path)
    assert uf.external_paths()["operation_funcs_external_path"] == str(path)


def test_save_cost_writes_separate_file_and_path(tmp_cfg):
    result = uf.save_user_func("cost", "my_cost", VALID_COST)
    assert [f["name"] for f in result["functions"]] == ["my_cost"]

    path = tmp_cfg / "user_funcs" / "cost_funcs_user.py"
    assert path.is_file()
    text = path.read_text()
    assert 'CUFLYNX_COSTS = ["my_cost"]' in text
    assert "from cost_funcs_user import is_MLE, cost_combiner" in text
    assert uf.external_path("cost") == str(path)
    assert uf.external_paths()["cost_funcs_external_path"] == str(path)
    # Operation and cost files are independent; no operation file was created.
    assert not (tmp_cfg / "user_funcs" / "operation_funcs_user.py").exists()
    assert "operation_funcs_external_path" not in uf.external_paths()


def test_does_not_write_into_ca_tree(tmp_cfg):
    """The rework must not touch CA's funcs_user (no bridge)."""
    uf.save_user_func("operation", "spread", VALID_OP)
    assert not (tmp_cfg / "ca").exists()  # only the config dir was written


def test_edit_updates_in_place(tmp_cfg):
    uf.save_user_func("operation", "spread", VALID_OP)
    edited = VALID_OP.replace("np.max(x) - np.min(x)", "np.mean(x)")
    result = uf.save_user_func("operation", "spread", edited)
    assert [f["name"] for f in result["functions"]] == ["spread"]
    assert "np.mean(x)" in result["functions"][0]["source"]
    text = (tmp_cfg / "user_funcs" / "operation_funcs_user.py").read_text()
    assert text.count("def spread(") == 1


def test_decorators_preserved_across_reads_and_edits(tmp_cfg):
    decorated = (
        "@differentiable\n"
        "@series_to_constant\n"
        "def amp(x, series_output=False):\n"
        "    if series_output:\n"
        "        return x\n"
        "    return float(np.max(x) - np.min(x))\n"
    )
    uf.save_user_func("operation", "amp", decorated)
    assert "@differentiable" in uf.read_user_funcs("operation")["functions"][0]["source"]
    uf.save_user_func("operation", "plain", VALID_OP.replace("spread", "plain"))
    text = (tmp_cfg / "user_funcs" / "operation_funcs_user.py").read_text()
    assert "@differentiable" in text and "@series_to_constant" in text


def test_multiple_preserve_order(tmp_cfg):
    uf.save_user_func("operation", "aaa", VALID_OP.replace("spread", "aaa"))
    uf.save_user_func("operation", "bbb", VALID_OP.replace("spread", "bbb"))
    result = uf.read_user_funcs("operation")
    assert [f["name"] for f in result["functions"]] == ["aaa", "bbb"]


def test_delete_removes(tmp_cfg):
    uf.save_user_func("operation", "aaa", VALID_OP.replace("spread", "aaa"))
    uf.save_user_func("operation", "bbb", VALID_OP.replace("spread", "bbb"))
    result = uf.delete_user_func("operation", "aaa")
    assert [f["name"] for f in result["functions"]] == ["bbb"]
    uf.delete_user_func("operation", "bbb")
    # File is now empty of funcs but still present, so the path stays exposed
    # (harmless: no funcs to register).
    assert (tmp_cfg / "user_funcs" / "operation_funcs_user.py").is_file()
    with pytest.raises(uf.UserFuncError):
        uf.delete_user_func("operation", "aaa")


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------
def test_operation_templates_have_tabs_and_mention_plotting(tmp_cfg):
    result = uf.read_user_funcs("operation")
    assert set(result["templates"]) == {"basic", "multi_operand", "kwargs", "differentiable"}
    # The template explains series_output is the series plotted with the feature.
    assert "plotted with the feature" in result["templates"]["basic"]
    # The kwargs template explains how kwarg entries are entered (per data_item).
    assert "data_item" in result["templates"]["kwargs"]
    # The differentiable template uses @differentiable + the casadi math backend
    # and states it is needed for AD or FSA.
    diff = result["templates"]["differentiable"]
    assert "@differentiable" in diff and "mb." in diff and "AD or FSA" in diff
    # Back-compat single ``template`` still present (first tab).
    assert result["template"] == result["templates"]["basic"]


def test_cost_templates_present(tmp_cfg):
    result = uf.read_user_funcs("cost")
    assert set(result["templates"]) == {"basic", "differentiable", "MLE"}
    assert "cost_type" in result["templates"]["basic"]
    # The differentiable cost template uses @differentiable + the math backend.
    assert "@differentiable" in result["templates"]["differentiable"]
    assert "mb." in result["templates"]["differentiable"]
    # The old "see CA's cost_funcs_user.py" pointer is gone from every template.
    assert not any("see CA" in t for t in result["templates"].values())


def test_saves_under_output_directory(tmp_cfg, tmp_path):
    """With an output dir the func file lives there, not in the config dir."""
    out = tmp_path / "run_outputs"
    uf.save_user_func("operation", "spread", VALID_OP, base_dir=str(out))
    path = out / "user_funcs" / "operation_funcs_user.py"
    assert path.is_file()
    assert uf.external_path("operation", str(out)) == str(path)
    assert uf.external_paths(str(out)) == {"operation_funcs_external_path": str(path)}
    # It is NOT in the config-dir fallback location.
    assert uf.external_path("operation") is None
    # And it round-trips when read back with the same base_dir.
    assert [f["name"] for f in uf.read_user_funcs("operation", str(out))["functions"]] == ["spread"]


def test_differentiable_operation_header_defines_mb(tmp_cfg):
    """The generated file imports the math backend and binds ``mb`` so casadi
    templates work when CA rebinds it."""
    uf.save_user_func("operation", "spread", VALID_OP)
    text = (tmp_cfg / "user_funcs" / "operation_funcs_user.py").read_text()
    assert "from param_id.math_backend import make_math_backend" in text
    assert "mb = make_math_backend(" in text


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["1bad", "has space", "def", "", "_hidden", "np"])
def test_invalid_name_rejected(tmp_cfg, bad):
    with pytest.raises(uf.UserFuncError):
        uf.save_user_func("operation", bad, VALID_OP.replace("spread", "ok"))


def test_cost_reserved_name_rejected(tmp_cfg):
    with pytest.raises(uf.UserFuncError):
        uf.save_user_func("cost", "is_MLE", VALID_COST.replace("my_cost", "is_MLE"))


def test_invalid_syntax_rejected(tmp_cfg):
    with pytest.raises(uf.UserFuncError):
        uf.save_user_func("operation", "spread", "def spread(x)\n    return x\n")


def test_name_must_match_def(tmp_cfg):
    with pytest.raises(uf.UserFuncError):
        uf.save_user_func("operation", "spread", VALID_OP.replace("def spread", "def other"))


def test_multiple_top_level_defs_rejected(tmp_cfg):
    src = VALID_OP + "\ndef helper(y):\n    return y\n"
    with pytest.raises(uf.UserFuncError):
        uf.save_user_func("operation", "spread", src)


def test_unknown_kind_rejected(tmp_cfg):
    with pytest.raises(uf.UserFuncError):
        uf.save_user_func("bogus", "spread", VALID_OP)


def test_read_available_false_when_ca_unconfigured(tmp_cfg, monkeypatch):
    monkeypatch.setattr(uf, "_circulatory_autogen_src", lambda: "")
    result = uf.read_user_funcs("operation")
    assert result["available"] is False
    assert result["templates"]


def test_external_path_none_when_file_missing(tmp_cfg):
    assert uf.external_path("operation") is None
    assert uf.external_path("cost") is None
    assert uf.external_paths() == {}


# ---------------------------------------------------------------------------
# back-compat shims (operation-only API from issue #58)
# ---------------------------------------------------------------------------
def test_back_compat_operation_helpers(tmp_cfg):
    uf.save_user_operation("spread", VALID_OP)
    assert [f["name"] for f in uf.read_user_operations()["functions"]] == ["spread"]
    uf.delete_user_operation("spread")
    assert uf.read_user_operations()["functions"] == []
    assert uf.UserOperationError is uf.UserFuncError


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
def test_operation_routes_save_list_delete(client, tmp_cfg):
    resp = client.get("/api/operation_funcs")
    assert resp.status_code == 200, resp.text
    assert resp.json()["functions"] == []
    assert resp.json()["templates"]

    resp = client.post("/api/operation_funcs", json={"name": "spread", "source": VALID_OP})
    assert resp.status_code == 200, resp.text
    assert [f["name"] for f in resp.json()["functions"]] == ["spread"]

    resp = client.get("/api/operation_funcs")
    assert [f["name"] for f in resp.json()["functions"]] == ["spread"]

    resp = client.delete("/api/operation_funcs/spread")
    assert resp.status_code == 200, resp.text
    assert resp.json()["functions"] == []


def test_cost_routes_save_list_delete(client, tmp_cfg):
    resp = client.post("/api/cost_funcs", json={"name": "my_cost", "source": VALID_COST})
    assert resp.status_code == 200, resp.text
    assert [f["name"] for f in resp.json()["functions"]] == ["my_cost"]

    resp = client.get("/api/cost_funcs")
    assert [f["name"] for f in resp.json()["functions"]] == ["my_cost"]

    resp = client.delete("/api/cost_funcs/my_cost")
    assert resp.status_code == 200, resp.text
    assert resp.json()["functions"] == []


def test_route_invalid_returns_422(client, tmp_cfg):
    resp = client.post("/api/operation_funcs", json={"name": "1bad", "source": VALID_OP})
    assert resp.status_code == 422
    resp = client.post("/api/cost_funcs", json={"name": "my_cost", "source": "def my_cost(x)\n x"})
    assert resp.status_code == 422


def test_route_saves_under_output_dir(client, tmp_cfg, tmp_path):
    out = tmp_path / "outs"
    resp = client.post(
        "/api/operation_funcs",
        json={"name": "spread", "source": VALID_OP, "output_dir": str(out)},
    )
    assert resp.status_code == 200, resp.text
    assert (out / "user_funcs" / "operation_funcs_user.py").is_file()
    # Listing with the same output_dir returns it; the config-dir fallback is empty.
    resp = client.get("/api/operation_funcs", params={"output_dir": str(out)})
    assert [f["name"] for f in resp.json()["functions"]] == ["spread"]
    assert client.get("/api/operation_funcs").json()["functions"] == []


def test_route_rejects_relative_output_dir(client, tmp_cfg):
    resp = client.post(
        "/api/operation_funcs",
        json={"name": "spread", "source": VALID_OP, "output_dir": "relative/dir"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# integration: surfaces in the real CA-sourced options
# ---------------------------------------------------------------------------
def _real_ca_importable() -> bool:
    import obs_options

    obs_options.reset_cache()
    opts = obs_options.get_obs_data_options(refresh=True)
    return "steady_state_avg" in opts.get("operations", [])


@pytest.mark.integration
def test_saved_op_appears_in_obs_options(tmp_path, monkeypatch):
    """End-to-end: a saved external op shows up in obs_options against a real CA."""
    import engine
    import obs_options

    import inspect

    real_src = Path(engine._circulatory_autogen_src())
    if not real_src.is_dir() or not _real_ca_importable():
        pytest.skip("real circulatory_autogen not importable")
    import operation_funcs

    if "external_path" not in inspect.signature(
        operation_funcs.get_operation_funcs_dict_for_mode
    ).parameters:
        pytest.skip("circulatory_autogen lacks external-path support (#303)")

    monkeypatch.setattr(uf, "config_dir", lambda: tmp_path)
    purge = ("operation_funcs", "cost_funcs_user")
    saved_modules = {m: sys.modules.get(m) for m in purge}
    saved_path = list(sys.path)
    try:
        uf.save_user_func("operation", "my_spread", VALID_OP.replace("spread", "my_spread"))
        obs_options.reset_cache()
        opts = obs_options.get_obs_data_options(refresh=True)
        assert "my_spread" in opts["operations"]
    finally:
        for m in purge:
            if saved_modules[m] is not None:
                sys.modules[m] = saved_modules[m]
            else:
                sys.modules.pop(m, None)
        sys.path[:] = saved_path
        obs_options.reset_cache()


# ---------------------------------------------------------------------------
# integration: a custom operation's kwargs actually change the computed value
# ---------------------------------------------------------------------------
KWARG_OP = (
    "def scaled_max(x, factor=1.0, series_output=False):\n"
    "    if series_output:\n"
    "        return x\n"
    "    return float(np.max(x) * factor)\n"
)


@pytest.mark.integration
def test_custom_operation_kwargs_change_the_computed_value(tmp_path, monkeypatch):
    """End-to-end for the kwargs feature (#112/#113) on a CUFLynx-authored op (#104).

    Proves the whole chain does something, not just that values are carried:
      1. the op's kwarg is discoverable for the editor (operation_kwargs_schema),
      2. circulatory_autogen parses our obs_data.json and keeps operation_kwargs,
      3. calling the registered op the way CA does yields a *different* value for a
         different kwarg — i.e. the inputs the editor collects have an effect.
    """
    import inspect
    import json

    import numpy as np

    import engine
    import obs_options

    real_src = Path(engine._circulatory_autogen_src())
    if not real_src.is_dir() or not _real_ca_importable():
        pytest.skip("real circulatory_autogen not importable")
    import operation_funcs

    if "external_path" not in inspect.signature(
        operation_funcs.get_operation_funcs_dict_for_mode
    ).parameters:
        pytest.skip("circulatory_autogen lacks external-path support (#303)")

    out = tmp_path / "outputs"
    monkeypatch.setattr(uf, "config_dir", lambda: tmp_path / "cfg")
    uf.save_user_func("operation", "scaled_max", KWARG_OP, base_dir=str(out))
    ext = uf.external_path("operation", str(out))

    # 1) Discoverable: the op AND its tunable kwarg reach the editor's schema.
    obs_options.reset_cache()
    try:
        opts = obs_options.get_obs_data_options(refresh=True, output_dir=str(out))
        assert "scaled_max" in opts["operations"]
        # `series_output` is reserved and must not be offered as a tunable.
        assert opts["operation_kwargs_schema"]["scaled_max"] == [
            {"name": "factor", "default": 1.0, "type": "number"}
        ]
    finally:
        obs_options.reset_cache()

    # 2) CA parses a CUFLynx-shaped obs_data.json and keeps the kwargs.
    from parsers.PrimitiveParsers import ObsAndParamDataParser

    obs = {
        "protocol_info": {"pre_times": [0.0], "sim_times": [[5]], "params_to_change": {}},
        "prediction_items": [],
        "data_items": [
            {"variable": "x_max", "name_for_plotting": "x_max", "data_type": "constant",
             "operation": "scaled_max", "operands": ["m/x"], "unit": "dimensionless",
             "weight": 1.0, "value": 30, "std": 3, "experiment_idx": 0,
             "subexperiment_idx": 0, "operation_kwargs": {"factor": 2.0}},
        ],
    }
    obs_path = tmp_path / "obs_data.json"
    obs_path.write_text(json.dumps(obs))

    parser = ObsAndParamDataParser()
    parsed = parser.parse_obs_data_json(
        param_id_obs_path=str(obs_path), pre_time=0.0, sim_time=5.0,
        model_type="cellml_only",
    )
    obs_info = parser.process_obs_info(parsed["gt_df"], str(tmp_path), 0.01)
    assert obs_info["operations"][0] == "scaled_max"
    assert obs_info["operation_kwargs"][0] == {"factor": 2.0}

    # 3) The kwarg changes the result — CA invokes func(*operands, **kwargs).
    op_funcs = operation_funcs.get_operation_funcs_dict_for_mode("numpy", external_path=ext)
    fn = op_funcs["scaled_max"]
    series = np.array([1.0, 3.0, 2.0])
    assert fn(series, **obs_info["operation_kwargs"][0]) == pytest.approx(6.0)
    assert fn(series, **{"factor": 4.0}) == pytest.approx(12.0)
    assert fn(series) == pytest.approx(3.0)  # the op's own default
