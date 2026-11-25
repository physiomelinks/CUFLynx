"""PhLynx's generated components are found by id, not by name (#422, PhLynx #1189).

PhLynx gives each component it generates an id ``phlynx_autogen_*``; when a modeller's own
component already has the name, PhLynx suffixes its own (``instance_parameters_1``). A model with
no such id is read by name, as before.
"""
import os
import re
from pathlib import Path

import pytest

import omex_export
import phlynx_components
from calibrated_model import calibrated_cellml
from cellml_meta import parse_cellml
from params_for_id import parse_params_for_id

#: PhLynx's export with a modeller's component called instance_parameters: PhLynx's own is
#: instance_parameters_1, known by its id. Both hold a constant C_aortic_root and a C.
CLASH = """<?xml version="1.0" encoding="UTF-8"?>
<model xmlns="http://www.cellml.org/cellml/2.0#" name="clash">
  <component name="instance_parameters" id="modellers_own">
    <variable name="C_aortic_root" units="dimensionless" initial_value="1"/>
    <variable name="C" units="dimensionless" initial_value="10"/>
  </component>
  <component name="instance_parameters_1" id="phlynx_autogen_instance_parameters_0001">
    <variable name="C_aortic_root" units="dimensionless" initial_value="2"/>
    <variable name="C" units="dimensionless" initial_value="20"/>
  </component>
  <component name="global_parameters" id="phlynx_autogen_global_parameters_0001">
    <variable name="R" units="dimensionless" initial_value="8.3"/>
  </component>
  <component name="phlynx_environment" id="phlynx_autogen_environment_0001">
    <variable name="time" units="second"/>
  </component>
</model>
"""

#: The same layout as PhLynx wrote it before ids: known by name.
NO_IDS = CLASH.replace(' id="modellers_own"', "").replace(
    ' id="phlynx_autogen_instance_parameters_0001"', "").replace(
    ' id="phlynx_autogen_global_parameters_0001"', "").replace(
    ' id="phlynx_autogen_environment_0001"', "")


def _params(*targets):
    return {"version": 1, "params": [{"targets": [t], "min": 0.0, "max": 100.0} for t in targets]}


def test_a_phlynx_model_s_parameter_components_are_the_ones_its_ids_name():
    meta = parse_cellml(CLASH)
    assert meta.component_ids["instance_parameters_1"] == "phlynx_autogen_instance_parameters_0001"
    assert sorted(meta.parameter_components) == ["global_parameters", "instance_parameters_1"]
    assert phlynx_components.actual_names(meta.component_ids) == {
        "instance_parameters": "instance_parameters_1", "global_parameters": "global_parameters",
        "phlynx_environment": "phlynx_environment"}


def test_a_model_without_phlynx_ids_is_read_by_name():
    meta = parse_cellml(NO_IDS)
    assert meta.parameter_components == phlynx_components.PARAMETER_NAMES
    assert phlynx_components.actual_names(meta.component_ids) == {}


#: without the plain C, aortic_root/C is only found under its bare name C_aortic_root
WITHOUT_C = re.sub(r'\s*<variable name="C" [^>]*/>', "", CLASH)


@pytest.mark.parametrize("model, expected", [(CLASH, 20.0), (WITHOUT_C, 2.0)],
                         ids=["ca-candidate", "bare-name"])
def test_a_value_resolves_into_phlynx_s_component_not_the_modeller_s(model, expected):
    """Both ways of finding aortic_root/C land in PhLynx's instance_parameters_1:
    circulatory_autogen's candidate ``instance_parameters/C`` (a PhLynx name, so PhLynx's
    component), and, without a C, the bare name C_aortic_root that both components have."""
    meta = parse_cellml(model)
    (entry,) = parse_params_for_id(_params("aortic_root/C"), meta.initial_values, meta.component_ids)
    assert entry.initial_value == expected


def test_without_ids_the_name_still_decides():
    meta = parse_cellml(NO_IDS)
    (entry,) = parse_params_for_id(_params("aortic_root/C"), meta.initial_values, meta.component_ids)
    assert entry.initial_value == 10.0


def test_a_calibrated_value_is_written_into_phlynx_s_component():
    text, report = calibrated_cellml(WITHOUT_C, {"aortic_root/C": 5.0})
    assert report["resolved"] == {"aortic_root/C": "instance_parameters_1/C_aortic_root"}
    meta = parse_cellml(text)
    assert meta.initial_values["instance_parameters_1/C_aortic_root"] == 5.0
    assert meta.initial_values["instance_parameters/C_aortic_root"] == 1.0


def test_sending_back_warns_about_a_value_in_the_modeller_s_component():
    """PhLynx reads parameter changes back from its own components only: a value written into
    the modeller's instance_parameters is one it will not pick up, though it is so named."""
    components = parse_cellml(CLASH).parameter_components
    report = {"resolved": {"a/C": "instance_parameters/C", "b/C": "instance_parameters_1/C"}}
    assert omex_export.outside_parameter_components(report, components) == [
        "a/C -> instance_parameters/C"]
    _, sent = omex_export.build_archive(cellml_text=CLASH, values={"aortic_root/C": 5.0})
    assert sent["outside_parameters"] == []


def _phlynx_dir():
    for path in (os.environ.get("PHLYNX_DIR"), Path(__file__).resolve().parents[3].parent / "phlynx",
                 Path.home() / "Documents" / "git_projects" / "phlynx"):
        if path and (Path(path) / "src" / "utils" / "constants.js").is_file():
            return Path(path)
    return None


def test_the_table_is_phlynx_s():
    """The ids are PhLynx's contract: compare with ``PHLYNX_GENERATED_COMPONENTS`` in a PhLynx
    checkout (``PHLYNX_DIR``, or one beside CUFLynx)."""
    phlynx = _phlynx_dir()
    if phlynx is None:
        pytest.skip("no PhLynx checkout (set PHLYNX_DIR)")
    text = (phlynx / "src" / "utils" / "constants.js").read_text()
    block = re.search(r"PHLYNX_GENERATED_COMPONENTS = Object\.freeze\(\{(.*?)\}\)", text, re.S)
    if block is None:
        pytest.skip("this PhLynx predates generated-component ids (PhLynx #1189)")
    names = dict(re.findall(r"export const (\w+) = '([^']*)'", text))
    found = {}
    for key, name, cid in re.findall(r"(\w+): \{ name: ([\w']+), id: '([^']+)' \}", block.group(1)):
        found[key] = (name.strip("'") if name.startswith("'") else names[name], cid)
    assert found == phlynx_components.GENERATED
