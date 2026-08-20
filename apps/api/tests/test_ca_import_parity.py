"""The three copies of the circulatory_autogen import rule must agree.

CUFLynx resolves every CA module through :mod:`ca_imports`, with exactly two
deliberate duplicates — both because the code cannot import an app module:

* ``sim_worker_runner.py``, the live tier's standalone child, which CLAUDE.md
  requires to stay free of *every* app import;
* the exported ``run_pipeline.py`` (``export_pipeline.PIPELINE_SCRIPT``), which
  runs in the user's own environment with only circulatory_autogen beside it.

"Keep them in step" was a comment, and they had drifted anyway. What had drifted:

    behaviour                    ca_imports  sim_worker  run_pipeline
    CA_PACKAGES gate             yes         no          no
    RELOCATED_MODULES            yes         no          no
    missing attr -> ImportError  yes         yes         NO

The last row was live. The export's "older CA -> fall back" idiom is
``except ImportError``; a bare ``getattr`` raises ``AttributeError``, which sails
straight past it, so an exported run crashed over a feature the connected CA
merely predates instead of degrading around it.

So this file pins all three against one table rather than trusting the comment.
"""

from __future__ import annotations

import sys
import types

import pytest

import ca_imports
import export_pipeline
import sim_worker_runner


@pytest.fixture(scope="module")
def pipeline_ns():
    """The exported ``run_pipeline.py`` executed as a module, for its resolver.

    ``__name__`` is not ``"__main__"``, so the script defines its functions and
    stops — the same way ``test_export_pipeline`` reaches into it.
    """
    ns = {"__name__": "exported_pipeline", "__file__": "run_pipeline.py"}
    exec(compile(export_pipeline.PIPELINE_SCRIPT, "run_pipeline.py", "exec"), ns)  # noqa: S102
    return ns


# The one table. Every copy is checked against these, not against each other, so
# a change made in two places out of three still fails.
def test_the_namespace_is_spelled_the_same_everywhere(pipeline_ns):
    assert sim_worker_runner._CA_NAMESPACE == ca_imports.NAMESPACE
    assert pipeline_ns["CA_NAMESPACE"] == ca_imports.NAMESPACE


def test_every_copy_carries_the_same_ca_packages_gate(pipeline_ns):
    """Without the gate a non-CA name acquires a ``libcuflynx.`` prefix, and the
    resolver spends an import (and an error message) on a module that cannot exist."""
    assert sim_worker_runner._CA_PACKAGES == ca_imports.CA_PACKAGES
    assert pipeline_ns["CA_PACKAGES"] == ca_imports.CA_PACKAGES


def test_every_copy_carries_the_same_relocation_table(pipeline_ns):
    """Without it, ``operation_funcs`` and the ``*_funcs_user`` modules resolve
    only where a directory happens to be on sys.path — which is exactly what the
    packaged app does not have (#18)."""
    assert sim_worker_runner._RELOCATED_MODULES == dict(ca_imports.RELOCATED_MODULES)
    assert pipeline_ns["RELOCATED_MODULES"] == dict(ca_imports.RELOCATED_MODULES)


#: Names that exercise each arm of the rule once: a bare CA package, a dotted CA
#: module, a relocated bare name, and a name that is not CA's at all.
CANDIDATE_CASES = [
    "parsers",
    "param_id.paramID",
    "operation_funcs",
    "cost_funcs_user",
    "json",
]


@pytest.mark.parametrize("name", CANDIDATE_CASES)
def test_the_three_resolvers_offer_the_same_candidates(name, pipeline_ns, monkeypatch):
    """Same input, same list, same order.

    ``ca_imports`` orders by whether the namespace is importable *here*; the two
    copies always put the namespaced spelling first (they run where nothing has
    probed). Pinned with the namespace available, which is the case both agree on
    and the only one a current CA produces.
    """
    monkeypatch.setattr(ca_imports, "_namespace_available", lambda: True)
    expected = ca_imports.candidates(name)

    assert sim_worker_runner._ca_candidates(name) == expected
    assert pipeline_ns["ca_candidates"](name) == expected


def _inject(monkeypatch, dotted, module):
    """Register ``module`` at ``dotted`` (and its parents) for the test only."""
    parts = dotted.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[:i])
        if parent not in sys.modules:
            monkeypatch.setitem(sys.modules, parent, types.ModuleType(parent))
    monkeypatch.setitem(sys.modules, dotted, module)


def test_all_three_report_a_missing_attribute_as_an_import_error(monkeypatch, pipeline_ns):
    """The whole point of the ``ca_from`` shape.

    Every "this CA is too old, use the built-in default" arm in CUFLynx and in the
    export catches ``ImportError``. A missing attribute means precisely "this CA
    predates the feature", so it has to arrive as one — ``run_pipeline``'s bare
    ``getattr`` raised ``AttributeError`` and blew through those arms.
    """
    fake = types.SimpleNamespace(present=1)
    _inject(monkeypatch, f"{ca_imports.NAMESPACE}.utilities.fake_mod", fake)

    assert ca_imports.ca_from("utilities.fake_mod", "present") == 1
    assert sim_worker_runner._ca_from("utilities.fake_mod", "present") == 1
    assert pipeline_ns["ca_from"]("utilities.fake_mod", "present") == 1

    with pytest.raises(ImportError):
        ca_imports.ca_from("utilities.fake_mod", "absent")
    with pytest.raises(ImportError):
        sim_worker_runner._ca_from("utilities.fake_mod", "absent")
    with pytest.raises(ImportError):
        pipeline_ns["ca_from"]("utilities.fake_mod", "absent")


def test_all_three_return_several_names_as_a_tuple(monkeypatch, pipeline_ns):
    fake = types.SimpleNamespace(a=1, b=2)
    _inject(monkeypatch, f"{ca_imports.NAMESPACE}.utilities.fake_pair", fake)

    assert ca_imports.ca_from("utilities.fake_pair", "a", "b") == (1, 2)
    assert sim_worker_runner._ca_from("utilities.fake_pair", "a", "b") == (1, 2)
    assert pipeline_ns["ca_from"]("utilities.fake_pair", "a", "b") == (1, 2)


def test_all_three_blame_the_ca_directory_rather_than_the_module(pipeline_ns):
    """Not ``No module named 'generators'``. Naming the CA directory is the
    difference between a user who can act and one who cannot (issue #180)."""
    name = "utilities.definitely_not_a_real_ca_module"

    for resolve in (
        ca_imports.ca_import,
        sim_worker_runner._ca_import,
        pipeline_ns["ca_import"],
    ):
        with pytest.raises(ImportError) as excinfo:
            resolve(name)
        message = str(excinfo.value)
        assert "circulatory_autogen" in message
        # Both spellings are named, so the message is the same whichever layout
        # the reader's checkout is in.
        assert f"{ca_imports.NAMESPACE}.{name}" in message
        assert name in message


@pytest.mark.unit
def test_every_copy_declines_a_module_that_is_still_importing(monkeypatch):
    """The ``sys.modules`` fast path is duplicated, so the guard on it has to be too.

    Python publishes a module before running its body; handing that out is what produced
    "has no ANALYSIS_OPTIONS" on a copy that had it. ``ca_imports`` declines an
    initialising entry and lets ``importlib`` block instead -- and ``sim_worker_runner``
    carries its own copy of this resolver (it must stay free of app imports), so without
    this the two drift and the live tier keeps the bug.
    """
    class _Spec:
        _initializing = True

    name = f"{ca_imports.NAMESPACE}.utilities.racy"
    half = types.ModuleType(name)
    half.__spec__ = _Spec()
    whole = types.ModuleType(name)
    whole.PRESENT = 1

    monkeypatch.setitem(sys.modules, name, half)
    # Pin the ordering rather than inherit it. ``sim_worker_runner._ca_candidates`` always
    # leads with the namespaced spelling, but ``ca_imports.candidates`` leads with it only
    # when libcuflynx is importable -- true on a dev machine and in the packaged app, false
    # on CI, which installs none. Left to the environment this test compares the two
    # resolvers on *different* candidate lists, and passes or fails on where it is run.
    monkeypatch.setattr(ca_imports, "_namespaced", True)

    def fake_import(cand):
        if cand == name:
            return whole
        # ModuleNotFoundError naming the candidate, because that is what "this spelling is
        # absent" looks like: a bare ImportError means "the module is there and something
        # *it* imports is not", which ca_import re-raises rather than trying the other.
        raise ModuleNotFoundError(f"No module named {cand!r}", name=cand)

    # ca_imports.importlib *is* the importlib module, so this patches the copy
    # sim_worker_runner imports locally too.
    monkeypatch.setattr(ca_imports.importlib, "import_module", fake_import)

    assert ca_imports.ca_import("utilities.racy") is whole
    assert sim_worker_runner._ca_import("utilities.racy") is whole
