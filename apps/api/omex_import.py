"""Unpack a COMBINE archive (.omex) into the files CUFLynx already understands (#149).

An OMEX is a zip with a `manifest.xml` listing its contents. A user with a whole
study in one archive should be able to drop it on *any* of the import boxes and
get the model, obs_data and params_for_id all loaded, rather than unzipping it
and dropping three files in the right order.

Two things this deliberately does not do:

* It does not require the manifest. Real archives in the wild have missing or
  wrong manifests, and the contents are identifiable anyway -- a `.cellml` is a
  CellML, `*params*.csv` is a params_for_id. The manifest is used to pick the
  *master* model when it says which one is master, because that is the one thing
  file names cannot tell you.
* It does not interpret `module_config.json`. That is PhLynx's own state; CUFLynx
  keeps it beside the outputs so PhLynx can be reopened with the same memory
  (#149), and otherwise leaves it entirely alone.
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


def _master_from_manifest(zf: zipfile.ZipFile) -> str | None:
    """The location the manifest marks ``master="true"``, if any.

    Which CellML is the main model is the one thing file names cannot tell you,
    so this is the manifest's job. Everything else is classified by name.
    """
    for name in zf.namelist():
        if not name.lower().endswith("manifest.xml"):
            continue
        try:
            root = ET.fromstring(zf.read(name))
        except ET.ParseError:
            continue
        for entry in root.iter():
            if not entry.tag.endswith("content"):
                continue
            if str(entry.get("master", "")).lower() != "true":
                continue
            loc = (entry.get("location") or "").lstrip("./")
            if loc.lower().endswith(MODEL_SUFFIXES):
                return loc
    return None


def _classify(names: list[str]) -> dict:
    """Sort archive members into the roles CUFLynx imports."""
    cellml = [n for n in names if n.lower().endswith(".cellml")]
    myokit = [n for n in names if n.lower().endswith(".mmt")]
    csvs = [n for n in names if n.lower().endswith(".csv")]
    jsons = [n for n in names if n.lower().endswith(".json")]

    params = [n for n in csvs if re.search(r"param", Path(n).name, re.I)]
    module_config = [n for n in jsons if Path(n).name == MODULE_CONFIG_NAME]
    # obs_data is the remaining JSON; prefer an obviously named one so a stray
    # metadata file does not get loaded as observations.
    obs_named = [n for n in jsons if re.search(r"obs", Path(n).name, re.I)]
    obs = obs_named or [n for n in jsons if n not in module_config]

    return {
        # A .mmt only counts when there is no CellML: an archive holding both has
        # presumably already been converted, and the CellML is the authoritative
        # copy -- re-converting would silently prefer the source over the file the
        # author chose to ship.
        "cellml": cellml or myokit,
        "params": params or csvs,
        "obs": obs,
        "module_config": module_config,
    }


def unpack(data: bytes) -> dict:
    """Read an archive into ``{cellml: {name: bytes}, obs, params, module_config}``.

    ``cellml`` keeps *every* CellML in the archive, with the master first: a
    non-flattened model needs its sister files, and the existing upload path
    already knows how to flatten a bundle. An archive whose model is a Myokit
    ``.mmt`` yields that instead, for the caller to convert.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise OmexImportError(f"not a readable archive: {exc}") from exc

    with zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if not names:
            raise OmexImportError("the archive is empty")
        roles = _classify(names)
        if not roles["cellml"]:
            raise OmexImportError(
                "the archive contains no .cellml or .mmt file, so there is no model "
                "to load."
            )

        master = _master_from_manifest(zf)
        ordered = list(roles["cellml"])
        if master:
            # Compare on the basename: manifest locations are relative and may or
            # may not carry a leading "./" or a directory prefix.
            base = Path(master).name.lower()
            ordered.sort(key=lambda n: Path(n).name.lower() != base)

        def read_first(members):
            for m in members:
                return Path(m).name, zf.read(m)
            return None, None

        cellml = {Path(n).name: zf.read(n) for n in ordered}
        obs_name, obs_bytes = read_first(roles["obs"])
        params_name, params_bytes = read_first(roles["params"])
        cfg_name, cfg_bytes = read_first(roles["module_config"])

    out = {
        "cellml": cellml,
        "master": Path(ordered[0]).name if ordered else None,
        "obs": (obs_name, obs_bytes) if obs_bytes is not None else None,
        "params": (params_name, params_bytes) if params_bytes is not None else None,
        "module_config": (cfg_name, cfg_bytes) if cfg_bytes is not None else None,
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
