"""Filesystem failures during an export must say what failed and why (issue #135).

`POST /api/export/pipeline` and `/api/export/plotting` do their filesystem work
unguarded, so an OSError -- an unwritable outputs dir, a full disk, a path made
too long by the dated export folder -- propagated and Starlette answered a
body-less 500. The frontend's `detail` lookup then fell through to Axios's own
text, and the user saw only:

    Export failed: AxiosError: Request failed with status code 500

which names neither the path nor the reason. Same symptom as #133, different
cause, and equally undiagnosable.
"""

from __future__ import annotations

import errno
import os
import stat
import sys

import main as main_mod
import pytest
from conftest import LV_MODEL_PATH, upload_model


def _model(client) -> str:
    return upload_model(client, LV_MODEL_PATH)["model_id"]


def _export(client, outputs_dir, model_id):
    return client.post(
        "/api/export/pipeline",
        json={
            "model_id": model_id,
            "file_prefix": "lotka_volterra",
            "sim_time": 2.0,
            "config_outputs_dir": str(outputs_dir),
        },
    )


@pytest.fixture
def readonly_dir(tmp_path):
    """A directory the server cannot write into."""
    d = tmp_path / "readonly"
    d.mkdir()
    d.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x------
    yield d
    d.chmod(stat.S_IRWXU)  # so tmp_path cleanup can remove it


needs_posix_perms = pytest.mark.skipif(
    sys.platform.startswith("win") or os.geteuid() == 0,
    reason="directory permissions are not enforced for this platform/user",
)


# ---------------------------------------------------------------------------
# The route
# ---------------------------------------------------------------------------
@needs_posix_perms
def test_export_to_an_unwritable_dir_reports_the_path_and_reason(client, readonly_dir):
    resp = _export(client, readonly_dir, _model(client))

    # A body at all -- this is the whole bug.
    assert resp.status_code != 200
    body = resp.json()
    assert "detail" in body, body
    detail = body["detail"]

    assert str(readonly_dir) in detail  # which path
    assert "Permission denied" in detail or "permission" in detail.lower()  # why
    assert "not writable" in detail  # what to do about it
    assert "AxiosError" not in detail


@needs_posix_perms
def test_a_client_chosen_directory_is_the_clients_to_fix(client, readonly_dir):
    """422, not 500: config_outputs_dir is what needs changing."""
    assert _export(client, readonly_dir, _model(client)).status_code == 422


@needs_posix_perms
def test_the_plotting_export_is_guarded_too(client, readonly_dir):
    resp = client.post(
        "/api/export/plotting", json={"config_outputs_dir": str(readonly_dir)}
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert str(readonly_dir) in detail
    assert "plotting script" in detail


def test_a_failure_in_the_servers_own_temp_dir_is_a_500(client, monkeypatch, tmp_path):
    """Blank config_outputs_dir means we chose the location, so its failure is
    not the client's to fix -- but it still owes a reason."""
    def boom(*_a, **_kw):
        raise PermissionError(errno.EACCES, "Permission denied", str(tmp_path / "x"))

    monkeypatch.setattr(main_mod.Path, "mkdir", boom)
    resp = client.post(
        "/api/export/pipeline",
        json={"model_id": _model(client), "file_prefix": "lv", "sim_time": 2.0},
    )
    assert resp.status_code == 500
    assert "Permission denied" in resp.json()["detail"]


def test_a_failure_writing_the_scripts_is_caught_too(client, monkeypatch, tmp_path):
    """The yaml/script writes happen after the copies, in a separate block."""
    real = main_mod.Path.write_text

    def boom(self, *a, **kw):
        if self.name == "run_pipeline.py":
            raise OSError(errno.ENOSPC, "No space left on device", str(self))
        return real(self, *a, **kw)

    monkeypatch.setattr(main_mod.Path, "write_text", boom)
    resp = _export(client, tmp_path, _model(client))
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert "run_pipeline.py" in detail
    assert "disk is full" in detail


def test_a_successful_export_is_unaffected(client, tmp_path):
    resp = _export(client, tmp_path, _model(client))
    assert resp.status_code == 200, resp.text
    assert os.path.isdir(resp.json()["export_dir"])


# ---------------------------------------------------------------------------
# Message building
# ---------------------------------------------------------------------------
def test_detail_names_the_path_that_actually_failed(tmp_path):
    """OSError.filename is the failing path, which with parents=True is often a
    parent rather than the one we asked for."""
    exc = PermissionError(errno.EACCES, "Permission denied", "/locked/parent")
    detail = main_mod._fs_error_detail(exc, "write the export to", tmp_path / "asked/for")
    assert "/locked/parent" in detail
    assert "asked/for" not in detail


def test_detail_falls_back_when_the_error_names_no_path(tmp_path):
    detail = main_mod._fs_error_detail(OSError("something odd"), "write to", tmp_path)
    assert str(tmp_path) in detail
    assert "something odd" in detail


@pytest.mark.parametrize(
    "code,expected",
    [
        (errno.EACCES, "not writable"),
        (errno.EROFS, "read-only"),
        (errno.ENOSPC, "disk is full"),
        (errno.ENAMETOOLONG, "too long"),
        (errno.ENOENT, "parent directory"),
        (errno.ENOTDIR, "not a directory"),
    ],
)
def test_each_common_failure_gets_an_actionable_hint(code, expected, tmp_path):
    exc = OSError(code, os.strerror(code), str(tmp_path / "f"))
    assert expected in main_mod._fs_error_detail(exc, "write to", tmp_path)


def test_an_unrecognised_errno_still_reports_path_and_reason(tmp_path):
    """No hint is better than a wrong one, but the facts must survive."""
    exc = OSError(errno.EIO, "Input/output error", str(tmp_path / "f"))
    detail = main_mod._fs_error_detail(exc, "write to", tmp_path)
    assert "Input/output error" in detail
    assert str(tmp_path / "f") in detail
