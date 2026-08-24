"""Open a directory circulatory_autogen actually produced.

Every other test of the loader uses fixtures written here, which agree with the
reader by construction: they encode what this app *believes* CA writes. If CA
renames a file or moves a stage's output, those fixtures keep passing and the app
stops being able to open a real folder.

So this one asks CA to build a run -- sensitivity, an emulator, a calibration, a
chain and a posterior predictive check, all tiny -- and then points the loader at
the result. The builder is shipped in the engine
(``libcuflynx.external_testing.full_pipeline_run``) precisely so that both sides
check against one real run rather than against each other's assumptions; CA's own
test asserts the same directory from the writing side. CA's wheel carries no
``tests/``, so a builder kept there would be unreachable from here.

Skips, rather than fails, when the engine has no builder or is missing the
optional extras. A CUFLynx that cannot reach a full-featured CA has not broken;
it simply cannot run this check.
"""
import os

import pytest

import load_outputs

pytestmark = [pytest.mark.integration, pytest.mark.slow]


def _builder():
    """CA's run builder, or a reason to skip."""
    pytest.importorskip("autoemulate", reason="the emulator stage needs CA's [emulation]")
    pytest.importorskip("emcee", reason="the chain needs CA's [uq]")
    try:
        from ca_imports import ca_from
        return ca_from("external_testing.full_pipeline_run", "build_full_pipeline_run")
    except Exception as exc:  # noqa: BLE001 - an older CA simply has no builder
        pytest.skip("this circulatory_autogen has no full-pipeline builder: %s" % exc)


def _resources_dir():
    from ca_imports import ca_paths

    for path in ca_paths():
        candidate = os.path.join(os.path.dirname(path), "resources")
        if os.path.isdir(candidate):
            return candidate
    pytest.skip("no circulatory_autogen resources directory found")


@pytest.fixture(scope="module")
def real_run(tmp_path_factory):
    """One real run, built by CA, read by every test below."""
    build = _builder()
    output_dir = str(tmp_path_factory.mktemp("ca_run"))
    result = build(
        output_dir=output_dir,
        resources_dir=_resources_dir(),
        generated_models_dir=os.path.join(output_dir, "generated_models"),
    )
    if result is None:
        pytest.skip("not rank 0")
    return {"output_dir": output_dir, "result": result}


@pytest.fixture(scope="module")
def loaded(real_run):
    return load_outputs.load_outputs(
        real_run["output_dir"], file_prefix=real_run["result"]["config"]["file_prefix"])


# --- the panels that should have something to show ------------------------------

def test_the_run_is_found_at_all(loaded, real_run):
    """CA names the run directory itself; the loader has to find it the same way."""
    assert loaded["run_dir"] == real_run["result"]["run_dir"]
    assert not loaded.get("error")


def test_nothing_failed_to_read(loaded):
    """A reader that quietly degrades is worse than one that fails: the panel is
    simply empty and nobody knows why."""
    assert loaded["missing"] == []


@pytest.mark.parametrize("section", ["calibration", "uq"])
def test_the_expected_sections_are_found(loaded, section):
    assert section in loaded["found"], loaded["found"]


def test_the_calibration_panel_has_parameters_to_show(loaded):
    best = loaded["calibration"]["best"]
    assert best["params"], "no best-fit parameters"
    assert all(isinstance(name, str) for name in best["params"])


def test_the_uq_panel_has_a_posterior_per_parameter(loaded, real_run):
    """This is the one that used to come back empty: uq_posterior_samples.npy is
    written by our runner, and a CA run has the chain instead."""
    params = loaded["uq"]["params"]
    assert params, "no posterior -- the chain was not read"
    for entry in params:
        assert entry["qname"]
        assert entry["bins"] and entry["counts"], "nothing for the panel to draw"
        assert entry["q05"] <= entry["q50"] <= entry["q95"]


def test_the_coverage_panel_has_both_numbers_at_both_levels(loaded):
    levels = loaded["uq"]["coverage"]["coverage"]["levels"]
    for level in ("0.8", "0.95"):
        row = levels[level]
        assert 0.0 <= row["predictive_coverage"] <= 1.0
        assert 0.0 <= row["sample_interval_coverage"] <= 1.0


def test_the_coverage_says_it_used_the_solver(loaded):
    """An emulator scoring its own predictions cannot report that it is wrong, so
    the panel captions this -- and the caption has to be true."""
    assert loaded["uq"]["coverage"]["used_emulator"] is False


def test_the_predictive_and_trace_artefacts_are_seen(loaded):
    assert loaded["uq"]["has_posterior_predictive"] is True
    assert loaded["uq"]["has_sample_traces"] is True


def test_the_emulator_panel_has_its_metadata(loaded):
    metadata = loaded["emulator"]["metadata"]
    assert metadata, "the emulator trained but its metadata was not read"
    assert metadata.get("feature_r2"), "no per-feature error for the panel to plot"


def test_the_run_is_one_of_the_listed_runs(loaded):
    """A directory can hold several; the one that was read must be among them."""
    assert loaded["run_dirs"]
    assert any(run["path"] == loaded["run_dir"] for run in loaded["run_dirs"])
