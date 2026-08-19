"""The extra Linux bundle is opt-in, and every asset built gets tested.

`CUFLynx-linux-x86_64-full` differs from `CUFLynx-linux-x86_64` in exactly one thing: the
`full` extra, which adds autoemulate and pymc/arviz/pytensor. Both come off the same runner
and the same spec, gated by ``CUFLYNX_BUNDLE_FULL``.

Two ways that quietly goes wrong, and neither shows up as a red build:

* **The gate stops gating.** If ``_FULL`` were ever hardcoded true, the four ordinary bundles
  would grow ~750 MB of torch. If it were hardcoded false, the full asset would be built,
  named "full", published, and contain none of what its name promises -- the exact
  silent-wrong the spec's ``_REQUIRED`` guard exists to prevent, since ``collect_all`` on an
  absent package returns empty lists rather than raising.
* **A build asset has no analysis-e2e entry.** That job is the only thing that runs a built
  binary end to end. An asset missing from its matrix is published untested, which is how a
  mis-collected torch or pytensor would reach a user.
"""
import ast
import os
import pathlib

import pytest
import yaml

_REPO = pathlib.Path(__file__).resolve().parents[3]
_SPEC = _REPO / "packaging" / "cuflynx.spec"
_WORKFLOW = _REPO / ".github" / "workflows" / "release.yml"

_FULL_ASSET = "CUFLynx-linux-x86_64-full"


def _workflow():
    return yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))


def _matrix(job):
    return _workflow()["jobs"][job]["strategy"]["matrix"]["include"]


def _required_for(full: str) -> tuple:
    """Evaluate just the spec's guard-list assignments, with the gate set to ``full``.

    The spec cannot be imported (it imports PyInstaller and reads the build environment),
    so lift out the four assignments that decide the list and run only those.
    """
    tree = ast.parse(_SPEC.read_text(encoding="utf-8"), filename=str(_SPEC))
    wanted = {"_ANALYSIS_PKGS", "_FULL", "_FULL_PKGS", "_REQUIRED"}
    picked = [n for n in tree.body
              if isinstance(n, ast.Assign)
              and any(getattr(t, "id", None) in wanted for t in n.targets)]
    found = {t.id for n in picked for t in n.targets}
    assert found == wanted, f"spec no longer defines {sorted(wanted - found)}"

    env = dict(os.environ)
    env["CUFLYNX_BUNDLE_FULL"] = full
    namespace = {"os": type("_os", (), {"environ": env})}
    exec(compile(ast.Module(body=picked, type_ignores=[]), "<spec>", "exec"), namespace)
    return namespace["_REQUIRED"], namespace["_FULL_PKGS"]


@pytest.mark.unit
def test_the_emulation_stack_is_required_only_for_the_full_bundle():
    off, full_pkgs = _required_for("0")
    on, _ = _required_for("1")

    assert set(full_pkgs) <= set(on), (
        f"CUFLYNX_BUNDLE_FULL=1 does not require {sorted(set(full_pkgs) - set(on))}. The full "
        f"asset would build without them: collect_all on an absent package returns empty "
        f"lists rather than raising, so it would publish named 'full' and contain neither "
        f"the emulator nor the pyMC backend."
    )
    assert not set(full_pkgs) & set(off), (
        f"the ordinary bundles require {sorted(set(full_pkgs) & set(off))}, which would add "
        f"~750 MB of torch to all four platforms."
    )
    # Everything else must be identical -- the two Linux bundles differ in this and nothing else.
    assert set(on) - set(off) == set(full_pkgs)


@pytest.mark.unit
def test_exactly_one_build_entry_is_full_and_it_is_linux():
    entries = _matrix("build")
    full = [e for e in entries if e.get("full") == "1"]
    assert len(full) == 1, f"expected one full entry, got {[e.get('asset') for e in full]}"
    assert full[0]["asset"] == _FULL_ASSET
    assert full[0]["os"].startswith("ubuntu"), full[0]["os"]

    plain = [e for e in entries if e.get("full") != "1"]
    assert len(plain) == 4, [e["asset"] for e in plain]
    # The two Linux entries must share a runner: same glibc floor, or the "identical except
    # for the extra" claim in the release notes is false.
    linux = [e for e in entries if e["os"].startswith("ubuntu")]
    assert len({e["os"] for e in linux}) == 1, linux


@pytest.mark.unit
def test_every_built_asset_is_exercised_by_analysis_e2e():
    built = {e["asset"] for e in _matrix("build")}
    tested = {e["asset"] for e in _matrix("analysis-e2e")}
    assert built == tested, (
        f"built but never run: {sorted(built - tested)}; "
        f"run but never built: {sorted(tested - built)}. analysis-e2e is the only job that "
        f"executes a built binary, so an asset missing from it is published untested."
    )


@pytest.mark.unit
def test_the_full_extra_is_declared_and_asks_for_the_libcuflynx_extras():
    text = (_REPO / "apps" / "api" / "pyproject.toml").read_text(encoding="utf-8")
    assert "\nfull = [" in text, "apps/api/pyproject.toml no longer defines a [full] extra"
    # The engine's own extras, not just the top-level packages: CA gates its emulator and
    # pyMC code paths on them, and resolves them by name in its error messages.
    assert "libcuflynx[emulation,uq]" in text, (
        "the full extra must pull libcuflynx's own [emulation,uq] extras, not only the "
        "packages -- CA names those extras when a feature is unavailable."
    )
