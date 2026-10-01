"""Synthesise recordings in each supported format, so tests need no binaries.

A patch-clamp corpus is gigabytes of proprietary binary. Committing even one real
``.wcp`` would put a few hundred KB of somebody's experiment in the repository
and pin the tests to that one file's quirks. Writing the formats instead keeps
the fixtures small, legible and parameterisable -- a test that needs three sweeps
of a known ramp asks for exactly that.

The risk of a synthesiser is that it drifts from what the reader expects and the
tests keep passing against a format nothing produces.
``test_obs_extract_readers.py`` guards that directly: every synthesised file is
read back through the *real* reader and checked against the values that went in.
"""

from __future__ import annotations

import json
import struct

import numpy as np

#: WCP stores samples as 16-bit ints; this is the full-scale count.
WCP_ADCMAX = 32767
#: A/D input voltage range, in the header as ``ad`` and per record as ``vmax``.
WCP_VMAX = 10.0


def write_wcp(path, sweeps, *, dt=1e-4, channels=(("Vm0", "mV"), ("Im0", "pA")),
              gains=None):
    """Write a WinWCP file holding ``sweeps``.

    ``sweeps`` is ``[[ch0_values, ch1_values], ...]`` in the channels' own units.
    The layout is the one ``myokit.formats.wcp`` reads: a 1024-byte ASCII
    ``key=value`` header, then per record a 512-byte analysis block followed by
    a little-endian ``(samples, channels)`` int16 data block.

    Channel order is deliberately settable and the default puts **voltage
    first** -- the opposite of this corpus's real files, which record ``Im0``
    then ``Vm0``. Tests use both orders, because a reader that quietly assumes
    either one is the bug this fixture exists to catch.
    """
    sweeps = [[np.asarray(c, dtype=float) for c in sweep] for sweep in sweeps]
    n_rec = len(sweeps)
    n_ch = len(channels)
    n_samp = len(sweeps[0][0])
    # ``yg`` is the channel gain, and it sets the representable range: myokit
    # decodes ``vmax / (adcmax * yg) * raw``, so full scale is ``vmax / yg``.
    # A gain of 1.0 with a 10 V range would clip anything past 10 mV, turning a
    # -80 mV trace into -10 mV. Derive it per channel from the data instead,
    # with headroom, so the fixture represents whatever the test asks for.
    if gains is None:
        gains = []
        for i in range(n_ch):
            span = max((float(np.max(np.abs(sw[i]))) for sw in sweeps), default=0.0)
            gains.append(WCP_VMAX / (span * 1.2) if span > 0 else 1.0)
    gains = list(gains)

    # ``nbh`` is the header size in **bytes**, while ``nba``/``nbd`` are counts
    # of 512-byte sectors. That asymmetry is real and myokit's reader carries a
    # comment about it ("Seems to be size in bytes!"). It is also the likely
    # reason myokit rejects a third of this corpus with "more than one sampling
    # rate": a file whose ``nbh`` means sectors sends the reader to the wrong
    # offset, where it parses data as the record's sampling interval and finds
    # it disagrees with the header. neo reads those files, which is why it is
    # the primary WCP reader.
    header_bytes = 1024
    header = {
        "ver": 9, "ctime": "01/01/2026 00:00:00", "nc": n_ch, "nr": n_rec,
        "nbh": header_bytes, "nba": 1, "nbd": (2 * n_ch * n_samp + 511) // 512,
        "ad": WCP_VMAX, "adcmax": WCP_ADCMAX, "np": n_samp, "dt": dt,
        "nz": 0, "id": "synthetic",
    }
    for i, (name, unit) in enumerate(channels):
        header[f"yn{i}"] = name
        header[f"yu{i}"] = unit
        header[f"yg{i}"] = gains[i]
        header[f"yz{i}"] = 0
        header[f"yo{i}"] = i
        header[f"yr{i}"] = 0

    text = "".join(f"{k}={v}\n" for k, v in header.items())
    head = text.encode("ascii").ljust(header_bytes, b"\x00")

    rab = 512 * header["nba"]
    rdb = 512 * header["nbd"]
    with open(path, "wb") as fh:
        fh.write(head)
        for rec in sweeps:
            block = b"ACCEPTED" + b"TEST"
            block += struct.pack("<f", 0.0)          # leak-subtraction group
            block += struct.pack("<f", 0.0)          # record time
            block += struct.pack("<f", dt)           # sampling interval
            block += struct.pack("<" + "f" * n_ch, *([WCP_VMAX] * n_ch))
            block += b"synthetic".ljust(16, b"\x00")
            fh.write(block.ljust(rab, b"\x00"))

            # Invert myokit's scaling: value = vmax / (adcmax * yg) * raw
            raw = np.zeros((n_samp, n_ch), dtype="<i2")
            for i, values in enumerate(rec):
                counts = values * WCP_ADCMAX * gains[i] / WCP_VMAX
                raw[:, i] = np.clip(np.rint(counts), -32768, 32767).astype("<i2")
            fh.write(raw.tobytes().ljust(rdb, b"\x00"))
    return str(path)


def write_csv(path, sweeps, *, dt=0.1, delimiter="\t", header=None):
    """Write a tabular recording: one block of rows per sweep, time restarting.

    ``sweeps`` is ``[[vm_values, im_values], ...]``. ``header`` is a list of
    column names, or None for a headerless positional ``[t, Vm, Im]`` file --
    both shapes occur in the wild and both are read.
    """
    lines = []
    if header:
        lines.append(delimiter.join(header))
    for sweep in sweeps:
        n = len(sweep[0])
        for k in range(n):
            cells = [f"{k * dt:.6f}"] + [f"{np.asarray(c, dtype=float)[k]:.6f}"
                                         for c in sweep]
            lines.append(delimiter.join(cells))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return str(path)


def write_npy(path, array, *, sample_rate_hz=None, channels=None, units=None):
    """Write a ``.npy`` and, when a rate is given, its JSON sidecar.

    Without the sidecar the reader has no sampling rate and must say so rather
    than invent one -- which is a case the tests exercise, so the rate is
    optional here on purpose.
    """
    array = np.asarray(array, dtype=float)
    np.save(path, array)
    if sample_rate_hz is not None:
        side = {"sample_rate_hz": sample_rate_hz}
        if channels:
            side["channels"] = list(channels)
        if units:
            side["units"] = list(units)
        with open(str(path).rsplit(".npy", 1)[0] + ".json", "w", encoding="utf-8") as fh:
            json.dump(side, fh)
    return str(path)


def ramp(n, start, stop):
    """A monotone ramp -- a signal whose every sample is checkable by hand."""
    return np.linspace(start, stop, n)


def step(n, base, level, *, lo=None, hi=None):
    """A baseline with a rectangular step, for stimulus-window tests."""
    out = np.full(n, float(base))
    lo = n // 4 if lo is None else lo
    hi = (3 * n) // 4 if hi is None else hi
    out[lo:hi] = float(level)
    return out
