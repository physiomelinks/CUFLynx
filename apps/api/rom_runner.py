"""Standalone reduced-order-model (ROM) runner, spawned as a subprocess by the API.

A ROM is set up by a *rom setup* file (libcuflynx's ``rom_setup.schema.json``): the model, which states are fitted,
the run design and the scales. Everything here goes through libcuflynx's ``cuflynx-rom setup`` command
(``rom.setup_cli``), so CUFLynx carries no ROM logic of its own and the results stay in libcuflynx's formats
(``<root>/runs``, ``<root>/fits``, ``<root>/results.json``).

Usage:  python -u rom_runner.py <config.json>

config.json:
{
  "action": "probe" | "generators" | "check" | "describe" | "runs" | "fit",
  "setup": "/abs/path/model.rom_setup.json",      (check, describe, runs, fit)
  "root": "/abs/output/dir",                       (runs, fit)
  "methods": ["beat map", ...] | null,             (fit; null = every method)
  "seed": null | int,                              (runs, fit)
  "external": null | "/abs/schedule_funcs.py"      (generators)
}

Output: progress lines, then one ``RESULT_MARKER`` line with a JSON payload, then ``DONE_MARKER`` -- or
``FAIL_MARKER`` and the reason. ``probe`` answers whether this interpreter's libcuflynx has rom setups at all:
``{"available": bool, "reason": str | null}``. ROMs are not in the public libcuflynx release yet, so a missing
module is the usual answer, not an error.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import traceback
from pathlib import Path

from ca_imports import ensure_ca_path

DONE_MARKER = "__ROM_DONE__"
FAIL_MARKER = "__ROM_FAILED__"
RESULT_MARKER = "__ROM_RESULT__ "

UNAVAILABLE = (
    "This libcuflynx has no reduced-order models (rom setups). They are not in the public release yet: point "
    "Settings at a circulatory_autogen checkout (or a Python) whose libcuflynx has libcuflynx.rom.setup_cli."
)


def setup_cli():
    """libcuflynx's ``rom.setup_cli`` module, or None when this libcuflynx has none.

    Imported by its namespaced name rather than through ``ca_import``: ``rom`` arrived after CA's move under
    ``libcuflynx.`` and has no flat spelling, so it is not one of ``CA_PACKAGES``. Only the module itself being
    absent means "no ROMs"; any other import failure (CasADi missing, say) is raised with its own message.
    """
    import importlib  # noqa: PLC0415

    ensure_ca_path()
    from ca_imports import NAMESPACE  # noqa: PLC0415 (shipped into runners/ too)

    name = f"{NAMESPACE}.rom.setup_cli"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name and name.startswith(exc.name):
            return None
        raise


def _json_call(cli, argv: list[str]) -> tuple[int, dict]:
    """Run a ``--json`` subcommand in-process and parse what it printed."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = cli.main(argv)
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    return code, (json.loads(lines[-1]) if lines else {})


def run(config: dict) -> dict:
    action = config["action"]
    cli = setup_cli()
    if action == "probe":
        return {"available": cli is not None, "reason": None if cli is not None else UNAVAILABLE}
    if cli is None:
        raise RuntimeError(UNAVAILABLE)
    if action == "generators":
        argv = ["generators", "--json"] + (["--external", config["external"]] if config.get("external") else [])
        return _json_call(cli, argv)[1]
    if action in ("check", "describe"):
        code, out = _json_call(cli, [action, config["setup"], "--json"])
        if code == 2:
            raise RuntimeError(f"cuflynx-rom setup {action} failed on {config['setup']}")
        return out
    if action in ("runs", "fit"):
        argv = [action, config["setup"], "--root", config["root"]]
        if config.get("seed") is not None:
            argv += ["--seed", str(int(config["seed"]))]
        if action == "fit" and config.get("methods"):
            argv += ["--methods", ",".join(config["methods"])]
        code = cli.main(argv)  # progress goes straight to stdout, which the API reads
        if code != 0:
            raise RuntimeError(f"cuflynx-rom setup {action} exited with code {code}")
        results = Path(config["root"]) / "results.json"
        return {"root": config["root"], "results": str(results) if action == "fit" and results.exists() else None}
    raise ValueError(f"unknown action {action!r}")


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(f"{FAIL_MARKER} usage: rom_runner.py <config.json>", flush=True)
        return 2
    config = json.loads(Path(argv[1]).read_text())
    try:
        result = run(config)
    except Exception as exc:  # surfaced to the captured stdout for the UI
        print(f"{FAIL_MARKER} {exc}", flush=True)
        traceback.print_exc()
        return 1
    print(RESULT_MARKER + json.dumps(result), flush=True)
    print(DONE_MARKER, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
