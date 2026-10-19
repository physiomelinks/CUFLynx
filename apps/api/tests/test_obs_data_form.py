"""An obs_data is an object with a protocol_info holding pre_times and sim_times.

libcuflynx reads only that form. A bare list of data items, or an object without a
``protocol_info``, is refused with the ``cuflynx-upgrade obs-data`` command that
converts it, and CUFLynx surfaces that refusal rather than running the document
with times of its own. The sites below read an already-accepted document, so
they read the object form and nothing else.
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
        "data_item_name": variable,
        "operands": [variable],
        "data_type": "constant",
        "operation": "mean",
        "unit": "J_per_m3",
        "value": 1.0,
        "std": 0.1,
    }
    item.update(kw)
    return item


BARE_LIST = [_item()]
OBJECT_FORM = {
    "protocol_info": {"pre_times": [0.0], "sim_times": [[2.0]]},
    "data_items": [_item()],
}


# ---------------------------------------------------------------------------
# The parser refuses the older forms, naming the converter
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("doc", [BARE_LIST, {"data_items": [_item()]}],
                         ids=["bare_list", "no_protocol_info"])
def test_an_obs_data_without_protocol_info_is_refused(doc):
    with pytest.raises(obs_data.ObsDataError, match="cuflynx-upgrade obs-data"):
        obs_data.parse_obs_data(doc)


@pytest.mark.parametrize("doc", [BARE_LIST, {"data_items": [_item()]}],
                         ids=["bare_list", "no_protocol_info"])
def test_the_refusal_is_cuflynxs_own_when_ca_cannot_be_asked(doc, monkeypatch):
    monkeypatch.setattr(obs_data, "_ca_parser", lambda: None)
    with pytest.raises(obs_data.ObsDataError, match="cuflynx-upgrade obs-data"):
        obs_data.parse_obs_data(doc)


def test_the_current_form_parses():
    parsed = obs_data.parse_obs_data(OBJECT_FORM)
    assert parsed.has_protocol
    assert parsed.data_items == OBJECT_FORM["data_items"]


# ---------------------------------------------------------------------------
# The shared helpers read the object form only
# ---------------------------------------------------------------------------
def test_data_items_of_reads_the_object_form_only():
    assert obs_data.data_items_of(OBJECT_FORM) == OBJECT_FORM["data_items"]
    assert obs_data.data_items_of(BARE_LIST) == []


def test_data_items_of_is_a_view_not_a_copy():
    """The MLE rewrite edits items in place and writes the document back, so the
    items handed out have to be the document's own."""
    doc = {"protocol_info": {}, "data_items": [_item()]}
    obs_data.data_items_of(doc)[0]["cost_type"] = "gaussian_MLE"
    assert doc["data_items"][0]["cost_type"] == "gaussian_MLE"


@pytest.mark.parametrize("doc", [None, "not a document", 3, {}, {"data_items": None}])
def test_data_items_of_reads_anything_else_as_no_items(doc):
    assert obs_data.data_items_of(doc) == []


def test_protocol_info_of():
    assert obs_data.protocol_info_of(OBJECT_FORM) == OBJECT_FORM["protocol_info"]
    assert obs_data.protocol_info_of(BARE_LIST) is None


# ---------------------------------------------------------------------------
# uq_runner._mle_obs_path
# ---------------------------------------------------------------------------
def test_the_mle_rewrite_stamps_cost_type_and_keeps_the_protocol(tmp_path):
    """MCMC/Laplace need ln L = -cost, so every data_item's cost_type is rewritten
    before the param_id is built."""
    obs_path = tmp_path / "obs_data.json"
    obs_path.write_text(json.dumps(OBJECT_FORM), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()

    written = uq_runner._mle_obs_path(
        {"obs_path": str(obs_path), "output_dir": str(out)}, "gaussian_MLE"
    )

    doc = json.loads(Path(written).read_text())
    assert doc["protocol_info"] == OBJECT_FORM["protocol_info"]
    assert all(it["cost_type"] == "gaussian_MLE" for it in doc["data_items"])


# ---------------------------------------------------------------------------
# The exported run_pipeline.py (CA's own script)
# ---------------------------------------------------------------------------
def _pipeline_ns(tmp_path):
    """The generated run_pipeline.py, exec'd the way the export folder runs it."""
    script = tmp_path / "run_pipeline.py"
    script.write_text(ep.render_pipeline_script(), encoding="utf-8")
    ns = {"__name__": "exported_pipeline", "__file__": str(script)}
    exec(compile(script.read_text(), str(script), "exec"), ns)  # noqa: S102
    return ns


def test_the_exported_mle_rewrite(tmp_path):
    ns = _pipeline_ns(tmp_path)
    obs_path = tmp_path / "obs_data.json"
    obs_path.write_text(json.dumps(OBJECT_FORM), encoding="utf-8")

    written = ns["mle_obs_data"](str(obs_path), str(tmp_path), "gaussian_MLE")

    items = obs_data.data_items_of(json.loads(Path(written).read_text()))
    assert items and all(it["cost_type"] == "gaussian_MLE" for it in items)


def test_the_exported_run_takes_its_window_from_the_protocol(tmp_path):
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


# ---------------------------------------------------------------------------
# The exported plot_utilities.py and panel functions
# ---------------------------------------------------------------------------
def _utilities_ns(tmp_path, out_dir):
    script = tmp_path / ep.PLOT_UTILITIES_NAME
    script.write_text(ep.render_plot_utilities(), encoding="utf-8")
    ns = {"__name__": "exported_plot_utilities", "__file__": str(script)}
    exec(compile(script.read_text(), str(script), "exec"), ns)  # noqa: S102
    ns["set_output_dir"](str(out_dir))
    return ns


def test_the_exported_plots_find_the_targets(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "run_obs_data.json").write_text(json.dumps(OBJECT_FORM), encoding="utf-8")

    ns = _utilities_ns(tmp_path, out)
    got = ns["observed"]()

    assert [o["variable"] for o in got] == ["aortic_root/u"]
    assert got[0]["value"] == 1.0


def test_the_exported_plots_read_no_targets_from_a_bare_list(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    (out / "run_obs_data.json").write_text(json.dumps(BARE_LIST), encoding="utf-8")

    assert _utilities_ns(tmp_path, out)["observed"]() == []


def test_panels_are_generated_from_the_object_form():
    src = ep.render_plotting_script(OBJECT_FORM)
    assert "aortic_root/u" in src
    assert "PANELS = [" in src


# ---------------------------------------------------------------------------
# Filling a protocol_info into an existing document
# ---------------------------------------------------------------------------
def test_a_protocol_is_filled_into_an_object_form_document(requires_obs_data_helpers):
    out = mmt_protocol.fill_protocol_info(OBJECT_FORM, {"sim_times": [[2000.0]]})

    assert out["protocol_info"]["sim_times"] == [[2000.0]]
    assert out["data_items"] == OBJECT_FORM["data_items"]


def test_a_bare_list_is_not_filled_but_refused():
    with pytest.raises(mmt_protocol.MmtProtocolError, match="cuflynx-upgrade obs-data"):
        mmt_protocol.fill_protocol_info(BARE_LIST, {"sim_times": [[1.0]]})
