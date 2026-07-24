"""Write a *calibrated* CellML model (issue #114).

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


def calibrated_cellml(cellml_text: str, best_params: dict[str, float]) -> tuple[str, dict]:
    """Return ``(new_cellml_text, report)`` with each best-fit value substituted.

    ``best_params`` maps ``"vessel/param"`` -> value. ``report`` is
    ``{"updated": [...], "unresolved": [...]}`` — full names that were written vs
    that couldn't be mapped to a model constant (never silently dropped).
    """
    meta = parse_cellml(cellml_text)
    gen_index = _build_gen_index(meta.initial_values)

    # Group the resolved targets by owning component so we substitute inside the
    # right block (bare variable names can repeat across components).
    by_component: dict[str, dict[str, float]] = {}
    updated: list[str] = []
    unresolved: list[str] = []
    for full_name, value in best_params.items():
        vessel, _, param = full_name.partition("/")
        key = resolve_model_qname(vessel, param, meta.initial_values, gen_index)
        if key is None:
            unresolved.append(full_name)
            continue
        comp, _, var = key.partition("/")
        by_component.setdefault(comp, {})[var] = value
        updated.append(full_name)

    if not by_component:
        return cellml_text, {"updated": updated, "unresolved": unresolved}

    def repl_component(match: re.Match) -> str:
        block = match.group(0)
        wanted = by_component.get(match.group("name"))
        if not wanted:
            return block
        for var, val in wanted.items():
            block, _ = _set_initial_value(block, var, val)
        return block

    new_text = _COMPONENT.sub(repl_component, cellml_text)
    return new_text, {"updated": updated, "unresolved": unresolved}
