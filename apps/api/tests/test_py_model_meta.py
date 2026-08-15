"""Unit tests for the AST-only external_python metadata parser.

The point of the module under test is that uploading a model does *not* run it,
so the first test here is that a file whose import would be fatal still parses.
"""

from pathlib import Path

import pytest

from py_model_meta import PyModelParseError, looks_like_py_filename, parse_py_model

FIXTURE = Path(__file__).resolve().parent / "data" / "heat1d_external_model.py"

VALID = '''
class MyModel:
    parameters = {"heat/k": 1.0, "heat/u_D": 0}
    output_names = ["heat/T_p1", "heat/T_p2"]

    def init_solver(self, config):
        pass


SIM_HELPER = MyModel
'''


def test_parses_the_declared_attributes():
    meta = parse_py_model(VALID)
    assert meta.name == "MyModel"
    assert meta.params == ["heat/k", "heat/u_D"]
    assert meta.odes == []
    assert meta.algebraic == ["heat/T_p1", "heat/T_p2"]
    assert meta.initial_values == {"heat/k": 1.0, "heat/u_D": 0.0}
    assert meta.all_names == ["heat/k", "heat/u_D", "heat/T_p1", "heat/T_p2"]
    # Nothing in the contract declares units, and inventing them would put a
    # wrong label on a plot axis.
    assert meta.units == {}
    assert meta.variable_count == 4


def test_the_repo_fixture_parses():
    """The fixture the integration tests upload has to satisfy the contract too."""
    meta = parse_py_model(FIXTURE.read_bytes())
    assert meta.name == "Heat1D"
    assert "heat/k" in meta.params
    assert meta.algebraic == ["heat/T_p1", "heat/T_p2", "heat/T_p3"]


def test_the_module_is_never_executed():
    """A file that would explode on import still yields its metadata.

    This is the whole reason the parser is written against the AST: a model the
    user was sent must be readable before they have decided to run it.
    """
    source = VALID + "\nraise SystemExit('this file must never be imported')\n"
    assert parse_py_model(source).params == ["heat/k", "heat/u_D"]


def test_missing_sim_helper_is_rejected():
    source = VALID.replace("SIM_HELPER = MyModel", "")
    with pytest.raises(PyModelParseError) as exc:
        parse_py_model(source)
    assert "SIM_HELPER" in str(exc.value)


def test_sim_helper_pointing_at_nothing_is_rejected():
    source = VALID.replace("SIM_HELPER = MyModel", "SIM_HELPER = NotDefinedHere")
    with pytest.raises(PyModelParseError) as exc:
        parse_py_model(source)
    assert "NotDefinedHere" in str(exc.value)


def test_sim_helper_that_is_an_expression_is_rejected():
    source = VALID.replace("SIM_HELPER = MyModel", "SIM_HELPER = make_model()")
    with pytest.raises(PyModelParseError) as exc:
        parse_py_model(source)
    assert "class defined in this file" in str(exc.value)


@pytest.mark.parametrize("attr", ["parameters", "output_names"])
def test_missing_attribute_is_rejected(attr):
    source = "\n".join(line for line in VALID.splitlines() if not line.strip().startswith(attr))
    with pytest.raises(PyModelParseError) as exc:
        parse_py_model(source)
    assert attr in str(exc.value)


@pytest.mark.parametrize(
    "line",
    [
        '    parameters = build_parameters()',
        '    parameters = {"heat/k": SOME_CONSTANT}',
    ],
)
def test_non_literal_parameters_are_rejected(line):
    source = VALID.replace('    parameters = {"heat/k": 1.0, "heat/u_D": 0}', line)
    with pytest.raises(PyModelParseError) as exc:
        parse_py_model(source)
    assert "literal" in str(exc.value)


def test_non_literal_output_names_are_rejected():
    source = VALID.replace('    output_names = ["heat/T_p1", "heat/T_p2"]',
                           '    output_names = list(OUTPUTS)')
    with pytest.raises(PyModelParseError) as exc:
        parse_py_model(source)
    assert "literal" in str(exc.value)


def test_parameters_of_the_wrong_shape_are_rejected():
    source = VALID.replace('    parameters = {"heat/k": 1.0, "heat/u_D": 0}',
                           '    parameters = ["heat/k"]')
    with pytest.raises(PyModelParseError) as exc:
        parse_py_model(source)
    assert "dict" in str(exc.value)


def test_output_names_of_the_wrong_shape_are_rejected():
    source = VALID.replace('    output_names = ["heat/T_p1", "heat/T_p2"]',
                           '    output_names = {"heat/T_p1": 1}')
    with pytest.raises(PyModelParseError) as exc:
        parse_py_model(source)
    assert "list" in str(exc.value)


def test_a_parameter_name_without_a_slash_is_rejected():
    source = VALID.replace('"heat/u_D": 0', '"u_D": 0')
    with pytest.raises(PyModelParseError) as exc:
        parse_py_model(source)
    assert "component/variable" in str(exc.value)


def test_an_output_name_without_a_slash_is_rejected():
    source = VALID.replace('"heat/T_p2"', '"T_p2"')
    with pytest.raises(PyModelParseError) as exc:
        parse_py_model(source)
    assert "component/variable" in str(exc.value)


@pytest.mark.parametrize("default", ['"1.0"', "True", "None"])
def test_a_non_number_default_is_rejected(default):
    """The default seeds a slider, so a string or a bool is not a value it can
    take — bools especially, since ``isinstance(True, int)`` would let one
    through as 1.0 and offer a continuum the model never meant."""
    source = VALID.replace('"heat/k": 1.0', f'"heat/k": {default}')
    with pytest.raises(PyModelParseError) as exc:
        parse_py_model(source)
    assert "number" in str(exc.value)


def test_invalid_python_is_rejected_with_the_line():
    with pytest.raises(PyModelParseError) as exc:
        parse_py_model("class Broken(:\n")
    assert "not valid Python" in str(exc.value)


def test_name_override_wins_over_the_class_name():
    assert parse_py_model(VALID, name="given").name == "given"


def test_looks_like_py_filename():
    assert looks_like_py_filename("model.py")
    assert looks_like_py_filename("MODEL.PY")
    assert not looks_like_py_filename("model.cellml")
    assert not looks_like_py_filename(None)
