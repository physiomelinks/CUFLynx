"""A bare-array obs_data.json must work everywhere the object form does.

circulatory_autogen accepts two shapes for an obs_data document: an object with
a ``data_items`` key, and a bare **array** of data_items (the data-only form --
``3compartment_obs_data.json`` ships one, and so does the heat_fenics example).
``obs_data.parse_obs_data`` has always handled both, which is what
``test_3compartment.test_bare_list_obs_data_is_still_supported`` pins.

Everything *downstream* of the parser assumed the object form, because a raw
document read back off disk never goes through the parser. Running UQ on a
data-only study therefore died in the runner:

    File "apps/api/uq_runner.py", line 120, in _mle_obs_path
        for item in obs.get("data_items", []):
    AttributeError: 'list' object has no attribute 'get'

Each test below is one such site. They read as small because the fix is one
shared helper (``obs_data.data_items_of``) rather than four isinstance checks;
what they are guarding is that no site goes back to ``.get("data_items")``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import export_pipeline as ep
import mmt_protocol
import obs_data
import pytest
import uq_runner


def _item(variable="aortic_root/u", **kw):
    """One CA data_item, minimally complete."""
    item = {
        "variable": variable,
        "operands": [variable],
        "data_type": "constant",
        "operation": "mean",
        "unit": "J_per_m3",
        "value": 1.0,
        "std": 0.1,
    }
    item.update(kw)
    return item


#: The two shapes, as the same observations. Parametrising on this is the point:
#: a site is only fixed when it answers the same for both.
BARE_LIST = [_item()]
OBJECT_FORM = {
    "protocol_info": {"pre_times": [0.0], "sim_times": [[2.0]]},
    "data_items": [_item()],
}


# ---------------------------------------------------------------------------
# The shared helper the sites go through
# ---------------------------------------------------------------------------
def test_data_items_of_reads_both_accepted_shapes():
    assert obs_data.data_items_of(BARE_LIST) == BARE_LIST
    assert obs_data.data_items_of(OBJECT_FORM) == OBJECT_FORM["data_items"]


def test_data_items_of_is_a_view_not_a_copy():
    """The MLE rewrite edits items in place and writes the document back, so the
    items handed out have to be the document's own."""
    doc = {"protocol_info": {}, "data_items": [_item()]}
    obs_data.data_items_of(doc)[0]["cost_type"] = "gaussian_MLE"
    assert doc["data_items"][0]["cost_type"] == "gaussian_MLE"


@pytest.mark.parametrize("doc", [None, "not a document", 3, {}, {"data_items": None}])
def test_data_items_of_reads_anything_else_as_no_items(doc):
    """Tolerant for consumers of an already-accepted document. parse_obs_data is
    what refuses a malformed one, with a message naming the problem."""
    assert obs_data.data_items_of(doc) == []


def test_protocol_info_of_is_none_for_the_data_only_form():
    """By definition: a bare array carries no protocol, and CUFLynx runs those
    with manual time."""
    assert obs_data.protocol_info_of(BARE_LIST) is None
    assert obs_data.protocol_info_of(OBJECT_FORM) == OBJECT_FORM["protocol_info"]


# ---------------------------------------------------------------------------
# Site 1: uq_runner._mle_obs_path -- the reported crash
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("obs", [BARE_LIST, OBJECT_FORM], ids=["bare_list", "object"])
def test_the_mle_rewrite_stamps_cost_type_on_either_shape(tmp_path, obs):
    """The exact traceback from the bug report: MCMC/Laplace need ln L = -cost, so
    every data_item's cost_type is rewritten before the param_id is built."""
    obs_path = tmp_path / "obs_data.json"
    obs_path.write_text(json.dumps(obs), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()

    written = uq_runner._mle_obs_path(
        {"obs_path": str(obs_path), "output_dir": str(out)}, "gaussian_MLE"
    )

    doc = json.loads(Path(written).read_text())
    items = obs_data.data_items_of(doc)
    assert items, "the rewrite dropped the data_items"
    assert all(it["cost_type"] == "gaussian_MLE" for it in items)


def test_the_mle_rewrite_keeps_the_shape_it_was_given(tmp_path):
    """CA reads the copy, so it must be handed the same shape the user wrote --
    a bare array must not silently become an object with no protocol_info."""
    obs_path = tmp_path / "obs_data.json"
    obs_path.write_text(json.dumps(BARE_LIST), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()

    written = uq_runner._mle_obs_path(
        {"obs_path": str(obs_path), "output_dir": str(out)}, "gaussian_MLE"
    )
    assert isinstance(json.loads(Path(written).read_text()), list)


# ---------------------------------------------------------------------------
# Site 2 + 3: the exported run_pipeline.py (its own copies -- it runs on a
# machine that has circulatory_autogen and nothing of CUFLynx)
# ---------------------------------------------------------------------------
def _pipeline_ns(tmp_path):
    """The generated run_pipeline.py, exec'd the way the export folder runs it."""
    script = tmp_path / "run_pipeline.py"
    script.write_text(ep.render_pipeline_script(), encoding="utf-8")
    ns = {"__name__": "exported_pipeline", "__file__": str(script)}
    exec(compile(script.read_text(), str(script), "exec"), ns)  # noqa: S102
    return ns


@pytest.mark.parametrize("obs", [BARE_LIST, OBJECT_FORM], ids=["bare_list", "object"])
def test_the_exported_mle_rewrite_handles_either_shape(tmp_path, obs):
    """mle_obs_data in the exported script mirrors uq_runner._mle_obs_path, and
    had the identical bug."""
    ns = _pipeline_ns(tmp_path)
    obs_path = tmp_path / "obs_data.json"
    obs_path.write_text(json.dumps(obs), encoding="utf-8")

    written = ns["mle_obs_data"](str(obs_path), str(tmp_path), "gaussian_MLE")

    items = obs_data.data_items_of(json.loads(Path(written).read_text()))
    assert items and all(it["cost_type"] == "gaussian_MLE" for it in items)


def test_the_exported_run_takes_its_window_from_the_protocol_when_there_is_one(tmp_path):
    """The simulation stage runs the same window as calibration/SA, which comes
    from the obs_data's protocol_info rather than the yaml."""
    ns = _pipeline_ns(tmp_path)
    obs_path = tmp_path / "obs_data.json"
    obs_path.write_text(json.dumps(OBJECT_FORM), encoding="utf-8")

    inp = ns["build_inp_data_dict"](
        {"file_prefix": "m", "model_file": "m.cellml", "pre_time": 9.0, "sim_time": 9.0,
         "param_id_obs_path": os.path.relpath(obs_path, tmp_path)},
        str(tmp_path),
    )
    assert (inp["pre_time"], inp["sim_time"]) == (0.0, 2.0)


def test_a_data_only_obs_data_leaves_the_exported_window_on_the_yaml(tmp_path):
    """A bare array has no protocol_info, so the yaml's times stand. Before the
    fix this raised AttributeError -- and *not* one of the exceptions the guard
    around it catches, so the exported run died on a data-only study."""
    ns = _pipeline_ns(tmp_path)
    obs_path = tmp_path / "obs_data.json"
    obs_path.write_text(json.dumps(BARE_LIST), encoding="utf-8")

    inp = ns["build_inp_data_dict"](
        {"file_prefix": "m", "model_file": "m.cellml", "pre_time": 9.0, "sim_time": 9.0,
         "param_id_obs_path": os.path.relpath(obs_path, tmp_path)},
        str(tmp_path),
    )
    assert (inp["pre_time"], inp["sim_time"]) == (9.0, 9.0)


# ---------------------------------------------------------------------------
# Site 4: the exported plot_utilities.py -- finding and reading the run's obs_data
# ---------------------------------------------------------------------------
def _utilities_ns(tmp_path, out_dir):
    script = tmp_path / ep.PLOT_UTILITIES_NAME
    script.write_text(ep.render_plot_utilities(), encoding="utf-8")
    ns = {"__name__": "exported_plot_utilities", "__file__": str(script)}
    exec(compile(script.read_text(), str(script), "exec"), ns)  # noqa: S102
    ns["set_output_dir"](str(out_dir))
    return ns


@pytest.mark.parametrize("obs", [BARE_LIST, OBJECT_FORM], ids=["bare_list", "object"])
def test_the_exported_plots_find_the_targets_in_either_shape(tmp_path, obs):
    """``latest_obs_data`` used to require a dict, so a data-only run exported
    plots with no observed targets on them at all -- a quiet wrong answer rather
    than a crash, which is worse."""
    out = tmp_path / "out"
    out.mkdir()
    (out / "run_obs_data.json").write_text(json.dumps(obs), encoding="utf-8")

    ns = _utilities_ns(tmp_path, out)
    got = ns["observed"]()

    assert [o["variable"] for o in got] == ["aortic_root/u"]
    assert got[0]["value"] == 1.0


# ---------------------------------------------------------------------------
# Site 5: the generated per-observable panel functions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("obs", [BARE_LIST, OBJECT_FORM], ids=["bare_list", "object"])
def test_panels_are_generated_from_either_shape(obs):
    src = ep.render_plotting_script(obs)
    assert "aortic_root/u" in src
    assert "PANELS = [" in src


# ---------------------------------------------------------------------------
# Site 6: filling a protocol_info into an existing document
# ---------------------------------------------------------------------------
def test_a_protocol_can_be_filled_into_a_data_only_document():
    """``dict(obs_data or {})`` raised on a bare array, so scripts/mmt_to_obs_data.py
    refused to update a data-only file. Adding a protocol turns it into the object
    form -- the only shape that can hold one -- carrying the same items."""
    out = mmt_protocol.fill_protocol_info(BARE_LIST, {"sim_times": [[2000.0]]})

    assert out["protocol_info"]["sim_times"] == [[2000.0]]
    assert out["data_items"] == BARE_LIST


def test_filling_a_protocol_does_not_mutate_the_bare_list_it_was_given():
    original = [_item()]
    mmt_protocol.fill_protocol_info(original, {"sim_times": [[1.0]]})
    assert original == [_item()], "the caller's document was rewritten in place"
