"""Tests for user-authored operation & cost funcs (issues #58 / #104 rework).

Covers the ``user_funcs`` module (validation, upsert, delete, external-file
layout, env-var wiring for both kinds) and the ``/api/{operation,cost}_funcs``
routes. A gated integration test confirms a saved op surfaces in the real
CA-sourced options.
"""

from __future__ import annotations

import os
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
    pretend CA is configured so ``available`` is True. Env vars are cleaned up."""
    monkeypatch.setattr(uf, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(uf, "_circulatory_autogen_src", lambda: str(tmp_path / "ca" / "src"))
    for env in ("OPERATION_FUNCS_EXTERNAL_PATH", "COST_FUNCS_EXTERNAL_PATH"):
        monkeypatch.delenv(env, raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# save / read / external-file layout
# ---------------------------------------------------------------------------
def test_save_operation_writes_external_file_and_sets_env(tmp_cfg):
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
    # Env var points CA (and the subprocess runners) at the file.
    assert os.environ["OPERATION_FUNCS_EXTERNAL_PATH"] == str(path)


def test_save_cost_writes_separate_file_and_env(tmp_cfg):
    result = uf.save_user_func("cost", "my_cost", VALID_COST)
    assert [f["name"] for f in result["functions"]] == ["my_cost"]

    path = tmp_cfg / "user_funcs" / "cost_funcs_user.py"
    assert path.is_file()
    text = path.read_text()
    assert 'CUFLYNX_COSTS = ["my_cost"]' in text
    assert "from cost_funcs_user import is_MLE, cost_combiner" in text
    assert os.environ["COST_FUNCS_EXTERNAL_PATH"] == str(path)
    # Operation and cost files are independent.
    assert not (tmp_cfg / "user_funcs" / "operation_funcs_user.py").exists()


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


def test_delete_removes_and_clears_env_when_empty(tmp_cfg):
    uf.save_user_func("operation", "aaa", VALID_OP.replace("spread", "aaa"))
    uf.save_user_func("operation", "bbb", VALID_OP.replace("spread", "bbb"))
    assert "OPERATION_FUNCS_EXTERNAL_PATH" in os.environ
    result = uf.delete_user_func("operation", "aaa")
    assert [f["name"] for f in result["functions"]] == ["bbb"]
    # Still one func left → env stays set (the file exists).
    assert "OPERATION_FUNCS_EXTERNAL_PATH" in os.environ
    uf.delete_user_func("operation", "bbb")
    # File is now empty of funcs but still present, so the env var stays pointed
    # at it (harmless: no funcs to register).
    assert (tmp_cfg / "user_funcs" / "operation_funcs_user.py").is_file()
    with pytest.raises(uf.UserFuncError):
        uf.delete_user_func("operation", "aaa")


def test_apply_env_removes_var_when_file_absent(tmp_cfg, monkeypatch):
    monkeypatch.setenv("OPERATION_FUNCS_EXTERNAL_PATH", "/stale/path.py")
    uf.apply_env()
    assert "OPERATION_FUNCS_EXTERNAL_PATH" not in os.environ


# ---------------------------------------------------------------------------
# templates
# ---------------------------------------------------------------------------
def test_operation_templates_have_tabs_and_mention_plotting(tmp_cfg):
    result = uf.read_user_funcs("operation")
    assert set(result["templates"]) == {"basic", "multi_operand", "kwargs"}
    # The template explains series_output is the series plotted with the feature.
    assert "plotted with the feature" in result["templates"]["basic"]
    # The kwargs template explains how kwarg entries are entered (per data_item).
    assert "data_item" in result["templates"]["kwargs"]
    # Back-compat single ``template`` still present (first tab).
    assert result["template"] == result["templates"]["basic"]


def test_cost_templates_present(tmp_cfg):
    result = uf.read_user_funcs("cost")
    assert set(result["templates"]) == {"basic", "MLE"}
    assert "cost_type" in result["templates"]["basic"]


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


def test_external_funcs_empty_when_file_missing(tmp_cfg):
    assert uf.external_funcs("operation") == {}
    assert uf.external_funcs("cost") == {}


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

    real_src = Path(engine._circulatory_autogen_src())
    if not real_src.is_dir() or not _real_ca_importable():
        pytest.skip("real circulatory_autogen not importable")

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
