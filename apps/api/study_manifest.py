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

Paths resolve relative to the manifest, so a study directory can be moved or copied
whole; absolute paths are allowed and left alone, which is what a shared artefact
outside the tree needs.
"""

from __future__ import annotations

import json
import os

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


class ManifestError(Exception):
    """The manifest is there but cannot be trusted."""


def path(output_dir: str) -> str:
    """Where :func:`write` puts a manifest: the unprefixed name."""
    return os.path.join(output_dir or "", MANIFEST_NAME)


def candidates(output_dir: str) -> list[str]:
    """Every manifest directly in ``output_dir``, newest first."""
    if not output_dir or not os.path.isdir(output_dir):
        return []
    found = [entry.path for entry in os.scandir(output_dir)
             if entry.is_file() and (entry.name == MANIFEST_NAME
                                     or entry.name.endswith(MANIFEST_SUFFIX))]
    return sorted(found, key=os.path.getmtime, reverse=True)


def find(output_dir: str, prefix: str | None = None) -> str | None:
    """The manifest to read for ``output_dir``, or ``None``.

    ``output_dir`` may also be a manifest file itself. With several in one directory the
    one named for ``prefix`` (``<prefix>_cuflynx_study.json``) wins, then the newest; the
    caller can list the rest with :func:`candidates`.
    """
    if output_dir and os.path.isfile(output_dir):
        return output_dir
    found = candidates(output_dir)
    if prefix:
        named = os.path.join(output_dir, prefix + MANIFEST_SUFFIX)
        if named in found:
            return named
    return found[0] if found else None


def exists(output_dir: str) -> bool:
    return find(output_dir) is not None


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


def read(output_dir: str, prefix: str | None = None) -> dict | None:
    """The manifest for ``output_dir`` (a directory or the file), resolved, or ``None``.

    Returns the declared values with paths made absolute, plus ``missing``: the
    declarations whose target is not on disk. Those are reported rather than quietly
    replaced by a guess -- a manifest that names a file which is not there is a broken
    study, and the caller should be able to say so.

    Raises :class:`ManifestError` for a file that exists but cannot be read, for the
    same reason: silently falling back to conventions would hide a corrupt study behind
    a plausible-looking answer.
    """
    location = find(output_dir, prefix)
    if not location:
        return None
    raw = load_raw(location)
    return resolve(raw, base_dir(raw, location), location)


def resolve(raw: dict, base: str, location: str | None = None) -> dict:
    """``raw`` with every declared path made absolute against ``base``."""
    resolved = dict(raw)
    missing: list[str] = []

    for key in PATH_KEYS:
        value = raw.get(key)
        if not value:
            continue
        full = _resolve(base, value)
        resolved[key] = full
        if not os.path.exists(full):
            missing.append(f"{key} ({value})")

    funcs = {}
    for kind, value in (raw.get("user_funcs") or {}).items():
        if kind not in USER_FUNC_KINDS or not value:
            continue
        full = _resolve(base, value)
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
            item["dir"] = _resolve(base, entry["dir"])
            if item.get("obs_data"):
                item["obs_data"] = _resolve(base, item["obs_data"])
            if not os.path.isdir(item["dir"]):
                missing.append(f"{label} ({entry['dir']})")
                continue
            entries.append(item)
        resolved[section] = entries

    resolved["study_dir"] = base
    resolved["manifest_path"] = location
    resolved["missing"] = missing
    return resolved


def is_manifest_name(name: str) -> bool:
    """Whether ``name`` is spelled like a manifest: ``cuflynx_study.json`` or ``*_cuflynx_study.json``."""
    base = os.path.basename(str(name or ""))
    return base == MANIFEST_NAME or base.endswith(MANIFEST_SUFFIX)


def study_location(raw, location: str | None = None,
                   where: str = "the study manifest") -> dict:
    """Which outputs directory a manifest opens: ``{"dir", "study_name", "file_prefix"}``.

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
                "This study manifest does not record its study_dir (it predates it), so "
                "where the study is cannot be told from the file alone. Set the outputs "
                "directory to the folder holding it instead.")
        if not os.path.isdir(directory):
            raise ManifestError(
                f"The study this manifest describes is not on this machine: "
                f"{directory} does not exist.")
    return {
        "dir": directory,
        "study_name": raw.get("study_name"),
        "file_prefix": raw.get("file_prefix"),
    }


def user_funcs(output_dir: str | None) -> dict:
    """``{kind: path}`` for the user funcs a study in ``output_dir`` declares.

    Empty when there is no manifest or it cannot be read: the funcs then live where they
    always have, under ``<output_dir>/user_funcs/``.
    """
    if not output_dir:
        return {}
    try:
        manifest = read(output_dir)
    except ManifestError:
        return {}
    return dict((manifest or {}).get("user_funcs") or {})


def _resolve(base: str, value: str) -> str:
    value = str(value)
    if os.path.isabs(value):
        return os.path.normpath(value)
    return os.path.normpath(os.path.join(base, value))


def write(output_dir: str, study: dict) -> str:
    """Write a manifest, storing paths relative to it where that is possible.

    Relative where it can be, so a study directory survives being moved or copied;
    absolute otherwise, which is what a shared emulator sitting outside the tree needs.
    Written whole rather than merged: a manifest describes one run of one study, and
    half of a previous one left in it would describe nothing.
    """
    os.makedirs(output_dir, exist_ok=True)
    base = os.path.abspath(output_dir)
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

    location = path(output_dir)
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
