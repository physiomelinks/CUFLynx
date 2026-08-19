"""The extra Linux bundle is opt-in, and every asset built gets tested.

`CUFLynx-linux-x86_64-full` differs from `CUFLynx-linux-x86_64` in exactly one thing: the
`full` extra, which adds autoemulate and pymc/arviz/pytensor. Both come off the same runner
and the same spec, gated by ``CUFLYNX_BUNDLE_FULL``.

Four ways that quietly goes wrong, and none shows up as a red build:

* **The gate stops gating.** If ``_FULL`` were ever hardcoded true, the four ordinary bundles
  would grow ~350 MB of torch. If it were hardcoded false, the full asset would be built,
  named "full", published, and contain none of what its name promises -- the exact
  silent-wrong the spec's ``_REQUIRED`` guard exists to prevent, since ``collect_all`` on an
  absent package returns empty lists rather than raising.
* **A build asset has no analysis-e2e entry.** That job is the only thing that runs a built
  binary end to end. An asset missing from its matrix is published untested, which is how a
  mis-collected torch or pytensor would reach a user.
* **Cython comes back into the bundle.** It breaks Myokit's compiler in the full bundle only,
  at run time only -- see ``test_cython_is_excluded_from_the_bundle``.
* **torch stops being the +cpu build.** The CUDA wheels add 2.7 GB and take the asset past
  GitHub's 2 GiB release limit, which is otherwise first reported by a failed publish on a
  tag that has already been pushed.

The last three were all found by the first rehearsal build of the full bundle, not by CI.
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


def _spec_values(full: str, wanted: set) -> dict:
    """Evaluate just the named spec assignments, with the gate set to ``full``.

    The spec cannot be imported (it imports PyInstaller and reads the build environment),
    so lift out the assignments that decide the lists and run only those.
    """
    tree = ast.parse(_SPEC.read_text(encoding="utf-8"), filename=str(_SPEC))
    picked = [n for n in tree.body
              if isinstance(n, ast.Assign)
              and any(getattr(t, "id", None) in wanted for t in n.targets)]
    found = {t.id for n in picked for t in n.targets}
    assert found == wanted, f"spec no longer defines {sorted(wanted - found)}"

    env = dict(os.environ)
    env["CUFLYNX_BUNDLE_FULL"] = full
    namespace = {"os": type("_os", (), {"environ": env})}
    exec(compile(ast.Module(body=picked, type_ignores=[]), "<spec>", "exec"), namespace)
    return namespace


def _required_for(full: str) -> tuple:
    ns = _spec_values(full, {"_ANALYSIS_PKGS", "_FULL", "_FULL_PKGS", "_REQUIRED"})
    return ns["_REQUIRED"], ns["_FULL_PKGS"]


def _excludes_for(full: str) -> list:
    return _spec_values(full, {"_FULL", "_BASE_EXCLUDES", "_FULL_KEEPS", "excludes"})["excludes"]


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
        f"~350 MB of torch to all four platforms."
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


@pytest.mark.unit
def test_cython_is_excluded_from_every_bundle():
    """Bundling Cython breaks CVODE_myokit -- in the full bundle only, and only at run time.

    setuptools' build_ext probes for Cython behind ``except ImportError``. Frozen, that
    import fails with FileNotFoundError (Cython/Utility/*.cpp are data files PyInstaller
    does not collect), which the except does not catch, so every Myokit compilation dies
    with "CompilationError: Unable to compile". Nothing pulls Cython into the four
    ordinary bundles, so this can only ever be caught by the full one -- and only by a
    real simulation, which is why it survived to the first rehearsal build.
    """
    for full in ("0", "1"):
        assert "Cython" in _excludes_for(full), (
            f"Cython is back in the bundle (CUFLYNX_BUNDLE_FULL={full}). The [full] extra "
            f"installs it, and its presence breaks the CVODE_myokit backend at run time -- "
            f"see the comment on the excludes list. The other four assets are unaffected, "
            f"so CI stays green."
        )


@pytest.mark.unit
def test_ipython_is_kept_in_the_full_bundle_only():
    """Excluding IPython does not trim a notebook helper -- it breaks `import autoemulate`.

    autoemulate's core/plotting.py does ``from IPython.display import ...`` at module
    scope, unguarded, so the whole package fails to import. Nothing else in the bundle
    imports autoemulate, so every other check in the pipeline passed on a bundle whose
    one distinguishing feature could not load; the runner-mode probe in analysis_smoke.py
    is what caught it.
    """
    assert "IPython" not in _excludes_for("1"), (
        "IPython is excluded from the full bundle, so `import autoemulate` raises "
        "ModuleNotFoundError and the Emulator tab cannot work at all."
    )
    assert "IPython" in _excludes_for("0"), (
        "IPython is now bundled into the four ordinary assets, where nothing imports it."
    )


def _build_steps() -> list:
    return _workflow()["jobs"]["build"]["steps"]


def _step_index(predicate, what: str) -> int:
    for i, step in enumerate(_build_steps()):
        if predicate(step):
            return i
    raise AssertionError(f"no build step {what}")


@pytest.mark.unit
def test_the_full_bundle_swaps_torch_for_its_cpu_build_after_the_extra():
    """The CUDA wheels are 2.7 GB and put the asset over GitHub's 2 GiB release limit.

    Order matters, and the intuitive order is the wrong one. Installing the +cpu wheel
    first does not survive: ``pip install .[...,full]`` re-resolves torch against the
    extra's own pins and replaces it -- observed, 2.13.0+cpu going in and 2.12.1+cu130
    coming out. The extra has to choose the version, and the swap has to come after it.
    """
    swap = _step_index(
        lambda s: "download.pytorch.org/whl/cpu" in str(s.get("run", "")),
        "installing torch from the PyTorch CPU index",
    )
    extra = _step_index(
        lambda s: s.get("name") == "Install Python deps",
        "named 'Install Python deps'",
    )
    assert swap > extra, (
        "the CPU torch swap must come AFTER the [full] extra: installed before, the "
        "extra re-resolves torch against its own pins and pulls ~2.7 GB of CUDA "
        "libraries back in."
    )

    steps = _build_steps()
    assert steps[swap].get("if") == "matrix.full == '1'", (
        f"the CPU torch swap is gated on {steps[swap].get('if')!r}; it must apply to the "
        f"full bundle only, so the other four assets are untouched."
    )
    # Orphaned CUDA packages are not removed by pip, and PyInstaller's bundled
    # hook-nvidia.* would collect them even though nothing imports them.
    assert "nvidia-" in str(steps[swap].get("run", "")), (
        "the swap leaves the nvidia-* packages installed; swapping the wheel alone does "
        "not shrink the bundle, because the hooks collect them regardless."
    )
    # And something after it has to confirm both halves actually took.
    check = str(_build_steps()[swap + 1].get("run", ""))
    assert "+cpu" in check and "nvidia-" in check, (
        "nothing verifies that torch ended up as +cpu with no CUDA packages left, so a "
        "failed swap would only surface as a failed release upload."
    )


@pytest.mark.unit
def test_every_asset_is_size_checked_before_it_is_uploaded():
    """2 GiB is a hard GitHub limit, and the upload is the last step of the pipeline.

    Without a check here, an oversized bundle is first reported by a failed publish --
    on a tag that has already been pushed.
    """
    steps = _build_steps()
    guard = _step_index(
        lambda s: "2 * 1024 ** 3" in str(s.get("run", "")),
        "checking the asset against the 2 GiB release limit",
    )
    upload = _step_index(
        lambda s: str(s.get("uses", "")).startswith("actions/upload-artifact"),
        "uploading the artifact",
    )
    assert guard < upload, "the size check must run before the artifact is uploaded"
    assert "if" not in steps[guard], (
        "the size check is conditional; it should apply to every asset, since any of them "
        "can grow past the limit."
    )


@pytest.mark.unit
def test_the_full_asset_is_probed_for_the_emulator_and_pymc():
    """Nothing else in the pipeline touches the only two features this asset adds.

    Every other check -- the smoke test's three solver backends, the sensitivity and
    calibration run -- behaves identically in both Linux bundles, so all of them pass on
    a "full" bundle that in fact carries neither autoemulate nor pymc. The spec's build
    time guard does not cover it either: it proves the packages import in the *build*
    interpreter, not in the frozen one.
    """
    e2e = [e for e in _matrix("analysis-e2e") if e["asset"] == _FULL_ASSET]
    assert e2e and e2e[0].get("full") == "1", (
        f"the {_FULL_ASSET} analysis-e2e entry does not set full, so analysis_smoke.py "
        f"runs without --full and the emulator/pyMC stack is never executed."
    )
    plain = [e for e in _matrix("analysis-e2e") if e["asset"] != _FULL_ASSET]
    assert not [e for e in plain if e.get("full")], (
        "an ordinary asset is marked full; the probe would fail on a bundle that is not "
        "supposed to contain the emulator at all."
    )

    step = _workflow()["jobs"]["analysis-e2e"]["steps"][-1]["run"]
    assert "--full" in step, "analysis_smoke.py is never passed --full"

    smoke = (_REPO / "scripts" / "analysis_smoke.py").read_text(encoding="utf-8")
    assert "def _check_full_stack" in smoke and "_check_full_stack(args.binary)" in smoke, (
        "scripts/analysis_smoke.py defines no full-stack probe, or never calls it."
    )
    # Importing the packages is not the feature. v0.4.1 shipped a bundle that passed the
    # import probe and whose Emulator tab still said the options could not be read, so the
    # app's own endpoint has to be asked as well.
    assert ("def _check_emulator_is_usable" in smoke
            and "_check_emulator_is_usable(base)" in smoke), (
        "scripts/analysis_smoke.py does not ask /api/emulator/defaults, so a bundle whose "
        "Emulator tab is broken still passes: importing autoemulate and rendering the tab "
        "are different things."
    )
    assert "/api/emulator/defaults" in smoke, (
        "the emulator probe does not call the endpoint the Emulator tab calls."
    )
    # The pytensor compile is the point of the probe, not an incidental import: pytensor
    # compiles C at run time, which is where a frozen process breaks.
    assert "pytensor.function" in smoke, (
        "the probe imports pytensor but never compiles anything with it, so it would not "
        "catch the failure it exists for."
    )
