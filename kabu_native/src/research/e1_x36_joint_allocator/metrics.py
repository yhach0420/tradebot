"""Economics, diagnostics, concentration, LODO/LOSO for joint replay."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from . import LOT_QTY, MAX_DAY_CONTRIB, MAX_SYMBOL_CONTRIB
from .panel import pnl_yen


def summarize_replay(sim: dict[str, Any]) -> dict[str, Any]:
    events = sim["events"]
    n = len(events)
    admitted = [e for e in events if e.get("admitted")]
    accepted = [e for e in events if e.get("accepted")]
    blocked = [e for e in events if e.get("CAPACITY_BLOCKED")]
    dup = [e for e in events if e.get("DUPLICATE_BLOCKED")]

    # opportunity series: accepted fill ret else 0 (signal denominator)
    opp = []
    for e in events:
        if e.get("accepted") and e.get("canonical_exit_ret_bps") is not None:
            opp.append(float(e["canonical_exit_ret_bps"]))
        else:
            opp.append(0.0)

    pnls = [float(e.get("realized_pnl_yen") or 0.0) for e in events]
    total_pnl = float(sum(pnls))

    by_day_opp: dict[str, list[float]] = defaultdict(list)
    by_day_pnl: dict[str, list[float]] = defaultdict(list)
    by_ss: dict[tuple, list[float]] = defaultdict(list)
    for e, v in zip(events, opp):
        by_day_opp[e["date"]].append(v)
        by_day_pnl[e["date"]].append(float(e.get("realized_pnl_yen") or 0.0))
        by_ss[(e["date"], e["symbol"], e["session"])].append(v)

    day_means = {d: float(np.mean(v)) for d, v in by_day_opp.items()}
    day_pnls = {d: float(sum(v)) for d, v in by_day_pnl.items()}
    ss_means = [float(np.mean(v)) for v in by_ss.values()]

    fill_rets = [float(e["canonical_exit_ret_bps"]) for e in accepted if e.get("canonical_exit_ret_bps") is not None]
    pos = sum(x for x in opp if x > 0)
    neg = sum(x for x in opp if x < 0)
    pf = float(pos / abs(neg)) if abs(neg) > 1e-12 else None

    # concentration on positive PnL
    by_sym_pnl: dict[str, float] = defaultdict(float)
    tot_pos_pnl = 0.0
    for e in accepted:
        p = float(e.get("realized_pnl_yen") or 0.0)
        if p > 0:
            by_sym_pnl[e["symbol"]] += p
            tot_pos_pnl += p
    top_sym_share = float(max(by_sym_pnl.values()) / tot_pos_pnl) if tot_pos_pnl > 1e-12 and by_sym_pnl else None
    top5 = sorted(by_sym_pnl.items(), key=lambda x: -x[1])[:5]

    tot_pos_day = sum(v for v in day_pnls.values() if v > 0)
    top_day_share = None
    top2_days = []
    if tot_pos_day > 1e-12:
        ranked_days = sorted(day_pnls.items(), key=lambda x: -x[1])
        top2_days = ranked_days[:2]
        if ranked_days[0][1] > 0:
            top_day_share = float(ranked_days[0][1] / tot_pos_day)

    slot_min = float(sim.get("occupied_slot_sec") or 0.0) / 60.0
    return {
        "signals": n,
        "admitted": len(admitted),
        "blocked": len(blocked),
        "duplicate_blocked": len(dup),
        "fills": len(accepted),
        "fill_rate_admitted": float(len(accepted) / len(admitted)) if admitted else None,
        "fill_rate_signal": float(len(accepted) / n) if n else None,
        "expired": sum(1 for e in events if e.get("expired")),
        "total_pnl_yen": total_pnl,
        "pnl_per_day": float(np.mean(list(day_pnls.values()))) if day_pnls else None,
        "day_pnls": day_pnls,
        "opp_bps_per_signal": float(np.mean(opp)) if opp else None,
        "bps_per_admitted": float(np.mean([
            float(e.get("realized_ret_bps") or 0.0) for e in admitted
        ])) if admitted else None,
        "bps_per_fill": float(np.mean(fill_rets)) if fill_rets else None,
        "pf": pf,
        "positive_days": sum(1 for v in day_means.values() if v > 0),
        "negative_days": sum(1 for v in day_means.values() if v < 0),
        "n_days": len(day_means),
        "day_means_opp": day_means,
        "ss_balanced": float(np.mean(ss_means)) if ss_means else None,
        "day_balanced": float(np.mean(list(day_means.values()))) if day_means else None,
        "slot_utilization_fills": float(len(accepted) / max(1, sim.get("max_open_plus_pending") or 1)),
        "pnl_per_occupied_slot_minute": float(total_pnl / slot_min) if slot_min > 1e-9 else None,
        "pnl_per_fill": float(total_pnl / len(accepted)) if accepted else None,
        "pnl_per_admitted": float(total_pnl / len(admitted)) if admitted else None,
        "fills_per_day": float(len(accepted) / max(1, len(day_means))),
        "hard_cap_violations": sim.get("hard_cap_violations"),
        "max_open_plus_pending": sim.get("max_open_plus_pending"),
        "max_symbol_contrib_share": top_sym_share,
        "max_day_contrib_share": top_day_share,
        "top2_days": top2_days,
        "top5_symbols": top5,
        "severe_symbol_concentration": bool(top_sym_share is not None and top_sym_share > MAX_SYMBOL_CONTRIB),
        "severe_day_concentration": bool(top_day_share is not None and top_day_share > MAX_DAY_CONTRIB),
        "capital": {
            "max_concurrent_notional_yen": sim.get("max_concurrent_notional_yen"),
            "p95_concurrent_notional_yen": sim.get("p95_concurrent_notional_yen"),
            "max_pending_reserved_notional_yen": sim.get("max_pending_reserved_notional_yen"),
        },
        "occupied_slot_sec": sim.get("occupied_slot_sec"),
    }


def fill_return_decomposition(learned: dict, baseline: dict, events_l: list, events_b: list) -> dict[str, Any]:
    """Decompose learned vs baseline into fillability vs conditional-return."""
    fill_l = learned.get("fill_rate_admitted")
    fill_b = baseline.get("fill_rate_admitted")
    ret_l = learned.get("bps_per_fill")
    ret_b = baseline.get("bps_per_fill")
    pnl_l = learned.get("total_pnl_yen") or 0.0
    pnl_b = baseline.get("total_pnl_yen") or 0.0
    return {
        "delta_total_pnl_yen": float(pnl_l - pnl_b),
        "delta_fill_rate_admitted": (
            None if fill_l is None or fill_b is None else float(fill_l - fill_b)
        ),
        "delta_bps_per_fill": (
            None if ret_l is None or ret_b is None else float(ret_l - ret_b)
        ),
        "fillability_gain": bool(fill_l is not None and fill_b is not None and fill_l > fill_b + 1e-9),
        "conditional_return_gain": bool(ret_l is not None and ret_b is not None and ret_l > ret_b + 1e-9),
        "both": bool(
            fill_l is not None and fill_b is not None and fill_l > fill_b + 1e-9
            and ret_l is not None and ret_b is not None and ret_l > ret_b + 1e-9
        ),
    }


def score_quintile_diag(events: list[dict], *, label_key: str) -> list[dict[str, Any]]:
    scored = [e for e in events if e.get("alloc_score") is not None and np.isfinite(e["alloc_score"])]
    if len(scored) < 20:
        return []
    scores = np.asarray([float(e["alloc_score"]) for e in scored], dtype=float)
    qs = np.quantile(scores, [0.2, 0.4, 0.6, 0.8])
    out = []
    for i in range(5):
        lo = float("-inf") if i == 0 else qs[i - 1]
        hi = float("inf") if i == 4 else qs[i]
        bucket = [e for e in scored if lo - 1e-15 <= float(e["alloc_score"]) <= hi + 1e-15]
        if i < 4:
            bucket = [e for e in scored if float(e["alloc_score"]) >= lo - 1e-15 and float(e["alloc_score"]) < hi + 1e-15]
        vals = [float(e.get(label_key) or 0.0) for e in bucket]
        fills = [e for e in bucket if e.get("FILL_1S") == 1]
        frets = [float(e["FIXED600_NET_BPS"]) for e in fills if e.get("FIXED600_NET_BPS") is not None]
        out.append({
            "quintile": i + 1,
            "n": len(bucket),
            "mean_score": float(np.mean([float(e["alloc_score"]) for e in bucket])) if bucket else None,
            "fill_rate": float(len(fills) / len(bucket)) if bucket else None,
            "mean_opp": float(np.mean(vals)) if vals else None,
            "mean_fill_ret": float(np.mean(frets)) if frets else None,
            "fill_pos_rate": float(sum(1 for x in frets if x > 0) / len(frets)) if frets else None,
        })
    return out


def lodo_from_day_means(day_means: dict[str, float]) -> dict[str, Any]:
    pos = sum(1 for v in day_means.values() if v > 0)
    return {
        "n_folds": len(day_means),
        "positive_holdout_days": pos,
        "majority_positive": pos > len(day_means) / 2.0 if day_means else False,
    }


def loso_sensitivity(events: list[dict], *, max_symbols: int = 40) -> dict[str, Any]:
    counts: dict[str, int] = defaultdict(int)
    for e in events:
        if e.get("accepted"):
            counts[e["symbol"]] += 1
    top = [s for s, _ in sorted(counts.items(), key=lambda x: -x[1])[:max_symbols]]
    folds = []
    for hold in top:
        sub = [e for e in events if e["symbol"] != hold]
        opp = [
            float(e["canonical_exit_ret_bps"]) if e.get("accepted") and e.get("canonical_exit_ret_bps") is not None else 0.0
            for e in sub
        ]
        folds.append({"holdout_symbol": hold, "opp_bps": float(np.mean(opp)) if opp else None})
    pos = sum(1 for f in folds if (f.get("opp_bps") or 0) > 0)
    return {
        "n_folds": len(folds),
        "positive_folds": pos,
        "majority_positive": pos > len(folds) / 2.0 if folds else False,
        "sample": folds[:12],
    }
