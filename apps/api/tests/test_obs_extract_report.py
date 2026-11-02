"""The record of what the extraction was made under.

Two things carry the weight here: that the ``.tex`` is always written and always
escaped (this corpus's filenames are full of underscores, so an unescaped
document does not compile at all), and that a missing ``pdflatex`` is an
ordinary outcome rather than a failure -- a frozen CUFLynx will not have TeX.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from obs_extract import config as C
from obs_extract.build import Outcome
from obs_extract.report import compile_pdf, escape, write_report

pytestmark = pytest.mark.unit


def _config():
    cfg = C.new_config("demo", "/data/Wistar")
    cfg["subprotocols"]["4AP|Kv-90"] = C.default_subprotocol("voltage")
    cfg["subprotocols"]["4AP|Kv-90"].update(used=True, study_role="calibration",
                                            sweep_limit=3, features=[{
        "operation": "min_in_range", "unit": "picoA", "unit_confirmed": True,
        "range": {"basis": "stimulus_window", "start_s": 0.1, "end_s": 0.2},
        "std": {"mode": "absolute", "value": 4.0}}])
    cfg["subprotocols"]["Rilu|Currentsteps"] = C.default_subprotocol("current")
    cfg["subprotocols"]["Rilu|Currentsteps"].update(used=True, study_role="validation")
    cfg["datasets"] = [
        C.default_dataset({"path": "/d/4AP/a_1.Kv-90.wcp",
                           "case_name": "4AP_a_1.Kv-90.wcp", "protocol": "4AP",
                           "subprotocol": "Kv-90", "format": "wcp"}),
        C.default_dataset({"path": "/d/Rilu/b.Currentsteps.wcp",
                           "case_name": "Rilu_b.Currentsteps.wcp",
                           "protocol": "Rilu", "subprotocol": "Currentsteps",
                           "format": "wcp"}),
    ]
    for d in cfg["datasets"]:
        d["used"] = True
    cfg["data_modifiers"] = [
        {"name": "liquid_junction_potential", "target": "voltage",
         "modifier": "X - 16.9"}]
    cfg["model_binding"]["measured_current_variable"] = "soma_SN/I_tot_pA"
    return cfg


def _outcome():
    out = Outcome(n_experiments=3, n_data_items=6, datasets_used=2, sweeps_used=3)
    out.skipped.append({"case_name": "4AP_c.wcp", "reason": "no stimulus", "sweep": 2})
    out.warnings.append("no stimulus was detected in one sweep")
    out.notes.append("voltage command smoothing narrowed 4x")
    return out


# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [("a_b", r"a\_b"), ("100%", r"100\%"), ("$x$", r"\$x\$"), ("a&b", r"a\&b"),
     ("#1", r"\#1"), ("{x}", r"\{x\}"), ("~", r"\textasciitilde{}"),
     ("^", r"\textasciicircum{}"), ("a\\b", r"a\textbackslash{}b")],
)
def test_escaping(raw, expected):
    assert escape(raw) == expected


def test_the_backslash_is_escaped_first():
    """Otherwise the replacements for the other nine get escaped themselves."""
    assert escape("\\_") == r"\textbackslash{}\_"


def test_none_becomes_empty_not_the_word_none():
    assert escape(None) == ""


# ---------------------------------------------------------------------------
def test_the_tex_is_written_with_every_section(tmp_path):
    result = write_report(_config(), _outcome(), str(tmp_path))
    assert os.path.isfile(result.tex_path)
    text = open(result.tex_path).read()

    assert "\\begin{document}" in text and "\\end{document}" in text
    for section in ("Source", "Model binding", "Data modifiers",
                    "Calibration datasets", "Validation datasets",
                    "Feature selection", "Extraction outcome"):
        assert section in text, section


def test_the_binding_is_recorded(tmp_path):
    """The same recording bound to a different variable is a different
    measurement, and this is the only place that choice is written down."""
    text = open(write_report(_config(), _outcome(), str(tmp_path)).tex_path).read()
    assert "soma\\_SN/I\\_tot\\_pA" in text


def test_the_modifiers_are_recorded(tmp_path):
    text = open(write_report(_config(), _outcome(), str(tmp_path)).tex_path).read()
    assert "liquid\\_junction\\_potential" in text
    assert "X - 16.9" in text


def test_no_modifiers_says_so_rather_than_omitting_the_section(tmp_path):
    cfg = _config()
    cfg["data_modifiers"] = []
    text = open(write_report(cfg, _outcome(), str(tmp_path)).tex_path).read()
    assert "used as they were recorded" in text


def test_datasets_are_split_by_study_role(tmp_path):
    text = open(write_report(_config(), _outcome(), str(tmp_path)).tex_path).read()
    assert "Calibration datasets (1)" in text
    assert "Validation datasets (1)" in text


def test_the_outcome_section_says_what_was_skipped_and_why(tmp_path):
    """The section the CLI's report has no equivalent of. "Why are there only 40
    items?" has no other answer."""
    text = open(write_report(_config(), _outcome(), str(tmp_path)).tex_path).read()
    assert "4AP\\_c.wcp" in text
    assert "no stimulus" in text
    assert "sweep 2" in text
    assert "narrowed 4x" in text


def test_a_thumbnail_is_included_when_there_is_one(tmp_path):
    thumb = tmp_path / "a.png"
    thumb.write_bytes(b"")
    result = write_report(_config(), _outcome(), str(tmp_path),
                          thumbnails={"4AP_a_1.Kv-90.wcp": str(thumb)})
    text = open(result.tex_path).read()
    assert "\\includegraphics" in text
    assert "no preview" in text, "the dataset without one says so"


def test_the_filenames_in_this_corpus_do_not_break_the_document(tmp_path):
    """Every real filename here has underscores and dots in it."""
    cfg = _config()
    cfg["datasets"][0]["case_name"] = "4AP_200926_005.1.1..1.1.1.UniqueAp.1.wcp"
    text = open(write_report(cfg, _outcome(), str(tmp_path)).tex_path).read()
    assert "4AP\\_200926\\_005" in text
    # Every underscore in the document body is a preceded by a backslash. TeX
    # would otherwise read one as a subscript and refuse the file outright.
    for i, char in enumerate(text):
        if char == "_":
            assert i and text[i - 1] == "\\", f"unescaped underscore at {i}"


def test_the_feature_range_is_shown_in_the_seconds_that_were_typed(tmp_path):
    text = open(write_report(_config(), _outcome(), str(tmp_path)).tex_path).read()
    assert "0.1--0.2 s" in text


def test_the_clamp_timing_is_recorded_per_group(tmp_path):
    """A reader has to be able to tell which sub-experiment a number came from."""
    text = open(write_report(_config(), _outcome(), str(tmp_path)).tex_path).read()
    assert "Stimulus sub-experiment" in text


# ---------------------------------------------------------------------------
def test_no_pdflatex_is_information_not_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    logged = []
    result = write_report(_config(), _outcome(), str(tmp_path), log=logged.append)

    assert os.path.isfile(result.tex_path), "the .tex is always written"
    assert result.pdf_path is None
    assert any("[info]" in line for line in logged)
    assert not any("[error]" in line for line in logged)
    assert any("compiled anywhere" in n for n in result.notes)


def test_a_failing_pdflatex_keeps_the_tex_and_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/pdflatex")

    def failing(*_a, **_k):
        return subprocess.CompletedProcess([], 1, stdout="! Undefined control sequence.\n", stderr="")

    monkeypatch.setattr(subprocess, "run", failing)
    logged = []
    result = write_report(_config(), _outcome(), str(tmp_path), log=logged.append)

    assert os.path.isfile(result.tex_path)
    assert result.pdf_path is None
    assert any("[warning]" in line for line in logged)
    assert any("Undefined control sequence" in n for n in result.notes)


def test_a_hanging_pdflatex_is_given_up_on(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/pdflatex")

    def hanging(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="pdflatex", timeout=60)

    monkeypatch.setattr(subprocess, "run", hanging)
    pdf, notes = compile_pdf(str(tmp_path / "x.tex"))
    assert pdf is None
    assert any("timed out" in n for n in notes)


def test_pdflatex_runs_twice_for_longtable(tmp_path, monkeypatch):
    """One pass leaves longtable's column widths unsettled -- which is why the
    CLI's first page is misaligned."""
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/pdflatex")
    calls = []

    def ok(cmd, **_k):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", ok)
    compile_pdf(str(tmp_path / "x.tex"))
    assert len(calls) == 2


def test_compilation_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/bin/pdflatex")
    cfg = _config()
    cfg["report"]["compile_pdf"] = False
    result = write_report(cfg, _outcome(), str(tmp_path))
    assert result.pdf_path is None
    assert any("turned off" in n for n in result.notes)


@pytest.mark.integration
def test_the_document_compiles_with_a_real_pdflatex(tmp_path):
    if shutil.which("pdflatex") is None:
        pytest.skip("pdflatex is not installed")
    result = write_report(_config(), _outcome(), str(tmp_path))
    assert result.pdf_path and os.path.isfile(result.pdf_path), result.notes
