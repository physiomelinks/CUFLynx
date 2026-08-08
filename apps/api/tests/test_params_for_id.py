import numpy as np
import pytest

from conftest import BG_MODEL_PATH, LV_MODEL_PATH, LV_PARAMS_CSV_PATH, upload_model


def _post_csv_file(client, path, model_id=None):
    url = "/api/params_for_id/upload"
    if model_id:
        url += f"?model_id={model_id}"
    with open(path, "rb") as fh:
        return client.post(url, files={"file": (path.name, fh, "text/csv")})


def _post_csv_text(client, text, model_id=None):
    url = "/api/params_for_id/upload"
    if model_id:
        url += f"?model_id={model_id}"
    return client.post(url, content=text, headers={"content-type": "text/csv"})


# ---------------------------------------------------------------------------
# Unit tier
# ---------------------------------------------------------------------------
def test_upload_lv_csv_returns_four_params(client):
    resp = _post_csv_file(client, LV_PARAMS_CSV_PATH)
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["params"]) == 4


def test_lv_qnames_correctly_formed(client):
    resp = _post_csv_file(client, LV_PARAMS_CSV_PATH)
    qnames = {p["qname"] for p in resp.json()["params"]}
    assert qnames == {
        "Lotka_Volterra_module/alpha",
        "Lotka_Volterra_module/beta",
        "Lotka_Volterra_module/delta",
        "Lotka_Volterra_module/gamma",
    }


def test_multi_vessel_name_is_one_parameter_naming_every_vessel(client):
    """A whitespace-split vessel_name is ONE parameter that varies in several
    components at once (issue #193) -- CA gives such a row a single entry in
    ``param_id_info['param_names']`` and a single value in the optimiser. It used
    to become one slider per vessel, which let the user move one and not the
    others and put the model in a state it never has."""
    csv = "vessel_name, param_name, min, max\n" "aortic_root venous_root, C, 1, 2\n"
    resp = _post_csv_text(client, csv)
    assert resp.status_code == 200, resp.text
    params = resp.json()["params"]
    assert len(params) == 1
    assert params[0]["qnames"] == ["aortic_root/C", "venous_root/C"]
    # The representative is the first member, so every existing qname lookup
    # (calibration write-back, saved-run markers) still finds the parameter.
    assert params[0]["qname"] == "aortic_root/C"


def test_a_single_vessel_row_still_lists_itself(client):
    """`qnames` is the truth for every row, so consumers never special-case the
    grouped form."""
    resp = _post_csv_text(client, "vessel_name,param_name,min,max\nmain,alpha_o2,1,2\n")
    assert resp.json()["params"][0]["qnames"] == ["main/alpha_o2"]


def test_missing_required_column_returns_422(client):
    csv = "vessel_name, param_name, max\nmain, alpha_o2, 0.05\n"
    resp = _post_csv_text(client, csv)
    assert resp.status_code == 422


def test_min_greater_than_max_returns_422(client):
    csv = "vessel_name, param_name, min, max\nmain, alpha_o2, 0.05, 0.005\n"
    resp = _post_csv_text(client, csv)
    assert resp.status_code == 422


def test_edit_dialog_csv_format_round_trips(client):
    # Exact format the frontend Edit dialog emits (buildParamsCsv): no spaces
    # after commas, optional param_type column. Guards against format drift.
    csv = (
        "vessel_name,param_name,min,max,name_for_plotting,param_type\n"
        "Lotka_Volterra_module,alpha,0.09,0.11,\\alpha,global\n"
    )
    resp = _post_csv_text(client, csv)
    assert resp.status_code == 200, resp.text
    (p,) = resp.json()["params"]
    assert p["qname"] == "Lotka_Volterra_module/alpha"
    assert p["min"] == 0.09 and p["max"] == 0.11
    assert p["name_for_plotting"] == "\\alpha"
    assert p["param_type"] == "global"


def test_comment_annotation_round_trips(client):
    # Free-text annotation column (issue #25): parsed back into each entry, and
    # blank cells stay None rather than becoming the string "nan".
    csv = (
        "vessel_name,param_name,min,max,name_for_plotting,comment\n"
        "Lotka_Volterra_module,alpha,0.1,7,\\alpha,\"range from Dash 2016, tentative\"\n"
        "Lotka_Volterra_module,beta,0.01,2,\\beta,\n"
    )
    resp = _post_csv_text(client, csv)
    assert resp.status_code == 200, resp.text
    by_qname = {p["qname"]: p for p in resp.json()["params"]}
    assert by_qname["Lotka_Volterra_module/alpha"]["comment"] == "range from Dash 2016, tentative"
    assert by_qname["Lotka_Volterra_module/beta"]["comment"] is None


def test_comment_absent_when_column_missing(client):
    resp = _post_csv_file(client, LV_PARAMS_CSV_PATH)
    assert resp.status_code == 200, resp.text
    assert all(p["comment"] is None for p in resp.json()["params"])


def test_initial_value_from_model_default(client):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    resp = _post_csv_file(client, LV_PARAMS_CSV_PATH, model_id=model_id)
    by_qname = {p["qname"]: p for p in resp.json()["params"]}
    # Lotka_Volterra_forced declares alpha initial_value="5".
    assert by_qname["Lotka_Volterra_module/alpha"]["initial_value"] == 5.0


# ---------------------------------------------------------------------------
# Integration tier
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_simulate_lv_at_alpha_min_and_max(client, requires_simulation):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]

    def max_x(alpha):
        resp = client.post(
            "/api/simulate",
            json={
                "model_id": model_id,
                "params": {"Lotka_Volterra_module/alpha": alpha},
                "sim_time": 5,
                "outputs": ["Lotka_Volterra_module/x"],
            },
        )
        assert resp.status_code == 200, resp.text
        return max(resp.json()["outputs"]["Lotka_Volterra_module/x"])

    low, high = max_x(0.1), max_x(7.0)
    assert abs(high - low) / abs(low) > 0.10


@pytest.mark.integration
def test_simulate_bg_model_alpha_o2_slider(client, requires_simulation):
    model_id = upload_model(client, BG_MODEL_PATH)["model_id"]
    csv = "vessel_name, param_name, min, max\nmain, alpha_o2, 0.005, 0.05\n"
    resp = _post_csv_text(client, csv, model_id=model_id)
    assert resp.status_code == 200, resp.text

    def mean_c_o2(alpha_o2):
        r = client.post(
            "/api/simulate",
            json={
                "model_id": model_id,
                "params": {"main/alpha_o2": alpha_o2},
                "sim_time": 20,
                "pre_time": 0,
                "outputs": ["main/c_o2"],
            },
        )
        assert r.status_code == 200, r.text
        return float(np.mean(r.json()["outputs"]["main/c_o2"]))

    assert mean_c_o2(0.005) != pytest.approx(mean_c_o2(0.05), rel=1e-3)


# ---------------------------------------------------------------------------
# Flat-model initial-value resolution (issue #114, DEFCON 1)
# ---------------------------------------------------------------------------
def test_flat_model_initial_values_resolve_via_gen_name():
    """A circulatory_autogen *flat* model renames constants (e.g. params_for_id
    `aortic_root/C` -> the model's `parameters/C_aortic_root`), so the direct
    `vessel/param` qname isn't in the model. The slider initial_value must still be
    the model's real value, not None (which the UI would replace with the range
    midpoint -> wrong sim, issue #114)."""
    from cellml_meta import parse_cellml
    from params_for_id import parse_params_for_id
    from conftest import RESOURCES_DIR

    meta = parse_cellml((RESOURCES_DIR / "3compartment_flat.cellml").read_bytes())
    csv = (RESOURCES_DIR / "3compartment_params_for_id.csv").read_bytes()
    entries = {e.qname: e.initial_value for e in parse_params_for_id(csv, meta.initial_values)}

    # global vessel -> bare gen name; other vessels -> param_vessel; both live in
    # the flat model's parameters* components.
    assert entries["global/q_lv_init"] == pytest.approx(0.00071536680911)
    assert entries["aortic_root/C"] == pytest.approx(1.674986287e-08)
    assert entries["global/E_lv_A"] == pytest.approx(248523797.83)
    assert entries["global/E_lv_B"] == pytest.approx(10268533.558)
    # None of them fell through to the "no value" case.
    assert all(v is not None for v in entries.values())


def test_direct_qname_still_wins_over_gen_name():
    """Non-flat models (Lotka-Volterra) name the constant `vessel/param` directly;
    that must resolve without the flat-model fallback kicking in."""
    from cellml_meta import parse_cellml
    from params_for_id import parse_params_for_id
    from conftest import RESOURCES_DIR

    meta = parse_cellml(LV_MODEL_PATH.read_bytes())
    csv = LV_PARAMS_CSV_PATH.read_bytes()
    entries = {e.qname: e.initial_value for e in parse_params_for_id(csv, meta.initial_values)}
    assert entries["Lotka_Volterra_module/alpha"] == pytest.approx(5.0)
    assert entries["Lotka_Volterra_module/beta"] == pytest.approx(0.2)


def test_gen_name_fallback_skips_ambiguous_bare_names():
    """If a bare gen name maps to multiple non-parameters components (a real clash),
    resolution returns None rather than guessing a wrong value."""
    from params_for_id import _resolve_initial_value, _build_gen_index

    initial = {"aortic_root_module/v": 0.0, "pvn_module/v": 1.0}  # bare 'v' clashes
    idx = _build_gen_index(initial)
    # vessel 'module' + param 'v'? gen name 'v_module' -> not present; but test the
    # ambiguous bare-name path directly with a gen name that clashes.
    assert _resolve_initial_value("global", "v", initial, idx) is None


def test_gen_name_fallback_prefers_parameters_component():
    """When the bare name clashes but exactly one hit is in a parameters* component,
    that one wins (the flat model's canonical source of the value)."""
    from params_for_id import _resolve_initial_value, _build_gen_index

    initial = {"parameters/R_x": 42.0, "some_module/R_x": 7.0}
    idx = _build_gen_index(initial)
    assert _resolve_initial_value("x", "R", initial, idx) == 42.0


# ---------------------------------------------------------------------------
# Grouped parameters (issue #193): one row naming several vessels is one
# parameter, so it needs one initial value -- and the components had better agree
# on it.
# ---------------------------------------------------------------------------
def _flat_3compartment_values():
    from cellml_meta import parse_cellml
    from conftest import RESOURCES_DIR

    return parse_cellml((RESOURCES_DIR / "3compartment_flat.cellml").read_bytes()).initial_values


def test_a_group_whose_components_agree_takes_their_value_without_complaint():
    """`I` is 1e-6 in both pvn and par, which is what a grouped row asserts, so
    the slider starts there and nothing is reported."""
    from params_for_id import parse_params_for_id

    entries = parse_params_for_id(
        "vessel_name,param_name,min,max\npvn par,I,1e-7,1e-5\n", _flat_3compartment_values()
    )
    assert entries[0].initial_value == pytest.approx(1e-6)
    assert entries[0].warning is None


def test_a_group_whose_components_disagree_says_so():
    """`I` is 1e-6 in pvn but 1e4 in aortic_root: the row claims one quantity that
    the model does not currently hold, and touching the slider overwrites the
    evidence -- so the first member's value is used and the disagreement named."""
    from params_for_id import parse_params_for_id

    entries = parse_params_for_id(
        "vessel_name,param_name,min,max\npvn aortic_root,I,1e-7,1e5\n",
        _flat_3compartment_values(),
    )
    assert entries[0].initial_value == pytest.approx(1e-6)
    warning = entries[0].warning
    assert warning and "pvn/I" in warning and "aortic_root/I" in warning


def test_the_group_warning_reaches_the_api_payload(client):
    """The slider is where the user sees it, and the slider is built from this."""
    from conftest import RESOURCES_DIR

    model_id = upload_model(client, RESOURCES_DIR / "3compartment_flat.cellml")["model_id"]
    resp = _post_csv_text(
        client,
        "vessel_name,param_name,min,max\npvn aortic_root,I,1e-7,1e5\n",
        model_id=model_id,
    )
    assert resp.status_code == 200, resp.text
    assert "aortic_root/I" in (resp.json()["params"][0]["warning"] or "")


@pytest.mark.integration
def test_a_grouped_parameter_reaches_every_component_it_names(client, requires_simulation):
    """End to end: the one value the UI reads off the one handle must arrive at
    all of the group's components in the solver.

    Asserted by contrast rather than by inspecting the payload -- setting both
    resistances must produce a different trace from setting only the first, which
    is exactly the difference a per-vessel slider could not express."""
    from conftest import RESOURCES_DIR

    model_id = upload_model(client, RESOURCES_DIR / "3compartment_flat.cellml")["model_id"]
    resp = _post_csv_text(
        client,
        "vessel_name,param_name,min,max\npar venous_svc,R,1e5,1e7\n",
        model_id=model_id,
    )
    assert resp.status_code == 200, resp.text
    qnames = resp.json()["params"][0]["qnames"]
    assert qnames == ["par/R", "venous_svc/R"]

    def run(params):
        r = client.post(
            "/api/simulate",
            json={
                "model_id": model_id,
                "params": params,
                "sim_time": 1.0,
                "outputs": ["aortic_root/u"],
            },
        )
        assert r.status_code == 200, r.text
        return np.array(r.json()["outputs"]["aortic_root/u"])

    # Order matters: the engine caches the compiled helper and a constant it was
    # given stays set, so the run that leaves the second member alone has to come
    # before the one that sets it.
    first_only = run({qnames[0]: 5e6})
    # What one slider at 5e6 sends: the same value under every member qname.
    grouped = run({q: 5e6 for q in qnames})
    assert grouped.size and np.all(np.isfinite(grouped))
    assert not np.allclose(grouped, first_only)


def test_a_member_the_model_lacks_is_not_a_disagreement():
    """An unresolved member is the pre-existing "no such variable" case; calling it
    a conflict would put a warning on every params_for_id written against a model
    that was since trimmed."""
    from params_for_id import parse_params_for_id

    entries = parse_params_for_id(
        "vessel_name,param_name,min,max\npvn nowhere,I,1e-7,1e-5\n",
        _flat_3compartment_values(),
    )
    assert entries[0].initial_value == pytest.approx(1e-6)
    assert entries[0].warning is None


# ---------------------------------------------------------------------------
# The `prior` column (MCMC / UQ priors)
#
# CA reads a missing prior as `uniform`, so dropping the column is not a lossless
# simplification -- it silently replaces every non-uniform prior with a uniform
# one, and the next MCMC run samples a different posterior without saying so.
# ---------------------------------------------------------------------------
def test_the_prior_column_is_parsed():
    from params_for_id import parse_params_for_id

    entries = parse_params_for_id(
        "vessel_name,param_name,min,max,prior\na,k,1,2,normal\nb,j,1,2,exponential\n"
    )
    assert [e.prior for e in entries] == ["normal", "exponential"]


def test_a_blank_prior_cell_is_not_stated():
    """Blank must stay None rather than becoming the string 'nan' or the default:
    CA decides what an absent prior means, and writing one back would put a prior
    into a CSV the user never gave one."""
    from params_for_id import parse_params_for_id

    entries = parse_params_for_id("vessel_name,param_name,min,max,prior\na,k,1,2,\n")
    assert entries[0].prior is None


def test_no_prior_column_leaves_every_entry_unstated():
    from params_for_id import parse_params_for_id

    entries = parse_params_for_id("vessel_name,param_name,min,max\na,k,1,2\n")
    assert entries[0].prior is None


def test_the_prior_survives_a_multi_vessel_row():
    """A whitespace-split vessel_name is one parameter (#193), and it keeps the
    row's prior -- the prior belongs to the row, as CA reads it."""
    from params_for_id import parse_params_for_id

    entries = parse_params_for_id("vessel_name,param_name,min,max,prior\na b,k,1,2,normal\n")
    assert [e.prior for e in entries] == ["normal"]
    assert entries[0].qnames == ["a/k", "b/k"]


def test_the_prior_reaches_the_api_payload(client):
    """The editor reads its rows from this payload, so a prior that stops here is
    a prior the editor cannot round-trip."""
    r = _post_csv_text(client, "vessel_name,param_name,min,max,prior\na,k,1,2,normal\n")
    assert r.status_code == 200, r.text
    assert r.json()["params"][0]["prior"] == "normal"


def test_the_shipped_lotka_volterra_csv_carries_its_priors(client):
    """The fixture that exposed this: it has a prior column, and the editor used
    to rewrite it without one."""
    r = _post_csv_file(client, LV_PARAMS_CSV_PATH)
    assert r.status_code == 200, r.text
    priors = [p["prior"] for p in r.json()["params"]]
    assert all(p is not None for p in priors), priors


# ---------------------------------------------------------------------------
# The values each prior takes (CA #356)
# ---------------------------------------------------------------------------
@pytest.fixture
def requires_ca_priors():
    """CA validates the hyper-parameters; without a CA new enough to know them
    there is no verdict to assert, and the upload passes them through."""
    try:
        from parsers.PrimitiveParsers import normalise_prior_params  # noqa: F401
    except Exception:
        pytest.skip("circulatory_autogen without the prior hyper-parameter schema")


def test_prior_hyperparameters_are_parsed(client):
    from params_for_id import parse_params_for_id

    entries = parse_params_for_id(
        "vessel_name,param_name,min,max,prior,prior_mean,prior_std\n"
        "a,k,0,10,normal,7.0,0.5\n"
    )
    assert entries[0].prior_params == {"prior_mean": "7.0", "prior_std": "0.5"}


def test_an_unstated_hyperparameter_is_absent_not_defaulted():
    """CA decides what an unstated value means (the centre of the range, a sixth
    of it); writing a number here would put one into a file that never had it."""
    from params_for_id import parse_params_for_id

    entries = parse_params_for_id(
        "vessel_name,param_name,min,max,prior,prior_mean,prior_std\n"
        "a,k,0,10,normal,,\n"
    )
    assert entries[0].prior_params == {}


def test_hyperparameters_reach_the_api_payload(client):
    r = _post_csv_text(
        client,
        "vessel_name,param_name,min,max,prior,prior_mean\na,k,0,10,normal,7.0\n",
    )
    assert r.status_code == 200, r.text
    assert r.json()["params"][0]["prior_params"] == {"prior_mean": "7.0"}


def test_ca_rejects_a_hyperparameter_the_prior_does_not_take(client, requires_ca_priors):
    """CA owns the rule; its complaint surfaces at upload rather than when a
    calibration starts."""
    r = _post_csv_text(
        client, "vessel_name,param_name,min,max,prior,prior_std\na,k,0,10,uniform,2.0\n"
    )
    assert r.status_code == 422
    assert "does not use it" in r.json()["detail"]


def test_ca_rejects_a_non_positive_scale(client, requires_ca_priors):
    r = _post_csv_text(
        client, "vessel_name,param_name,min,max,prior,prior_std\na,k,0,10,normal,0\n"
    )
    assert r.status_code == 422
    assert "greater than zero" in r.json()["detail"]


def test_a_file_without_the_columns_is_unaffected(client):
    r = _post_csv_text(client, "vessel_name,param_name,min,max,prior\na,k,0,10,normal\n")
    assert r.status_code == 200, r.text
    assert r.json()["params"][0]["prior_params"] == {}


def test_the_columns_are_read_even_without_ca(monkeypatch):
    """The names decide whether the user's column is read at all. Dropping them
    when CA is unreachable would silently discard the hyper-parameters -- the same
    data loss this support exists to fix. Only validation degrades."""
    import params_for_id as pfi
    import solver_options as so

    monkeypatch.setattr(so, "_introspect_param_prior_types",
                        lambda: (_ for _ in ()).throw(ImportError("no CA")))
    monkeypatch.setattr(pfi, "_validate_prior_params", lambda *a, **k: None)
    so.reset_cache()

    entries = pfi.parse_params_for_id(
        "vessel_name,param_name,min,max,prior,prior_std\na,k,0,10,normal,0.5\n"
    )
    assert entries[0].prior_params == {"prior_std": "0.5"}
