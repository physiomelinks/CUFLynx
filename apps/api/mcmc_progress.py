"""Live MCMC progress, derived from the chain circulatory_autogen writes as it samples.

CA #417 makes ``mcmc_chain.npy`` grow during a run instead of appearing once at the end, which
is what lets the Progress tab draw a chain that is still being sampled (CUFLynx #244). This
module turns that file into the three views the issue asks for: the walker traces, the
autocorrelation, and the windowed mean.

**The definitions are CA's, deliberately.** CA plots the same three things at the end of a run
(``plot_mcmc``, ``plot_autocorrelation``, ``plot_chain_avg``), and a live view that disagreed
with the PDF produced ten minutes later would be worse than no live view at all. Each function
below names the CA routine it mirrors; if one of them changes, this has to change with it.

The chain is the largest artefact a run produces -- steps x walkers x parameters, polled every
few seconds -- so everything here is thinned before it is serialised. The browser cannot draw
100k points per parameter and would not show anything more for having them.
"""

from __future__ import annotations

import os

import numpy as np

CHAIN_FILE = "mcmc_chain.npy"

#: Points per line after thinning. Above this a trace is denser than the pixels it is drawn on.
MAX_POINTS = 400

#: Walkers drawn per parameter. Every walker overlaid is CA's choice for a static PDF; on a
#: polled endpoint it is bytes for ink that is already black, so a sample stands in for the
#: ensemble. The count that were run is reported alongside, so the plot cannot imply otherwise.
MAX_WALKERS = 12

#: CA's plot_chain_avg default.
DEFAULT_WINDOW = 10


def chain_path(output_dir: str) -> str:
    return os.path.join(output_dir, CHAIN_FILE)


def read_chain(output_dir: str) -> np.ndarray | None:
    """The chain so far, or None if there is not one yet.

    Returns None rather than raising on a chain that cannot be read: this is polled against a
    file another process is writing, and the answer to "not there yet" and "half-written by a CA
    old enough to lack #417's atomic save" is the same -- show nothing this tick, ask again.
    """
    path = chain_path(output_dir)
    if not os.path.exists(path):
        return None
    try:
        samples = np.load(path)
    except Exception:  # noqa: BLE001 - a partial or corrupt read is "no data yet"
        return None
    if samples.ndim != 3 or samples.size == 0:
        return None
    return samples


def _thin(num_steps: int) -> np.ndarray:
    """Indices of at most MAX_POINTS steps, evenly spaced, always including the last one.

    The last step matters more than even spacing: it is where the chain has got to, and a plot
    that stops short of it looks like a run that has stalled.
    """
    if num_steps <= MAX_POINTS:
        return np.arange(num_steps)
    return np.unique(np.linspace(0, num_steps - 1, MAX_POINTS).round().astype(int))


def traces(samples: np.ndarray) -> list[list[list[float]]]:
    """Walker traces per parameter -- CA's ``plot_mcmc`` chain plot, thinned.

    Shape is [param][walker][step]; the x values are returned once, in ``steps``, since every
    trace shares them.
    """
    keep = _thin(samples.shape[0])
    walkers = min(samples.shape[1], MAX_WALKERS)
    return [
        [samples[keep, walker, param].tolist() for walker in range(walkers)]
        for param in range(samples.shape[2])
    ]


def windowed_means(samples: np.ndarray, window: int = DEFAULT_WINDOW):
    """Running mean per walker -- CA's ``plot_chain_avg``.

    Same convolution CA uses (``mode='valid'``, so the first value sits at ``window - 1``), and
    the same "skip it, do not fake it" rule when the chain is shorter than the window: early in
    a run there is nothing to average yet, and a single point plotted as a trend reads as one.

    Convergence shows up as the walkers' running means coming together and flattening, which is
    the thing worth watching *during* a run rather than after it.
    """
    num_steps = samples.shape[0]
    if window >= num_steps:
        return None
    kernel = np.ones(window) / window
    walkers = min(samples.shape[1], MAX_WALKERS)
    full_x = np.arange(num_steps - window + 1) + window - 1
    keep = _thin(len(full_x))
    series = [
        [
            np.convolve(samples[:, walker, param], kernel, mode="valid")[keep].tolist()
            for walker in range(walkers)
        ]
        for param in range(samples.shape[2])
    ]
    return {"steps": full_x[keep].tolist(), "series": series, "window": window}


def autocorrelation_1d(values: np.ndarray) -> np.ndarray:
    """Normalised autocorrelation of one walker's trace.

    This is ``emcee.autocorr.function_1d`` -- FFT autocorrelation on a zero-padded power of two,
    normalised by lag zero. Reimplemented rather than imported so the API does not take a
    dependency on emcee to draw a picture; ``test_matches_emcee`` asserts the two agree to
    floating point, so "the same as CA's" is checked rather than asserted in a comment.
    """
    values = np.atleast_1d(np.asarray(values, dtype=float))
    n = 1
    while n < len(values):
        n <<= 1
    freq = np.fft.fft(values - np.mean(values), n=2 * n)
    acf = np.fft.ifft(freq * np.conjugate(freq))[: len(values)].real
    if acf[0] == 0:
        # A walker that never moved: constant, so every lag is perfectly correlated. Dividing
        # would be 0/0 and would poison the plot with NaN.
        return np.ones_like(acf)
    return acf / acf[0]


def autocorrelations(samples: np.ndarray):
    """Autocorrelation vs lag per parameter -- CA's ``plot_autocorrelation``.

    ``bounded`` applies CA's own reading of the plot: every walker inside +-0.1 over the last
    fifth of the lags means the chain is producing near-independent draws. It is reported rather
    than left to the eye because it is the question the plot exists to answer.
    """
    num_steps = samples.shape[0]
    if num_steps < 2:
        return None
    walkers = min(samples.shape[1], MAX_WALKERS)
    keep = _thin(num_steps)
    series, bounded = [], True
    for param in range(samples.shape[2]):
        per_walker = []
        for walker in range(walkers):
            acf = autocorrelation_1d(samples[:, walker, param])
            tail = max(1, int(0.2 * len(acf)))
            if np.any(np.abs(acf[-tail:]) > 0.1):
                bounded = False
            per_walker.append(acf[keep].tolist())
        series.append(per_walker)
    return {"lags": keep.tolist(), "series": series, "bounded": bounded}


def progress(output_dir: str, param_labels: list[str] | None = None) -> dict:
    """The live payload: the three views plus enough context to label them.

    Always the same shape, so the client renders "nothing yet" from ``steps: 0`` rather than
    from a missing key. A run that has not written its first checkpoint is not an error.
    """
    samples = read_chain(output_dir)
    if samples is None:
        return {
            "steps": 0,
            "walkers": 0,
            "num_params": 0,
            "param_labels": [],
            "walkers_shown": 0,
            "trace_steps": [],
            "traces": [],
            "windowed_mean": None,
            "autocorrelation": None,
        }

    num_steps, num_walkers, num_params = samples.shape
    keep = _thin(num_steps)
    labels = list(param_labels or [])
    if len(labels) != num_params:
        labels = [f"parameter {idx + 1}" for idx in range(num_params)]

    return {
        "steps": int(num_steps),
        "walkers": int(num_walkers),
        "num_params": int(num_params),
        "param_labels": labels,
        "walkers_shown": int(min(num_walkers, MAX_WALKERS)),
        "trace_steps": keep.tolist(),
        "traces": traces(samples),
        "windowed_mean": windowed_means(samples),
        "autocorrelation": autocorrelations(samples),
    }
