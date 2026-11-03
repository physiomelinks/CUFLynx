"""A study says where its own files are, instead of CUFLynx guessing.

Everything CUFLynx knows about a run directory it produced is a convention: the
emulator is under ``emulators/``, the calibrated model is ``<prefix>_calibrated.cellml``
at the top level, and when several run directories share a folder the most recently
written one is the one meant. Those rules are right for a directory this app produced
and are guesses everywhere else, and guesses here do not fail loudly -- they return a
different study's numbers. A folder holding nine runs answered with an eighteen-parameter
fit belonging to none of them, and nothing in the result said so.

So a study may leave a ``<study_name>_cuflynx_study.json`` (or, the older spelling, a
bare ``cuflynx_study.json``) naming its own parts. circulatory_autogen writes it: every
calibration, UQ, sensitivity analysis and emulator run records itself in it, along with
the model, obs_data, params_for_id, the user funcs and the solver settings
(``libcuflynx.utilities.cuflynx_study``). When one is present it is believed and nothing
is inferred; when it is absent the conventions still apply, so every directory that
loads today keeps loading. Dropping the file into the app opens its study.

The other thing declaration buys is *sharing*. One emulator trained on a joint dataset
serves several obs_data -- that is the whole point of training it jointly -- and a
convention that looks under ``<selected>/emulators/`` forces a copy per dataset. A
manifest can point at the one bundle.

Relative paths resolve against the folder holding the manifest, so a working dir can be
moved, copied or zipped whole. libcuflynx and CUFLynx copy every input into
``<working dir>/inputs/`` and record only relative paths. A manifest written before that
records inputs by absolute path and an absolute ``study_dir``; reading one, an absolute
path under the recorded ``study_dir`` is rebased onto the manifest's folder, and one that
no longer exists is looked for under ``inputs/<kind>/`` by its name. ``study_dir`` is
otherwise only a hint, for a manifest read away from its folder.

An output directory the study wrote outside its working dir (an absolute ``sa_options``
``output_dir`` elsewhere) is recorded by absolute path with ``"outside": true``: it does not
travel with the working dir. :func:`outside_warnings` names those, so whatever opens a study
(a picked folder, a dropped manifest, an unzipped working dir, an archive) says which outputs
were left behind rather than leaving a panel quietly empty.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import zipfile

import working_dir

MANIFEST_NAME = "cuflynx_study.json"

#: What circulatory_autogen writes: ``<study_name>_cuflynx_study.json`` in the study's
#: ``param_id_output_dir``, one per study, so several studies can share a directory.
#: ``cuflynx_study.json`` with no prefix is the older spelling and is still read.
MANIFEST_SUFFIX = "_" + MANIFEST_NAME

#: Format of a study manifest carried inside an OMEX archive, so the archive reader can
#: classify it by its declared format rather than by its name.
OMEX_FORMAT = "application/x.vnd.cuflynx-study+json"

#: Schema this reader understands. A manifest declaring a higher major version is
#: refused rather than read for the parts that look familiar: the point of the file is
#: that it is believed, and a half-understood manifest is worse than none.
SCHEMA = 1

#: Single-path keys, resolved and checked. Anything else in the file is carried through
#: untouched, so a writer can record more than this reader knows about.
PATH_KEYS = ("model", "base_model", "obs_data", "params_for_id", "emulator", "user_inputs")

#: The study's parts, by the name both the folder manifest and the one embedded in an
#: OMEX archive use. ``omex_import`` keeps its own role names (``cellml`` / ``obs`` /
#: ``params``) for classifying members and maps them onto these.
KEYS = PATH_KEYS + ("user_funcs", "solver_info", "pre_time", "sim_time", "runs",
                    "sensitivity", "file_prefix", "study_name", "study_dir")

#: ``user_funcs`` kinds, as ``user_funcs.py`` names them.
USER_FUNC_KINDS = ("operation", "cost", "modifier")

#: The ``inputs/`` subfolder each path key's file is kept in (``working_dir.KINDS``).
INPUT_KIND = {"model": "model", "base_model": "model", "obs_data": "obs_data",
              "params_for_id": "params_for_id", "user_inputs": "user_inputs"}


class ManifestError(Exception):
    """The manifest is there but cannot be trusted."""


def path(working_dir: str) -> str:
    """Where :func:`write` puts a manifest: the unprefixed name."""
    return os.path.join(working_dir or "", MANIFEST_NAME)


def candidates(working_dir: str) -> list[str]:
    """Every manifest directly in ``working_dir``, newest first."""
    if not working_dir or not os.path.isdir(working_dir):
        return []
    found = [entry.path for entry in os.scandir(working_dir)
             if entry.is_file() and (entry.name == MANIFEST_NAME
                                     or entry.name.endswith(MANIFEST_SUFFIX))]
    return sorted(found, key=os.path.getmtime, reverse=True)


def find(working_dir: str, prefix: str | None = None) -> str | None:
    """The manifest to read for ``working_dir``, or ``None``.

    ``working_dir`` may also be a manifest file itself. With several in one directory the
    one named for ``prefix`` (``<prefix>_cuflynx_study.json``) wins, then the newest; the
    caller can list the rest with :func:`candidates`.
    """
    if working_dir and os.path.isfile(working_dir):
        return working_dir
    found = candidates(working_dir)
    if prefix:
        named = os.path.join(working_dir, prefix + MANIFEST_SUFFIX)
        if named in found:
            return named
    return found[0] if found else None


def exists(working_dir: str) -> bool:
    return find(working_dir) is not None


def load_raw(location: str) -> dict:
    """The manifest at ``location`` as stored, checked but not resolved."""
    try:
        with open(location, encoding="utf-8-sig") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as error:
        raise ManifestError(f"{location} could not be read: {error}") from error
    check(raw, location)
    return raw


def check(raw, where: str = "the manifest") -> None:
    """Refuse anything this reader should not believe."""
    if not isinstance(raw, dict):
        raise ManifestError(f"{where} is not a JSON object")
    declared = raw.get("schema")
    if not isinstance(declared, int):
        raise ManifestError(f"{where} does not declare an integer 'schema'")
    if declared > SCHEMA:
        raise ManifestError(
            f"{where} declares schema {declared} and this CUFLynx understands "
            f"{SCHEMA}. Update CUFLynx rather than reading it partially.")


def _relative_values(raw: dict) -> list[str]:
    values = [raw.get(key) for key in PATH_KEYS]
    values += list((raw.get("user_funcs") or {}).values())
    for section in ("runs", "sensitivity"):
        values += [entry.get("dir") for entry in raw.get(section) or []
                   if isinstance(entry, dict)]
    return [str(v) for v in values if v and not os.path.isabs(str(v))]


def base_dir(raw: dict, location: str) -> str:
    """What the manifest's relative paths are relative to.

    Its own directory when it is sitting in its study -- which is what makes a study
    directory movable. A copy that has been taken out of its study (a dropped file, one
    unpacked from an OMEX archive) resolves against the ``study_dir`` it records instead.
    """
    here = os.path.dirname(os.path.abspath(location))
    relative = _relative_values(raw)
    if not relative or any(os.path.exists(_resolve(here, v)) for v in relative):
        return here
    recorded = raw.get("study_dir")
    if recorded and os.path.isdir(recorded):
        return os.path.abspath(recorded)
    return here


def read(working_dir: str, prefix: str | None = None) -> dict | None:
    """The manifest for ``working_dir`` (a directory or the file), resolved, or ``None``.

    Returns the declared values with paths made absolute, plus ``missing``: the
    declarations whose target is not on disk. Those are reported rather than quietly
    replaced by a guess -- a manifest that names a file which is not there is a broken
    study, and the caller should be able to say so.

    Raises :class:`ManifestError` for a file that exists but cannot be read, for the
    same reason: silently falling back to conventions would hide a corrupt study behind
    a plausible-looking answer.
    """
    location = find(working_dir, prefix)
    if not location:
        return None
    raw = load_raw(location)
    return resolve(raw, base_dir(raw, location), location)


def resolve(raw: dict, base: str, location: str | None = None) -> dict:
    """``raw`` with every declared path made absolute against ``base``."""
    resolved = dict(raw)
    missing: list[str] = []
    recorded = raw.get("study_dir")

    def at(value, kind=None):
        return _resolve(base, value, recorded, kind)

    for key in PATH_KEYS:
        value = raw.get(key)
        if not value:
            continue
        full = at(value, INPUT_KIND.get(key))
        resolved[key] = full
        if not os.path.exists(full):
            missing.append(f"{key} ({value})")

    funcs = {}
    for kind, value in (raw.get("user_funcs") or {}).items():
        if kind not in USER_FUNC_KINDS or not value:
            continue
        full = at(value, "funcs")
        if os.path.isfile(full):
            funcs[kind] = full
        else:
            missing.append(f"{kind} funcs ({value})")
    resolved["user_funcs"] = funcs

    for section, label in (("runs", "run dir"), ("sensitivity", "sensitivity dir")):
        entries = []
        for entry in raw.get(section) or []:
            if not isinstance(entry, dict) or not entry.get("dir"):
                continue
            item = dict(entry)
            item["dir"] = at(entry["dir"])
            if item.get("obs_data"):
                item["obs_data"] = at(item["obs_data"], "obs_data")
            if not os.path.isdir(item["dir"]):
                missing.append(f"{label} ({entry['dir']})")
                continue
            entries.append(item)
        resolved[section] = entries

    resolved["study_dir"] = base
    resolved["manifest_path"] = location
    resolved["outside_outputs"] = outside_outputs(raw)
    resolved["missing"] = missing
    return resolved


def is_manifest_name(name: str) -> bool:
    """Whether ``name`` is spelled like a manifest: ``cuflynx_study.json`` or ``*_cuflynx_study.json``."""
    base = os.path.basename(str(name or ""))
    return base == MANIFEST_NAME or base.endswith(MANIFEST_SUFFIX)


def study_location(raw, location: str | None = None,
                   where: str = "the study manifest") -> dict:
    """Which working dir a manifest opens: ``{"dir", "study_name", "file_prefix"}``.

    ``location`` is where the manifest is on disk, when that is known (a desktop drop, a
    picked file, a ``cuflynx://open?study=`` link); the study is then the directory
    holding it. A manifest with no location (a browser drop carries only its contents)
    opens the ``study_dir`` it records. Raises :class:`ManifestError` when the manifest
    cannot be believed or names no directory on this machine.
    """
    check(raw, where)
    if location and os.path.isfile(location):
        directory = os.path.dirname(os.path.abspath(location))
    else:
        directory = raw.get("study_dir")
        if not directory:
            raise ManifestError(
                "This study manifest records only paths relative to its own folder, and a "
                "file dropped from the browser does not say where that folder is. Set the "
                "working dir to the folder holding it instead, or drop the folder.")
        if not os.path.isdir(directory):
            raise ManifestError(
                f"The study this manifest describes is not on this machine: "
                f"{directory} does not exist.")
    return {
        "dir": directory,
        "study_name": raw.get("study_name"),
        "file_prefix": raw.get("file_prefix"),
        "warnings": outside_warnings(raw),
    }


def _flagged_outside(raw: dict) -> list[dict]:
    """:func:`outside_outputs` for a CA that predates ``cuflynx_study.outside_outputs``."""
    return [{"section": section, "dir": entry["dir"], "method": entry.get("method")}
            for section in ("runs", "sensitivity") for entry in raw.get(section) or []
            if isinstance(entry, dict) and entry.get("outside") and entry.get("dir")]


def outside_outputs(raw: dict | None) -> list[dict]:
    """The output directories a manifest records outside its working dir, as
    ``[{"section", "dir", "method"}]``: libcuflynx's ``cuflynx_study.outside_outputs``, or the
    same reading of the ``"outside": true`` flags when the CA in use predates it."""
    if not isinstance(raw, dict):
        return []
    try:
        import ca_imports  # noqa: PLC0415 - CA is optional here
        ca_imports.ensure_ca_path()
        listed = ca_imports.ca_from("utilities.cuflynx_study", "outside_outputs")
    except Exception:  # noqa: BLE001 - no CA at all, or one predating outside_outputs
        listed = _flagged_outside
    try:
        return list(listed(raw))
    except Exception:  # a CA that reads the manifest differently: the flags still say it
        return _flagged_outside(raw)


def outside_warnings(manifest: dict | None) -> list[str]:
    """What to tell someone opening a study whose manifest records outputs outside its
    working dir: one warning naming each, or none. ``manifest`` is a raw manifest or a
    :func:`read` result (which carries ``outside_outputs``)."""
    if not isinstance(manifest, dict):
        return []
    found = manifest.get("outside_outputs")
    if found is None:
        found = outside_outputs(manifest)
    if not found:
        return []
    named = "; ".join(
        f"{item['dir']} ({item['section']}{', ' + item['method'] if item.get('method') else ''}"
        f"{'' if os.path.isdir(str(item['dir'])) else ', missing here'})" for item in found)
    return [f"This study wrote output outside its working dir, which was not carried with "
            f"the study: {named}. Only what is inside the working dir travels with it."]


def user_funcs(working_dir: str | None) -> dict:
    """``{kind: path}`` for the user funcs a study in ``working_dir`` declares.

    Empty when there is no manifest or it cannot be read: the funcs then live where
    CUFLynx keeps them, ``<working_dir>/inputs/funcs/`` (``user_funcs/`` in an older folder).
    """
    if not working_dir:
        return {}
    try:
        manifest = read(working_dir)
    except ManifestError:
        return {}
    return dict((manifest or {}).get("user_funcs") or {})


def _resolve(base: str, value: str, recorded: str | None = None,
             kind: str | None = None) -> str:
    """``value`` as a path on this machine.

    Relative: against ``base``, the manifest's folder. Absolute (an older manifest):
    as written when it exists; else, when it lies under the ``study_dir`` the manifest
    recorded, the same place under ``base`` (the folder was moved); else, for an input
    of ``kind``, the version of ``<name>`` stored last in ``<base>/inputs/<kind>/``
    (``working_dir.latest``: the input was copied into the working dir). Otherwise as written, which the caller reports as missing.
    """
    value = str(value)
    if not os.path.isabs(value):
        return os.path.normpath(os.path.join(base, value))
    full = os.path.normpath(value)
    if os.path.exists(full):
        return full
    if recorded and os.path.isabs(str(recorded)):
        old = os.path.normpath(str(recorded))
        try:
            inside = os.path.commonpath([old, full]) == old
        except ValueError:
            inside = False
        if inside:
            moved = os.path.normpath(os.path.join(base, os.path.relpath(full, old)))
            if os.path.exists(moved):
                return moved
    if kind:
        copied = working_dir.latest(base, kind, os.path.basename(full))
        if copied is not None:
            return str(copied)
    return full


def write(working_dir: str, study: dict) -> str:
    """Write a manifest, storing paths relative to it where that is possible.

    Relative where it can be, so a study directory survives being moved or copied;
    absolute otherwise, which is what a shared emulator sitting outside the tree needs.
    Written whole rather than merged: a manifest describes one run of one study, and
    half of a previous one left in it would describe nothing.
    """
    os.makedirs(working_dir, exist_ok=True)
    base = os.path.abspath(working_dir)
    record: dict = {"schema": SCHEMA}

    for key, value in study.items():
        if key in PATH_KEYS and value:
            record[key] = _relative_if_sensible(base, value)
        elif key == "runs":
            record["runs"] = [
                {**entry, "dir": _relative_if_sensible(base, entry["dir"])}
                for entry in (value or []) if entry.get("dir")
            ]
        else:
            record[key] = value

    location = path(working_dir)
    with open(location, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2)
        handle.write("\n")
    return location


def _relative_if_sensible(base: str, value: str) -> str:
    """Relative when the target is inside the study, absolute when it is anywhere else.

    The split is what makes "a study directory can be moved" a promise rather than a
    hope: everything inside it is recorded relative and travels with it, and everything
    outside is recorded absolute and keeps resolving from wherever the study ends up. A
    ``..`` path would be neither -- correct only while the study stays exactly where it
    is, which is the case this file exists to stop being an assumption.
    """
    full = os.path.abspath(str(value))
    try:
        relative = os.path.relpath(full, base)
    except ValueError:
        # Windows raises rather than answering when the two paths are on different
        # drives, and "on another drive" is the strongest possible form of outside the
        # study -- no relative path exists at all. Exactly the shared-emulator case: a
        # study on D: referencing a bundle on C:.
        return full
    if relative.split(os.sep)[0] == os.pardir:
        return full
    return relative


# --- a zipped working dir ------------------------------------------------------------

#: What every member of an OMEX archive is listed in, at its root. A working dir has none,
#: and an OMEX carries a study manifest at its root too, so this is what tells them apart.
OMEX_MANIFEST = "manifest.xml"

#: Folders a zip tool adds that are not part of the study.
_ZIP_NOISE = ("__MACOSX/",)


class UnsafeZipError(ManifestError):
    """A member of the zip would be written outside the folder it is extracted into."""


def _unsafe_member(info: zipfile.ZipInfo) -> str:
    """Why ``info`` cannot be extracted safely, or ``""``."""
    name = info.filename
    if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
        return f"{name!r} is an absolute path"
    if ".." in re.split(r"[/\\]", name):
        return f"{name!r} climbs out of the folder (..)"
    if stat.S_ISLNK(info.external_attr >> 16):
        return f"{name!r} is a symbolic link"
    return ""


def _members(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    return [info for info in zf.infolist() if not info.filename.startswith(_ZIP_NOISE)]


def zip_study(zf: zipfile.ZipFile) -> dict | None:
    """What a zip holds: a working dir, an OMEX, or ``None`` for neither.

    A working dir is a zip with a study manifest (``*_cuflynx_study.json``) at its root or
    one folder down (a zip of the folder rather than of its contents), and no OMEX
    ``manifest.xml`` at its root. Returns ``{"kind": "omex"}`` or ``{"kind": "working_dir",
    "root", "manifest", "raw"}``: ``root`` is the folder prefix holding the manifest
    (``""`` or ``"<folder>/"``), ``raw`` the manifest as read. Raises
    :class:`UnsafeZipError` for a working dir with a member that would land outside the
    folder it is extracted into, and :class:`ManifestError` for a manifest that cannot be
    believed.
    """
    members = _members(zf)
    names = {info.filename for info in members}
    if OMEX_MANIFEST in names:
        return {"kind": "omex"}
    found = []
    for info in members:
        parts = info.filename.rstrip("/").split("/")
        if not info.is_dir() and len(parts) <= 2 and is_manifest_name(parts[-1]):
            found.append((len(parts), info.filename))
    if not found:
        return None
    unsafe = [reason for reason in map(_unsafe_member, members) if reason]
    if unsafe:
        raise UnsafeZipError(
            "This zip was not extracted: " + "; ".join(unsafe[:5])
            + (f" (and {len(unsafe) - 5} more)" if len(unsafe) > 5 else ""))
    _, member = min(found)
    try:
        raw = json.loads(zf.read(member).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:
        raise ManifestError(f"{member} in the zip could not be read: {error}") from error
    check(raw, member)
    root = member[: len(member) - len(os.path.basename(member))]
    return {"kind": "working_dir", "root": root, "manifest": member, "raw": raw}


def zip_study_name(found: dict, fallback: str) -> str:
    """A folder name for the study a zip holds: its ``study_name``, else its manifest's."""
    name = str(found["raw"].get("study_name") or "").strip()
    if not name:
        base = os.path.basename(found["manifest"])
        name = base[: -len(MANIFEST_SUFFIX)] if base.endswith(MANIFEST_SUFFIX) else ""
    name = re.sub(r"[\\/:*?\"<>|]+", "_", name or fallback).strip(" .")
    return name or "study"


def is_nonempty_dir(path: str | os.PathLike) -> bool:
    return os.path.isdir(path) and any(os.scandir(path))


def free_folder(parent: str | os.PathLike, name: str) -> str:
    """``<parent>/<name>``, or ``<name>_2``, ``_3``... when that is a non-empty folder or a file."""
    candidate = os.path.join(parent, name)
    n = 2
    while os.path.isfile(candidate) or is_nonempty_dir(candidate):
        candidate = os.path.join(parent, f"{name}_{n}")
        n += 1
    return candidate


def extract_study_zip(zf: zipfile.ZipFile, found: dict, dest: str | os.PathLike) -> str:
    """Extract the working dir :func:`zip_study` found into ``dest``; return the working dir.

    Members under the manifest's folder are written with that folder stripped, so the
    working dir is ``dest`` itself. When the zip also holds members outside that folder,
    nothing is stripped and the working dir is ``dest/<folder>``. Every member is checked
    again here: none is written outside ``dest``.
    """
    dest = os.path.abspath(str(dest))
    members = _members(zf)
    root = found["root"]
    strip = root if all(info.filename.startswith(root) for info in members) else ""
    for info in members:
        if _unsafe_member(info):
            raise UnsafeZipError(f"This zip was not extracted: {_unsafe_member(info)}")
        relative = info.filename[len(strip):]
        if not relative:
            continue
        target = os.path.abspath(os.path.join(dest, relative))
        if os.path.commonpath([dest, target]) != dest:
            raise UnsafeZipError(f"This zip was not extracted: {info.filename!r} is outside it")
        if info.is_dir():
            os.makedirs(target, exist_ok=True)
            continue
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with zf.open(info) as source, open(target, "wb") as handle:
            shutil.copyfileobj(source, handle)
    return os.path.join(dest, root[len(strip):].rstrip("/")) if root[len(strip):] else dest

