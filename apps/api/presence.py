"""Which browser tabs still have CUFLynx open -- so the shell knows when to quit.

The desktop shell normally owns a native window and exits when it closes. When no
window can be opened (a Linux build without GTK/Qt bindings) it falls back to the
system browser, and then nothing tells it the user has gone: closing a tab ends no
process. Launched from a file manager there is no terminal to Ctrl+C either, so
the server -- and PyInstaller's unpacked ``/tmp/_MEI*`` directory, which is only
removed when the process exits -- lived until logout.

Each tab beats ``/api/presence/beat`` with an id of its own and sends
``/api/presence/leave`` from ``pagehide``. Ids rather than one shared timestamp,
because a goodbye from one tab must not end a session another tab is still using.

Two timings, both deliberately generous:

* ``stale_after`` -- a tab that stops beating without saying goodbye (a crashed
  browser, a killed process) is presumed gone after this long. It has to outlast
  Chrome's *intensive throttling*, which limits a hidden tab's timers to one run a
  minute, or a tab left in the background would be counted as closed.
* ``grace`` -- once the last tab has gone, how long to wait before quitting, so a
  reload (``pagehide`` then a fresh beat from the new page) is not mistaken for a
  close.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

STALE_AFTER_S = 150.0
GRACE_S = 15.0
# Before any tab has ever arrived: the browser may be slow to start, or the user
# may open the printed URL by hand. Long, because quitting under someone who is
# still on their way costs more than a few idle minutes.
FIRST_CONTACT_S = 300.0


class Presence:
    def __init__(
        self,
        stale_after: float = STALE_AFTER_S,
        grace: float = GRACE_S,
        first_contact: float = FIRST_CONTACT_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.stale_after = stale_after
        self.grace = grace
        self.first_contact = first_contact
        self._clock = clock
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}
        self._started = clock()
        # When the last tab was last known to be there; None until one arrives.
        self._last_present: float | None = None

    def beat(self, client_id: str) -> None:
        with self._lock:
            now = self._clock()
            self._seen[client_id] = now
            self._last_present = now

    def leave(self, client_id: str) -> None:
        with self._lock:
            if self._seen.pop(client_id, None) is not None:
                self._last_present = self._clock()

    def _live(self, now: float) -> int:
        return sum(1 for t in self._seen.values() if now - t <= self.stale_after)

    def abandoned(self) -> bool:
        """True once every tab has gone (or none ever came) for long enough."""
        with self._lock:
            now = self._clock()
            if self._last_present is None:
                return now - self._started > self.first_contact
            if self._live(now):
                return False
            # Gone since the later of the last goodbye and the moment the last
            # silent tab went stale.
            gone_since = self._last_present
            if self._seen:
                gone_since = max(gone_since, max(self._seen.values()) + self.stale_after)
            return now - gone_since > self.grace


# The one the API routes feed and the desktop shell reads.
presence = Presence()
