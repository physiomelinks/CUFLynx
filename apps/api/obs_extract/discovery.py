"""Walk a directory of recordings and work out how they group.

Extraction settings -- which features, what stimulus, how many sweeps -- are
shared by every recording of the same kind, so the first thing a scan has to do
is decide what "the same kind" means. Two labels do it:

``protocol``
    The compound or experiment family. Taken from the sub-directory the file
    sits in, because that is how these corpora are organised on disk
    (``Wistar/4AP/...``, ``Wistar/Rilu/...``). A file at the root has no
    directory to name it, so its protocol is its subprotocol.

``subprotocol``
    The stimulus waveform: ``UniqueAp``, ``Kv-90``, ``Currentsteps``. Taken from
    the filename, which in this corpus looks like
    ``200926_005.1.1..1.1.1.UniqueAp.1.wcp`` -- dot-separated, the informative
    token buried among index numbers.

Both are **inferred, then editable**. The inference is right for the corpus it
was written against and will be wrong somewhere else, so nothing downstream may
depend on the rule -- only on the values, which the user can retype per file or
retag per group. That is why this module returns labels rather than enforcing a
directory convention.
"""

from __future__ import annotations

import os
import re

from .readers import SUPPORTED_SUFFIXES, probe

#: A dot-separated token that carries no information: pure digits, or digits and
#: underscores (``005``, ``1``, ``200926_005``). Used to find the one token in a
#: filename that names the waveform.
_UNINFORMATIVE = re.compile(r"^[\d_]*$")

#: ``YYMMDD`` at the start of a filename, the recording date in this corpus.
_DATE_PREFIX = re.compile(r"^(\d{2})(\d{2})(\d{2})")


def subprotocol_from_filename(filename: str) -> str:
    """The waveform name in a dot-separated filename, or the stem.

    ``200926_005.1.1..1.1.1.UniqueAp.1.wcp`` -> ``UniqueAp``.

    Takes the **last** informative token rather than the first, because the
    leading token is the date-and-cell identifier and the trailing ones are
    repeat indices. Empty parts (the ``..``) are skipped. A filename with no
    informative token at all falls back to the whole stem, which is at least
    stable and groups identical names together.
    """
    stem = os.path.splitext(os.path.basename(str(filename or "")))[0]
    parts = [p for p in stem.split(".") if p]
    informative = [p for p in parts if not _UNINFORMATIVE.match(p)]
    if not informative:
        return stem
    # The first part is the date/cell id (``200926_005``); it is informative by
    # the regex but it names the recording, not the waveform. Drop it when there
    # is something else to use.
    if len(informative) > 1 and informative[0] == parts[0]:
        informative = informative[1:]
    return informative[-1]


def date_from_filename(filename: str) -> str | None:
    """``YYYY-MM-DD`` from a ``YYMMDD`` filename prefix, or None.

    Only for display and the report -- nothing keys on it. Two-digit years are
    read as 20xx, which is right for this corpus and wrong in 2100.
    """
    m = _DATE_PREFIX.match(os.path.basename(str(filename or "")))
    if not m:
        return None
    yy, mm, dd = m.groups()
    if not ("01" <= mm <= "12" and "01" <= dd <= "31"):
        return None
    return f"20{yy}-{mm}-{dd}"


def group_key(protocol: str, subprotocol: str) -> str:
    """``"protocol|subprotocol"`` -- the key settings are shared under.

    The separator is ``|`` because it cannot occur in a path component on any
    platform this runs on, so the key round-trips through JSON and back into two
    labels without an escape rule.
    """
    return f"{protocol}|{subprotocol}"


def split_group_key(key: str) -> tuple[str, str]:
    protocol, _, subprotocol = str(key).partition("|")
    return protocol, subprotocol


def case_name(root: str, path: str) -> str:
    """A short, unique-enough name for one recording.

    ``<subdir>_<filename>`` for a file in a protocol directory, the filename
    alone at the root. Used as the obs_data ``experiment_ids`` entry and in the
    report, so it wants to be readable rather than a full path -- and it is what
    the deferred drug-condition pairing will key on.
    """
    rel = os.path.relpath(path, root)
    parts = rel.split(os.sep)
    if len(parts) == 1:
        return parts[0]
    return f"{parts[-2]}_{parts[-1]}"


def discover(
    root: str,
    *,
    recurse: bool = True,
    suffixes: tuple[str, ...] | list[str] = SUPPORTED_SUFFIXES,
    exclude: tuple[str, ...] | list[str] = (),
    reader_opts: dict[str, dict] | None = None,
    probe_files: bool = True,
) -> dict:
    """Every readable recording under ``root``, grouped and probed.

    Returns ``{"root", "datasets": [...], "groups": [...], "warnings": [...]}``.
    A dataset entry carries the inferred labels plus whatever :func:`probe`
    could tell us -- including ``readable: False`` and a reason, because one
    corrupt file must not fail a scan of four hundred.

    ``exclude`` matches on the file's basename or its case name, so a skip list
    survives the directory being moved. ``reader_opts`` is keyed by case name,
    letting a per-dataset setting (a ``.npy`` sample rate, a channel role) reach
    the probe on a rescan.
    """
    root = os.path.abspath(os.path.expanduser(str(root)))
    if not os.path.isdir(root):
        from .errors import ObsExtractError  # noqa: PLC0415 - avoid a cycle at import

        raise ObsExtractError(f"{root}: not a directory")

    wanted = {s.lower() for s in suffixes}
    skip = {str(x) for x in exclude}
    reader_opts = reader_opts or {}
    datasets: list[dict] = []
    warnings: list[str] = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Sorted in place so the walk is deterministic; a scan that reorders
        # itself between runs makes a saved config's dataset list churn.
        dirnames.sort()
        if not recurse and dirpath != root:
            dirnames[:] = []
            continue
        for fname in sorted(filenames):
            if os.path.splitext(fname)[1].lower() not in wanted:
                continue
            path = os.path.join(dirpath, fname)
            case = case_name(root, path)
            if fname in skip or case in skip:
                continue
            rel_dir = os.path.relpath(dirpath, root)
            subprotocol = subprotocol_from_filename(fname)
            protocol = subprotocol if rel_dir == "." else rel_dir.split(os.sep)[0]

            entry = {
                "path": path,
                "case_name": case,
                "protocol": protocol,
                "subprotocol": subprotocol,
                "group": group_key(protocol, subprotocol),
                "date": date_from_filename(fname),
                "size_bytes": _size(path),
            }
            if probe_files:
                entry.update(probe(path, **reader_opts.get(case, {})))
                # probe() re-states path and format; keep its richer values but
                # do not let it overwrite the inferred labels.
                entry["path"] = path
            datasets.append(entry)

    groups = _summarise_groups(datasets)
    unreadable = [d for d in datasets if probe_files and not d.get("readable")]
    if unreadable:
        warnings.append(
            f"{len(unreadable)} of {len(datasets)} file(s) could not be read; "
            f"see each row for the reason.")
    return {"root": root, "datasets": datasets, "groups": groups, "warnings": warnings}


def _size(path: str) -> int | None:
    try:
        return os.path.getsize(path)
    except OSError:  # pragma: no cover
        return None


def _summarise_groups(datasets: list[dict]) -> list[dict]:
    """One row per ``protocol|subprotocol``, in first-seen order.

    First-seen rather than sorted, so the GUI's group order follows the walk and
    a user who scrolled to a group finds it in the same place after a rescan.
    """
    order: list[str] = []
    by_key: dict[str, dict] = {}
    for d in datasets:
        key = d["group"]
        if key not in by_key:
            order.append(key)
            by_key[key] = {
                "group": key, "protocol": d["protocol"],
                "subprotocol": d["subprotocol"], "n_datasets": 0,
                "n_readable": 0, "formats": set(), "sweep_counts": set(),
            }
        row = by_key[key]
        row["n_datasets"] += 1
        row["n_readable"] += 1 if d.get("readable") else 0
        row["formats"].add(d.get("format") or "")
        if d.get("sweep_count") is not None:
            row["sweep_counts"].add(int(d["sweep_count"]))
    out = []
    for key in order:
        row = by_key[key]
        row["formats"] = sorted(f for f in row["formats"] if f)
        # The GUI offers a sweep limit per group, so it needs to know the
        # smallest count any member has -- a limit above that silently includes
        # fewer sweeps from some files than others.
        counts = sorted(row.pop("sweep_counts"))
        row["min_sweeps"] = counts[0] if counts else None
        row["max_sweeps"] = counts[-1] if counts else None
        out.append(row)
    return out
