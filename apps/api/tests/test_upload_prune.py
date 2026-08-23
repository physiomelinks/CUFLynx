"""Tests for ageing out /tmp/cuflynx_uploads.

The upload dir is deliberately persistent — a model is re-derived from its
.cellml after a reload, and calib_/sa_/uq_ run dirs are read back for results —
so nothing deleted from it during a session. On a long-lived server that grew
without bound in a temp dir the OS only clears at boot.
"""

import os

import main as main_mod


def _age(path, days):
    """Backdate *path* by *days*, the way an upload from a past session looks."""
    old = os.stat(path).st_mtime - days * 86400
    os.utime(path, (old, old))


def test_entries_past_the_ttl_are_removed(tmp_path):
    stale = tmp_path / "stale.cellml"
    stale.write_text("<model/>")
    _age(stale, 30)

    assert main_mod.prune_upload_dir(tmp_path, ttl_days=7) == 1
    assert not stale.exists()


def test_recent_entries_are_kept(tmp_path):
    fresh = tmp_path / "fresh.cellml"
    fresh.write_text("<model/>")

    assert main_mod.prune_upload_dir(tmp_path, ttl_days=7) == 0
    assert fresh.exists()


def test_stale_run_directories_are_removed_whole(tmp_path):
    # calib_/sa_/uq_ output dirs are directories, not files: unlink() alone
    # would leave every one of them behind.
    run_dir = tmp_path / "calib_abc123_deadbeef"
    (run_dir / "case_1").mkdir(parents=True)
    (run_dir / "case_1" / "best_cost.npy").write_bytes(b"\x00")
    _age(run_dir / "case_1" / "best_cost.npy", 30)
    _age(run_dir / "case_1", 30)
    _age(run_dir, 30)

    assert main_mod.prune_upload_dir(tmp_path, ttl_days=7) == 1
    assert not run_dir.exists()


def test_a_ttl_of_zero_disables_the_prune(tmp_path):
    ancient = tmp_path / "ancient.cellml"
    ancient.write_text("<model/>")
    _age(ancient, 3650)

    assert main_mod.prune_upload_dir(tmp_path, ttl_days=0) == 0
    assert ancient.exists()


def test_a_missing_directory_is_not_an_error(tmp_path):
    assert main_mod.prune_upload_dir(tmp_path / "not_here", ttl_days=7) == 0


def test_the_default_ttl_applies_when_none_is_passed(tmp_path):
    # The startup call passes neither argument, so the module default has to be
    # a finite age rather than "keep everything".
    assert 0 < main_mod.UPLOAD_TTL_DAYS < 3650

    stale = tmp_path / "stale.cellml"
    stale.write_text("<model/>")
    _age(stale, main_mod.UPLOAD_TTL_DAYS + 1)

    assert main_mod.prune_upload_dir(tmp_path) == 1
    assert not stale.exists()


def test_a_stored_source_archive_ages_out_like_any_upload(tmp_path):
    """The `.omex` a study was loaded from is kept whole so it can be sent back
    to PhLynx (#290). It is the largest thing in the uploads dir, so it must be
    swept by the same TTL rather than accumulating forever."""
    archive = tmp_path / f"abc123{main_mod.MODEL_ARCHIVE_SUFFIX}"
    archive.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    _age(archive, 30)

    assert main_mod.prune_upload_dir(tmp_path, ttl_days=7) == 1
    assert not archive.exists()
