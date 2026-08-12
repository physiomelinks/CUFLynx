"""Reading a params_for_id CSV when circulatory_autogen is not importable.

A freshly downloaded release starts in exactly that state -- CA is chosen at
runtime and is not bundled -- and the upload used to be refused, so the packaged
app could not open a params_for_id CSV at all. That is the first thing a study
needs: without it there are no sliders and nothing to calibrate.

CA remains the authority whenever it can be imported. These tests cover the
fallback, and pin it to CA's mapping so the two cannot drift.
"""

import pytest

import params_json

# One row per feature of the mapping: a grouped (multi-vessel) row, a plain row,
# every passthrough column, a boolean, and a prior with hyper-parameters.
RICH_CSV = """vessel_name,param_name,param_type,min,max,name_for_plotting,comment,prior,prior_mean,prior_std,unbounded
heart aorta,C,const,1e-9,5e-8,C_{ao},shared compliance,normal,2e-9,1e-9,false
venous,R,const,1e6,1e8,R_{v},,uniform,,,true
"""


@pytest.fixture
def without_ca(monkeypatch):
    """Make CA unimportable, the way a packaged app with no CA directory is."""
    monkeypatch.setattr(params_json, "_ca_csv_converter", lambda: None)


def test_a_csv_loads_without_circulatory_autogen(without_ca):
    """The bug: this raised "circulatory_autogen ... is not available"."""
    doc = params_json.csv_to_json(RICH_CSV)
    assert [p["name"] for p in doc["params"]] == ["heart/C", "venous/R"]


def test_a_grouped_row_keeps_every_target(without_ca):
    """One row is one calibrated quantity written into several components (#193).

    Splitting it per vessel would give each its own slider and let the user build
    a state the model never has.
    """
    doc = params_json.csv_to_json(RICH_CSV)
    assert doc["params"][0]["targets"] == ["heart/C", "aorta/C"]
    assert doc["params"][1]["targets"] == ["venous/R"]


def test_priors_land_in_prior_params_not_at_the_top_level(without_ca):
    doc = params_json.csv_to_json(RICH_CSV)
    first = doc["params"][0]
    assert first["prior"] == "normal"
    assert first["prior_params"] == {"prior_mean": "2e-9", "prior_std": "1e-9"}
    # A blank hyper-parameter cell is absent, not an empty string: CA omits it,
    # and an empty string would later parse as a number and fail.
    assert "prior_params" not in doc["params"][1]


def test_a_boolean_column_is_read_and_a_nonsense_one_refused(without_ca):
    doc = params_json.csv_to_json(RICH_CSV)
    assert doc["params"][0]["unbounded"] is False
    assert doc["params"][1]["unbounded"] is True

    bad = RICH_CSV.replace(",true\n", ",perhaps\n")
    with pytest.raises(params_json.ParamsJsonError, match="true/false"):
        params_json.csv_to_json(bad)


def test_a_row_missing_its_identifiers_names_the_row(without_ca):
    csv = "vessel_name,param_name,min,max\n,C,1,2\n"
    with pytest.raises(params_json.ParamsJsonError, match="row 0"):
        params_json.csv_to_json(csv)


def test_a_csv_without_the_required_columns_says_which_it_has(without_ca):
    with pytest.raises(params_json.ParamsJsonError, match="vessel_name"):
        params_json.csv_to_json("alpha,beta\n1,2\n")


def test_a_utf8_bom_does_not_reach_the_first_column_name(without_ca):
    """Excel writes one, and it would otherwise make column 1 unrecognisable."""
    doc = params_json.csv_to_json(RICH_CSV.encode("utf-8-sig"))
    assert doc["params"][0]["targets"] == ["heart/C", "aorta/C"]


def test_params_csv_fallback_matches_ca():
    """The fallback and CA must produce the same study.

    This is what keeps a local reimplementation honest: CA's converter is the
    definition, and its mapping is documented precisely so a tool without CA can
    reproduce it. If CA adds a column or renames one, this fails here -- in CI,
    where CA is importable -- rather than silently giving packaged users a
    different study from the one CA would have built.
    """
    convert = params_json._ca_csv_converter()
    if convert is None:
        pytest.skip("circulatory_autogen is not importable in this environment")

    from_ca = params_json.load_doc(convert(RICH_CSV))
    from_fallback = params_json.load_doc(params_json._csv_to_json_without_ca(RICH_CSV))
    assert from_fallback == from_ca


def test_ca_is_preferred_when_it_is_available(monkeypatch):
    """The fallback is a fallback: with CA present, CA does the conversion, so a
    column CA learns about flows through without an edit here."""
    called = {}

    def fake_convert(text):
        called["yes"] = True
        return {"version": 1, "defaults": {},
                "params": [{"targets": ["a/b"], "name": "a/b", "min": "0", "max": "1"}]}

    monkeypatch.setattr(params_json, "_ca_csv_converter", lambda: fake_convert)
    params_json.csv_to_json(RICH_CSV)
    assert called == {"yes": True}
