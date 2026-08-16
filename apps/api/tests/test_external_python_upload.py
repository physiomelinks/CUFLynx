"""Uploading an ``external_python`` model: the .py branch of /api/models/upload.

Unit tier — nothing here simulates, so nothing here needs circulatory_autogen or
Myokit. The model file is read by AST, which is the point (see py_model_meta).
"""

from pathlib import Path

import pytest

import main
import model_codegen
from conftest import BG_MODEL_PATH

FIXTURE = Path(__file__).resolve().parent / "data" / "heat1d_external_model.py"


def upload_py(client, source=None, filename="heat1d_external_model.py"):
    data = FIXTURE.read_bytes() if source is None else source.encode("utf-8")
    return client.post(
        "/api/models/upload",
        files={"file": (filename, data, "text/x-python")},
    )


def test_upload_returns_the_declared_metadata(client):
    resp = upload_py(client)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "Heat1D"
    assert data["params"] == ["heat/k", "heat/u_D"]
    # No states: the class owns its own integration.
    assert data["odes"] == []
    assert data["variable_count"] == 5
    # The one field that tells the client this model is run by the user's code.
    assert data["model_format"] == "external_python"


def test_a_cellml_upload_carries_no_model_format(client):
    """The field is absent, not false: its presence is the signal."""
    with open(BG_MODEL_PATH, "rb") as fh:
        resp = client.post(
            "/api/models/upload", files={"file": (BG_MODEL_PATH.name, fh, "application/xml")}
        )
    assert resp.status_code == 200, resp.text
    assert "model_format" not in resp.json()


def test_the_file_is_stored_as_py_and_verbatim(client):
    model_id = upload_py(client).json()["model_id"]
    path = main.UPLOAD_DIR / f"{model_id}.py"
    assert path.is_file()
    assert path.read_bytes() == FIXTURE.read_bytes()


def test_variables_lists_the_outputs_as_algebraic(client):
    model_id = upload_py(client).json()["model_id"]
    body = client.get(f"/api/models/{model_id}/variables").json()
    assert body["algebraic"] == ["heat/T_p1", "heat/T_p2", "heat/T_p3"]
    # Load-bearing: the sliders, the params_for_id bounds and the calibrated
    # model writer all read initial_values.
    assert body["initial_values"] == {"heat/k": 0.5, "heat/u_D": 0.0}


def test_a_py_that_is_not_a_model_is_422_with_the_reason(client):
    resp = upload_py(client, source="x = 1\n")
    assert resp.status_code == 422
    assert "SIM_HELPER" in resp.json()["detail"]


def test_a_py_with_a_slashless_name_is_422(client):
    source = (
        "class M:\n"
        "    parameters = {'k': 1.0}\n"
        "    output_names = ['heat/T']\n"
        "SIM_HELPER = M\n"
    )
    resp = upload_py(client, source=source)
    assert resp.status_code == 422
    assert "component/variable" in resp.json()["detail"]


def test_a_py_that_does_not_parse_is_422(client):
    resp = upload_py(client, source="class Broken(:\n")
    assert resp.status_code == 422
    assert "not valid Python" in resp.json()["detail"]


def test_a_py_in_a_multi_file_bundle_is_not_treated_as_a_model(client):
    """A bundle means "a model and the sisters it imports", which is a CellML
    idea; a .py arriving in one is not an external model, and must not be read
    as one."""
    resp = client.post(
        "/api/models/upload",
        files=[
            ("files", ("a.py", FIXTURE.read_bytes(), "text/x-python")),
            ("files", ("b.py", FIXTURE.read_bytes(), "text/x-python")),
        ],
    )
    assert resp.status_code == 422
    assert "SIM_HELPER" not in resp.json()["detail"]


def test_model_recovered_from_disk_when_the_registry_lost_it(client):
    """A dev-server reload wipes the in-memory registry; the .py on disk is
    re-parsed, by AST again -- recovery must not be the one path that imports."""
    model_id = upload_py(client).json()["model_id"]
    main._models.clear()
    resp = client.get(f"/api/models/{model_id}/variables")
    assert resp.status_code == 200, resp.text
    assert resp.json()["params"] == ["heat/k", "heat/u_D"]
    assert model_id in main._models
    assert main._models[model_id].path.suffix == ".py"


def test_an_unparseable_py_on_disk_still_404s(client):
    (main.UPLOAD_DIR / "deadbeef.py").write_text("class Broken(:\n")
    assert client.get("/api/models/deadbeef/variables").status_code == 404


@pytest.mark.parametrize("model_id", [None, "abc123"])
def test_resolve_model_path_is_verbatim_for_external_python(tmp_path, model_id):
    """There is nothing to generate from and nothing that could generate it: the
    path CA imports must be the path the upload wrote."""
    path = tmp_path / "user_model.py"
    path.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
    resolved = model_codegen.resolve_model_path(
        str(path), "external_python", model_id=model_id
    )
    assert resolved == str(path)


def test_resolve_model_path_still_generates_for_the_python_backend(monkeypatch):
    """The passthrough must be for external_python alone -- `python` still needs
    CA's generated module."""
    calls = []
    monkeypatch.setattr(
        model_codegen, "generate_python_model",
        lambda cellml_path, **kw: calls.append(kw) or "/generated/model.py",
    )
    assert model_codegen.resolve_model_path("/tmp/x.cellml", "python") == "/generated/model.py"
    assert calls and calls[0]["casadi_compat"] is False
