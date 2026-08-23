"""A study delivered by PhLynx over localhost (#287).

PhLynx builds a COMBINE archive and posts it to a *running* CUFLynx. The design
turns on one asymmetry that is easy to forget: **CORS stops a page reading our
responses, it does not stop it sending requests.** So anything running in the
user's browser can reach `/api/inbox`, and the endpoint must therefore *stage* the
archive rather than import it -- the confirmation in the UI is the security
control, and a route that imported on arrival would leave nothing to confirm.

The other property pinned here is that a delivered study behaves *exactly* like a
dropped one: accept returns the same body `/api/omex/upload` returns, because both
run `import_omex_bytes`. Two importers that agree today are two importers that
disagree later.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from conftest import RESOURCES_DIR

import inbox as inbox_mod
import main as main_mod

EXAMPLE = RESOURCES_DIR / "3compartment.omex"
PHLYNX_ORIGIN = "https://www.phlynx.com"


@pytest.fixture(autouse=True)
def _empty_inbox():
    """The store is process-wide, like the model registry beside it."""
    inbox_mod.inbox.clear()
    yield
    inbox_mod.inbox.clear()


def _deliver(client, data: bytes, origin: str = PHLYNX_ORIGIN, **params):
    return client.post(
        "/api/inbox", content=data, headers={"origin": origin}, params=params
    )


def _zip(members: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, blob in members.items():
            zf.writestr(name, blob)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Delivery stages, it does not import
# ---------------------------------------------------------------------------
def test_a_delivery_is_staged_and_not_loaded(client):
    resp = _deliver(client, EXAMPLE.read_bytes(), filename="3compartment")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["origin"] == PHLYNX_ORIGIN
    assert "3compartment_obs_data.json" in body["members"]
    # Nothing was imported: no model exists yet, because the user has not agreed.
    assert not main_mod._models


def test_peek_returns_metadata_only(client):
    _deliver(client, EXAMPLE.read_bytes())
    pending = client.get("/api/inbox").json()["pending"]
    assert set(pending) == {"origin", "filename", "bytes", "members"}
    assert pending["bytes"] == EXAMPLE.stat().st_size


def test_an_empty_inbox_is_not_an_error(client):
    assert client.get("/api/inbox").json()["pending"] is None


def test_the_sending_origin_is_recorded(client):
    """The dialog names it, and that naming is the whole basis on which a user can
    judge a payload they did not ask for."""
    _deliver(client, EXAMPLE.read_bytes(), origin="https://evil.example")
    assert client.get("/api/inbox").json()["pending"]["origin"] == "https://evil.example"


def test_a_delivery_with_no_origin_header_still_says_something(client):
    resp = client.post("/api/inbox", content=EXAMPLE.read_bytes())
    assert resp.status_code == 200, resp.text
    assert client.get("/api/inbox").json()["pending"]["origin"] == "an unknown page"


# ---------------------------------------------------------------------------
# Accept / reject
# ---------------------------------------------------------------------------
def test_accept_returns_exactly_what_the_upload_route_returns(client):
    """One importer, one response shape. The frontend feeds both into the same
    emits, so a difference here is a difference the UI would have to special-case."""
    with open(EXAMPLE, "rb") as fh:
        dropped = client.post(
            "/api/omex/upload", files={"file": (EXAMPLE.name, fh, "application/zip")}
        ).json()

    _deliver(client, EXAMPLE.read_bytes())
    delivered = client.post("/api/inbox/accept").json()

    assert delivered.keys() == dropped.keys() | {"delivered_from"}
    # Same study, modulo the per-upload id.
    for key in ("name", "variable_count", "model_filename"):
        assert delivered[key] == dropped[key], key
    assert delivered["model_id"] != dropped["model_id"]
    assert len(delivered["obs_data"]["data_items"]) == len(dropped["obs_data"]["data_items"])
    assert delivered["delivered_from"] == PHLYNX_ORIGIN


def test_accepting_clears_the_inbox(client):
    _deliver(client, EXAMPLE.read_bytes())
    assert client.post("/api/inbox/accept").status_code == 200
    assert client.get("/api/inbox").json()["pending"] is None
    # And a second accept has nothing to load rather than loading it twice.
    assert client.post("/api/inbox/accept").status_code == 404


def test_reject_discards_without_importing(client):
    _deliver(client, EXAMPLE.read_bytes())
    assert client.post("/api/inbox/reject").json()["discarded"] is True
    assert client.get("/api/inbox").json()["pending"] is None
    assert not main_mod._models
    # Rejecting an empty inbox is a no-op, not a 404: the user may have clicked
    # twice, and the second click means the same thing as the first.
    assert client.post("/api/inbox/reject").json()["discarded"] is False


def test_a_second_delivery_replaces_an_unaccepted_first(client):
    """One slot, not a queue: if two studies arrive before the user looks, the
    second is the one they meant, and a queue would make them dismiss a stale one
    before seeing it."""
    _deliver(client, EXAMPLE.read_bytes(), filename="first")
    second = _zip({"m.cellml": (RESOURCES_DIR / "Lotka_Volterra_forced.cellml").read_bytes()})
    _deliver(client, second, filename="second")

    pending = client.get("/api/inbox").json()["pending"]
    assert pending["filename"] == "second.omex"
    assert pending["members"] == ["m.cellml"]


# ---------------------------------------------------------------------------
# What is refused, and when
# ---------------------------------------------------------------------------
def test_an_unreadable_archive_is_refused_at_delivery(client):
    """At *delivery*, so PhLynx learns it sent something broken -- rather than at
    accept, where the user would discover it a minute later with no way to tell
    the sender."""
    resp = _deliver(client, b"not a zip at all")
    assert resp.status_code == 422
    assert client.get("/api/inbox").json()["pending"] is None


def test_an_archive_with_no_model_is_refused_at_delivery(client):
    resp = _deliver(client, _zip({"notes.txt": b"hello"}))
    assert resp.status_code == 422


def test_an_empty_body_is_refused(client):
    assert _deliver(client, b"").status_code == 422


def test_an_oversized_delivery_is_refused(client, monkeypatch):
    monkeypatch.setattr(main_mod, "MAX_INBOX_BYTES", 1024)
    resp = _deliver(client, EXAMPLE.read_bytes())
    assert resp.status_code == 413


def test_the_filename_cannot_escape_its_own_name(client):
    """It is shown in a dialog and used as a label, and it comes from a query
    string -- so it goes through the same sanitiser every other client-supplied
    stem does."""
    _deliver(client, EXAMPLE.read_bytes(), filename="../../etc/passwd")
    name = client.get("/api/inbox").json()["pending"]["filename"]
    assert "/" not in name and ".." not in name
    assert name.endswith(".omex")


# ---------------------------------------------------------------------------
# The port contract PhLynx probes
# ---------------------------------------------------------------------------
def test_health_identifies_the_app(client):
    """PhLynx probes a range of ports and must be able to tell CUFLynx from
    anything else that answers /api/health on 8787."""
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["app"] == inbox_mod.APP_NAME == "CUFLynx"
    assert body["version"] == main_mod.__version__


def test_the_receive_ports_are_a_short_ordered_range():
    """Written down once and imported by both tiers: PhLynx probes these in this
    order, and a range that drifted between the API and the shell would leave the
    app listening where nobody looks."""
    ports = inbox_mod.RECEIVE_PORTS
    assert ports[0] == inbox_mod.PREFERRED_PORT == 8787
    assert list(ports) == sorted(ports)
    assert len(ports) <= 8, "every extra port is one PhLynx must probe before giving up"


# ---------------------------------------------------------------------------
# CORS and Private Network Access -- the security boundary, so asserted
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("origin", ["https://www.phlynx.com", "https://phlynx.com"])
def test_phlynx_is_allowed_and_granted_private_network_access(client, origin):
    resp = client.options(
        "/api/inbox",
        headers={
            "origin": origin,
            "access-control-request-method": "POST",
            "access-control-request-private-network": "true",
        },
    )
    assert resp.headers.get("access-control-allow-origin") == origin
    # Chrome refuses the real request without this, however permissive CORS is.
    assert resp.headers.get("access-control-allow-private-network") == "true"


def test_an_unlisted_origin_gets_neither(client):
    resp = client.options(
        "/api/inbox",
        headers={
            "origin": "https://evil.example",
            "access-control-request-method": "POST",
            "access-control-request-private-network": "true",
        },
    )
    assert "access-control-allow-origin" not in resp.headers
    assert "access-control-allow-private-network" not in resp.headers


def test_the_allowlist_is_never_a_wildcard():
    """This is the only door into an API that is otherwise trusted because nothing
    off-machine can reach it."""
    assert "*" not in main_mod.ALLOWED_ORIGINS
    assert all(o.startswith("http") for o in main_mod.ALLOWED_ORIGINS)
