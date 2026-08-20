"""Fail a CI job that reported success having run (almost) nothing.

Every test in CUFLynx's integration tier sits behind a fixture that skips when its
dependencies are missing -- ``requires_simulation`` (myokit/libcellml/CA),
``requires_casadi``, ``requires_params_csv``, and the ``libcuflynx is installed``
guard in ``test_libcuflynx_package.py``. That is correct locally: a contributor
without Myokit should get a fast green run, not a wall of red.

In CI it is a trap. If an install step half-fails -- a resolver picking a wheel that
does not exist for this Python, an extra that silently resolved to nothing -- every
guarded test skips, pytest exits 0, and the job that exists to run the integration
tier passes having run none of it. **A skipped test is a green job**, and that is the
likely failure here rather than a red X.

Unlike circulatory_autogen's equivalent (`fail_if_tests_skipped.py`), this does not
demand zero skips: some skips in this tier are legitimate on a given runner (no casadi
in the light matrix leg, say). It asserts a *floor* on how many tests actually ran, so
the job fails when the tier collapses rather than when one optional backend is absent.

Usage::

    python .github/scripts/assert_tests_ran.py --min-ran 25 junit-integration.xml

The floor is deliberately a number someone has to update when the tier shrinks -- if a
legitimate change drops the count, seeing this fail is the prompt to ask whether the
coverage really should have gone.
"""
from __future__ import annotations

import argparse
import glob
import sys
import xml.etree.ElementTree as ET


def _suites(root):
    return [root] if root.tag == "testsuite" else root.findall("testsuite")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("patterns", nargs="+", help="junit xml files (globs allowed)")
    parser.add_argument(
        "--min-ran", type=int, default=1,
        help="fail unless at least this many tests actually executed (default: 1)")
    args = parser.parse_args(argv)

    total = skipped = failed = 0
    seen_any = False

    # Globs are expanded here rather than by the shell so a pattern matching nothing is
    # reported as "no results" instead of being passed through literally and read as a
    # missing file -- which looks like a different problem entirely.
    for pattern in args.patterns:
        for path in sorted(glob.glob(pattern)):
            seen_any = True
            root = ET.parse(path).getroot()
            for suite in _suites(root):
                t = int(suite.get("tests", 0))
                s = int(suite.get("skipped", 0))
                f = int(suite.get("failures", 0)) + int(suite.get("errors", 0))
                total += t
                skipped += s
                failed += f
                print(f"{path}: {t} collected, {s} skipped, {t - s} ran, {f} failed")

    if not seen_any:
        print(
            f"error: no junit files matched {args.patterns}. The test step did not "
            f"produce results, so nothing here can vouch for it.", file=sys.stderr)
        return 1

    ran = total - skipped
    print(f"\ntotal: {total} collected, {skipped} skipped, {ran} ran, {failed} failed")

    if ran < args.min_ran:
        print(
            f"\nerror: only {ran} test(s) actually ran, expected at least "
            f"{args.min_ran}. {skipped} were skipped -- the usual cause is an install "
            f"step that half-succeeded, leaving every dependency-guarded test to skip "
            f"while pytest still exits 0. Check the install log before lowering this "
            f"floor.", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
