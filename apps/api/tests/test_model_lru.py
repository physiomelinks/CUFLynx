"""The live tier keeps only the compiled models used last (engine and sim worker alike)."""
from __future__ import annotations

import importlib.util
import inspect

import engine as engine_mod
import pytest
from runtime_paths import runner_path


def _worker_module():
    spec = importlib.util.spec_from_file_location(
        "sim_worker_runner_under_test", runner_path("sim_worker_runner.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_model_used_longest_ago_is_dropped():
    evicted = []
    cache = engine_mod._ModelLRU(max_size=2, on_evict=evicted.append)
    cache["a"] = 1
    cache["b"] = 2
    assert cache.get("a") == 1  # a used again: b is now the oldest
    cache["c"] = 3
    assert list(cache) == ["a", "c"]
    assert evicted == ["b"]


def test_dropping_a_runner_forgets_its_protocol_binding():
    """A runner built again starts unbound, so the record that it was bound has to go
    with it, or the rebuilt runner would skip the binding and run without its protocol."""
    eng = engine_mod.SimulationEngine()
    for i in range(engine_mod.MAX_CACHED_MODELS + 1):
        eng._runners[(f"m{i}", "cellml", "CVODE_myokit")] = object()
        eng._runner_protocol_info[(f"m{i}", "cellml", "CVODE_myokit")] = object()
    assert ("m0", "cellml", "CVODE_myokit") not in eng._runners
    assert ("m0", "cellml", "CVODE_myokit") not in eng._runner_protocol_info
    assert len(eng._runners) == engine_mod.MAX_CACHED_MODELS
    assert len(eng._helpers) == 0 and isinstance(eng._helpers, engine_mod._ModelLRU)


def test_the_worker_caps_its_models_the_same_way():
    worker_mod = _worker_module()
    worker = worker_mod.Worker()
    for i in range(worker_mod.MAX_CACHED_MODELS + 1):
        worker._runners[f"m{i}"] = object()
        worker._runner_protocol_info[f"m{i}"] = {"protocol": i}
    assert "m0" not in worker._runners and "m0" not in worker._runner_protocol_info
    assert isinstance(worker._helpers, worker_mod._ModelLRU)


@pytest.mark.parametrize("name", ["MAX_CACHED_MODELS", "_ModelLRU"])
def test_the_worker_copy_matches_the_engine(name):
    """sim_worker_runner.py may not import the app, so it carries its own copy: keep
    the two in step, or the cap depends on whether an interpreter is set in Settings."""
    worker_mod = _worker_module()
    ours, theirs = getattr(engine_mod, name), getattr(worker_mod, name)
    if name == "MAX_CACHED_MODELS":
        assert ours == theirs
        return
    for method in ("__init__", "get", "__setitem__"):
        assert inspect.getsource(getattr(ours, method)) == inspect.getsource(
            getattr(theirs, method))
