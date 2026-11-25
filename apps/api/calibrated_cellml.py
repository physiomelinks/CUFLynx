"""Write parameter values into a CellML model (issue #114).

:func:`substitute_values` is the one entry point: the calibrated-model save of a
direct calibration run, the send to PhLynx (``omex_export``) and a module
library's calibration (``library_calibration``) all write through it. It uses
libcuflynx's writer, ``param_id.calibrated_model.substitute_parameter_values``,
the one a workflow step's calibrated model is written with, so every calibrated
model is the same file for the same values.

:func:`calibrated_cellml` below is the **fallback** for a configured
circulatory_autogen that predates that writer (probed by
:func:`ca_substitute_parameter_values`), and for the packaged app with no CA
directory set. Its report has the same shape as CA's.

When a calibration finishes, CUFLynx substitutes the best-fit parameter values
into the uploaded flat CellML's ``initial_value`` attributes and saves the result,
so the calibrated model can be reloaded (in CUFLynx or circulatory_autogen) and
reproduces the calibrated simulation. Without this the "load the calibrated model"
workflow has nothing to load, and re-loading the *pre*-calibration model shows the
old values.

The best-fit params are keyed by their params_for_id ``vessel/param`` full names;
each is mapped to the flat model's actual constant variable via the same
resolution used to *read* loaded values (:func:`params_for_id.resolve_model_qname`),
so writing is the exact inverse of reading. Pure-XML string substitution (scoped to
the owning component) keeps the rest of the document byte-for-byte intact — no
libCellML round-trip that could drop namespaces/formatting.
"""

from __future__ import annotations

import re

from cellml_meta import parse_cellml
from params_for_id import _build_gen_index, resolve_model_qname

_VARIABLE_TAG = re.compile(r"<variable\b[^>]*?/?>")
_COMPONENT = re.compile(r'<component\b[^>]*?\bname="(?P<name>[^"]+)".*?</component>', re.S)


def _format_value(val: float) -> str:
    """Shortest string that round-trips back to ``float(val)``."""
    return repr(float(val))


def _set_initial_value(component_block: str, var: str, val: float) -> tuple[str, bool]:
    """Replace ``var``'s ``initial_value`` within one component block. Returns
    (new_block, changed). Robust to attribute order; only the matching variable's
    ``initial_value`` is touched."""
    changed = False

    def repl(match: re.Match) -> str:
        nonlocal changed
        tag = match.group(0)
        name = re.search(r'\bname="([^"]*)"', tag)
        if not name or name.group(1) != var or 'initial_value="' not in tag:
            return tag
        changed = True
        return re.sub(
            r'(\binitial_value=")[^"]*(")',
            lambda m: m.group(1) + _format_value(val) + m.group(2),
            tag,
            count=1,
        )

    return _VARIABLE_TAG.sub(repl, component_block), changed


def ca_substitute_parameter_values():
    """libcuflynx's ``substitute_parameter_values``, or None when the configured
    circulatory_autogen does not have it (or none is reachable)."""
    try:
        from ca_imports import ca_from  # local: runners/ sibling, a leaf

        return ca_from("param_id.calibrated_model", "substitute_parameter_values")
    except ImportError:
        return None


def substitute_values(cellml_text: str, values: dict[str, float]) -> tuple[str, dict]:
    """``(new_text, report)``: ``values`` (``vessel/param`` -> value) written into
    the model's ``initial_value`` attributes, nothing else changed.

    ``report`` is ``{"updated": [...], "unresolved": [...], "resolved": {name:
    "component/variable"}}``. Written by CA's writer when the configured CA has
    it, else by :func:`calibrated_cellml`.
    """
    writer = ca_substitute_parameter_values()
    if writer is None:
        return calibrated_cellml(cellml_text, values)
    return writer(cellml_text, values)


def calibrated_cellml(cellml_text: str, best_params: dict[str, float]) -> tuple[str, dict]:
    """Return ``(new_cellml_text, report)`` with each best-fit value substituted.

    The fallback writer, for a CA without ``substitute_parameter_values``; callers
    go through :func:`substitute_values`.

    ``best_params`` maps ``"vessel/param"`` -> value. ``report`` is
    ``{"updated": [...], "unresolved": [...], "resolved": {...}}`` — full names
    that were written vs that couldn't be mapped to a model constant (never
    silently dropped), plus where each written name landed as ``component/var``.

    ``resolved`` exists so a caller can say *which component* a value went into
    without redoing the resolution: sending a study to PhLynx has to warn when a
    parameter is written outside ``parameters`` / ``parameters_global``, because
    per #287 that is a change PhLynx will not pick up.
    """
    meta = parse_cellml(cellml_text)
    gen_index = _build_gen_index(meta.initial_values)

    # Group the resolved targets by owning component so we substitute inside the
    # right block (bare variable names can repeat across components).
    by_component: dict[str, dict[str, float]] = {}
    updated: list[str] = []
    unresolved: list[str] = []
    resolved: dict[str, str] = {}
    for full_name, value in best_params.items():
        vessel, _, param = full_name.partition("/")
        key = resolve_model_qname(vessel, param, meta.initial_values, gen_index)
        if key is None:
            unresolved.append(full_name)
            continue
        comp, _, var = key.partition("/")
        by_component.setdefault(comp, {})[var] = value
        updated.append(full_name)
        resolved[full_name] = key

    report = {"updated": updated, "unresolved": unresolved, "resolved": resolved}
    if not by_component:
        return cellml_text, report

    def repl_component(match: re.Match) -> str:
        block = match.group(0)
        wanted = by_component.get(match.group("name"))
        if not wanted:
            return block
        for var, val in wanted.items():
            block, _ = _set_initial_value(block, var, val)
        return block

    new_text = _COMPONENT.sub(repl_component, cellml_text)
    return new_text, report
