"""The committed EasyML fixtures: where they came from, and that they still match.

The `.model` files under `resources/models/third_party/` are *derived* — Myokit's
EasyML exporter run over the `.mmt` files beside them. That is a stronger claim
on someone else's work than the unmodified copies the rest of that directory
holds, so the two rules the README states are checked here rather than trusted:
no derived file comes from a source carrying a licence notice, and every derived
file still matches its source.

The load/convert/simulate coverage of these fixtures is in
``test_easyml_import.py``, which sweeps whatever ``.model`` files are present.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pytest

from conftest import RESOURCES_DIR, all_easyml_fixtures

THIRD_PARTY = RESOURCES_DIR / "models" / "third_party"
README = THIRD_PARTY / "README.md"

#: CUFLynx's own EasyML example, which is not from the third-party set and is
#: covered by CUFLynx's licence.
OURS = "hodgkin_huxley_1952.model"


def _derived():
    return sorted(THIRD_PARTY.glob("*.model"))


def _readme_rows():
    """``(file, source)`` for every row of the derived-exports table."""
    rows = {}
    for line in README.read_text().splitlines():
        match = re.match(r"\|\s*`([\w.-]+\.model)`\s*\|\s*`([\w.-]+\.mmt)`", line)
        if match:
            rows[match.group(1)] = match.group(2)
    return rows


def _licence_noticed_stems():
    """Stems of every `.mmt` the README marks with a licence notice.

    Read from the table rather than listed here: the table is what a person
    updates when a fixture is added, and a second list would be the thing that
    goes stale.
    """
    stems = set()
    for line in README.read_text().splitlines():
        if "GNU GPL" not in line:
            continue
        for name in re.findall(r"`([\w.*-]+)\.mmt`", line):
            stems.add(name.rstrip("*"))
    return stems


# ---------------------------------------------------------------------------
# The rules the README states
# ---------------------------------------------------------------------------
def test_there_are_derived_fixtures_to_check():
    """Guards the sweeps below: an empty directory would make them vacuous."""
    assert _derived(), "no .model fixtures found; the checks below prove nothing"


def test_the_licence_notices_were_actually_found():
    """Likewise for the exclusion rule -- an empty set would excuse everything."""
    assert _licence_noticed_stems(), "no GPL rows parsed out of the README table"


@pytest.mark.parametrize("path", _derived(), ids=lambda p: p.name)
def test_no_derived_model_comes_from_a_licence_noticed_source(path):
    """A derivative of a GPL-noticed file is a different proposition from an
    unmodified copy of one aggregated beside an Apache-2.0 project."""
    noticed = _licence_noticed_stems()
    source = _readme_rows().get(path.name, path.name)
    stem = Path(source).stem
    offending = [n for n in noticed if stem == n or stem.startswith(n)]
    assert not offending, (
        f"{path.name} derives from {source}, which the README marks with a "
        f"licence notice ({', '.join(offending)})"
    )


@pytest.mark.parametrize("path", _derived(), ids=lambda p: p.name)
def test_every_derived_model_is_documented(path):
    rows = _readme_rows()
    assert path.name in rows, (
        f"{path.name} has no row in the derived-exports table in {README.name}; "
        f"without one there is nothing recording whose work it is"
    )
    assert (THIRD_PARTY / rows[path.name]).is_file(), (
        f"{path.name} names {rows[path.name]} as its source, which is not here"
    )


@pytest.mark.integration
@pytest.mark.parametrize("path", _derived(), ids=lambda p: p.name)
def test_every_derived_model_still_matches_its_source(path, requires_easyml):
    """Re-export the source and compare, so a stale fixture fails rather than
    drifting quietly after a Myokit upgrade.

    Compared as models, not as text, because **the exporter's output order is not
    deterministic**: ``guess.membrane_currents()`` hands back the currents in
    varying order, so two exports of one model differ in the order of the terms
    of ``Iion`` and of the ``.trace()`` group. Addition is commutative and a
    group is a set, so those are the same model -- but no byte comparison could
    ever pass, and one written anyway would have been quarantined as flaky
    rather than read as the finding it is.

    What is compared instead is what a stale fixture would actually move: the
    states, their initial values, the parameters and the methods.
    """
    myokit = pytest.importorskip("myokit")
    from myokit.formats.easyml import EasyMLExporter

    import easyml_import
    from cellml_meta import parse_cellml

    source = THIRD_PARTY / _readme_rows()[path.name]
    with tempfile.TemporaryDirectory() as td:
        fresh_path = Path(td) / path.name
        EasyMLExporter().model(str(fresh_path), myokit.load_model(str(source)))
        fresh = easyml_import.import_easyml(
            fresh_path.read_bytes(), filename=path.name)
    committed = easyml_import.import_easyml(path.read_bytes(), filename=path.name)

    stale = (
        f"{path.name} no longer matches an export of {source.name}. Regenerate "
        f"it -- see the README -- or say why it should differ."
    )
    assert committed["model_name"] == fresh["model_name"], stale
    assert committed["parameters"] == fresh["parameters"], stale
    assert committed["methods"] == fresh["methods"], stale
    assert sorted(committed["traces"]) == sorted(fresh["traces"]), stale

    got, want = parse_cellml(committed["cellml"]), parse_cellml(fresh["cellml"])
    assert sorted(got.odes) == sorted(want.odes), stale
    assert sorted(got.params) == sorted(want.params), stale
    assert got.initial_values == want.initial_values, stale


# ---------------------------------------------------------------------------
# Ours is not theirs
# ---------------------------------------------------------------------------
def test_our_own_example_is_not_in_the_third_party_directory():
    """It carries CUFLynx's licence, and filing it beside files that do not
    would be the easiest possible way to lose that distinction."""
    assert (RESOURCES_DIR / OURS).is_file()
    assert not (THIRD_PARTY / OURS).exists()


def test_the_sweep_sees_both_ours_and_theirs():
    """The import tests parametrise over all of them; this is what makes that
    one sweep rather than two."""
    names = {p.name for p in all_easyml_fixtures()}
    assert OURS in names
    assert names >= {p.name for p in _derived()}
