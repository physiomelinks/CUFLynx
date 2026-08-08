"""The shipped cost templates are executable and mean what their docstrings say.

A template is copy-paste starting material: a user selects it in the "Custom
funcs" dialog, edits a little, and calibrates against it. So a template that
does not run, or whose documented special cases are wrong, is worse than no
template -- it is a wrong answer with an official-looking provenance.

The robust-loss template (issue #201) carries a claim that has to be true rather
than plausible: at ``alpha = 2`` it *is* MSE. If that drifts, switching a
data_item to it and leaving alpha alone would silently rescale the cost, and
every weight in the obs_data would then mean something slightly different.
"""
import math

import numpy as np
import pytest

from user_funcs import _COST_TEMPLATES


def _load(template_key, name):
    """Execute a template the way CA does: with ``mb`` bound to the numpy backend.

    Uses CA's own math backend when it is importable, so the template is checked
    against the object it will really be handed; falls back to numpy otherwise,
    since the arithmetic here uses only the shared subset.
    """
    try:  # pragma: no cover - depends on the environment, both paths are exercised
        from param_id.math_backend import make_math_backend

        mb = make_math_backend("numpy")
    except Exception:  # noqa: BLE001 - no CA on the path
        mb = _NumpyBackend()

    namespace = {"np": np, "mb": mb, "differentiable": lambda f: f,
                 "is_MLE": lambda f: f, "cost_combiner": lambda f: f}
    exec(_COST_TEMPLATES[template_key], namespace)  # noqa: S102 - our own source
    return namespace[name]


class _NumpyBackend:
    """The subset of CA's math backend these templates use."""
    power = staticmethod(np.power)
    exp = staticmethod(np.exp)
    log = staticmethod(np.log)
    sum = staticmethod(np.sum)
    mean = staticmethod(np.mean)
    abs = staticmethod(np.abs)
    numel = staticmethod(np.size)


OUTPUT = np.array([1.0, 2.5, 4.0, 3.25])
TARGET = np.array([1.2, 2.0, 3.0, 3.5])
STD = np.array([0.5, 0.5, 2.0, 1.0])
WEIGHT = np.array([1.0, 2.0, 0.5, 1.0])


def _mse_reference(output, desired_mean, std, weight):
    """CA's MSE, written out: ``2 * gaussian_MLE``, i.e. the weighted mean of the
    squared standardised residual (funcs_user/cost_funcs_user.py)."""
    per = ((output - desired_mean) / std) ** 2 * weight
    return float(np.sum(per) / np.size(per))


# ---------------------------------------------------------------------------
# The claim issue #201 asks for
# ---------------------------------------------------------------------------
def test_the_robust_loss_at_alpha_2_is_exactly_mse():
    """The identity the template's docstring promises, so a user who selects the
    robust loss and leaves alpha alone changes nothing about their cost."""
    robust = _load("robust", "robust_loss")

    got = robust(OUTPUT, TARGET, STD, WEIGHT, alpha=2.0, c=1.0)

    assert got == pytest.approx(_mse_reference(OUTPUT, TARGET, STD, WEIGHT), rel=1e-12)


def test_alpha_2_is_the_default_so_the_swap_is_a_no_op():
    """Stated separately because it is the *default* that makes the swap safe;
    a template defaulting to alpha=1 would satisfy the test above and still
    change every user's cost the moment they selected it."""
    robust = _load("robust", "robust_loss")

    assert robust(OUTPUT, TARGET, STD, WEIGHT) == pytest.approx(
        _mse_reference(OUTPUT, TARGET, STD, WEIGHT), rel=1e-12
    )


@pytest.mark.integration
def test_the_robust_loss_at_alpha_2_matches_cas_own_mse(requires_ca):
    """The same identity against CA's actual MSE rather than a restatement of it,
    so the reference above cannot drift from the function it claims to mirror."""
    from parsers.PrimitiveParsers import scriptFunctionParser

    mse = scriptFunctionParser().get_cost_funcs_dict("numpy")["MSE"]
    robust = _load("robust", "robust_loss")

    assert robust(OUTPUT, TARGET, STD, WEIGHT, alpha=2.0, c=1.0) == pytest.approx(
        float(mse(OUTPUT, TARGET, STD, WEIGHT)), rel=1e-12
    )


# ---------------------------------------------------------------------------
# The rest of the family, since the special cases are hand-written
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "alpha,closed_form",
    [
        (0.0, lambda sq: np.log(0.5 * sq + 1.0)),
        (-math.inf, lambda sq: 1.0 - np.exp(-0.5 * sq)),
        (1.0, lambda sq: (1.0 / 1.0) * (np.power(sq / 1.0 + 1.0, 0.5) - 1.0)),
        (-2.0, lambda sq: (4.0 / -2.0) * (np.power(sq / 4.0 + 1.0, -1.0) - 1.0)),
    ],
)
def test_each_branch_matches_its_closed_form(alpha, closed_form):
    """The general form divides by alpha and by |alpha - 2|, so the alpha = 0 and
    alpha = 2 branches are not optional -- and a wrong branch is a plausible
    number, not an error."""
    robust = _load("robust", "robust_loss")
    sq = ((OUTPUT - TARGET) / STD) ** 2
    expected = float(np.mean(2.0 * closed_form(sq) * WEIGHT))

    got = robust(OUTPUT, TARGET, STD, WEIGHT, alpha=alpha, c=1.0)

    assert got == pytest.approx(expected, rel=1e-12)


def test_a_lower_alpha_gives_an_outlier_less_influence():
    """The reason to use it at all. A single bad point must not dominate."""
    output = np.array([1.0, 1.0, 1.0, 50.0])  # the last point is the outlier
    target = np.ones(4)
    std = np.ones(4)
    weight = np.ones(4)
    robust = _load("robust", "robust_loss")

    quadratic = robust(output, target, std, weight, alpha=2.0)
    cauchy = robust(output, target, std, weight, alpha=0.0)
    welsch = robust(output, target, std, weight, alpha=-math.inf)

    assert welsch < cauchy < quadratic
    # Welsch saturates: the outlier contributes a bounded amount however bad it is.
    worse = robust(np.array([1.0, 1.0, 1.0, 5000.0]), target, std, weight,
                   alpha=-math.inf)
    assert worse == pytest.approx(welsch, rel=1e-6)


def test_c_rescales_as_well_as_setting_the_transition():
    """``c`` is not only where down-weighting starts -- it divides the residual, so
    it rescales the whole cost by ``1/c**2``.

    Worth pinning because it is the easy misreading: residuals well inside ``c``
    stay quadratic, so a large ``c`` recovers the *shape* of MSE but at
    ``MSE / c**2``, not MSE. A user comparing two data_items with different ``c``
    is comparing differently-scaled costs, which is the same trap as an
    unnormalised weight.
    """
    robust = _load("robust", "robust_loss")
    mse = _mse_reference(OUTPUT, TARGET, STD, WEIGHT)

    wide = robust(OUTPUT, TARGET, STD, WEIGHT, alpha=0.0, c=1000.0)

    assert wide * 1000.0**2 == pytest.approx(mse, rel=1e-4)
    # And at c = 1 the identity of the first test is untouched.
    assert robust(OUTPUT, TARGET, STD, WEIGHT, alpha=2.0, c=1.0) == pytest.approx(mse)


# ---------------------------------------------------------------------------
# Every template, not just the new one
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("key", sorted(_COST_TEMPLATES))
def test_every_cost_template_runs_and_returns_a_finite_scalar(key):
    """A template that does not execute is a broken starting point shipped as a
    working one."""
    namespace = {"np": np, "mb": _NumpyBackend(), "differentiable": lambda f: f,
                 "is_MLE": lambda f: f, "cost_combiner": lambda f: f}
    exec(_COST_TEMPLATES[key], namespace)  # noqa: S102 - our own source
    func = next(v for k, v in namespace.items()
                if callable(v) and k.startswith(("my_", "robust_")))

    try:
        value = func(OUTPUT, TARGET, STD, WEIGHT)
    except TypeError:  # the kwargs template deliberately declares no `std`
        value = func(OUTPUT, TARGET, WEIGHT)

    assert np.isfinite(float(value))
