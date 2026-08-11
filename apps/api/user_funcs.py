"""User-authored *operation*, *cost* and *modifier* funcs for circulatory_autogen.

Issue #58 (+ #104 rework): let a user write their own funcs from the GUI, without
opening circulatory_autogen. Three kinds, distinguished by what they act on:

- **operation** — reduces a data_item's operand series to the scalar a cost func
  compares. Declared in obs_data.
- **cost** — compares a model output to its target and returns a scalar cost.
  Declared in obs_data.
- **modifier** — maps one calibrated theta to each model parameter it governs
  (``p_i = fn(theta, baseline_i, **inputs)``). Declared in params_for_id (CA #383).

An operation acts on an *output*, a modifier acts on a *parameter* — CA's own
vocabulary, worth keeping straight because the two were once the same word.

**External-file design (no bridge into CA's tree).** Each kind is stored in a
single file under the user's chosen **output directory** (``config_outputs_dir``,
the same dir the analysis runs write to), so the funcs travel with the outputs.
When no output directory is set it falls back to the user config dir::

    <output_dir>/user_funcs/operation_funcs_user.py
    <output_dir>/user_funcs/cost_funcs_user.py
    <output_dir>/user_funcs/modifier_funcs_user.py

CUFLynx passes the file path to circulatory_autogen through CA's config keys
``operation_funcs_external_path`` / ``cost_funcs_external_path`` (CA #303) and
``modifier_funcs_external_path`` (CA #383): the analysis runners include them in
the run config (forwarded to ``CVS0DParamID`` / ``SensitivityAnalysis``),
``obs_options`` hands the first two to CA's ``get_operation_funcs_dict_for_mode``
/ ``get_cost_funcs_dict_for_mode`` / ``cost_func_metadata`` builders, and
``solver_options`` hands the third to CA's ``param_modifiers()`` — so every
editor dropdown shows the merged set. :func:`external_path` is the single source
of a kind's path, and the export bundle copies each file it finds beside the
study (so a params_for_id naming a user modifier stays reproducible).

CA loads the files and registers their top-level funcs alongside its built-ins,
keeping only funcs whose ``__module__`` is the file itself — so the decorators we
*import* (``differentiable`` / ``series_to_constant`` / ``is_MLE`` /
``cost_combiner``) are auto-excluded. We never define fallbacks for them (that
would get them registered as funcs).

Security: this writes and later executes arbitrary user Python inside CA at run
time. That is inherent to the feature and consistent with CUFLynx's localhost,
single-user assumption (see CLAUDE.md "Security caveats"). We validate that the
name is a safe identifier and that the code parses, but we do not sandbox.
"""

from __future__ import annotations

import ast
import keyword
from dataclasses import dataclass
from pathlib import Path

from engine import _circulatory_autogen_src
from settings_store import config_dir


class UserFuncError(ValueError):
    """Raised for an invalid func name or code (surface as HTTP 422)."""


# Back-compat alias: the original module exported ``UserOperationError``.
UserOperationError = UserFuncError


# ---------------------------------------------------------------------------
# Kind configuration
# ---------------------------------------------------------------------------
_OPERATION_HEADER = '''"""User-defined observable operations authored via CUFLynx (issues #58 / #104).

Each top-level function here is registered as a selectable "operation" in the
obs_data editor and used by circulatory_autogen during calibration / sensitivity
/ UQ (loaded from CA's operation_funcs_external_path config input; CA #303).

An operation receives the operand array(s) for a data_item and returns a scalar.
Return the operand series when ``series_output=True`` so the reduction can be
plotted on top of the series — that returned series is what is drawn with the
feature in the plots. ``np`` (numpy) is available for plain funcs; write the body
against the math backend ``mb`` (and add ``@differentiable``) to use the operation
with AD or FSA gradients. The ``@differentiable`` / ``@series_to_constant`` markers
mirror CA's (imported, never redefined).

Managed by CUFLynx's "Custom funcs" dialog; the header may be regenerated.
"""
import numpy as np  # noqa: F401 -- available to user operations

# Imported (not defined) so CA registers only the user funcs below, never these.
from param_id.differentiable import differentiable  # noqa: F401
from param_id.operation_funcs import series_to_constant  # noqa: F401
from param_id.math_backend import make_math_backend

# Math backend for differentiable ops: use ``mb.<op>`` instead of numpy so CA can
# rebind it to casadi and take symbolic gradients. CA sets this per backend.
mb = make_math_backend("numpy")
'''

_COST_HEADER = '''"""User-defined cost functions authored via CUFLynx (issues #58 / #104).

Each top-level function here is registered as a selectable "cost_type" in the
obs_data editor and used by circulatory_autogen during calibration / sensitivity
/ UQ (loaded from CA's cost_funcs_external_path config input; CA #303).

A cost func compares a model ``output`` to its target and returns a scalar cost
(lower = better fit). It must work for both scalars and arrays. ``np`` (numpy) is
available for plain funcs; write the body against the math backend ``mb`` (and add
``@differentiable``) for AD gradients. The ``@differentiable`` / ``@is_MLE`` /
``@cost_combiner`` markers mirror CA's (imported, never redefined).

The signature is yours to choose (CA #370). CA fills the model output and the
ground truth positionally, then supplies ``std`` and ``weight`` *only* when the
signature declares them -- so a cost with no notion of a standard deviation just
leaves it out. Any further keyword argument (give it a default) is filled per
data_item from that item's ``cost_kwargs`` in obs_data.json, and the obs_data
editor offers an input for each one; see the "kwargs" template.

Managed by CUFLynx's "Custom funcs" dialog; the header may be regenerated.
"""
import numpy as np  # noqa: F401 -- available to user cost funcs

# Imported (not defined) so CA registers only the user funcs below, never these.
from param_id.differentiable import differentiable  # noqa: F401
from cost_funcs_user import is_MLE, cost_combiner  # noqa: F401
from param_id.math_backend import make_math_backend

# Math backend for differentiable costs: use ``mb.<op>`` instead of numpy so CA
# can rebind it to casadi and take symbolic gradients. CA sets this per backend.
mb = make_math_backend("numpy")
'''

# Operation editor templates. The dialog offers each as a tab; the backend is the
# single source of truth for their text.
_OPERATION_TEMPLATES = {
    "basic": '''def my_operation(x, series_output=False):
    """Reduce the operand series ``x`` to a scalar (what a cost func compares).

    When ``series_output=True`` return the *series* to draw on top of the data;
    that same series is what gets plotted with the feature in the plots. For AD or
    FSA gradients, use the Differentiable template instead.
    """
    if series_output:
        return x
    return float(np.max(x) - np.min(x))
''',
    "multi_operand": '''def my_operation(x, y, series_output=False):
    """Combine two operand series into a scalar.

    List the operands (in this order) as the data_item's ``operands`` — ``x`` and
    ``y`` arrive as arrays. Return a series when ``series_output=True`` to plot the
    feature on top of the data.
    """
    if series_output:
        return x - y
    return float(np.trapz(x - y))
''',
    "kwargs": '''def my_operation(x, threshold=0.0, n_peaks=1, series_output=False):
    """Operation with tunable keyword arguments.

    Every keyword argument (here ``threshold`` and ``n_peaks``) is parsed from
    this signature and becomes an editable input on each data_item that selects
    this operation in the obs_data editor (CUFLynx #112) — enter a value next to
    the operation and it is passed in per data_item. Give every kwarg a default.
    ``series_output=True`` returns the series that is plotted with the feature.
    """
    above = x[x > threshold]
    if series_output:
        return x
    return float(np.mean(above[:n_peaks]) if len(above) else 0.0)
''',
    "differentiable": '''@differentiable
def my_operation(x, series_output=False):
    """Differentiable operation — the ``@differentiable`` marker is required to use
    it with AD or FSA gradients.

    Built on the math backend ``mb`` (not numpy) so CA can rebind ``mb`` to casadi
    and take symbolic gradients. ``mb`` provides ``max``/``min``/``mean``/
    ``max_minus_min``/``power``/``abs``/``sum``/``exp``/``log``. Drop the decorator
    (and you may use numpy) if finite-difference gradients are enough.
    ``series_output=True`` returns the series plotted with the feature.
    """
    if series_output:
        return x
    return mb.max_minus_min(x)
''',
}

_COST_TEMPLATES = {
    "basic": '''def my_cost(output, desired_mean, std, weight):
    """Scalar cost between model ``output`` and the target ``desired_mean``.

    Must work for scalars and arrays; lower = better fit. Select it as a
    data_item's ``cost_type`` in the obs_data editor. For AD gradients, use the
    Differentiable template instead.

    ``std`` and ``weight`` come from the data_item and are passed only because
    they are named here — drop either if this cost has no use for it, and add
    keyword arguments of your own with the Kwargs template.
    """
    return float(np.sum(((output - desired_mean) / std) ** 2 * weight))
''',
    "kwargs": '''def my_cost(output, desired_mean, weight, tolerance=0.0, exponent=2.0):
    """Cost with tunable keyword arguments, set per data_item.

    Every keyword argument circulatory_autogen does not supply itself (here
    ``tolerance`` and ``exponent``) is parsed from this signature and becomes an
    editable input on each data_item that selects this cost in the obs_data editor
    — the values are written to that data_item's ``cost_kwargs`` and passed in per
    data_item. Give every one a default, so an item that sets none still scores.

    ``std`` and ``weight`` are the arguments CA fills from the data_item's own
    fields, so they are reserved: name the ones this cost needs (here ``weight``,
    and deliberately no ``std``) and leave out the rest — CA passes only what the
    signature has room for. Setting either through ``cost_kwargs`` is an error,
    because it would shadow the real value and quietly change what is calibrated.
    """
    error = np.abs(output - desired_mean)
    return float(np.sum(np.maximum(error - tolerance, 0.0) ** exponent * weight))
''',
    "robust": '''@differentiable
def robust_loss(output, desired_mean, std, weight, alpha=2.0, c=1.0):
    """Barron's general and adaptive robust loss (CVPR 2019), tuned per data_item.

    One family that contains several familiar losses, chosen by ``alpha``:

    ====== ==========================================================
    alpha  loss
    ====== ==========================================================
    2      squared error — identical to ``MSE`` (see the note below)
    1      pseudo-Huber (Charbonnier)
    0      Cauchy / Lorentzian
    -2     Geman-McClure
    -inf   Welsch / Leclerc
    ====== ==========================================================

    Lower ``alpha`` gives outliers progressively less influence, which is the
    point: a single bad experimental point cannot then drag the whole fit. ``c``
    sets the residual scale at which that down-weighting starts, in units of
    ``std`` (residuals much smaller than ``c`` are still scored quadratically).

    Note that ``c`` divides the residual, so it also **rescales the cost by
    1/c**2**. Two data_items with different ``c`` are therefore on different
    scales — the same trap as unnormalised weights. Prefer leaving ``c`` at 1 and
    letting each item's ``std`` carry its scale, unless you mean to reweight.

    **The half is Barron's and is kept.** At ``alpha=2, c=1`` this is exactly
    ``0.5 * mean(((output - desired_mean)/std)**2 * weight)`` — the L2 /
    least-squares objective, which is precisely **half of CA's ``MSE``** and
    bit-identical to CA's ``gaussian_MLE`` (``MSE`` is defined as
    ``2*gaussian_MLE``). So the default lands on a cost CA already has, rather
    than on a rescaled variant of one.

    A constant factor cannot move the optimum, so this does not change what a
    single-observable fit converges to. It *does* change the cost's size relative
    to other data_items: an item scored with this at ``alpha=2`` contributes half
    what the same item scored with ``MSE`` would. If you mix the two in one
    obs_data, either use ``gaussian_MLE`` for the others or raise this item's
    ``weight`` to compensate.

    Branching on ``alpha`` in Python is safe under AD: it is a keyword argument
    fixed per data_item, not part of the data, so the branch is taken once when
    the graph is built. The special cases are not optional — the general form
    divides by ``alpha`` and by ``|alpha - 2|``.
    """
    x = (output - desired_mean) / std
    sq = mb.power(x / c, 2)
    if alpha == 2.0:
        rho = 0.5 * sq
    elif alpha == 0.0:
        rho = mb.log(0.5 * sq + 1.0)
    elif alpha == float("-inf"):
        rho = 1.0 - mb.exp(-0.5 * sq)
    else:
        d = abs(alpha - 2.0)
        rho = (d / alpha) * (mb.power(sq / d + 1.0, 0.5 * alpha) - 1.0)
    per = rho * weight
    return mb.sum(per) / mb.numel(per)
''',
    "differentiable": '''@differentiable
def my_cost(output, desired_mean, std, weight):
    """Differentiable cost — the ``@differentiable`` marker is required to use AD
    gradients.

    Built on the math backend ``mb`` (not numpy) so CA can rebind ``mb`` to casadi
    and take symbolic gradients. Must work for scalars and arrays; lower = better
    fit.
    """
    return mb.sum(mb.power((output - desired_mean) / std, 2) * weight)
''',
    "MLE": '''@differentiable
@is_MLE
def my_mle_cost(output, desired_mean, std, weight):
    """Negative-log-likelihood cost (required by the Bayesian method).

    ``@is_MLE`` marks the value as a negative log likelihood; ``@differentiable``
    (built on the ``mb`` math backend) keeps it usable for AD gradients.
    """
    per = mb.power((output - desired_mean) / std, 2) * weight
    return 0.5 * mb.mean(per)
''',
}


_MODIFIER_HEADER = '''"""User-defined parameter modifiers authored via CUFLynx (issue #58, CA #383).

Each top-level function here is registered as a selectable "modifier" in the
params_for_id editor and used by circulatory_autogen during calibration /
sensitivity / UQ (loaded from CA's modifier_funcs_external_path config input).

A modifier says how one calibrated variable (``theta``) computes each of the model
parameters it governs::

    p_i = fn(theta, baseline_i, **inputs)

``baseline_i`` is that target's model-default value. ``inputs`` are extra model
constants the params_for_id entry names by qname, resolved to their defaults once
at setup — so nothing compounds across calibration iterations. Declare them with
``@modifier_func(inputs={...})``: ``"float"`` for one qname, ``"list"`` for
several. That declaration is what the params editor reads to render a field per
input, so an undecorated function is ignored rather than half-registered.

**Every modifier must be affine in theta** (``a*theta + b`` for fixed inputs).
That is not a style rule: the analytic gradients apply a constant chain-rule
weight ``a = dp_i/dtheta``, and theta's starting value is derived by inverting the
mapping at the baseline. CA probes affinity numerically at setup and refuses a
function that fails, before it can produce a silently wrong gradient.

Managed by CUFLynx's "Custom funcs" dialog; the header may be regenerated.
"""
import numpy as np  # noqa: F401 -- available to user modifiers

# Imported (not defined) so CA registers only the decorated funcs below, never this.
from param_id.modifier_funcs import modifier_func  # noqa: F401
'''

_MODIFIER_TEMPLATES = {
    "basic": '''@modifier_func(
    inputs={},
    description="one calibrated offset added to every target's default value")
def my_modifier(theta, baseline):
    """Map the calibrated ``theta`` to one target's value.

    ``baseline`` is that target's default value in the model, so this entry shifts
    every target it governs by the same calibrated amount. Must be affine in
    ``theta`` — ``a*theta + b`` for fixed inputs — which CA verifies at setup.
    """
    return baseline + theta
''',
    "list_input": '''@modifier_func(
    inputs={"subtract": "list"},
    description="target = theta - sum(subtract): calibrate a total, derive the remainder")
def my_remainder(theta, baseline, subtract):
    """Derive a target as whatever is left of a calibrated total.

    ``inputs`` declares each extra argument: ``"list"`` means the params_for_id
    entry names several model constants by qname (e.g. ``heart/q_rv_init``) and
    this receives their default values as a list of floats. Calibrating a total
    volume and deriving one compartment from it is the motivating case (CA #383).
    """
    return theta - sum(subtract)
''',
    "float_input": '''@modifier_func(
    inputs={"reference": "float"},
    description="scale a target against another model constant")
def my_relative_scale(theta, baseline, reference):
    """Scale by ``theta`` relative to one named constant rather than the default.

    ``"float"`` means the entry names exactly one qname for ``reference``, and this
    receives that constant's default value. Note ``baseline`` goes unused here —
    that is fine; it is offered to every modifier, not required by one.
    """
    return theta * reference
''',
}


@dataclass(frozen=True)
class _Kind:
    key: str  # "operation" | "cost" | "modifier"
    filename: str
    config_key: str  # the CA config key CUFLynx sets to the file path (CA #303)
    list_marker: str  # top-level assignment CUFLynx uses to remember ordering
    header: str
    templates: dict
    reserved: frozenset  # structural names a user func must not shadow


_KINDS = {
    "operation": _Kind(
        key="operation",
        filename="operation_funcs_user.py",
        config_key="operation_funcs_external_path",
        list_marker="CUFLYNX_OPERATIONS",
        header=_OPERATION_HEADER,
        templates=_OPERATION_TEMPLATES,
        reserved=frozenset(
            {
                "CUFLYNX_OPERATIONS", "series_to_constant", "differentiable",
                "np", "mb", "make_math_backend",
            }
        ),
    ),
    "cost": _Kind(
        key="cost",
        filename="cost_funcs_user.py",
        config_key="cost_funcs_external_path",
        list_marker="CUFLYNX_COSTS",
        header=_COST_HEADER,
        templates=_COST_TEMPLATES,
        reserved=frozenset(
            {
                "CUFLYNX_COSTS", "is_MLE", "cost_combiner", "differentiable",
                "np", "mb", "make_math_backend",
            }
        ),
    ),
    "modifier": _Kind(
        key="modifier",
        filename="modifier_funcs_user.py",
        config_key="modifier_funcs_external_path",
        list_marker="CUFLYNX_MODIFIERS",
        header=_MODIFIER_HEADER,
        templates=_MODIFIER_TEMPLATES,
        reserved=frozenset({"CUFLYNX_MODIFIERS", "modifier_func", "np"}),
    ),
}


def _kind(kind: str) -> _Kind:
    try:
        return _KINDS[kind]
    except KeyError:
        raise UserFuncError(f"unknown func kind '{kind}'") from None


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
def _user_dir(base_dir: str | None = None) -> Path:
    """Dir holding the external func files: the run's output directory when given
    (so funcs live with the outputs), else the user config dir as a fallback."""
    root = Path(base_dir) if base_dir else config_dir()
    return root / "user_funcs"


def _user_file(kind: str, base_dir: str | None = None) -> Path:
    return _user_dir(base_dir) / _kind(kind).filename


def external_path(kind: str, base_dir: str | None = None) -> str | None:
    """The external func file path for ``kind`` when it exists, else ``None``.

    Single source of the path CUFLynx passes to CA — into the analysis run configs
    (forwarded to ``CVS0DParamID`` / ``SensitivityAnalysis``) and to CA's discovery
    builders in ``obs_options`` (CA #303). ``base_dir`` is the output directory the
    funcs were saved under.
    """
    path = _user_file(kind, base_dir)
    return str(path) if path.is_file() else None


def external_paths(base_dir: str | None = None) -> dict:
    """``{ca_config_key: path}`` for every kind whose file exists — splat into a
    run config so CA loads the user funcs (``operation_funcs_external_path`` /
    ``cost_funcs_external_path`` / ``modifier_funcs_external_path``).

    Keyed by CA's config key rather than by our kind name, so a caller that
    forwards these never has to know how many kinds there are: adding one here
    puts it in the run config, the export bundle and the exported script at once.
    """
    return {
        k.config_key: str(_user_dir(base_dir) / k.filename)
        for k in _KINDS.values()
        if (_user_dir(base_dir) / k.filename).is_file()
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------
def _validate_name(kind: str, name: str) -> str:
    """Judge the name the ``def`` line gives this func.

    The messages name the ``def`` rather than a form field, because that is now
    the only place a name is entered.
    """
    k = _kind(kind)
    name = (name or "").strip()
    if not name:
        raise UserFuncError(f"{k.key} name is required")
    if not name.isidentifier() or keyword.iskeyword(name):
        raise UserFuncError(f"'{name}' is not a valid Python function name")
    if name.startswith("_"):
        raise UserFuncError(
            f"rename the function: a {k.key} name must not start with '_' "
            f"(found 'def {name}')"
        )
    if name in k.reserved:
        raise UserFuncError(
            f"rename the function: '{name}' is a reserved name (found 'def {name}')"
        )
    return name


def _validate_source(kind: str, source: str) -> tuple[str, str]:
    """Validate ``source`` is one top-level ``def``; return ``(name, source)``.

    **The code names the function, and nothing else does.** There used to be a
    separate name field that had to agree with the ``def``, which meant the same
    fact was entered twice and the only feedback for disagreeing was a rejected
    save. Deriving it removes the disagreement rather than reporting it.
    """
    k = _kind(kind)
    source = (source or "").strip("\n")
    if not source.strip():
        raise UserFuncError(f"{k.key} code is required")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise UserFuncError(f"invalid Python: {exc.msg} (line {exc.lineno})") from exc
    defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
    if len(tree.body) != 1 or len(defs) != 1:
        raise UserFuncError("code must be exactly one top-level function definition")
    return _validate_name(kind, defs[0].name), source


def func_name_from_source(kind: str, source: str) -> str:
    """The ``def`` name ``source`` would be saved under, or raise UserFuncError."""
    return _validate_source(kind, source)[0]


# ---------------------------------------------------------------------------
# Read / parse the user file
# ---------------------------------------------------------------------------
def _node_source(text: str, node: ast.FunctionDef) -> str | None:
    """Source for ``node`` *including* any decorator lines.

    ``ast.get_source_segment`` starts at the ``def`` line, dropping decorators, so
    extend the range up to the first decorator when present.
    """
    seg = ast.get_source_segment(text, node)
    if seg is None:
        return None
    if node.decorator_list:
        start = min(d.lineno for d in node.decorator_list) - 1
        return "\n".join(text.splitlines()[start : node.end_lineno])
    return seg


def _parse_existing(kind: str, base_dir: str | None = None) -> tuple[list[str], dict[str, str]]:
    """Return (ordered names, {name: source}) from the on-disk user file."""
    k = _kind(kind)
    path = _user_file(kind, base_dir)
    if not path.is_file():
        return [], {}
    text = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [], {}
    order: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == k.list_marker for t in node.targets
        ):
            try:
                order = [str(x) for x in ast.literal_eval(node.value)]
            except Exception:  # noqa: BLE001 - tolerate a hand-mangled list
                order = []
    sources: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name not in k.reserved:
            seg = _node_source(text, node)
            if seg is not None:
                sources[node.name] = seg
    ordered = [n for n in order if n in sources]
    ordered += [n for n in sources if n not in ordered]
    return ordered, sources


def read_user_funcs(kind: str, base_dir: str | None = None) -> dict:
    """List the current user funcs of ``kind`` plus the editor templates.

    Shape: ``{"kind", "functions": [{"name","source"}], "templates", "template",
    "available", "path"}``. ``available`` is False when CA isn't configured (the
    imported decorators can't resolve, so the funcs can't load). ``base_dir`` is
    the output directory the funcs are stored under.
    """
    k = _kind(kind)
    available = bool(_circulatory_autogen_src())
    order, sources = _parse_existing(kind, base_dir)
    return {
        "kind": k.key,
        "functions": [{"name": n, "source": sources[n]} for n in order],
        "templates": dict(k.templates),
        "template": next(iter(k.templates.values())),  # back-compat: the first tab
        "available": available,
        "path": str(_user_file(kind, base_dir)),
    }


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------
def _render(kind: str, order: list[str], sources: dict[str, str]) -> str:
    k = _kind(kind)
    names = ", ".join(f'"{n}"' for n in order)
    parts = [k.header, "", "", f"{k.list_marker} = [{names}]"]
    for name in order:
        parts.append("")
        parts.append("")
        parts.append(sources[name].rstrip("\n"))
    return "\n".join(parts).rstrip("\n") + "\n"


def save_user_func(
    kind: str,
    previous_name: str | None,
    source: str,
    base_dir: str | None = None,
) -> dict:
    """Save the ``kind`` func defined by ``source``, under ``base_dir`` (falling
    back to the config dir).

    **The name comes from the code**, not from the caller: ``source`` must be one
    top-level ``def``, and that ``def``'s name is the func's name.

    ``previous_name`` is the entry being edited, or None/"" for a new one. It
    exists only so that renaming the ``def`` while editing *renames* the func
    rather than leaving the old name behind as a second, stale copy — which is
    what a name derived purely from the code would otherwise do.

    Raises :class:`UserFuncError` (HTTP 422) on an invalid name or code.
    """
    name, source = _validate_source(kind, source)
    order, sources = _parse_existing(kind, base_dir)

    previous_name = (previous_name or "").strip()
    if previous_name and previous_name != name and previous_name in sources:
        # Renamed in place: keep its position in the file so the list does not
        # reshuffle under the user on a rename.
        order = [name if n == previous_name else n for n in order]
        del sources[previous_name]

    if name not in order:
        order.append(name)
    sources[name] = source

    _user_dir(base_dir).mkdir(parents=True, exist_ok=True)
    _user_file(kind, base_dir).write_text(_render(kind, order, sources), encoding="utf-8")
    _refresh_options()
    return read_user_funcs(kind, base_dir)


def delete_user_func(kind: str, name: str, base_dir: str | None = None) -> dict:
    """Remove the ``kind`` func ``name`` (under ``base_dir``); return the list.

    Raises :class:`UserFuncError` (HTTP 422) if it doesn't exist.
    """
    order, sources = _parse_existing(kind, base_dir)
    if name not in sources:
        raise UserFuncError(f"no user {kind} named '{name}'")
    order = [n for n in order if n != name]
    del sources[name]
    _user_file(kind, base_dir).write_text(_render(kind, order, sources), encoding="utf-8")
    _refresh_options()
    return read_user_funcs(kind, base_dir)


def _refresh_options() -> None:
    """Drop the introspection caches so a just-saved func shows in the dropdowns.

    Both caches, not just obs_options': operations and costs are offered by
    ``obs_options``, modifiers by ``solver_options``, and a saved modifier that
    does not appear in the params editor until restart reads as a failed save.
    """
    for module_name in ("obs_options", "solver_options"):
        try:
            import importlib

            importlib.import_module(module_name).reset_cache()
        except Exception:  # noqa: BLE001 - options cache is best-effort
            pass


# ---------------------------------------------------------------------------
# Back-compat shims for the original operation-only API (issue #58)
# ---------------------------------------------------------------------------
def read_user_operations() -> dict:
    return read_user_funcs("operation")


def save_user_operation(name: str, source: str) -> dict:
    return save_user_func("operation", name, source)


def delete_user_operation(name: str) -> dict:
    return delete_user_func("operation", name)
