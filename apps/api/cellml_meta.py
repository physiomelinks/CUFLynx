"""Lightweight, dependency-free CellML metadata extraction.

This module parses CellML 1.0/1.1/2.0 XML *only for metadata* — model name and
the classification of variables into ODE states, parameters (constants with an
``initial_value``) and computed algebraic variables.  It deliberately does
**not** perform any simulation: actual integration is delegated to
``circulatory_autogen`` (see :mod:`engine`).  Keeping this pure-XML means the
upload and variable-listing endpoints — and their unit tests — run without
Myokit or libCellML installed.

Qualified ("Myokit") names use the ``component/variable`` convention that the
rest of the stack (params_for_id, set_param_vals) expects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET


def _strip_ns(tag: str) -> str:
    """Return the local tag name, dropping any ``{namespace}`` prefix."""
    return tag.rsplit("}", 1)[-1]


@dataclass
class CellMLModel:
    """Structured metadata for an uploaded CellML model."""

    name: str
    params: list[str] = field(default_factory=list)
    odes: list[str] = field(default_factory=list)
    algebraic: list[str] = field(default_factory=list)
    all_names: list[str] = field(default_factory=list)
    initial_values: dict[str, float] = field(default_factory=dict)

    @property
    def variable_count(self) -> int:
        return len(self.all_names)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "params": self.params,
            "odes": self.odes,
            "algebraic": self.algebraic,
            "all_names": self.all_names,
            "initial_values": self.initial_values,
            "variable_count": self.variable_count,
        }


class CellMLParseError(ValueError):
    """Raised when the supplied bytes are not a parseable CellML model."""


def _iter_components(root: ET.Element):
    for el in root.iter():
        if _strip_ns(el.tag) == "component":
            yield el


def _classify_math(component: ET.Element):
    """Return (states, computed) sets of variable names for one component.

    ``states`` are variables that appear as the differentiated quantity of a
    ``<diff>`` (i.e. ODE states); ``computed`` are variables assigned by a plain
    ``<eq>`` (algebraic).
    """
    states: set[str] = set()
    computed: set[str] = set()

    for math in component:
        if _strip_ns(math.tag) != "math":
            continue
        for apply_el in list(math):
            if _strip_ns(apply_el.tag) != "apply":
                continue
            children = list(apply_el)
            if not children or _strip_ns(children[0].tag) != "eq":
                continue
            if len(children) < 2:
                continue
            lhs = children[1]
            lhs_tag = _strip_ns(lhs.tag)
            if lhs_tag == "apply":
                inner = list(lhs)
                if inner and _strip_ns(inner[0].tag) == "diff":
                    # state var = the <ci> that is not inside <bvar>
                    for sub in inner[1:]:
                        if _strip_ns(sub.tag) == "ci" and sub.text:
                            states.add(sub.text.strip())
            elif lhs_tag == "ci" and lhs.text:
                computed.add(lhs.text.strip())
    return states, computed


def parse_cellml(data: bytes | str) -> CellMLModel:
    """Parse CellML *bytes* into a :class:`CellMLModel`.

    Raises :class:`CellMLParseError` for anything that is not a CellML document.
    """
    if isinstance(data, bytes):
        text = data.decode("utf-8-sig", errors="replace")
    else:
        text = data

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:  # noqa: PERF203 - clarity over micro-opt
        raise CellMLParseError(f"not valid XML: {exc}") from exc

    if _strip_ns(root.tag) != "model":
        raise CellMLParseError(
            f"root element is <{_strip_ns(root.tag)}>, expected <model>"
        )
    if "cellml.org/cellml" not in (root.tag if "}" in root.tag else "") and not list(
        _iter_components(root)
    ):
        raise CellMLParseError("no CellML <component> elements found")

    model = CellMLModel(name=root.get("name", "unnamed_model"))

    for comp in _iter_components(root):
        comp_name = comp.get("name")
        if not comp_name:
            continue
        states, computed = _classify_math(comp)

        for var in comp:
            if _strip_ns(var.tag) != "variable":
                continue
            var_name = var.get("name")
            if not var_name:
                continue
            qname = f"{comp_name}/{var_name}"
            model.all_names.append(qname)

            init = var.get("initial_value")
            if init is not None:
                try:
                    model.initial_values[qname] = float(init)
                except ValueError:
                    # initial_value referencing another variable (rare); skip number
                    pass

            if var_name in states:
                model.odes.append(qname)
            elif init is not None:
                model.params.append(qname)
            elif var_name in computed:
                model.algebraic.append(qname)
            # else: bound time variable or interface-only var — listed in
            # all_names but uncategorised.

    return model


_CELLML_EXT = re.compile(r"\.(cellml|xml)$", re.IGNORECASE)


def looks_like_cellml_filename(filename: str | None) -> bool:
    return bool(filename and _CELLML_EXT.search(filename))
