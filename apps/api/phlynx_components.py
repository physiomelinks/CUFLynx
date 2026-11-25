"""The components PhLynx generates when it exports a model, and which of them hold parameters (#422).

PhLynx gives each generated component an id (``PHLYNX_GENERATED_COMPONENTS`` in PhLynx's
``src/utils/constants.js``, PhLynx #1189) because a modeller's own component may have the same name:
PhLynx then suffixes its own (``instance_parameters_1``) and recognises it by the id. CUFLynx does
the same. ``tests/test_phlynx_components.py`` checks this table against a PhLynx checkout.

A model with no PhLynx id (an older PhLynx archive, a model circulatory_autogen generated, an
external Python model) is read by component name, as before.
"""
from __future__ import annotations

#: PhLynx's generated components: ``{key: (name, id)}``, as ``PHLYNX_GENERATED_COMPONENTS`` has them.
GENERATED = {
    "environment": ("phlynx_environment", "phlynx_autogen_environment_0001"),
    "globalParameters": ("global_parameters", "phlynx_autogen_global_parameters_0001"),
    "instanceParameters": ("instance_parameters", "phlynx_autogen_instance_parameters_0001"),
    "summations": ("generated_summations", "phlynx_autogen_summations_0001"),
    "multiplications": ("generated_multiplications", "phlynx_autogen_multiplications_0001"),
    "inspectionModules": ("inspection_modules", "phlynx_autogen_inspection_modules_0001"),
    "affineUnitConversions": ("affine_unit_conversions", "phlynx_autogen_affine_unit_conversions_0001"),
}

#: the ids of the generated components that hold a model's parameters
PARAMETER_IDS = frozenset(GENERATED[key][1] for key in ("globalParameters", "instanceParameters"))

#: the components a model without PhLynx ids keeps its parameters in. ``parameters`` /
#: ``parameters_global`` is circulatory_autogen's (#287); ``instance_parameters`` /
#: ``global_parameters`` is PhLynx's before it gave them ids (#345).
PARAMETER_NAMES = ("parameters", "parameters_global", "instance_parameters", "global_parameters")


def is_generated(component_id: str | None) -> bool:
    return component_id in {cid for _, cid in GENERATED.values()}


def actual_names(component_ids: dict[str, str | None]) -> dict[str, str]:
    """``{PhLynx's name for a generated component: the name it has in this model}``, for the
    generated components found by id: ``{"instance_parameters": "instance_parameters_1"}`` when a
    modeller's own component took the name. Empty for a model without PhLynx ids. A name like
    circulatory_autogen's candidate ``instance_parameters/C`` is read through it, so it means
    PhLynx's component and never the modeller's of that name."""
    by_id = {cid: name for name, cid in component_ids.items() if is_generated(cid)}
    return {name: by_id[cid] for name, cid in GENERATED.values() if cid in by_id}


def parameter_components(component_ids: dict[str, str | None]) -> tuple[str, ...]:
    """The names of the components holding a model's parameters, from ``{component name: id}``.

    A model with a PhLynx id on any component was written by PhLynx with ids: its parameter
    components are the ones whose id says so, whatever they are called, and a modeller's own
    component that happens to be called ``instance_parameters`` is not one. Any other model's are
    those named in :data:`PARAMETER_NAMES`."""
    if any(is_generated(cid) for cid in component_ids.values()):
        return tuple(name for name, cid in component_ids.items() if cid in PARAMETER_IDS)
    return PARAMETER_NAMES
