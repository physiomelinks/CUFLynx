

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
