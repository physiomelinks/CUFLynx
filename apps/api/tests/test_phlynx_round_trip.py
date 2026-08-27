"""The whole exchange, end to end: PhLynx -> calibrate -> PhLynx (#287/#290).

Everything else about the OMEX contract is tested a piece at a time --
`test_omex_import.py` for what CUFLynx reads, `test_omex_export.py` for what it
writes. This file is the one that runs the *journey*: a study arrives as the
archive PhLynx sends, a real calibration moves its parameters, and the archive
that goes back has the new values in the model and PhLynx's own files untouched.

That ordering is the point. Each half passes on its own while the round trip is
still broken -- a writer that preserves every member is worth nothing if the
values never reach the model, and values in the model are worth nothing if
PhLynx's workspace does not survive the trip. The failure this guards against is
the one where both unit suites are green and the user gets their model back with
the calibration missing, or their workspace gone.

The tier is `integration` because the calibration is real: a short genetic
algorithm on the 3compartment model through circulatory_autogen and Myokit.

Most of it builds the archive to #287's *written* contract, which proves CUFLynx
honours the spec and proves nothing about whether the spec matches what PhLynx
actually serialises. The last two tests close that gap by running the same journey
against PhLynx's **own bytes**, from `resources/phlynx_export.omex`.

**Do not merge this until that fixture exists.** It is the whole point: a green
suite with no real export is exactly the state #287 was written to end.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path

import omex_import
import pytest
from cellml_meta import parse_cellml
from conftest import RESOURCES_DIR, SN_OBS_DATA_PATH, SN_PARAMS_CSV_PATH
from params_for_id import parse_params_for_id

from test_calibration import (
    C3_MODEL_PATH,
    C3_OBS_DATA_PATH,
    C3_PARAMS_CSV_PATH,
    _wait,
)

#: Where a real PhLynx export lives. Not a fixture we can author: the whole point
#: of it is to be PhLynx's own bytes, not our rendering of their contract.
REAL_PHLYNX_EXPORT = RESOURCES_DIR / "phlynx_export.omex"

#: PhLynx's editor state, as #287 specifies it. Opaque to CUFLynx by design --
#: these are here to be returned unread, not to be understood.
FLOW_JSON = json.dumps(
    {
        "id": "phlynx-flow",
        "version": "1.0.0",
        "nodes": [{"id": "heart", "type": "module"}, {"id": "aortic_root", "type": "vessel"}],
        "edges": [{"source": "heart", "target": "aortic_root"}],
    },
    indent=2,
)
CHANGES_JSON = json.dumps({"id": "phlynx-changes", "version": "1.0.0", "modified": True})

MANIFEST = """<?xml version="1.0" encoding="UTF-8"?>
<omexManifest xmlns="http://identifiers.org/combine.specifications/omex-manifest">
  <content location="." format="http://identifiers.org/combine.specifications/omex"/>
  <content location="./manifest.xml" format="http://identifiers.org/combine.specifications/omex-manifest"/>
  <content location="./document.sedml" format="http://identifiers.org/combine.specifications/sed-ml" master="true"/>
  <content location="./model.cellml" format="http://identifiers.org/combine.specifications/cellml"/>
  <content location="./simulation.json" format="http://purl.org/NET/mediatypes/application/json"/>
  <content location="./flow.json" format="application/x.vnd.phlynx-flow+json"/>
  <content location="./changes.json" format="application/x.vnd.phlynx-changes+json"/>
  <content location="./3compartment_obs_data.json" format="application/json"/>
  <content location="./3compartment_params_for_id.csv" format="text/csv"/>
</omexManifest>
"""

#: The members PhLynx owns. None of them is CUFLynx's to read, write or drop, so
#: every one is checked byte-for-byte on the way back.
PHLYNX_OWNED = ("flow.json", "changes.json", "document.sedml", "simulation.json")


def phlynx_export_bytes() -> bytes:
    """The archive PhLynx sends, built to the #287 contract.

    A stand-in for `resources/phlynx_export.omex` until PhLynx can produce one:
    it carries the same members in the same roles, so everything CUFLynx does with
    it is exercised. What it cannot stand in for is PhLynx's actual serialisation,
    which is why the fixture it substitutes for is still asserted below.
    """
    members = {
        "manifest.xml": MANIFEST,
        "model.cellml": C3_MODEL_PATH.read_bytes(),
        "document.sedml": '<?xml version="1.0"?><sedML level="1" version="3"/>',
        "simulation.json": json.dumps({"plots": [], "settings": {"dt": 0.01}}),
        "flow.json": FLOW_JSON,
        "changes.json": CHANGES_JSON,
        "3compartment_obs_data.json": C3_OBS_DATA_PATH.read_bytes(),
        "3compartment_params_for_id.csv": C3_PARAMS_CSV_PATH.read_bytes(),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _members(blob: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return {n: zf.read(n) for n in zf.namelist()}


def _receive(client, archive: bytes) -> dict:
    """Drop the archive on CUFLynx the way the browser does."""
    resp = client.post(
        "/api/omex/upload", files={"file": ("phlynx.omex", archive, "application/zip")}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _send_back(client, model_id: str, **payload) -> dict:
    resp = client.post("/api/phlynx/send", json={"model_id": model_id, **payload})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _decoded(sent: dict) -> bytes:
    import base64

    return base64.b64decode(sent["base64"])


# ---------------------------------------------------------------------------
# Receiving
# ---------------------------------------------------------------------------
def test_a_phlynx_study_arrives_whole(client):
    """Model, observations and parameters in one drop, and no banner for the
    files CUFLynx does not understand -- which is what a PhLynx archive is mostly
    made of."""
    body = _receive(client, phlynx_export_bytes())
    assert body["model_filename"] == "model.cellml"
    assert body["obs_data"] and "error" not in body["obs_data"]
    assert body["params_for_id"] and "error" not in body["params_for_id"]
    assert len(body["params_for_id"]["params"]) == 4


def test_the_editor_state_is_kept_without_being_read(client):
    """`flow.json` is opaque to CUFLynx: it is neither parsed nor imported, and it
    must still be there to send back. Kept because the *archive* is kept, not
    because anything understood it."""
    body = _receive(client, phlynx_export_bytes())
    sent = _send_back(client, body["model_id"], source="as_imported")
    assert "flow.json" in _members(_decoded(sent))


# ---------------------------------------------------------------------------
# The round trip, with a real calibration in the middle
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_a_phlynx_study_calibrates_and_returns_with_its_best_fit(
    client, requires_simulation, tmp_path
):
    """The journey #290 exists for.

    Receive PhLynx's archive, run a real (short) calibration, and send it back
    asking for the best fit. What comes out must be PhLynx's own archive with the
    calibrated constants written into the model -- and nothing else touched.
    """
    original = phlynx_export_bytes()
    body = _receive(client, original)
    model_id = body["model_id"]

    resp = client.post(
        "/api/calibration/run",
        json={
            "model_id": model_id,
            "settings": {
                "param_id_method": "genetic_algorithm",
                "num_calls_to_function": 30,
                "DEBUG": True,  # small population: an interactive-scale run
                "dt": 0.01,
                "config_outputs_dir": str(tmp_path),
            },
        },
    )
    assert resp.status_code == 200, resp.text
    status, lines = _wait(client, resp.json()["job_id"], timeout=600)
    assert status["state"] == "done", "\n".join(lines)
    best = status["best_params"]
    assert best, "the calibration produced no best fit, so there is nothing to send"

    sent = _send_back(
        client, model_id, source="best_fit", output_dir=str(tmp_path)
    )
    returned = _members(_decoded(sent))
    arrived = _members(original)

    # 1. Everything PhLynx owns comes back exactly as it left.
    for name in PHLYNX_OWNED:
        assert returned[name] == arrived[name], f"{name} was not returned verbatim"
    # Including the flag CUFLynx must not interpret: `changes.json` is PhLynx's
    # outgoing marker and it does not consult it on import (#287 answer 3).
    assert json.loads(returned["changes.json"])["modified"] is True

    # 2. The observations are the author's ground truth, not CUFLynx's to rewrite.
    assert returned["3compartment_obs_data.json"] == arrived["3compartment_obs_data.json"]

    # 3. The model carries the calibration.
    meta = parse_cellml(returned["model.cellml"])
    written = {
        e.qname: e.initial_value
        for e in parse_params_for_id(C3_PARAMS_CSV_PATH.read_bytes(), meta.initial_values)
    }
    for qname, value in best.items():
        assert written[qname] == pytest.approx(value, rel=1e-6), qname

    # 4. Every value landed where PhLynx will read it back from (#287): a name
    #    resolved outside `parameters` / `parameters_global` travels and is then
    #    ignored, so the send reports it rather than letting it look applied.
    assert sent["unresolved"] == []
    assert sent["outside_parameters"] == []

    # 5. And the archive is still a study, not just a file that survived: the
    #    round trip closes back into CUFLynx.
    back = omex_import.unpack(_decoded(sent))
    assert list(back["cellml"]) == ["model.cellml"]
    assert back["obs"][0] == "3compartment_obs_data.json"
    assert back["params"] is not None


@pytest.mark.integration
def test_the_returned_study_reloads_in_cuflynx_with_the_calibrated_values(
    client, requires_simulation, tmp_path
):
    """"Open what I sent back" -- the user's own check that the trip worked.

    Separate from the send assertions on purpose: those read the bytes, this one
    puts them through the front door again, which is the only thing that proves
    the archive is loadable rather than merely correct.
    """
    body = _receive(client, phlynx_export_bytes())
    resp = client.post(
        "/api/calibration/run",
        json={
            "model_id": body["model_id"],
            "settings": {
                "param_id_method": "genetic_algorithm",
                "num_calls_to_function": 30,
                "DEBUG": True,
                "dt": 0.01,
                "config_outputs_dir": str(tmp_path),
            },
        },
    )
    assert resp.status_code == 200, resp.text
    status, lines = _wait(client, resp.json()["job_id"], timeout=600)
    assert status["state"] == "done", "\n".join(lines)
    best = status["best_params"]

    sent = _send_back(client, body["model_id"], source="best_fit", output_dir=str(tmp_path))
    reloaded = _receive(client, _decoded(sent))

    assert reloaded["obs_data"] and "error" not in reloaded["obs_data"]
    assert reloaded["params_for_id"] and "error" not in reloaded["params_for_id"]
    # The sliders come up on the calibrated values, which is what "I opened my
    # calibrated study" has to mean.
    initial = {p["qname"]: p["initial_value"] for p in reloaded["params_for_id"]["params"]}
    for qname, value in best.items():
        assert initial[qname] == pytest.approx(value, rel=1e-6), qname


@pytest.mark.integration
def test_a_second_round_trip_does_not_erode_the_archive(
    client, requires_simulation, tmp_path
):
    """PhLynx -> CUFLynx -> PhLynx -> CUFLynx -> PhLynx. A study is edited more
    than once, and the members CUFLynx does not own must survive every crossing,
    not just the first -- the failure mode of a writer that rebuilds from roles is
    that it loses one member per trip."""
    original = phlynx_export_bytes()
    first = _receive(client, original)
    once = _decoded(_send_back(client, first["model_id"], source="as_imported"))
    second = _receive(client, once)
    twice = _decoded(_send_back(client, second["model_id"], source="as_imported"))

    arrived, out = _members(original), _members(twice)
    for name in PHLYNX_OWNED:
        assert out[name] == arrived[name], f"{name} did not survive two round trips"
    assert out["model.cellml"] == arrived["model.cellml"], (
        "an as-imported send changed the model, so the archive is not a fixed point"
    )


# ---------------------------------------------------------------------------
# Pending PhLynx (expected to fail until their half lands)
# ---------------------------------------------------------------------------
def test_a_genuine_phlynx_export_is_available_to_test_against():
    """EXPECTED TO FAIL until PhLynx can export a CUFLynx archive.

    Every other test here builds the archive to #287's written contract, which
    proves CUFLynx honours the spec -- and proves nothing about whether the spec
    matches what PhLynx actually serialises. Only PhLynx's own bytes can do that,
    and PhLynx cannot produce them yet:

      * `EXPORT_KEYS.CUFLYNX` is declared `disabled: true` with no `action`, and
        `SEND_KEYS.CUFLYNX` exists in `constants.js` but is absent from
        `sendOptions` -- so there is no PhLynx -> CUFLynx path at all.
      * `generateOmexArchive` (services/compress.js) writes manifest, model.cellml,
        document.sedml and simulation.json. Neither `flow.json` nor `changes.json`
        is emitted yet, so no archive PhLynx makes today carries the two files
        #287 says the exchange turns on.

    Drop the export at the path below when it exists. Do not delete this test to
    make the suite green: a green suite with no real fixture is exactly the state
    #287 was written to end.
    """
    assert REAL_PHLYNX_EXPORT.is_file(), (
        f"no genuine PhLynx export at {REAL_PHLYNX_EXPORT}.\n"
        "This failure is expected and tracked: PhLynx must first enable its CUFLynx "
        "export/send and emit flow.json + changes.json (#287). Until then the round "
        "trip is only verified against the written contract, not against PhLynx."
    )


@pytest.mark.integration
def test_a_genuine_phlynx_export_round_trips(client, requires_simulation, tmp_path):
    """The same journey as above, against PhLynx's own bytes rather than our
    rendering of its contract.

    Skipped rather than failed while the fixture is absent: the *one* failure that
    should be reporting "PhLynx has not landed its half" is
    `test_a_genuine_phlynx_export_is_available_to_test_against`, and a second copy
    of it here would only make the real signal harder to find. This arms itself the
    moment the export exists -- nobody has to remember to enable it.
    """
    if not REAL_PHLYNX_EXPORT.is_file():
        pytest.skip(
            "no genuine PhLynx export yet -- tracked by "
            "test_a_genuine_phlynx_export_is_available_to_test_against"
        )
    original = REAL_PHLYNX_EXPORT.read_bytes()
    body = _receive(client, original)
    sent = _send_back(client, body["model_id"], source="current", values={})
    returned, arrived = _members(_decoded(sent)), _members(original)

    model_member = omex_import.unpack(original)["roles"]["cellml"][0]
    untouched = set(arrived) - {model_member, "manifest.xml"}
    # params_for_id is refreshed from the study by design, so it is the one other
    # member allowed to differ.
    untouched -= {n for n in untouched if "param" in Path(n).name.lower()}
    for name in sorted(untouched):
        assert returned[name] == arrived[name], f"{name} was not returned verbatim"


# ---------------------------------------------------------------------------
# What the genuine export turned up
# ---------------------------------------------------------------------------
@pytest.mark.integration
def test_the_genuine_export_carries_its_state_under_the_new_names(client, tmp_path):
    """The mismatch this whole PR existed to find.

    #287 was written against `flow.json`; PhLynx ships `flow-snapshot.json`
    (phlynx#542 flattened the workspace format). Matching on the name meant the
    editor state was not recognised at all -- so it was not kept beside the
    model, and both files were reported as obs_data candidates. Recognising it by
    the declared format, which is what upstream said was the stable half, fixes
    both.
    """
    if not REAL_PHLYNX_EXPORT.is_file():
        pytest.skip("no genuine PhLynx export to check against")
    with open(REAL_PHLYNX_EXPORT, "rb") as fh:
        resp = client.post(
            "/api/omex/upload",
            params={"output_dir": str(tmp_path)},
            files={"file": (REAL_PHLYNX_EXPORT.name, fh, "application/zip")},
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()

    # It carries no observations and no parameters, and that is not worth saying.
    assert body["obs_data"] is None
    assert body["params_for_id"] is None
    assert body["warnings"] == []

    kept = {p.name for p in tmp_path.rglob("*.json")}
    assert {"flow-snapshot.json", "changes.json"} <= kept, (
        "PhLynx's editor state was not kept beside the model, so reopening the "
        "study there would lose the layout"
    )
    assert body["module_config_path"], "nothing reported as kept"


# ---------------------------------------------------------------------------
# A PhLynx study, run
#
# Arriving whole is not the same as being usable. These are the two things a
# user does next: give the study its observations and its parameters, and press
# run. Both went wrong on a real PhLynx study and neither was covered.
# ---------------------------------------------------------------------------
def _attach_study_files(client, model_id: str) -> dict:
    """The obs_data and params_for_id for the SN study, the way the UI sends them.

    The archive carries neither -- PhLynx does not author them -- so a user
    supplies them, and `resources/SN_simple_*` are the study's own.
    """
    up = client.post(
        "/api/obs_data/upload",
        json={"model_id": model_id, "obs_data": json.loads(SN_OBS_DATA_PATH.read_text())},
    )
    assert up.status_code == 200, up.text
    assert up.json()["has_protocol"] is True

    with open(SN_PARAMS_CSV_PATH, "rb") as fh:
        pf = client.post(
            "/api/params_for_id/upload",
            data={"model_id": model_id},
            files={"file": (SN_PARAMS_CSV_PATH.name, fh, "text/csv")},
        )
    assert pf.status_code == 200, pf.text
    return {"obs": up.json(), "params": pf.json()}


@pytest.mark.integration
def test_a_phlynx_study_runs_once_it_has_obs_data_and_params(
    client, requires_simulation
):
    """Load PhLynx's own archive, give it a study, and press run.

    This failed on the real thing with

        Pacing parameter soma_SN/I_in must resolve to a valid variable

    followed by five hundred names the user had never typed. The cause is not in
    this repository: PhLynx hoists a module's constants into a `parameters`
    component under names of its own (`soma_SN_c_ER`, or plain `g_Na` when that
    is already unique), Myokit's importer then *merges* each connected pair so
    only the hoisted copy keeps a qname, and libcuflynx's resolver knew only
    circulatory_autogen's mirror-image convention (`c_ER_soma_SN`). Every name in
    the user's obs_data and params_for_id resolved to nothing.

    Fixed in circulatory_autogen (`VariableNameResolver._aliases`); asserted here
    because this is where it is user-visible, and because nothing else in either
    repository runs a *PhLynx-generated* model.
    """
    if not REAL_PHLYNX_EXPORT.is_file():
        pytest.skip("no genuine PhLynx export to run")
    loaded = _receive(client, REAL_PHLYNX_EXPORT.read_bytes())
    _attach_study_files(client, loaded["model_id"])

    # No protocol_info in the body: the obs_data just uploaded drives the run,
    # exactly as the app does it.
    resp = client.post(
        "/api/protocol/run",
        json={"model_id": loaded["model_id"], "params": {}, "outputs": ["soma_SN/V"]},
    )
    assert resp.status_code == 200, resp.text
    experiments = resp.json()["experiments"]
    assert len(experiments) == 3, "one per protocol experiment"
    for exp in experiments:
        trace = exp["outputs"]["soma_SN/V"]
        assert len(trace) > 1
        assert all(v == v for v in trace), "the membrane voltage went NaN"


@pytest.mark.integration
def test_a_phlynx_studys_parameters_reach_the_solver(client, requires_simulation):
    """Resolving the name is only half of it -- the value has to land.

    A name that resolves to the wrong variable, or to none, shows up as a slider
    that does nothing: the study calibrates, reports a best fit, and the model
    never moved. Moving one conductance has to move the trace.
    """
    if not REAL_PHLYNX_EXPORT.is_file():
        pytest.skip("no genuine PhLynx export to run")
    loaded = _receive(client, REAL_PHLYNX_EXPORT.read_bytes())
    _attach_study_files(client, loaded["model_id"])

    def peak(g_Na: float) -> float:
        resp = client.post(
            "/api/protocol/run",
            json={
                "model_id": loaded["model_id"],
                "params": {"soma_SN/g_Na": g_Na},
                "outputs": ["soma_SN/V"],
            },
        )
        assert resp.status_code == 200, resp.text
        return max(resp.json()["experiments"][0]["outputs"]["soma_SN/V"])

    assert peak(6.0) != pytest.approx(peak(1.0), abs=1e-9), (
        "the sodium conductance did not reach the model -- the parameter name "
        "resolved to nothing and the write was silently dropped"
    )


# ---------------------------------------------------------------------------
# Send and come back — the half no fixture can settle
#
# EXPECTED TO FAIL. Every other test here settles what CUFLynx does with bytes
# it is handed. This one asks the other question: is the archive CUFLynx *sends*
# one PhLynx can open, and does a study survive the trip out and back?
#
# It cannot be answered by a saved export, because a saved export only shows what
# PhLynx writes -- not what it accepts. So the two halves of PhLynx's own source
# are mirrored here, and the assertions are against those rules rather than
# against our reading of #287.
# ---------------------------------------------------------------------------
PHLYNX_RETURN_MANIFEST = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<omexManifest xmlns="http://identifiers.org/combine.specifications/omex-manifest">
  <content location="." format="http://identifiers.org/combine.specifications/omex"/>
  <content location="document.sedml" format="http://identifiers.org/combine.specifications/sed-ml" master="true"/>
  <content location="model.cellml" format="http://identifiers.org/combine.specifications/cellml"/>
  <content location="flow-snapshot.json" format="application/x.vnd.phlynx-flow+json"/>
  <content location="changes.json" format="application/x.vnd.phlynx-changes+json"/>
  <content location="simulation.json" format="http://purl.org/NET/mediatypes/application/json"/>
</omexManifest>
"""


class PhlynxCannotOpen(Exception):
    """What PhLynx's importer raises, spelled the way it spells it."""


#: Transcribed from `services/import/omex.js` on **phlynx PR #517**
#: (`hsorby:197-import-omex`), which is open and not yet on their main. That PR
#: replaced the fixed `zip.file('model.cellml')` lookup with a manifest-driven
#: one, so the member name no longer has to be guessed -- which is what let the
#: send start working at all.
CELLML_FORMAT = "http://identifiers.org/combine.specifications/cellml"
MANIFEST_NS = "http://identifiers.org/combine.specifications/omex-manifest"


def _phlynx_opens(archive: bytes) -> str:
    """PhLynx's `importOmexFile`, transcribed.

    Transcribed rather than paraphrased, so that when PhLynx changes it this test
    is edited against their source and not against someone's memory of it -- as
    it was just now, when #517 landed and the old fixed-name lookup went away.

    The rules it enforces, in its order: a `manifest.xml` must exist; the root
    must be an `omexManifest` in the OMEX namespace; every `<content>` must carry
    both a location and a format; every location referenced must be present; and
    the CellML is whichever entry declares the CellML format -- one of them, or
    exactly one marked master.
    """
    from xml.etree import ElementTree as ET

    members = _members(archive)
    if "manifest.xml" not in members:
        raise PhlynxCannotOpen("Invalid OMEX file: missing manifest.xml")
    try:
        root = ET.fromstring(members["manifest.xml"])
    except ET.ParseError as exc:
        raise PhlynxCannotOpen(f"manifest.xml is not valid XML: {exc}") from exc
    if not root.tag.endswith("omexManifest"):
        raise PhlynxCannotOpen("manifest.xml is not a valid omexManifest")

    cellmls: list[tuple[str, bool]] = []
    for content in root.iter(f"{{{MANIFEST_NS}}}content"):
        location, fmt = content.get("location"), content.get("format")
        if not location or not fmt:
            raise PhlynxCannotOpen(
                f"manifest.xml contains a content entry missing location or format "
                f"({location!r}, {fmt!r})"
            )
        location = location[2:] if location.startswith("./") else location
        if location in (".", "manifest.xml"):
            continue
        if location not in members:
            raise PhlynxCannotOpen(f'manifest.xml references missing file "{location}"')
        if fmt == CELLML_FORMAT:
            cellmls.append((location, content.get("master") == "true"))

    if not cellmls:
        raise PhlynxCannotOpen("Invalid OMEX file: no CellML files found.")
    if len(cellmls) == 1:
        return members[cellmls[0][0]].decode("utf-8")
    masters = [loc for loc, is_master in cellmls if is_master]
    if len(masters) != 1:
        raise PhlynxCannotOpen(
            "multiple CellML files require exactly one master CellML file"
        )
    return members[masters[0]].decode("utf-8")


#: The manifest formats PhLynx will look inside for one of its own JSON files.
#: `/^application\/(?:json|.+\+json)$/i` in their `omex.js`, plus the one purl
#: spelling they special-case.
_JSON_FORMAT = re.compile(r"^application/(?:json|.+\+json)$", re.I)
_PURL_JSON = "http://purl.org/NET/mediatypes/application/json"


def _is_flow_snapshot(blob: bytes) -> bool:
    """`isPhlynxFlowSnapshotFile`, transcribed from `import/omexClassifiers.js`.

    Content, not filename: PhLynx renamed this member once already (#542, when
    `flow.json` became `flow-snapshot.json`), and the sniff is what survived.
    """
    try:
        parsed = json.loads(blob)
    except (ValueError, UnicodeDecodeError):
        return False
    return (
        isinstance(parsed, dict)
        and isinstance(parsed.get("nodeData"), list)
        and isinstance(parsed.get("edges"), list)
        and parsed.get("id") == "phlynx-flow-snapshot"
        and str(parsed.get("version", "")).startswith("1.0")
    )


def _phlynx_workspace(archive: bytes) -> list[str]:
    """The modules PhLynx puts **on the canvas**, per `processImportedOmexArchive`.

    This is the step `_phlynx_opens` stops short of, and the gap a user found by
    hand: an archive can open perfectly and still leave the workspace empty.
    PhLynx builds the canvas from `flow-snapshot.json` alone --

        if (result.files?.flowSnapshot) { await loadFlowSnapshot(...) }
        else if (result.files?.cellml)  { await loadCellMLData(...) }

    -- and `loadCellMLData` registers the CellML's components in the *library*
    (`libraryStore.addMathFile`), which is not the canvas. So a study with no
    snapshot opens with nothing in the workspace no matter how good its model is.

    Returns the node names `loadFlowSnapshot` would add, empty when there is no
    snapshot to load.
    """
    from xml.etree import ElementTree as ET

    members = _members(archive)
    root = ET.fromstring(members["manifest.xml"])
    for content in root.iter(f"{{{MANIFEST_NS}}}content"):
        location, fmt = content.get("location") or "", content.get("format") or ""
        location = location[2:] if location.startswith("./") else location
        if location in ("", ".", "manifest.xml") or location not in members:
            continue
        if not (_JSON_FORMAT.match(fmt) or fmt == _PURL_JSON):
            continue
        if _is_flow_snapshot(members[location]):
            snapshot = json.loads(members[location])
            return [node["data"]["name"] for node in snapshot["nodeData"]]
    return []


def _phlynx_returns(edited_cellml: str, state: dict, cellml_name: str,
                    extras: dict | None = None) -> bytes:
    """What PhLynx's exporter writes (`generateOmexArchive`, same branch).

    Two things it does now that it did not: it keeps the model's filename, and it
    restores `omexStore.preservedExtras` -- every member its importer did not
    recognise, written back with its manifest entry, skipping the locations it
    owns itself. That is what carries a CUFLynx study's obs_data and
    params_for_id across, since PhLynx neither reads nor authors either.
    """
    reserved = {"manifest.xml", "document.sedml", cellml_name,
                "flow-snapshot.json", "changes.json"}
    kept = {loc: blob for loc, blob in (extras or {}).items() if loc not in reserved}

    entries = [
        ("document.sedml", "http://identifiers.org/combine.specifications/sed-ml", True),
        (cellml_name, CELLML_FORMAT, False),
        ("flow-snapshot.json", "application/x.vnd.phlynx-flow+json", False),
        ("changes.json", "application/x.vnd.phlynx-changes+json", False),
        ("simulation.json", "http://purl.org/NET/mediatypes/application/json", False),
    ]
    # The format each extra arrived with, which is what PhLynx stores alongside
    # it -- an extra whose format changed on the way through would be a different
    # member as far as the next reader is concerned.
    entries += [(loc, _EXTRA_FORMATS.get(loc, "application/json"), False) for loc in kept]

    def _entry(loc: str, fmt: str, master: bool) -> str:
        flag = ' master="true"' if master else ""
        return f'  <content location="{loc}" format="{fmt}"{flag}/>'

    lines = "\n".join(_entry(*entry) for entry in entries)
    manifest = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        f'<omexManifest xmlns="{MANIFEST_NS}">\n'
        f'  <content location="." format="http://identifiers.org/combine.specifications/omex"/>\n'
        f"{lines}\n</omexManifest>\n"
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.xml", manifest)
        zf.writestr(cellml_name, edited_cellml)
        zf.writestr("document.sedml", "<sedML/>")
        zf.writestr("simulation.json", json.dumps({"input": [], "output": [], "parameters": []}))
        zf.writestr("flow-snapshot.json", state.get("flow-snapshot.json", b'{"nodes": []}'))
        zf.writestr("changes.json", state.get("changes.json", b'{"modified": true}'))
        for loc, blob in kept.items():
            zf.writestr(loc, blob)
    return buf.getvalue()


#: A parameter, as CUFLynx understands one: a `<variable>`'s `initial_value`.
#: Unique in the flattened 3compartment model, and doubling it moves the outputs.
PARAM_BEFORE = '<variable name="R_pvn" units="Js_per_m6" initial_value="1333000" interface="public"/>'
PARAM_AFTER = PARAM_BEFORE.replace('1333000', '2666000')

#: Maths, as distinct from a parameter: a literal coefficient *inside* an
#: equation. CUFLynx cannot reach this -- it can write an `initial_value` and
#: nothing else -- so it is exactly the kind of change PhLynx is opened for.
MATHS_BEFORE = '<cn cellml:units="dimensionless">0.25</cn>'
MATHS_AFTER = '<cn cellml:units="dimensionless">0.75</cn>'


def _edit(before: str, after: str):
    """Replace one occurrence, refusing to silently do nothing.

    An edit that matched nothing would leave the outputs identical and the
    assertion below would read as "the change did not come back" when the truth
    is that no change was made -- so the model moving underneath this test has to
    say so in its own words.
    """

    def apply(cellml: str) -> str:
        assert before in cellml, f"the model no longer contains {before!r} to edit"
        return cellml.replace(before, after, 1)

    return apply


def _outputs(client, model_id: str, names: list[str], sim_time: float = 1.0) -> dict:
    resp = client.post(
        "/api/simulate",
        json={"model_id": model_id, "params": {}, "outputs": names, "sim_time": sim_time},
    )
    assert resp.status_code == 200, resp.text
    series = resp.json()["outputs"]
    return {name: series[name][-1] for name in names}


#: Formats PhLynx carries with each preserved extra, keyed by member. Filled in
#: by the journey below from the manifest that arrived, because that is where
#: PhLynx gets them from too.
_EXTRA_FORMATS: dict[str, str] = {}

#: Members PhLynx's importer recognises and therefore does **not** hold as an
#: extra: the model, the SED-ML, its own workspace, and the simulation settings.
_PHLYNX_KNOWS = {"manifest.xml", "document.sedml", "flow-snapshot.json",
                 "changes.json", "simulation.json"}


def _phlynx_extras(archive: bytes) -> dict:
    """What PhLynx's importer sorts into `extras` and its exporter restores.

    Everything the manifest declares that PhLynx has no meaning for -- a CUFLynx
    obs_data and params_for_id among them. Classified from the manifest rather
    than from the filenames, the way their importer does it.
    """
    from xml.etree import ElementTree as ET

    members = _members(archive)
    extras: dict[str, bytes] = {}
    _EXTRA_FORMATS.clear()
    root = ET.fromstring(members["manifest.xml"])
    for content in root.iter(f"{{{MANIFEST_NS}}}content"):
        location, fmt = content.get("location") or "", content.get("format") or ""
        location = location[2:] if location.startswith("./") else location
        if location in ("", ".") or location in _PHLYNX_KNOWS:
            continue
        if fmt == CELLML_FORMAT or location not in members:
            continue
        extras[location] = members[location]
        _EXTRA_FORMATS[location] = fmt
    return extras


def _through_phlynx(client, loaded: dict, edit) -> dict:
    """Send the study out, let PhLynx open and change it, and bring it back.

    The step that fails today is ``_phlynx_opens``; everything after it is what
    has to hold once the send works, and is written to run unchanged then.
    """
    sent = _decoded(_send_back(client, loaded["model_id"]))
    state = {
        name: blob
        for name, blob in _members(sent).items()
        if name.endswith(".json") and ("flow" in name or "changes" in name)
    }
    cellml = _phlynx_opens(sent)
    # PhLynx keeps the model's filename now (#517), so the return carries the
    # name that went out rather than inventing one.
    name = next(n for n in _members(sent) if n.endswith(".cellml"))
    return _receive(
        client, _phlynx_returns(edit(cellml), state, name, _phlynx_extras(sent))
    )


@pytest.mark.integration
def test_a_study_sent_to_phlynx_comes_back_without_losing_its_study(
    client, requires_simulation
):
    """EXPECTED TO FAIL until the exchange works in both directions.

    The journey a user actually asks for: open the model in PhLynx, change it
    there, bring it back, and carry on calibrating the study you already had.

    Two things have to become true, and neither is CUFLynx's to decide alone:

    1. **PhLynx has to be able to open what CUFLynx sends.** Its loader looks the
       model up as `model.cellml`; CUFLynx keeps the member name the archive
       arrived with, because #287 says members come back verbatim. For a study
       that came from an archive -- the 3compartment example, every PhLynx
       export -- those disagree and the send fails at PhLynx's first line. Either
       PhLynx looks up the CellML by its manifest entry, or the verbatim rule
       gets an exception for this one member. That is a contract question, so
       this test does not presume the answer; it only refuses to go green while
       neither has happened.

    2. **What comes back must not take the study with it.** PhLynx's exporter
       writes no obs_data and no params_for_id, so a returned archive loaded as
       a fresh study replaces a calibrated one with an empty one. The model is
       PhLynx's to change; the observations and parameters are not, and they are
       not in the file to be restored from.

    Do not delete this to make the suite green, and do not weaken it to whatever
    CUFLynx happens to do today -- a green suite that never sends anywhere is the
    state #287 was written to end.
    """
    with open(RESOURCES_DIR / "3compartment.omex", "rb") as fh:
        loaded = _receive(client, fh.read())
    assert loaded["obs_data"], "the fixture is meant to carry observations"
    assert loaded["params_for_id"], "the fixture is meant to carry parameters"
    obs_before = loaded["obs_data"]
    params_before = loaded["params_for_id"]

    returned = _through_phlynx(
        client, loaded, _edit("<model", "<!-- edited in PhLynx -->\n<model")
    )

    assert returned["obs_data"] == obs_before, (
        "the observations were lost coming back from PhLynx, which does not carry them"
    )
    # The parameters, not the filename. CUFLynx refreshes params_for_id from the
    # study on the way out and writes its canonical JSON, so a study loaded from
    # a `.csv` legitimately comes back as `.json` -- that is the range and
    # selection edits travelling, which is the point of refreshing it.
    assert returned["params_for_id"], (
        "the parameters were lost coming back from PhLynx"
    )
    assert returned["params_for_id"]["params"] == params_before["params"], (
        "the parameters changed on the way through PhLynx, which does not read them"
    )


@pytest.mark.integration
def test_a_parameter_changed_in_phlynx_changes_the_outputs(client, requires_simulation):
    """EXPECTED TO FAIL with the two above, and for the same first reason.

    A value edited in PhLynx has to reach the solver, not merely the file. So
    this simulates before and after rather than diffing XML: a model that comes
    back carrying the new text but running the old numbers passes a byte
    comparison and fails the user.

    `R_pvn` is a resistance the flattened 3compartment model declares once;
    doubling it moves every output measured here.
    """
    with open(RESOURCES_DIR / "3compartment.omex", "rb") as fh:
        loaded = _receive(client, fh.read())
    watched = loaded["odes"][:3]
    before = _outputs(client, loaded["model_id"], watched)

    returned = _through_phlynx(client, loaded, _edit(PARAM_BEFORE, PARAM_AFTER))
    after = _outputs(client, returned["model_id"], watched)

    assert after != before, (
        f"doubling R_pvn in PhLynx left the outputs identical ({before}), so the "
        f"edit did not reach the solver"
    )
    # And the study it belongs to is still there to be calibrated against.
    assert returned["obs_data"] and returned["params_for_id"]


@pytest.mark.integration
def test_maths_changed_in_phlynx_changes_the_outputs(client, requires_simulation):
    """EXPECTED TO FAIL with the two above, and for the same first reason.

    The change CUFLynx cannot make itself, which is the reason to open PhLynx at
    all: a coefficient *inside* an equation. CUFLynx can write a variable's
    `initial_value` and nothing else, so an equation edit can only arrive from
    the outside -- and if it does not survive the trip, the whole exchange is
    only good for numbers the user could already have changed with a slider.
    """
    with open(RESOURCES_DIR / "3compartment.omex", "rb") as fh:
        loaded = _receive(client, fh.read())
    watched = loaded["odes"][:3]
    before = _outputs(client, loaded["model_id"], watched)

    returned = _through_phlynx(client, loaded, _edit(MATHS_BEFORE, MATHS_AFTER))
    after = _outputs(client, returned["model_id"], watched)

    assert after != before, (
        f"changing a coefficient inside an equation in PhLynx left the outputs "
        f"identical ({before}), so the maths edit did not reach the solver"
    )
    assert returned["obs_data"] and returned["params_for_id"]


# ---------------------------------------------------------------------------
# The workspace, not just the model
#
# `_phlynx_opens` proves an archive is *readable*. It says nothing about whether
# the study is *usable* once it is open, and the difference is what a user hit:
# PhLynx loaded, and the workspace was empty. See `_phlynx_workspace`.
# ---------------------------------------------------------------------------
def test_a_phlynx_studys_workspace_survives_the_send(client):
    """Out and back with the canvas intact -- the reason to press Edit at all.

    CUFLynx never reads or writes `flow-snapshot.json`; it returns it with every
    other member it did not author. This is the assertion that the policy is
    enough: the same three modules come back, so PhLynx reopens the study the
    user left rather than a bag of components.
    """
    if not REAL_PHLYNX_EXPORT.is_file():
        pytest.skip("no genuine PhLynx export to send")
    original = REAL_PHLYNX_EXPORT.read_bytes()
    arrived = _phlynx_workspace(original)
    assert arrived, "the fixture has no workspace, so it cannot show one surviving"

    loaded = _receive(client, original)
    sent = _decoded(_send_back(client, loaded["model_id"]))
    assert _phlynx_workspace(sent) == arrived


def test_a_study_cuflynx_assembled_reaches_phlynx_with_no_workspace(client):
    """The other half of the same question, and the answer is: it does not.

    A study that never came from PhLynx -- the bundled examples, or three files
    dropped one at a time -- has no `flow-snapshot.json`, and CUFLynx has none to
    give it. A snapshot is not derivable from what CUFLynx holds: its nodes carry
    port couplings, handle ids, positions and a `mathRef` into a module library,
    none of which survives into the flattened CellML CUFLynx simulates, and
    inventing them would be authoring PhLynx's editor state -- exactly what
    `omex_export` refuses to do so that a real one is never overwritten.

    So this is pinned rather than fixed, and it is pinned here so that "PhLynx
    opened empty" is a documented consequence with a named cause instead of a
    bug report. Closing it needs PhLynx to build a workspace from the CellML when
    an archive brings no snapshot; when they do, this test is the one to delete.
    """
    with open(RESOURCES_DIR / "3compartment.omex", "rb") as fh:
        loaded = _receive(client, fh.read())
    sent = _decoded(_send_back(client, loaded["model_id"]))

    assert _phlynx_opens(sent), "the model itself still travels"
    assert _phlynx_workspace(sent) == [], (
        "a snapshot appeared in an archive CUFLynx assembled -- if CUFLynx has "
        "started authoring PhLynx's editor state, that is a decision to make "
        "deliberately (#287), not a side effect"
    )
