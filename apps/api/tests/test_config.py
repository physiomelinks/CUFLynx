"""Tests for the runtime CA-directory config endpoints."""


def test_get_config_shape(client):
    body = client.get("/api/config").json()
    assert {"ca_dir", "ca_src", "ca_exists"} <= set(body)


def test_set_config_repo_dir_appends_src(client, tmp_path):
    (tmp_path / "src").mkdir()
    resp = client.post("/api/config", json={"ca_dir": str(tmp_path)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ca_src"] == str(tmp_path / "src")
    assert body["ca_dir"] == str(tmp_path)
    assert body["ca_exists"] is True


def test_set_config_src_dir_used_directly(client, tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    resp = client.post("/api/config", json={"ca_dir": str(src)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ca_src"] == str(src)


def test_set_config_invalid_dir_422(client):
    assert client.post("/api/config", json={"ca_dir": "/no/such/dir"}).status_code == 422


def test_set_config_blank_resets_to_default(client, tmp_path):
    (tmp_path / "src").mkdir()
    client.post("/api/config", json={"ca_dir": str(tmp_path)})
    # Blank clears the override; ca_dir falls back to the sibling default.
    body = client.post("/api/config", json={"ca_dir": ""}).json()
    assert str(tmp_path) not in body["ca_src"]
