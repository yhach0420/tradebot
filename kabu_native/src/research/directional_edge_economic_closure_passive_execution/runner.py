"""DEECPA runner — economic closure + passive execution audit."""
from __future__ import annotations

import pickle
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

from research.continuous_directional_vs_execution_edge.labels import tick_size_jpy
from research.continuous_directional_vs_execution_edge.scoring import (
    _score_samples,
    eval_dir_fixed,
    exec_selected_metrics,
    fit_dir_candidate,
)
from research.directional_edge_economic_closure_passive_execution.constants import (
    ARMS,
    CANCEL,
    COST_BPS,
    COST_RATE,
    ENRICHED_CACHE,
    FIXED_CANDIDATE,
    FIXED_HID,
    FIXED_LABEL,
    FIXED_THRESHOLD,
    HOLD_DAYS,
    LIVE_ORDER,
    LOT,
    OUT_ROOT,
    PRIMARY_HORIZON_SEC,
    REPRO_ABS_TOL,
    SOURCE_CDEED,
    STRIDE,
    SUBMIT,
    TRAIN_DAYS,
    VAL_DAYS,
)
from research.directional_edge_economic_closure_passive_execution.economics import (
    by_day,
    by_symbol,
    dependence,
    legacy_yen_from_cadj_bps,
    net_pnl_yen_100,
    notional_bands,
    price_bands,
    summarize_trades,
)
from research.directional_edge_economic_closure_passive_execution.execution import (
    immediate_cross_trade,
    simulate_passive_arm,
)
from research.directional_edge_economic_closure_passive_execution.reporting import emit
from research.upward_edge_identification_audit.loader import load_streams

JST = ZoneInfo("Asia/Tokyo")

EXPECT = {
    "s1_train": 9408,
    "s1_val": 4011,
    "n_sel_train": 942,
    "n_sel_val": 568,
    "auc_train": 0.6464730524423923,
    "auc_val": 0.6075517248913067,
    "mid_train": 20.253402695429727,
    "mid_val": 9.793374318536651,
    "mfe_mae_train": 3.495536396422987,
    "mfe_mae_val": 2.0681225105140526,
    "exec_h60": -28.57474359130176,
    "exec_h180": -28.17291382681275,
    "exec_h300": -22.377793077280025,
    "threshold": FIXED_THRESHOLD,
}


def _close(a: Optional[float], b: Optional[float], tol: float = REPRO_ABS_TOL) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= tol


def _spread_bps(s) -> float:
    if getattr(s, "_spread_bps", None) is not None:
        return float(s._spread_bps)
    if s.spread_bps is not None:
        return float(s.spread_bps)
    return (s.entry_ask - s.entry_bid) / s.entry_ask * 10000.0


def _spread_ticks(s) -> float:
    if getattr(s, "_spread_ticks", None) is not None:
        return float(s._spread_ticks)
    return (s.entry_ask - s.entry_bid) / tick_size_jpy(s.entry_ask)


def _cohort_mask(name: str, s) -> bool:
    spr = _spread_bps(s)
    ticks = _spread_ticks(s)
    if name == "C0":
        return True
    if name == "C1":
        return spr < 15.0
    if name == "C2":
        return ticks <= 1.0 + 1e-9
    if name == "C3":
        return ticks <= 2.0 + 1e-9
    if name == "C4":
        return spr <= 5.0 + 1e-9
    if name == "C5":
        return spr <= 10.0 + 1e-9
    return False


def _mfe_mae(trades: Sequence[dict]) -> Optional[float]:
    mfes = [t["mfe_bps"] for t in trades if t.get("mfe_bps") is not None]
    maes = [t["mae_bps"] for t in trades if t.get("mae_bps") is not None]
    if not mfes or not maes:
        return None
    avg_mfe = sum(mfes) / len(mfes)
    avg_abs_mae = sum(abs(x) for x in maes) / len(maes)
    if avg_abs_mae == 0:
        return None
    return avg_mfe / avg_abs_mae


def _filled_summary(trades: Sequence[dict]) -> dict[str, Any]:
    fills = [t for t in trades if (t.get("filled_qty") or 0) > 0 and t.get("status") not in ("NO_FILL", "DATA_END", "STALE_BLOCKED", "DATA_END_SESSION_BOUNDARY")]
    # also allow FULL/PARTIAL with exit
    fills = [t for t in trades if (t.get("filled_qty") or 0) > 0 and t.get("exit_price") is not None]
    base = summarize_trades(trades)
    fill_sum = summarize_trades(fills) if fills else summarize_trades([])
    return {
        **base,
        "avg_return_bps_filled": fill_sum.get("avg_return_bps"),
        "profit_factor_bps_filled": fill_sum.get("profit_factor_bps"),
        "mfe_mae": _mfe_mae(fills),
        "partial_fill_rate": (base["partial_fills"] / base["signals"]) if base["signals"] else None,
        "no_fill_rate": (base["no_fills"] / base["signals"]) if base["signals"] else None,
    }


def _extreme_symbol(trades: Sequence[dict], share_cap: float = 0.50) -> bool:
    dep = dependence(trades)
    share = dep.get("top1_symbol_share")
    return share is not None and share >= share_cap


def _arm_train_pass(summary: dict, trades: Sequence[dict], train_days: list[str]) -> tuple[bool, list[str]]:
    reasons = []
    ok = True
    if (summary.get("total_pnl_yen_100") or 0) <= 0:
        ok = False
        reasons.append("total_pnl<=0")
    if (summary.get("per_signal_pnl_yen") or 0) <= 0:
        ok = False
        reasons.append("per_signal<=0")
    if (summary.get("profit_factor_yen_100") or 0) <= 1:
        ok = False
        reasons.append("PF_yen<=1")
    bps = summary.get("avg_return_bps_filled")
    if bps is None:
        bps = summary.get("avg_return_bps")
    if (bps or 0) <= 0:
        ok = False
        reasons.append("avg_bps<=0")
    if (summary.get("fills") or 0) < 100:
        ok = False
        reasons.append("fills<100")
    by = by_day(trades)
    for d in train_days:
        if (by.get(d) or {}).get("total_pnl_yen_100", 0) <= 0:
            ok = False
            reasons.append(f"day_{d}_nonpos")
    dep = dependence([t for t in trades if (t.get("filled_qty") or 0) > 0])
    if (dep.get("top1_symbol_share") or 0) >= 0.30:
        ok = False
        reasons.append("top1_symbol>=30%")
    if (dep.get("top3_symbol_share") or 0) >= 0.60:
        ok = False
        reasons.append("top3_symbol>=60%")
    return ok, reasons


def _arm_val_pass(summary: dict, trades: Sequence[dict]) -> tuple[bool, list[str]]:
    reasons = []
    ok = True
    if (summary.get("total_pnl_yen_100") or 0) <= 0:
        ok = False
        reasons.append("total_pnl<=0")
    if (summary.get("per_signal_pnl_yen") or 0) <= 0:
        ok = False
        reasons.append("per_signal<=0")
    if (summary.get("profit_factor_yen_100") or 0) <= 1:
        ok = False
        reasons.append("PF_yen<=1")
    bps = summary.get("avg_return_bps_filled")
    if bps is None:
        bps = summary.get("avg_return_bps")
    if (bps or 0) <= 0:
        ok = False
        reasons.append("avg_bps<=0")
    if (summary.get("fills") or 0) < 50:
        ok = False
        reasons.append("fills<50")
    if _extreme_symbol(trades, 0.50):
        ok = False
        reasons.append("extreme_symbol")
    return ok, reasons


def _run_arm(samples, streams, arm: str) -> list[dict]:
    out = []
    for s in samples:
        ticks = streams.get(s.stream_key)
        if not ticks:
            out.append({
                "day": s.day, "symbol": s.symbol, "sample_id": s.sample_id, "arm": arm,
                "status": "STALE_BLOCKED", "filled_qty": 0, "qty": 0,
                "net_pnl_yen_100": 0.0, "net_return_bps": 0.0, "entry_notional_yen": 0.0,
                "entry_price": s.entry_ask, "exit_price": None, "spread_bps": _spread_bps(s),
            })
            continue
        if arm == "E0":
            out.append(immediate_cross_trade(s, ticks, PRIMARY_HORIZON_SEC))
        else:
            out.append(simulate_passive_arm(s, ticks, arm))
    return out


def _flatten_daily(split: str, arm: str, trades: Sequence[dict]) -> list[dict]:
    rows = []
    for d, sm in by_day(trades).items():
        rows.append({"split": split, "arm": arm, "day": d, **sm})
    return rows


def _flatten_symbols(split: str, arm: str, trades: Sequence[dict]) -> list[dict]:
    rows = []
    for sym, sm in by_symbol(trades, top=50).items():
        # attach avg entry
        sub = [t for t in trades if t.get("symbol") == sym]
        avg_px = sum(t.get("entry_price") or 0 for t in sub) / len(sub) if sub else None
        avg_spr = sum(t.get("spread_bps") or 0 for t in sub) / len(sub) if sub else None
        rows.append({
            "split": split, "arm": arm, "symbol": sym,
            "avg_entry_price": avg_px, "avg_spread_bps": avg_spr, **sm,
        })
    return rows


def manual_yen_audit(train_samples: Sequence, n: int = 25) -> dict[str, Any]:
    """Audit CDEED C4_FIVE_BPS (TRAIN pop, h60): cost_adj≈−1.8bps vs avg_yen_100≈+710."""
    c4 = [s for s in train_samples if _cohort_mask("C4", s)]
    checks = []
    for s in c4[: max(n, 20)]:
        ex = (getattr(s, "execution", None) or {}).get("h60") or {}
        cadj = ex.get("cost_adj_bps")
        yen_legacy = ex.get("yen_100")
        ask = float(s.entry_ask)
        if cadj is None:
            continue
        yen_recalc = legacy_yen_from_cadj_bps(cadj, ask)
        term = ex.get("terminal_bps")
        if term is not None:
            exit_proxy = ask * (1.0 + term / 10000.0)
            true_econ = net_pnl_yen_100(ask, exit_proxy, LOT)
        else:
            true_econ = None
        same_trade_sign = (cadj >= 0) == (yen_recalc >= 0)
        checks.append({
            "sample_id": s.sample_id, "symbol": s.symbol, "entry_ask": ask,
            "cost_adj_bps": cadj, "yen_100_stored": yen_legacy,
            "yen_100_recalc": yen_recalc,
            "abs_err": abs((yen_legacy or 0) - yen_recalc) if yen_legacy is not None else None,
            "true_net_pnl_yen_100": None if true_econ is None else true_econ["net_pnl_yen_100"],
            "true_net_bps": None if true_econ is None else true_econ["net_return_bps"],
            "tick_size": tick_size_jpy(ask),
            "spread_bps": _spread_bps(s),
            "same_trade_sign": same_trade_sign,
            "notional_100": ask * LOT,
        })
    rows = []
    for s in c4:
        ex = (getattr(s, "execution", None) or {}).get("h60") or {}
        if ex.get("cost_adj_bps") is None:
            continue
        rows.append({**ex, "ask": float(s.entry_ask), "symbol": s.symbol})
    avg_bps = sum(r["cost_adj_bps"] for r in rows) / len(rows) if rows else None
    avg_yen = sum(r.get("yen_100") or 0 for r in rows) / len(rows) if rows else None
    # price-band contribution
    hi = [r for r in rows if r["ask"] >= 10000]
    lo = [r for r in rows if r["ask"] < 1000]
    formula_ok = all((c.get("abs_err") or 0) < 1e-6 for c in checks if c.get("abs_err") is not None)
    if not formula_ok:
        code = "FIXED100_PNL_CALCULATION_BUG"
    elif avg_bps is not None and avg_yen is not None and ((avg_bps < 0 < avg_yen) or (avg_bps > 0 > avg_yen)):
        code = "YEN_BPS_OBJECTIVE_DIVERGENCE"
    else:
        code = "YEN_BPS_ALIGNED"
    return {
        "code": code,
        "n_checked": len(checks),
        "checks": checks,
        "c4_n": len(rows),
        "c4_avg_cost_adj_bps": avg_bps,
        "c4_avg_yen_100": avg_yen,
        "c4_high_price_n": len(hi),
        "c4_high_price_avg_yen": (sum(r.get("yen_100") or 0 for r in hi) / len(hi)) if hi else None,
        "c4_low_price_n": len(lo),
        "c4_low_price_avg_yen": (sum(r.get("yen_100") or 0 for r in lo) / len(lo)) if lo else None,
        "explanation": (
            "Per-trade yen = cadj_bps/10000 * entry_ask * 100 is algebraically same-sign as cadj (not a unit bug). "
            "Equal-weight mean(bps)<0 can coexist with mean(yen)>0 because yen scales with price: "
            "a few high-priced winners dominate yen while many small-bps losers dominate the bps average."
        ),
        "formula_ok": formula_ok,
    }


def run_deecpa(*, run_id: Optional[str] = None, test_results=None) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / run_id
    codes: list[str] = []

    if not ENRICHED_CACHE.exists():
        payload = {
            "run_id": run_id, "verdict": {"final_verdict": "DEECPA_INTEGRITY_BLOCKED", "codes": ["missing_enriched_cache"]},
            "completion": {"60_final_verdict": "DEECPA_INTEGRITY_BLOCKED"},
            "tests": test_results or {},
        }
        emit(out_dir, payload)
        payload["out_dir"] = str(out_dir)
        return payload

    print("[deecpa] load enriched cache...", flush=True)
    bundle = pickle.loads(ENRICHED_CACHE.read_bytes())
    tr, va, ho = bundle["tr"], bundle["va"], bundle["ho"]

    # Source reproduction — fit fixed model, apply frozen threshold
    print("[deecpa] fit fixed D-MID_D4_H6...", flush=True)
    model = fit_dir_candidate(tr, FIXED_LABEL, FIXED_HID)
    # Use frozen threshold (not recomputed) for selection; still check model threshold close
    thr = FIXED_THRESHOLD
    tr_sc = model.train_scores
    va_sc = _score_samples(model, va)
    ho_sc = _score_samples(model, ho) if ho else []
    tr_m = eval_dir_fixed(tr, FIXED_LABEL, tr_sc, thr)
    va_m = eval_dir_fixed(va, FIXED_LABEL, va_sc, thr)
    tr_sel = [s for s, sc in zip(tr, tr_sc) if sc >= thr]
    va_sel = [s for s, sc in zip(va, va_sc) if sc >= thr]
    ho_sel = [s for s, sc in zip(ho, ho_sc) if sc >= thr] if ho else []

    exec_va_h60 = exec_selected_metrics(va_sel, "h60")
    exec_va_h180 = exec_selected_metrics(va_sel, "h180")
    exec_va_h300 = exec_selected_metrics(va_sel, "h300")

    repro_checks = {
        "s1_train": (len(tr), EXPECT["s1_train"]),
        "s1_val": (len(va), EXPECT["s1_val"]),
        "n_sel_train": (tr_m["n_selected"], EXPECT["n_sel_train"]),
        "n_sel_val": (va_m["n_selected"], EXPECT["n_sel_val"]),
        "threshold": (thr, EXPECT["threshold"]),
        "auc_train": (tr_m["roc_auc"], EXPECT["auc_train"]),
        "auc_val": (va_m["roc_auc"], EXPECT["auc_val"]),
        "mid_train": (tr_m["selected_avg_terminal"], EXPECT["mid_train"]),
        "mid_val": (va_m["selected_avg_terminal"], EXPECT["mid_val"]),
        "mfe_mae_train": (tr_m["selected_mfe_mae"], EXPECT["mfe_mae_train"]),
        "mfe_mae_val": (va_m["selected_mfe_mae"], EXPECT["mfe_mae_val"]),
        "exec_h60": (exec_va_h60["cost_adj"], EXPECT["exec_h60"]),
        "exec_h180": (exec_va_h180["cost_adj"], EXPECT["exec_h180"]),
        "exec_h300": (exec_va_h300["cost_adj"], EXPECT["exec_h300"]),
        "model_threshold": (model.fixed_threshold, EXPECT["threshold"]),
    }
    repro_ok = all(_close(a, b) for a, b in repro_checks.values())
    reproduction = {
        "ok": repro_ok,
        "checks": {k: {"got": a, "expect": b, "ok": _close(a, b)} for k, (a, b) in repro_checks.items()},
        "source": str(SOURCE_CDEED),
        "candidate": FIXED_CANDIDATE,
        "threshold": thr,
    }
    if not repro_ok:
        payload = {
            "run_id": run_id, "reproduction": reproduction,
            "verdict": {"final_verdict": "DEECPA_REPRODUCTION_BLOCKED", "codes": ["DEECPA_REPRODUCTION_BLOCKED"]},
            "completion": {
                "1_source_reproduction": reproduction,
                "2_fixed_candidate": FIXED_CANDIDATE,
                "3_fixed_threshold": thr,
                "60_final_verdict": "DEECPA_REPRODUCTION_BLOCKED",
            },
            "tests": test_results or {},
            "integrity": {"submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER, "mainline_changed": False},
        }
        emit(out_dir, payload)
        payload["out_dir"] = str(out_dir)
        return payload

    print("[deecpa] load streams...", flush=True)
    streams = load_streams(list(dict.fromkeys(TRAIN_DAYS + VAL_DAYS + HOLD_DAYS)))

    # Yen / bps audit on C4 population (CDEED anomaly)
    print("[deecpa] yen/bps audit...", flush=True)
    yen_audit = manual_yen_audit(tr, n=25)
    if yen_audit["code"] == "YEN_BPS_OBJECTIVE_DIVERGENCE":
        codes.append("YEN_BPS_OBJECTIVE_DIVERGENCE")
    elif yen_audit["code"] == "FIXED100_PNL_CALCULATION_BUG":
        codes.append("FIXED100_PNL_CALCULATION_BUG")

    economic_formula = {
        "qty": LOT,
        "gross_pnl_yen": "(exit_price - entry_price) * 100",
        "cost_yen": "entry_price * 100 * 0.0005",
        "net_pnl_yen_100": "gross_pnl_yen - cost_yen",
        "cost_bps_once": COST_BPS,
        "legacy_cdeed_yen": "cadj_bps/10000 * entry_ask * 100",
        "primary_horizon_sec": PRIMARY_HORIZON_SEC,
        "exit_rule": "signal_time + 180s canonical bid",
    }

    # Immediate cross multi-horizon
    print("[deecpa] immediate-cross TRAIN/VAL...", flush=True)
    horizons = [30.0, 60.0, 180.0, 300.0]
    horizon_comparison = {}
    for h in horizons:
        key = f"h{int(h)}"
        tr_h = [immediate_cross_trade(s, streams[s.stream_key], h) for s in tr_sel if streams.get(s.stream_key)]
        va_h = [immediate_cross_trade(s, streams[s.stream_key], h) for s in va_sel if streams.get(s.stream_key)]
        # only valid exits for exec metrics comparable to CDEED, but also full per-signal
        horizon_comparison[key] = {
            "train": _filled_summary(tr_h),
            "val": _filled_summary(va_h),
        }

    imm_tr = [immediate_cross_trade(s, streams[s.stream_key], PRIMARY_HORIZON_SEC) for s in tr_sel if streams.get(s.stream_key)]
    imm_va = [immediate_cross_trade(s, streams[s.stream_key], PRIMARY_HORIZON_SEC) for s in va_sel if streams.get(s.stream_key)]
    imm_tr_s = _filled_summary(imm_tr)
    imm_va_s = _filled_summary(imm_va)
    # Immediate TRAIN also exhibits yen+/bps− (fixed-100 vs equal-weight bps)
    if (imm_tr_s.get("total_pnl_yen_100") or 0) > 0 and (imm_tr_s.get("avg_return_bps_filled") or 0) < 0:
        if "YEN_BPS_OBJECTIVE_DIVERGENCE" not in codes:
            codes.append("YEN_BPS_OBJECTIVE_DIVERGENCE")

    # Gate E0
    e0_ok = (
        (imm_va_s.get("total_pnl_yen_100") or 0) > 0
        and (imm_va_s.get("profit_factor_yen_100") or 0) > 1
        and (imm_va_s.get("avg_pnl_yen_100") or 0) > 0
        and (imm_va_s.get("avg_return_bps_filled") or imm_va_s.get("avg_return_bps") or 0) > 0
        and (imm_va_s.get("mfe_mae") or 0) > 1
        and (imm_va_s.get("fills") or 0) >= 50
        and not _extreme_symbol(imm_va)
    )
    yen_pos = (imm_va_s.get("total_pnl_yen_100") or 0) > 0 and (imm_va_s.get("avg_pnl_yen_100") or 0) > 0
    bps_neg = (imm_va_s.get("avg_return_bps_filled") or imm_va_s.get("avg_return_bps") or 0) <= 0
    pf_bps_neg = (imm_va_s.get("profit_factor_bps_filled") or imm_va_s.get("profit_factor_bps") or 0) is not None and (
        (imm_va_s.get("profit_factor_bps_filled") or imm_va_s.get("profit_factor_bps") or 0) < 1
    )
    if e0_ok:
        e0_verdict = "IMMEDIATE_CROSS_MARKETABLE_EDGE"
        codes.append("IMMEDIATE_CROSS_MARKETABLE_EDGE")
    elif yen_pos and (bps_neg or pf_bps_neg):
        e0_verdict = "FIXED100_YEN_EDGE_ONLY"
        codes.append("FIXED100_YEN_EDGE_ONLY")
    else:
        e0_verdict = "IMMEDIATE_CROSS_NOT_MONETIZABLE"
        codes.append("IMMEDIATE_CROSS_NOT_MONETIZABLE")

    # Fixed-candidate spread cohorts (selected only)
    print("[deecpa] fixed-candidate spread cohorts...", flush=True)
    cohort_names = ["C0", "C1", "C2", "C3", "C4", "C5"]
    spread_cohorts = {}
    cohort_rows = []
    for name in cohort_names:
        tr_c = [s for s in tr_sel if _cohort_mask(name, s)]
        va_c = [s for s in va_sel if _cohort_mask(name, s)]
        tr_t = [immediate_cross_trade(s, streams[s.stream_key], PRIMARY_HORIZON_SEC) for s in tr_c if streams.get(s.stream_key)]
        va_t = [immediate_cross_trade(s, streams[s.stream_key], PRIMARY_HORIZON_SEC) for s in va_c if streams.get(s.stream_key)]
        tr_sum = _filled_summary(tr_t)
        va_sum = _filled_summary(va_t)
        spread_cohorts[name] = {
            "train_n": len(tr_c), "val_n": len(va_c),
            "train_symbols": len({s.symbol for s in tr_c}),
            "val_symbols": len({s.symbol for s in va_c}),
            "train": tr_sum, "val": va_sum,
            "train_daily": by_day(tr_t), "val_daily": by_day(va_t),
            "train_symbols_detail": by_symbol(tr_t), "val_symbols_detail": by_symbol(va_t),
            "val_price_band": price_bands(va_t), "val_notional_band": notional_bands(va_t),
            "immediate_fill_rate": 1.0,  # cross always fills if exit exists
        }
        cohort_rows.append({
            "cohort": name,
            "train_n": len(tr_c), "val_n": len(va_c),
            "train_symbols": len({s.symbol for s in tr_c}),
            "val_symbols": len({s.symbol for s in va_c}),
            "train_total_pnl": tr_sum.get("total_pnl_yen_100"),
            "val_total_pnl": va_sum.get("total_pnl_yen_100"),
            "train_avg_pnl": tr_sum.get("avg_pnl_yen_100"),
            "val_avg_pnl": va_sum.get("avg_pnl_yen_100"),
            "train_pf_yen": tr_sum.get("profit_factor_yen_100"),
            "val_pf_yen": va_sum.get("profit_factor_yen_100"),
            "train_avg_bps": tr_sum.get("avg_return_bps_filled"),
            "val_avg_bps": va_sum.get("avg_return_bps_filled"),
            "train_pf_bps": tr_sum.get("profit_factor_bps_filled"),
            "val_pf_bps": va_sum.get("profit_factor_bps_filled"),
            "train_mfe_mae": tr_sum.get("mfe_mae"),
            "val_mfe_mae": va_sum.get("mfe_mae"),
        })

    c4 = spread_cohorts["C4"]["val"]
    low_spread_ok = (
        (c4.get("total_pnl_yen_100") or 0) > 0
        and (c4.get("profit_factor_yen_100") or 0) > 1
        and (c4.get("avg_return_bps_filled") or 0) > 0
        and (c4.get("fills") or 0) >= 20
    )
    if low_spread_ok:
        codes.append("LOW_SPREAD_MARKETABLE_EDGE_VALIDATED")

    # Passive arms if E0 not marketable
    print("[deecpa] execution arms TRAIN...", flush=True)
    arm_train: dict[str, Any] = {}
    arm_trades_tr: dict[str, list] = {}
    for arm in ARMS:
        print(f"  arm {arm} TRAIN n={len(tr_sel)}", flush=True)
        trades = _run_arm(tr_sel, streams, arm)
        arm_trades_tr[arm] = trades
        arm_train[arm] = _filled_summary(trades)

    train_arm_selection = []
    passed_arms = []
    for arm in ARMS:
        ok, reasons = _arm_train_pass(arm_train[arm], arm_trades_tr[arm], TRAIN_DAYS)
        row = {
            "arm": arm, "ok": ok, "reasons": reasons,
            **{k: arm_train[arm].get(k) for k in (
                "signals", "fills", "fill_rate", "partial_fills", "no_fills",
                "total_pnl_yen_100", "per_signal_pnl_yen", "profit_factor_yen_100",
                "avg_return_bps_filled", "avg_return_bps", "mfe_mae",
            )},
        }
        train_arm_selection.append(row)
        if ok:
            passed_arms.append(arm)

    # TRAIN select: max expected net pnl per signal among passed; else none
    selected_arm = None
    if passed_arms:
        selected_arm = max(passed_arms, key=lambda a: (
            arm_train[a].get("per_signal_pnl_yen") or -1e99,
            arm_train[a].get("profit_factor_yen_100") or -1e99,
            arm_train[a].get("avg_return_bps_filled") or arm_train[a].get("avg_return_bps") or -1e99,
            arm_train[a].get("fill_rate") or 0,
        ))
    elif e0_verdict == "IMMEDIATE_CROSS_MARKETABLE_EDGE":
        selected_arm = "E0"

    validation = {"run": False}
    holdout = {"run": False}
    arm_val_trades = []
    arm_ho_trades = []
    val_verdict = None
    ho_verdict = None

    if selected_arm is None and e0_verdict != "IMMEDIATE_CROSS_MARKETABLE_EDGE":
        codes.append("PASSIVE_ENTRY_NO_TRAIN_EDGE")
        val_verdict = "PASSIVE_ENTRY_NO_TRAIN_EDGE"
    else:
        if selected_arm is None:
            selected_arm = "E0"
        print(f"[deecpa] VAL fixed arm={selected_arm}...", flush=True)
        arm_val_trades = _run_arm(va_sel, streams, selected_arm)
        va_sum = _filled_summary(arm_val_trades)
        vok, vreasons = _arm_val_pass(va_sum, arm_val_trades)
        validation = {
            "run": True, "arm": selected_arm, "ok": vok, "reasons": vreasons,
            "summary": va_sum, "daily": by_day(arm_val_trades), "symbols": by_symbol(arm_val_trades),
            "dependence": dependence(arm_val_trades),
            "price_band": price_bands(arm_val_trades), "notional_band": notional_bands(arm_val_trades),
        }
        if vok:
            val_verdict = "PASSIVE_ENTRY_MARKETABLE_EDGE_VALIDATED"
            codes.append("PASSIVE_ENTRY_MARKETABLE_EDGE_VALIDATED")
            if selected_arm == "E1":
                codes.append("WAIT_FOR_SPREAD_COMPRESSION_EDGE")
            elif selected_arm == "E2":
                codes.append("PASSIVE_BID_FILL_EDGE")
            elif selected_arm in ("E3", "E4"):
                codes.append("PASSIVE_INSIDE_FILL_EDGE")
            codes.append("DIRECTIONAL_EDGE_EXECUTION_MONETIZED")
        else:
            val_verdict = "PASSIVE_ENTRY_EDGE_NOT_VALIDATED"
            codes.append("PASSIVE_ENTRY_EDGE_NOT_VALIDATED")

        # HOLDOUT only if E0 gate or VAL pass
        if e0_ok or vok:
            print("[deecpa] HOLDOUT once...", flush=True)
            arm_ho_trades = _run_arm(ho_sel, streams, selected_arm)
            ho_sum = _filled_summary(arm_ho_trades)
            hok, hreasons = _arm_val_pass(ho_sum, arm_ho_trades)
            holdout = {
                "run": True, "arm": selected_arm, "ok": hok, "reasons": hreasons,
                "summary": ho_sum, "daily": by_day(arm_ho_trades), "symbols": by_symbol(arm_ho_trades),
            }
            ho_verdict = "HOLDOUT_PASS" if hok else "HOLDOUT_FAIL"
        else:
            holdout = {"run": False, "reason": "gates_failed"}

    # Cause flags
    if "IMMEDIATE_CROSS_NOT_MONETIZABLE" in codes and not any(
        x in codes for x in ("PASSIVE_ENTRY_MARKETABLE_EDGE_VALIDATED", "LOW_SPREAD_MARKETABLE_EDGE_VALIDATED")
    ):
        # structural small if mid move < cost+typical spread
        mid_val = va_m.get("selected_avg_terminal") or 0
        if mid_val < COST_BPS + 10:
            codes.append("DIRECTIONAL_EDGE_STRUCTURALLY_TOO_SMALL")

    # Final verdict priority
    if "DEECPA_INTEGRITY_BLOCKED" in codes:
        final = "DEECPA_INTEGRITY_BLOCKED"
    elif "DIRECTIONAL_EDGE_EXECUTION_MONETIZED" in codes or val_verdict == "PASSIVE_ENTRY_MARKETABLE_EDGE_VALIDATED":
        final = "DIRECTIONAL_EDGE_EXECUTION_MONETIZED"
    elif "LOW_SPREAD_MARKETABLE_EDGE_VALIDATED" in codes:
        final = "LOW_SPREAD_MARKETABLE_EDGE_VALIDATED"
    elif "FIXED100_YEN_EDGE_ONLY" in codes and e0_verdict == "FIXED100_YEN_EDGE_ONLY":
        final = "FIXED100_YEN_EDGE_ONLY"
    elif "PASSIVE_FILL_OBSERVABILITY_BLOCKED" in codes:
        final = "PASSIVE_FILL_OBSERVABILITY_BLOCKED"
    elif val_verdict == "PASSIVE_ENTRY_EDGE_NOT_VALIDATED" or val_verdict == "PASSIVE_ENTRY_NO_TRAIN_EDGE":
        if "DIRECTIONAL_EDGE_STRUCTURALLY_TOO_SMALL" in codes:
            final = "DIRECTIONAL_EDGE_STRUCTURALLY_TOO_SMALL"
        else:
            final = "PASSIVE_ENTRY_EDGE_NOT_VALIDATED"
    elif e0_verdict == "IMMEDIATE_CROSS_NOT_MONETIZABLE":
        final = "DIRECTIONAL_EDGE_STRUCTURALLY_TOO_SMALL" if "DIRECTIONAL_EDGE_STRUCTURALLY_TOO_SMALL" in codes else "PASSIVE_ENTRY_EDGE_NOT_VALIDATED"
    else:
        final = e0_verdict

    # Sheets data
    daily_rows = []
    symbol_rows = []
    for arm, trades in arm_trades_tr.items():
        daily_rows.extend(_flatten_daily("TRAIN", arm, trades))
        symbol_rows.extend(_flatten_symbols("TRAIN", arm, trades))
    if arm_val_trades:
        daily_rows.extend(_flatten_daily("VAL", selected_arm or "?", arm_val_trades))
        symbol_rows.extend(_flatten_symbols("VAL", selected_arm or "?", arm_val_trades))
    if arm_ho_trades:
        daily_rows.extend(_flatten_daily("HOLD", selected_arm or "?", arm_ho_trades))
        symbol_rows.extend(_flatten_symbols("HOLD", selected_arm or "?", arm_ho_trades))
    # always include immediate daily/symbols
    daily_rows.extend(_flatten_daily("TRAIN", "E0_imm", imm_tr))
    daily_rows.extend(_flatten_daily("VAL", "E0_imm", imm_va))
    symbol_rows.extend(_flatten_symbols("TRAIN", "E0_imm", imm_tr))
    symbol_rows.extend(_flatten_symbols("VAL", "E0_imm", imm_va))

    orders = []
    fills = []
    partials = []
    no_fills = []
    queue_audit = []
    for arm, trades in list(arm_trades_tr.items()) + ([(selected_arm, arm_val_trades)] if arm_val_trades else []):
        for t in trades:
            row = {k: t.get(k) for k in (
                "day", "symbol", "sample_id", "arm", "status", "order_price", "entry_price",
                "exit_price", "filled_qty", "unfilled_qty", "initial_queue_ahead",
                "net_pnl_yen_100", "net_return_bps", "entry_notional_yen", "fill_latency_ms",
                "spread_bps",
            )}
            orders.append(row)
            if t.get("status") == "FULL_FILL":
                fills.append(row)
            elif t.get("status") == "PARTIAL_FILL":
                partials.append(row)
            elif t.get("status") == "NO_FILL":
                no_fills.append(row)
            if t.get("initial_queue_ahead") is not None:
                queue_audit.append({
                    "sample_id": t.get("sample_id"), "arm": t.get("arm"),
                    "order_price": t.get("order_price"), "initial_queue_ahead": t.get("initial_queue_ahead"),
                    "status": t.get("status"), "filled_qty": t.get("filled_qty"),
                })

    execution_arms_rows = [{"arm": a, "split": "TRAIN", **arm_train[a]} for a in ARMS]
    if selected_arm and validation.get("summary"):
        execution_arms_rows.append({"arm": selected_arm, "split": "VAL", **validation["summary"]})

    integrity = {
        "S1_only": True,
        "fixed_candidate": FIXED_CANDIDATE,
        "fixed_threshold": thr,
        "no_retrain": True,
        "no_new_features": True,
        "train_only_arm_selection": True,
        "val_no_reselect": True,
        "event_stride": STRIDE,
        "signal_plus_180_exit": True,
        "conservative_queue": True,
        "unknown_trade_no_queue": True,
        "quote_cancel_no_queue": True,
        "partial_fill": True,
        "cost_5bps_once": True,
        "no_double_spread": True,
        "daily_nonempty": len(daily_rows) > 0,
        "symbols_nonempty": len(symbol_rows) > 0,
        "submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER,
        "mainline_changed": False,
        "ok": True,
    }
    if not integrity["daily_nonempty"] or not integrity["symbols_nonempty"]:
        integrity["ok"] = False
        codes.append("DEECPA_INTEGRITY_BLOCKED")
        final = "DEECPA_INTEGRITY_BLOCKED"

    # Arm rate tables for completion
    def _rates(summ):
        return {
            "fill_rate": summ.get("fill_rate"),
            "partial_fill_rate": summ.get("partial_fill_rate"),
            "no_fill_rate": summ.get("no_fill_rate"),
            "total_pnl": summ.get("total_pnl_yen_100"),
            "pf_yen": summ.get("profit_factor_yen_100"),
            "avg_bps": summ.get("avg_return_bps_filled"),
            "per_signal": summ.get("per_signal_pnl_yen"),
        }

    completion = {
        "1_source_reproduction": {"ok": True, "checks": reproduction["checks"]},
        "2_fixed_candidate": FIXED_CANDIDATE,
        "3_fixed_threshold": thr,
        "4_TRAIN_signals": len(tr_sel),
        "5_VAL_signals": len(va_sel),
        "6_net_pnl_yen_100_formula": economic_formula["net_pnl_yen_100"],
        "7_5bps_yen_formula": economic_formula["cost_yen"],
        "8_avg_yen_100_audit": yen_audit["code"],
        "9_yen_bps_sign_reason": yen_audit["explanation"],
        "10_Immediate_TRAIN_h180_total_yen": imm_tr_s.get("total_pnl_yen_100"),
        "11_Immediate_VAL_h180_total_yen": imm_va_s.get("total_pnl_yen_100"),
        "12_Immediate_TRAIN_PF_yen": imm_tr_s.get("profit_factor_yen_100"),
        "13_Immediate_VAL_PF_yen": imm_va_s.get("profit_factor_yen_100"),
        "14_Immediate_TRAIN_avg_bps": imm_tr_s.get("avg_return_bps_filled"),
        "15_Immediate_VAL_avg_bps": imm_va_s.get("avg_return_bps_filled"),
        "16_Immediate_Gate_E0": e0_verdict,
        "17_C0_VAL": cohort_rows[0],
        "18_C1_VAL": cohort_rows[1],
        "19_C2_VAL": cohort_rows[2],
        "20_C3_VAL": cohort_rows[3],
        "21_C4_VAL": cohort_rows[4],
        "22_C5_VAL": cohort_rows[5],
        "23_LOW_SPREAD_edge": low_spread_ok,
        "24_E0_fill_rate": arm_train["E0"].get("fill_rate"),
        "25_E1_fill_rate": arm_train["E1"].get("fill_rate"),
        "26_E2_fill_rate": arm_train["E2"].get("fill_rate"),
        "27_E3_fill_rate": arm_train["E3"].get("fill_rate"),
        "28_E4_fill_rate": arm_train["E4"].get("fill_rate"),
        "29_partial_fill_rates": {a: arm_train[a].get("partial_fill_rate") for a in ARMS},
        "30_no_fill_rates": {a: arm_train[a].get("no_fill_rate") for a in ARMS},
        "31_arm_TRAIN_total_yen": {a: arm_train[a].get("total_pnl_yen_100") for a in ARMS},
        "32_arm_TRAIN_PF_yen": {a: arm_train[a].get("profit_factor_yen_100") for a in ARMS},
        "33_arm_TRAIN_avg_bps": {a: arm_train[a].get("avg_return_bps_filled") for a in ARMS},
        "34_arm_TRAIN_per_signal_yen": {a: arm_train[a].get("per_signal_pnl_yen") for a in ARMS},
        "35_TRAIN_fixed_arm": selected_arm,
        "36_fixed_arm_VAL_fills": (validation.get("summary") or {}).get("fills"),
        "37_fixed_arm_VAL_total_yen": (validation.get("summary") or {}).get("total_pnl_yen_100"),
        "38_fixed_arm_VAL_PF_yen": (validation.get("summary") or {}).get("profit_factor_yen_100"),
        "39_fixed_arm_VAL_avg_bps": (validation.get("summary") or {}).get("avg_return_bps_filled"),
        "40_fixed_arm_VAL_per_signal_yen": (validation.get("summary") or {}).get("per_signal_pnl_yen"),
        "41_VAL_verdict": val_verdict,
        "42_HOLDOUT_run": holdout.get("run"),
        "43_HOLDOUT_result": ho_verdict or holdout,
        "44_daily_reproducibility": by_day(arm_val_trades) if arm_val_trades else by_day(imm_va),
        "45_symbol_reproducibility": by_symbol(arm_val_trades if arm_val_trades else imm_va),
        "46_top1_top3_symbol": dependence(arm_val_trades if arm_val_trades else imm_va),
        "47_price_band": price_bands(arm_val_trades if arm_val_trades else imm_va),
        "48_notional_band": notional_bands(arm_val_trades if arm_val_trades else imm_va),
        "49_YEN_BPS_OBJECTIVE_DIVERGENCE": "YEN_BPS_OBJECTIVE_DIVERGENCE" in codes,
        "50_FIXED100_YEN_EDGE_ONLY": "FIXED100_YEN_EDGE_ONLY" in codes,
        "51_WAIT_FOR_SPREAD_COMPRESSION_EDGE": "WAIT_FOR_SPREAD_COMPRESSION_EDGE" in codes,
        "52_PASSIVE_BID_FILL_EDGE": "PASSIVE_BID_FILL_EDGE" in codes,
        "53_PASSIVE_INSIDE_FILL_EDGE": "PASSIVE_INSIDE_FILL_EDGE" in codes,
        "54_DIRECTIONAL_EDGE_EXECUTION_MONETIZED": "DIRECTIONAL_EDGE_EXECUTION_MONETIZED" in codes,
        "55_DIRECTIONAL_EDGE_STRUCTURALLY_TOO_SMALL": "DIRECTIONAL_EDGE_STRUCTURALLY_TOO_SMALL" in codes,
        "56_integrity": integrity,
        "57_tests": (test_results or {}).get("passed"),
        "58_submit_cancel_live": {"submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER},
        "59_mainline_changed": False,
        "60_final_verdict": final,
        "arm_rates": {a: _rates(arm_train[a]) for a in ARMS},
    }

    payload = {
        "run_id": run_id,
        "phase": "directional_edge_economic_closure_passive_execution",
        "submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER,
        "mainline_changed": False,
        "reproduction": reproduction,
        "economic_formula": economic_formula,
        "manual_yen_checks": yen_audit["checks"],
        "yen_vs_bps": {
            "code": yen_audit["code"],
            "c4_avg_bps": yen_audit["c4_avg_cost_adj_bps"],
            "c4_avg_yen": yen_audit["c4_avg_yen_100"],
            "explanation": yen_audit["explanation"],
        },
        "immediate_cross": {"train": imm_tr_s, "val": imm_va_s, "gate_e0": e0_verdict},
        "immediate_cross_rows": [
            {"split": "TRAIN", **imm_tr_s},
            {"split": "VAL", **imm_va_s},
        ],
        "horizon_comparison": horizon_comparison,
        "spread_cohorts": spread_cohorts,
        "cohort_rows": cohort_rows,
        "execution_arms": arm_train,
        "execution_arms_rows": execution_arms_rows,
        "orders": orders[:5000],
        "fills": fills[:3000],
        "partial_fills": partials[:2000],
        "no_fills": no_fills[:2000],
        "queue_audit": queue_audit[:3000],
        "train_arm_selection": train_arm_selection,
        "validation": validation,
        "holdout": holdout,
        "daily": {r["day"]: r for r in daily_rows[:200]},
        "symbols": {r["symbol"]: r for r in symbol_rows[:200]},
        "daily_rows": daily_rows,
        "symbol_rows": symbol_rows,
        "trade_dependence": dependence(arm_val_trades if arm_val_trades else imm_va),
        "symbol_dependence": dependence(arm_val_trades if arm_val_trades else imm_va),
        "price_band": price_bands(arm_val_trades if arm_val_trades else imm_va),
        "notional_band": notional_bands(arm_val_trades if arm_val_trades else imm_va),
        "execution_audit": {
            "primary_horizon": PRIMARY_HORIZON_SEC,
            "exit": "signal_time+180s bid",
            "queue_model": "CONSERVATIVE_QUEUE",
            "selected_arm": selected_arm,
        },
        "integrity": integrity,
        "verdict": {"final_verdict": final, "codes": sorted(set(codes)), "e0": e0_verdict, "val": val_verdict},
        "completion": completion,
        "tests": test_results or {},
        "directional": {"train": tr_m, "val": va_m},
        "cdeed_exec_repro": {"h60": exec_va_h60, "h180": exec_va_h180, "h300": exec_va_h300},
    }
    print(f"[deecpa] emit {out_dir} final={final}", flush=True)
    emit(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    return payload


if __name__ == "__main__":
    run_deecpa()
