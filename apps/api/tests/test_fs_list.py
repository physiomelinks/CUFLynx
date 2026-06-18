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


def test_fs_mkdir_creates_folder(client, tmp_path):
    resp = client.post("/api/fs/mkdir", json={"parent": str(tmp_path), "name": "outputs"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["path"] == str(tmp_path / "outputs")
    assert (tmp_path / "outputs").is_dir()


def test_fs_mkdir_rejects_bad_name(client, tmp_path):
    for bad in ["", "a/b", "..", "."]:
        resp = client.post("/api/fs/mkdir", json={"parent": str(tmp_path), "name": bad})
        assert resp.status_code == 422, bad


def test_fs_mkdir_conflict_409(client, tmp_path):
    (tmp_path / "dup").mkdir()
    resp = client.post("/api/fs/mkdir", json={"parent": str(tmp_path), "name": "dup"})
    assert resp.status_code == 409
