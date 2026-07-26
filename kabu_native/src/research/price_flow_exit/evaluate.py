"""Walk-forward evaluation for Price-Flow EXIT."""
from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any, Optional, Sequence

from research.pbv2_zero_base_revalidation.metrics import pnl_metric_block
from research.price_flow_exit.constants import (
    CAPTURE_DAYS,
    EPSILON,
    MIN_OOS_DAYS_FOR_CANDIDATE,
    OOS_DAYS,
    WARMUP_DAY,
)
from research.price_flow_exit.entries import FixedEntry, load_push_day
from research.price_flow_exit.exit_rules import ExitParams, classify_abcd, fit_exit_params_train, simulate_exit
from research.price_flow_exit.path_mfe import (
    bars_after_entry,
    compute_executable_mfe,
    simulate_x0,
    _ret_5bps,
)
from research.volume_confirmed_impulse_entry.features import aggregate_to_seconds


MODES = ("X0", "X1", "X2", "X3", "X4", "X5", "X6")


def _stats(xs: Sequence[float]) -> dict[str, Any]:
    if not xs:
        return {"n": 0, "mean": None, "median": None}
    return {
        "n": len(xs),
        "mean": round(sum(xs) / len(xs), 4),
        "median": round(statistics.median(xs), 4),
    }


def evaluate_cohort(
    entries: Sequence[FixedEntry],
    push_by_day: dict[str, dict],
    *,
    params: ExitParams,
    modes: Sequence[str] = MODES,
) -> dict[str, Any]:
    rows = []
    for e in entries:
        ticks = push_by_day.get(e.day, {}).get(e.symbol) or []
        if not ticks:
            continue
        bars = aggregate_to_seconds(ticks)
        path = bars_after_entry(bars, e.entry_time)
        mfe = compute_executable_mfe(e, path)
        x0 = simulate_x0(e, path)
        # ABCD / capture use percent returns (not yen) vs executable_MFE_5bps
        actual_pct_5bps = _ret_5bps(e.entry_price, x0.exit_price)
        abcd = classify_abcd(mfe, actual_pct_5bps)
        rec: dict[str, Any] = {
            "day": e.day,
            "symbol": e.symbol,
            "entry_time": e.entry_time.isoformat(),
            "entry_price": e.entry_price,
            "entry_method": e.entry_method,
            "cohort": e.cohort,
            "pbv2": e.pbv2,
            "vcie": e.vcie,
            "breakout_level": e.breakout_level,
            "abcd": abcd.label,
            "capture_ratio": abcd.capture_ratio,
            "executable_mfe_5bps": mfe.mfe_5bps,
            "raw_mfe": mfe.raw_mfe,
            "quote_evaluable": mfe.quote_evaluable,
            "positive_duration": mfe.positive_duration,
            "break_even_duration": mfe.break_even_duration,
            "time_to_mfe": mfe.time_to_mfe,
            "setup_id": e.setup_id,
            "impulse_episode_id": e.impulse_episode_id,
            "breakout_episode_id": e.breakout_episode_id,
            "accept": e.accept,
            "actual_exit_reason": e.actual_exit_reason,
            "actual_pnl_5bps": e.actual_pnl_5bps,
        }
        for mode in modes:
            ex = simulate_exit(e, path, mode=mode, params=params) if mode != "X0" else x0
            rec[f"{mode}_pnl_5bps"] = ex.pnl_5bps
            rec[f"{mode}_reason"] = ex.exit_reason
            rec[f"{mode}_hold_sec"] = ex.hold_sec
            rec[f"{mode}_exit_time"] = ex.exit_time.isoformat()
            # early exit regret: max executable mfe after exit
            regret = None
            if mfe.mfe_5bps is not None:
                post = [b for b in path if b.t > ex.exit_time]
                if post and mfe.peak_bid:
                    # remaining upside vs exit
                    peak_after = None
                    for b in post:
                        if b.bid is not None:
                            peak_after = b.bid if peak_after is None else max(peak_after, b.bid)
                    if peak_after is not None and ex.exit_price > 0:
                        regret = (peak_after - ex.exit_price) / e.entry_price * 100.0
            rec[f"{mode}_early_exit_regret_pct"] = regret
        if mfe.mfe_5bps is not None and mfe.mfe_5bps > 0:
            rec["x0_capture_ratio"] = actual_pct_5bps / max(mfe.mfe_5bps, EPSILON)
        else:
            rec["x0_capture_ratio"] = None
        rec["actual_pct_5bps"] = actual_pct_5bps
        rows.append(rec)
    return {"rows": rows, "n": len(rows)}


def abcd_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    labels = ("A", "B", "C", "D1", "D2", "D3", "D4", "UNKNOWN")
    counts = {k: 0 for k in labels}
    pnl = {k: 0.0 for k in labels}
    for r in rows:
        lab = r.get("abcd") or "UNKNOWN"
        if lab not in counts:
            lab = "UNKNOWN"
        counts[lab] += 1
        pnl[lab] += float(r.get("X0_pnl_5bps") or 0)
    n = max(1, len(rows))
    d_low = counts["D1"] + counts["D2"]
    c = counts["C"]
    a_b = counts["A"] + counts["B"]
    return {
        "counts": counts,
        "ratios": {k: round(v / n, 4) for k, v in counts.items()},
        "pnl_5bps": {k: round(v, 2) for k, v in pnl.items()},
        "entry_unrecoverable_ratio": round(a_b / n, 4),
        "exit_improvable_ratio": round((c + d_low) / n, 4),
        "C_ratio": round(c / n, 4),
        "D1D2_ratio": round(d_low / n, 4),
        "n": len(rows),
    }


def mode_metrics(rows: Sequence[dict[str, Any]], mode: str) -> dict[str, Any]:
    y5 = [float(r.get(f"{mode}_pnl_5bps") or 0) for r in rows]
    yraw = y5[:]  # already 5bps yen from util
    block = pnl_metric_block(yraw, y5)
    stops = sum(1 for r in rows if "stop" in str(r.get(f"{mode}_reason") or "").lower())
    nps = sum(1 for r in rows if "no_progress" in str(r.get(f"{mode}_reason") or "").lower())
    holds = [float(r.get(f"{mode}_hold_sec") or 0) for r in rows]
    regrets = [float(r[f"{mode}_early_exit_regret_pct"]) for r in rows if r.get(f"{mode}_early_exit_regret_pct") is not None]
    by_day = defaultdict(float)
    for r, y in zip(rows, y5):
        by_day[r["day"]] += y
    pos = sum(1 for v in by_day.values() if v > 0)
    neg = sum(1 for v in by_day.values() if v < 0)
    # max dd on cumulative day order
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for d in sorted(by_day):
        cum += by_day[d]
        peak = max(peak, cum)
        max_dd = min(max_dd, cum - peak)
    early_stop = sum(
        1
        for r in rows
        if "stop" in str(r.get(f"{mode}_reason") or "").lower() and float(r.get(f"{mode}_hold_sec") or 9999) <= 300
    )
    return {
        **block,
        "stop_rate": round(stops / max(1, len(rows)), 4),
        "early_stop_rate": round(early_stop / max(1, len(rows)), 4),
        "np_rate": round(nps / max(1, len(rows)), 4),
        "avg_hold_sec": round(sum(holds) / max(1, len(holds)), 2) if holds else None,
        "median_hold_sec": round(statistics.median(holds), 2) if holds else None,
        "mean_early_exit_regret_pct": round(sum(regrets) / len(regrets), 4) if regrets else None,
        "pos_days": pos,
        "neg_days": neg,
        "max_dd_5bps": round(max_dd, 2),
        "daily": [{"day": d, "pnl_5bps": round(by_day[d], 2)} for d in sorted(by_day)],
    }


def baseline_parity(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Compare X0 sim vs actual exits on accepted paper trades."""
    matched = [r for r in rows if r.get("accept") and r.get("actual_exit_reason")]
    if not matched:
        return {
            "n_accept_with_actual": 0,
            "reason_match_rate": None,
            "gate_ok": False,
            "verdict": "EXIT_BASELINE_REPRODUCTION_BLOCKED",
            "note": "no accepted paper trades with actual_exit_reason in capture-day cohort",
        }
    reason_ok = 0
    for r in matched:
        act = str(r.get("actual_exit_reason") or "").lower()
        sim = str(r.get("X0_reason") or "").lower()
        # coarse match families
        if ("stop" in act and "stop" in sim) or ("trail" in act and "trail" in sim) or (
            "no_progress" in act and "no_progress" in sim
        ) or ("session" in act and "session" in sim) or (act and act in sim):
            reason_ok += 1
    rate = reason_ok / len(matched)
    ok = rate >= 0.40  # tolerant: path/clock differences on PUSH vs runtime ticks
    return {
        "n_accept_with_actual": len(matched),
        "reason_match_rate": round(rate, 4),
        "gate_ok": ok,
        "verdict": "EXIT_BASELINE_REPRODUCED" if ok else "EXIT_BASELINE_REPRODUCTION_BLOCKED",
        "note": "parity uses family match on stop/trail/np/session; PUSH path ≠ live tick stream",
    }


def run_evaluation(cohorts: dict[str, list[FixedEntry]]) -> dict[str, Any]:
    push_by_day = {d: load_push_day(d) for d in CAPTURE_DAYS}
    # train ABCD on warmup for param fit
    e0_warm = [e for e in cohorts["E0"] if e.day == WARMUP_DAY]
    warm_eval = evaluate_cohort(e0_warm, push_by_day, params=ExitParams())
    params = fit_exit_params_train(warm_eval["rows"])

    out_cohorts = {}
    for name, ents in cohorts.items():
        print(f"[pfe] evaluate {name} n={len(ents)}…", flush=True)
        full = evaluate_cohort(ents, push_by_day, params=params)
        oos_rows = [r for r in full["rows"] if r["day"] in OOS_DAYS]
        warm_rows = [r for r in full["rows"] if r["day"] == WARMUP_DAY]
        abcd = abcd_summary(oos_rows if oos_rows else full["rows"])
        modes = {m: mode_metrics(oos_rows, m) for m in MODES} if oos_rows else {m: mode_metrics(full["rows"], m) for m in MODES}
        best = max(modes.items(), key=lambda kv: float(kv[1].get("total_pnl_5bps") or -1e18))
        out_cohorts[name] = {
            "n_total": full["n"],
            "n_oos": len(oos_rows),
            "n_warmup": len(warm_rows),
            "abcd": abcd,
            "modes": modes,
            "best_mode": best[0],
            "mfe_stats": _stats([float(r["executable_mfe_5bps"]) for r in oos_rows if r.get("executable_mfe_5bps") is not None]),
            "pnl_stats": _stats([float(r["X0_pnl_5bps"]) for r in oos_rows if r.get("X0_pnl_5bps") is not None]),
            "capture_stats": _stats([float(r["x0_capture_ratio"]) for r in oos_rows if r.get("x0_capture_ratio") is not None]),
            "sample_rows": oos_rows[:200],
            "parity": baseline_parity([r for r in full["rows"] if r["day"] in CAPTURE_DAYS]),
        }

    # bottleneck verdict from E0 OOS ABCD + capture ratio
    e0a = out_cohorts["E0"]["abcd"]
    cap_med = out_cohorts["E0"]["capture_stats"].get("median")
    if (e0a["C_ratio"] >= 0.15) or (e0a["D1D2_ratio"] >= 0.20) or (cap_med is not None and cap_med < 0.5):
        if e0a["entry_unrecoverable_ratio"] >= 0.80 and e0a["exit_improvable_ratio"] < 0.15:
            bottleneck = "ENTRY_BOTTLENECK_CONFIRMED"
        elif e0a["entry_unrecoverable_ratio"] >= 0.50:
            bottleneck = "MIXED_ENTRY_EXIT_BOTTLENECK"
        else:
            bottleneck = "EXIT_BOTTLENECK_CONFIRMED"
    else:
        bottleneck = (
            "ENTRY_BOTTLENECK_CONFIRMED"
            if e0a["entry_unrecoverable_ratio"] >= 0.80
            else "MIXED_ENTRY_EXIT_BOTTLENECK"
        )

    return {
        "warmup_day": WARMUP_DAY,
        "oos_days": list(OOS_DAYS),
        "capture_days": list(CAPTURE_DAYS),
        "params": params.__dict__,
        "cohorts": out_cohorts,
        "bottleneck": bottleneck,
        "insufficient_oos": len(OOS_DAYS) < MIN_OOS_DAYS_FOR_CANDIDATE,
    }
