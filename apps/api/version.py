"""The application version — the one place it is written down.

v0.1.7 shipped calling itself 0.1.0, because the number lived in four
independent files and a release bumped none of them. Three (pyproject, the
FastAPI app, the Windows resource) are now derived from or checked against this
string, and ``package.json`` -- which cannot import Python -- is held to it by
``tests/test_version.py``. Bump this, run the tests, and anything left behind
says so.
"""

__version__ = "0.3.0"
