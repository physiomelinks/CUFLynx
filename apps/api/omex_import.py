"""Unpack a COMBINE archive (.omex) into the files CUFLynx already understands (#149).

An OMEX is a zip with a `manifest.xml` listing its contents. A user with a whole
study in one archive should be able to drop it on *any* of the import boxes and
get the model, obs_data and params_for_id all loaded, rather than unzipping it
and dropping three files in the right order.

Two things this deliberately does not do:

* It does not require the manifest. Real archives in the wild have missing or
  wrong manifests, and the contents are identifiable anyway -- a `.cellml` is a
  CellML, `*params*.csv` is a params_for_id. The manifest is used to pick the
  *master* model when it says which one is master, and to rule out members whose
  declared format says they are not observations -- neither of which file names
  can tell you.
* It does not interpret `module_config.json`, `flow.json` or `changes.json`.
  That is PhLynx's own state; CUFLynx keeps it beside the outputs so PhLynx can
  be reopened with the same memory (#149), returns it untouched in any archive
  it writes (#287/#290), and otherwise leaves it entirely alone.

``unpack`` also returns **every** member and the parsed manifest, because an
archive sent back to PhLynx has to carry the parts CUFLynx does not understand
through byte-for-byte -- see :mod:`omex_export`. Nothing can be returned verbatim
that was not kept.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

# PhLynx's editor state, carried along so the archive round-trips through it.
MODULE_CONFIG_NAME = "module_config.json"

#: The COMBINE format specifiers PhLynx uses for its own state files (#287).
#: The *format* is the contract, not the file name -- upstream is explicit that
#: `flow.json` may be renamed -- so these are only ever matched against the
#: manifest, never against a member name.
PHLYNX_FLOW_FORMAT = "application/x.vnd.phlynx-flow+json"
PHLYNX_CHANGES_FORMAT = "application/x.vnd.phlynx-changes+json"

#: Substrings of a declared format that mean "this member is not observations".
#: Matched loosely because a format is written both as a media type and as a
#: COMBINE specification URL (`http://identifiers.org/combine.specifications/sed-ml`).
_NON_OBS_FORMAT_MARKERS = (
    "x.vnd.phlynx-flow+json",
    "x.vnd.phlynx-changes+json",
    "sed-ml",
    "sedml",
)

OMEX_SUFFIXES = (".omex",)

# The model formats an archive may carry. A .mmt is converted to CellML on the
# way in exactly as a dropped one is (#27), so an archive built around a Myokit
# model is not a second kind of study.
MODEL_SUFFIXES = (".cellml", ".mmt")


class OmexImportError(ValueError):
    """A COMBINE archive that could not be read (surface as HTTP 422)."""


def is_omex_filename(name: str) -> bool:
    return Path(str(name or "")).suffix.lower() in OMEX_SUFFIXES


def looks_like_omex(data: bytes) -> bool:
    """Whether ``data`` is a zip that plausibly holds a model.

    Extension alone is not enough -- archives are handed around as `.zip` too --
    and a zip that contains no model is not something to route through here.
    """
    if not data[:2] == b"PK":
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = [n.lower() for n in zf.namelist()]
    except zipfile.BadZipFile:
        return False
    return any(n.endswith(MODEL_SUFFIXES) for n in names) or any(
        n.endswith("manifest.xml") for n in names
    )


def read_manifest(zf: zipfile.ZipFile) -> dict | None:
    """``{"name", "entries": [{"location", "format", "master"}]}``, or None.

    Parsed once and handed to everything that needs it: which CellML is master,
    which members are declared as something other than observations, and (in
    :mod:`omex_export`) which entries have to be re-emitted verbatim.
    """
    for name in zf.namelist():
        if not name.lower().endswith("manifest.xml"):
            continue
        try:
            root = ET.fromstring(zf.read(name))
        except ET.ParseError:
            continue
        entries = [
            {
                "location": entry.get("location") or "",
                "format": entry.get("format") or "",
                "master": str(entry.get("master", "")).lower() == "true",
            }
            for entry in root.iter()
            if entry.tag.endswith("content")
        ]
        if entries:
            return {"name": name, "entries": entries}
    return None


def _master_from_manifest(entries: list[dict]) -> str | None:
    """The location the manifest marks ``master="true"``, if any.

    Which CellML is the main model is the one thing file names cannot tell you,
    so this is the manifest's job. Everything else is classified by name.
    """
    for entry in entries:
        if not entry["master"]:
            continue
        loc = entry["location"].lstrip("./")
        if loc.lower().endswith(MODEL_SUFFIXES):
            return loc
    return None


def formats_by_member(entries: list[dict]) -> dict[str, str]:
    """``{basename(lower): format}`` from manifest entries.

    Keyed on the basename because manifest locations are relative and may or may
    not carry a leading ``./`` or a directory prefix -- the same comparison
    :func:`unpack` makes for the master model.
    """
    out: dict[str, str] = {}
    for entry in entries:
        loc = entry["location"].lstrip("./")
        if not loc or loc == ".":
            continue
        out[Path(loc).name.lower()] = entry["format"]
    return out


def _declared_non_obs(name: str, formats: dict[str, str]) -> bool:
    fmt = str(formats.get(Path(name).name.lower(), "")).lower()
    return any(marker in fmt for marker in _NON_OBS_FORMAT_MARKERS)


def _looks_like_obs_data(blob: bytes | None) -> bool:
    """Whether a JSON member is plausibly an obs_data document.

    circulatory_autogen accepts two shapes (``obs_data.parse_obs_data``): a bare
    array of data_items, or an object with ``protocol_info`` / ``data_items``.
    Nothing else in an archive looks like either, which is what lets a PhLynx
    ``simulation.json`` -- declared as plain ``application/json``, exactly like a
    real obs_data -- stay out of the observations slot instead of importing as a
    parse-error banner.
    """
    if not blob:
        return False
    try:
        doc = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return False
    if isinstance(doc, list):
        return True
    return isinstance(doc, dict) and ("data_items" in doc or "protocol_info" in doc)


def _classify(
    names: list[str],
    formats: dict[str, str] | None = None,
    members: dict[str, bytes] | None = None,
) -> dict:
    """Sort archive members into the roles CUFLynx imports."""
    formats = formats or {}
    members = members or {}

    cellml = [n for n in names if n.lower().endswith(".cellml")]
    myokit = [n for n in names if n.lower().endswith(".mmt")]
    csvs = [n for n in names if n.lower().endswith(".csv")]
    jsons = [n for n in names if n.lower().endswith(".json")]

    def named(candidates, word):
        return [n for n in candidates if re.search(word, Path(n).name, re.I)]

    params_csv = named(csvs, r"param")
    # A params_for_id is stored as **JSON** by CUFLynx (`_save_params_file`), so
    # an archive CUFLynx writes has to be readable by CUFLynx: without this the
    # params member would fall through to the obs_data pool and the study would
    # come back missing its parameters.
    params_json = named(jsons, r"param")
    module_config = [n for n in jsons if Path(n).name == MODULE_CONFIG_NAME]

    spoken_for = set(module_config) | set(params_json)
    candidates = [
        n
        for n in jsons
        if n not in spoken_for and not _declared_non_obs(n, formats)
    ]
    # An obviously named one wins, as it always has. Only the leftovers are
    # sniffed, so an archive with no manifest and a plainly named obs_data keeps
    # working exactly as before.
    obs_named = named(candidates, r"obs")
    obs = obs_named or [n for n in candidates if _looks_like_obs_data(members.get(n))]

    return {
        # A .mmt only counts when there is no CellML: an archive holding both has
        # presumably already been converted, and the CellML is the authoritative
        # copy -- re-converting would silently prefer the source over the file the
        # author chose to ship.
        "cellml": cellml or myokit,
        "params": params_csv or params_json or csvs,
        "obs": obs,
        "module_config": module_config,
    }


def unpack(data: bytes) -> dict:
    """Read an archive into its roles, plus everything it holds.

    ``cellml`` keeps *every* CellML in the archive, with the master first: a
    non-flattened model needs its sister files, and the existing upload path
    already knows how to flatten a bundle. An archive whose model is a Myokit
    ``.mmt`` yields that instead, for the caller to convert.

    ``members`` and ``manifest`` are what make the archive re-emittable: an OMEX
    sent back to PhLynx has to carry every member CUFLynx does not understand
    through byte-for-byte (#287), and it cannot do that from four roles.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise OmexImportError(f"not a readable archive: {exc}") from exc

    with zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            raise OmexImportError("the archive is empty")
        members = {n: zf.read(n) for n in names}
        manifest = read_manifest(zf)
        entries = manifest["entries"] if manifest else []
        formats = formats_by_member(entries)
        roles = _classify(names, formats, members)
        if not roles["cellml"]:
            raise OmexImportError(
                "the archive contains no .cellml or .mmt file, so there is no model "
                "to load."
            )

        master = _master_from_manifest(entries)
        ordered = list(roles["cellml"])
        if master:
            # Compare on the basename: manifest locations are relative and may or
            # may not carry a leading "./" or a directory prefix.
            base = Path(master).name.lower()
            ordered.sort(key=lambda n: Path(n).name.lower() != base)

        def read_first(role_members):
            for m in role_members:
                return Path(m).name, members[m]
            return None, None

        cellml = {Path(n).name: members[n] for n in ordered}
        obs_name, obs_bytes = read_first(roles["obs"])
        params_name, params_bytes = read_first(roles["params"])
        cfg_name, cfg_bytes = read_first(roles["module_config"])

    out = {
        "cellml": cellml,
        "master": Path(ordered[0]).name if ordered else None,
        "obs": (obs_name, obs_bytes) if obs_bytes is not None else None,
        "params": (params_name, params_bytes) if params_bytes is not None else None,
        "module_config": (cfg_name, cfg_bytes) if cfg_bytes is not None else None,
        # Everything, under its archive-relative name, for re-emission (#290).
        "members": members,
        "manifest": manifest,
        # The role members by archive-relative name, so a writer can tell which
        # member to replace without re-running the name heuristics.
        "roles": {
            "cellml": ordered,
            "obs": list(roles["obs"]),
            "params": list(roles["params"]),
            "module_config": list(roles["module_config"]),
        },
    }
    return out


def save_module_config(data: bytes, out_dir: str | None) -> str | None:
    """Keep PhLynx's editor state beside the outputs so it can be reopened (#149).

    Validated as JSON before saving -- writing a corrupt file under a name PhLynx
    will try to read is worse than not writing one. Never fatal: the model still
    imported, and this is a convenience.
    """
    if not data or not out_dir:
        return None
    try:
        json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    try:
        target = Path(out_dir) / MODULE_CONFIG_NAME
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return str(target)
    except OSError:
        return None
