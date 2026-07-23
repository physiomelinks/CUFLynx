"""Tests for the bundled example-model route (issue #91).

The "Start" dialog fetches an example CellML file and feeds it through the
normal upload flow, so the backend only needs to serve the bundled resource.
"""

from __future__ import annotations


def test_example_model_served(client):
    resp = client.get("/api/examples/3compartment")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("application/xml")
    # It is the real bundled CellML document, not an empty/placeholder body.
    assert b"<model" in resp.content
    assert len(resp.content) > 0


def test_unknown_example_is_404(client):
    resp = client.get("/api/examples/does-not-exist")
    assert resp.status_code == 404
