#!/usr/bin/env python3
"""Fill an obs_data.json's ``protocol_info`` from a Myokit .mmt's ``[[protocol]]``.

CUFLynx imports the model out of a .mmt and leaves the protocol behind, because
the protocol belongs in obs_data rather than in the CellML (see
``apps/api/myokit_import.py``). This script carries it the rest of the way, so
the pacing a user already wrote in Myokit does not have to be retyped as
sim_times -- a transcription that is tedious to do and silent to get wrong.

    scripts/mmt_to_obs_data.py resources/br-1977.mmt
    scripts/mmt_to_obs_data.py model.mmt --beats 5 -o study_obs_data.json
    scripts/mmt_to_obs_data.py model.mmt --stdout

With no -o, it writes ``<model>_obs_data.json`` beside the .mmt. If that file
already exists it is *updated*: only protocol_info is replaced, so data_items
and everything else you have written survive.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from mmt_protocol import (  # noqa: E402 - after the sys.path line, by necessity
    DEFAULT_BEATS,
    MmtProtocolError,
    fill_protocol_info,
    protocol_info_from_mmt,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__.split("\n", 1)[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("mmt", type=Path, help="the Myokit .mmt file to read the protocol from")
    p.add_argument(
        "-o",
        "--out",
        type=Path,
        default=None,
        help="obs_data.json to write or update (default: <model>_obs_data.json beside the .mmt)",
    )
    p.add_argument(
        "--beats",
        type=int,
        default=DEFAULT_BEATS,
        help=f"how many periods of an indefinitely-repeating protocol to run (default {DEFAULT_BEATS})",
    )
    p.add_argument(
        "--duration",
        type=float,
        default=None,
        help="total simulated time, overriding --beats",
    )
    p.add_argument(
        "--pre-time",
        type=float,
        default=0.0,
        help="unlogged settling time before the experiment (default 0)",
    )
    p.add_argument("--label", default=None, help="experiment label (default is derived)")
    p.add_argument(
        "--stdout",
        action="store_true",
        help="print the protocol_info instead of writing a file",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        data = args.mmt.read_bytes()
    except OSError as exc:
        print(f"could not read {args.mmt}: {exc}", file=sys.stderr)
        return 2

    try:
        info, notes = protocol_info_from_mmt(
            data,
            filename=args.mmt.name,
            beats=args.beats,
            duration=args.duration,
            pre_time=args.pre_time,
            label=args.label,
        )
    except MmtProtocolError as exc:
        print(f"{args.mmt.name}: {exc}", file=sys.stderr)
        return 1

    for note in notes:
        print(f"note: {note}", file=sys.stderr)

    if args.stdout:
        print(json.dumps(info, indent=4))
        return 0

    out = args.out or args.mmt.with_name(f"{args.mmt.stem}_obs_data.json")
    existing: dict = {}
    if out.exists():
        try:
            existing = json.loads(out.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            # Refuse rather than overwrite: the file may hold hand-written
            # data_items that are not reproducible from anything here.
            print(f"{out} exists but could not be read ({exc}); not overwriting.", file=sys.stderr)
            return 2

    document = fill_protocol_info(existing, info)
    try:
        out.write_text(json.dumps(document, indent=4) + "\n")
    except OSError as exc:
        print(f"could not write {out}: {exc}", file=sys.stderr)
        return 2

    what = "updated protocol_info in" if existing else "wrote"
    n_sub = len(info["sim_times"][0])
    param = next(iter(info["params_to_change"]))
    print(f"{what} {out} ({n_sub} sub-experiments driving {param})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
