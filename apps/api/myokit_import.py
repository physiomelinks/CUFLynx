"""Accept a Myokit model by exporting it to CellML first (issue #27).

Everything downstream of the upload assumes CellML: the metadata parser, the
params_for_id naming (``component/variable``), the exported pipeline, and CA
itself. Rather than teach each of those about ``.mmt``, a dropped Myokit model is
converted once on the way in and the rest of the app never knows the difference.

**The conversion itself lives in circulatory_autogen**, in
``libcuflynx.parsers.MyokitParsers``. It is engine work, not GUI work: CA's own
pipeline benefits from reading a ``.mmt``, and the ``protocol_info`` half of the
same file has to speak CA's ``protocol_shapes`` vocabulary, which is impossible
to keep honest from another repository. This module is what is left over once
that moved out -- the parts that are genuinely CUFLynx's:

- the filename/content predicates the upload sniff needs *before* it knows
  whether CA is reachable, so they stay local and depend on nothing;
- :class:`MyokitImportError`, kept as a CUFLynx class with a stable identity.
  The CA directory can be re-pointed at runtime, so a class imported from CA
  would change identity mid-session and ``except MyokitImportError`` at the call
  sites would quietly stop matching;
- persisting the converted file next to the study, which is a CUFLynx artefact
  convention rather than a conversion.

There is no local re-implementation of the conversion. The same choice CA's
params_for_id CSV converter made (see ``params_for_id``): one implementation, and
a named error when the engine is too old to have it, rather than two copies that
drift and disagree about what a model means.
"""

from __future__ import annotations

from pathlib import Path

# Myokit's own extension. `.txt` is deliberately not accepted: it would make any
# stray text file look like a model.
MYOKIT_SUFFIXES = (".mmt",)

def no_parser_hint(what: str, module: str) -> str:
    """What to say when the engine cannot supply a conversion.

    One sentence, written once, because the ``.mmt`` and ``.model`` readers both
    need it and a user who hits either wants the same two remedies.
    """
    return (
        f"the circulatory_autogen this CUFLynx is using does not provide the "
        f"{what} reader ({module}). Update circulatory_autogen, or export the "
        f"model to CellML yourself and drop that instead."
    )


#: The Myokit reader's own phrasing of :func:`no_parser_hint`.
NO_PARSER_HINT = no_parser_hint("Myokit", "libcuflynx.parsers.MyokitParsers")


class MyokitImportError(ValueError):
    """A Myokit model that could not be read or exported (surface as HTTP 422)."""


def is_myokit_filename(name: str) -> bool:
    return Path(str(name or "")).suffix.lower() in MYOKIT_SUFFIXES


def looks_like_myokit(data: bytes) -> bool:
    """Whether ``data`` is an ``.mmt`` file, judged by its own section headers.

    Content rather than extension, so a model dropped with the wrong name is
    still recognised -- and, more importantly, so an XML file named ``.mmt`` is
    not fed to the Myokit parser.

    Local on purpose: the upload route asks this of every file it receives, and
    it must answer the same way whether or not a CA directory is configured.
    """
    try:
        head = data[:4096].decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 - undecodable is not a Myokit model
        return False
    if head.lstrip().startswith("<"):
        return False  # XML: CellML, SBML, or an OMEX manifest
    # An .mmt is a sectioned file; [[model]] is the one every model has.
    return "[[model]]" in head


def _ca_parser(module: str = "parsers.MyokitParsers"):
    """A CA parser module, or None when CA cannot be reached or is too old.

    Same shape as ``obs_data._ca_parser``: lazy, through the one resolver, and
    None rather than an exception so callers can phrase their own message. The
    module is a parameter because ``easyml_import`` needs exactly this and
    a second copy of it would be a second place to fix.
    """
    try:
        from ca_imports import ca_import, ensure_ca_path  # noqa: PLC0415

        ensure_ca_path()
        mod = ca_import(module)
    except Exception:  # noqa: BLE001 - CA absent or too old; nothing to ask
        return None
    return mod


def cellml_from_myokit(data: bytes, *, filename: str, out_dir: str | None = None) -> tuple[bytes, str | None]:
    """Convert a Myokit ``.mmt`` to CellML 2.0.

    Returns ``(cellml_bytes, saved_path_or_None)``. ``saved_path`` is where the
    converted file was kept for the user; None when no output directory was
    given, in which case the conversion is still returned but not persisted.
    """
    parser = _ca_parser()
    if parser is None:
        raise MyokitImportError(f"could not convert that .mmt: {NO_PARSER_HINT}")
    try:
        return parser.cellml_from_myokit(data, filename=filename, out_dir=out_dir)
    except ValueError as exc:
        # CA's MyokitImportError is a ValueError, and so is anything Myokit
        # raises about a malformed file. Both are the user's file rather than a
        # fault, so both become the 422 the call sites already expect.
        raise MyokitImportError(str(exc)) from exc
