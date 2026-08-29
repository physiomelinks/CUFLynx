"""The one error class this package raises for anything the user can fix.

Kept local, and kept a ``ValueError``, for the same two reasons
``myokit_import.MyokitImportError`` is: the CA directory can be re-pointed at
runtime, so a class imported from CA would change identity mid-session and
``except ObsExtractError`` at the call sites would quietly stop matching; and a
``ValueError`` is what the rest of ``apps/api`` already maps to HTTP 422.

The distinction this package draws is between *a file or a setting the user
chose* -- an unreadable recording, an expression with a function call in it, a
``.npy`` with no sample rate -- which is an ``ObsExtractError`` and a 422, and a
fault in the code, which is left to propagate as whatever it is. A scan must
never turn a single unreadable file into a failed request, so discovery catches
this per file and reports ``readable: false`` instead.
"""

from __future__ import annotations


class ObsExtractError(ValueError):
    """A recording, config, or expression the user can correct (HTTP 422)."""
