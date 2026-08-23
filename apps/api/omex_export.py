"""Write a COMBINE archive back out, preserving what CUFLynx did not author (#290).

CUFLynx and PhLynx exchange a study as an ``.omex``. #287 fixes what that means:
the exchanged data is the flattened CellML, PhLynx expects CUFLynx to change only
the constants, and **every other member of the archive comes back untouched** --
PhLynx's ``flow.json`` / ``changes.json`` editor state, its SED-ML and
``simulation.json``, and anything CUFLynx has never heard of.

So the rule this module implements is *copy everything, replace two things*:

* the model CellML, by CUFLynx's flattened copy with the current parameter
  values substituted into its ``initial_value`` attributes;
* params_for_id, by the study's own, so range and selection edits made in the
  params editor travel back.

**obs_data is never replaced.** It passes through verbatim when the archive
carries one, and is only *added* when the study has one and the archive does not
-- which is not a replacement. PhLynx's own state files are never parsed, never
authored and never mutated: whichever of them arrived is exactly what leaves, and
none is invented when absent. #287 has not settled whether ``flow.json`` or
``module_config.json`` is authoritative, and this is what keeps CUFLynx from
having to care.

With no source archive at all -- a study assembled from three dropped files -- a
minimal one is synthesised rather than the send being refused: manifest, model,
obs_data, params_for_id, and no editor state, because CUFLynx has none to invent.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

import omex_import
from calibrated_model import calibrated_cellml

#: The components #287 says PhLynx will read parameter changes back out of. A
#: value written anywhere else is still written -- CUFLynx does not own the
#: model's layout -- but the caller is told, because PhLynx will not pick it up.
PHLYNX_PARAMETER_COMPONENTS = ("parameters", "parameters_global")

#: The name a synthesised archive gives its model. PhLynx's URL loader looks the
#: member up by this exact name today, and per #287 the filename is ours to
#: choose, so choosing the one that works is free.
DEFAULT_MODEL_NAME = "model.cellml"

CELLML_FORMAT = "http://identifiers.org/combine.specifications/cellml"
OMEX_FORMAT = "http://identifiers.org/combine.specifications/omex"
MANIFEST_FORMAT = "http://identifiers.org/combine.specifications/omex-manifest"
MANIFEST_NAME = "manifest.xml"

_FORMATS_BY_SUFFIX = {
    ".cellml": CELLML_FORMAT,
    ".json": "application/json",
    ".csv": "text/csv",
    ".mmt": "http://purl.org/NET/mediatypes/text/x-myokit",
}


class OmexExportError(ValueError):
    """The archive could not be built (surface as HTTP 422)."""


def _format_for(name: str) -> str:
    return _FORMATS_BY_SUFFIX.get(Path(name).suffix.lower(), "application/octet-stream")


def build_manifest(entries: list[dict]) -> bytes:
    """Serialise ``[{"location", "format", "master"}]`` as a COMBINE manifest.

    The self-referencing ``.`` entry and the manifest's own entry are mandatory
    and are emitted here rather than carried in ``entries``, so a caller only
    ever deals with real members.
    """
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<omexManifest xmlns="http://identifiers.org/combine.specifications/omex-manifest">',
        f'  <content location="." format="{OMEX_FORMAT}"/>',
        f'  <content location="./{MANIFEST_NAME}" format="{MANIFEST_FORMAT}"/>',
    ]
    for entry in entries:
        loc = entry["location"]
        master = ' master="true"' if entry.get("master") else ""
        lines.append(
            f"  <content location={quoteattr(loc)} "
            f"format={quoteattr(entry['format'])}{master}/>"
        )
    lines.append("</omexManifest>")
    lines.append("")
    return "\n".join(lines).encode("utf-8")


def substitute_values(cellml_text: str, values: dict[str, float]) -> tuple[str, dict]:
    """The model with ``values`` written into it, plus what happened.

    Delegates to :func:`calibrated_model.calibrated_cellml`, which already maps a
    params_for_id ``vessel/param`` onto the flat model's owning component and
    substitutes without a libCellML round-trip. There is deliberately no second
    substituter here: a send and a saved calibrated model must write the same
    file for the same values.
    """
    if not values:
        return cellml_text, {"updated": [], "unresolved": [], "resolved": {}}
    return calibrated_cellml(cellml_text, values)


def outside_parameter_components(report: dict) -> list[str]:
    """Written names whose owning component is not one PhLynx reads back (#287)."""
    out = []
    for full_name, key in (report.get("resolved") or {}).items():
        component = key.partition("/")[0]
        if component not in PHLYNX_PARAMETER_COMPONENTS:
            out.append(f"{full_name} -> {key}")
    return sorted(out)


def _relative(location: str) -> str:
    return location.lstrip("./")


def _entry_for(location: str, master: bool = False) -> dict:
    return {
        "location": f"./{location}",
        "format": _format_for(location),
        "master": master,
    }


def _replace_member(
    members: dict[str, bytes],
    entries: list[dict],
    old_name: str | None,
    new_name: str,
    blob: bytes,
) -> None:
    """Write ``blob`` as ``new_name``, retiring ``old_name`` when it differs.

    The rename case is real: an archive from PhLynx carries a params_for_id
    ``.csv`` while CUFLynx stores the canonical ``.json`` (there is no converter
    back), so the outgoing archive must not keep both -- two files disagreeing
    about which is current is the same failure ``_save_params_file`` avoids
    inside the uploads dir.
    """
    if old_name and old_name != new_name:
        members.pop(old_name, None)
        base = Path(old_name).name.lower()
        entries[:] = [e for e in entries if Path(_relative(e["location"])).name.lower() != base]
    members[new_name] = blob
    base = Path(new_name).name.lower()
    if not any(Path(_relative(e["location"])).name.lower() == base for e in entries):
        entries.append(_entry_for(new_name))


def build_archive(
    *,
    cellml_text: str,
    values: dict[str, float] | None = None,
    source_archive: bytes | None = None,
    obs_bytes: bytes | None = None,
    obs_name: str = "obs_data.json",
    params_bytes: bytes | None = None,
    params_name: str = "params_for_id.json",
    model_name: str = DEFAULT_MODEL_NAME,
) -> tuple[bytes, dict]:
    """Return ``(omex_bytes, report)``.

    ``report`` carries the substitution outcome (``updated`` / ``unresolved`` /
    ``outside_parameters``) and ``members``, the names written.
    """
    new_cellml, report = substitute_values(cellml_text, values or {})

    members: dict[str, bytes] = {}
    entries: list[dict] = []
    parts: dict | None = None

    if source_archive:
        try:
            parts = omex_import.unpack(source_archive)
        except omex_import.OmexImportError as exc:
            raise OmexExportError(f"the stored archive could not be read: {exc}") from exc
        # Everything, byte for byte -- minus the manifest, which is rebuilt from
        # its own entries below so that a member CUFLynx retires cannot be left
        # behind as a dangling <content> line.
        manifest_name = (parts["manifest"] or {}).get("name")
        members = {
            name: blob
            for name, blob in parts["members"].items()
            if name != manifest_name
        }
        for entry in (parts["manifest"] or {}).get("entries", []):
            loc = _relative(entry["location"])
            if not loc or loc == "." or loc.lower().endswith(MANIFEST_NAME):
                continue
            entries.append(dict(entry))

    if parts is not None and parts["roles"]["cellml"]:
        model_member = parts["roles"]["cellml"][0]
        # Exactly one CellML leaves. The others are the master's imports, and
        # CUFLynx's model is the *flattened* document that subsumes them --
        # shipping both would hand PhLynx the same definitions twice, and per
        # #287 several CellMLs is also the case where the master entry has to
        # disambiguate. This is the one member class not carried through
        # verbatim, and the flattening is why.
        for extra in parts["roles"]["cellml"][1:]:
            members.pop(extra, None)
            base = Path(extra).name.lower()
            entries = [
                e for e in entries if Path(_relative(e["location"])).name.lower() != base
            ]
        _replace_member(members, entries, model_member, model_member, new_cellml.encode("utf-8"))
        model_out = model_member
        # `master` is deliberately left exactly as the source manifest had it. A
        # PhLynx archive marks its SED-ML master, not its CellML, and with one
        # CellML in the archive that file is used regardless (#287 answer 4) --
        # so rewriting the flag would edit a member we promised to return
        # untouched in order to say something already unambiguous.
    else:
        # Synthesised from a study that never came from an archive. Here nobody
        # else has claimed master, so the model takes it.
        model_out = model_name
        _replace_member(members, entries, None, model_out, new_cellml.encode("utf-8"))
        for entry in entries:
            entry["master"] = Path(_relative(entry["location"])).name.lower() == Path(
                model_out
            ).name.lower()

    # params_for_id: refreshed from the study, because range and selection edits
    # made in CUFLynx are edits to the study and belong in what goes back.
    if params_bytes is not None:
        old = (parts["roles"]["params"] or [None])[0] if parts else None
        _replace_member(members, entries, old, params_name, params_bytes)

    # obs_data: added when the archive has none, never replaced when it has one.
    if obs_bytes is not None and not (parts and parts["roles"]["obs"]):
        _replace_member(members, entries, None, obs_name, obs_bytes)

    report = {
        "updated": report.get("updated", []),
        "unresolved": report.get("unresolved", []),
        "outside_parameters": outside_parameter_components(report),
        "model_name": model_out,
        "members": sorted(members),
    }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(MANIFEST_NAME, build_manifest(entries))
        for name in sorted(members):
            zf.writestr(name, members[name])
    return buf.getvalue(), report
