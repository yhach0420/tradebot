"""Frozen-index inference for one hypothesis. No new bootstrap draws and no synthetic p."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.causal_driver_pb1.phase2_discovery.clock import CLOCK_MINS, N_CLOCK
from research.causal_driver_pb1.phase2_discovery.infer import point_b_fx
from research.causal_driver_pb1.sector_breadth_discovery import BOOTSTRAP_N
from research.causal_driver_pb1.sector_breadth_discovery.infer import fit_frozen


def bh_monotone(p: np.ndarray) -> np.ndarray:
    arr = np.asarray(p, dtype=np.float64)
    if arr.ndim != 1 or not np.all(np.isfinite(arr)):
        raise ValueError("bh_requires_finite_p")
    m = int(arr.size)
    order = np.lexsort((np.arange(m, dtype=np.int64), arr))
    q = np.empty(m, dtype=np.float64)
    prev = 1.0
    for rank in range(m, 0, -1):
        i = int(order[rank - 1])
        prev = min(prev, float(arr[i]) * m / rank)
        q[i] = prev
    return np.clip(q, 0.0, 1.0)


def _flat_pack(y: np.ndarray, cols: list[np.ndarray], date_ok: np.ndarray | None = None) -> dict[str, Any] | None:
    finite = np.isfinite(y)
    for col in cols:
        finite = finite & np.isfinite(col)
    if date_ok is not None:
        finite = finite & date_ok[:, None]
    n = int(finite.sum())
    if n < 40:
        return None
    date_ids = np.repeat(np.arange(y.shape[0], dtype=np.int32), N_CLOCK)[finite.reshape(-1)]
    minute_ids = np.tile(np.arange(N_CLOCK, dtype=np.int32), y.shape[0])[finite.reshape(-1)]
    mod5 = np.tile(np.array([int(t) % 5 for t in CLOCK_MINS], dtype=np.int32), y.shape[0])[finite.reshape(-1)]
    xcols = [col.reshape(-1)[finite.reshape(-1)] for col in cols]
    return {
        "y": y.reshape(-1)[finite.reshape(-1)],
        "cols": xcols,
        "date_ids": date_ids,
        "minute_ids": minute_ids,
        "mod5": mod5,
        "n": n,
    }


def fit_hypothesis(
    *,
    y: np.ndarray,
    y60: np.ndarray,
    cols: list[np.ndarray],
    months: np.ndarray,
    n_dates: int,
    boot_index: np.ndarray,
    extra_date_masks: dict[str, np.ndarray],
) -> dict[str, Any]:
    packed = _flat_pack(y, cols)
    if packed is None:
        return {"ok": False, "reason": "primary_rows"}
    if boot_index.shape != (int(BOOTSTRAP_N), int(n_dates)):
        return {"ok": False, "reason": "bootstrap_index_shape", "primary_row_n": packed["n"]}
    rec = fit_frozen(
        packed["y"],
        packed["cols"][0],
        packed["cols"][1:],
        packed["date_ids"],
        packed["minute_ids"],
        n_dates,
        boot_index,
    )
    if not rec.get("ok"):
        return {"ok": False, "reason": rec.get("reason") or "bootstrap", "primary_row_n": packed["n"]}
    extra = {name: _subset_beta(packed, mask) for name, mask in extra_date_masks.items()}
    mod_pos = 0
    for r in range(5):
        beta = _subset_beta(packed, None, minute_value=r)
        if beta is not None and beta > 0:
            mod_pos += 1
    month_ids = months[packed["date_ids"]]
    uniq = sorted(set(int(x) for x in month_ids))
    lomo_pos = 0
    for mi in uniq:
        beta = _subset_beta_ids(packed, month_ids != mi)
        if beta is not None and beta > 0:
            lomo_pos += 1
    lomo_frac = (lomo_pos / len(uniq)) if uniq else None
    strict = _flat_pack(y60, cols)
    if strict is None:
        strict_rec = {"ok": False, "n": 0}
    else:
        strict_rec = fit_frozen(
            strict["y"],
            strict["cols"][0],
            strict["cols"][1:],
            strict["date_ids"],
            strict["minute_ids"],
            n_dates,
            boot_index,
        )
        strict_rec["n"] = strict["n"]
    return {
        "ok": True,
        "primary_row_n": packed["n"],
        "strict60_row_n": int(strict_rec.get("n") or 0),
        "beta_pooled": float(rec["b_fx"]),
        "ci_lo": float(rec["ci_lo"]),
        "ci_hi": float(rec["ci_hi"]),
        "p": float(rec["p_boot"]),
        **{name: (None if beta is None else float(beta)) for name, beta in extra.items()},
        "minute_mod5_positive_n": int(mod_pos),
        "lomo_positive_fraction": None if lomo_frac is None else float(lomo_frac),
        "lomo_month_n": len(uniq),
        "beta_60": None if not strict_rec.get("ok") else float(strict_rec["b_fx"]),
        "ci60_lo": None if not strict_rec.get("ok") else float(strict_rec["ci_lo"]),
        "ci60_hi": None if not strict_rec.get("ok") else float(strict_rec["ci_hi"]),
        "strict_ok": bool(strict_rec.get("ok")),
    }


def _subset_beta(packed: dict[str, Any], date_mask: np.ndarray | None, minute_value: int | None = None) -> float | None:
    if minute_value is None:
        assert date_mask is not None
        keep = date_mask[packed["date_ids"]]
    else:
        keep = packed["mod5"] == int(minute_value)
    return _subset_beta_ids(packed, keep)


def _subset_beta_ids(packed: dict[str, Any], keep: np.ndarray) -> float | None:
    if int(keep.sum()) < 40:
        return None
    return point_b_fx(
        packed["y"][keep],
        packed["cols"][0][keep],
        [c[keep] for c in packed["cols"][1:]],
        packed["minute_ids"][keep],
    )


def gate_discovery(row: dict[str, Any], q: float) -> dict[str, Any]:
    bdev = row.get("beta_dev")
    bc1 = row.get("beta_c1")
    t1 = bool(bdev is not None and bc1 is not None and bdev > 0 and bc1 > 0)
    t2 = bool(row.get("ci_lo") is not None and float(row["ci_lo"]) > 0.0)
    t3 = bool(q <= 0.05)
    t4 = int(row.get("minute_mod5_positive_n") or 0) >= 4
    frac = row.get("lomo_positive_fraction")
    t5 = bool(frac is not None and float(frac) >= 0.75)
    b60 = row.get("beta_60")
    c60 = row.get("ci60_lo")
    bp = row.get("beta_pooled")
    t6 = bool(
        b60 is not None
        and c60 is not None
        and bp is not None
        and b60 > 0
        and float(c60) > 0.0
        and b60 >= 0.50 * float(bp)
    )
    flags = {"T1": t1, "T2": t2, "T3": t3, "T4": t4, "T5": t5, "T6": t6}
    first = next((k for k in ("T1", "T2", "T3", "T4", "T5", "T6") if not flags[k]), None)
    flags["discovery_candidate"] = first is None
    flags["first_fail"] = first
    flags["q"] = float(q)
    return flags


def gate_fv(row: dict[str, Any], q: float) -> dict[str, Any]:
    folds = [row.get("beta_early"), row.get("beta_middle"), row.get("beta_late")]
    pos = sum(1 for b in folds if b is not None and b > 0)
    b60 = row.get("beta_60")
    c60 = row.get("ci60_lo")
    bp = row.get("beta_fv")
    flags = {
        "F1": bool(bp is not None and bp > 0),
        "F2": bool(row.get("ci_lo") is not None and float(row["ci_lo"]) > 0.0),
        "F3": pos >= 2,
        "F4": bool(row.get("lomo_positive_fraction") is not None and float(row["lomo_positive_fraction"]) >= 0.75),
        "F5": bool(b60 is not None and c60 is not None and bp is not None and b60 > 0 and float(c60) > 0.0 and b60 >= 0.50 * float(bp)),
        "F6": bool(q <= 0.05),
    }
    flags["fv_confirmed"] = all(flags[k] for k in ("F1", "F2", "F3", "F4", "F5", "F6"))
    flags["q"] = float(q)
    flags["first_fail"] = next((k for k in ("F1", "F2", "F3", "F4", "F5", "F6") if not flags[k]), None)
    return flags
