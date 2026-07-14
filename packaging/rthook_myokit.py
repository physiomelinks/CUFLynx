"""PyInstaller runtime hook: point Myokit at its bundled C templates.

Myokit derives its data directories at import time from the location of its own
source file::

    DIR_MYOKIT = os.path.abspath(os.path.dirname(inspect.getfile(frame)))
    DIR_CFUNC  = os.path.join(DIR_MYOKIT, '_sim')    # cmodel.h, cvodessim.c, ...

Inside a PyInstaller bundle ``inspect.getfile`` yields a *relative* path, so
``abspath`` resolves it against the **current working directory** rather than the
unpacked bundle. Myokit then looks for ``cmodel.h`` next to wherever the user
happened to launch the app, and every simulation dies with::

    FileNotFoundError: .../myokit/_sim/cmodel.h

The template files *are* in the bundle (the spec's ``collect_all('myokit')`` puts
them there) — only the lookup path is wrong. ``DIR_CFUNC`` / ``DIR_DATA`` are read
at call time (myokit/_sim/cmodel.py, cvodessim.py), not captured at import, so
rewriting them here fixes every later simulation.

Runs before the entry script; importing myokit here is what lets us patch the
already-computed constants.
"""

import os
import sys

if hasattr(sys, "_MEIPASS"):
    try:
        import myokit
    except ImportError:  # myokit not bundled — nothing to fix
        pass
    else:
        _base = os.path.join(sys._MEIPASS, "myokit")
        myokit.DIR_MYOKIT = _base
        myokit.DIR_CFUNC = os.path.join(_base, "_sim")  # C templates + headers
        myokit.DIR_DATA = os.path.join(_base, "_bin")

        # Sundials (CVODE) is bundled too, so the user doesn't have to install it.
        # Like DIR_CFUNC these are read at call time (myokit/_sim/cvodessim.py),
        # so pointing them at the bundled copies is enough. Without this Myokit
        # would look on the host and fail to compile on a machine without Sundials.
        _sundials = os.path.join(sys._MEIPASS, "sundials")
        if os.path.isdir(_sundials):
            myokit.SUNDIALS_INC = [os.path.join(_sundials, "include")]
            myokit.SUNDIALS_LIB = [os.path.join(_sundials, "lib")]
