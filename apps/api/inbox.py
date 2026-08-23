"""Where a study delivered by PhLynx waits for the user to accept it (#287).

PhLynx builds a COMBINE archive and needs to hand it to a *running* CUFLynx. It
does that by posting to ``/api/inbox`` on localhost, having found us by probing
:data:`RECEIVE_PORTS` and checking ``/api/health`` says ``app == "CUFLynx"``.

Two things this module exists to keep straight:

* **The port range is a contract.** PhLynx probes exactly these ports, so the
  range is written down once and imported by both the API and the desktop shell
  rather than being repeated in a comment somewhere.
* **A delivery is not an import.** The bytes are staged and the user is asked.
  CORS stops a page *reading* our responses; it does not stop it *sending*, so
  anything that can run JavaScript in the user's browser can deliver a study. The
  confirmation in the UI is the security control, and staging is what makes it
  possible -- an endpoint that imported on arrival would have nothing left to ask
  about.

Deliberately free of FastAPI imports: it is a store and two constants, and the
desktop shell reads the constants without wanting the app.
"""

from __future__ import annotations

import io
import threading
import zipfile
from dataclasses import dataclass, field

#: What ``/api/health`` calls itself. PhLynx keys off this to be sure the thing
#: answering on 8787 is CUFLynx and not some other local service.
APP_NAME = "CUFLynx"

#: The first port the desktop shell tries, and the first PhLynx probes.
PREFERRED_PORT = 8787

#: The whole range, in order. Short on purpose: every entry is a port PhLynx must
#: probe before giving up, and a long walk makes "CUFLynx is not running" slow to
#: discover.
RECEIVE_PORTS = tuple(range(PREFERRED_PORT, PREFERRED_PORT + 5))


@dataclass
class PendingStudy:
    """One delivered archive, awaiting the user's decision."""

    data: bytes
    origin: str
    filename: str
    members: list[str] = field(default_factory=list)

    def summary(self) -> dict:
        """What the confirmation dialog shows -- never the archive itself.

        The user is being asked to trust a payload from a page; naming its origin,
        its size and what is inside it is the whole basis on which they can.
        """
        return {
            "origin": self.origin,
            "filename": self.filename,
            "bytes": len(self.data),
            "members": list(self.members),
        }


class Inbox:
    """A one-slot store for the study most recently delivered.

    One slot, not a queue: two archives arriving before the user looks means the
    second is the one they want, and a queue would make them dismiss a stale study
    before seeing it. Locked because the deliverer is a request thread and the
    reader is the polling UI.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: PendingStudy | None = None

    def deliver(self, data: bytes, origin: str, filename: str) -> dict:
        members = _member_names(data)
        with self._lock:
            self._pending = PendingStudy(data, origin, filename, members)
            return self._pending.summary()

    def peek(self) -> dict | None:
        with self._lock:
            return self._pending.summary() if self._pending else None

    def take(self) -> PendingStudy | None:
        """Remove and return the pending study -- the accept path."""
        with self._lock:
            pending, self._pending = self._pending, None
            return pending

    def clear(self) -> bool:
        """Discard without importing -- the reject path. True if there was one."""
        with self._lock:
            had = self._pending is not None
            self._pending = None
            return had


def _member_names(data: bytes) -> list[str]:
    """The archive's member names, for the dialog.

    Best-effort: an unreadable zip still gets staged so the *route* can reject it
    with a real message. Listing names is not the place to decide an archive is
    invalid.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return [n for n in zf.namelist() if not n.endswith("/")]
    except (zipfile.BadZipFile, OSError):
        return []


#: Process-wide, like the model registry beside it: one desktop app, one user.
inbox = Inbox()
