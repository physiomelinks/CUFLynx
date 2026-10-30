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

The hook has a second job, further down: making the *other* two inputs that
run-time compile needs -- Sundials' headers and CPython's own -- resolve to the
copies in the bundle rather than to wherever the build machine happened to keep
them. Same class of bug, same fix.
"""

import os
import sys
import sysconfig

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

    # CPython's own headers, needed by the same run-time compile. The spec ships
    # them to <bundle>/include/python<X.Y> for exactly this -- but on macOS
    # distutils never looked there, so every packaged Mac shipped with them as
    # dead weight.
    #
    # distutils resolves its include directory from the *build* interpreter's
    # baked-in INCLUDEPY, which on a framework build is the absolute
    #     /Library/Frameworks/Python.framework/Versions/3.10/include/python3.10
    # That path exists on the build machine and on a GitHub runner -- actions/
    # setup-python installs precisely it -- and on almost no user's Mac. So a user
    # who had never installed python.org Python got
    #     fatal error: 'Python.h' file not found
    # from a perfectly healthy Xcode, surfacing as the opaque
    #     DistutilsExecError: command '/usr/bin/clang' failed with exit code 1
    # Linux is unaffected: there the include path is prefix-relative and
    # PyInstaller points the prefix at the bundle, so it already lands on the
    # shipped copy. Confirmed on both macOS architectures by the `no-python-headers`
    # state in .github/workflows/mac-extended.yml, which hides the framework's
    # include dir to imitate an ordinary Mac.
    #
    # Appending to CFLAGS rather than rewriting INCLUDEPY is deliberate. Which
    # knob distutils consults depends on the setuptools version frozen into the
    # bundle -- newer ones prefer INCLUDEPY, older ones join sys.base_prefix --
    # and that version is chosen by the build environment, not by us. The env var
    # is the one input every version honours: customize_compiler *appends* it
    # (cflags + ' ' + os.environ['CFLAGS']), so nothing already there is lost. And
    # a -I naming a directory that does not exist is not an error to any C
    # compiler, so adding ours is sufficient -- the stale one can stay.
    _py_inc = os.path.join(
        sys._MEIPASS, "include", "python" + sysconfig.get_python_version()
    )
    if os.path.isfile(os.path.join(_py_inc, "Python.h")):
        os.environ["CFLAGS"] = (os.environ.get("CFLAGS", "") + " -I" + _py_inc).strip()
