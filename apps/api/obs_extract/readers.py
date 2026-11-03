"""Read one patch-clamp recording, whatever format it arrived in.

Four formats, one shape. ``.wcp`` and ``.abf`` are binary instrument files;
``.csv`` and ``.npy`` are what somebody exports when the instrument file is not
available. They differ enormously in what they carry -- an ``.abf`` knows its own
sampling rate and channel units, a ``.npy`` is a bare array -- so the job of this
module is to present all four as a :class:`Recording` and to be explicit, per
file, about what it could not work out.

**Why myokit and not pyabf.** ``myokit.formats.SweepSource`` is the interface
``WcpFile`` and ``AbfFile`` both implement, and myokit is already a core CUFLynx
dependency. Measured against the corpus this was written for, ``AbfFile`` read
21/21 ``.abf`` files, so ``pyabf`` -- which the sympathetic_neuron scripts use --
buys nothing and is deliberately not a dependency here.

**Why neo is nevertheless required for .wcp.** The same measurement over 60 of
488 ``.wcp`` files: myokit read 41, failing the other 19 with *"WCP file contains
more than one sampling rate"*; ``neo.io.WinWcpIO`` read all 60. A third of a real
corpus is not an acceptable loss, so neo is the primary ``.wcp`` reader and
myokit is the fallback for installs that do not have it.

**Laziness is the point.** A scan of several hundred recordings must not decode
sample data, so :func:`probe` reads headers only and :meth:`Recording.sweep`
decodes one sweep at a time. Both binary readers memory-map or seek rather than
slurp, which is why this is affordable at all.
"""

from __future__ import annotations

import csv as _csv
import json
import os
import re
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .errors import ObsExtractError

#: Suffixes this module can be asked to open. Order is display order in the GUI.
SUPPORTED_SUFFIXES = (".wcp", ".abf", ".csv", ".txt", ".npy")

#: The two roles the extraction pipeline needs to tell apart. A recording may
#: carry other channels; they are kept and addressable by name, but only these
#: two drive clamp commands and observables.
VOLTAGE = "voltage"
CURRENT = "current"

#: Substrings that name a channel, when units cannot settle it. Lowercased
#: comparison. Deliberately short and specific: ``"v"`` alone would match
#: "Vcmd", "Vhold" and half the alphabet soup instruments emit.
DEFAULT_NAME_PATTERNS = {
    VOLTAGE: ("vm", "v_m", "voltage", "v_sensed", "membrane"),
    CURRENT: ("im", "i_m", "current", "i_tot"),
}


@dataclass(frozen=True)
class ChannelInfo:
    """One recorded signal, and what we worked out about it."""

    index: int
    name: str
    unit: str  # rendered, e.g. "[mV]"; "" when the format does not say
    role: str | None = None  # VOLTAGE | CURRENT | None
    #: How ``role`` was decided, so the report and the GUI can say. One of
    #: "explicit", "unit", "name", "position", or "" when undecided.
    role_source: str = ""


@dataclass
class Recording:
    """A file's worth of sweeps, decoded on demand.

    ``sweep_count`` and ``channels`` come from the header, so they are available
    after :func:`probe` without touching sample data. ``_load`` is the closure
    that decodes one sweep; it is set by whichever reader opened the file.
    """

    path: str
    format: str
    sweep_count: int
    sample_rate_hz: float
    duration_s: float
    channels: list[ChannelInfo]
    equal_length_sweeps: bool = True
    #: Format metadata, verbatim, for the extraction report.
    meta: dict = field(default_factory=dict)
    #: Anything the reader had to guess or could not do. Carried into the scan
    #: response and into the report -- a guess the user never sees is the thing
    #: that makes an extraction wrong in a way nobody can explain later.
    warnings: list[str] = field(default_factory=list)
    _load: Callable[[int], tuple[np.ndarray, dict[str, np.ndarray]]] | None = None

    def sweep(self, index: int) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """``(t_seconds, {channel_name: values})`` for one sweep.

        Keyed by channel *name*, not role, so a recording with three channels
        keeps all three. Callers that want a role use :meth:`channel_named`.
        """
        if self._load is None:  # pragma: no cover - every reader sets it
            raise ObsExtractError(f"{self.path}: no loader was attached")
        if not 0 <= index < self.sweep_count:
            raise ObsExtractError(
                f"{self.path}: sweep {index} out of range (0..{self.sweep_count - 1})")
        return self._load(index)

    def name_for_role(self, role: str) -> str | None:
        for ch in self.channels:
            if ch.role == role:
                return ch.name
        return None

    def roles_resolved(self) -> bool:
        """Whether at least one channel has a role.

        Not "both": a current-clamp recording exported as a single voltage trace
        is perfectly usable, and demanding a current channel would reject it.
        """
        return any(ch.role for ch in self.channels)


# ---------------------------------------------------------------------------
# Role resolution
# ---------------------------------------------------------------------------
def _unit_role(unit_obj) -> str | None:
    """VOLTAGE/CURRENT from a myokit Unit, by dimension rather than by name.

    Compares the SI exponent vector, so ``[mV]``, ``[V]`` and the base-unit
    spelling ``[g*m^2/s^3/A]`` all answer voltage -- which matters, because the
    ABF reader hands back whichever of those the file recorded.

    This is preferred over name matching wherever units exist. In this corpus
    the WCP channel order is ``['Im0', 'Vm0']`` -- *current first* -- so a
    positional rule would silently swap the two, and a name rule depends on the
    operator's naming discipline. The dimension cannot be got wrong.
    """
    try:
        import myokit  # noqa: PLC0415 - core dep, but keep the import local

        exps = list(unit_obj.exponents())
        if exps == list(myokit.units.V.exponents()):
            return VOLTAGE
        if exps == list(myokit.units.A.exponents()):
            return CURRENT
    except Exception:  # noqa: BLE001 - not a myokit Unit, or an odd unit
        return None
    return None


#: Unit spellings recognised without myokit, by their base symbol. myokit parses
#: the general case; this covers what instruments actually write, so a CSV or
#: .npy carrying "pA"/"mV" still resolves its roles on an install that has no
#: myokit -- where the alternative is falling through to the positional guess,
#: which can be backwards.
_SI_PREFIXES = ("", "y", "z", "a", "f", "p", "n", "u", "\u00b5", "m", "c", "d",
                "da", "h", "k", "M", "G", "T")
_UNIT_BASES = {"v": VOLTAGE, "volt": VOLTAGE, "volts": VOLTAGE,
               "a": CURRENT, "amp": CURRENT, "amps": CURRENT, "ampere": CURRENT,
               "amperes": CURRENT}


def _unit_role_from_text(unit: str) -> str | None:
    """VOLTAGE/CURRENT from a unit string like ``[mV]`` or ``pA``, or None."""
    text = str(unit or "").strip().strip("[]").strip()
    if not text:
        return None
    for prefix in sorted(_SI_PREFIXES, key=len, reverse=True):
        if prefix and not text.startswith(prefix):
            continue
        rest = text[len(prefix):]
        role = _UNIT_BASES.get(rest.lower())
        if role:
            return role
    return None


def _myokit_units_from_strings(units: list[str]) -> list:
    """myokit Units parsed from rendered strings, for readers that carry text.

    neo hands back quantities rather than myokit Units, and the CSV/NPY readers
    have only whatever string the user supplied. Parsing them into Units keeps
    role resolution on one code path -- dimension comparison -- instead of a
    second, weaker string-matching path for half the formats.

    An unparseable entry becomes None, which simply means role resolution falls
    through to the name rule for that channel.
    """
    try:
        import myokit  # noqa: PLC0415
    except ImportError:  # pragma: no cover - core dep
        return [None] * len(units)
    out = []
    for u in units:
        text = str(u or "").strip().strip("[]")
        try:
            out.append(myokit.parse_unit(text) if text else None)
        except Exception:  # noqa: BLE001 - an unrecognised unit is not an error
            out.append(None)
    return out


def _name_role(name: str, patterns: dict[str, tuple[str, ...]]) -> str | None:
    low = str(name or "").lower()
    for role, subs in patterns.items():
        if any(s in low for s in subs):
            return role
    return None


def resolve_roles(
    channels: list[ChannelInfo],
    *,
    unit_objects: list | None = None,
    explicit: dict[int, str] | None = None,
    name_patterns: dict[str, tuple[str, ...]] | None = None,
    allow_positional: bool = True,
) -> tuple[list[ChannelInfo], list[str]]:
    """Decide which channel is voltage and which is current.

    Order: an explicit per-index override, then unit dimension, then a name
    substring, then -- only for a two-channel recording with neither -- position.
    Returns the updated channels and any warnings.

    The positional fallback warns rather than staying silent, because it is the
    one rule here that can be confidently wrong: this corpus's own WCP files
    order the channels current-first, the opposite of the convention the
    sympathetic_neuron scripts assume.
    """
    explicit = explicit or {}
    patterns = name_patterns or DEFAULT_NAME_PATTERNS
    warnings: list[str] = []
    out: list[ChannelInfo] = []
    for ch in channels:
        role, source = None, ""
        if ch.index in explicit:
            role, source = explicit[ch.index], "explicit"
        if role is None and unit_objects is not None and ch.index < len(unit_objects):
            role = _unit_role(unit_objects[ch.index])
            source = "unit" if role else ""
        if role is None:
            # myokit may be absent, or the unit may be one it does not parse.
            role = _unit_role_from_text(ch.unit)
            source = "unit" if role else ""
        if role is None:
            role = _name_role(ch.name, patterns)
            source = "name" if role else ""
        out.append(ChannelInfo(ch.index, ch.name, ch.unit, role, source))

    if allow_positional and len(out) == 2 and not any(c.role for c in out):
        warnings.append(
            "neither channel names nor units identified voltage and current; "
            "assuming channel 0 is voltage and channel 1 is current. Check this "
            "-- WCP files in particular are often recorded current-first.")
        out = [
            ChannelInfo(out[0].index, out[0].name, out[0].unit, VOLTAGE, "position"),
            ChannelInfo(out[1].index, out[1].name, out[1].unit, CURRENT, "position"),
        ]
    # A duplicate role means two channels claimed the same thing; keep the first
    # and say so rather than letting the later one silently win.
    seen: dict[str, int] = {}
    final: list[ChannelInfo] = []
    for ch in out:
        if ch.role and ch.role in seen:
            warnings.append(
                f"channels {seen[ch.role]} and {ch.index} both look like "
                f"{ch.role}; using channel {seen[ch.role]}.")
            final.append(ChannelInfo(ch.index, ch.name, ch.unit, None, ""))
            continue
        if ch.role:
            seen[ch.role] = ch.index
        final.append(ch)
    return final, warnings


# ---------------------------------------------------------------------------
# .wcp -- neo first, myokit as the fallback
# ---------------------------------------------------------------------------
#: neo labels a WCP signal ``"Channels: (Im0)"``; myokit calls the same channel
#: ``"Im0"``. The name is a dict key and a report label, and a config saved while
#: neo was installed has to keep working when it is not -- so both readers are
#: made to agree on the instrument's own name.
_NEO_NAME = re.compile(r"^\s*Channels?:\s*\((.*)\)\s*$")


def _clean_neo_name(name, index: int) -> str:
    text = str(name or "").strip()
    match = _NEO_NAME.match(text)
    if match:
        text = match.group(1).strip()
    return text or f"ch{index}"


def _read_wcp(path: str, opts: dict) -> Recording:
    """neo first, then myokit -- and myokit is tried even when neo *failed*.

    The two libraries do not fail on the same files. Over 488 real recordings neo
    read 482 and myokit 41 of a 60-file sample, but the six neo could not open
    (internal ``KeyError: 'RTIME'`` and an unset ``analysisHeader``) are not the
    ones myokit rejects. Trying the second reader after the first errors costs a
    failed parse and occasionally rescues a file, so the fallback is on failure
    rather than only on neo being absent.

    Both reasons are reported when both fail: "myokit could not read it" alone
    would be misleading on a machine where neo was the one that was tried first.
    """
    neo_error = None
    try:
        rec = _read_wcp_neo(path, opts)
        if rec is not None:
            return rec
    except ObsExtractError as exc:
        neo_error = exc

    try:
        rec = _read_wcp_myokit(path, opts)
        if rec is not None:
            return rec
    except ObsExtractError as exc:
        if neo_error is not None:
            raise ObsExtractError(
                f"{os.path.basename(path)}: neither reader could open it. "
                f"neo: {_reason(neo_error)}; myokit: {_reason(exc)}") from exc
        raise

    raise ObsExtractError(
        f"{os.path.basename(path)}: could not be read as WCP. neo is not "
        f"installed and myokit's reader is unavailable -- install the "
        f"'dataimport' extra (pip install 'cuflynx-api[dataimport]').")


def _reason(exc: Exception) -> str:
    """The message without the filename prefix each reader already added."""
    text = str(exc)
    _, sep, rest = text.partition(": ")
    return (rest or text) if sep else text


def _has_neo() -> bool:
    try:
        import neo  # noqa: F401,PLC0415

        return True
    except ImportError:
        return False


def _read_wcp_neo(path: str, opts: dict) -> Recording | None:
    try:
        from neo.io import WinWcpIO  # noqa: PLC0415
    except ImportError:
        return None
    try:
        block = WinWcpIO(path).read_block(lazy=False)
    except Exception as exc:  # noqa: BLE001 - a file neo cannot read
        raise ObsExtractError(f"{os.path.basename(path)}: neo could not read it ({exc})") from exc

    segments = list(block.segments)
    if not segments:
        raise ObsExtractError(f"{os.path.basename(path)}: no sweeps in the file")
    first = segments[0].analogsignals
    names, units = [], []
    for i, sig in enumerate(first):
        names.append(_clean_neo_name(getattr(sig, "name", ""), i))
        units.append(f"[{sig.units.dimensionality.string}]")

    channels = [ChannelInfo(i, n, u) for i, (n, u) in enumerate(zip(names, units))]
    # neo carries units as quantities, not myokit Units. Render them into
    # myokit's vocabulary so role resolution has one code path for every format.
    channels, warns = resolve_roles(
        channels, unit_objects=_myokit_units_from_strings(units),
        explicit=opts.get("roles"), name_patterns=opts.get("name_patterns"))

    rate = float(first[0].sampling_rate.rescale("Hz").magnitude) if first else 0.0
    n = int(first[0].shape[0]) if first else 0

    def load(i: int):
        sigs = segments[i].analogsignals
        t = np.asarray(sigs[0].times.rescale("s").magnitude, dtype=float)
        return t, {names[k]: np.asarray(s.magnitude, dtype=float).ravel()
                   for k, s in enumerate(sigs)}

    lengths = {int(s.analogsignals[0].shape[0]) for s in segments if s.analogsignals}
    return Recording(
        path=path, format="wcp", sweep_count=len(segments), sample_rate_hz=rate,
        duration_s=(n / rate if rate else 0.0), channels=channels,
        equal_length_sweeps=len(lengths) <= 1,
        meta={"reader": "neo.io.WinWcpIO", "n_channels": len(names)},
        warnings=warns, _load=load)


def _read_wcp_myokit(path: str, opts: dict) -> Recording | None:
    try:
        from myokit.formats.wcp import WcpFile  # noqa: PLC0415

        f = WcpFile(path)
    except ImportError:  # pragma: no cover - myokit is a core dep
        return None
    except ObsExtractError:
        raise
    except Exception as exc:  # noqa: BLE001
        # The known one is "more than one sampling rate", which neo handles --
        # so recommend neo, but only when neo is not already installed and
        # failing too. Telling someone to install what they have is noise.
        hint = ""
        if not _has_neo():
            hint = (" Installing neo (the 'dataimport' extra) reads WCP files "
                    "myokit cannot.")
        raise ObsExtractError(
            f"{os.path.basename(path)}: myokit could not read it ({exc})."
            f"{hint}") from exc
    return _from_sweep_source(f, path, "wcp", opts, reader="myokit.formats.wcp")


# ---------------------------------------------------------------------------
# .abf -- myokit only; pyabf is deliberately not a dependency
# ---------------------------------------------------------------------------
def _has_myokit() -> bool:
    try:
        import myokit  # noqa: F401,PLC0415

        return True
    except ImportError:
        return False


def _read_abf(path: str, opts: dict) -> Recording:
    try:
        from myokit.formats.axon import AbfFile  # noqa: PLC0415
    except ImportError as exc:
        raise ObsExtractError(
            f"{os.path.basename(path)}: reading ABF needs myokit, which is not "
            f"installed here.") from exc

    try:
        f = AbfFile(path)
    except Exception as exc:  # noqa: BLE001
        raise ObsExtractError(f"{os.path.basename(path)}: could not read as ABF ({exc})") from exc
    return _from_sweep_source(f, path, "abf", opts, reader="myokit.formats.axon")


def _from_sweep_source(src, path: str, fmt: str, opts: dict, *, reader: str) -> Recording:
    """Wrap anything implementing ``myokit.formats.SweepSource``."""
    names = [str(n) for n in src.channel_names()]
    unit_objs = list(src.channel_units())
    channels = [ChannelInfo(i, n, str(u)) for i, (n, u) in enumerate(zip(names, unit_objs))]
    channels, warns = resolve_roles(
        channels, unit_objects=unit_objs, explicit=opts.get("roles"),
        name_patterns=opts.get("name_patterns"))

    n_sweeps = int(src.sweep_count())
    if n_sweeps <= 0:
        # Two files in the reference corpus have a valid header and no records.
        # Asking for channel 0 to derive the sampling rate raises out of myokit
        # ("Channel 0 not found (empty file)"), which would surface as an
        # unexpected error rather than the plain fact that the file is empty.
        raise ObsExtractError(
            f"{os.path.basename(path)}: the file parsed but holds no sweeps.")
    times0, _ = _sweep_source_sweep(src, 0, names)
    rate = 1.0 / float(times0[1] - times0[0]) if times0.size > 1 else 0.0

    return Recording(
        path=path, format=fmt, sweep_count=n_sweeps, sample_rate_hz=rate,
        duration_s=float(times0[-1] - times0[0]) if times0.size else 0.0,
        channels=channels,
        equal_length_sweeps=bool(getattr(src, "equal_length_sweeps", lambda: True)()),
        meta={"reader": reader, "n_channels": len(names)},
        warnings=warns,
        _load=lambda i: _sweep_source_sweep(src, i, names))


def _sweep_source_sweep(src, index: int, names: list[str]):
    """One sweep out of a SweepSource, as ``(t, {name: values})``.

    ``channel(i)`` returns ``(times, sweeps)`` with one entry per sweep, so the
    per-sweep slice happens here rather than in every caller.
    """
    out: dict[str, np.ndarray] = {}
    times = None
    for k, name in enumerate(names):
        t, sweeps = src.channel(k, join_sweeps=False)
        arr = sweeps[index] if isinstance(sweeps, (list, tuple)) else sweeps
        out[name] = np.asarray(arr, dtype=float).ravel()
        if times is None:
            times = np.asarray(t[index] if isinstance(t, (list, tuple)) else t,
                               dtype=float).ravel()
    if times is None:  # pragma: no cover - a file with no channels
        raise ObsExtractError("recording has no channels")
    return times, out


# ---------------------------------------------------------------------------
# .csv / .txt
# ---------------------------------------------------------------------------
#: Column-name synonyms, lowercased and stripped of non-alphanumerics.
_CSV_COLUMNS = {
    "time": ("t", "time", "times", "timesec", "times", "timeseconds"),
    VOLTAGE: ("vm", "v", "voltage", "vmv", "membranepotential"),
    CURRENT: ("im", "i", "current", "ipa"),
}


def _norm_col(name: str) -> str:
    return "".join(c for c in str(name).lower() if c.isalnum())


def _read_csv(path: str, opts: dict) -> Recording:
    import pandas as pd  # noqa: PLC0415 - core dep, kept local for import cost

    delim = opts.get("delimiter") or _sniff_delimiter(path)
    has_header = opts.get("has_header")
    if has_header is None:
        has_header = _looks_like_header(path, delim)
    try:
        df = pd.read_csv(path, delimiter=delim, header=0 if has_header else None)
    except Exception as exc:  # noqa: BLE001
        raise ObsExtractError(f"{os.path.basename(path)}: could not parse as CSV ({exc})") from exc
    if df.empty:
        raise ObsExtractError(f"{os.path.basename(path)}: no rows")

    warns: list[str] = []
    if has_header:
        by_norm = {_norm_col(c): c for c in df.columns}
        tcol = next((by_norm[n] for n in _CSV_COLUMNS["time"] if n in by_norm), None)
        vcol = next((by_norm[n] for n in _CSV_COLUMNS[VOLTAGE] if n in by_norm), None)
        icol = next((by_norm[n] for n in _CSV_COLUMNS[CURRENT] if n in by_norm), None)
    else:
        tcol = vcol = icol = None
    if tcol is None:
        # Positional [t, Vm, Im], the shape the sympathetic_neuron .txt files use.
        cols = list(df.columns)
        tcol = cols[0]
        vcol = cols[1] if len(cols) > 1 else None
        icol = cols[2] if len(cols) > 2 else None
        warns.append(
            "no recognised column names; read positionally as "
            "[time, voltage, current].")

    t_all = np.asarray(df[tcol], dtype=float)
    signals: dict[str, np.ndarray] = {}
    if vcol is not None:
        signals["Vm"] = np.asarray(df[vcol], dtype=float)
    if icol is not None:
        signals["Im"] = np.asarray(df[icol], dtype=float)
    if not signals:
        raise ObsExtractError(f"{os.path.basename(path)}: no signal columns beside time")

    sweep_col = opts.get("sweep_column")
    bounds = _sweep_bounds(
        t_all, np.asarray(df[sweep_col]) if sweep_col in df.columns else None)
    rate = _rate_from_time(t_all, bounds)

    channels = [ChannelInfo(i, n, opts.get("units", {}).get(n, ""))
                for i, n in enumerate(signals)]
    channels, rwarns = resolve_roles(
        channels, unit_objects=_myokit_units_from_strings([c.unit for c in channels]),
        explicit=opts.get("roles"), name_patterns=opts.get("name_patterns"))

    def load(i: int):
        lo, hi = bounds[i]
        return t_all[lo:hi] - t_all[lo], {k: v[lo:hi] for k, v in signals.items()}

    lengths = {hi - lo for lo, hi in bounds}
    return Recording(
        path=path, format="csv", sweep_count=len(bounds), sample_rate_hz=rate,
        duration_s=float(t_all[bounds[0][1] - 1] - t_all[bounds[0][0]]) if bounds else 0.0,
        channels=channels, equal_length_sweeps=len(lengths) <= 1,
        meta={"reader": "pandas", "delimiter": delim, "header": bool(has_header)},
        warnings=warns + rwarns, _load=load)


def _sniff_delimiter(path: str) -> str:
    try:
        with open(path, newline="", encoding="utf-8", errors="ignore") as fh:
            sample = fh.read(8192)
        return _csv.Sniffer().sniff(sample, delimiters="\t,; ").delimiter
    except Exception:  # noqa: BLE001 - Sniffer is easily defeated; guess by count
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                line = fh.readline()
            return "\t" if line.count("\t") >= line.count(",") else ","
        except OSError:
            return ","


def _looks_like_header(path: str, delim: str) -> bool:
    """Whether row 0 is names rather than numbers."""
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            first = fh.readline().strip()
    except OSError:  # pragma: no cover
        return False
    if not first:
        return False
    for cell in first.split(delim):
        try:
            float(cell.strip())
        except ValueError:
            return True
    return False


def _sweep_bounds(t: np.ndarray, sweep_col=None) -> list[tuple[int, int]]:
    """``[(start, stop), ...]`` row ranges, one per sweep.

    Split wherever time stops increasing, and **include the tail**. The
    sympathetic_neuron reader splits on ``t == 0.0`` exactly and iterates
    ``[split[i-1]:split[i]]``, which drops everything after the final split --
    the last sweep of every multi-sweep file. Comparing ``t[i] <= t[i-1]``
    instead also catches a file whose sweeps restart at something other than
    exactly zero, and float equality never enters into it.
    """
    n = int(t.size)
    if n == 0:
        return []
    if sweep_col is not None and np.asarray(sweep_col).size == n:
        col = np.asarray(sweep_col)
        starts = [0] + [i for i in range(1, n) if col[i] != col[i - 1]]
    else:
        starts = [0] + [i for i in range(1, n) if t[i] <= t[i - 1]]
    return [(s, starts[k + 1] if k + 1 < len(starts) else n)
            for k, s in enumerate(starts)]


def _rate_from_time(t: np.ndarray, bounds) -> float:
    if not bounds:
        return 0.0
    lo, hi = bounds[0]
    seg = t[lo:hi]
    if seg.size < 2:
        return 0.0
    dt = float(np.median(np.diff(seg)))
    return 1.0 / dt if dt > 0 else 0.0


# ---------------------------------------------------------------------------
# .npy
# ---------------------------------------------------------------------------
def _read_npy(path: str, opts: dict) -> Recording:
    """A bare array plus a sample rate from somewhere.

    ``allow_pickle`` is **never** enabled. A ``.npy`` here is a file the user
    browsed to, and ``allow_pickle=True`` on an untrusted array is arbitrary code
    execution at load time -- so an object array is refused with a message that
    names the flag, rather than quietly working for the person who knows.

    Shapes: ``(n,)`` one sweep/one channel; ``(sweeps, samples)`` one channel;
    ``(sweeps, channels, samples)`` the general case. Time is always synthesised,
    because a ``.npy`` carries none.
    """
    try:
        arr = np.load(path, allow_pickle=False)
    except ValueError as exc:
        if "allow_pickle" in str(exc):
            raise ObsExtractError(
                f"{os.path.basename(path)}: this .npy holds Python objects and "
                f"would need allow_pickle=True to load, which would execute code "
                f"from the file. Re-export it as a plain numeric array.") from exc
        raise ObsExtractError(f"{os.path.basename(path)}: could not load ({exc})") from exc
    except Exception as exc:  # noqa: BLE001
        raise ObsExtractError(f"{os.path.basename(path)}: could not load ({exc})") from exc

    side = _npy_sidecar(path)
    rate = opts.get("sample_rate_hz") or side.get("sample_rate_hz")
    if not rate:
        raise ObsExtractError(
            f"{os.path.basename(path)}: a .npy carries no sampling rate. Add a "
            f"'{os.path.splitext(os.path.basename(path))[0]}.json' beside it "
            f'containing {{"sample_rate_hz": 20000}}, or set the rate for this '
            f"dataset.")
    rate = float(rate)

    arr = np.asarray(arr, dtype=float)
    warns: list[str] = []
    if arr.ndim == 1:
        data = arr[None, None, :]
    elif arr.ndim == 2:
        if opts.get("transpose"):
            arr = arr.T
        elif arr.shape[0] > arr.shape[1]:
            warns.append(
                f"array is {arr.shape[0]}x{arr.shape[1]}; read as "
                f"{arr.shape[0]} sweeps of {arr.shape[1]} samples. If it is the "
                f"other way round, set transpose for this dataset.")
        data = arr[:, None, :]
    elif arr.ndim == 3:
        data = arr
    else:
        raise ObsExtractError(
            f"{os.path.basename(path)}: {arr.ndim}-dimensional array; expected "
            f"(samples), (sweeps, samples) or (sweeps, channels, samples).")

    n_sweeps, n_ch, n_samp = data.shape
    names = list(side.get("channels") or opts.get("channels") or [])
    if len(names) != n_ch:
        names = ([VOLTAGE, CURRENT][:n_ch] if n_ch <= 2
                 else [f"ch{i}" for i in range(n_ch)])
    units = list(side.get("units") or [])
    channels = [ChannelInfo(i, str(names[i]), str(units[i]) if i < len(units) else "")
                for i in range(n_ch)]
    channels, rwarns = resolve_roles(
        channels, unit_objects=_myokit_units_from_strings([c.unit for c in channels]),
        explicit=opts.get("roles"), name_patterns=opts.get("name_patterns"))

    t = np.arange(n_samp, dtype=float) / rate

    def load(i: int):
        return t, {channels[k].name: data[i, k, :] for k in range(n_ch)}

    return Recording(
        path=path, format="npy", sweep_count=n_sweeps, sample_rate_hz=rate,
        duration_s=n_samp / rate, channels=channels, equal_length_sweeps=True,
        meta={"reader": "numpy", "shape": list(arr.shape), "sidecar": bool(side)},
        warnings=warns + rwarns, _load=load)


def _npy_sidecar(path: str) -> dict:
    side = os.path.splitext(path)[0] + ".json"
    if not os.path.isfile(side):
        return {}
    try:
        with open(side, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 - a malformed sidecar is not a fatal error
        return {}


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------
_READERS = {
    ".wcp": _read_wcp,
    ".abf": _read_abf,
    ".csv": _read_csv,
    ".txt": _read_csv,
    ".npy": _read_npy,
}


def _require_sweeps(rec: Recording) -> Recording:
    """Refuse a recording with no sweeps in it.

    Two files in the reference corpus parse cleanly and report ``sweep_count``
    0 -- a header with no records behind it. Handing that back as "readable"
    puts a row in the GUI that can never contribute an observable, so it is
    reported as the empty file it is.
    """
    if rec.sweep_count <= 0:
        raise ObsExtractError(
            f"{os.path.basename(rec.path)}: the file parsed but holds no sweeps.")
    return rec


def open_recording(path: str, **opts) -> Recording:
    """Open ``path``, or raise :class:`ObsExtractError` saying why not.

    ``opts`` are the per-dataset reader settings from the extraction config:
    ``roles`` ({index: role}), ``name_patterns``, ``sample_rate_hz``,
    ``transpose``, ``delimiter``, ``has_header``, ``sweep_column``, ``units``.
    """
    suffix = os.path.splitext(path)[1].lower()
    reader = _READERS.get(suffix)
    if reader is None:
        raise ObsExtractError(
            f"{os.path.basename(path)}: unsupported format '{suffix}'. "
            f"Supported: {', '.join(SUPPORTED_SUFFIXES)}.")
    if not os.path.isfile(path):
        raise ObsExtractError(f"{path}: no such file")
    return _require_sweeps(reader(path, opts))


def probe(path: str, **opts) -> dict:
    """Header-only summary of one recording, for the directory scan.

    Never raises for a file it cannot read: a scan of several hundred recordings
    must not fail because one of them is corrupt. The failure is reported as
    ``readable: False`` with the reason, and ``needs`` names what the GUI should
    ask for so the file can be retried.
    """
    base = {"path": path, "format": os.path.splitext(path)[1].lower().lstrip("."),
            "readable": False, "needs": [], "error": None, "warnings": []}
    try:
        rec = open_recording(path, **opts)
    except ObsExtractError as exc:
        base["error"] = str(exc)
        if "sampling rate" in str(exc) or "sample_rate_hz" in str(exc):
            base["needs"] = ["sample_rate_hz"]
        return base
    except Exception as exc:  # noqa: BLE001 - a scan reports, it does not fail
        base["error"] = f"unexpected error: {exc}"
        return base

    base.update(
        readable=True,
        sweep_count=rec.sweep_count,
        sample_rate_hz=rec.sample_rate_hz,
        duration_s=rec.duration_s,
        equal_length_sweeps=rec.equal_length_sweeps,
        channels=[{"index": c.index, "name": c.name, "unit": c.unit,
                   "role": c.role, "role_source": c.role_source} for c in rec.channels],
        meta=rec.meta,
        warnings=list(rec.warnings),
    )
    if not rec.roles_resolved():
        base["needs"] = ["channel_roles"]
    return base


def available_formats() -> list[dict]:
    """Which formats this install can actually read, and what is missing.

    ``.wcp`` is reported available either way -- myokit reads most of them -- but
    carries the neo hint, because "available" and "reads every file you have" are
    not the same claim and the difference was measured at a third of a corpus.
    """
    has_neo, has_myokit = _has_neo(), _has_myokit()

    if has_neo:
        wcp_note = None
    elif has_myokit:
        wcp_note = ("myokit reads most WCP files; install neo (the 'dataimport' "
                    "extra) for the ones recorded at more than one sampling rate.")
    else:
        wcp_note = ("reading WCP needs neo (the 'dataimport' extra) or myokit; "
                    "neither is installed here.")

    return [
        # Available when *either* reader is importable -- and honestly false when
        # neither is. Claiming a format the GUI then cannot open is worse than
        # greying it out with a reason.
        {"suffix": ".wcp", "available": has_neo or has_myokit,
         "needs": None if has_neo else ("neo" if has_myokit else "neo or myokit"),
         "note": wcp_note},
        {"suffix": ".abf", "available": has_myokit,
         "needs": None if has_myokit else "myokit",
         "note": None if has_myokit else "reading ABF needs myokit."},
        {"suffix": ".csv", "available": True, "needs": None, "note": None},
        {"suffix": ".npy", "available": True, "needs": None,
         "note": "needs a sample rate, from a <name>.json sidecar or the dataset settings."},
    ]
