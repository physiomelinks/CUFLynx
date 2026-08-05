"""The example studies the "Start" dialog offers, and the files they ship as.

Its own module, deliberately, because two very different places need the same
list: the route that serves an example (``main.get_example_model``) and the
PyInstaller spec that decides what goes into the executable. Issue #180 is what
happens when those two disagree -- the route read from ``resources/``, the spec
never collected it, and the button 404'd with "example model file missing" in the
packaged app while working perfectly from source. Importing the manifest into the
spec makes that class of drift a *build* failure instead of a user-facing one.

Kept free of FastAPI/Myokit imports so the spec can import it during a build.

Examples ship as **COMBINE archives**: an example is a whole study -- model,
obs_data and params_for_id -- and a loose ``.cellml`` can only carry the first
third of it.
"""

from __future__ import annotations

from pathlib import Path

from runtime_paths import resources_dir

# logical name (the URL segment) -> filename under resources/
EXAMPLE_MODELS: dict[str, str] = {
    "3compartment": "3compartment.omex",
}

MEDIA_TYPES = {
    ".omex": "application/zip",
    ".cellml": "application/xml",
    ".xml": "application/xml",
}


def media_type(filename: str) -> str:
    return MEDIA_TYPES.get(Path(filename).suffix.lower(), "application/octet-stream")


def example_datas() -> list[tuple[str, str]]:
    """PyInstaller ``datas`` entries for every bundled example.

    The destination is ``resources``, which is where ``runtime_paths.resources_dir()``
    looks inside the bundle. Raises rather than skipping a missing file: a build
    that quietly omits an example only fails later, in a user's hands.
    """
    entries = []
    for filename in sorted(set(EXAMPLE_MODELS.values())):
        src = resources_dir() / filename
        if not src.is_file():
            raise FileNotFoundError(
                f"example {filename!r} is listed in EXAMPLE_MODELS but missing from "
                f"{resources_dir()}"
            )
        entries.append((str(src), "resources"))
    return entries
