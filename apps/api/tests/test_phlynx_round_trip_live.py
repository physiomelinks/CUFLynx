"""The CUFLynx -> PhLynx -> CUFLynx round trip, against PhLynx's real code.

`test_phlynx_round_trip.py` transcribes PhLynx's rules into Python so the exchange
can be reasoned about without a checkout. This runs the other half of that bargain:
it imports PhLynx's **actual** modules and pushes a real CUFLynx archive through
them. The two are complementary -- the transcription says what we believe, this
says what PhLynx does -- and when they disagree, this one is right.

That is not hypothetical. The transcription's claim that a snapshot-less archive
opens PhLynx empty was true when written and false forty minutes later, when
phlynx 98a327b ("Fix loading with no flow snapshot") replaced the library-only
fallback with `parseCellMLConnections` + `loadFromCellML`. Nothing in a
transcription can notice that; this can.

Skipped unless a PhLynx checkout is present, so the suite still runs anywhere:
set ``PHLYNX_DIR``, or keep the checkout beside CUFLynx. It also needs node and a
PhLynx new enough to have the OMEX import service at all.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
BRIDGE = Path(__file__).parent / "phlynx_bridge" / "roundtrip.mjs"
STUDY = REPO / "resources" / "3compartment.omex"


#: The module PhLynx gained in #517. Its absence is a *failure*, not a skip: a
#: PhLynx that cannot import an OMEX archive cannot be sent one, and that is the
#: state phlynx.com is in today. Skipping it would be how the exchange silently
#: stays broken -- exactly the trap the transcription-only tests fell into.
IMPORT_SERVICE = Path("src") / "services" / "import" / "omex.js"


def _phlynx_dir() -> Path | None:
    """A PhLynx checkout, whatever branch it is on, or None if there is none."""
    candidates = []
    if os.environ.get("PHLYNX_DIR"):
        candidates.append(Path(os.environ["PHLYNX_DIR"]))
    candidates.append(REPO.parent / "phlynx")
    for path in candidates:
        if (path / "src" / "services").is_dir():
            return path
    return None


#: CUFLynx's web install, which is where the bridge gets jsdom. PhLynx's own
#: vitest uses happy-dom, whose selector engine cannot match a tag name with an
#: underscore (`map_variables`) and so silently zeroes its connection parser.
WEB_DIR = REPO / "apps" / "web"


@pytest.fixture(scope="module")
def phlynx() -> Path:
    path = _phlynx_dir()
    if path is None:
        pytest.skip("no PhLynx checkout (set PHLYNX_DIR, or clone it beside CUFLynx)")
    if shutil.which("node") is None:
        pytest.skip("node is not on PATH")
    if not (path / IMPORT_SERVICE).is_file():
        pytest.fail(
            f"the PhLynx at {path} has no {IMPORT_SERVICE.as_posix()}, so it cannot "
            f"import an OMEX archive at all -- its `?open=omex` loader looks up a "
            f"literal 'model.cellml' and throws on anything else, which is every "
            f"archive CUFLynx builds. That is PhLynx `main`, and what phlynx.com "
            f"serves. The exchange needs phlynx #517."
        )
    if not (path / "node_modules" / "jszip").is_dir():
        pytest.skip(f"PhLynx at {path} has no node_modules -- run its install first")
    if not (WEB_DIR / "node_modules" / "jsdom").is_dir():
        pytest.skip("jsdom not installed in apps/web -- run the web install first")
    return path


@pytest.fixture(scope="module")
def exchanged(phlynx, tmp_path_factory) -> dict:
    """Drive the whole loop once; the tests below read its result.

    One traversal, several assertions: each leg is slow enough (a real archive, a
    node subprocess) that repeating it per assertion would make the file the
    slowest in the suite for no extra coverage.

    Its own ``TestClient`` rather than the ``client`` fixture, which is
    function-scoped so the autouse reset can run between tests. What this returns
    is plain data, already read out of the responses, so a later reset cannot
    invalidate it.
    """
    from fastapi.testclient import TestClient
    import main

    client = TestClient(main.app)
    tmp = tmp_path_factory.mktemp("phlynx_round_trip")
    out_path, back_path = tmp / "to_phlynx.omex", tmp / "back.omex"

    # --- CUFLynx assembles a study and sends it ---
    loaded = client.post(
        "/api/omex/upload",
        files={"file": (STUDY.name, STUDY.read_bytes(), "application/zip")},
    ).json()
    sent = client.post(
        "/api/phlynx/send",
        json={"model_id": loaded["model_id"], "source": "as_imported"},
    )
    assert sent.status_code == 200, sent.text
    out_path.write_bytes(base64.b64decode(sent.json()["base64"]))

    # --- PhLynx receives, renders and returns it ---
    env = {**os.environ, "PHLYNX_DIR": str(phlynx), "CUFLYNX_WEB_DIR": str(WEB_DIR)}
    proc = subprocess.run(
        ["node", str(BRIDGE), str(out_path), str(back_path)],
        capture_output=True, text=True, env=env, timeout=300,
    )
    if proc.returncode == 3:
        pytest.skip(proc.stderr.strip().splitlines()[-1])
    if proc.returncode != 0:
        pytest.fail(f"the PhLynx half failed:\n{proc.stderr[-3000:]}")

    # --- CUFLynx takes it back ---
    returned = client.post(
        "/api/omex/upload",
        files={"file": ("back.omex", back_path.read_bytes(), "application/zip")},
    )
    assert returned.status_code == 200, returned.text
    return {"sent": sent.json(), "phlynx": json.loads(proc.stdout), "returned": returned.json()}


@pytest.mark.integration
def test_phlynx_opens_what_cuflynx_sends(exchanged):
    """The first leg. phlynx.com cannot do this, which is the point of the test.

    PhLynx `main` -- what phlynx.com serves -- looks up a literal `model.cellml`
    and throws when it is absent. CUFLynx names its model after the study, so on
    `main` this leg fails before anything reaches the canvas.
    """
    assert exchanged["phlynx"]["opened"]
    assert exchanged["phlynx"]["cellml"].endswith(".cellml")


@pytest.mark.integration
def test_the_modules_reach_the_canvas(exchanged):
    """An archive can open perfectly and still leave the workspace empty.

    A CUFLynx study carries no `flow-snapshot.json` -- CUFLynx does not author
    PhLynx's editor state -- so the canvas has to be recovered from the CellML's
    own connections. That it can be is what makes "Edit" worth pressing.
    """
    assert exchanged["phlynx"]["flow_snapshot"] is None, "CUFLynx must not author a snapshot"
    nodes = exchanged["phlynx"]["canvas_nodes"]
    assert len(nodes) > 1, f"PhLynx put nothing on the canvas: {nodes}"
    assert exchanged["phlynx"]["canvas_edges"] > 0


@pytest.mark.integration
def test_the_study_survives_the_visit(exchanged):
    """The return leg: what PhLynx does not understand, it must not discard.

    obs_data and params_for_id mean nothing to PhLynx. They come back only
    because its exporter re-emits every member it preserved, and that is the
    single property this whole exchange rests on.
    """
    sent_members = set(exchanged["sent"]["members"])
    returned_members = set(exchanged["phlynx"]["returned_members"])
    assert sent_members <= returned_members, (
        f"PhLynx dropped {sorted(sent_members - returned_members)}"
    )
    assert "flow-snapshot.json" in returned_members, "PhLynx should add its own state"


@pytest.mark.integration
def test_cuflynx_can_read_its_study_back(exchanged):
    """And the study is still a study -- not merely a set of surviving bytes."""
    back = exchanged["returned"]
    assert back["obs_data"] and not back["obs_data"].get("error")
    assert back["params_for_id"] and not back["params_for_id"].get("error")
    assert back["obs_data"]["data_items"], "the observations came back empty"
    assert back["params_for_id"]["params"], "the parameters came back empty"
