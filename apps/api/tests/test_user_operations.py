"""Tests for user-authored observable operations (issue #58).

Covers the ``user_operations`` module (validation, upsert, delete, bridge) and
the ``/api/operation_funcs`` routes. A stub ``operation_funcs_user.py`` mirroring
CA's ``register_user_operations`` contract proves a saved func is discovered; a
gated integration test confirms it surfaces in the real CA-sourced options.
"""

from __future__ import annotations

import importlib
import shutil
import sys
from pathlib import Path

import pytest

import user_operations as uo

# A stub matching CA's real register_user_operations contract: register every
# top-level function defined in this module (the CUFLynx bridge re-exposes user
# ops here with __module__ retargeted so they are picked up).
STUB_OPERATION_FUNCS_USER = '''
def register_user_operations(registry, backend):
    g = globals()
    mod = __name__
    for name, obj in list(g.items()):
        if name.startswith("_") or name == "register_user_operations":
            continue
        if not callable(obj) or isinstance(obj, type):
            continue
        if getattr(obj, "__module__", None) != mod:
            continue
        registry[name] = obj
'''

VALID_SOURCE = (
    "def spread(x, series_output=False):\n"
    "    if series_output:\n"
    "        return x\n"
    "    return float(np.max(x) - np.min(x))\n"
)


@pytest.fixture
def tmp_ca(tmp_path, monkeypatch):
    """A minimal CA layout (<root>/src + <root>/funcs_user with a bridge stub)."""
    root = tmp_path / "ca"
    (root / "src").mkdir(parents=True)
    fu = root / "funcs_user"
    fu.mkdir()
    (fu / "operation_funcs_user.py").write_text(STUB_OPERATION_FUNCS_USER, encoding="utf-8")
    monkeypatch.setattr(uo, "_circulatory_autogen_src", lambda: str(root / "src"))
    # Keep the user modules out of the real import cache during these tests.
    for m in (uo.USER_MODULE, "operation_funcs_user"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    return root


# ---------------------------------------------------------------------------
# module-level: validation
# ---------------------------------------------------------------------------
def test_save_writes_file_and_lists(tmp_ca):
    result = uo.save_user_operation("spread", VALID_SOURCE)
    names = [f["name"] for f in result["functions"]]
    assert names == ["spread"]
    assert result["functions"][0]["source"].startswith("def spread(")

    user_file = tmp_ca / "funcs_user" / uo.USER_FILENAME
    assert user_file.is_file()
    text = user_file.read_text()
    assert 'CUFLYNX_OPERATIONS = ["spread"]' in text
    assert "def spread(" in text


def test_save_appends_bridge_once(tmp_ca):
    bridge = tmp_ca / "funcs_user" / "operation_funcs_user.py"
    uo.save_user_operation("spread", VALID_SOURCE)
    text1 = bridge.read_text()
    assert uo._BRIDGE_BEGIN in text1
    # A second save must not duplicate the bridge block.
    uo.save_user_operation("other", VALID_SOURCE.replace("spread", "other"))
    text2 = bridge.read_text()
    assert text2.count(uo._BRIDGE_BEGIN) == 1


def test_saved_op_is_registered_via_bridge(tmp_ca):
    """The written file + bridge => CA's register_user_operations picks the op up."""
    uo.save_user_operation("spread", VALID_SOURCE)
    fu = str(tmp_ca / "funcs_user")
    monkeypatched_path = fu not in sys.path
    if monkeypatched_path:
        sys.path.insert(0, fu)
    try:
        for m in (uo.USER_MODULE, "operation_funcs_user"):
            sys.modules.pop(m, None)
        ofu = importlib.import_module("operation_funcs_user")
        registry: dict = {}
        ofu.register_user_operations(registry, object())
        assert "spread" in registry
        assert callable(registry["spread"])
    finally:
        for m in (uo.USER_MODULE, "operation_funcs_user"):
            sys.modules.pop(m, None)
        if monkeypatched_path and fu in sys.path:
            sys.path.remove(fu)


def test_edit_updates_in_place(tmp_ca):
    uo.save_user_operation("spread", VALID_SOURCE)
    edited = VALID_SOURCE.replace("np.max(x) - np.min(x)", "np.mean(x)")
    result = uo.save_user_operation("spread", edited)
    assert [f["name"] for f in result["functions"]] == ["spread"]
    assert "np.mean(x)" in result["functions"][0]["source"]
    # The file has exactly one def spread.
    text = (tmp_ca / "funcs_user" / uo.USER_FILENAME).read_text()
    assert text.count("def spread(") == 1


def test_decorators_preserved_across_reads_and_edits(tmp_ca):
    """@differentiable / @series_to_constant survive read-back and a later edit of
    another op (they must not be dropped when the file is regenerated)."""
    decorated = (
        "@differentiable\n"
        "@series_to_constant\n"
        "def amp(x, series_output=False):\n"
        "    if series_output:\n"
        "        return x\n"
        "    return float(np.max(x) - np.min(x))\n"
    )
    uo.save_user_operation("amp", decorated)
    assert "@differentiable" in uo.read_user_operations()["functions"][0]["source"]
    # Adding a second op regenerates the file; amp's decorators must remain.
    uo.save_user_operation("plain", VALID_SOURCE.replace("spread", "plain"))
    text = (tmp_ca / "funcs_user" / uo.USER_FILENAME).read_text()
    assert "@differentiable" in text and "@series_to_constant" in text


def test_multiple_ops_preserve_order(tmp_ca):
    uo.save_user_operation("aaa", VALID_SOURCE.replace("spread", "aaa"))
    uo.save_user_operation("bbb", VALID_SOURCE.replace("spread", "bbb"))
    result = uo.read_user_operations()
    assert [f["name"] for f in result["functions"]] == ["aaa", "bbb"]


def test_delete_removes(tmp_ca):
    uo.save_user_operation("aaa", VALID_SOURCE.replace("spread", "aaa"))
    uo.save_user_operation("bbb", VALID_SOURCE.replace("spread", "bbb"))
    result = uo.delete_user_operation("aaa")
    assert [f["name"] for f in result["functions"]] == ["bbb"]
    with pytest.raises(uo.UserOperationError):
        uo.delete_user_operation("aaa")


@pytest.mark.parametrize("bad", ["1bad", "has space", "def", "", "_hidden", "np"])
def test_invalid_name_rejected(tmp_ca, bad):
    with pytest.raises(uo.UserOperationError):
        uo.save_user_operation(bad, VALID_SOURCE.replace("spread", "ok"))


def test_invalid_syntax_rejected(tmp_ca):
    with pytest.raises(uo.UserOperationError):
        uo.save_user_operation("spread", "def spread(x)\n    return x\n")  # missing ':'


def test_name_must_match_def(tmp_ca):
    with pytest.raises(uo.UserOperationError):
        uo.save_user_operation("spread", VALID_SOURCE.replace("def spread", "def other"))


def test_multiple_top_level_defs_rejected(tmp_ca):
    src = VALID_SOURCE + "\ndef helper(y):\n    return y\n"
    with pytest.raises(uo.UserOperationError):
        uo.save_user_operation("spread", src)


def test_read_available_false_when_ca_unconfigured(monkeypatch):
    monkeypatch.setattr(uo, "_circulatory_autogen_src", lambda: "")
    result = uo.read_user_operations()
    assert result["available"] is False
    assert result["functions"] == []
    assert result["template"]


# ---------------------------------------------------------------------------
# routes
# ---------------------------------------------------------------------------
def test_route_save_list_delete(client, tmp_ca):
    resp = client.get("/api/operation_funcs")
    assert resp.status_code == 200, resp.text
    assert resp.json()["functions"] == []
    assert resp.json()["template"]

    resp = client.post("/api/operation_funcs", json={"name": "spread", "source": VALID_SOURCE})
    assert resp.status_code == 200, resp.text
    assert [f["name"] for f in resp.json()["functions"]] == ["spread"]

    resp = client.get("/api/operation_funcs")
    assert [f["name"] for f in resp.json()["functions"]] == ["spread"]

    resp = client.delete("/api/operation_funcs/spread")
    assert resp.status_code == 200, resp.text
    assert resp.json()["functions"] == []


def test_route_invalid_returns_422(client, tmp_ca):
    resp = client.post("/api/operation_funcs", json={"name": "1bad", "source": VALID_SOURCE})
    assert resp.status_code == 422
    resp = client.post(
        "/api/operation_funcs", json={"name": "spread", "source": "def spread(x)\n return x"}
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
    """End-to-end: a saved op shows up in obs_options against a real CA (copy)."""
    import engine
    import obs_options

    real_src = Path(engine._circulatory_autogen_src())
    if not real_src.is_dir() or not _real_ca_importable():
        pytest.skip("real circulatory_autogen not importable")

    # tmp CA: symlink src (heavy, shared), copy funcs_user (writable, isolated).
    root = tmp_path / "ca"
    root.mkdir()
    (root / "src").symlink_to(real_src, target_is_directory=True)
    shutil.copytree(real_src.parent / "funcs_user", root / "funcs_user")

    monkeypatch.setattr(uo, "_circulatory_autogen_src", lambda: str(root / "src"))
    monkeypatch.setattr(obs_options, "_circulatory_autogen_src", lambda: str(root / "src"))

    purge = (uo.USER_MODULE, "operation_funcs_user", "operation_funcs", "cost_funcs_user")
    saved_modules = {m: sys.modules.get(m) for m in purge}
    saved_path = list(sys.path)
    fu = str(root / "funcs_user")
    try:
        for m in purge:
            sys.modules.pop(m, None)
        sys.path.insert(0, fu)
        uo.save_user_operation("my_spread", VALID_SOURCE.replace("spread", "my_spread"))
        obs_options.reset_cache()
        opts = obs_options.get_obs_data_options(refresh=True)
        assert "my_spread" in opts["operations"]
    finally:
        for m in purge:
            sys.modules.pop(m, None)
            if saved_modules[m] is not None:
                sys.modules[m] = saved_modules[m]
        sys.path[:] = saved_path
        obs_options.reset_cache()
