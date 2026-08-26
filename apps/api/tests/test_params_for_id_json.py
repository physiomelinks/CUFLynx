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

from conftest import set_ca_module

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
def test_csv_reads_exactly_as_it_did_before_json(snapshot, requires_params_csv):
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


def test_a_multi_vessel_row_becomes_one_entry_with_both_targets(requires_params_csv):
    """CA reads `vessel_name="a b"` as one parameter in two components (#193)."""
    csv = "vessel_name,param_name,min,max\naortic_root par,C,1e-9,5e-8\n"
    doc = params_json.csv_to_json(csv)

    assert doc["params"][0]["targets"] == ["aortic_root/C", "par/C"]


def test_a_global_row_keeps_its_gen_name_fallback(requires_params_csv):
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
def test_entries_survive_a_write_and_reread(filename, requires_params_csv):
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
        from ca_imports import ca_from

        ca_from("parsers.PrimitiveParsers", "derive_bounds_from_prior")
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
def test_a_blank_optional_cell_is_absent_rather_than_the_string_nan(requires_params_csv):
    """Reading a blank `name_for_plotting` used to yield the text "nan", which
    then appeared on the plot axis."""
    csv = "vessel_name,param_name,min,max,name_for_plotting\na,x,1,2,\n"

    assert parse_params_for_id(csv)[0].name_for_plotting is None


def test_a_csv_without_bounds_columns_is_a_params_for_id_error(requires_params_csv):
    """The JSON layer has its own error type; only one may reach the API, which
    maps it to 422.

    CA's converter accepts a CSV without min/max columns ('unbounded' rows make
    them optional), so the missing bounds surface downstream as the row-level
    "min and max are required" error rather than a column complaint. Supersedes
    the transitional both-wordings version from #222."""
    with pytest.raises(ParamsForIdError, match="min and max are required"):
        parse_params_for_id("vessel_name,param_name\na,x\n")


# ---------------------------------------------------------------------------
# Without circulatory_autogen: CSV is an actionable error, JSON still works
# ---------------------------------------------------------------------------
@pytest.fixture
def no_ca(monkeypatch):
    """Force the no-CA state even on a machine that has CA.

    Setting a sys.modules entry to None makes any import of it raise
    ImportError, regardless of sys.path -- necessary because dev machines have
    CA as a sibling and it is usually already imported by earlier tests.
    """
    import sys

    set_ca_module(monkeypatch, "parsers", None)
    set_ca_module(monkeypatch, "parsers.PrimitiveParsers", None)


def test_reading_a_csv_without_ca_uses_the_local_mapping(no_ca):
    """A packaged app starts with no CA directory, and a study starts with a CSV.

    This used to raise, pointing the user at Settings -- the #208 position, that
    a fallback only exercised where CA is absent drifts unobserved. The state
    turned out not to be a corner: it is every fresh download, and it left the
    app unable to open a params_for_id at all. The drift objection is answered
    instead of ignored, by test_params_csv_fallback_matches_ca in
    test_params_csv_without_ca.py, which compares the two where CA *is* present.
    """
    doc = params_json.csv_to_json("vessel_name,param_name,min,max\na,x,1,2\n")
    assert doc["params"][0]["targets"] == ["a/x"]


def test_uploading_a_csv_without_ca_now_succeeds(no_ca, client):
    resp = client.post(
        "/api/params_for_id/upload",
        content="vessel_name,param_name,min,max\na,x,1,2\n",
        headers={"content-type": "text/csv"},
    )
    assert resp.status_code == 200, resp.text
    assert [p["qname"] for p in resp.json()["params"]] == ["a/x"]


def test_uploading_json_without_ca_still_works(no_ca, client):
    """JSON parsing does not need CA; only the CSV conversion is CA's."""
    doc = {"params": [{"targets": ["a/x"], "min": 1.0, "max": 2.0}]}
    resp = client.post(
        "/api/params_for_id/upload",
        content=json.dumps(doc),
        headers={"content-type": "application/json"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["params"][0]["qname"] == "a/x"


# ---------------------------------------------------------------------------
# Modifier entries (CA #378): the slider carries θ, targets get θ·baselineᵢ
# ---------------------------------------------------------------------------
MODEL_VALUES = {"a/C": 2e-8, "b/C": 4e-8}


def _scale_doc(**over):
    entry = {"name": "C_scale", "modifies": ["a/C", "b/C"], "modifier": "scale",
             "min": 0.5, "max": 2.0}
    entry.update(over)
    return {"params": [entry]}


def test_a_scale_modifier_becomes_one_theta_entry():
    entry = parse_params_for_id(_scale_doc(), MODEL_VALUES)[0]

    assert entry.qname == "a/C"  # the anchor: modifies[0]
    assert entry.qnames == ["a/C", "b/C"]
    assert entry.name == "C_scale"
    assert entry.operation == "scale"
    # θ starts at the operation's identity -- never at a model value.
    assert entry.initial_value == 1.0 and entry.identity == 1.0
    assert entry.baselines == {"a/C": 2e-8, "b/C": 4e-8}
    assert (entry.min, entry.max) == (0.5, 2.0)
    assert entry.warning is None


def test_a_file_written_before_the_rename_still_loads():
    """CA renamed the key to `modifier` (CA #385) and deprecated `operation`.
    Every params_for_id CUFLynx has already written uses the old name, so
    reading it must keep working -- silently, since the user did nothing wrong."""
    doc = {"params": [{"name": "C_scale", "modifies": ["a/C", "b/C"],
                       "operation": "scale", "min": 0.5, "max": 2.0}]}
    entry = parse_params_for_id(doc, MODEL_VALUES)[0]

    assert entry.operation == "scale"
    assert entry.qnames == ["a/C", "b/C"]


def test_setting_both_names_is_refused(requires_ca_resolver):
    """CA refuses an entry carrying the key and its deprecated alias rather than
    silently picking one -- two spellings of the same thing in one entry is a
    file the author did not mean to write."""
    doc = {"params": [{"name": "s", "modifies": ["a/C"], "modifier": "scale",
                       "operation": "scale", "min": 0.5, "max": 2.0}]}
    with pytest.raises(ParamsForIdError, match="deprecated alias"):
        parse_params_for_id(doc, MODEL_VALUES)


def test_modifier_baselines_keep_file_order():
    """baselines[i] pairs with modifies[i]; reordering would scale the wrong
    parameter by the wrong default."""
    entry = parse_params_for_id(_scale_doc(modifies=["b/C", "a/C"]), MODEL_VALUES)[0]
    assert entry.qnames == ["b/C", "a/C"]
    assert list(entry.baselines) == ["b/C", "a/C"]


def test_an_unresolved_modifier_target_warns_but_parses():
    entry = parse_params_for_id(_scale_doc(modifies=["a/C", "ghost/x"]), MODEL_VALUES)[0]
    assert "ghost/x" in entry.warning
    assert entry.baselines == {"a/C": 2e-8}


def test_a_zero_baseline_warns():
    """θ·0 is 0 for every θ -- the slider would appear to do nothing."""
    entry = parse_params_for_id(_scale_doc(), {"a/C": 0.0, "b/C": 4e-8})[0]
    assert "zero baseline" in entry.warning


def test_a_modifier_without_bounds_is_rejected():
    with pytest.raises(ParamsForIdError, match="min and max"):
        parse_params_for_id(
            {"params": [{"name": "s", "modifies": ["a/C"], "modifier": "scale"}]}
        )


def test_a_modifier_cannot_be_unbounded():
    with pytest.raises(ParamsForIdError, match="unbounded"):
        parse_params_for_id(_scale_doc(unbounded=True))


def test_an_unknown_operation_is_rejected_by_name():
    with pytest.raises(ParamsForIdError, match="warp"):
        parse_params_for_id(_scale_doc(modifier="warp"))


def test_targets_and_modifies_together_are_rejected():
    with pytest.raises(ParamsForIdError, match="modifies"):
        parse_params_for_id(
            {"params": [{"targets": ["a/C"], "modifies": ["b/C"],
                         "modifier": "scale", "min": 0.5, "max": 2.0}]}
        )


def test_a_modifier_round_trips_through_the_editor_writer():
    """parse -> entries_to_json -> parse must be lossless, and the written
    entry must carry modifies+operation and never targets (CA refuses both)."""
    doc = {"params": [
        # Free/override entries name parameters the modifier does not touch --
        # a modified qname that is also free is (rightly) refused by CA.
        {"targets": ["c/R"], "min": 1e-9, "max": 5e-8},
        {"name": "grp", "targets": ["a/C2", "b/C2"], "min": 1.0, "max": 2.0},
        _scale_doc()["params"][0],
    ]}
    first = parse_params_for_id(doc, MODEL_VALUES)
    written = params_json.entries_to_json(first)

    mod = written["params"][2]
    # `modifier`, not `operation`: CA renamed the key (a modifier acts on
    # parameters, an operation acts on outputs) and warns on the old one.
    assert mod["modifies"] == ["a/C", "b/C"] and mod["modifier"] == "scale"
    assert "operation" not in mod
    assert "targets" not in mod
    assert written["params"][1]["name"] == "grp"

    again = parse_params_for_id(written, MODEL_VALUES)
    assert [e.as_dict() for e in again] == [e.as_dict() for e in first]


# CA's cross-entry rules, judged by its resolver when it is importable.
@pytest.fixture
def requires_ca_resolver():
    if params_json._ca_doc_resolver() is None:
        pytest.skip("circulatory_autogen's params_for_id resolver not available")


def test_ca_rejects_a_duplicate_name(requires_ca_resolver):
    with pytest.raises(ParamsForIdError, match="reuses the name"):
        parse_params_for_id({"params": [
            {"name": "same", "targets": ["a/C"], "min": 1.0, "max": 2.0},
            {"name": "same", "targets": ["b/C"], "min": 1.0, "max": 2.0},
        ]})


def test_ca_rejects_a_modifier_of_a_modifier(requires_ca_resolver):
    with pytest.raises(ParamsForIdError):
        parse_params_for_id({"params": [
            {"name": "s1", "modifies": ["a/C"], "modifier": "scale",
             "min": 0.5, "max": 2.0},
            {"name": "s2", "modifies": ["a/C"], "modifier": "scale",
             "min": 0.5, "max": 2.0},
        ]})


def test_ca_rejects_a_modified_param_that_is_also_free(requires_ca_resolver):
    with pytest.raises(ParamsForIdError):
        parse_params_for_id({"params": [
            {"targets": ["a/C"], "min": 1e-9, "max": 5e-8},
            {"name": "s", "modifies": ["a/C"], "modifier": "scale",
             "min": 0.5, "max": 2.0},
        ]})


def test_ca_rejects_an_unknown_entry_key(requires_ca_resolver):
    with pytest.raises(ParamsForIdError, match="unknown key"):
        parse_params_for_id(
            {"params": [{"targets": ["a/C"], "min": 1.0, "max": 2.0, "wingspan": 3}]}
        )


# ---------------------------------------------------------------------------
# The stored file's suffix follows its content (CA branches on the suffix)
# ---------------------------------------------------------------------------
def test_a_json_upload_is_stored_with_a_json_suffix(client):
    """CA's get_param_id_info picks its parser by filename suffix, so a JSON doc
    saved under .csv would be handed to CA's CSV parser by every runner."""
    import main
    from conftest import LV_MODEL_PATH, upload_model

    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    doc = {"params": [{"targets": ["Lotka_Volterra_module/alpha"], "min": 0.1, "max": 2.0}]}
    r = client.post(
        f"/api/params_for_id/upload?model_id={model_id}",
        content=json.dumps(doc),
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 200, r.text
    assert main._models[model_id].params_path.suffix == ".json"


def test_an_uploaded_csv_is_stored_as_json(client, requires_params_csv):
    """A CSV is converted on the way in, so the stored study is always JSON.

    JSON is the only form that can carry a modifier, its inputs or a prior's
    parameters, so keeping the CSV as the stored form would make those
    unrepresentable in whatever ends up in the user's outputs directory.
    """
    import main
    from conftest import LV_MODEL_PATH, LV_PARAMS_CSV_PATH, upload_model

    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    with open(LV_PARAMS_CSV_PATH, "rb") as fh:
        r = client.post(
            f"/api/params_for_id/upload?model_id={model_id}",
            files={"file": (LV_PARAMS_CSV_PATH.name, fh, "text/csv")},
        )
    assert r.status_code == 200, r.text
    stored = main._models[model_id].params_path
    assert stored.suffix == ".json"
    doc = json.loads(stored.read_text())
    assert doc["params"], "converted document has no parameters"


def test_a_csv_is_kept_as_csv_when_ca_cannot_convert_it(monkeypatch):
    """Without CA the CSV is stored as-is rather than the upload being refused.

    The packaged app starts with no CA directory set, and a study must not
    become unloadable because of that -- CA's own CSV path still reads it. Driven
    at ``_save_params_file`` rather than through the route, because faking "no
    CA" also disables the *parsing* the route does first, which would fail the
    request for an unrelated reason and prove nothing about storage.

    Also pins the stale-twin rule: a format switch must not leave two params
    files disagreeing about which is current. The runners read whichever path
    the record holds, but a human inspecting the upload dir would find both.
    """
    import pathlib

    import main
    import params_json
    from conftest import LV_MODEL_PATH

    def _no_ca(_data):
        raise params_json.ParamsJsonError("circulatory_autogen is not available")

    monkeypatch.setattr(params_json, "csv_to_json", _no_ca)

    # The study's files are named after the study now, so this needs a record to
    # ask -- the collision the model_id used to prevent is handled by the
    # per-model directory `_study_file` puts them in.
    record = main._ModelRecord("twin-test", pathlib.Path("unused.cellml"),
                               main.parse_cellml(LV_MODEL_PATH.read_bytes()),
                               "twin_study")
    json_path = main._save_params_file(record, b'{"params": []}')
    assert json_path.suffix == ".json"
    assert json_path.name.startswith("twin_study"), json_path.name

    csv_path = main._save_params_file(
        record, b"vessel_name,param_name,param_type,min,max\nheart,C,constant,1,2\n"
    )
    assert csv_path.suffix == ".csv"
    assert not json_path.exists(), "stale .json twin left beside the .csv"
    csv_path.unlink(missing_ok=True)


def test_ca_available_in_ci():
    """Canary: in CI the CA checkout must be importable, or the whole CSV test
    tier silently skips and reads as green. Fails (not skips) only in CI."""
    import os

    from conftest import _params_csv_converter_available

    if os.environ.get("CI") and not _params_csv_converter_available():
        raise AssertionError(
            "CI is expected to provide a circulatory_autogen checkout via "
            "CIRCULATORY_AUTOGEN_SRC, but its params CSV converter is not "
            "importable -- the CSV test tier is silently skipping"
        )


def test_min_greater_than_max_names_the_entry():
    with pytest.raises(ParamsForIdError, match=r"min \(5.0\) > max \(1.0\)"):
        parse_params_for_id({"params": [{"targets": ["a/x"], "min": 5.0, "max": 1.0}]})


def test_an_entry_with_no_targets_is_rejected():
    # Matches a fragment stable across both judges: CA's resolver says
    # 'needs a non-empty "targets" list'; the no-CA path says 'no targets'.
    with pytest.raises(ParamsForIdError, match="targets"):
        parse_params_for_id({"params": [{"targets": [], "min": 1.0, "max": 2.0}]})


def test_a_document_without_params_is_rejected():
    with pytest.raises(ParamsForIdError, match="no 'params' list"):
        parse_params_for_id({"defaults": {}})


def test_bounds_are_required_unless_unbounded():
    with pytest.raises(ParamsForIdError, match="min and max are required"):
        parse_params_for_id({"params": [{"targets": ["a/x"]}]})


# ---------------------------------------------------------------------------
# A modifier's `inputs` (CA #383)
# ---------------------------------------------------------------------------
def test_a_modifiers_inputs_survive_a_round_trip():
    """`inputs` names the model constants the modifier function needs.

    CA's `remainder` cannot be called without its `subtract` list, so dropping
    the key on a read/write cycle would silently break the entry on the next run
    -- and the editor rewrites this file every time it saves.
    """
    from params_json import entries_to_json

    doc = {
        "params": [
            {
                "name": "q_total",
                "modifies": ["heart/q_lv_init"],
                "modifier": "remainder",
                "inputs": {"subtract": ["heart/q_rv_init", "aortic_root/q_init"]},
                "min": 1e-4,
                "max": 1e-2,
            }
        ]
    }
    entries = parse_params_for_id(doc)
    assert entries[0].inputs == {"subtract": ["heart/q_rv_init", "aortic_root/q_init"]}
    # Exposed to the editor, so the row can carry it through a save.
    assert entries[0].as_dict()["inputs"] == doc["params"][0]["inputs"]
    written = entries_to_json(entries)["params"][0]
    assert written["inputs"] == doc["params"][0]["inputs"]


def test_an_entry_without_inputs_does_not_grow_the_key():
    """CA refuses keys outside its closed entry-key set, and an empty one would
    be noise in a file the user reads."""
    from params_json import entries_to_json

    doc = {"params": [{"targets": ["a/x"], "min": 0.1, "max": 2.0}]}
    written = entries_to_json(parse_params_for_id(doc))["params"][0]
    assert "inputs" not in written
