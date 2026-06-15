"""Tests for the server-side filesystem browser endpoint (/api/fs/list)."""


def test_fs_list_directory(client, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x")
    body = client.get("/api/fs/list", params={"path": str(tmp_path)}).json()
    assert body["path"] == str(tmp_path)
    assert body["parent"] == str(tmp_path.parent)
    names = [e["name"] for e in body["entries"]]
    # directories sort before files
    assert names == ["sub", "a.txt"]
    assert body["entries"][0]["is_dir"] is True
    assert body["entries"][1]["is_dir"] is False


def test_fs_list_dirs_only(client, tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x")
    body = client.get(
        "/api/fs/list", params={"path": str(tmp_path), "dirs_only": True}
    ).json()
    assert [e["name"] for e in body["entries"]] == ["sub"]


def test_fs_list_defaults_to_home(client):
    body = client.get("/api/fs/list").json()
    assert body["path"]  # some absolute home path
    assert "entries" in body


def test_fs_list_missing_dir_404(client, tmp_path):
    resp = client.get("/api/fs/list", params={"path": str(tmp_path / "nope")})
    assert resp.status_code == 404
