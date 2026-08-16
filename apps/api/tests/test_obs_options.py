"""Tests for GET /api/obs_data/options (operation/cost_type names from CA)."""

from conftest import set_ca_module


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
    assert opts["differentiable_operations"] == {}
    assert opts["operation_kwargs_schema"] == {}
    assert opts["cost_kwargs_schema"] == {}
    assert opts["cost_kwargs_accepts_any"] == {}
    assert opts["data_types"] == obs_options.FALLBACK_DATA_TYPES
    assert opts["plot_types"] == obs_options.FALLBACK_PLOT_TYPES
    obs_options.reset_cache()


def test_operation_differentiability_introspected(monkeypatch):
    """Each operation is mapped to whether it's @differentiable, so the obs editor
    can flag data_items whose operation blocks AD gradients."""
    import types

    import obs_options

    op_funcs = {"max": object(), "calc_spike_period": object()}
    calls = {}

    def fake_is_diff(fn):
        calls.setdefault("seen", []).append(fn)
        return fn is op_funcs["max"]

    fake_mod = types.ModuleType("param_id.differentiable")
    fake_mod.is_circulatory_differentiable = fake_is_diff
    # Registered under both spellings: a fake named only flat would lose to the
    # real module the moment the configured CA is a namespaced checkout.
    set_ca_module(monkeypatch, "param_id.differentiable", fake_mod)

    out = obs_options._introspect_operation_differentiability(op_funcs)

    assert out == {"max": True, "calc_spike_period": False}


def test_operation_differentiability_empty_on_older_ca(monkeypatch):
    """An older CA without is_circulatory_differentiable yields {} (no false
    'not differentiable' warnings in the editor)."""
    import sys
    import obs_options

    # Ensure the import fails deterministically.
    set_ca_module(monkeypatch, "param_id.differentiable", None)
    assert obs_options._introspect_operation_differentiability({"max": object()}) == {}


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


def test_operation_kwargs_schema_parses_signature():
    """An operation's keyword args are surfaced (name/default/inferred type),
    excluding the positional operand(s) and the reserved series_output flag."""
    import obs_options

    def peak_above(x, threshold=0.5, window=10, invert=False, label="p", series_output=False):
        return x

    def addition(x1, x2):  # operands only, no tunable kwargs
        return x1 + x2

    schema = obs_options._introspect_operation_kwargs(
        {"peak_above": peak_above, "addition": addition, "max": max}
    )
    # addition/max have no tunable kwargs -> omitted entirely.
    assert "addition" not in schema
    assert "max" not in schema
    kwargs = schema["peak_above"]
    # operand `x` and reserved `series_output` are excluded; the rest surface in order.
    assert [k["name"] for k in kwargs] == ["threshold", "window", "invert", "label"]
    by_name = {k["name"]: k for k in kwargs}
    assert by_name["threshold"] == {"name": "threshold", "default": 0.5, "type": "number"}
    assert by_name["window"] == {"name": "window", "default": 10, "type": "integer"}
    assert by_name["invert"] == {"name": "invert", "default": False, "type": "boolean"}
    assert by_name["label"] == {"name": "label", "default": "p", "type": "string"}


def test_operation_kwargs_schema_handles_uninspectable_and_varargs():
    """Callables without a usable signature are skipped (not fatal); *args/**kwargs
    are ignored, and a None default falls back to a free-text string input."""
    import obs_options

    def with_star(x, *args, scale=None, **kwargs):
        return x

    schema = obs_options._introspect_operation_kwargs({"with_star": with_star})
    assert schema["with_star"] == [{"name": "scale", "default": None, "type": "string"}]


def test_operation_kwargs_schema_exposed_via_endpoint(client):
    """GET /api/obs_data/options carries the operation_kwargs_schema map (a dict,
    present whether sourced from CA or the fallback)."""
    body = client.get("/api/obs_data/options").json()
    assert isinstance(body["operation_kwargs_schema"], dict)


# ---------------------------------------------------------------------------
# cost_kwargs (CA #370 / issue #201): a data_item may set the cost func's own
# keyword arguments, so the editor has to know which ones a cost accepts.
# ---------------------------------------------------------------------------
def test_cost_kwargs_schema_excludes_the_framework_arguments():
    """`std` and `weight` come from the data_item's own fields, and the model
    output / ground truth are filled positionally. None of them is a cost_kwarg,
    so none may be offered as an editable input."""
    import obs_options

    def tolerant(output, desired_mean, std, weight, tolerance=0.5, mode="abs"):
        return 0.0

    schema, accepts_any = obs_options._introspect_cost_kwargs({"tolerant": tolerant})
    assert [k["name"] for k in schema["tolerant"]] == ["tolerance", "mode"]
    assert schema["tolerant"][0] == {"name": "tolerance", "default": 0.5, "type": "number"}
    assert schema["tolerant"][1] == {"name": "mode", "default": "abs", "type": "string"}
    assert accepts_any == {"tolerant": False}


def test_cost_kwargs_schema_omits_a_cost_with_no_tunables():
    """CA's own costs have none, and an empty list per func would be noise."""
    import obs_options

    def gaussian_MLE(output, desired_mean, std, weight):
        return 0.0

    schema, accepts_any = obs_options._introspect_cost_kwargs({"gaussian_MLE": gaussian_MLE})
    assert schema == {}
    # ... but it is still *reported on*: "declares no kwargs" and "never
    # introspected" must not look the same, or the editor cannot tell whether a
    # stored kwarg is invalid or merely unknown.
    assert accepts_any == {"gaussian_MLE": False}


def test_a_cost_that_takes_star_kwargs_is_marked_accepts_any():
    """CA validates nothing for such a func (MSE is one), so the editor must not
    delete a stored kwarg just because it isn't in the schema."""
    import obs_options

    def MSE(*args, **kwargs):
        return 0.0

    schema, accepts_any = obs_options._introspect_cost_kwargs({"MSE": MSE})
    assert schema == {}
    assert accepts_any == {"MSE": True}


def test_cost_kwargs_reserved_names_come_from_ca(monkeypatch):
    """The reserved set is CA's -- it is what CA *rejects* in a data_item's
    cost_kwargs, so a local copy that drifted would offer an input CA refuses."""
    import obs_options

    assert obs_options._reserved_cost_kwargs() >= {"std", "weight"}
    # A cost func that gives std/weight defaults still must not surface them.
    def defaulted(output, desired_mean, std=1.0, weight=1.0, scale=2.0):
        return 0.0

    schema, _ = obs_options._introspect_cost_kwargs({"defaulted": defaulted})
    assert [k["name"] for k in schema["defaulted"]] == ["scale"]


def test_cost_kwargs_schema_survives_a_ca_without_the_contract(monkeypatch):
    """Pre-#370 CA has no param_id.cost_kwargs; CUFLynx parses the signature
    itself rather than losing the feature (and the editor keeps working)."""
    import sys
    import obs_options

    set_ca_module(monkeypatch, "param_id.cost_kwargs", None)

    def tolerant(output, desired_mean, std, weight, tolerance=0.5):
        return 0.0

    schema, accepts_any = obs_options._introspect_cost_kwargs({"tolerant": tolerant})
    assert [k["name"] for k in schema["tolerant"]] == ["tolerance"]
    assert accepts_any == {"tolerant": False}


def test_cost_kwargs_exposed_via_endpoint(client):
    """GET /api/obs_data/options carries both maps, from CA or the fallback."""
    body = client.get("/api/obs_data/options").json()
    assert isinstance(body["cost_kwargs_schema"], dict)
    assert isinstance(body["cost_kwargs_accepts_any"], dict)
    # Whatever the schema names must be a cost the editor can select.
    assert set(body["cost_kwargs_schema"]) <= set(body["cost_types"])


# Issue #147: the editor should offer as many operand fields as the operation
# consumes, rather than leaving the user to add them by hand and guess.
def test_operation_operands_reports_the_arity_of_each_operation(client):
    opts = client.get("/api/obs_data/options").json()
    spec = opts["operation_operands"]
    assert spec["max"]["count"] == 1
    assert spec["addition"]["count"] == 2
    assert spec["division"]["count"] == 2
    # The parameter names come with it, so a two-operand row can say which is which.
    assert spec["addition"]["names"] == ["x1", "x2"]


def test_operation_operands_excludes_the_tunable_kwargs(client):
    """Only the parameters CA fills from `operands` count; a keyword with a
    default is a setting, not an operand."""
    opts = client.get("/api/obs_data/options").json()
    spec = opts["operation_operands"]
    # `series_output` has a default and is reserved machinery, not an operand.
    assert "series_output" not in spec["max"]["names"]


def test_operation_operands_marks_a_variadic_operation(client):
    """A `*args` / `**kwargs` operation has no fixed count, so the editor must
    keep letting the user manage the fields by hand."""
    opts = client.get("/api/obs_data/options").json()
    for entry in opts["operation_operands"].values():
        assert isinstance(entry["variadic"], bool)
        assert entry["count"] == len(entry["names"])


def test_the_fallback_covers_every_operation_it_offers(client, monkeypatch):
    """CI and a CA-less install take the fallback path, where the operand counts
    must still be there -- otherwise the editor silently loses the feature.

    Also pins the two lists together: an operation added to FALLBACK_OPERATIONS
    without its arity would leave that row hand-managed for no visible reason.
    """
    import obs_options as oo

    monkeypatch.setattr(oo, "_introspect", lambda *a, **k: (_ for _ in ()).throw(ImportError))
    oo.reset_cache()
    try:
        opts = client.get("/api/obs_data/options").json()
        spec = opts["operation_operands"]
        assert spec["max"]["count"] == 1
        assert spec["division"]["count"] == 2
        offered = {op for op in opts["operations"] if op}
        assert offered == set(spec), "FALLBACK_OPERATIONS and its arities have drifted"
    finally:
        oo.reset_cache()


# ---------------------------------------------------------------------------
# The default cost_type (issue #212)
# ---------------------------------------------------------------------------
def test_the_default_cost_type_comes_from_ca(requires_ca):
    """Naming the default in the editor is only honest if CA said it.

    CA published it as obs_data_helpers.DEFAULT_COST_TYPE in #392; before that
    it was a literal in two places in PrimitiveParsers, which is how CA ended up
    with three different answers (parser, OMEX importer, Bayesian path). This
    asserts we read CA's, not a fourth copy.
    """
    import obs_options as oo
    from ca_imports import ca_from

    DEFAULT_COST_TYPE = ca_from("utilities.obs_data_helpers", "DEFAULT_COST_TYPE")

    oo.reset_cache()
    try:
        assert oo.get_obs_data_options()["default_cost_type"] == DEFAULT_COST_TYPE
    finally:
        oo.reset_cache()


def test_an_older_ca_reports_no_default_rather_than_guessing(monkeypatch):
    """The editor then says plain "default" -- naming the wrong cost function
    would be worse than naming none."""
    import obs_options as oo

    assert oo._introspect_default_cost_type.__doc__  # documented, not incidental
    # Both spellings blocked, or "older CA" would only be simulated for whichever
    # layout the developer's CA directory happens to be in.
    set_ca_module(monkeypatch, "utilities.obs_data_helpers", None)

    assert oo._introspect_default_cost_type() == ""
