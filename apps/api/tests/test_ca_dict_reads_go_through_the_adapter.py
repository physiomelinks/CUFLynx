"""No module may read a circulatory_autogen dict by string literal except ``ca_obs``.

The rule exists because the alternative was tried. ``ca_imports`` carries the same rule for
CA *imports* as a docstring -- "no scattered try/except ImportError" -- and it drifted anyway,
because a comment cannot fail a build.

The cost of it drifting here is specific: CA renamed two ``obs_info`` keys, and the shipped
CUFLynx binary raised ``KeyError`` at users, because the literals were spread across four
modules and nothing fast noticed. One file, one edit per CA change, enforced.
"""
import ast
import pathlib

import pytest

API = pathlib.Path(__file__).resolve().parents[1]

#: The dict variables whose subscripts this rule covers. Named rather than inferred: plenty
#: of other dicts in this app are legitimately read by literal, and a rule that fires on
#: those would be turned off within a week.
CA_DICTS = {"obs_info", "protocol_info", "param_id_info"}

#: ``ca_obs`` is where the literals live. ``obs_data.py`` builds obs_data *files* rather than
#: reading CA's parsed dicts, and its keys are the file vocabulary, not this one.
ALLOWED = {"ca_obs.py"}


def _modules():
    for path in sorted(API.glob("*.py")):
        if path.name in ALLOWED:
            continue
        yield path


def _offending_reads(path):
    """Every ``<ca dict>["literal"]`` in *path*, as (line, source-ish) pairs."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Subscript):
            continue
        if not isinstance(node.slice, ast.Constant) or not isinstance(node.slice.value, str):
            continue
        target = node.value
        # `obs_info["x"]` and `pid.obs_info["x"]` alike.
        name = (target.id if isinstance(target, ast.Name)
                else target.attr if isinstance(target, ast.Attribute) else None)
        if name in CA_DICTS:
            found.append((node.lineno, f'{name}["{node.slice.value}"]'))
    return found


@pytest.mark.parametrize("path", list(_modules()), ids=lambda p: p.name)
def test_no_module_reads_a_ca_dict_by_literal(path):
    offenders = _offending_reads(path)
    assert not offenders, (
        f"{path.name} reads circulatory_autogen dicts by string literal:\n"
        + "\n".join(f"  line {line}: {src}" for line, src in offenders)
        + "\n\nUse apps/api/ca_obs.py instead, adding a function for the field if it has "
          "none. Every literal outside that file is a place a CA rename has to be found by "
          "hand -- which is how the shipped app came to raise KeyError at users.")
