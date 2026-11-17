"""Every analysis runner the API launches ships into the packaged app's ``runners/`` folder.

``runtime_paths.runner_path(name)`` resolves to ``<bundle>/runners/<name>`` when frozen, so a runner missing from
``packaging/cuflynx.spec``'s list works from source and fails only in the packaged app.
"""

import re
from pathlib import Path

API = Path(__file__).resolve().parents[1]
SPEC = API.parents[1] / "packaging" / "cuflynx.spec"


def test_every_runner_path_is_shipped():
    launched = set()
    for py in API.glob("*.py"):
        launched |= set(re.findall(r'runner_path\("([A-Za-z0-9_]+\.py)"\)', py.read_text(encoding="utf-8")))
    assert {"emulator_runner.py", "rom_runner.py"} <= launched
    spec = SPEC.read_text(encoding="utf-8")
    missing = sorted(name for name in launched if f'"{name}"' not in spec)
    assert not missing, f"runners launched by the API but not shipped in {SPEC.name}: {missing}"
