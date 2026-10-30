"""Accept an openCARP EasyML ``.model`` file by converting it to CellML on the way in.

The same rail a ``.mmt`` rides (see :mod:`myokit_import`): the conversion happens
once at the door, and the metadata parser, the ``component/variable`` naming,
the exported pipeline and CA itself keep seeing the CellML they already expect.

**The reader lives in circulatory_autogen**, in
``libcuflynx.parsers.EasyMLParsers``. Everything it has to work out is engine
knowledge -- which states are gating variables whose equations EasyML leaves
implicit, what the missing membrane equation should be, what a ``.method()``
group means -- and the default protocol it produces is CA's ``protocol_shapes``
vocabulary. What is left here is the CUFLynx-facing surface:

- the filename and content predicates the upload sniff needs *before* it knows
  whether CA is reachable;
- :class:`EasyMLImportError`, a CUFLynx class with a stable identity (the CA
  directory can be re-pointed at runtime, so an imported class would change
  identity mid-session and ``except`` at the call sites would stop matching);
- nothing else. There is deliberately no local re-implementation: a second
  reader of a language this implicit would not agree with the first for long.

An import that succeeds still usually has something to say -- a synthesised
membrane equation, a gate started at its steady state, an integration method
openCARP would have used and this will not. Those come back as ``warnings`` and
are meant to reach the user, not the log.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from myokit_import import _ca_parser, no_parser_hint

#: openCARP's extension for an EasyML model.
EASYML_SUFFIXES = (".model",)

_MARKUP = (".method(", ".param(", ".trace(", ".external(", ".nodal(",
           ".regional(", "diff_")
_INIT = re.compile(r"\b\w+_init\s*=")


class EasyMLImportError(ValueError):
    """An EasyML file that could not be read (surface as HTTP 422)."""


def is_easyml_filename(name: str) -> bool:
    return Path(str(name or "")).suffix.lower() in EASYML_SUFFIXES


def looks_like_easyml(data: bytes) -> bool:
    """Whether ``data`` is an EasyML model, judged by its own markup.

    Content rather than extension, because ``.model`` is a generic suffix that
    other tools use for unrelated files -- the cardiac-geometry files next door
    among them -- so recognising one by name alone would hand a mesh to a model
    parser.
    """
    try:
        head = data[:8192].decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001 - undecodable is not a model file
        return False
    if head.lstrip().startswith("<"):
        return False  # XML: CellML, SBML, or an OMEX manifest
    if ";" not in head:
        return False
    return any(m in head for m in _MARKUP) or _INIT.search(head) is not None


def wants_easyml(name: str, data: bytes) -> bool:
    """Whether the upload route should hand this file to the EasyML reader.

    The content sniff is the real test. The extension only gets a vote for a
    file the sniff cannot place, and never for XML: a CellML document named
    ``.model`` is a CellML document, and routing it here would replace a clear
    CellML error with an EasyML one about a language it was never written in.
    """
    if looks_like_easyml(data):
        return True
    return is_easyml_filename(name) and not data.lstrip().startswith(b"<")


def import_easyml(data: bytes, *, filename: str, out_dir: str | None = None) -> dict[str, Any]:
    """Read a ``.model`` file: CellML, warnings, parameters, a default protocol.

    One call rather than three, because a published ionic model is a large file
    and parsing it is the expensive part.
    """
    parser = _ca_parser("parsers.EasyMLParsers")
    if parser is None:
        raise EasyMLImportError(
            "could not read that .model file: "
            + no_parser_hint("EasyML", "libcuflynx.parsers.EasyMLParsers")
        )
    try:
        return parser.import_easyml(data, filename=filename, out_dir=out_dir)
    except ValueError as exc:
        # CA's EasyMLImportError is a ValueError, and so is anything Myokit
        # raises about a model it cannot export. Both are the user's file rather
        # than a fault, so both become the 422 the call sites expect.
        raise EasyMLImportError(str(exc)) from exc
