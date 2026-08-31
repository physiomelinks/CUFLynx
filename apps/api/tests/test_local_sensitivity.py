"""Unit tests for the local-sensitivity AD guard (no CA / casadi needed)."""

import local_sensitivity as ls
import numpy as np
import pytest


def _op(differentiable: bool):
    def fn(*_a, **_k):
        return 0.0

    fn._diff = differentiable
    return fn


def _is_diff(fn):
    return getattr(fn, "_diff", False)


def _resolve(current_params, mode="current"):
    return ls._resolve_nominal(
        _FakePid(), ["a/x", "b/y"], np.array([0.0, 0.0]), np.array([10.0, 10.0]),
        {"nominal": mode}, None, None, current_params=current_params,
    )


class _FakeHelper:
    def get_init_param_vals(self, _names):
        # model built-in initial values for two params
        return [[1.0], [2.0]]


class _FakePid:
    """Stands in for CA's ``ParamID``.

    The local path reads the study from the param-id engine, never from the
    Sobol sampling manager -- both parse the same files and each owns a
    simulation helper, so reading from both compiled the model twice (#216).
    """

    sim_helper = _FakeHelper()
    param_id_info = {"param_names": [["a/x"], ["b/y"]]}


def test_resolve_nominal_current_uses_slider_values(monkeypatch):
    """Regression (#65): nominal='current' local SA must linearise about the user's
    current slider values, not the model's built-in initial values."""
    nominal, source = _resolve({"a/x": 5.0, "b/y": 7.0})
    assert list(nominal) == [5.0, 7.0]
    assert "sliders" in source


def test_resolve_nominal_current_falls_back_to_model_defaults():
    """With no slider values supplied, fall back to the model's initial values."""
    nominal, source = _resolve(None)
    assert list(nominal) == [1.0, 2.0]
    assert "model defaults" in source


def test_resolve_nominal_current_partial_override():
    """A param missing from the slider map keeps its model-default init value."""
    nominal, source = _resolve({"a/x": 5.0})  # only a/x provided
    assert list(nominal) == [5.0, 2.0]
    assert "sliders" in source


# ---------------------------------------------------------------------------
# operation_kwargs actually reach the operation call (#112/#113)
# ---------------------------------------------------------------------------
class _FakeExecutor:
    """Stands in for the protocol executor: returns canned operand outputs."""

    def __init__(self, outputs):
        self.outputs = outputs

    def run_protocol(self, _protocol_info, **_kwargs):
        return True, self.outputs, None, None


class _FakeFeatureSM:
    def __init__(self, obs_info, outputs):
        self.obs_info = obs_info
        self.protocol_info = {}
        self.param_id_info = {"param_names": ["p"]}
        self._protocol_executor = _FakeExecutor(outputs)


def _feature_sm(kwargs_list, n=1, names=None):
    obs = {
        "operations": ["scaled_max"] * n,
        "operands": [["m/x"]] * n,
        "experiment_idxs": [0] * n,
        "subexperiment_idxs": [0] * n,
        "operation_kwargs": kwargs_list,
        "item_names_for_plotting": names or [f"f{i}" for i in range(n)],
    }
    # operands_outputs[j] is the operand tuple for observable j.
    outputs = {(0, 0): [(np.array([1.0, 3.0, 2.0]),)] * n}
    return _FakeFeatureSM(obs, outputs)


def _scaled_max(x, factor=1.0):
    return float(np.max(x) * factor)


def test_local_ad_is_offered_only_where_this_path_implements_it():
    from local_sensitivity import local_gradient_sources

    sources = [
        {"value": "FD", "label": "Finite difference"},
        {"value": "AD", "label": "Automatic differentiation (AADC)"},
    ]
    offered = {s["value"]: s for s in local_gradient_sources(sources, "aadc_python")}
    assert offered["FD"]["disabled_here"] is False
    assert offered["AD"]["disabled_here"] is True
    # Says why, and names the format that would work.
    assert "casadi_python" in offered["AD"]["reason"]


def test_local_ad_is_offered_for_casadi():
    from local_sensitivity import local_gradient_sources

    sources = [{"value": "AD", "label": "Automatic differentiation (CasADi)"}]
    assert local_gradient_sources(sources, "casadi_python")[0]["disabled_here"] is False


def test_fd_works_on_every_backend():
    """It only runs forward simulations, so nothing gates it."""
    from local_sensitivity import local_gradient_sources

    for fmt in ("aadc_python", "cellml", "python", "casadi_python"):
        got = local_gradient_sources([{"value": "FD", "label": "FD"}], fmt)
        assert got[0]["disabled_here"] is False


def test_an_unsupported_source_is_marked_not_dropped():
    """Dropping it would read as "this backend has no AD at all", which is wrong
    -- calibration can use AADC's."""
    from local_sensitivity import local_gradient_sources

    got = local_gradient_sources([{"value": "AD", "label": "AD"}], "aadc_python")
    assert len(got) == 1 and got[0]["value"] == "AD"


def test_the_refusal_message_explains_the_distinction():
    """The old message said AD "requires casadi_python", implying AADC has no AD."""
    import local_sensitivity as ls

    assert ls.LOCAL_GRADIENT_SUPPORT["AD"] == ("casadi_python",)
    assert ls.LOCAL_GRADIENT_SUPPORT["FD"] is None


@pytest.mark.parametrize("spelling", ["AUTO", "auto", "ANALYTIC", ""])
@pytest.mark.parametrize(
    "model_type,expected", [("casadi_python", "AD"), ("cellml", "FSA")]
)
def test_cas_own_spelling_resolves_to_this_backends_arm(spelling, model_type, expected):
    """CA's schema defaults gradient_method to 'AUTO', and its own API takes
    ''/'ANALYTIC'/'AUTO' to mean "this backend's analytic arm". CUFLynx names the
    arm so the menu can offer and disable it -- but rejecting CA's word for the
    same choice made a defaulted run fail with "gradient_method 'AUTO' is not
    available", which was true of the name and wrong about the capability.
    """
    import local_sensitivity as ls

    settings = {"gradient_method": spelling, "nominal": "current"}
    resolved = ls.resolve_gradient_method(settings, model_type)

    assert resolved == expected


def test_a_genuinely_unknown_gradient_method_is_still_rejected():
    """Accepting CA's spellings must not turn the check into a rubber stamp."""
    import local_sensitivity as ls

    with pytest.raises(NotImplementedError, match="not available"):
        ls.resolve_gradient_method({"gradient_method": "MAGIC"}, "cellml")


# ---------------------------------------------------------------------------
# CA's model_type spelling arrives from the run config
# ---------------------------------------------------------------------------
# main.py writes ``"model_type": ca_model_type(engine.model_type)`` into all four
# run configs on purpose: a CA that predates the ``cellml_only`` -> ``cellml``
# rename exits on a model_type it does not recognise, so the config has to speak
# its language. The runners then read that value straight back and hand it here,
# where LOCAL_GRADIENT_SUPPORT is keyed by CUFLynx's canonical spelling -- so the
# old word matched nothing and every gradient source was refused.
#
# The failure is #122 exactly, arriving by a different route: /api/settings builds
# the *menu* from the canonical ``engine.model_type``, so the UI offered FSA and
# the run then raised NotImplementedError.
CA_SPELLING = "cellml_only"


def test_cas_model_type_spelling_does_not_disable_fsa():
    import local_sensitivity as ls

    assert ls.resolve_gradient_method({"gradient_method": "FSA"}, CA_SPELLING) == "FSA"


def test_an_fsa_run_from_a_ca_spelled_config_is_not_refused():
    """The failure itself: the run reaches ``compute_local_sensitivity`` with the
    config's ``cellml_only`` and its FSA guard rejects the very method
    ``resolve_gradient_method`` had just chosen.

    Getting past the guard is the assertion; the run then stops on the missing
    engine, which is a different (and correct) complaint.
    """
    import local_sensitivity as ls

    with pytest.raises(RuntimeError, match="param-id engine"):
        ls.compute_local_sensitivity(
            sa=None,
            settings={"gradient_method": "FSA"},
            model_type=CA_SPELLING,
            engine=None,
        )


def test_cas_model_type_spelling_still_resolves_auto_to_fsa():
    """The default a run config carries. It resolved to FSA either way, but with
    ``cellml_only`` the very next check refused the FSA it had just chosen."""
    import local_sensitivity as ls

    assert ls.resolve_gradient_method({"gradient_method": "AUTO"}, CA_SPELLING) == "FSA"


def test_cas_model_type_spelling_leaves_the_menu_intact():
    import local_sensitivity as ls

    sources = [{"value": "FD", "label": "FD"}, {"value": "FSA", "label": "FSA"}]
    offered = {s["value"]: s for s in ls.local_gradient_sources(sources, CA_SPELLING)}

    assert offered["FSA"]["disabled_here"] is False
    assert offered["FD"]["disabled_here"] is False


def test_cas_model_type_spelling_does_not_silently_enable_the_wrong_arm():
    """Canonicalising must not become "accept anything": a format whose AD this
    path really cannot do is still refused."""
    import local_sensitivity as ls

    offered = ls.local_gradient_sources([{"value": "AD", "label": "AD"}], CA_SPELLING)
    assert offered[0]["disabled_here"] is True
    assert "casadi_python" in offered[0]["reason"]


def test_calibrations_do_ad_is_keyed_off_the_same_canonical_answer():
    """``calibration_runner`` reads ``do_ad`` from this same call with the same
    config value, so it shares the fix by construction.

    It is worth pinning even though today's single alias (``cellml_only`` ->
    ``cellml``) happens to land on the same 'AUTO' answer either way -- neither
    spelling is in ``LOCAL_GRADIENT_SUPPORT["AD"]``, so both resolve to FSA. The
    next renamed format need not be so forgiving, and the call is the same one
    that *did* mis-answer the sensitivity path.
    """
    import local_sensitivity as ls

    for settings in ({"gradient_method": "AUTO"}, {"gradient_method": "FSA"}):
        do_ad = ls.resolve_gradient_method(settings, CA_SPELLING) in ("AD", "FSA")
        assert do_ad is True
        assert ls.resolve_gradient_method(settings, CA_SPELLING) == (
            ls.resolve_gradient_method(settings, "cellml")
        )


# ---------------------------------------------------------------------------
# The theta / anchor contract for modifier parameters (#208)
# ---------------------------------------------------------------------------
class _ModifierHelper:
    def get_init_param_vals(self, _names):
        # The anchor's *physical* model default -- wrong as a theta, which is
        # the point of the identity overwrite.
        return [[2e-8]]


class _ModifierPid:
    sim_helper = _ModifierHelper()
    # A modifier's param_names entry IS its modifies list, and the engine carries
    # the matching modifiers metadata beside it.
    param_id_info = {
        "param_names": [["m/p", "m/q"]],
        "modifiers": [
            {"index": 0, "name": "s", "operation": "scale",
             "targets": ["m/p", "m/q"], "baselines": None},
        ],
    }


def _resolve_modifier(current_params, requires_ca=True):
    return ls._resolve_nominal(
        _ModifierPid(), ["m/p"], np.array([0.5]), np.array([2.0]),
        {"nominal": "current"}, None, None, current_params=current_params,
    )


def test_a_modifier_slot_takes_theta_from_its_anchor(requires_params_csv):
    """The contract: analysisDict puts theta at modifies[0], and the nominal
    resolver matches current_params by param_names[i][0] -- which for a modifier
    IS modifies[0]. Theta lands in the modifier's slot with no name mapping."""
    nominal, source = _resolve_modifier({"m/p": 1.4})
    assert list(nominal) == [1.4]
    assert "sliders" in source


def test_a_modifier_slot_defaults_to_identity_not_a_model_value(requires_params_csv):
    """Without a slider override the slot must be the operation's identity
    (theta=1: every target at its baseline), never the anchor's physical
    default -- 2e-8 as a theta would collapse every target toward zero."""
    nominal, source = _resolve_modifier(None)
    assert list(nominal) == [1.0]
    assert "model defaults" in source


