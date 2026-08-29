"""Build an obs_data document from a directory of raw recordings.

This replaces a pair of CLI scripts (``select_datasets.py`` and
``pre_process_data.py`` in the sympathetic_neuron repo) that asked one terminal
question per recording and per stimulus waveform, then turned the answers into
obs_data. The answers become a saved, reloadable ``obs_extraction_config.json``;
the conversion becomes a job with a log; and the decisions become a report.

**Why this is a package rather than a flat module.** Every other module in
``apps/api`` is a flat file, and being the first directory here is a real cost.
It buys one thing deliberately: this subsystem is a candidate to move to its own
repository once it grows, and a directory moves with ``git mv`` while eight
sibling ``obs_extract_*.py`` files move with a rename of every import in the app.

**The coupling contract, so that stays true.** ``obs_extract`` may import:

- ``obs_options``  -- CA's operation registry and kwargs schemas
- ``obs_data``     -- the obs_data validator, so what is built is checked by CA
- ``ca_imports``   -- the one supported route to circulatory_autogen
- ``solver_plots`` -- *path* helpers only, never its pyplot-based figure saving

and nothing else from ``apps/api``. In particular not ``main``, ``engine``,
``calibration``, ``runtime_paths`` or ``settings_store``. Every directory this
package writes to arrives as an argument; it never resolves one for itself.
``tests/test_obs_extract_isolation.py`` enforces this, so a convenient import
added later fails a test rather than quietly welding the package to the app.
"""

from __future__ import annotations

from .discovery import (
    case_name,
    date_from_filename,
    discover,
    group_key,
    split_group_key,
    subprotocol_from_filename,
)
from .errors import ObsExtractError
from .readers import (
    CURRENT,
    SUPPORTED_SUFFIXES,
    VOLTAGE,
    ChannelInfo,
    Recording,
    available_formats,
    open_recording,
    probe,
    resolve_roles,
)

__all__ = [
    "CURRENT",
    "SUPPORTED_SUFFIXES",
    "VOLTAGE",
    "ChannelInfo",
    "ObsExtractError",
    "Recording",
    "available_formats",
    "case_name",
    "date_from_filename",
    "discover",
    "group_key",
    "open_recording",
    "probe",
    "resolve_roles",
    "split_group_key",
    "subprotocol_from_filename",
]
