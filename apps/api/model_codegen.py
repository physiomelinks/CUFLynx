"""On-demand CellML -> Python code generation for the non-cellml backends.

``generated_model_format`` (CA ``model_type``) ``python`` and ``casadi_python``
need a generated ``.py`` module, not the raw CellML — ``get_simulation_helper``
rejects a non-``.py`` ``model_path`` for those solvers. We generate it lazily the
first time a run needs it (per the chosen format) using CA's ``PythonGenerator``
and cache the result, so leaving the format at ``cellml_only`` costs nothing.

``casadi_python`` needs the CasADi-compatible transform (ternaries/division
rewritten for symbolic execution), so python and casadi_python get distinct
generated modules.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from engine import _ensure_ca_on_path

# Generated modules live next to the uploads, keyed by model + format so the two
# python backends don't clobber each other.
GEN_DIR = Path(tempfile.gettempdir()) / "cuflynx_generated"

# (abspath(cellml), casadi_compat) -> generated .py path.
_cache: dict[tuple[str, bool], str] = {}


def reset_cache() -> None:
    """Forget generated-model paths (call when the CA directory changes)."""
    _cache.clear()


def generate_python_model(cellml_path: str, *, casadi_compat: bool, module_name: str) -> str:
    """Generate ``<module_name>.py`` from a CellML file and return its path.

    Cached by ``(cellml_path, casadi_compat)``; a cached path whose file still
    exists is returned without regenerating.
    """
    key = (str(Path(cellml_path).resolve()), bool(casadi_compat))
    cached = _cache.get(key)
    if cached and Path(cached).is_file():
        return cached

    _ensure_ca_on_path()
    from generators.PythonGenerator import PythonGenerator  # noqa: E402

    GEN_DIR.mkdir(parents=True, exist_ok=True)
    gen = PythonGenerator(
        str(cellml_path),
        output_dir=str(GEN_DIR),
        module_name=module_name,
        casadi_compat=bool(casadi_compat),
    )
    py_path = gen.generate()
    _cache[key] = py_path
    return py_path


def resolve_model_path(cellml_path: str, model_type: str, *, model_id: str | None = None) -> str:
    """Path to feed CA for this format: the CellML for ``cellml_only``, else the
    generated ``.py`` (generating + caching on first use)."""
    if model_type in (None, "", "cellml_only"):
        return str(cellml_path)
    casadi = model_type == "casadi_python"
    stem = model_id or Path(cellml_path).stem
    module_name = f"gen_{stem}_{'casadi' if casadi else 'py'}"
    return generate_python_model(cellml_path, casadi_compat=casadi, module_name=module_name)
