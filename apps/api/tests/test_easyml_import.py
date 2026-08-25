"""Accept an openCARP EasyML ``.model`` file by converting it to CellML at the door.

The reading is circulatory_autogen's (``libcuflynx.parsers.EasyMLParsers``) and is
tested there. What is tested here is CUFLynx's share of it: recognising the file,
routing it, keeping the source, turning a refusal into a 422, offering the
synthesised protocol, and -- the part that would otherwise be silent -- putting
the reader's warnings in front of the user.
"""

from __future__ import annotations

import io
import json
import os
import zipfile
from pathlib import Path

import easyml_import
import pytest
from fastapi.testclient import TestClient

import main
from conftest import all_easyml_fixtures

# A whole model in miniature: an external V (so the membrane equation has to be
# synthesised), a current sum, one explicit state and one gate.
MODEL = b"""
/*
name: TinyCell
*/
V; .nodal(); .external(Vm);
Iion; .nodal(); .external();

V_init = -80.0;
Ca_init = 0.0002;

g_Na = 16.0; .param();

m_inf = 1.0 / (1.0 + exp(-(V + 40.0) / 10.0));
tau_m = 0.1;

INa = g_Na * m * m * m * (V - 50.0);
diff_Ca = -0.001 * INa - 0.01 * Ca;

Iion = INa;

group {
  m;
}.method(rush_larsen);
"""


@pytest.fixture
def client():
    with TestClient(main.app) as c:
        yield c


def upload(client, data=MODEL, name="tiny.model", outputs=None):
    params = {"output_dir": outputs} if outputs else None
    return client.post(
        "/api/models/upload",
        files={"file": (name, data, "application/octet-stream")},
        params=params,
    )


# ---------------------------------------------------------------------------
# Recognition, which is CUFLynx's alone: it happens before CA is consulted
# ---------------------------------------------------------------------------
def test_recognises_the_extension():
    assert easyml_import.is_easyml_filename("Courtemanche.model")
    assert not easyml_import.is_easyml_filename("model.cellml")


def test_recognises_the_content():
    assert easyml_import.looks_like_easyml(MODEL)


def test_a_cellml_document_named_model_is_left_to_the_cellml_path():
    """``.model`` is a generic suffix. Routing a CellML file here would replace a
    clear CellML error with an EasyML one about a language it was never in."""
    cellml = b'<?xml version="1.0"?><model xmlns="http://www.cellml.org/cellml/2.0#"/>'
    assert not easyml_import.wants_easyml("thing.model", cellml)


def test_an_easyml_file_with_the_wrong_name_is_still_recognised():
    assert easyml_import.wants_easyml("mystery.txt", MODEL)


def test_the_predicates_do_not_need_circulatory_autogen(monkeypatch):
    """They run on every upload, before anything knows whether CA is reachable."""
    monkeypatch.setattr(easyml_import, "_ca_parser", lambda *a, **k: None)
    assert easyml_import.wants_easyml("tiny.model", MODEL)


# ---------------------------------------------------------------------------
# The upload
# ---------------------------------------------------------------------------
def test_the_model_arrives_as_cellml(client, requires_easyml):
    r = upload(client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "TinyCell"
    assert body["converted_from"] == "tiny.model"
    assert body["odes"], "the synthesised membrane equation should give it states"


def test_the_parameters_reach_the_variable_list(client, requires_easyml):
    """EasyML is flat, so the component the reader invents from the file's own
    name is what makes ``component/variable`` mean anything downstream."""
    body = upload(client).json()
    r = client.get(f"/api/models/{body['model_id']}/variables")
    assert r.status_code == 200
    variables = r.json()
    assert "TinyCell/g_Na" in variables["all_names"]
    assert "TinyCell/g_Na" in variables["params"]
    assert "TinyCell/V" in variables["odes"]


def test_the_reader_is_not_silent_about_what_it_decided(client, requires_easyml):
    """An EasyML import always has something to say, and it must not be the log
    that hears it."""
    body = upload(client).json()
    warnings = body["warnings"]
    assert any("membrane equation" in w for w in warnings), warnings
    assert any("rush_larsen" in w for w in warnings), warnings


def test_the_synthesised_stimulus_is_offered_as_obs_data(client, requires_easyml):
    body = upload(client).json()
    offer = body["protocol_obs_data"]
    assert offer["filename"] == "tiny_obs_data.json"
    assert list(offer["obs_data"]["protocol_info"]["params_to_change"]) == [
        "TinyCell/i_stim"
    ]
    assert any("carries no stimulus" in n for n in offer["notes"])


def test_the_converted_cellml_is_kept_for_the_user(client, requires_easyml, tmp_path):
    body = upload(client, outputs=str(tmp_path)).json()
    saved = body["converted_cellml_path"]
    assert saved and Path(saved).read_bytes().lstrip().startswith(b"<?xml")


def test_the_dropped_file_is_kept_as_the_models_source(client, requires_easyml):
    """Without this the file the user wrote exists nowhere on the server and
    "show me my model" has nothing to show."""
    body = upload(client).json()
    r = client.get(f"/api/models/{body['model_id']}/source")
    assert r.status_code == 200
    assert r.content == MODEL


def test_editing_the_source_opens_the_model_file_not_the_cellml(
    client, requires_easyml, tmp_path, monkeypatch
):
    import editor_launch

    monkeypatch.setattr(
        editor_launch, "open_in_editor",
        lambda path, **_: {"opened": True, "editor": "fake-editor", "reason": ""})
    body = upload(client, outputs=str(tmp_path)).json()
    r = client.post(
        f"/api/models/{body['model_id']}/edit",
        json={"config_outputs_dir": str(tmp_path)},
    )
    assert r.status_code == 200, r.text
    edit = r.json()
    assert edit["filename"].endswith(".model")
    assert not edit["runs"], "the CellML runs; the .model is its source"
    assert Path(edit["path"]).read_bytes() == MODEL


def test_an_unreadable_model_is_a_422_naming_the_reason(client, requires_easyml):
    r = upload(client, data=b"V_init = -80;\ndiff_y = nowhere;\ny_init = 0;\n")
    assert r.status_code == 422
    assert "nowhere" in r.json()["detail"]


def test_a_model_in_an_archive_takes_the_same_route(client, requires_easyml):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("tiny.model", MODEL)
    r = client.post(
        "/api/omex/upload",
        files={"file": ("study.omex", buf.getvalue(), "application/zip")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "TinyCell"
    assert body["converted_from"] == "tiny.model"
    assert any("membrane equation" in w for w in body["warnings"]), body["warnings"]


# ---------------------------------------------------------------------------
# The shim contract: what happens when the engine cannot read it
# ---------------------------------------------------------------------------
def test_without_the_engine_reader_the_error_says_what_to_do(monkeypatch):
    """There is deliberately no local re-implementation, so this is the whole
    behaviour of a CUFLynx whose circulatory_autogen predates the reader."""
    monkeypatch.setattr(easyml_import, "_ca_parser", lambda *a, **k: None)
    with pytest.raises(easyml_import.EasyMLImportError) as exc:
        easyml_import.import_easyml(MODEL, filename="tiny.model")
    assert "libcuflynx.parsers.EasyMLParsers" in str(exc.value)
    assert "Update circulatory_autogen" in str(exc.value)


def test_the_engine_error_class_does_not_leak_through(monkeypatch):
    """The CA directory can be re-pointed at runtime, so the class call sites
    catch has to be CUFLynx's own."""
    class Boom:
        @staticmethod
        def import_easyml(*a, **k):
            raise ValueError("engine said no")

    monkeypatch.setattr(easyml_import, "_ca_parser", lambda *a, **k: Boom)
    with pytest.raises(easyml_import.EasyMLImportError, match="engine said no"):
        easyml_import.import_easyml(MODEL, filename="tiny.model")


# ---------------------------------------------------------------------------
# End to end: the imported model is an ordinary CUFLynx model
# ---------------------------------------------------------------------------
def test_the_imported_model_simulates(client, requires_easyml, requires_simulation):
    """By the time it reaches the engine it is CellML, so nothing downstream --
    no solver, no model type, no packaging -- had to learn this format exists.
    This is the assertion that says so."""
    body = upload(client).json()
    r = client.post(
        "/api/simulate",
        json={
            "model_id": body["model_id"],
            "params": {},
            "outputs": ["TinyCell/V", "TinyCell/Ca"],
        },
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["time"]) > 1
    assert len(out["outputs"]["TinyCell/V"]) == len(out["time"])


def test_a_param_group_variable_can_be_changed(client, requires_easyml, requires_simulation):
    """``.param()`` is EasyML's own word for "a user may set this", and it has to
    survive into the thing the sliders and params_for_id address."""
    body = upload(client).json()

    def peak(g_na):
        r = client.post(
            "/api/simulate",
            json={
                "model_id": body["model_id"],
                "params": {"TinyCell/g_Na": g_na},
                "outputs": ["TinyCell/V"],
            },
        )
        assert r.status_code == 200, r.text
        return max(r.json()["outputs"]["TinyCell/V"])

    assert peak(16.0) != pytest.approx(peak(1.0)), "g_Na had no effect on the model"


# ---------------------------------------------------------------------------
# The shipped examples, swept the way the .mmt fixtures are
#
# resources/ carries EasyML written *for* this repository rather than exported
# from the .mmt files next door. openCARP's own model library is under the
# openCARP Academic Public License and is not redistributable from here, and a
# Myokit export of a third-party .mmt would be a derivative of a file this
# repository only aggregates -- weaker ground than the verbatim aggregation
# resources/models/third_party/README.md relies on.
#
# Reader correctness against real published models is circulatory_autogen's
# round-trip test, which exports Myokit's own examples and reads them back
# without committing anything. What these cover is the route through this app.
# ---------------------------------------------------------------------------
@pytest.mark.integration
@pytest.mark.parametrize("path", all_easyml_fixtures(), ids=lambda p: p.name)
def test_every_easyml_fixture_converts_to_a_model_worth_running(
    path, requires_easyml, tmp_path
):
    from cellml_meta import parse_cellml

    read = easyml_import.import_easyml(
        path.read_bytes(), filename=path.name, out_dir=str(tmp_path)
    )
    meta = parse_cellml(read["cellml"])
    assert meta.variable_count > 0
    assert meta.odes, "no states, so there would be nothing to integrate"
    assert Path(read["cellml_path"]).is_file()
    assert read["warnings"], (
        "an EasyML import always decides something -- at minimum the membrane "
        "equation the format leaves out -- and must say so"
    )


@pytest.mark.integration
@pytest.mark.parametrize("path", all_easyml_fixtures(), ids=lambda p: p.name)
def test_every_easyml_fixture_loads_through_the_upload_route(
    path, client, requires_easyml
):
    body = upload(client, data=path.read_bytes(), name=path.name).json()
    assert body["converted_from"] == path.name
    assert body["odes"], "the synthesised membrane equation should give it states"
    assert body["warnings"]


@pytest.mark.integration
@pytest.mark.parametrize("path", all_easyml_fixtures(), ids=lambda p: p.name)
def test_every_easyml_fixture_offers_an_obs_data_the_validator_accepts(
    path, client, requires_easyml
):
    """A protocol the UI would adopt and the next request would reject is worse
    than no protocol at all, so the offer goes through the real validator."""
    body = upload(client, data=path.read_bytes(), name=path.name).json()
    offered = body["protocol_obs_data"]
    assert offered is not None
    if offered["obs_data"] is None:
        assert offered["reason"], "no protocol, and no reason given"
        return
    r = client.post(
        "/api/obs_data/upload",
        json={"model_id": body["model_id"], "obs_data": offered["obs_data"]},
    )
    assert r.status_code == 200, r.text


@pytest.mark.integration
@pytest.mark.parametrize("path", all_easyml_fixtures(), ids=lambda p: p.name)
def test_every_easyml_fixture_simulates(
    path, client, requires_easyml, requires_simulation
):
    """Loading is half of it. By this point the model is ordinary CellML, so
    this is the assertion that the whole rail works end to end."""
    body = upload(client, data=path.read_bytes(), name=path.name).json()
    r = client.post(
        "/api/simulate",
        json={"model_id": body["model_id"], "params": {}, "outputs": body["odes"][:4]},
    )
    assert r.status_code == 200, r.text
    out = r.json()
    assert len(out["time"]) > 1
    for name, series in out["outputs"].items():
        assert len(series) == len(out["time"]), name
        assert all(v == v for v in series), f"{name} went NaN"


# ---------------------------------------------------------------------------
# ...and one of them is a real cell, so it can be held to a real standard
# ---------------------------------------------------------------------------
HH = "hodgkin_huxley_1952.model"


@pytest.mark.integration
def test_the_hodgkin_huxley_example_rests_where_the_paper_says(
    client, requires_easyml, requires_simulation
):
    """The gates carry no initial values, so the reader had to derive them. The
    published resting values are m~0.05, h~0.6, n~0.32, and getting those right
    is the difference between a cell at rest and one that drifts for a beat."""
    from conftest import RESOURCES_DIR

    body = upload(client, data=(RESOURCES_DIR / HH).read_bytes(), name=HH).json()
    r = client.get(f"/api/models/{body['model_id']}/variables")
    initial = r.json()["initial_values"]
    assert initial["HodgkinHuxley1952/V"] == pytest.approx(-65.0)
    assert initial["HodgkinHuxley1952/m"] == pytest.approx(0.053, abs=0.005)
    assert initial["HodgkinHuxley1952/h"] == pytest.approx(0.596, abs=0.005)
    assert initial["HodgkinHuxley1952/n"] == pytest.approx(0.318, abs=0.005)


@pytest.mark.integration
def test_the_hodgkin_huxley_example_fires_an_action_potential(
    client, requires_easyml, requires_simulation
):
    """Driven by the stimulus the import offers, the squid axon should do what
    it is famous for: a fast upstroke past 0 mV and an undershoot below rest.

    The protocol handed to the runner is the offered one, edited only in the
    ways a user would edit it -- the import cannot know a model's stimulus
    amplitude, because an EasyML file carries none.
    """
    from conftest import RESOURCES_DIR

    body = upload(client, data=(RESOURCES_DIR / HH).read_bytes(), name=HH).json()
    info = body["protocol_obs_data"]["obs_data"]["protocol_info"]
    (shape,) = info["protocol_shapes"].values()
    shape["events"][0].update({"level": -20.0, "start": 5.0, "period": 50.0})
    info["sim_times"] = [[50.0]]

    r = client.post(
        "/api/protocol/run",
        json={
            "model_id": body["model_id"],
            "protocol_info": info,
            "params": {},
            "outputs": ["HodgkinHuxley1952/V"],
        },
    )
    assert r.status_code == 200, r.text
    v = r.json()["experiments"][0]["outputs"]["HodgkinHuxley1952/V"]
    assert v[0] == pytest.approx(-65.0, abs=1.0), "should start at rest"
    assert max(v) > 0.0, f"no upstroke; peak {max(v):.1f} mV"
    assert min(v) < -66.0, f"no undershoot; minimum {min(v):.1f} mV"
