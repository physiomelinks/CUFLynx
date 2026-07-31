"""Accept a Myokit model by exporting it to CellML first (issue #27).

Everything downstream of the upload assumes CellML: the metadata parser, the
params_for_id naming (``component/variable``), the exported pipeline, and CA
itself. Rather than teach each of those about ``.mmt``, a dropped Myokit model is
converted once on the way in and the rest of the app never knows the difference.

The converted file is written to the outputs directory (as the issue asks) so the
user keeps an artefact they can inspect, re-import, or hand to circulatory_autogen
directly -- a conversion that existed only inside a temp dir would be invisible
and unreproducible.

Myokit is an optional dependency here in the same sense as elsewhere: it is
bundled in the packaged app but may be absent from a bare source checkout, so it
is imported lazily and a clear error is raised rather than an ImportError
traceback.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

# Myokit's own extension. `.txt` is deliberately not accepted: it would make any
# stray text file look like a model.
MYOKIT_SUFFIXES = (".mmt",)


class MyokitImportError(ValueError):
    """A Myokit model that could not be read or exported (surface as HTTP 422)."""


def is_myokit_filename(name: str) -> bool:
    return Path(str(name or "")).suffix.lower() in MYOKIT_SUFFIXES


def looks_like_myokit(data: bytes) -> bool:
    """Whether ``data`` is an ``.mmt`` file, judged by its own section headers.

    Content rather than extension, so a model dropped with the wrong name is
    still recognised -- and, more importantly, so an XML file named ``.mmt`` is
    not fed to the Myokit parser.
    """
    try:
        head = data[:4096].decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 - undecodable is not a Myokit model
        return False
    if head.lstrip().startswith("<"):
        return False  # XML: CellML, SBML, or an OMEX manifest
    # An .mmt is a sectioned file; [[model]] is the one every model has.
    return "[[model]]" in head


def cellml_from_myokit(data: bytes, *, filename: str, out_dir: str | None = None) -> tuple[bytes, str | None]:
    """Convert a Myokit ``.mmt`` to CellML 2.0.

    Returns ``(cellml_bytes, saved_path_or_None)``. ``saved_path`` is where the
    converted file was kept for the user; None when no output directory was
    given, in which case the conversion is still returned but not persisted.
    """
    try:
        import myokit  # noqa: PLC0415 - optional/heavy, imported on use
        from myokit.formats.cellml import CellML2Exporter  # noqa: PLC0415
    except ImportError as exc:
        raise MyokitImportError(
            "Myokit is not installed, so a .mmt model cannot be converted to CellML. "
            "Install myokit, or export the model to CellML yourself and drop that."
        ) from exc

    stem = Path(filename).stem or "model"
    with tempfile.TemporaryDirectory() as td:
        mmt_path = Path(td) / f"{stem}.mmt"
        mmt_path.write_bytes(data)
        try:
            # Only the [[model]] section is imported. A .mmt also carries a
            # [[protocol]] (a pacing stimulus) and a [[script]], and neither
            # belongs in a CUFLynx model: the protocol here comes from
            # obs_data's protocol_info, so baking Myokit's own stimulus into the
            # CellML would give the model two sources of pacing that disagree.
            # load() rather than load_model() so a malformed protocol/script
            # section still yields a clear error rather than a parse failure
            # attributed to the model.
            model, _protocol, _script = myokit.load(str(mmt_path))
        except Exception as exc:  # noqa: BLE001 - myokit raises several types
            raise MyokitImportError(f"could not read the Myokit model: {exc}") from exc
        if model is None:
            raise MyokitImportError(
                "that .mmt file has no [[model]] section, so there is nothing to convert."
            )

        out_path = Path(td) / f"{stem}.cellml"
        try:
            # No protocol argument: the exported CellML is the model alone.
            CellML2Exporter().model(str(out_path), model)
        except Exception as exc:  # noqa: BLE001 - export failures are varied
            raise MyokitImportError(f"could not export the Myokit model to CellML: {exc}") from exc
        cellml = out_path.read_bytes()

    saved = None
    if out_dir:
        try:
            target = Path(out_dir) / f"{stem}.cellml"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(cellml)
            saved = str(target)
        except OSError:
            # Keeping a copy is a convenience; failing to would be a poor reason
            # to reject a model that converted successfully.
            saved = None
    return cellml, saved
