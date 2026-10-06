"""Coverage, common ranking, fill identities, first-entry, EXIT classes. No new model."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

import numpy as np

from research.anchor_vs_event_driven.run_comparison import _bare
from research.c3_execution_coupling_audit.analyze import admit_clock, stats_block, topk_block
from research.c3_execution_reconciliation import F1_LOOKBACK, F1_RAW
from research.edge_decay_rca.analyze import tag_entry_kind
from research.entry_objective_redesign_c3.oof import TARGET, ranking_metrics, ranking_pop
from research.executable_target_v2_b_threshold.analyze import pack_metrics


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def abc_line(pack: dict[str, Any] | None) -> str:
    if not pack:
        return "n/a"
    return f"{pack.get('trades')} / {pack.get('PnL') if pack.get('PnL') is not None else pack.get('pnl')} / {pack.get('PF')} / {pack.get('maxDD')}"


def row_key(r: dict[str, Any]) -> str:
    return f"{r.get('date')}|{r.get('anchor')}|{_bare(r.get('symbol'))}"


def classify_missing(r: dict[str, Any]) -> dict[str, Any]:
    lookback_hit: list[str] = []
    feature_hit: list[str] = []
    for f in F1_RAW:
        v = _f(r.get(f))
        if v is None:
            if f in F1_LOOKBACK:
                lookback_hit.append(f)
            else:
                feature_hit.append(f)
    imb = _f(r.get("imbalance"))
    xs = _f(r.get("xs_imbalance_z"))
    if lookback_hit:
        reason = "LOOKBACK_INSUFFICIENT"
        feat = lookback_hit[0]
    elif feature_hit:
        reason = "FEATURE_MISSING"
        feat = feature_hit[0]
    elif xs is None and imb is not None:
        reason = "NORMALIZATION_FAILURE"
        feat = "xs_imbalance_z"
    elif xs is None:
        reason = "FEATURE_MISSING"
        feat = "xs_imbalance_z"
    else:
        raw_ok = all(_f(r.get(f)) is not None for f in (*F1_RAW, "xs_imbalance_z"))
        reason = "LOOKUP_MISS" if raw_ok else "OTHER"
        feat = None
    return {
        "date": r.get("date"),
        "anchor": r.get("anchor"),
        "symbol": _bare(r.get("symbol")),
        "reason": reason,
        "feature": feat,
    }


def coverage_block(
    ranking: list[dict[str, Any]],
    *,
    oof_by: dict[str, float],
) -> dict[str, Any]:
    available = []
    missing = []
    for r in ranking:
        k = row_key(r)
        oof = oof_by.get(k)
        if oof is not None:
            available.append(r)
        else:
            missing.append(classify_missing(r))
    by_reason = Counter(m["reason"] for m in missing)
    by_feat: Counter[str] = Counter()
    by_date: Counter[str] = Counter()
    by_anchor: Counter[str] = Counter()
    for m in missing:
        by_date[str(m.get("date"))] += 1
        by_anchor[str(m.get("anchor"))] += 1
        if m.get("feature"):
            by_feat[str(m["feature"])] += 1
    reason_order = (
        "FEATURE_MISSING",
        "LOOKBACK_INSUFFICIENT",
        "NONFINITE_FEATURE",
        "NORMALIZATION_FAILURE",
        "LOOKUP_MISS",
        "OTHER",
    )
    reason_counts = {k: int(by_reason.get(k, 0)) for k in reason_order}
    return {
        "EXECUTABLE_T0_ROWS": len(ranking),
        "C3_SCORE_AVAILABLE_N": len(available),
        "C3_SCORE_MISSING_N": len(missing),
        "missing_reason_counts": reason_counts,
        "missing_by_feature": dict(by_feat),
        "missing_by_date": dict(by_date),
        "missing_by_anchor": dict(by_anchor),
        "missing_head": missing[:40],
    }


def common_scorable(
    ranking: list[dict[str, Any]],
    *,
    oof_by: dict[str, float],
    final_by: dict[str, float],
) -> list[dict[str, Any]]:
    out = []
    for r in ranking:
        k = row_key(r)
        cur = _f(r.get("current_score"))
        oof = oof_by.get(k)
        fin = final_by.get(k)
        if cur is None or oof is None or fin is None:
            continue
        rec = dict(r)
        rec["c3_oof_score"] = oof
        rec["c3_final_score"] = fin
        rec["symbol"] = _bare(rec.get("symbol"))
        out.append(rec)
    return out


def attach_would_fill(rows: list[dict[str, Any]], wf_by: dict[str, dict[str, Any]]) -> None:
    for r in rows:
        wf = wf_by.get(row_key(r)) or {}
        r["WOULD_FILL_1S"] = bool(wf.get("WOULD_FILL_1S")) if wf else False
        fill_px = _f(wf.get("fill_price"))
        mark = _f(r.get("m4_t1_px"))
        if r["WOULD_FILL_1S"] and fill_px is not None and fill_px > 0 and mark is not None and mark > 0:
            r["FILL_TO_600S_RETURN"] = float(mark) / float(fill_px) - 1.0
        else:
            r["FILL_TO_600S_RETURN"] = None


def trade_admit_key(t: dict[str, Any]) -> str:
    day = str(t.get("date") or "")
    clock = admit_clock(day, t.get("fill_time")) or str(t.get("anchor_time") or t.get("anchor") or "")
    return f"{day}|{clock}|{_bare(t.get('symbol'))}"


def original_vs_common_overlap(
    *,
    trades: list[dict[str, Any]],
    ranking_keys: set[str],
    common_keys: set[str],
    panel_by: dict[str, dict[str, Any]],
    lane: str,
) -> dict[str, Any]:
    in_rank = 0
    in_common = 0
    panel_not_rank = 0
    no_panel = 0
    rank_no_c3 = 0
    reasons: Counter[str] = Counter()
    for t in trades:
        k = trade_admit_key(t)
        if k in common_keys:
            in_common += 1
            in_rank += 1
            continue
        if k in ranking_keys:
            in_rank += 1
            rank_no_c3 += 1
            r = panel_by.get(k) or {}
            reasons[classify_missing(r)["reason"]] += 1
            continue
        if k in panel_by:
            panel_not_rank += 1
            r = panel_by[k]
            if not r.get("executable_at_t0"):
                reasons["LIVE_FILL_PANEL_NOT_EXECUTABLE"] += 1
            else:
                reasons["PANEL_NOT_RANKING_POP"] += 1
            continue
        no_panel += 1
        reasons["NO_PANEL_ROW"] += 1
    n = len(trades)
    return {
        "lane": lane,
        "CLOSED_TRADE_N": n,
        "ON_RANKING_POP_N": in_rank,
        "ON_COMMON_SCORABLE_N": in_common,
        "RANKING_BUT_C3_SCORE_MISSING_N": rank_no_c3,
        "PANEL_NOT_RANKING_POP_N": panel_not_rank,
        "NO_PANEL_ROW_N": no_panel,
        "missing_join_reasons": dict(reasons),
        "note": (
            "Join is admit_clock(fill_time)×symbol, not packed anchor_time. "
            "ON_COMMON_SCORABLE_N is original Exact closed trades that sit on the "
            "COMMON_SCORABLE ranking keys. Packed Dual-Lane anchor_time can disagree "
            "with fill_time (known 15:00 remap)."
        ),
    }


def fill_identity(
    *,
    trades: list[dict[str, Any]],
    admits: list[dict[str, Any]],
    harvest_fills: list[dict[str, Any]],
    expired: list[dict[str, Any]],
    dual_admit: Optional[int] = None,
    dual_exit: Optional[int] = None,
    lane: str,
) -> dict[str, Any]:
    closed = len(trades)
    pending = len(admits)
    harvest = len(harvest_fills)
    port_fill = int(dual_admit) if dual_admit is not None else closed
    note = (
        "PORTFOLIO_FILL_N = Dual-Lane primary ADMIT (fill into lane). "
        "HARVEST_FILL_N = CollectorEngine a_fills (V1R_FILL harvest; incomplete vs Dual-Lane). "
        "CLOSED_TRADE_N = packed completed trades (ADMIT+EXIT_EXECUTED). "
        "PENDING_CREATED_N = V1R_ENTRY_PENDING / a_admits. "
        "STANDALONE_WOULD_FILL_1S is not a portfolio event."
    )
    closed_ok = True
    if dual_exit is not None:
        closed_ok = closed == int(dual_exit)
    fill_ok = True
    if dual_admit is not None:
        fill_ok = closed == int(dual_admit)
    return {
        "lane": lane,
        "PENDING_CREATED_N": pending,
        "HARVEST_FILL_N": harvest,
        "PORTFOLIO_FILL_N": port_fill,
        "CLOSED_TRADE_N": closed,
        "EXPIRED_N": len(expired),
        "DUAL_ADMIT_PRIMARY_N": dual_admit,
        "DUAL_EXIT_PRIMARY_N": dual_exit,
        "HEADLINE_EQ_CLOSED": True,
        "CLOSED_EQ_DUAL_EXIT": closed_ok,
        "PORTFOLIO_FILL_EQ_CLOSED": fill_ok,
        "HARVEST_VS_CLOSED_GAP": harvest - closed,
        "note": note,
    }


def trade_fill_to_600(t: dict[str, Any], panel_idx: dict[tuple[str, str, str], dict[str, Any]]) -> Optional[float]:
    day = str(t.get("date") or "")
    clock = admit_clock(day, t.get("fill_time")) or str(t.get("anchor_time") or t.get("anchor") or "")
    p = panel_idx.get((day, clock, _bare(t.get("symbol")))) or {}
    mark = _f(p.get("m4_t1_px"))
    fp = _f(t.get("fill_price"))
    if mark is None or fp is None or fp <= 0:
        return None
    return float(mark) / float(fp) - 1.0


def first_entry_canonical(trades: list[dict[str, Any]], panel: list[dict[str, Any]]) -> dict[str, Any]:
    tagged = tag_entry_kind(trades)
    first = [t for t in tagged if t.get("entry_kind") == "FIRST_ENTRY"]
    idx = {(str(r.get("date")), str(r.get("anchor")), _bare(r.get("symbol"))): r for r in panel}
    f6s = []
    tgs = []
    for t in first:
        f6 = trade_fill_to_600(t, idx)
        f6s.append(f6)
        day = str(t.get("date") or "")
        clock = admit_clock(day, t.get("fill_time")) or str(t.get("anchor_time") or "")
        p = idx.get((day, clock, _bare(t.get("symbol")))) or {}
        tgs.append(_f(p.get(TARGET)))
    pack = pack_metrics(first, None)
    vs = [v for v in f6s if v is not None]
    return {
        "FIRST_N": len(first),
        "FIRST_TARGET_V4_MEAN": float(np.mean([v for v in tgs if v is not None])) if any(v is not None for v in tgs) else None,
        "FIRST_FILL_TO_600_MEAN": float(np.mean(vs)) if vs else None,
        "FIRST_FILL_TO_600_MEDIAN": float(np.median(vs)) if vs else None,
        "FIRST_FILL_TO_600_POS_RATE": (sum(1 for v in vs if v > 0) / len(vs)) if vs else None,
        "FIRST_ACTUAL_PNL": pack.get("PnL"),
        "FIRST_PF": pack.get("PF"),
        "n_fill_to_600": len(vs),
        "canonical_join": "trade_id/fill_time admit_clock × panel m4_t1_px / fill_price - 1",
    }


def exit_classes(trades: list[dict[str, Any]], panel: list[dict[str, Any]]) -> dict[str, Any]:
    idx = {(str(r.get("date")), str(r.get("anchor")), _bare(r.get("symbol"))): r for r in panel}
    a = b = c = d = miss = 0
    rows = []
    for t in trades:
        f6 = trade_fill_to_600(t, idx)
        pnl = _f(t.get("pnl_yen_100"))
        if f6 is None or pnl is None:
            miss += 1
            klass = "MISSING"
        elif f6 > 0 and pnl > 0:
            a += 1
            klass = "A"
        elif f6 > 0 and pnl <= 0:
            b += 1
            klass = "B"
        elif f6 <= 0 and pnl > 0:
            c += 1
            klass = "C"
        else:
            d += 1
            klass = "D"
        rows.append(
            {
                "trade_id": t.get("trade_id"),
                "date": t.get("date"),
                "symbol": _bare(t.get("symbol")),
                "FILL_TO_600": f6,
                "EXIT_PNL": pnl,
                "class": klass,
            }
        )
    total = a + b + c + d
    return {
        "n_trades": len(trades),
        "C3_EXIT_CLASS_A_N": a,
        "C3_EXIT_CLASS_B_N": b,
        "C3_EXIT_CLASS_C_N": c,
        "C3_EXIT_CLASS_D_N": d,
        "MISSING_N": miss,
        "SUM_ABCD": total,
        "ASSERT_SUM_EQ_53": total == 53 and miss == 0 and len(trades) == 53,
        "rows": rows,
    }


def adverse_on_common(common: list[dict[str, Any]]) -> dict[str, Any]:
    all_t = float(np.mean([_f(r.get(TARGET)) for r in common if _f(r.get(TARGET)) is not None])) if common else None
    cur = topk_block(common, "current_score", 3, all_target=all_t)
    oof = topk_block(common, "c3_oof_score", 3, all_target=all_t)
    fin = topk_block(common, "c3_final_score", 3, all_target=all_t)
    cur_t = cur.get("TARGET_V4_600S")
    oof_t = oof.get("TARGET_V4_600S")
    fin_t = fin.get("TARGET_V4_600S")
    cur_f6 = cur.get("FILL_TO_600S_RETURN_MEAN")
    oof_f6 = oof.get("FILL_TO_600S_RETURN_MEAN")
    fin_f6 = fin.get("FILL_TO_600S_RETURN_MEAN")
    confirmed = bool(
        oof_t is not None
        and cur_t is not None
        and float(oof_t) > float(cur_t)
        and oof_f6 is not None
        and cur_f6 is not None
        and float(oof_f6) < float(cur_f6)
    )
    confirmed_final = bool(
        fin_t is not None
        and cur_t is not None
        and float(fin_t) > float(cur_t)
        and fin_f6 is not None
        and cur_f6 is not None
        and float(fin_f6) < float(cur_f6)
    )
    return {
        "CURRENT_TOP3": cur,
        "C3_OOF_TOP3": oof,
        "C3_FINAL_TOP3": fin,
        "PASSIVE_FILL_ADVERSE_SELECTION_CONFIRMED": bool(confirmed or confirmed_final),
        "confirmed_on_oof": confirmed,
        "confirmed_on_final": confirmed_final,
    }


def merge_lane(bodies: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "trades": [],
        "admits": [],
        "fills": [],
        "expired": [],
        "DUAL_ADMIT_PRIMARY_N": 0,
        "DUAL_EXIT_PRIMARY_N": 0,
    }
    for b in bodies:
        for k in ("trades", "admits", "fills", "expired"):
            out[k].extend(b.get(k) or [])
        out["DUAL_ADMIT_PRIMARY_N"] += int(b.get("DUAL_ADMIT_PRIMARY_N") or 0)
        out["DUAL_EXIT_PRIMARY_N"] += int(b.get("DUAL_EXIT_PRIMARY_N") or 0)
    return out


def decide(
    *,
    common_n: int,
    exec_n: int,
    ranking: dict[str, Any],
    a2_pack: dict[str, Any] | None,
    a2_common: dict[str, Any] | None,
    c3_oof_common: dict[str, Any] | None,
    c3_final_common: dict[str, Any] | None,
    adverse: dict[str, Any],
    identities: list[dict[str, Any]],
    exit_ok: bool,
) -> dict[str, Any]:
    id_fail = any(
        (r.get("DUAL_ADMIT_PRIMARY_N") is not None and not r.get("PORTFOLIO_FILL_EQ_CLOSED"))
        or (r.get("DUAL_EXIT_PRIMARY_N") is not None and not r.get("CLOSED_EQ_DUAL_EXIT"))
        for r in identities
        if str(r.get("lane") or "").endswith("COMMON")
    )
    if id_fail or not exit_ok:
        return {
            "VERDICT": "C3_EXECUTION_AUDIT_INTEGRITY_FAILED",
            "PRIMARY_CAUSE_AFTER_RECONCILIATION": "COUNT_IDENTITY_FAIL",
            "NEXT_RESEARCH": "NONE",
        }

    cur_u = ranking.get("CURRENT_COMMON_TOP3_UPLIFT")
    oof_u = ranking.get("C3_OOF_COMMON_TOP3_UPLIFT")
    edge = bool(oof_u is not None and cur_u is not None and float(oof_u) > float(cur_u))

    def _pnl(p: dict[str, Any] | None) -> Optional[float]:
        if not p:
            return None
        v = p.get("PnL")
        if v is None:
            v = p.get("pnl")
        return _f(v)

    def _pf(p: dict[str, Any] | None) -> Optional[float]:
        if not p:
            return None
        try:
            return float(p.get("PF") or 0)
        except (TypeError, ValueError):
            return None

    a2c_pnl = _pnl(a2_common)
    a2_pnl = _pnl(a2_pack)
    oof_pnl = _pnl(c3_oof_common)
    fin_pnl = _pnl(c3_final_common)
    a2c_pf = _pf(a2_common)
    oof_fail = bool(oof_pnl is None or oof_pnl <= 0 or (oof_pnl is not None and float(_pf(c3_oof_common) or 0) < 1))
    fin_fail = bool(fin_pnl is None or fin_pnl <= 0 or (fin_pnl is not None and float(_pf(c3_final_common) or 0) < 1))
    exact_fail = bool(oof_fail and fin_fail)
    fill_worse = bool(adverse.get("PASSIVE_FILL_ADVERSE_SELECTION_CONFIRMED"))

    a2_collapsed = False
    if a2c_pnl is not None and a2_pnl is not None:
        a2_collapsed = bool(a2c_pnl <= 0 or (a2c_pf is not None and a2c_pf < 1.0) or a2c_pnl < 0.3 * float(a2_pnl))
    coverage_ratio = (common_n / exec_n) if exec_n else None

    case_a = bool(edge and exact_fail and fill_worse)
    case_b = bool(a2_collapsed and exact_fail)
    if case_a and case_b:
        verdict = "C3_EXECUTION_COUPLING_NOT_YET_RESOLVED"
        primary = "SCORABLE_POPULATION_CHANGE_AND_RANKING_EXECUTION_MISMATCH"
        nxt = "NONE"
    elif case_b:
        verdict = "C3_MODEL_POPULATION_COVERAGE_PROBLEM"
        primary = "SCORABLE_POPULATION_CHANGE"
        nxt = "MODEL_POPULATION"
    elif case_a:
        verdict = "C3_EXECUTION_AWARE_OBJECTIVE_READY"
        primary = "RANKING_EXECUTION_MISMATCH"
        nxt = "EXECUTION_AWARE_OBJECTIVE"
    elif edge and exact_fail:
        verdict = "C3_EXECUTION_COUPLING_NOT_YET_RESOLVED"
        primary = "RANKING_EXECUTION_MISMATCH_INCOMPLETE"
        nxt = "NONE"
    else:
        verdict = "C3_EXECUTION_COUPLING_NOT_YET_RESOLVED"
        primary = "UNRESOLVED"
        nxt = "NONE"

    return {
        "VERDICT": verdict,
        "PRIMARY_CAUSE_AFTER_RECONCILIATION": primary,
        "NEXT_RESEARCH": nxt,
        "common_ranking_edge": edge,
        "exact_fail": exact_fail,
        "a2_common_collapsed": a2_collapsed,
        "case_a_ranking_edge_exact_fail_fill_worse": case_a,
        "case_b_a2_common_collapsed": case_b,
        "coverage_ratio": coverage_ratio,
        "PASSIVE_FILL_ADVERSE_SELECTION_CONFIRMED": fill_worse,
    }
