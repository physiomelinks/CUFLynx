"""The shipped cost templates are executable and mean what their docstrings say.

A template is copy-paste starting material: a user selects it in the "Custom
funcs" dialog, edits a little, and calibrates against it. So a template that
does not run, or whose documented special cases are wrong, is worse than no
template -- it is a wrong answer with an official-looking provenance.

The robust-loss template (issue #201) carries a claim that has to be true rather
than plausible: at ``alpha = 2`` it is the **L2** objective, which is exactly
**half of CA's MSE** and bit-identical to CA's ``gaussian_MLE`` (CA defines
``MSE = 2*gaussian_MLE``). Barron's half is kept, so the default lands on a cost
CA already has rather than on a rescaled variant of one.

That factor is asserted from both directions -- half of MSE is L2, and the
template at alpha=2 is that same value -- because a constant factor is invisible
in a single fit's optimum and very visible in how much a data_item contributes
next to its neighbours.
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
        from ca_imports import ca_from

        mb = ca_from("param_id.math_backend", "make_math_backend")("numpy")
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


def _l2_reference(output, desired_mean, std, weight):
    """The L2 / least-squares objective: ``0.5 * mean(residual**2)``.

    The half is not decoration -- it is what makes the value the Gaussian
    negative log-likelihood up to constants, which is why CA's ``gaussian_MLE``
    carries it and why ``MSE`` is defined as twice this.
    """
    per = ((output - desired_mean) / std) ** 2 * weight
    return float(0.5 * np.sum(per) / np.size(per))


# ---------------------------------------------------------------------------
# The claim issue #201 asks for
# ---------------------------------------------------------------------------
def test_half_of_mse_is_l2():
    """The premise the rest rests on, asserted rather than assumed: L2 (with its
    half) *is* half of CA's MSE. If this stops holding, the identity below would
    still pass while meaning something different."""
    assert _l2_reference(OUTPUT, TARGET, STD, WEIGHT) == pytest.approx(
        0.5 * _mse_reference(OUTPUT, TARGET, STD, WEIGHT), rel=1e-12
    )


def test_the_robust_loss_at_alpha_2_is_l2_and_so_half_of_mse():
    """Barron's alpha=2 case is ``0.5*(x/c)**2`` and the half is kept, so the
    default lands exactly on the L2 objective -- half of MSE."""
    robust = _load("robust", "robust_loss")

    got = robust(OUTPUT, TARGET, STD, WEIGHT, alpha=2.0, c=1.0)

    assert got == pytest.approx(_l2_reference(OUTPUT, TARGET, STD, WEIGHT), rel=1e-12)
    assert got == pytest.approx(0.5 * _mse_reference(OUTPUT, TARGET, STD, WEIGHT), rel=1e-12)


def test_alpha_2_is_the_default():
    """Stated separately because it is the *default* that decides what a user gets
    on selecting this cost; a template defaulting to alpha=1 would satisfy the
    test above and still give everyone a different cost."""
    robust = _load("robust", "robust_loss")

    assert robust(OUTPUT, TARGET, STD, WEIGHT) == pytest.approx(
        _l2_reference(OUTPUT, TARGET, STD, WEIGHT), rel=1e-12
    )


def test_the_half_halves_the_cost_relative_to_an_mse_item():
    """The consequence worth knowing, pinned so it cannot be discovered mid-run.

    A constant factor cannot move a single fit's optimum, but it does change this
    item's size relative to others: an item scored with this at alpha=2
    contributes half what the same item scored with MSE would.
    """
    robust = _load("robust", "robust_loss")

    ratio = robust(OUTPUT, TARGET, STD, WEIGHT) / _mse_reference(OUTPUT, TARGET, STD, WEIGHT)

    assert ratio == pytest.approx(0.5, rel=1e-12)


@pytest.mark.integration
def test_alpha_2_is_bit_identical_to_cas_gaussian_mle(requires_ca):
    """Against CA's real cost objects rather than a restatement of them, so the
    references above cannot drift from the functions they claim to mirror.

    ``gaussian_MLE`` is the one it should land on exactly: CA defines
    ``MSE = 2*gaussian_MLE``, so keeping Barron's half puts the default on a cost
    CA already has rather than on a rescaled variant of one.
    """
    from ca_imports import ca_from

    scriptFunctionParser = ca_from("parsers.PrimitiveParsers", "scriptFunctionParser")

    funcs = scriptFunctionParser().get_cost_funcs_dict("numpy")
    robust = _load("robust", "robust_loss")

    got = robust(OUTPUT, TARGET, STD, WEIGHT, alpha=2.0, c=1.0)

    assert got == pytest.approx(float(funcs["gaussian_MLE"](OUTPUT, TARGET, STD, WEIGHT)), rel=1e-12)
    assert got == pytest.approx(0.5 * float(funcs["MSE"](OUTPUT, TARGET, STD, WEIGHT)), rel=1e-12)


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
    expected = float(np.mean(closed_form(sq) * WEIGHT))

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
    l2 = _l2_reference(OUTPUT, TARGET, STD, WEIGHT)

    wide = robust(OUTPUT, TARGET, STD, WEIGHT, alpha=0.0, c=1000.0)

    assert wide * 1000.0**2 == pytest.approx(l2, rel=1e-4)
    # And at c = 1 the identity of the first test is untouched.
    assert robust(OUTPUT, TARGET, STD, WEIGHT, alpha=2.0, c=1.0) == pytest.approx(l2)


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
