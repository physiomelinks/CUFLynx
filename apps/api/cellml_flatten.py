"""Flatten a multi-file / CellML 1.1 model into one self-contained CellML 2.0 doc.

A user can drag a non-flattened model (a main ``.cellml`` that ``<import>``s
sister files, often CellML 1.1) into the app. The standard pipeline needs a
single self-contained CellML 2.0 file, so we resolve the imports and flatten
before saving -- exactly how circulatory_autogen does it in
``src/utilities/libcellml_helper_funcs.py``: parse in non-strict mode (accepts
1.1), ``resolveImports`` against the file's directory (finds the sisters),
``flattenModel``, then ``printModel`` (libCellML's Printer always emits 2.0).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# NB: libcellml is imported lazily inside flatten_cellml() -- it is only present
# in the full simulation environment (and the packaged app), not the unit-test
# tier. Keeping it out of module scope lets main.py import the pure-Python helpers
# (has_imports / pick_main_cellml, regex-based) without libcellml installed.

#: <import ... xlink:href="sister.cellml"> -- the sister files a model references.
_IMPORT_HREF = re.compile(rb'<import\b[^>]*?href\s*=\s*[\'"]([^\'"]+)[\'"]', re.IGNORECASE)


class CellMLFlattenError(Exception):
    """Raised when a multi-file CellML model cannot be resolved/flattened."""


def imported_hrefs(cellml_bytes: bytes) -> set[str]:
    """The (basename) sister files a CellML document imports."""
    return {os.path.basename(h.decode("utf-8", "replace")) for h in _IMPORT_HREF.findall(cellml_bytes)}


def has_imports(cellml_bytes: bytes) -> bool:
    return bool(_IMPORT_HREF.search(cellml_bytes))


def pick_main_cellml(files: dict[str, bytes]) -> str:
    """Choose the top-level model among an uploaded bundle.

    The main file imports its sisters but is itself imported by none, so it is
    the root of the import graph. When several files import (a diamond/chain) the
    root is the one no other file references. Raises if that is ambiguous so the
    caller can report a clear error rather than flatten the wrong file.
    """
    if len(files) == 1:
        return next(iter(files))
    referenced: set[str] = set()
    for data in files.values():
        referenced |= imported_hrefs(data)
    # Roots = uploaded files nobody imports. Prefer ones that themselves import
    # (a lone leaf with no imports isn't a model root).
    roots = [name for name in files if os.path.basename(name) not in referenced]
    importing_roots = [name for name in roots if has_imports(files[name])]
    candidates = importing_roots or roots
    if len(candidates) == 1:
        return candidates[0]
    raise CellMLFlattenError(
        "could not identify the main CellML file among the uploaded set "
        f"(candidates: {sorted(candidates) or sorted(files)}). Upload the main "
        "model together with exactly the sister files it imports."
    )


def flatten_cellml(main_path: str, base_dir: str | None = None) -> str:
    """Resolve imports and flatten ``main_path`` to self-contained CellML 2.0 text.

    ``base_dir`` (default: the main file's directory) is where the imported sister
    files are located. Mirrors circulatory_autogen's parse/resolve/flatten/print.
    """
    from libcellml import Importer, Parser, Printer  # lazy: full env only

    base_dir = base_dir or os.path.dirname(os.path.abspath(main_path))
    parser = Parser(False)  # non-strict: accept CellML 1.1 as well as 2.0
    model = parser.parseModel(Path(main_path).read_text())
    if model is None or parser.errorCount():
        raise CellMLFlattenError(_first_issue(parser, "parse") or "could not parse the CellML model")

    importer = Importer(False)
    importer.resolveImports(model, base_dir)
    if model.hasUnresolvedImports():
        raise CellMLFlattenError(
            _first_issue(importer, "resolve imports")
            or "unresolved imports -- upload every sister file the model imports"
        )

    flat = importer.flattenModel(model)
    if flat is None:
        raise CellMLFlattenError(_first_issue(importer, "flatten") or "flattening the model failed")
    return Printer().printModel(flat)


def _first_issue(logger, what: str) -> str | None:
    """The first error message from a libCellML logger (Parser/Importer), if any."""
    if logger.errorCount():
        return f"{what}: {logger.error(0).description()}"
    return None
