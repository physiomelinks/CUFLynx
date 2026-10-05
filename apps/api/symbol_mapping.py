"""A study's variable mapping: the LaTeX symbol of every variable, edited in the Variables
panel and used by CA's ``cuflynx-methods-latex``.

The file (``<prefix>_variable_mapping.csv``) and everything about it are CA's
(``libcuflynx.reporting.variable_mapping``): its columns, the default symbol rule, keeping
edits when the model changes. This module only decides where a study's file lives and
reaches CA through ``ca_from``.

Where it lives:

* the supermodule tab of a calibration workflow: CA's ``workflow_mapping_path`` (the target
  instance's directory), which is where ``cuflynx-methods-latex --workflow`` looks for it;
* any other study: ``<outputs>/<prefix>_variable_mapping.csv``, or the uploads directory
  when no outputs directory is set.
"""

from __future__ import annotations

import os

from ca_imports import CaImportError, ca_from

NEEDS_CA = (
    "Variable mappings need a circulatory_autogen (libcuflynx) with libcuflynx.reporting "
    "(physiomelinks/circulatory_autogen#547). Point Settings -> CA dir at one, or upgrade "
    "libcuflynx."
)


class MappingUnavailable(RuntimeError):
    """The configured CA has no reporting module."""


class MappingError(ValueError):
    """A model or mapping CA refused, with CA's own message."""


def _ca(*names):
    try:
        return ca_from("reporting.variable_mapping", *names)
    except CaImportError as exc:
        raise MappingUnavailable(NEEDS_CA) from exc


def default_path(file_prefix: str, outputs_dir: str, fallback_dir: str) -> str:
    directory = outputs_dir.strip() if outputs_dir else ""
    return os.path.join(directory or fallback_dir, f"{file_prefix}_variable_mapping.csv")


def workflow_target_path(workflow_path: str, module_library_dirs: list[str]) -> str:
    try:
        fn = ca_from("reporting.methods", "workflow_mapping_path")
    except CaImportError as exc:
        raise MappingUnavailable(NEEDS_CA) from exc
    try:
        return fn(workflow_path, module_library_dirs)
    except ValueError as exc:
        raise MappingError(str(exc)) from exc


def describe(model_path: str, path: str) -> dict:
    """The model's rows -- with the symbols already in ``path`` kept -- and the problems a
    reader would notice (two variables with one symbol, a variable with none)."""
    variable_mapping, check = _ca("variable_mapping", "check_variable_mapping")
    try:
        rows = variable_mapping(model_path, path)
    except (ValueError, OSError) as exc:
        raise MappingError(str(exc)) from exc
    return {"path": path, "exists": os.path.isfile(path), "rows": rows,
            "problems": check(rows)}


def save(model_path: str, path: str, edits: list[dict]) -> dict:
    """Write ``edits`` ({variable_name, latex}) over the model's rows, then describe it.

    The rows come from the model, so a symbol for a variable the model does not have is
    dropped rather than written, and kind / units / component stay CA's.
    """
    variable_mapping, write = _ca("variable_mapping", "write_variable_mapping")
    try:
        rows = variable_mapping(model_path, path)
    except (ValueError, OSError) as exc:
        raise MappingError(str(exc)) from exc
    latex = {e["variable_name"]: (e.get("latex") or "").strip() for e in edits
             if e.get("variable_name")}
    for row in rows:
        if row["variable_name"] in latex:
            row["latex"] = latex[row["variable_name"]]
    try:
        write(rows, path)
    except (ValueError, OSError) as exc:
        raise MappingError(str(exc)) from exc
    return describe(model_path, path)
