"""Metadata for an uploaded ``external_python`` model — **by AST, never by import**.

An ``external_python`` model is a plain ``.py`` the user wrote: a solver class
wrapped by circulatory_autogen's ``ExternalSimulationHelper`` (``model_type
external_python`` / ``solver external``). The class declares what CUFLynx needs
in order to draw sliders and pick outputs *before* anything has been run:

.. code-block:: python

    class MyModel:
        parameters = {"heat/k": 1.0, "heat/u_D": 0.0}   # name -> default
        output_names = ["heat/T_p1", "heat/T_p2"]
        # init_solver / update_times / set_param_vals / run / get_results ...

    SIM_HELPER = MyModel     # required module-level registration

**This module never executes the user's file.** It parses it with :mod:`ast` and
``literal_eval``\\ s the two class attributes, so uploading a model cannot run code
in the API process. The file *is* executed later — when a simulation builds the
helper — but that happens in the engine or, when an interpreter is chosen in
Settings, in the worker process, which is the same trust level (and the same
place) as circulatory_autogen's ``python_user_defined`` funcs. Upload is a
different moment from run: a user browsing a model they were sent has not asked
for its ``import`` side effects, and the variables endpoint must answer without
them.

The result is a :class:`cellml_meta.CellMLModel`, because everything downstream —
``GET /api/models/{id}/variables``, the sliders, ``params_for_id`` bounds, the
calibrated-model writer — projects that one dataclass. ``parameters`` become
``params`` (with ``initial_values``), ``output_names`` become ``algebraic``;
there are no ``odes`` (the class owns its own integration) and no ``units`` (the
contract declares none).
"""

from __future__ import annotations

import ast
import re

from cellml_meta import CellMLModel

#: The module-level name the user's file must bind to their solver class. CA's
#: ExternalSimulationHelper looks for the same name, so a file that loads there
#: parses here and vice versa.
REGISTRATION_NAME = "SIM_HELPER"

#: The two class attributes that must be literals. They are read, not called, so
#: the contract is that they are *literal* class attributes: anything computed
#: could only be known by running the file.
PARAMETERS_ATTR = "parameters"
OUTPUTS_ATTR = "output_names"


class PyModelParseError(ValueError):
    """Raised when a ``.py`` is not a usable external_python model.

    The message is meant to be shown to the user verbatim (the upload route
    returns it as the 422 detail), so it says what to change in the file.
    """


_PY_EXT = re.compile(r"\.py$", re.IGNORECASE)


def looks_like_py_filename(filename: str | None) -> bool:
    """Whether ``filename`` names a Python module.

    Extension only, deliberately: a solver class has no sniffable signature, and
    guessing from content would mean deciding that some CellML/mmt uploads are
    Python. The user names the file.
    """
    return bool(filename and _PY_EXT.search(filename))


def _module_registration(tree: ast.Module) -> str:
    """The class name bound to ``SIM_HELPER`` at module level."""
    for node in tree.body:
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == REGISTRATION_NAME for t in targets):
            continue
        value = node.value
        if isinstance(value, ast.Name):
            return value.id
        raise PyModelParseError(
            f"{REGISTRATION_NAME} must be the name of a class defined in this file "
            f"(e.g. '{REGISTRATION_NAME} = MyModel'), not an expression."
        )
    raise PyModelParseError(
        f"no module-level '{REGISTRATION_NAME} = <YourClass>' found. Add it at the "
        "bottom of the file so CUFLynx and circulatory_autogen know which class is "
        "the solver."
    )


def _class_def(tree: ast.Module, name: str) -> ast.ClassDef:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise PyModelParseError(
        f"{REGISTRATION_NAME} names '{name}', but no class '{name}' is defined at the "
        "top level of this file."
    )


def _literal_attr(cls: ast.ClassDef, attr: str):
    """``literal_eval`` of the class attribute ``attr``, or raise."""
    for node in cls.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        if not any(isinstance(t, ast.Name) and t.id == attr for t in targets):
            continue
        if node.value is None:
            break
        try:
            return ast.literal_eval(node.value)
        except (ValueError, SyntaxError, TypeError) as exc:
            raise PyModelParseError(
                f"class {cls.name}: '{attr}' must be a literal that can be read "
                "without running the file (a plain dict/list of numbers and "
                f"strings); it is computed instead ({exc})."
            ) from exc
    raise PyModelParseError(
        f"class {cls.name} has no '{attr}' class attribute. Declare it literally, "
        f"e.g. {attr} = " + (
            '{"component/variable": 1.0}' if attr == PARAMETERS_ATTR
            else '["component/variable"]'
        ) + "."
    )


def _check_qname(name, *, where: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise PyModelParseError(f"{where}: names must be non-empty strings; got {name!r}.")
    if "/" not in name:
        raise PyModelParseError(
            f"{where}: '{name}' must be a 'component/variable' name — every name in "
            "CUFLynx and circulatory_autogen carries the component it belongs to "
            "(e.g. 'heat/k')."
        )
    return name


def _check_number(value, name: str) -> float:
    # bool is an int in Python; a True default would silently become 1.0 and the
    # slider would offer a continuum the model never meant.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PyModelParseError(
            f"{PARAMETERS_ATTR}['{name}'] must be a number (its default value); "
            f"got {value!r}."
        )
    return float(value)


def parse_py_model(data: bytes | str, *, name: str | None = None) -> CellMLModel:
    """Parse an external_python model file into a :class:`CellMLModel`.

    ``name`` overrides the model name, which otherwise is the solver class's own
    name. The class name is preferred over the uploaded filename because the
    upload is stored as ``<model_id>.py`` and re-parsed from there on recovery —
    a filename-derived name would become a uuid after a server reload.

    Raises :class:`PyModelParseError`, whose message is user-facing.
    """
    if isinstance(data, bytes):
        text = data.decode("utf-8-sig", errors="replace")
    else:
        text = data

    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        raise PyModelParseError(
            f"not valid Python: {exc.msg} (line {exc.lineno})"
        ) from exc

    cls = _class_def(tree, _module_registration(tree))

    raw_params = _literal_attr(cls, PARAMETERS_ATTR)
    if not isinstance(raw_params, dict):
        raise PyModelParseError(
            f"class {cls.name}: '{PARAMETERS_ATTR}' must be a dict of "
            f"'component/variable' -> default value; got {type(raw_params).__name__}."
        )
    raw_outputs = _literal_attr(cls, OUTPUTS_ATTR)
    if not isinstance(raw_outputs, (list, tuple)):
        raise PyModelParseError(
            f"class {cls.name}: '{OUTPUTS_ATTR}' must be a list of "
            f"'component/variable' names; got {type(raw_outputs).__name__}."
        )

    params: list[str] = []
    initial_values: dict[str, float] = {}
    for key, value in raw_params.items():
        qname = _check_qname(key, where=f"class {cls.name}: {PARAMETERS_ATTR}")
        params.append(qname)
        initial_values[qname] = _check_number(value, qname)

    outputs: list[str] = []
    for entry in raw_outputs:
        qname = _check_qname(entry, where=f"class {cls.name}: {OUTPUTS_ATTR}")
        if qname not in outputs:
            outputs.append(qname)

    # Union, params first, without disturbing either order: all_names is what the
    # variable pickers list, and a stable order keeps the UI stable.
    all_names = list(params) + [n for n in outputs if n not in initial_values]

    return CellMLModel(
        name=name or cls.name,
        params=params,
        odes=[],
        algebraic=outputs,
        all_names=all_names,
        initial_values=initial_values,
        units={},
    )
