"""The working dir: one folder holding every input and output of a study.

The user picks it in the app ("Working dir"). Runs write their outputs into it, and
every input a study uses is copied under ``<W>/inputs/<kind>/`` when it is loaded or
first used, so the folder can be zipped, moved or sent and opened again from its
study manifest. libcuflynx scripts write the same layout.

    <W>/
      <study>_cuflynx_study.json     study manifest, paths relative to <W>
      inputs/
        model/                       <prefix>.cellml as run, source .mmt/.model/.omex,
                                     source/ (a dropped multi-file CellML as given)
        obs_data/                    <prefix>_obs_data.json, edited copies
        params_for_id/               <prefix>_params_for_id.json, edited copies
        user_inputs/                 user_inputs.yaml as given
        funcs/                       operation/cost/modifier_funcs_user.py, user_model.py
        workflows/<wf>/              calibration_workflow.json and its packed inputs
        generated_models/<prefix>/   Myokit/EasyML conversions, PhLynx module_config
        variable_mapping.csv, obs_extraction_config.json, uq_obs_data.json
      <method>_<prefix>_<obs>/       calibration / UQ run dirs (CA)
      sensitivity/                   Sobol and local sensitivity results
      emulators/, workflows/<wf>/    emulator bundles, workflow run outputs
      saved_params/                  saved manual parameter sets
      export_<yymmdd>/               exported script bundles

A folder written before this layout keeps opening: :func:`readable` falls back to the
old place of each file (``user_funcs/``, ``generated_models/`` and the top level).
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

INPUTS = "inputs"

#: Subfolders of ``inputs/``, one per kind of input.
KINDS = ("model", "obs_data", "params_for_id", "user_inputs", "funcs", "system", "emulator",
         "workflows", "generated_models")

#: Where each kind lived in a folder written before ``inputs/`` existed, relative to
#: the working dir. ``""`` is the working dir itself.
LEGACY = {
    "funcs": "user_funcs",
    "generated_models": "generated_models",
    "obs_data": "",
    "params_for_id": "",
    "": "",
}

SENSITIVITY = "sensitivity"
SAVED_PARAMS = "saved_params"

#: What a run says when there is no working dir to run in.
REQUIRED_MESSAGE = (
    "Set a working dir before running: every input and output of the study is kept "
    "there, so it can be reopened, zipped and sent. Pick one under Working dir in the "
    "import panel.")


def inputs_dir(working_dir: str | os.PathLike, kind: str = "") -> Path:
    """``<W>/inputs/<kind>/`` (``<W>/inputs/`` for ``kind=""``). Not created."""
    if kind and kind not in KINDS:
        raise ValueError(f"unknown input kind {kind!r}")
    root = Path(working_dir) / INPUTS
    return root / kind if kind else root


def input_path(working_dir: str | os.PathLike, kind: str, name: str) -> Path:
    """``<W>/inputs/<kind>/<name>``, taking only the basename of ``name``."""
    return inputs_dir(working_dir, kind) / Path(str(name)).name


def is_inside(working_dir: str | os.PathLike, path: str | os.PathLike) -> bool:
    """Whether ``path`` is in the working dir (or is it)."""
    try:
        root = Path(working_dir).resolve()
        target = Path(path).resolve()
    except OSError:
        return False
    return target == root or root in target.parents


def adopt_bytes(working_dir: str | os.PathLike, kind: str, name: str, data: bytes) -> Path:
    """Write ``data`` as ``<W>/inputs/<kind>/<name>``; the file's path.

    Rewritten only when the content differs, so re-adopting the same input leaves its
    modification time alone.
    """
    target = input_path(working_dir, kind, name)
    if target.is_file() and target.read_bytes() == data:
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def adopt(working_dir: str | os.PathLike, kind: str, source: str | os.PathLike,
          name: str | None = None) -> Path:
    """Copy ``source`` into ``<W>/inputs/<kind>/``; the path the study should use.

    A file already inside the working dir is returned as it is: it already travels
    with the study, wherever in the folder it sits (a folder in the older layout).
    """
    source = Path(source)
    if is_inside(working_dir, source):
        return source
    return adopt_bytes(working_dir, kind, name or source.name, source.read_bytes())


def readable(working_dir: str | os.PathLike, kind: str, name: str) -> Path:
    """The file ``name`` of ``kind`` in this working dir, in either layout.

    ``<W>/inputs/<kind>/<name>`` when it exists or when the older place does not hold
    it; else the older place (see :data:`LEGACY`). Writers use the result too, so a
    file found in the older layout is edited where it is rather than forked.
    """
    current = inputs_dir(working_dir, kind) / name if kind else inputs_dir(working_dir) / name
    if current.exists() or kind not in LEGACY:
        return current
    legacy = Path(working_dir) / LEGACY[kind] / name
    return legacy if legacy.exists() else current


def sensitivity_dir(working_dir: str | os.PathLike) -> Path:
    """``<W>/sensitivity/``: where sensitivity analyses write."""
    return Path(working_dir) / SENSITIVITY


def saved_params_dir(working_dir: str | os.PathLike) -> Path:
    """``<W>/saved_params/``: where saved manual parameter sets go."""
    return Path(working_dir) / SAVED_PARAMS


def copy_tree(working_dir: str | os.PathLike, kind: str, source: str | os.PathLike,
              name: str) -> Path:
    """Copy the directory ``source`` to ``<W>/inputs/<kind>/<name>/``; the copy.

    Replaces an earlier copy of the same name. A ``source`` already inside the
    working dir is returned unchanged.
    """
    source = Path(source)
    if is_inside(working_dir, source):
        return source
    target = inputs_dir(working_dir, kind) / Path(name).name
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(source, target)
    return target
