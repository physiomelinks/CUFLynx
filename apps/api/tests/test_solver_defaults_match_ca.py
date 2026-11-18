"""CUFLynx's default solver settings are the notebook route's.

A user who sets no solver option gets ``solver_options.default_solver_info`` in CUFLynx, and
``get_default_inp_data_dict`` (or ``get_solver_info_default``) in CA's notebook route. The two
routes give the same results only when those agree (``test_notebook_equivalence.py``), so the
settings that change a CVODE_myokit run -- rtol, atol and MaximumStep -- must be equal.

CUFLynx reads its defaults from CA's ``SOLVER_INFO_FIELDS``. Before CA fix/route-consistency,
``get_solver_info_default`` stated its own (rtol 1e-8 against the fields' 1e-6), so the test
is skipped on a CA whose two statements of the CVODE_myokit defaults disagree.
"""

import pytest

KEYS = ("rtol", "atol", "MaximumStep")


@pytest.fixture
def ca_defaults(monkeypatch, tmp_path):
    from ca_imports import ca_from, ca_import, ensure_ca_path

    ensure_ca_path()
    try:
        parsers = ca_import("parsers.PrimitiveParsers")
        get_default_inp_data_dict = ca_from("utilities.utility_funcs", "get_default_inp_data_dict")
    except ImportError:
        pytest.skip("circulatory_autogen not importable")
    fields = {f["name"]: f.get("default")
              for f in parsers.SOLVER_INFO_FIELDS.get("CVODE_myokit", [])}
    stated = parsers.get_solver_info_default("cellml")
    if any(stated.get(k) != fields.get(k) for k in KEYS):
        pytest.skip(
            "this libcuflynx predates circulatory_autogen fix/route-consistency: its "
            f"get_solver_info_default states CVODE_myokit defaults of its own ({stated}) "
            f"instead of SOLVER_INFO_FIELDS', which CUFLynx reads ({fields})")
    # get_default_inp_data_dict makes libcuflynx's default directories.
    monkeypatch.setenv("CUFLYNX_USER_DIR", str(tmp_path / "user_dir"))
    inp = get_default_inp_data_dict("model", "model_parameters.json", str(tmp_path))
    return {
        "get_solver_info_default": parsers.get_solver_info_default("cellml"),
        "get_default_inp_data_dict": inp["solver_info"],
    }


def test_cuflynx_cvode_myokit_defaults_are_the_notebook_routes(ca_defaults):
    from solver_options import default_solver_info

    cuflynx = default_solver_info("CVODE_myokit")
    for source, ca in ca_defaults.items():
        assert ca.get("solver", "CVODE_myokit") == "CVODE_myokit", (source, ca)
        assert {k: cuflynx.get(k) for k in KEYS} == {k: ca.get(k) for k in KEYS}, (
            f"CUFLynx's default solver_info {cuflynx} differs from CA's {source} {ca}")
