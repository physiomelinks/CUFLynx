"""Tests for the bundled example-study route (issues #91, #180).

The "Start" dialog fetches a bundled example and feeds it through the normal
upload flow, so the backend only needs to serve the bundled resource -- and the
build only needs to actually ship it, which is where #180 went wrong.
"""

from __future__ import annotations

import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import examples
import pytest
from conftest import RESOURCES_DIR

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_example_model_served(client):
    resp = client.get("/api/examples/3compartment")
    assert resp.status_code == 200
    assert len(resp.content) > 0


def test_the_example_is_served_with_no_cache(client):
    """The browser fetches the example and posts the bytes back to the upload
    route, so its cache decides which version is imported. Without this the
    response carried only an etag, which a browser may reuse without asking --
    and an updated example then loads as the old one from a server that is
    serving the new one, with nothing on either side to say so."""
    resp = client.get("/api/examples/3compartment")
    assert "no-cache" in resp.headers.get("cache-control", ""), dict(resp.headers)


def test_unknown_example_is_404(client):
    resp = client.get("/api/examples/does-not-exist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Shipped as an archive, so one click loads the whole study (#180)
# ---------------------------------------------------------------------------
def test_the_example_is_an_archive_carrying_the_whole_study(client):
    """A loose .cellml can only carry a third of an example: no obs_data, no
    params_for_id. The archive is what makes "create -> example" a study."""
    resp = client.get("/api/examples/3compartment")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = {Path(n).name for n in zf.namelist()}
    assert "3compartment_flat.cellml" in names
    assert "3compartment_obs_data.json" in names
    assert "3compartment_params_for_id.csv" in names


def test_the_served_example_loads_through_the_omex_route(client):
    """What the frontend does with it: the same path a dropped archive takes."""
    blob = client.get("/api/examples/3compartment").content
    resp = client.post(
        "/api/omex/upload", files={"file": ("3compartment.omex", blob, "application/zip")}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model_id"]
    assert body["obs_data"]["data_items"]
    assert body["params_for_id"]["params"]


# ---------------------------------------------------------------------------
# Shipped at all -- the actual #180 failure
# ---------------------------------------------------------------------------
def test_every_example_exists_in_resources():
    for filename in examples.EXAMPLE_MODELS.values():
        assert (RESOURCES_DIR / filename).is_file(), f"{filename} missing from resources/"


def test_examples_are_collected_into_the_frozen_bundle():
    """``resources/`` is not bundled wholesale, so each example must be listed --
    and the destination must be where ``runtime_paths.resources_dir()`` looks."""
    entries = examples.example_datas()
    assert len(entries) == len(set(examples.EXAMPLE_MODELS.values()))
    for src, dest in entries:
        assert dest == "resources"
        assert Path(src).is_file()
    assert {Path(src).name for src, _ in entries} == set(examples.EXAMPLE_MODELS.values())


def test_the_route_finds_the_example_in_a_bundle_laid_out_by_the_spec(client, tmp_path, monkeypatch):
    """End of the #180 chain: lay the collected files out the way PyInstaller
    would and serve one, without a Python interpreter's source tree in reach."""
    for src, dest in examples.example_datas():
        (tmp_path / dest).mkdir(parents=True, exist_ok=True)
        shutil.copy(src, tmp_path / dest / Path(src).name)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    resp = client.get("/api/examples/3compartment")
    assert resp.status_code == 200, resp.text
    assert zipfile.is_zipfile(io.BytesIO(resp.content))


def test_a_missing_example_fails_the_build_rather_than_the_user(monkeypatch):
    """#180 shipped an executable whose example file simply was not there. A
    build that cannot find one must stop, not produce that executable."""
    monkeypatch.setitem(examples.EXAMPLE_MODELS, "ghost", "not_a_real_example.omex")
    with pytest.raises(FileNotFoundError):
        examples.example_datas()


def test_the_packaging_spec_collects_the_examples():
    """The regression itself: the spec never mentioned resources/, so the route
    404'd only in the packaged app. Reading the manifest keeps the two in step."""
    spec = (REPO_ROOT / "packaging" / "cuflynx.spec").read_text(encoding="utf-8")
    # A live statement, not a mention in a comment: the point is that the build
    # runs it.
    assert any(
        line.strip() == "datas += examples.example_datas()" for line in spec.splitlines()
    )


# ---------------------------------------------------------------------------
# Still loadable by the CA that is shipped with it
#
# "Create -> 3compartment" is the first thing a new user clicks, and the example
# is a *file*: it does not follow circulatory_autogen's schema when that schema
# changes. CA #466 split an obs_data item's name from its plotting labels and
# made `data_item_name` unique, which turns any example written before it into a
# study that loads with an empty observations tab. These pin the example to the
# vocabulary CA reads today.
# ---------------------------------------------------------------------------
def _example_obs_data(name: str = "3compartment") -> dict | None:
    import examples as ex

    path = RESOURCES_DIR / ex.EXAMPLE_MODELS[name]
    with zipfile.ZipFile(path) as zf:
        members = [n for n in zf.namelist() if n.endswith("_obs_data.json")]
        return json.loads(zf.read(members[0])) if members else None


def _items(doc) -> list:
    if isinstance(doc, list):
        return doc
    return list(doc.get("data_items") or []) + list(doc.get("prediction_items") or [])


def test_the_example_obs_data_uses_the_current_vocabulary():
    """`variable` / `name_for_plotting` are what CA read before #466. An example
    still written in them is rejected by the CA it ships beside -- which is
    exactly how it fails: the model loads, the observations do not."""
    import obs_data as obs_mod

    doc = _example_obs_data()
    assert doc is not None, "the 3compartment example carries no obs_data"
    assert obs_mod.legacy_vocabulary_hint(doc) is None, (
        "the bundled example is written in the pre-#466 obs_data vocabulary; "
        "convert it with `cuflynx-migrate-obs-data`"
    )


def test_the_example_obs_data_names_every_item_once():
    """The consequence of the split, and the thing CA actually refuses: a name
    repeated across the mean/max/min of one trace."""
    names = [it.get("data_item_name") for it in _items(_example_obs_data())]
    assert all(names), "every data_item needs a data_item_name"
    assert len(set(names)) == len(names), f"duplicate data_item_name in the example: {names}"


def test_the_served_example_loads_with_nothing_left_unexplained(client, tmp_path, requires_ca):
    """End to end, the way the Start dialog does it: the whole study lands, and
    the import has nothing to warn about. A future example that goes stale fails
    here rather than in a user's empty observations tab.

    Needs a reachable CA: with none, the import correctly warns that nothing
    checked the obs_data, and "no warnings" is the wrong bar to hold it to."""
    blob = client.get("/api/examples/3compartment").content
    resp = client.post(
        "/api/omex/upload",
        params={"output_dir": str(tmp_path)},
        files={"file": ("3compartment.omex", blob, "application/zip")},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert not body["obs_data"].get("error"), body["obs_data"].get("error")
    assert len(body["obs_data"]["data_items"]) == 6
    assert body["warnings"] == [], body["warnings"]


def test_every_bundled_example_that_carries_observations_loads_them(client, tmp_path):
    """Whatever else ships later: an example either has no obs_data, or has one
    the app can load. Half a study behind a Create button is the failure."""
    import examples as ex

    for name, filename in ex.EXAMPLE_MODELS.items():
        doc = _example_obs_data(name)
        blob = (RESOURCES_DIR / filename).read_bytes()
        resp = client.post(
            "/api/omex/upload",
            params={"output_dir": str(tmp_path)},
            files={"file": (filename, blob, "application/zip")},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if doc is None:
            continue
        assert body["obs_data"] is not None, f"{filename}: obs_data was not loaded"
        assert not body["obs_data"].get("error"), f"{filename}: {body['obs_data'].get('error')}"
