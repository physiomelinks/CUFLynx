

# ---------------------------------------------------------------------------
# Where the GUI's sensitivity plots land
# ---------------------------------------------------------------------------
def test_sa_plots_are_collected_into_their_own_directory(tmp_path):
    """circulatory_autogen draws its Sobol figures straight into the run
    directory, beside the indices, the samples and the npy files -- so what a
    user actually goes looking for ends up mixed in with what they do not."""
    import sensitivity_runner as sr

    (tmp_path / "S1_Sobol_Heatmap.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "u_lv_n64_First_order_idx.png").write_bytes(b"\x89PNG\r\n")
    (tmp_path / "results.json").write_text("{}")

    sr._collect_plots(str(tmp_path))

    plots = tmp_path / sr.SA_PLOTS_DIRNAME
    assert sorted(p.name for p in plots.glob("*.png")) == [
        "S1_Sobol_Heatmap.png",
        "u_lv_n64_First_order_idx.png",
    ]
    # Moved, not copied: two of everything is its own kind of mess.
    assert not list(tmp_path.glob("*.png"))
    # And the data is left exactly where every reader of it expects.
    assert (tmp_path / "results.json").is_file()


def test_collecting_plots_is_a_no_op_when_there_are_none(tmp_path):
    import sensitivity_runner as sr

    sr._collect_plots(str(tmp_path))
    assert not (tmp_path / sr.SA_PLOTS_DIRNAME).exists()


def test_a_failure_to_collect_plots_does_not_fail_the_run(tmp_path, monkeypatch):
    """The analysis is done and its numbers are written. Failing the run over
    where a PNG sits would be absurd."""
    import sensitivity_runner as sr

    (tmp_path / "plot.png").write_bytes(b"\x89PNG\r\n")
    monkeypatch.setattr(
        sr.Path, "mkdir", lambda *a, **k: (_ for _ in ()).throw(OSError("read-only"))
    )
    sr._collect_plots(str(tmp_path))  # must not raise


# ---------------------------------------------------------------------------
# The runner and compute_local_sensitivity must agree on what a run *is*
# ---------------------------------------------------------------------------
def _run_local_runner(monkeypatch, tmp_path, gradient_method):
    """Drive ``sensitivity_runner.run`` through the local path with CA stubbed
    out, returning (engines_built, kwargs compute_local_sensitivity received)."""
    import sys
    import types

    import local_sensitivity as ls
    import sensitivity_runner as sr

    fake_pkg = types.ModuleType("sensitivity_analysis")
    fake_mod = types.ModuleType("sensitivity_analysis.sensitivityAnalysis")
    fake_mod.SensitivityAnalysis = lambda **kwargs: object()
    monkeypatch.setitem(sys.modules, "sensitivity_analysis", fake_pkg)
    monkeypatch.setitem(sys.modules, "sensitivity_analysis.sensitivityAnalysis", fake_mod)
    monkeypatch.setattr(sr, "_ensure_ca_on_path", lambda: None)

    built = []
    monkeypatch.setattr(
        sr, "_build_local_engine", lambda *a, **k: built.append("engine") or "ENGINE"
    )
    received = {}

    def fake_compute(sa, settings, **kwargs):
        received.update(kwargs)
        # The full payload shape: the runner now writes it out in CA's
        # local-sensitivity CSV format, so a stub missing a key would fail there
        # rather than in what this test is actually about.
        return {
            "indices": {"local": {}},
            "method": "local",
            "param_names": [],
            "output_names": [],
            "gradient_method": settings.get("gradient_method"),
            "nominal": [],
            "nominal_source": "test",
        }

    monkeypatch.setattr(ls, "compute_local_sensitivity", fake_compute)

    sr.run({
        "model_path": "model.cellml",
        "obs_path": "obs.json",
        "params_path": str(tmp_path / "params.csv"),
        "output_dir": str(tmp_path / "out"),
        "model_type": "cellml_only",
        "settings": {"method": "local", "gradient_method": gradient_method},
    })
    return built, received


def test_the_runner_builds_the_fsa_engine_for_cas_auto_spelling(tmp_path, monkeypatch):
    """On cellml_only, CA's 'AUTO' resolves to FSA -- so the runner must build the
    param-id engine. It used to test the raw string against ("FSA", "CVODES"),
    while resolve_gradient_method mapped 'AUTO' to FSA only afterwards: 'AUTO'
    skipped engine construction and then failed with "needs a param-id engine".
    A test of the resolver alone passes with that bug live; only running the
    runner catches the two sites disagreeing.
    """
    built, received = _run_local_runner(monkeypatch, tmp_path, "AUTO")
    assert built, "AUTO on cellml_only means FSA, so the engine must be built"
    assert received["engine"] == "ENGINE"


def test_the_runner_still_skips_the_engine_for_fd(tmp_path, monkeypatch):
    """The engine is a full CVS0DParamID construction; FD must not pay for it."""
    built, received = _run_local_runner(monkeypatch, tmp_path, "FD")
    assert not built
    assert received["engine"] is None
