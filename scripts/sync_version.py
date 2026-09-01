"""Write the version into the two files that cannot read Python.

``apps/api/version.py`` is the source: ``pyproject.toml`` already reads it through
``[tool.hatch.version]``. Two other files carry the same number and cannot --
``apps/web/package.json`` (npm) and ``packaging/version_info.txt`` (a PyInstaller Windows
resource, which is what Windows shows under Properties -> Details).

``tests/test_version.py`` checks the three agree, and it works -- it caught a release that
bumped two of the three. But a check tells you afterwards; this removes the opportunity.
The file it most protects is the resource one, whose own comment records it drifting to
0.1.2 while everything else said 0.1.0.

    python scripts/sync_version.py            # rewrite the two from version.py
    python scripts/sync_version.py --check    # fail if they disagree (for CI)
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
VERSION_PY = ROOT / "apps" / "api" / "version.py"
PACKAGE_JSON = ROOT / "apps" / "web" / "package.json"
VERSION_INFO = ROOT / "packaging" / "version_info.txt"


def source_version() -> str:
    text = VERSION_PY.read_text(encoding="utf-8")
    match = re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise SystemExit(f"no __version__ in {VERSION_PY}")
    return match.group(1)


def _package_json(version: str, text: str) -> str:
    # Rewritten textually rather than via json.dumps: the file is hand-formatted and
    # round-tripping it would reflow every line and bury the one that changed.
    new, n = re.subn(r'("version"\s*:\s*)"[^"]*"', rf'\1"{version}"', text, count=1)
    if n != 1:
        raise SystemExit(f'no "version" field in {PACKAGE_JSON}')
    return new


def _version_info(version: str, text: str) -> str:
    parts = version.split(".")
    if len(parts) != 3:
        raise SystemExit(f"expected a three-part version, got {version!r}")
    major, minor, patch = parts
    quad = f"({major}, {minor}, {patch}, 0)"
    text = re.sub(r"filevers=\(\d+, \d+, \d+, \d+\)", f"filevers={quad}", text, count=1)
    text = re.sub(r"prodvers=\(\d+, \d+, \d+, \d+\)", f"prodvers={quad}", text, count=1)
    for field in ("FileVersion", "ProductVersion"):
        text = re.sub(rf"StringStruct\('{field}', '[^']*'\)",
                      f"StringStruct('{field}', '{version}.0')", text, count=1)
    return text


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report disagreement instead of fixing it")
    args = parser.parse_args(argv)

    version = source_version()
    targets = [(PACKAGE_JSON, _package_json), (VERSION_INFO, _version_info)]

    stale = []
    for path, rewrite in targets:
        current = path.read_text(encoding="utf-8")
        wanted = rewrite(version, current)
        if current == wanted:
            continue
        if args.check:
            stale.append(path)
        else:
            path.write_text(wanted, encoding="utf-8")
            print(f"updated {path.relative_to(ROOT)} -> {version}")

    if stale:
        print(f"these do not carry version {version} from apps/api/version.py:", file=sys.stderr)
        for path in stale:
            print(f"  {path.relative_to(ROOT)}", file=sys.stderr)
        print("run: python scripts/sync_version.py", file=sys.stderr)
        return 1

    # Say something on the no-op path too: a silent script is one nobody trusts they ran.
    if not args.check:
        print(f"version {version} is in all three files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
