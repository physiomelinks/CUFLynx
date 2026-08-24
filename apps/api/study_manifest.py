"""A study says where its own files are, instead of CUFLynx guessing.

Everything CUFLynx knows about a run directory it produced is a convention: the
emulator is under ``emulators/``, the calibrated model is ``<prefix>_calibrated.cellml``
at the top level, and when several run directories share a folder the most recently
written one is the one meant. Those rules are right for a directory this app produced
and are guesses everywhere else, and guesses here do not fail loudly -- they return a
different study's numbers. A folder holding nine runs answered with an eighteen-parameter
fit belonging to none of them, and nothing in the result said so.

So a study may leave a ``cuflynx_study.json`` naming its own parts. When one is present
it is believed and nothing is inferred; when it is absent the conventions still apply,
so every directory that loads today keeps loading.

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

#: Schema this reader understands. A manifest declaring a higher major version is
#: refused rather than read for the parts that look familiar: the point of the file is
#: that it is believed, and a half-understood manifest is worse than none.
SCHEMA = 1

#: Single-path keys, resolved and checked. Anything else in the file is carried through
#: untouched, so a writer can record more than this reader knows about.
PATH_KEYS = ("model", "base_model", "obs_data", "params_for_id", "emulator")


class ManifestError(Exception):
    """The manifest is there but cannot be trusted."""


def path(output_dir: str) -> str:
    return os.path.join(output_dir or "", MANIFEST_NAME)


def exists(output_dir: str) -> bool:
    return os.path.isfile(path(output_dir))


def read(output_dir: str) -> dict | None:
    """The manifest in ``output_dir``, resolved, or ``None`` if there isn't one.

    Returns the declared values with paths made absolute, plus ``missing``: the
    declarations whose target is not on disk. Those are reported rather than quietly
    replaced by a guess -- a manifest that names a file which is not there is a broken
    study, and the caller should be able to say so.

    Raises :class:`ManifestError` for a file that exists but cannot be read, for the
    same reason: silently falling back to conventions would hide a corrupt study behind
    a plausible-looking answer.
    """
    location = path(output_dir)
    if not os.path.isfile(location):
        return None

    try:
        with open(location, encoding="utf-8-sig") as handle:
            raw = json.load(handle)
    except (OSError, ValueError) as error:
        raise ManifestError(f"{location} could not be read: {error}") from error

    if not isinstance(raw, dict):
        raise ManifestError(f"{location} is not a JSON object")

    declared = raw.get("schema")
    if not isinstance(declared, int):
        raise ManifestError(f"{location} does not declare an integer 'schema'")
    if declared > SCHEMA:
        raise ManifestError(
            f"{location} declares schema {declared} and this CUFLynx understands "
            f"{SCHEMA}. Update CUFLynx rather than reading it partially.")

    base = os.path.dirname(os.path.abspath(location))
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

    runs = []
    for entry in raw.get("runs") or []:
        if not isinstance(entry, dict) or not entry.get("dir"):
            continue
        run = dict(entry)
        run["dir"] = _resolve(base, entry["dir"])
        if not os.path.isdir(run["dir"]):
            missing.append(f"run dir ({entry['dir']})")
            continue
        runs.append(run)
    resolved["runs"] = runs

    resolved["manifest_path"] = location
    resolved["missing"] = missing
    return resolved


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
    relative = os.path.relpath(full, base)
    if relative.split(os.sep)[0] == os.pardir:
        return full
    return relative
