"""Audit posthoc DROP_TOP, Full Causal EX-TOP1, corrected 75 ranking, fold-local LODO."""
from __future__ import annotations

import inspect
from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.e4_x2_z3_causal_concentration_recheck_v1 import (
    CASE_A,
    CASE_B,
    CASE_C,
    CASE_D,
    CASE_E,
    CASE_TRUE_DEP,
    DROP_TOP_METHOD_A,
    EXPECTED_BASE,
    FOCUS_CANDIDATE,
    PRIOR_ECONOMIC_PASS_N,
    PRIOR_VERDICT,
)
from research.e4_x2_z3_causal_concentration_recheck_v1.harvest import AUDIT
from research.simple_full_strategy_discovery_v1 import (
    DEVELOPMENT_DAYS,
    LODO_TOP3_MIN,
    MIN_PF,
    SHARES,
)
from research.simple_full_strategy_discovery_v1.analyze import (
    coverage_ok,
    evaluate_candidate,
    pack_trades,
    parse_cid,
    _pf_sort_key,
)
from research.simple_full_strategy_discovery_v1.spec import candidate_ids
from research.simple_tech_entry_family.portfolio import _sym


def leakage_n() -> dict[str, int]:
    keys = (
        "HOLDOUT_BURNED_READ_N",
        "STRESS_READ_N",
        "STRESS_FILE_OPEN_N",
        "STRESS_METRIC_COMPUTE_N",
        "FUTURE_DATA_N",
        "CURRENT_PRICE_TIME_AS_BOARD_FRESH_N",
        "SPLIT_LEAKAGE_N",
        "EXTRA_CANDIDATE_N",
        "PRIOR_CACHE_WRITE_N",
    )
    return {k: int(AUDIT.get(k) or 0) for k in keys}


def integrity_ok() -> bool:
    return all(int(v) == 0 for v in leakage_n().values())


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _median(xs: list[Any]) -> Optional[float]:
    vs = [x for x in (_f(v) for v in xs) if x is not None]
    return float(np.median(vs)) if vs else None


def _mean(xs: list[Any]) -> Optional[float]:
    vs = [x for x in (_f(v) for v in xs) if x is not None]
    return float(np.mean(vs)) if vs else None


def audit_drop_top_method() -> dict[str, Any]:
    src = inspect.getsource(pack_trades)
    _lines, start = inspect.getsourcelines(pack_trades)
    snippet = "".join(ln for ln in _lines if "top_sym" in ln or "drop_top" in ln)
    posthoc = "total - top_sym_pnl" in src
    causal_rerun = "portfolio_replay" in src and "exclude" in src
    if posthoc and not causal_rerun:
        method = DROP_TOP_METHOD_A
        klass = "A"
        prior_causal = False
    elif causal_rerun and not posthoc:
        method = "CAUSAL_UNIVERSE_EXCLUSION_RERUN"
        klass = "B"
        prior_causal = True
    else:
        method = "OTHER"
        klass = "C"
        prior_causal = False
    return {
        "DROP_TOP_SYMBOL_METHOD": method,
        "METHOD_CLASS": klass,
        "SOURCE_FILE": "src/research/simple_full_strategy_discovery_v1/analyze.py",
        "SOURCE_FUNCTION": "pack_trades",
        "SOURCE_LINE_START": int(start),
        "SOURCE_SNIPPET": snippet.strip(),
        "PRIOR_DROP_TOP_CAUSAL": prior_causal,
    }


def parity_check(row: dict[str, Any]) -> dict[str, Any]:
    diffs = {}
    ok = True
    for k, exp in EXPECTED_BASE.items():
        got = row.get(k)
        if k in ("positive_day_n", "negative_day_n", "TRADE_N", "fill_n", "signal_n"):
            match = int(got or 0) == int(exp)
        elif k == "PF":
            match = got is not None and abs(float(got) - float(exp)) <= 1e-9
        else:
            match = got is not None and abs(float(got) - float(exp)) <= 0.51
        diffs[k] = {"expected": exp, "got": got, "ok": match}
        ok = ok and match
    return {"ok": ok, "diffs": diffs}


def economic_other_ok(p: dict[str, Any]) -> bool:
    total = _f(p.get("TOTAL_PNL"))
    pf = p.get("PF")
    pf_ok = pf is not None and (pf == float("inf") or float(pf) > float(MIN_PF))
    dd = _f(p.get("MAXDD")) or 0.0
    return bool(
        total is not None
        and float(total) > 0.0
        and pf_ok
        and int(p.get("positive_day_n") or 0) > int(p.get("negative_day_n") or 0)
        and _f(p.get("EX_BEST_DAY_PNL")) is not None
        and float(p["EX_BEST_DAY_PNL"]) > 0.0
        and (float(total) + float(dd)) > 0.0
    )


def corrected_score(base: dict[str, Any], causal_pnl: float) -> Optional[float]:
    n = int(base.get("TRADE_N") or 0)
    if n <= 0:
        return None
    a = float(base.get("TOTAL_PNL") or 0.0) / float(n)
    b = float(base.get("EX_BEST_DAY_PNL") or 0.0) / float(n)
    c = float(causal_pnl) / float(n)
    return float(min(a, b, c))


def tid(t: dict[str, Any]) -> tuple[str, str, float]:
    return (str(t.get("date") or ""), _sym(t), float(t.get("t0") or 0.0))


def top_symbol_from_trades(trades: list[dict[str, Any]]) -> tuple[Optional[str], float]:
    by_sym: dict[str, float] = defaultdict(float)
    for t in trades:
        by_sym[_sym(t)] += float(t.get("pnl_yen_100") or 0.0)
    if not by_sym:
        return None, 0.0
    top = max(by_sym.keys(), key=lambda s: by_sym[s])
    return top, float(by_sym[top])


def symbol_ledger(trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        buckets[_sym(t)].append(t)
    rows = []
    for sym, xs in buckets.items():
        pnls = [float(x.get("pnl_yen_100") or 0.0) for x in xs]
        gp = sum(v for v in pnls if v > 0)
        gl = -sum(v for v in pnls if v < 0)
        pf = (float("inf") if gp > 0 else None) if gl <= 1e-12 else float(gp / gl)
        rows.append(
            {
                "symbol": sym,
                "trade_n": len(xs),
                "net_pnl": float(sum(pnls)),
                "gross_profit": float(gp),
                "gross_loss": float(gl),
                "PF": pf,
                "median_pnl": _median(pnls),
                "avg_pnl": (float(sum(pnls)) / len(xs)) if xs else None,
            }
        )
    rows.sort(key=lambda r: (-float(r["net_pnl"]), str(r["symbol"])))
    return rows


def _bps(t: dict[str, Any]) -> Optional[float]:
    px = _f(t.get("fill_price"))
    pnl = _f(t.get("pnl_yen_100"))
    if px is None or px <= 0 or pnl is None:
        return None
    return float(pnl) / (float(px) * float(SHARES)) * 10000.0


def notional_diagnostic(trades: list[dict[str, Any]]) -> dict[str, Any]:
    px = [_f(t.get("fill_price")) for t in trades]
    pxv = [x for x in px if x is not None and x > 0]
    notionals = [x * float(SHARES) for x in pxv]
    bps = [_bps(t) for t in trades]
    bpsv = [x for x in bps if x is not None]
    pnl = float(sum(float(t.get("pnl_yen_100") or 0.0) for t in trades))
    notional_sum = float(sum(notionals)) if notionals else 0.0
    tw_bps = (pnl / notional_sum * 10000.0) if notional_sum > 0 else None
    return {
        "trade_n": len(trades),
        "median_entry_price": _median(pxv),
        "median_notional_100": _median(notionals),
        "net_pnl_yen": pnl,
        "trade_weighted_pnl_bps": tw_bps,
        "avg_pnl_bps": _mean(bpsv),
        "median_pnl_bps": _median(bpsv),
    }


def exclude_symbol(rows: list[dict[str, Any]], symbol: str) -> list[dict[str, Any]]:
    target = str(symbol).replace(".T", "")
    return [r for r in rows if _sym(r) != target]


def posthoc_ex_top1(trades: list[dict[str, Any]], top: str, *, days: list[str]) -> dict[str, Any]:
    kept = [t for t in trades if _sym(t) != str(top).replace(".T", "")]
    return pack_trades(kept, days=days)


def causal_ex_top1(
    cid: str,
    rows: list[dict[str, Any]],
    ctrl_rows: list[dict[str, Any]],
    *,
    days: list[str],
    top: str,
    base_trades: list[dict[str, Any]],
) -> dict[str, Any]:
    rs = exclude_symbol(rows, top)
    cs = exclude_symbol(ctrl_rows, top)
    causal = evaluate_candidate(cid, rs, cs, days=days)
    c_trades = list(causal.get("_trades") or [])
    base_ids = {tid(t) for t in base_trades}
    causal_ids = {tid(t) for t in c_trades}
    top_n = str(top).replace(".T", "")
    removed_top = [t for t in base_trades if _sym(t) == top_n]
    removed_non = [t for t in base_trades if _sym(t) != top_n and tid(t) not in causal_ids]
    unchanged = [t for t in base_trades if _sym(t) != top_n and tid(t) in causal_ids]
    newly = [t for t in c_trades if tid(t) not in base_ids]
    causal["newly_admitted_trade_n"] = len(newly)
    causal["newly_admitted_trade_pnl"] = float(sum(float(t.get("pnl_yen_100") or 0.0) for t in newly))
    causal["removed_top_symbol_trade_n"] = len(removed_top)
    causal["removed_non_top_trade_n"] = len(removed_non)
    causal["unchanged_non_top_trade_n"] = len(unchanged)
    causal["excluded_symbol"] = top_n
    return causal


def strip(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in {"_trades", "_causal"}}


def other_gate_pass(row: dict[str, Any]) -> bool:
    return coverage_ok(row) and economic_other_ok(row)


def rank_corrected(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    passed = [r for r in rows if r.get("corrected_gate") == "PASS"]
    passed.sort(
        key=lambda r: (
            -float(r.get("score") or -1e18),
            -_pf_sort_key(r.get("PF")),
            -int(r.get("TRADE_N") or 0),
            str(r.get("candidate_id") or ""),
        )
    )
    return passed


def evaluate_corrected_set(
    rows_by: dict[str, list[dict[str, Any]]],
    ctrl_by: dict[str, list[dict[str, Any]]],
    *,
    days: list[str],
) -> list[dict[str, Any]]:
    out = []
    dayset = set(days)
    for cid in candidate_ids():
        e, x, _z = parse_cid(cid)
        rs = [r for r in (rows_by.get(cid) or []) if str(r.get("date") or "") in dayset]
        cs = [r for r in (ctrl_by.get(f"{e}_{x}") or []) if str(r.get("date") or "") in dayset]
        ev = evaluate_candidate(cid, rs, cs, days=days)
        trades = list(ev.get("_trades") or [])
        ev["_trades"] = trades
        other = other_gate_pass(ev)
        ev["other_gates"] = other
        if other:
            top, _pnl = top_symbol_from_trades(trades)
            if top:
                causal = causal_ex_top1(cid, rs, cs, days=days, top=top, base_trades=trades)
                cpnl = float(causal.get("TOTAL_PNL") or 0.0)
                ev["CAUSAL_EX_TOP1_PNL"] = cpnl
                ev["CAUSAL_EX_TOP1_PF"] = causal.get("PF")
                ev["CAUSAL_EX_TOP1_MAXDD"] = causal.get("MAXDD")
                ev["CAUSAL_EX_TOP1_positive_day_n"] = causal.get("positive_day_n")
                ev["CAUSAL_EX_TOP1_negative_day_n"] = causal.get("negative_day_n")
                ev["CAUSAL_EX_TOP1_zero_day_n"] = causal.get("zero_day_n")
                ev["CAUSAL_EX_TOP1_TRADE_N"] = causal.get("TRADE_N")
                ev["CAUSAL_EX_TOP1_signal_n"] = causal.get("signal_n")
                ev["CAUSAL_EX_TOP1_fill_n"] = causal.get("fill_n")
                ev["CAUSAL_EX_TOP1_best_day"] = causal.get("best_day")
                ev["CAUSAL_EX_TOP1_worst_day"] = causal.get("worst_day")
                ev["fold_top_symbol"] = top
                ev["newly_admitted_trade_n"] = causal.get("newly_admitted_trade_n")
                ev["newly_admitted_trade_pnl"] = causal.get("newly_admitted_trade_pnl")
                ev["removed_top_symbol_trade_n"] = causal.get("removed_top_symbol_trade_n")
                ev["removed_non_top_trade_n"] = causal.get("removed_non_top_trade_n")
                ev["unchanged_non_top_trade_n"] = causal.get("unchanged_non_top_trade_n")
                ev["_causal"] = strip(causal)
                ev["score"] = corrected_score(ev, cpnl)
                ev["corrected_gate"] = "PASS" if cpnl >= 0.0 else "CAUSAL_EX_TOP1_FAIL"
            else:
                ev["CAUSAL_EX_TOP1_PNL"] = ev.get("TOTAL_PNL")
                ev["score"] = corrected_score(ev, float(ev.get("TOTAL_PNL") or 0.0))
                ev["corrected_gate"] = "PASS"
        else:
            ev["corrected_gate"] = "COVERAGE_FAIL" if not coverage_ok(ev) else "ECONOMIC_FAIL"
            ev["score"] = None
        out.append(ev)
    return out


def lodo(
    rows_by: dict[str, list[dict[str, Any]]],
    ctrl_by: dict[str, list[dict[str, Any]]],
    winner_id: Optional[str],
) -> dict[str, Any]:
    days = list(DEVELOPMENT_DAYS)
    folds = []
    selected_n = 0
    top3_n = 0
    for leave in days:
        keep = [d for d in days if d != leave]
        ranked = evaluate_corrected_set(rows_by, ctrl_by, days=keep)
        passed = rank_corrected(ranked)
        win = passed[0]["candidate_id"] if passed else None
        top3 = [r["candidate_id"] for r in passed[:3]]
        folds.append(
            {
                "leave": leave,
                "winner": win,
                "top3": top3,
                "pass_n": len(passed),
                "fold_top_symbol": None if not passed else passed[0].get("fold_top_symbol"),
            }
        )
        if winner_id and win == winner_id:
            selected_n += 1
        if winner_id and winner_id in top3:
            top3_n += 1
        print(f"LODO leave={leave} winner={win} top3={top3} pass_n={len(passed)}", flush=True)
    return {
        "folds": folds,
        "WINNER_SELECTED_N": selected_n,
        "WINNER_TOP3_N": top3_n,
        "TOP3_N": top3_n,
        "stable": bool(winner_id) and int(top3_n) >= int(LODO_TOP3_MIN),
    }


def decide(
    *,
    integrity: bool,
    parity_ok_flag: bool,
    method_class: str,
    e4_causal_pnl: Optional[float],
    pass_n: int,
    winner_id: Optional[str],
    lodo_pack: dict[str, Any] | None,
) -> dict[str, Any]:
    base = {
        "CERTIFIED": False,
        "TRUE_OOS": False,
        "SIZING": False,
        "STRESS_OPENED": False,
        "FULL_STRATEGY_DEV_FROZEN": False,
        "ARCHITECTURE_REDESIGN_ALLOWED": False,
    }
    if not integrity or not parity_ok_flag:
        return {**base, "CASE": "E", "VERDICT": CASE_E, "NEXT": "STOP. DO_NOT_INTERPRET_ECONOMICS."}
    if method_class == "B":
        return {
            **base,
            "CASE": "TRUE_DEP",
            "VERDICT": CASE_TRUE_DEP,
            "ARCHITECTURE_REDESIGN_ALLOWED": True,
            "NEXT": "Prior DROP_TOP already causal. E4 close. Architecture redesign. Do not open Stress.",
        }
    if int(pass_n) <= 0:
        if e4_causal_pnl is not None and float(e4_causal_pnl) < 0.0:
            return {
                **base,
                "CASE": "TRUE_DEP",
                "VERDICT": CASE_TRUE_DEP,
                "ARCHITECTURE_REDESIGN_ALLOWED": True,
                "NEXT": "CAUSAL_EX_TOP1_PNL < 0. E4 close. Architecture redesign. Do not open Stress.",
            }
        return {
            **base,
            "CASE": "B",
            "VERDICT": CASE_B,
            "ARCHITECTURE_REDESIGN_ALLOWED": True,
            "NEXT": "No corrected PASS candidate. Architecture redesign. Do not open Stress.",
        }
    if not bool((lodo_pack or {}).get("stable")):
        return {
            **base,
            "CASE": "D",
            "VERDICT": CASE_D,
            "ARCHITECTURE_REDESIGN_ALLOWED": True,
            "NEXT": "SELECTION_UNSTABLE. Do not open Stress. Do not retune thresholds.",
        }
    if winner_id == FOCUS_CANDIDATE:
        return {
            **base,
            "CASE": "A",
            "VERDICT": CASE_A,
            "FULL_STRATEGY_DEV_FROZEN": True,
            "NEXT": "Open 4-day REUSED_HISTORY_STRESS for this one frozen candidate only. Not this run.",
        }
    return {
        **base,
        "CASE": "C",
        "VERDICT": CASE_C,
        "FULL_STRATEGY_DEV_FROZEN": True,
        "NEXT": "Corrected selection chose a non-E4 winner. Frozen after LODO. Stress not this run.",
    }


def public_pass_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "candidate_id": row.get("candidate_id"),
        "PnL": row.get("TOTAL_PNL"),
        "PF": ("inf" if row.get("PF") == float("inf") else row.get("PF")),
        "MaxDD": row.get("MAXDD"),
        "positive_days": row.get("positive_day_n"),
        "negative_days": row.get("negative_day_n"),
        "EX_BEST": row.get("EX_BEST_DAY_PNL"),
        "CAUSAL_EX_TOP1": row.get("CAUSAL_EX_TOP1_PNL"),
        "score": row.get("score"),
        "TRADE_N": row.get("TRADE_N"),
        "fold_top_symbol": row.get("fold_top_symbol"),
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    method = dict(report.get("method_audit") or {})
    e4 = dict(report.get("e4_pack") or {})
    top = dict(report.get("top_symbol_pack") or {})
    post = dict(report.get("posthoc") or {})
    causal = dict(report.get("causal") or {})
    lodo_p = dict(report.get("lodo") or {})
    w = dict(report.get("provisional_winner") or {})
    return {
        "1_prior_verdict": PRIOR_VERDICT,
        "2_why_concentration_audit_required": "E4_X2_Z3 passed every economic gate except DROP_TOP_SYMBOL. Need to know if that gate was Full Causal exclusion or posthoc ledger subtraction, then restore original LODO.",
        "3_base_parity": (report.get("parity") or {}).get("ok"),
        "4_prior_DROP_TOP_method": method.get("DROP_TOP_SYMBOL_METHOD"),
        "5_source_file_function": {
            "file": method.get("SOURCE_FILE"),
            "function": method.get("SOURCE_FUNCTION"),
            "line_start": method.get("SOURCE_LINE_START"),
            "snippet": method.get("SOURCE_SNIPPET"),
        },
        "6_was_prior_DROP_TOP_causal": method.get("PRIOR_DROP_TOP_CAUSAL"),
        "7_top_symbol": top.get("symbol"),
        "8_top_symbol_trade_n": top.get("trade_n"),
        "9_top_symbol_PnL": top.get("net_pnl"),
        "10_top_symbol_normalized_bps_diagnostic": top.get("bps"),
        "11_top_symbol_notional_diagnostic": top.get("notional"),
        "12_posthoc_EX_TOP1_PnL": post.get("TOTAL_PNL"),
        "13_causal_EX_TOP1_PnL": causal.get("TOTAL_PNL"),
        "14_causal_EX_TOP1_PF": causal.get("PF"),
        "15_causal_EX_TOP1_MaxDD": causal.get("MAXDD"),
        "16_causal_EX_TOP1_day_signs": {
            "positive": causal.get("positive_day_n"),
            "negative": causal.get("negative_day_n"),
            "zero": causal.get("zero_day_n"),
        },
        "17_newly_admitted_trade_n": causal.get("newly_admitted_trade_n"),
        "18_newly_admitted_PnL": causal.get("newly_admitted_trade_pnl"),
        "19_CAUSAL_REFILL_EFFECT": report.get("CAUSAL_REFILL_EFFECT"),
        "20_corrected_E4_economic_gate": e4.get("corrected_gate"),
        "21_corrected_economic_PASS_candidate_n": report.get("CORRECTED_ECONOMIC_PASS_N"),
        "22_corrected_PASS_candidate_ids": report.get("corrected_pass_ids"),
        "23_provisional_winner": w.get("candidate_id"),
        "24_robust_score": w.get("score"),
        "25_LODO_ran": bool(lodo_p),
        "26_fold_winners": [f.get("winner") for f in (lodo_p.get("folds") or [])],
        "27_fold_top3s": [f.get("top3") for f in (lodo_p.get("folds") or [])],
        "28_provisional_winner_selected_n": lodo_p.get("WINNER_SELECTED_N"),
        "29_provisional_winner_TOP3_N": lodo_p.get("WINNER_TOP3_N"),
        "30_selection_stable": lodo_p.get("stable"),
        "31_FULL_STRATEGY_DEV_FROZEN": bool(d.get("FULL_STRATEGY_DEV_FROZEN")),
        "32_Stress_opened": False,
        "33_burned_Holdout_read": False,
        "34_Sizing_ran": False,
        "35_architecture_redesign_allowed": bool(d.get("ARCHITECTURE_REDESIGN_ALLOWED")),
        "36_verdict": d.get("VERDICT"),
        "37_next": d.get("NEXT"),
        "38_Runtime_changed": False,
        "39_future_used": bool(int((report.get("leakage") or {}).get("FUTURE_DATA_N") or 0)),
        "40_MAX_RESEARCH_DATE": "20260807",
        "41_TRUE_OOS": False,
        "42_CERTIFIED": False,
        "43_submit_cancel_live": "0/0/0",
        "PRIOR_ECONOMIC_PASS_N": PRIOR_ECONOMIC_PASS_N,
    }
