"""The working dir: one folder holding every input and output of a study.

The user picks it in the app ("Working dir"). Runs write their outputs into it, and
every input a study uses is stored under ``<W>/inputs/<kind>/`` when it is loaded,
edited or first used, so the folder can be zipped, moved or sent and opened again from
its study manifest. The layout is libcuflynx's (``utilities.cuflynx_study``): this module
stores through :func:`store` and finds through :func:`latest`, both libcuflynx's, so
CUFLynx and libcuflynx scripts write one layout.

    <W>/
      <study>_cuflynx_study.json     study manifest, paths relative to <W>
      inputs/
        index.json                   every stored version: kind, file, folder, content
                                     hash, when first used / last stored, runs using it
        <kind>/<yyyy-mm-dd_HHMMSS>/  one version of an input, by kind:
          model                      <prefix>.cellml as run, source .mmt/.model/.omex,
                                     a dropped multi-file CellML as given
          obs_data                   <prefix>_obs_data.json, edited copies, uq_obs_data.json
          params_for_id              <prefix>_params_for_id.json, edited copies
          user_inputs                user_inputs.yaml as given
          funcs                      operation/cost/modifier_funcs_user.py, user_model.py
          variable_mapping           <prefix>_variable_mapping.csv
          obs_extraction             obs_extraction_config.json
        workflows/<wf>/<stamp>/      calibration_workflow.json and its packed inputs
        generated_models/<prefix>/<stamp>/
                                     Myokit/EasyML conversions, PhLynx module_config
      <method>_<prefix>_<obs>/       calibration / UQ run dirs (CA)
      sensitivity/                   Sobol and local sensitivity results
      emulators/, workflows/<wf>/    emulator bundles, workflow run outputs
      saved_params/                  saved manual parameter sets
      export_<yymmdd>/               exported script bundles

Every version lives in a folder dated when it was first used, and saving an input
never rewrites one: a new content gets a new folder, a content stored before reuses
its folder. So no file looks current by sitting under a plain name. Which version is
current is said by the study's record (the paths a run is given and the manifest
records) or, for a file the app keeps per working dir (funcs, the variable mapping),
by :func:`latest`: the version stored last.

A folder written before this layout keeps opening: :func:`readable` falls back to the
old place of each file (``user_funcs/``, ``generated_models/`` and the top level).
"""

from __future__ import annotations

import os
from pathlib import Path

from ca_imports import CaImportError, ca_from

INPUTS = "inputs"

#: Kinds of input, each a folder of ``inputs/``. ``workflows`` and ``generated_models``
#: take a subfolder (``workflows/<wf>``).
KINDS = ("model", "obs_data", "params_for_id", "user_inputs", "funcs", "system", "emulator",
         "workflows", "generated_models", "variable_mapping", "obs_extraction")

#: Where each kind lived in a folder written before the dated layout, relative to the
#: working dir. ``""`` is the working dir itself.
LEGACY = {
    "funcs": "user_funcs",
    "generated_models": "generated_models",
    "obs_data": "",
    "params_for_id": "",
    "variable_mapping": "",
    "obs_extraction": "",
}

SENSITIVITY = "sensitivity"
SAVED_PARAMS = "saved_params"

#: What a run says when there is no working dir to run in.
REQUIRED_MESSAGE = (
    "Set a working dir before running: every input and output of the study is kept "
    "there, so it can be reopened, zipped and sent. Pick one under Working dir in the "
    "import panel.")

NEEDS_CA = (
    "Keeping a study's inputs in its working dir needs a circulatory_autogen (libcuflynx) "
    "with utilities.cuflynx_study.store_input (physiomelinks/circulatory_autogen#619). "
    "Point Settings -> CA dir at one, or upgrade libcuflynx.")


class WorkingDirUnavailable(RuntimeError):
    """The configured CA cannot store a working dir's inputs (:data:`NEEDS_CA`)."""


def _ca(name: str):
    try:
        return ca_from("utilities.cuflynx_study", name)
    except CaImportError as exc:
        raise WorkingDirUnavailable(NEEDS_CA) from exc


def _kind(kind: str) -> str:
    if str(kind).split("/")[0] not in KINDS:
        raise ValueError(f"unknown input kind {kind!r}")
    return kind


def inputs_dir(working_dir: str | os.PathLike, kind: str = "") -> Path:
    """``<W>/inputs/<kind>/`` (``<W>/inputs/`` for ``kind=""``): the folder holding the
    dated versions of ``kind``. Not created."""
    root = Path(working_dir) / INPUTS
    return root / _kind(kind) if kind else root


def is_inside(working_dir: str | os.PathLike, path: str | os.PathLike) -> bool:
    """Whether ``path`` is in the working dir (or is it)."""
    try:
        root = Path(working_dir).resolve()
        target = Path(path).resolve()
    except OSError:
        return False
    return target == root or root in target.parents


def store(working_dir: str | os.PathLike, kind: str, source: str | os.PathLike | bytes,
          name: str | None = None) -> Path:
    """Store ``source`` (a file, a folder or bytes) as an input of ``kind``; its path.

    libcuflynx's ``store_input``: a new content goes into a new
    ``<W>/inputs/<kind>/<yyyy-mm-dd_HHMMSS>/`` folder, a content stored before reuses
    its folder, and either way it becomes the :func:`latest` of its kind and name.
    ``name`` is the file name to store it under (required for bytes; for a folder, the
    file within it the version is of). A source already in the working dir is used where
    it is. Raises :class:`WorkingDirUnavailable` when the configured CA predates it.
    """
    if not isinstance(source, (bytes, bytearray)):
        source = str(source)
    relative = _ca("store_input")(str(working_dir), _kind(kind), source, name)
    return Path(working_dir) / relative


def latest(working_dir: str | os.PathLike, kind: str, name: str | None = None) -> Path | None:
    """The version of ``kind`` (called ``name``) stored last in the working dir, or None:
    libcuflynx's ``latest_input`` over ``inputs/index.json``. None too when the configured
    CA predates it: such a CA cannot have stored one through :func:`store`."""
    _kind(kind)
    try:
        latest_input = _ca("latest_input")
    except WorkingDirUnavailable:
        return None
    found = latest_input(str(working_dir), kind, name)
    return Path(found) if found else None


def stored_version(path: str | os.PathLike) -> tuple[Path, str, Path] | None:
    """``(working dir, kind, folder)`` of the stored version holding ``path``, or None when
    ``path`` is not in one (``inputs/index.json`` of the working dir it is in)."""
    target = Path(path).resolve()
    for parent in target.parents:
        if parent.name == INPUTS and (parent / "index.json").is_file():
            try:
                read_index = _ca("read_index")
            except WorkingDirUnavailable:
                return None
            root = parent.parent
            for version in read_index(str(root)):
                folder = (root / str(version.get("folder") or "")).resolve()
                if version.get("folder") and folder in target.parents:
                    return root, str(version.get("kind")), folder
            return None
    return None


def readable(working_dir: str | os.PathLike, kind: str, name: str) -> Path | None:
    """The file ``name`` of ``kind`` in this working dir: the :func:`latest` stored
    version, else where a folder in the older layout keeps it (see :data:`LEGACY`), else
    None."""
    found = latest(working_dir, kind, name)
    if found is not None:
        return found
    if kind in LEGACY:
        legacy = Path(working_dir) / LEGACY[kind] / name
        if legacy.exists():
            return legacy
    return None


def sensitivity_dir(working_dir: str | os.PathLike) -> Path:
    """``<W>/sensitivity/``: where sensitivity analyses write."""
    return Path(working_dir) / SENSITIVITY


def saved_params_dir(working_dir: str | os.PathLike) -> Path:
    """``<W>/saved_params/``: where saved manual parameter sets go."""
    return Path(working_dir) / SAVED_PARAMS
