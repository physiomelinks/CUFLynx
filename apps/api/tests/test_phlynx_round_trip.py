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
import zipfile
from pathlib import Path

import omex_import
import pytest
from cellml_meta import parse_cellml
from conftest import RESOURCES_DIR
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


def _phlynx_returns(edited_cellml: str, state: dict, cellml_name: str) -> bytes:
    """What PhLynx's exporter writes (`generateOmexArchive`, same branch).

    It keeps the model's filename now, which the old one did not. What it still
    does **not** write is an obs_data or a params_for_id: its importer sorts them
    into `extras` and the exporter never re-emits them. That absence is the whole
    of the gate below -- a study that goes to PhLynx and comes back has to arrive
    with its observations and parameters intact, and nothing in the returned file
    could supply them.
    """
    entries = [
        ("document.sedml", "http://identifiers.org/combine.specifications/sed-ml", True),
        (cellml_name, CELLML_FORMAT, False),
        ("flow-snapshot.json", "application/x.vnd.phlynx-flow+json", False),
        ("changes.json", "application/x.vnd.phlynx-changes+json", False),
        ("simulation.json", "http://purl.org/NET/mediatypes/application/json", False),
    ]
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
    return _receive(client, _phlynx_returns(edit(cellml), state, name))


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
    assert returned["params_for_id"] == params_before, (
        "the parameters were lost coming back from PhLynx, which does not carry them"
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
