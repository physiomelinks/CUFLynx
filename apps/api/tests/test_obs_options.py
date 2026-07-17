"""Tests for GET /api/obs_data/options (operation/cost_type names from CA)."""


def test_obs_data_options_returns_lists(client):
    resp = client.get("/api/obs_data/options")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    for key in ("operations", "cost_types", "data_types", "plot_types"):
        assert isinstance(body[key], list) and body[key], key
    # Present whether sourced from CA or the hardcoded fallback.
    assert "max" in body["operations"]
    assert "MSE" in body["cost_types"]
    assert "constant" in body["data_types"] and "series" in body["data_types"]
    assert "horizontal" in body["plot_types"]


def test_obs_data_options_fallback_when_ca_unavailable(monkeypatch):
    import obs_options

    def _boom():
        raise ImportError("no circulatory_autogen")

    obs_options.reset_cache()
    monkeypatch.setattr(obs_options, "_introspect", _boom)
    opts = obs_options.get_obs_data_options(refresh=True)
    assert opts["operations"] == obs_options.FALLBACK_OPERATIONS
    assert opts["cost_types"] == obs_options.FALLBACK_COST_TYPES
    assert opts["cost_func_metadata"] == {}
    assert opts["data_types"] == obs_options.FALLBACK_DATA_TYPES
    assert opts["plot_types"] == obs_options.FALLBACK_PLOT_TYPES
    obs_options.reset_cache()


def test_cost_func_metadata_introspected_and_normalised():
    """CA's cost_func_metadata() flags are surfaced (coerced to bools with defaults)
    so the obs editor can label cost types."""
    import types
    import obs_options

    fake = types.SimpleNamespace(cost_func_metadata=lambda: {
        "gaussian_MLE": {"is_MLE": True, "differentiable": True},
        "additive": {"is_combiner": True},
    })
    meta = obs_options._introspect_cost_func_metadata(fake)
    assert meta["gaussian_MLE"] == {"is_MLE": True, "is_combiner": False, "differentiable": True}
    assert meta["additive"] == {"is_MLE": False, "is_combiner": True, "differentiable": False}


def test_cost_func_metadata_empty_on_older_ca():
    """An older CA without cost_func_metadata() yields {} (plain cost_types still work)."""
    import types
    import obs_options

    def _boom():
        raise AttributeError("no cost_func_metadata")

    fake = types.SimpleNamespace(cost_func_metadata=_boom)
    assert obs_options._introspect_cost_func_metadata(fake) == {}
