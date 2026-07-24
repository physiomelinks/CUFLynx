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
    """
    if not data_items or not outputs:
        return {}
    op_funcs = get_operation_funcs(output_dir)
    if not op_funcs:
        return {}
    import numpy as np  # CA's src is on sys.path by now; numpy is always bundled

    var2idx = {k: k for k in outputs}
    result: dict[int, list] = {}
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
        resolved = True
        for name in operands:
            key = name if name in outputs else _resolve_output_key(var2idx, name)
            if key is None or key not in outputs:
                resolved = False
                break
            arrays.append(np.asarray(outputs[key], dtype=float))
        if not resolved or not arrays:
            continue

        func = op_funcs[op]
        raw_kwargs = item.get("operation_kwargs")
        kwargs = dict(raw_kwargs) if isinstance(raw_kwargs, dict) else {}
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
        result[i] = [float(v) for v in arr]
    return result
