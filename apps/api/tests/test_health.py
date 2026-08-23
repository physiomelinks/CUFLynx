from inbox import APP_NAME
from version import __version__


def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "app": APP_NAME, "version": __version__}


def test_health_says_which_app_is_answering(client):
    """PhLynx finds a running CUFLynx by probing a small range of ports (#287).
    Without a marker it could not tell us from anything else that answers
    /api/health on 8787 -- and would post a study at it."""
    body = client.get("/api/health").json()
    assert body["app"] == "CUFLynx"
    assert body["version"]


def test_cors_header(client):
    resp = client.get(
        "/api/health",
        headers={"Origin": "http://localhost:5173"},
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"
