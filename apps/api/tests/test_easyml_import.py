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
