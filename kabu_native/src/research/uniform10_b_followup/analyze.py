"""B0 parity, rank bias, nested Exact B1, optional B2. Occupancy forbidden."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

import numpy as np

from research.executable_target_v2_b_threshold.analyze import pack_metrics
from research.uniform10_b_followup import (
    B0_DD_TOL,
    B0_PF_TOL,
    B0_PNL_TOL,
    B0_TRADE_TOL,
    B1_PERCENTILES,
    BIAS_ABS_GAP_MIN,
    BIAS_ENRICHMENT_MIN,
    EXPECTED_B0_MAXDD,
    EXPECTED_B0_PF,
    EXPECTED_B0_PNL,
    EXPECTED_B0_TRADES,
    MIN_TRADE_RETENTION,
)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _pf(v: Any) -> float:
    if v is None:
        return 0.0
    if v == "Infinity" or v == float("inf"):
        return 9.0
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _dd(v: Any) -> float:
    x = _f(v)
    return float(x) if x is not None else -1e18


def label_p(p: Any) -> str:
    return "NO_THRESHOLD" if p is None else f"p{int(p)}"


def b0_parity(pack: dict[str, Any]) -> dict[str, Any]:
    trades = int(pack.get("trades") or 0)
    pnl = float(pack.get("PnL") if pack.get("PnL") is not None else pack.get("pnl") or 0.0)
    pf = _pf(pack.get("PF"))
    dd = float(pack.get("maxDD") or 0.0)
    ok = (
        abs(trades - int(EXPECTED_B0_TRADES)) <= int(B0_TRADE_TOL)
        and abs(pnl - float(EXPECTED_B0_PNL)) <= float(B0_PNL_TOL)
        and abs(pf - float(EXPECTED_B0_PF)) <= float(B0_PF_TOL)
        and abs(dd - float(EXPECTED_B0_MAXDD)) <= float(B0_DD_TOL)
    )
    return {
        "ok": ok,
        "observed": {"trades": trades, "PnL": pnl, "PF": pack.get("PF"), "maxDD": dd},
        "expected": {
            "trades": EXPECTED_B0_TRADES,
            "PnL": EXPECTED_B0_PNL,
            "PF": EXPECTED_B0_PF,
            "maxDD": EXPECTED_B0_MAXDD,
        },
    }


def _topk_rate(rows: list[dict[str, Any]], k: int) -> Optional[float]:
    xs = [r for r in rows if _f(r.get("rank")) is not None and float(r["rank"]) <= float(k) + 1e-12]
    if not xs:
        return None
    return sum(1 for r in xs if not r.get("executable_at_t0")) / len(xs)


def rank_audit(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [r for r in rows if _f(r.get("score")) is not None and _f(r.get("rank")) is not None]
    n = len(scored)
    non = [r for r in scored if not r.get("executable_at_t0")]
    exe = [r for r in scored if r.get("executable_at_t0")]
    all_rate = (len(non) / n) if n else None
    top1 = _topk_rate(scored, 1)
    top3 = _topk_rate(scored, 3)
    top5 = _topk_rate(scored, 5)
    top10 = _topk_rate(scored, 10)
    def _enr(rate: Optional[float]) -> Optional[float]:
        if rate is None or all_rate in (None, 0):
            return None
        return float(rate) / float(all_rate)

    enr1 = _enr(top1)
    enr3 = _enr(top3)
    enr5 = _enr(top5)
    enr10 = _enr(top10)
    buckets = Counter(str(r.get("nonexec_bucket") or "EXECUTABLE") for r in scored if not r.get("executable_at_t0"))

    def _score_q(xs):
        ss = [_f(r.get("score")) for r in xs]
        ss = [x for x in ss if x is not None]
        if not ss:
            return {"n": 0, "mean": None, "median": None}
        arr = np.asarray(ss, dtype=float)
        return {"n": int(arr.size), "mean": float(np.mean(arr)), "median": float(np.median(arr))}

    def _rank_q(xs):
        ss = [_f(r.get("rank")) for r in xs]
        ss = [x for x in ss if x is not None]
        if not ss:
            return {"n": 0, "mean": None, "median": None}
        arr = np.asarray(ss, dtype=float)
        return {"n": int(arr.size), "mean": float(np.mean(arr)), "median": float(np.median(arr))}

    gap3 = None if top3 is None or all_rate is None else float(top3) - float(all_rate)
    bias = bool(
        (enr3 is not None and float(enr3) >= float(BIAS_ENRICHMENT_MIN))
        or (gap3 is not None and float(gap3) >= float(BIAS_ABS_GAP_MIN))
    )
    return {
        "n_scored": n,
        "n_executable": len(exe),
        "n_nonexec": len(non),
        "NONEXEC_ALL_RATE": all_rate,
        "NONEXEC_TOP1_RATE": top1,
        "NONEXEC_TOP3_RATE": top3,
        "NONEXEC_TOP5_RATE": top5,
        "NONEXEC_TOP10_RATE": top10,
        "NONEXEC_TOP1_ENRICHMENT": enr1,
        "NONEXEC_TOP3_ENRICHMENT": enr3,
        "NONEXEC_TOP5_ENRICHMENT": enr5,
        "NONEXEC_TOP10_ENRICHMENT": enr10,
        "NONEXEC_TOP3_ABS_GAP": gap3,
        "mean_rank_executable": _rank_q(exe).get("mean"),
        "mean_rank_nonexec": _rank_q(non).get("mean"),
        "score_executable": _score_q(exe),
        "score_nonexec": _score_q(non),
        "nonexec_buckets": dict(buckets),
        "CURRENT_ENTRY_NONEXEC_RANK_BIAS": bias,
        "bias_rule": (
            f"enrichment>= {BIAS_ENRICHMENT_MIN} or abs_gap>= {BIAS_ABS_GAP_MIN}; "
            "frozen before seeing rates"
        ),
    }


def flow_join(*, rank_rows: list[dict], admits: list[dict], fills: list[dict], expired: list[dict]) -> dict[str, Any]:
    idx = {(str(r.get("date")), str(r.get("anchor")), str(r.get("symbol"))): r for r in rank_rows}

    def _flag(rec: dict) -> Optional[bool]:
        key = (str(rec.get("date")), str(rec.get("anchor")), str(rec.get("symbol") or "").replace(".T", ""))
        hit = idx.get(key)
        if hit is None:
            return None
        return not bool(hit.get("executable_at_t0"))

    pending_n = len(admits)
    pending_ne = sum(1 for a in admits if _flag(a) is True)
    fill_n = len(fills)
    fill_ne = sum(1 for a in fills if _flag(a) is True)
    exp_n = len(expired)
    exp_ne = sum(1 for a in expired if _flag(a) is True)

    def _opens(rec: dict) -> bool:
        key = (str(rec.get("date")), str(rec.get("anchor")), str(rec.get("symbol") or "").replace(".T", ""))
        hit = idx.get(key)
        return bool(hit and hit.get("opens_within_1s"))

    pending_ne_open1s = sum(1 for a in admits if _flag(a) is True and _opens(a))
    pending_ne_never_fill = pending_ne - fill_ne
    scored_non = [r for r in rank_rows if not r.get("executable_at_t0")]
    scored_open1s = sum(1 for r in scored_non if r.get("opens_within_1s"))
    return {
        "PENDING_N": pending_n,
        "PENDING_NONEXEC_AT_T0_N": pending_ne,
        "PENDING_NONEXEC_AT_T0_RATE": pending_ne / pending_n if pending_n else None,
        "FILL_N": fill_n,
        "FILL_FROM_NONEXEC_AT_T0_N": fill_ne,
        "EXPIRED_N": exp_n,
        "EXPIRED_FROM_NONEXEC_AT_T0_N": exp_ne,
        "EXPIRED_FROM_NONEXEC_RATE": exp_ne / exp_n if exp_n else None,
        "PENDING_NONEXEC_OPENED_WITHIN_1S_N": pending_ne_open1s,
        "PENDING_NONEXEC_NEVER_FILLED_N": pending_ne_never_fill,
        "SCORED_NONEXEC_OPENED_WITHIN_1S_N": scored_open1s,
        "note": "opens_within_1s is diagnostic only. Not an ENTRY feature or gate.",
    }


def opportunity_cost_b2(rank_rows: list[dict], trades: list[dict]) -> dict[str, Any]:
    """B0 fills that were non-executable at t0 — dropped if B2 t0-exec gate is applied."""
    idx = {(str(r.get("date")), str(r.get("anchor")), str(r.get("symbol"))): r for r in rank_rows}
    hit: list[dict] = []
    open1s = 0
    for t in trades:
        key = (
            str(t.get("date")),
            str(t.get("anchor_time") or t.get("anchor") or ""),
            str(t.get("symbol") or "").replace(".T", ""),
        )
        row = idx.get(key)
        if row is None or row.get("executable_at_t0"):
            continue
        hit.append(t)
        if row.get("opens_within_1s"):
            open1s += 1
    pnl = float(sum(float(t.get("pnl_yen_100") or 0.0) for t in hit))
    return {
        "B2_WOULD_DROP_FILL_N": len(hit),
        "B2_WOULD_DROP_FILL_PNL": pnl,
        "B2_WOULD_DROP_OPENED_WITHIN_1S_N": open1s,
        "note": "Opportunity cost of B2_EXECUTABLE_T0. Diagnostic only. Not an ENTRY feature.",
    }


def compare_pack(b0: dict[str, Any], other: dict[str, Any], *, tag: str) -> dict[str, Any]:
    ex0 = b0.get("exclude") or {}
    ex1 = other.get("exclude") or {}
    keys = (
        "trades", "PnL", "PF", "maxDD", "avg_trade", "median_trade",
        "positive_day_rate", "median_daily_pnl",
    )
    out = {"tag": tag}
    for k in keys:
        out[f"B0_{k}"] = b0.get(k)
        out[f"new_{k}"] = other.get(k)
    out["B0_AM_pnl"] = (b0.get("AM") or {}).get("pnl")
    out["new_AM_pnl"] = (other.get("AM") or {}).get("pnl")
    out["B0_PM_pnl"] = (b0.get("PM") or {}).get("pnl")
    out["new_PM_pnl"] = (other.get("PM") or {}).get("pnl")
    out["B0_first_pnl"] = (b0.get("first_entry") or {}).get("pnl")
    out["new_first_pnl"] = (other.get("first_entry") or {}).get("pnl")
    out["B0_reentry_pnl"] = (b0.get("re_entry") or {}).get("pnl")
    out["new_reentry_pnl"] = (other.get("re_entry") or {}).get("pnl")
    for name in ("ex_top3_trades", "ex_top3_days", "ex_top_symbol", "ex_285A"):
        out[f"B0_{name}_PnL"] = (ex0.get(name) or {}).get("PnL")
        out[f"new_{name}_PnL"] = (ex1.get(name) or {}).get("PnL")
    ret = None
    if b0.get("trades"):
        ret = (other.get("trades") or 0) / float(b0["trades"])
    out["trade_retention"] = ret
    return out


def robust_vs_b0(m: dict[str, Any], b0: dict[str, Any]) -> dict[str, Any]:
    ret = None
    if b0.get("trades"):
        ret = (m.get("trades") or 0) / float(b0["trades"])
    med_ok = float(m.get("median_daily_pnl") or -1e18) > float(b0.get("median_daily_pnl") or -1e18)
    pf_ok = _pf(m.get("PF")) + 1e-12 >= _pf(b0.get("PF"))
    dd_ok = _dd(m.get("maxDD")) + 1e-9 >= _dd(b0.get("maxDD"))
    ret_ok = ret is None or float(ret) >= float(MIN_TRADE_RETENTION)
    ok = bool(med_ok and pf_ok and dd_ok and ret_ok)
    return {
        "median_daily_pnl_beats": med_ok,
        "PF_not_worse": pf_ok,
        "maxDD_not_worse": dd_ok,
        "trade_retention_ok": ret_ok,
        "trade_retention": ret,
        "robust": ok,
    }


def inner_select(train_by_p: dict[Any, list[dict[str, Any]]], days: list[str]) -> Any:
    """Primary: daily expected value / robustness. Not PnL-max. Not all-4 mechanical NOR."""
    b0 = pack_metrics(train_by_p.get(None) or [], days)
    best_p: Any = None
    best_key = None
    for p in B1_PERCENTILES:
        if p is None:
            continue
        m = pack_metrics(train_by_p.get(p) or [], days)
        chk = robust_vs_b0(m, b0)
        if not chk.get("robust"):
            continue
        key = (
            float(m.get("median_daily_pnl") or -1e18),
            float(m.get("positive_day_rate") or 0.0),
            _dd(m.get("maxDD")),
            _pf(m.get("PF")),
        )
        if best_key is None or key > best_key:
            best_key = key
            best_p = p
    return best_p


def nested_exact(trades_by_day_p: dict[str, dict[Any, list[dict[str, Any]]]], days: list[str]) -> dict[str, Any]:
    oof: list[dict[str, Any]] = []
    folds = []
    for held in days:
        train_days = [d for d in days if d != held]
        train_by_p: dict[Any, list] = {p: [] for p in B1_PERCENTILES}
        for d in train_days:
            for p in B1_PERCENTILES:
                train_by_p[p].extend(trades_by_day_p.get(d, {}).get(p) or [])
        chosen = inner_select(train_by_p, train_days)
        held_trades = list(trades_by_day_p.get(held, {}).get(chosen) or [])
        for t in held_trades:
            rec = dict(t)
            rec["oof_held_out_day"] = held
            rec["selected_percentile"] = chosen
            oof.append(rec)
        inner_m = pack_metrics(train_by_p[chosen], train_days)
        folds.append(
            {
                "held_out_day": held,
                "selected_percentile": label_p(chosen),
                "selected_p": chosen,
                "inner_trades": inner_m.get("trades"),
                "inner_PnL": inner_m.get("PnL"),
                "inner_PF": inner_m.get("PF"),
                "inner_maxDD": inner_m.get("maxDD"),
                "inner_positive_day_rate": inner_m.get("positive_day_rate"),
                "inner_median_daily_pnl": inner_m.get("median_daily_pnl"),
                "outer_trades": len(held_trades),
            }
        )
    counts = Counter(f["selected_p"] for f in folds)
    mode_p = counts.most_common(1)[0][0] if counts else None
    mixed = len([k for k in counts if counts[k] > 0]) > 1
    return {
        "oof_trades": oof,
        "folds": folds,
        "mode_p": mode_p,
        "BEST_B1_THRESHOLD": label_p(mode_p),
        "mixed_fold_selection": mixed,
        "fold_counts": {label_p(k): v for k, v in counts.items()},
    }


def slim_pack(pack: dict[str, Any]) -> dict[str, Any]:
    return {k: pack.get(k) for k in (
        "trades", "PnL", "PF", "maxDD", "avg_trade", "median_trade",
        "positive_day_rate", "median_daily_pnl", "AM", "PM", "first_entry", "re_entry",
    )}


def verdict_pack(
    *,
    parity_ok: bool,
    bias: bool,
    b1_robust: bool,
    b2_tested: bool,
    b2_robust: Optional[bool],
    b1_label: str,
) -> dict[str, Any]:
    if not parity_ok:
        verdict = "B_INTEGRITY_FAILED"
        rec = "NONE"
    elif b1_robust:
        verdict = "B1_EXACT_THRESHOLD_IMPROVEMENT_FOUND"
        rec = "B1"
    elif b2_tested and b2_robust:
        verdict = "B2_EXECUTABLE_GATE_IMPROVEMENT_FOUND"
        rec = "B2"
    elif bias and b2_tested and not b2_robust:
        verdict = "B_NO_ROBUST_IMPROVEMENT"
        rec = "B0"
    elif bias:
        verdict = "B_CURRENT_ENTRY_EXECUTABILITY_BIAS_FOUND"
        rec = "B0"
    elif not b1_robust:
        verdict = "B_CURRENT_ENTRY_NO_EXECUTABILITY_BIAS"
        rec = "B0"
    else:
        verdict = "B1_EXACT_NO_ROBUST_THRESHOLD"
        rec = "B0"
    nxt = (
        "Do not write B into Runtime. C2 unchanged. Await explicit instruction. "
        "Per-feature thresholds remain DO_NOT_START."
    )
    if not parity_ok:
        nxt = "STOP. B0 Exact Dual-Lane did not match the known headline. Do not start B1/B2 Runtime."
    return {
        "VERDICT": verdict,
        "RECOMMENDED_B_VARIANT": rec,
        "B1_SELECTED_THRESHOLD": b1_label,
        "B1_ROBUST_IMPROVEMENT": b1_robust,
        "B2_TESTED": b2_tested,
        "B2_ROBUST_IMPROVEMENT": b2_robust,
        "CURRENT_ENTRY_NONEXEC_RANK_BIAS": bias,
        "RECOMMENDED_NEXT_STEP": nxt,
    }
