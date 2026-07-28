from conftest import BG_MODEL_PATH, LV_MODEL_PATH, upload_model


def test_bg_model_variables_contains_param_and_ode(client):
    model_id = upload_model(client, BG_MODEL_PATH)["model_id"]
    resp = client.get(f"/api/models/{model_id}/variables")
    assert resp.status_code == 200
    body = resp.json()
    assert "main/alpha_o2" in body["params"]
    assert "main/p_o2" in body["odes"]
    assert "main/c_o2" in body["algebraic"]


def test_lv_variables_contains_alpha(client):
    model_id = upload_model(client, LV_MODEL_PATH)["model_id"]
    body = client.get(f"/api/models/{model_id}/variables").json()
    assert "Lotka_Volterra_module/alpha" in body["params"]
    assert "Lotka_Volterra_module/x" in body["odes"]


def test_parse_cellml_extracts_units():
    """Units come from the CellML `units` attribute on each <variable> (#125)."""
    from cellml_meta import parse_cellml

    bg = parse_cellml(BG_MODEL_PATH.read_bytes())
    assert bg.units["main/p_o2"] == "kPa"
    assert bg.units["main/alpha_o2"] == "mM_per_kPa"
    assert bg.units["main/c_o2"] == "mM"
    assert bg.units["main/t"] == "second"

    lv = parse_cellml(LV_MODEL_PATH.read_bytes())
    assert lv.units["Lotka_Volterra_module/x"] == "dimensionless"
    assert lv.units["Lotka_Volterra_module/t"] == "second"
    assert lv.units["environment/time"] == "second"


def test_parse_cellml_omits_variables_without_units():
    from cellml_meta import parse_cellml

    xml = """<?xml version="1.0"?>
    <model xmlns="http://www.cellml.org/cellml/1.1#" name="m">
      <component name="c">
        <variable name="a" units="mmHg" initial_value="1"/>
        <variable name="b" initial_value="2"/>
      </component>
    </model>"""
    m = parse_cellml(xml)
    assert m.units == {"c/a": "mmHg"}
    assert "c/b" in m.all_names
    assert m.as_dict()["units"] == {"c/a": "mmHg"}


def test_variables_endpoint_returns_units(client):
    model_id = upload_model(client, BG_MODEL_PATH)["model_id"]
    body = client.get(f"/api/models/{model_id}/variables").json()
    assert body["units"]["main/p_o2"] == "kPa"
    assert body["units"]["main/t"] == "second"


def test_variables_unknown_model_returns_404(client):
    assert client.get("/api/models/does-not-exist/variables").status_code == 404
