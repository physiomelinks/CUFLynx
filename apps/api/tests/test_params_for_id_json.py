"""params_for_id as JSON, with CSV converted to it on read.

Two things are being protected here, and they pull in opposite directions.

*Nothing changed for CSV.* Every existing study is a CSV, and it now reaches the
parser through a conversion it did not go through before. A mis-mapped column
would not fail -- it would quietly change a bound or drop a prior, and the
calibration would run happily on the wrong parameterisation. So the CSV path is
pinned against goldens captured from the pre-conversion parser, key by key over
every fixture in ``resources/``, rather than by spot checks.

*Something new is possible.* A CSV row builds its qualified names as
``vessel_name[i] + '/' + param_name``, so a group is forced to share one
``param_name``. The JSON form's ``targets`` is a list of full qnames, which has
no such constraint -- that is the whole reason for the format.
"""
import json
from pathlib import Path

import pytest

import params_json
from params_for_id import ParamEntry, ParamsForIdError, parse_params_for_id

RESOURCES = Path(__file__).resolve().parents[3] / "resources"
GOLDENS = Path(__file__).parent / "data" / "params_for_id_goldens.json"

# The same synthetic model values the goldens were captured against. The four
# compliances deliberately differ so the grouped-initial-value warning is
# exercised rather than merely present.
INITIAL_VALUES = {
    "parameters/C_aortic_root": 1e-8,
    "parameters/C_par": 2e-8,
    "parameters/C_pvn": 3e-8,
    "parameters/C_venous_svc": 4e-8,
    "parameters_global/q_lv_init": 5e-4,
    "parameters_global/E_lv_A": 2e8,
    "parameters_global/E_lv_B": 1e7,
}


def _goldens():
    return json.loads(GOLDENS.read_text())


# ---------------------------------------------------------------------------
# The backwards-compatibility guarantee
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("snapshot", sorted(_goldens()))
def test_csv_reads_exactly_as_it_did_before_json(snapshot):
    """Key-by-key equality against the pre-conversion parser, per fixture.

    Captured from the implementation that read the CSV directly, so this fails if
    the CSV -> JSON mapping loses, renames or coerces anything at all.
    """
    filename, tag = snapshot.split("::")
    initial_values = INITIAL_VALUES if tag == "with_initials" else None
    entries = parse_params_for_id((RESOURCES / filename).read_bytes(), initial_values)

    assert [e.as_dict() for e in entries] == _goldens()[snapshot]


def test_every_shipped_fixture_is_covered():
    """A fixture added to resources/ without a golden would silently not be tested."""
    covered = {s.split("::")[0] for s in _goldens()}
    shipped = {p.name for p in RESOURCES.glob("*params*.csv")}

    assert shipped == covered, f"fixtures without goldens: {sorted(shipped - covered)}"


def test_a_multi_vessel_row_becomes_one_entry_with_both_targets():
    """CA reads `vessel_name="a b"` as one parameter in two components (#193)."""
    csv = "vessel_name,param_name,min,max\naortic_root par,C,1e-9,5e-8\n"
    doc = params_json.csv_to_json(csv)

    assert doc["params"][0]["targets"] == ["aortic_root/C", "par/C"]


def test_a_global_row_keeps_its_gen_name_fallback():
    """The flat-model rename (#298/#368): `global` contributes a bare name."""
    csv = "vessel_name,param_name,min,max\nglobal,q_lv_init,1e-4,1e-3\n"
    entries = parse_params_for_id(csv, INITIAL_VALUES)

    assert entries[0].qnames == ["global/q_lv_init"]
    assert entries[0].initial_value == pytest.approx(5e-4)


# ---------------------------------------------------------------------------
# What the JSON form makes possible
# ---------------------------------------------------------------------------
def test_targets_may_name_different_parameters():
    """The feature. A CSV cannot express this: its qnames are built from one
    `param_name`, so every member of a group had to share it."""
    doc = {
        "params": [
            {
                "name": "compliance_and_elastance",
                "targets": ["aortic_root/C", "global/E_lv_A"],
                "min": 1.0,
                "max": 2.0,
            }
        ]
    }
    entries = parse_params_for_id(doc, INITIAL_VALUES)

    assert len(entries) == 1
    assert entries[0].qnames == ["aortic_root/C", "global/E_lv_A"]


def test_a_heterogeneous_group_reports_its_disagreement_without_a_shared_name():
    """The warning has no single parameter name to quote, so it describes the
    members instead -- it must still fire, because touching the slider overwrites
    every member and the evidence is gone."""
    doc = {"params": [{"targets": ["aortic_root/C", "global/E_lv_A"], "min": 1.0, "max": 2.0}]}
    entry = parse_params_for_id(doc, INITIAL_VALUES)[0]

    assert entry.warning is not None
    assert "aortic_root/C = 1e-08" in entry.warning
    assert "parameter group" in entry.warning


def test_json_is_sniffed_from_content_not_filename():
    """Uploads arrive as bytes; a renamed file must still get the right parser."""
    assert params_json.looks_like_json(b'  {"params": []}')
    assert params_json.looks_like_json("[{}]")
    assert not params_json.looks_like_json("vessel_name,param_name,min,max\n")


def test_a_bare_list_is_accepted():
    """CA's own entry point takes a list of dicts; rejecting what CA accepts would
    make the two formats subtly different."""
    entries = parse_params_for_id([{"targets": ["a/x"], "min": 1.0, "max": 2.0}])

    assert entries[0].qnames == ["a/x"]


# ---------------------------------------------------------------------------
# defaults
# ---------------------------------------------------------------------------
def test_defaults_apply_to_entries_that_do_not_set_the_key():
    doc = {
        "defaults": {"prior": "normal", "param_type": "const"},
        "params": [
            {"targets": ["a/x"], "min": 1.0, "max": 2.0,
             "prior_params": {"prior_mean": 1.5, "prior_std": 0.1}},
        ],
    }
    entry = parse_params_for_id(doc)[0]

    assert entry.prior == "normal"
    assert entry.param_type == "const"


def test_an_entry_overrides_the_defaults_block():
    doc = {
        "defaults": {"prior": "normal"},
        "params": [{"targets": ["a/x"], "min": 1.0, "max": 2.0, "prior": "uniform"}],
    }

    assert parse_params_for_id(doc)[0].prior == "uniform"


def test_prior_params_merge_per_key_rather_than_wholesale():
    """Setting one hyper-parameter for the whole file must not wipe an entry's
    own. Changing the family in one place is the reason the block exists, so it
    has to compose."""
    doc = {
        "defaults": {"prior": "normal", "prior_params": {"prior_std": 0.5}},
        "params": [{"targets": ["a/x"], "min": 1.0, "max": 2.0,
                    "prior_params": {"prior_mean": 1.5}}],
    }
    entry = parse_params_for_id(doc)[0]

    assert entry.prior_params == {"prior_mean": "1.5", "prior_std": "0.5"}


# ---------------------------------------------------------------------------
# Round-tripping through the editor
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("filename", sorted(p.name for p in RESOURCES.glob("*params*.csv")))
def test_entries_survive_a_write_and_reread(filename):
    """What the editor does: read, write JSON, read again. Anything the writer
    drops would be lost the first time a user saved a file they had not edited."""
    first = parse_params_for_id((RESOURCES / filename).read_bytes(), INITIAL_VALUES)
    again = parse_params_for_id(params_json.entries_to_json(first), INITIAL_VALUES)

    assert [e.as_dict() for e in again] == [e.as_dict() for e in first]


def test_target_order_is_preserved_on_write():
    """CA's `baselines[i]` are index-aligned with `targets[i]`, so a reorder here
    would pair a scale factor with the wrong parameter's baseline."""
    doc = {"params": [{"targets": ["z/c", "a/b", "m/q"], "min": 1.0, "max": 2.0}]}
    written = params_json.entries_to_json(parse_params_for_id(doc))

    assert written["params"][0]["targets"] == ["z/c", "a/b", "m/q"]


def test_a_derived_range_is_not_written_back_as_authored_bounds():
    """An unbounded entry's bounds come from its prior. Writing them back would
    freeze a derived value into the file and stop it tracking the prior.

    Asserted against a constructed entry rather than a parsed one: the subject is
    the writer, and deriving the range needs a circulatory_autogen that the
    no-CA CI job deliberately does not have. The end-to-end version is below.
    """
    entry = ParamEntry(
        qname="a/x", qnames=["a/x"], min=0.0, max=20.0, name_for_plotting=None,
        param_type=None, unbounded=True, prior="normal",
        prior_params={"prior_mean": "10.0", "prior_std": "2.0"},
    )
    written = params_json.entries_to_json([entry])["params"][0]

    assert written["unbounded"] is True
    assert "min" not in written and "max" not in written


@pytest.fixture
def requires_ca_priors():
    """CA owns the derivation of an unbounded entry's range; without a CA new
    enough to do it there is nothing to assert."""
    try:
        from parsers.PrimitiveParsers import derive_bounds_from_prior  # noqa: F401
    except Exception:
        pytest.skip("circulatory_autogen without the prior hyper-parameter schema")


def test_an_unbounded_entry_gets_its_range_from_its_prior(requires_ca_priors):
    """End to end: the sliders must cover the range the calibration will search,
    so the bounds have to be derived rather than left absent."""
    doc = {"params": [{"targets": ["a/x"], "unbounded": True, "prior": "normal",
                       "prior_params": {"prior_mean": 10.0, "prior_std": 2.0}}]}
    entry = parse_params_for_id(doc)[0]

    assert entry.min is not None and entry.max is not None
    assert entry.min < 10.0 < entry.max


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------
def test_a_blank_optional_cell_is_absent_rather_than_the_string_nan():
    """Reading a blank `name_for_plotting` used to yield the text "nan", which
    then appeared on the plot axis."""
    csv = "vessel_name,param_name,min,max,name_for_plotting\na,x,1,2,\n"

    assert parse_params_for_id(csv)[0].name_for_plotting is None


def test_a_missing_required_column_is_a_params_for_id_error():
    """The JSON layer has its own error type; only one may reach the API, which
    maps it to 422.

    The wording depends on which converter ran. Without CA, the local fallback
    requires the min/max *columns* and names them. With CA importable,
    csv_to_json prefers CA's own params_for_id_csv_to_json, which — since
    'unbounded' rows exist — accepts a CSV without them; the missing bounds then
    surface downstream as the row-level "min and max are required unless
    'unbounded' is set" error. Either way it is one ParamsForIdError, so pin the
    type and accept both messages rather than the local fallback's phrasing."""
    with pytest.raises(
        ParamsForIdError, match="missing required column|min and max are required"
    ):
        parse_params_for_id("vessel_name,param_name\na,x\n")


def test_min_greater_than_max_names_the_entry():
    with pytest.raises(ParamsForIdError, match=r"min \(5.0\) > max \(1.0\)"):
        parse_params_for_id({"params": [{"targets": ["a/x"], "min": 5.0, "max": 1.0}]})


def test_an_entry_with_no_targets_is_rejected():
    with pytest.raises(ParamsForIdError, match="no targets"):
        parse_params_for_id({"params": [{"targets": [], "min": 1.0, "max": 2.0}]})


def test_a_document_without_params_is_rejected():
    with pytest.raises(ParamsForIdError, match="no 'params' list"):
        parse_params_for_id({"defaults": {}})


def test_bounds_are_required_unless_unbounded():
    with pytest.raises(ParamsForIdError, match="min and max are required"):
        parse_params_for_id({"params": [{"targets": ["a/x"]}]})
