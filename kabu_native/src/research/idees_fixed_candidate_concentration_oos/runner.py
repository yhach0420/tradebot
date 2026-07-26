"""IDEES-CC runner — E1_X5 concentration resilience and OOS closure."""
from __future__ import annotations

import pickle
from collections import defaultdict
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.continuous_directional_vs_execution_edge.scoring import _score_samples, fit_dir_candidate
from research.idees_fixed_candidate_concentration_oos.analysis import (
    classify_concentration,
    compare_x1_x5,
    exclude_symbols,
    leave_one_symbol_out,
    metrics,
    one_trade_per_symbol_session,
    remove_top1_trade,
    symbol_table,
    time_bands,
)
from research.idees_fixed_candidate_concentration_oos.constants import (
    CANCEL,
    COMPARE_EXIT,
    ENRICHED_CACHE,
    ENTRY_ARM,
    EXIT_ARM,
    FIXED_HID,
    FIXED_LABEL,
    FIXED_STRATEGY,
    FIXED_THRESHOLD,
    HOLD_DAYS,
    LIVE_ORDER,
    OUT_ROOT,
    REPRO_ABS_TOL,
    REPRO_EXPECT,
    SOURCE_IDEES,
    SUBMIT,
    TRAIN_DAYS,
    VAL_DAYS,
)
from research.idees_fixed_candidate_concentration_oos.reporting import emit
from research.integrated_directional_entry_exit_strategy.portfolio import replay_cap5_ranked
from research.integrated_directional_entry_exit_strategy.runner import (
    _exits_from_hits,
    _resolve_entries,
    _stream_index,
)
from research.upward_edge_identification_audit.loader import load_streams

JST = ZoneInfo("Asia/Tokyo")


def _close(a, b, tol=REPRO_ABS_TOL) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= tol


def _close_daily(got: float, expect: float) -> bool:
    # daily stored with float noise; allow 1e-6 absolute
    return abs(float(got) - float(expect)) <= 1e-6


def _slim(m: dict) -> dict:
    return {k: v for k, v in m.items() if k not in ("accepted", "symbols_pnl")}


def _pbv2_overlap(accepted, days: list[str]) -> dict[str, Any]:
    from research.idees_fixed_candidate_concentration_oos.constants import REPO_ROOT
    from research.integrated_directional_entry_exit_strategy.constants import OUT_ROOT as IDEES_OUT
    cache = IDEES_OUT / "_cache" / "pbv2_entry_times.pkl"
    day_set = set(days)
    try:
        if cache.exists():
            pb_times = pickle.loads(cache.read_bytes())
        else:
            from research.price_flow_exit.entries import load_pbv2_entries
            pb_all = load_pbv2_entries(REPO_ROOT)
            pb_times = [(e.day, e.symbol, e.entry_time) for e in pb_all]
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(pickle.dumps(pb_times, protocol=pickle.HIGHEST_PROTOCOL))
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "error": str(exc), "overlap_n": 0, "unique_n": len(accepted), "unique_pnl": sum(t.net_pnl_yen_100 for t in accepted)}
    pb = [(d, s, t) for d, s, t in pb_times if d in day_set]
    by = defaultdict(list)
    for d, s, t in pb:
        by[(d, s)].append(t)
    overlap = unique_n = 0
    unique_pnl = 0.0
    near = 0
    for t in accepted:
        times = by.get((t.day, t.symbol), [])
        if any(abs((t.entry_time - pt).total_seconds()) <= 120 for pt in times):
            overlap += 1
            near += 1
        else:
            unique_n += 1
            unique_pnl += t.net_pnl_yen_100
    return {
        "available": True, "pbv2_n": len(pb), "strategy_n": len(accepted),
        "overlap_n": overlap, "overlap_rate": overlap / len(accepted) if accepted else None,
        "unique_n": unique_n, "unique_pnl": unique_pnl, "near_entry_n": near,
        "cap_conflict_note": "CAP5 independent of PBv2; no shared portfolio",
    }


def _train_resilience_gate(r0, r1, r2, r3, r5) -> tuple[bool, list[str]]:
    reasons = []
    ok = True
    if not ((r0.get("total_pnl_yen_100") or 0) > 0 and (r0.get("profit_factor_yen_100") or 0) > 1 and (r0.get("avg_bps") or 0) > 0):
        ok = False
        reasons.append("R0_fail")
    if not (
        (r1.get("total_pnl_yen_100") or 0) > 0
        and (r1.get("profit_factor_yen_100") or 0) > 1
        and (r1.get("avg_bps") or 0) > 0
        and (r1.get("trades") or 0) >= 30
    ):
        ok = False
        reasons.append("R1_fail")
    if not (
        (r2.get("total_pnl_yen_100") or 0) > 0
        and (r2.get("profit_factor_yen_100") or 0) > 1
        and (r2.get("trades") or 0) >= 20
    ):
        ok = False
        reasons.append("R2_fail")
    if not ((r3.get("worst_pnl") or 0) > 0):
        ok = False
        reasons.append("R3_worst_nonpos")
    if not ((r5.get("total_pnl_yen_100") or 0) > 0 and (r5.get("profit_factor_yen_100") or 0) > 1):
        ok = False
        reasons.append("R5_fail")
    return ok, reasons


def _val_gate(m, top1_removed_m) -> tuple[bool, list[str]]:
    reasons = []
    ok = True
    if (m.get("total_pnl_yen_100") or 0) <= 0:
        ok = False
        reasons.append("pnl<=0")
    if (m.get("profit_factor_yen_100") or 0) <= 1.05:
        ok = False
        reasons.append("PF<=1.05")
    if (m.get("avg_pnl_yen_100") or 0) <= 0:
        ok = False
        reasons.append("avg<=0")
    if (m.get("avg_bps") or 0) <= 0:
        ok = False
        reasons.append("avg_bps<=0")
    if (m.get("trades") or 0) < 20:
        ok = False
        reasons.append("trades<20")
    gp = m.get("gross_profit_yen") or 0
    dd = abs(m.get("max_drawdown_yen") or 0)
    if gp > 0 and dd >= gp:
        ok = False
        reasons.append("dd>=gross_profit")
    if (top1_removed_m.get("total_pnl_yen_100") or 0) <= 0:
        ok = False
        reasons.append("top1_trade_removed_nonpos")
    return ok, reasons


def _hold_gate(m, top1_removed_m) -> tuple[bool, list[str]]:
    reasons = []
    ok = True
    if (m.get("total_pnl_yen_100") or 0) <= 0:
        ok = False
        reasons.append("pnl<=0")
    if (m.get("profit_factor_yen_100") or 0) <= 1.0:
        ok = False
        reasons.append("PF<=1")
    if (m.get("avg_pnl_yen_100") or 0) <= 0:
        ok = False
        reasons.append("avg<=0")
    if (m.get("avg_bps") or 0) <= 0:
        ok = False
        reasons.append("avg_bps<=0")
    if (top1_removed_m.get("total_pnl_yen_100") or 0) <= 0:
        ok = False
        reasons.append("top1_trade_removed_nonpos")
    return ok, reasons


def _run_e1_exit(samples, scores, streams, exit_arm: str):
    by, pos, tl = _stream_index(samples, scores)
    hits = _resolve_entries(samples, scores, streams, ENTRY_ARM, by, pos)
    raw = _exits_from_hits(hits, exit_arm, streams, tl)
    cap = replay_cap5_ranked(raw)
    return hits, raw, cap


def run_idees_cc(*, run_id: Optional[str] = None, test_results=None) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = OUT_ROOT / run_id

    candidate_spec = {
        "strategy": FIXED_STRATEGY,
        "entry": (
            f"D-MID_D4_H6 score>={FIXED_THRESHOLD} AND spread<=5bps; "
            "immediate canonical ask ENTRY"
        ),
        "exit": (
            "Hard stop -15bps; trail after bid-MFE +20bps with 40% giveback; "
            "+50bps take-profit; max hold 300s; session end bid close"
        ),
        "execution": "ask ENTRY / bid EXIT / 100 shares / 5bps roundtrip / CAP5 / stride=1",
        "source": str(SOURCE_IDEES),
    }

    if not ENRICHED_CACHE.exists():
        payload = {
            "run_id": run_id, "candidate_spec": candidate_spec,
            "verdict": {"final_verdict": "IDEES_CC_INTEGRITY_BLOCKED"},
            "completion": {"43_final_verdict": "IDEES_CC_INTEGRITY_BLOCKED"},
            "tests": test_results or {},
        }
        emit(out_dir, payload)
        payload["out_dir"] = str(out_dir)
        return payload

    print("[idees-cc] load cache + fit model...", flush=True)
    bundle = pickle.loads(ENRICHED_CACHE.read_bytes())
    tr, va, ho = bundle["tr"], bundle["va"], bundle["ho"]
    model = fit_dir_candidate(tr, FIXED_LABEL, FIXED_HID)
    if abs((model.fixed_threshold or 0) - FIXED_THRESHOLD) > 1e-9:
        payload = {
            "run_id": run_id, "candidate_spec": candidate_spec,
            "verdict": {"final_verdict": "IDEES_CC_INTEGRITY_BLOCKED", "reason": "threshold_mismatch"},
            "completion": {"43_final_verdict": "IDEES_CC_INTEGRITY_BLOCKED"},
            "tests": test_results or {},
        }
        emit(out_dir, payload)
        payload["out_dir"] = str(out_dir)
        return payload

    tr_sc = model.train_scores
    va_sc = _score_samples(model, va)
    ho_sc = _score_samples(model, ho) if ho else []

    print("[idees-cc] load streams...", flush=True)
    streams = load_streams(list(dict.fromkeys(TRAIN_DAYS + VAL_DAYS + HOLD_DAYS)))

    print("[idees-cc] TRAIN E1_X5 replay...", flush=True)
    hits_tr, raw_x5_tr, cap_x5_tr = _run_e1_exit(tr, tr_sc, streams, EXIT_ARM)
    accepted_tr = list(cap_x5_tr.get("accepted") or [])
    # Use IDEES portfolio metrics for reproduction (same functions), then analysis metrics for avg_bps
    r0_port = {k: v for k, v in cap_x5_tr.items() if k != "accepted"}
    r0 = metrics(accepted_tr)

    repro_checks = {
        "trades": (r0_port.get("trades"), REPRO_EXPECT["trades"]),
        "total_pnl_yen_100": (r0_port.get("total_pnl_yen_100"), REPRO_EXPECT["total_pnl_yen_100"]),
        "avg_pnl_yen_100": (r0_port.get("avg_pnl_yen_100"), REPRO_EXPECT["avg_pnl_yen_100"]),
        "profit_factor_yen_100": (r0_port.get("profit_factor_yen_100"), REPRO_EXPECT["profit_factor_yen_100"]),
        "max_drawdown_yen": (r0_port.get("max_drawdown_yen"), REPRO_EXPECT["max_drawdown_yen"]),
        "top1_symbol_share": (r0_port.get("top1_symbol_share"), REPRO_EXPECT["top1_symbol_share"]),
        "top3_symbol_share": (r0_port.get("top3_symbol_share"), REPRO_EXPECT["top3_symbol_share"]),
        "daily_20260721": ((r0_port.get("daily") or {}).get("20260721"), REPRO_EXPECT["daily_20260721"]),
        "daily_20260722": ((r0_port.get("daily") or {}).get("20260722"), REPRO_EXPECT["daily_20260722"]),
    }
    repro_ok = True
    repro_detail = {}
    for k, (got, exp) in repro_checks.items():
        if k.startswith("daily_"):
            ok = _close_daily(got, exp) if got is not None else False
        else:
            ok = _close(got, exp)
        repro_detail[k] = {"got": got, "expect": exp, "ok": ok}
        repro_ok = repro_ok and ok

    reproduction = {"ok": repro_ok, "checks": repro_detail, "source": str(SOURCE_IDEES)}
    if not repro_ok:
        payload = {
            "run_id": run_id, "candidate_spec": candidate_spec, "reproduction": reproduction,
            "verdict": {"final_verdict": "IDEES_CC_REPRODUCTION_BLOCKED"},
            "completion": {
                "1_reproduction": reproduction,
                "43_final_verdict": "IDEES_CC_REPRODUCTION_BLOCKED",
            },
            "integrity": {"submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER, "mainline_changed": False},
            "tests": test_results or {},
        }
        emit(out_dir, payload)
        payload["out_dir"] = str(out_dir)
        return payload

    print("[idees-cc] concentration decomposition...", flush=True)
    sym_rows = symbol_table(accepted_tr)
    yen_bps = classify_concentration(sym_rows)
    top1 = sym_rows[0]["symbol"] if sym_rows else None
    top3 = [r["symbol"] for r in sym_rows[:3]]

    # Stress diagnostics (re-aggregation only)
    r1_trades = exclude_symbols(accepted_tr, {top1} if top1 else set())
    r1 = metrics(r1_trades)
    r2_trades = exclude_symbols(accepted_tr, set(top3))
    r2 = metrics(r2_trades)
    r3 = leave_one_symbol_out(accepted_tr)
    r4_trades = one_trade_per_symbol_session(accepted_tr)
    r4 = metrics(r4_trades)
    r5_trades, top_trade = remove_top1_trade(accepted_tr)
    r5 = metrics(r5_trades)

    # Daily under R0/R1/R2
    daily_rows = []
    for tag, m in (("R0", r0), ("R1", r1), ("R2", r2)):
        for d, pnl in (m.get("daily") or {}).items():
            daily_rows.append({"stress": tag, "day": d, "pnl": pnl, "trades": None})
    # more precise daily trade counts
    for tag, trades in (("R0", accepted_tr), ("R1", r1_trades), ("R2", r2_trades)):
        by = defaultdict(list)
        for t in trades:
            by[t.day].append(t)
        for d, rows in by.items():
            daily_rows.append({"stress": tag, "day": d, "pnl": sum(x.net_pnl_yen_100 for x in rows), "trades": len(rows), **metrics(rows)})

    resilient, res_reasons = _train_resilience_gate(r0, r1, r2, r3, r5)
    train_resilience = "TRAIN_CONCENTRATION_RESILIENT" if resilient else "TRAIN_SYMBOL_DEPENDENCY_CONFIRMED"

    # X1 vs X5 on same ENTRY hits (CAP5 each; pair by sample_id on accepted X5 vs raw/CAP X1)
    print("[idees-cc] E1_X1 comparison...", flush=True)
    _, raw_x1_tr, cap_x1_tr = _run_e1_exit(tr, tr_sc, streams, COMPARE_EXIT)
    x1_acc = list(cap_x1_tr.get("accepted") or [])
    # Pair on CAP5-accepted X5 vs all X1 outcomes for same entries (prefer accepted X1, else raw)
    x1_for_pair = {t.sample_id: t for t in raw_x1_tr}
    x1_x5 = compare_x1_x5(accepted_tr, list(x1_for_pair.values()))
    x1_x5["x1_cap5"] = _slim(metrics(x1_acc))
    x1_x5["x5_cap5"] = _slim(r0)

    tb_train = time_bands(accepted_tr)
    time_band_rows = [{"split": "TRAIN", "band": k, **v} for k, v in tb_train.items()]

    validation = {"run": False}
    holdout = {"run": False}
    val_verdict = None
    hold_verdict = None
    accepted_va = []
    accepted_ho = []

    if resilient:
        print("[idees-cc] VAL 20260723...", flush=True)
        _, _, cap_va = _run_e1_exit(va, va_sc, streams, EXIT_ARM)
        accepted_va = list(cap_va.get("accepted") or [])
        m_va = metrics(accepted_va)
        va_ex1 = metrics(exclude_symbols(accepted_va, {m_va["top1_symbol"]} if m_va.get("top1_symbol") else set()))
        va_ex3 = metrics(exclude_symbols(accepted_va, set(m_va.get("top3_symbols") or [])))
        va_loso = leave_one_symbol_out(accepted_va)
        va_rm, _ = remove_top1_trade(accepted_va)
        va_r5 = metrics(va_rm)
        vok, vreasons = _val_gate(m_va, va_r5)
        val_verdict = "VAL_PASS" if vok else "E1_X5_VALIDATION_FAILED"
        validation = {
            "run": True, "ok": vok, "reasons": vreasons,
            "full": _slim(m_va),
            "exclude_top1_symbol": _slim(va_ex1),
            "exclude_top3_symbols": _slim(va_ex3),
            "leave_one_symbol_out_worst": va_loso.get("worst_row"),
            "top1_trade_removed": _slim(va_r5),
            "time_bands": time_bands(accepted_va),
        }
        for band, v in time_bands(accepted_va).items():
            time_band_rows.append({"split": "VAL", "band": band, **v})

        if vok:
            print("[idees-cc] HOLDOUT 20260724 once...", flush=True)
            _, _, cap_ho = _run_e1_exit(ho, ho_sc, streams, EXIT_ARM)
            accepted_ho = list(cap_ho.get("accepted") or [])
            m_ho = metrics(accepted_ho)
            ho_rm, _ = remove_top1_trade(accepted_ho)
            ho_r5 = metrics(ho_rm)
            hok, hreasons = _hold_gate(m_ho, ho_r5)
            hold_verdict = "HOLD_PASS" if hok else "E1_X5_HOLDOUT_FAILED"
            holdout = {
                "run": True, "ok": hok, "reasons": hreasons,
                "full": _slim(m_ho),
                "top1_trade_removed": _slim(ho_r5),
                "exclude_top1_symbol": _slim(metrics(exclude_symbols(accepted_ho, {m_ho["top1_symbol"]} if m_ho.get("top1_symbol") else set()))),
                "time_bands": time_bands(accepted_ho),
            }
            for band, v in time_bands(accepted_ho).items():
                time_band_rows.append({"split": "HOLD", "band": band, **v})

    # Final verdict
    if not resilient:
        if yen_bps.get("YEN_PRICE_WEIGHT_CONCENTRATION") and not yen_bps.get("TRUE_SYMBOL_EDGE_CONCENTRATION"):
            # still dependency confirmed if resilience failed
            final = "TRAIN_SYMBOL_DEPENDENCY_CONFIRMED"
            # if only yen weight but resilience failed, still SYMBOL_DEPENDENCY
        else:
            final = "TRAIN_SYMBOL_DEPENDENCY_CONFIRMED"
    elif val_verdict == "VAL_PASS" and hold_verdict == "HOLD_PASS":
        final = "INTEGRATED_DIRECTIONAL_STRATEGY_VALIDATED"
    elif val_verdict == "VAL_PASS" and hold_verdict == "E1_X5_HOLDOUT_FAILED":
        final = "INTEGRATED_DIRECTIONAL_STRATEGY_VAL_VALIDATED"
    elif val_verdict == "E1_X5_VALIDATION_FAILED":
        final = "E1_X5_VALIDATION_FAILED"
    else:
        final = "TRAIN_CONCENTRATION_RESILIENT"

    # If yen-weight only AND resilient, can note YEN_PRICE_WEIGHT — but final priority above
    # Spec has YEN_PRICE_WEIGHT_CONCENTRATION as a final verdict option when that's the finding
    if final == "TRAIN_SYMBOL_DEPENDENCY_CONFIRMED" and yen_bps.get("YEN_PRICE_WEIGHT_CONCENTRATION"):
        # Prefer more specific code when resilience failed due to true dependency after exclude
        pass
    if resilient and yen_bps.get("YEN_PRICE_WEIGHT_CONCENTRATION") and final == "TRAIN_CONCENTRATION_RESILIENT":
        # both true — keep TRAIN_CONCENTRATION_RESILIENT as stronger (edge remains)
        pass

    pb_days = TRAIN_DAYS + (VAL_DAYS if validation.get("run") else []) + (HOLD_DAYS if holdout.get("run") else [])
    pb_acc = accepted_tr + accepted_va + accepted_ho
    pbv2 = _pbv2_overlap(pb_acc, pb_days)

    exit_reason_rows = [{"split": "TRAIN", "reason": k, "n": v} for k, v in (r0.get("exit_reasons") or {}).items()]
    if validation.get("run"):
        for k, v in ((validation.get("full") or {}).get("exit_reasons") or {}).items():
            exit_reason_rows.append({"split": "VAL", "reason": k, "n": v})

    integrity = {
        "fixed_strategy": FIXED_STRATEGY,
        "no_new_entry": True,
        "no_new_exit": True,
        "no_param_change": True,
        "no_feature_add": True,
        "no_symbol_rule": True,
        "reproduction_ok": True,
        "train_only_top_symbols_for_diag": True,
        "val_no_reselect": True,
        "submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER,
        "mainline_changed": False,
        "ok": True,
    }

    completion = {
        "1_reproduction": {"ok": True, "checks": repro_detail},
        "2_top1_symbol": top1,
        "3_top3_symbols": top3,
        "4_top1_top3_yen_share": {
            "top1": yen_bps.get("top1_yen_share"),
            "top3": yen_bps.get("top3_yen_share"),
        },
        "5_top1_top3_bps_share": {
            "top1": yen_bps.get("top1_bps_share"),
            "top3": yen_bps.get("top3_bps_share"),
        },
        "6_top1_top3_trade_share": {
            "top1": yen_bps.get("top1_trade_share"),
            "top3": yen_bps.get("top3_trade_share"),
        },
        "7_top1_top3_notional_share": {
            "top1": yen_bps.get("top1_notional_share"),
            "top3": yen_bps.get("top3_notional_share"),
        },
        "8_YEN_PRICE_WEIGHT_CONCENTRATION": yen_bps.get("YEN_PRICE_WEIGHT_CONCENTRATION"),
        "9_TRUE_SYMBOL_EDGE_CONCENTRATION": yen_bps.get("TRUE_SYMBOL_EDGE_CONCENTRATION"),
        "10_R0": _slim(r0),
        "11_R1": _slim(r1),
        "12_R2": _slim(r2),
        "13_loso_worst": r3.get("worst_row"),
        "14_top1_trade_removed": _slim(r5),
        "15_one_trade_per_symbol_session": _slim(r4),
        "16_20260721_after_exclude": {
            "R0": (r0.get("daily") or {}).get("20260721"),
            "R1": (r1.get("daily") or {}).get("20260721"),
            "R2": (r2.get("daily") or {}).get("20260721"),
        },
        "17_20260722_after_exclude": {
            "R0": (r0.get("daily") or {}).get("20260722"),
            "R1": (r1.get("daily") or {}).get("20260722"),
            "R2": (r2.get("daily") or {}).get("20260722"),
        },
        "18_TRAIN_resilience": train_resilience,
        "18_reasons": res_reasons,
        "19_VAL_run": validation.get("run"),
        "20_VAL_trades": (validation.get("full") or {}).get("trades"),
        "21_VAL_pnl": (validation.get("full") or {}).get("total_pnl_yen_100"),
        "22_VAL_PF": (validation.get("full") or {}).get("profit_factor_yen_100"),
        "23_VAL_avg_bps": (validation.get("full") or {}).get("avg_bps"),
        "24_VAL_max_DD": (validation.get("full") or {}).get("max_drawdown_yen"),
        "25_VAL_top1_trade_removed": validation.get("top1_trade_removed"),
        "26_VAL_verdict": val_verdict,
        "27_HOLD_run": holdout.get("run"),
        "28_HOLD_trades": (holdout.get("full") or {}).get("trades"),
        "29_HOLD_pnl": (holdout.get("full") or {}).get("total_pnl_yen_100"),
        "30_HOLD_PF": (holdout.get("full") or {}).get("profit_factor_yen_100"),
        "31_HOLD_avg_bps": (holdout.get("full") or {}).get("avg_bps"),
        "32_HOLD_top1_trade_removed": holdout.get("top1_trade_removed"),
        "33_HOLD_verdict": hold_verdict,
        "34_X5_vs_X1": x1_x5,
        "35_X5_exit_improvement": x1_x5.get("total_pnl_diff"),
        "36_pbv2_overlap_rate": pbv2.get("overlap_rate"),
        "37_unique_pnl": pbv2.get("unique_pnl"),
        "38_strategy_confirmed": final == "INTEGRATED_DIRECTIONAL_STRATEGY_VALIDATED",
        "39_integrity": integrity,
        "40_tests": (test_results or {}).get("passed"),
        "41_submit_cancel_live": {"submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER},
        "42_mainline_changed": False,
        "43_final_verdict": final,
        "top_trade_removed_sample": None if top_trade is None else {
            "sample_id": top_trade.sample_id, "symbol": top_trade.symbol,
            "pnl": top_trade.net_pnl_yen_100,
        },
        "concentration_code": yen_bps.get("code"),
    }

    payload = {
        "run_id": run_id,
        "phase": "idees_fixed_candidate_concentration_oos",
        "submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER,
        "mainline_changed": False,
        "candidate_spec": candidate_spec,
        "reproduction": reproduction,
        "train_full": _slim(r0),
        "symbol_rows": sym_rows,
        "yen_bps": yen_bps,
        "r0": _slim(r0),
        "r1": {**_slim(r1), "excluded": top1},
        "r2": {**_slim(r2), "excluded": top3},
        "r3": {"worst_pnl": r3.get("worst_pnl"), "median_pnl": r3.get("median_pnl"), "best_pnl": r3.get("best_pnl"), "worst_row": r3.get("worst_row"), "rows": r3.get("rows")},
        "r4": _slim(r4),
        "r5": {**_slim(r5), "removed": completion["top_trade_removed_sample"]},
        "daily_rows": daily_rows,
        "time_band_rows": time_band_rows,
        "validation": validation,
        "holdout": holdout,
        "x1_x5": x1_x5,
        "exit_reason_rows": exit_reason_rows,
        "cap5": {
            "train": {"trades": r0_port.get("trades"), "cap_blocked": r0_port.get("cap_blocked"), "pnl": r0_port.get("total_pnl_yen_100")},
            "val": {"trades": (validation.get("full") or {}).get("trades"), "pnl": (validation.get("full") or {}).get("total_pnl_yen_100")} if validation.get("run") else None,
            "hold": {"trades": (holdout.get("full") or {}).get("trades"), "pnl": (holdout.get("full") or {}).get("total_pnl_yen_100")} if holdout.get("run") else None,
        },
        "pbv2_overlap": pbv2,
        "execution_audit": candidate_spec,
        "integrity": integrity,
        "verdict": {
            "final_verdict": final,
            "train_resilience": train_resilience,
            "val": val_verdict,
            "hold": hold_verdict,
            "concentration": yen_bps.get("code"),
        },
        "completion": completion,
        "tests": test_results or {},
        "entry_hits_train": len(hits_tr),
    }
    print(f"[idees-cc] emit {out_dir} final={final}", flush=True)
    emit(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    return payload


if __name__ == "__main__":
    run_idees_cc()
