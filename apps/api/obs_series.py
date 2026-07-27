"""Compute the ``series_output`` (transformed) series an obs_data operation defines.

The live Output plots panel overlays the *model* series a data_item's operation
produces — not the raw operand. This mirrors circulatory_autogen's
``paramID.get_obs_output_dict(get_all_series=True)``: a data_item whose operation
func carries a ``series_to_constant`` branch is plotted as
``func(*operands, series_output=True, **operation_kwargs)``; an operation without
that branch that returns an array plots that array; scalar-returning operations
and data_items with no operation have no transformed series (the raw operand is
plotted unchanged). CA's operation funcs are Python, so the frontend can't run
them — the transform must happen here (issue #111).
"""

from __future__ import annotations

from engine import _resolve_output_key
from obs_options import get_operation_funcs


def compute_output_series(
    data_items: list, outputs: dict, output_dir: str | None = None
) -> dict:
    """Map ``data_item index -> transformed series`` for a simulated result.

    ``outputs`` is the ``{qname: [floats]}`` block a simulate / protocol run
    returns. Only data_items whose operation yields a series are included; every
    other data_item (no operation, scalar-only operation, unresolved operand, or
    an operation that raises) is simply omitted, so the caller falls back to
    plotting the raw operand. Never raises — a missing/broken CA yields ``{}``.

    A series is emitted only when it is something *new to plot*. Most of CA's
    ``@series_to_constant`` operations have an identity series branch — ``max`` /
    ``min`` / ``mean`` / ``max_minus_min`` all ``return x`` under
    ``series_output=True`` — so several data_items on one variable would each
    contribute a byte-identical copy of the raw operand. The plot draws one line
    per entry, so three data_items on ``aortic_root/u`` (mean, max, min) became
    three coincident lines and three tooltip rows for a single trace. Two rules
    prevent that: a result equal to one of its own operands is dropped (the raw
    trace is already plotted), and a result equal to one already emitted for the
    same operands is dropped (a repeated transform). Each distinct
    ``(operation, operands, kwargs)`` is therefore evaluated once per call.
    """
    if not data_items or not outputs:
        return {}
    op_funcs = get_operation_funcs(output_dir)
    if not op_funcs:
        return {}
    import numpy as np  # CA's src is on sys.path by now; numpy is always bundled

    var2idx = {k: k for k in outputs}
    result: dict[int, list] = {}
    # (operation, operand keys, kwargs) already evaluated -> don't call the func
    # again; the first data_item to use it has decided whether it is plottable.
    called: set[tuple] = set()
    # operand keys -> series already emitted for them, so a repeat is recognised.
    emitted: dict[tuple, list] = {}
    for i, item in enumerate(data_items):
        if not isinstance(item, dict):
            continue
        op = item.get("operation")
        if not op or op not in op_funcs:
            continue
        if item.get("data_type") == "frequency":
            continue

        operands = item.get("operands") or []
        arrays = []
        keys = []
        resolved = True
        for name in operands:
            key = name if name in outputs else _resolve_output_key(var2idx, name)
            if key is None or key not in outputs:
                resolved = False
                break
            keys.append(key)
            arrays.append(np.asarray(outputs[key], dtype=float))
        if not resolved or not arrays:
            continue

        func = op_funcs[op]
        raw_kwargs = item.get("operation_kwargs")
        kwargs = dict(raw_kwargs) if isinstance(raw_kwargs, dict) else {}
        operand_key = tuple(keys)
        # repr() rather than a hash of the values: kwargs may hold unhashable
        # lists, and this only has to be stable within one call.
        call_key = (op, operand_key, repr(sorted(kwargs.items())))
        if call_key in called:
            continue  # same operation on the same operands -> same series
        called.add(call_key)
        try:
            if getattr(func, "series_to_constant", False):
                out = func(*arrays, series_output=True, **kwargs)
            else:
                out = func(*arrays, **kwargs)
        except Exception:  # noqa: BLE001 - bad kwargs / operand shape -> skip item
            continue

        arr = np.asarray(out, dtype=float)
        if arr.ndim != 1 or arr.size == 0:
            # A scalar (constant-only operation) or an unexpected shape: no series
            # to overlay, so the raw operand is plotted instead.
            continue
        if any(np.array_equal(arr, a) for a in arrays):
            # An identity series branch: the operand is already the plotted trace,
            # so overlaying it again just stacks a duplicate line on top of it.
            continue
        seen = emitted.setdefault(operand_key, [])
        if any(np.array_equal(arr, s) for s in seen):
            # A different operation, same transformed series -> one line, not two.
            continue
        seen.append(arr)
        result[i] = [float(v) for v in arr]
    return result
