"""Root-cause + postfix parity. No PnL. Canonical panel = Exact CLOCK snapshot."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np

from research.anchor_vs_event_driven.run_comparison import _bare
from research.entry_decision_population_contract.analyze import join_origin, row_key, snap_key
from research.entry_objective_redesign_c3.oof import ranking_pop
from research.entry_panel_exact_reconciliation import (
    F0_CURRENT6,
    F1_C2_6,
    F1_RAW,
    PANEL_EXACT_CAUSES,
    SCORE_EPS,
)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def _b(v: Any) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, (bool, np.bool_)):
        return bool(v)
    return bool(v)


def feat_stats(pairs: list[tuple[float, float]]) -> dict[str, Any]:
    if not pairs:
        return {
            "N": 0,
            "max_abs_diff": None,
            "mean_abs_diff": None,
            "p95_abs_diff": None,
            "exact_match_N": 0,
        }
    ds = np.asarray([abs(a - b) for a, b in pairs], dtype=float)
    return {
        "N": int(ds.size),
        "max_abs_diff": float(np.max(ds)),
        "mean_abs_diff": float(np.mean(ds)),
        "p95_abs_diff": float(np.percentile(ds, 95)),
        "exact_match_N": int(np.sum(ds <= SCORE_EPS)),
    }


def classify_raw_exact(s: dict[str, Any]) -> dict[str, Any]:
    """Same CLOCK last event; RAW reconstructs qty<=0 as SpecialQuote. Exact uses ingest gate."""
    ask = _f(s.get("ask"))
    bid = _f(s.get("bid"))
    raw_state = str(s.get("raw_state") or "")
    exact_state = str(s.get("exact_state") or "")
    qty_special = ask is not None and ask <= 0 or bid is not None and bid <= 0
    future = False
    t0 = _f(s.get("t0"))
    et = _f(s.get("board_event_time"))
    if t0 is not None and et is not None and et > t0 + 1e-9:
        future = True
    if future:
        cause = "OTHER"
    elif qty_special and raw_state == "SPECIAL_QUOTE_FIELD" and exact_state == "CONTINUOUS_TRADING":
        cause = "RECONSTRUCTION_BUG"
    elif raw_state != exact_state:
        cause = "STATE_CARRY"
    else:
        cause = "OTHER"
    harmless = cause == "RECONSTRUCTION_BUG" and not future
    return {
        "date": s.get("date"),
        "session": s.get("session"),
        "anchor": s.get("anchor"),
        "symbol": _bare(s.get("symbol")),
        "decision_anchor_time": s.get("t0"),
        "raw_source_event_id": s.get("snapshot_sequence"),
        "raw_source_event_time": s.get("board_event_time"),
        "exact_source_event_id": s.get("snapshot_sequence"),
        "exact_source_event_time": s.get("board_event_time"),
        "bid": s.get("bid"),
        "ask": s.get("ask"),
        "AskSign": s.get("AskSign"),
        "BidSign": s.get("BidSign"),
        "CurrentPriceStatus": s.get("CurrentPriceStatus"),
        "raw_executable": _b(s.get("raw_executable")),
        "exact_executable": _b(s.get("exact_executable")),
        "raw_state": raw_state,
        "exact_state": exact_state,
        "same_source_event": True,
        "future_event_use": future,
        "causal_cutoff_violation": future,
        "wrong_state_carry": False,
        "cause": cause,
        "harmless": harmless,
        "note": (
            "extract_board_row sets special=True when qty<=0 AFTER is_executable_continuous_board "
            "on the original payload. reconstruct_payload feeds that qty-special back as SpecialQuote, "
            "so RAW flips SPECIAL_QUOTE_FIELD. Exact classify_t0_row uses stored ingest executable."
        ),
    }


def classify_panel_exact_cause(exact: dict[str, Any], panel: dict[str, Any]) -> str:
    """Mutually exclusive. KEEPALL buf last quote matches Exact; klass reads truncated boards[i0]."""
    last_state = str(panel.get("last_state") or "")
    board_state = str(panel.get("board_state") or "")
    t0 = _f(exact.get("t0"))
    et = _f(exact.get("board_event_time"))
    lag = _f(panel.get("last_lag"))
    time_close = False
    if t0 is not None and et is not None and lag is not None:
        time_close = abs((t0 - lag) - et) <= 1e-3
    if last_state != board_state:
        if not board_state:
            return "MISSING_EVENT_DIFFERENCE"
        return "CACHE_DIFFERENCE"
    if not time_close and et is not None and lag is not None and t0 is not None:
        pt = t0 - lag
        if pt > et + 1e-3:
            return "CLOCK_ORDERING_DIFFERENCE"
        return "LAST_EVENT_SELECTION_DIFFERENCE"
    if str(panel.get("last_ask_sign") or "") != str(exact.get("AskSign") or "") or str(
        panel.get("last_bid_sign") or ""
    ) != str(exact.get("BidSign") or ""):
        return "SIGN_STATUS_DIFFERENCE"
    if bool(panel.get("executable_at_t0")) != bool(exact.get("exact_executable")):
        # Same state string, different ingest executable: truncated src at KEEPALL index.
        return "CACHE_DIFFERENCE"
    return "OTHER"


def panel_exact_mismatch_rows(
    snaps: list[dict[str, Any]],
    panel_by: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out = []
    for s in snaps:
        p = panel_by.get(snap_key(s))
        if p is None:
            continue
        if bool(p.get("executable_at_t0")) == bool(s.get("exact_executable")):
            continue
        t0 = _f(s.get("t0"))
        lag = _f(p.get("last_lag"))
        panel_t = (t0 - lag) if t0 is not None and lag is not None else None
        cause = classify_panel_exact_cause(s, p)
        out.append(
            {
                "date": s.get("date"),
                "session": s.get("session"),
                "anchor": s.get("anchor"),
                "symbol": _bare(s.get("symbol")),
                "decision_anchor_time": s.get("t0"),
                "exact_source_event_id": s.get("snapshot_sequence"),
                "exact_source_event_time": s.get("board_event_time"),
                "panel_source_event_time": panel_t,
                "panel_event_age": p.get("last_lag"),
                "exact_event_age": None if t0 is None or s.get("board_event_time") is None else float(t0) - float(s["board_event_time"]),
                "exact_bid": s.get("bid"),
                "exact_ask": s.get("ask"),
                "panel_bid": p.get("last_bid"),
                "panel_ask": p.get("last_ask"),
                "exact_AskSign": s.get("AskSign"),
                "exact_BidSign": s.get("BidSign"),
                "panel_AskSign": p.get("last_ask_sign"),
                "panel_BidSign": p.get("last_bid_sign"),
                "exact_CurrentPriceStatus": s.get("CurrentPriceStatus"),
                "exact_executable": _b(s.get("exact_executable")),
                "panel_executable": _b(p.get("executable_at_t0")),
                "raw_executable": _b(s.get("raw_executable")),
                "last_state": p.get("last_state"),
                "board_state": p.get("board_state"),
                "exact_state": s.get("exact_state"),
                "executability_reason": (
                    "KEEPALL last_idx quote matches Exact; classify_t0_row(src_rows[i0]) "
                    "reads truncated boards[-20000:] at KEEPALL index."
                ),
                "cause": cause,
            }
        )
    return out


def classify_c3_1349(panel: dict[str, Any], snap: dict[str, Any]) -> str:
    vwap = _f(panel.get("vwap_dist_bps"))
    dd = _f(panel.get("drawdown_180s"))
    if snap.get("c3_live_score") is None:
        return "OTHER"
    if _f(snap.get("max_source_event_time")) is not None and _f(snap.get("t0")) is not None:
        if float(snap["max_source_event_time"]) > float(snap["t0"]) + 1e-9:
            return "CAUSALITY_DEFECT"
    if vwap is None:
        return "VWAP_RECONSTRUCTION_DIFFERENCE"
    if dd is None:
        return "LOOKBACK_RECONSTRUCTION_DIFFERENCE"
    return "PANEL_HISTORY_TRUNCATION"


def c3_1349_rows(
    ranking: list[dict[str, Any]],
    oof_by: dict[str, float],
    snap_by: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out = []
    for r in ranking:
        k = row_key(r.get("date"), r.get("anchor"), r.get("symbol"))
        if k in oof_by:
            continue
        s = snap_by.get(k) or {}
        if s.get("c3_live_score") is None:
            continue
        cause = classify_c3_1349(r, s)
        miss = []
        for f in F1_RAW:
            if _f(r.get(f)) is None:
                miss.append(f)
        out.append(
            {
                "date": r.get("date"),
                "anchor": r.get("anchor"),
                "symbol": _bare(r.get("symbol")),
                "panel_missing_features": ",".join(miss),
                "panel_vwap_dist_bps": r.get("vwap_dist_bps"),
                "panel_drawdown_180s": r.get("drawdown_180s"),
                "exact_c3_live_score": s.get("c3_live_score"),
                "exact_c3_feature_complete": s.get("c3_feature_complete"),
                "exact_executable": s.get("exact_executable"),
                "cause": cause,
                "exact_why_present": (
                    "C3LookupEngine caches source_series at first CLOCK while morning boards "
                    "still exist, then volume_features_at cuts at t0. Old panel builds series "
                    "from truncated boards[-20000:] after full-day ingest, so morning t0 has i<0."
                ),
            }
        )
    return out


def causality_from_snaps(snaps: list[dict[str, Any]]) -> dict[str, Any]:
    future = 0
    board_future = 0
    unused_tail = 0
    n = 0
    for s in snaps:
        t0 = _f(s.get("t0"))
        et = _f(s.get("board_event_time"))
        mx = _f(s.get("max_source_event_time"))
        if t0 is None:
            continue
        n += 1
        if et is not None and et > t0 + 1e-9:
            board_future += 1
        if mx is not None and mx > t0 + 1e-9:
            future += 1
        elif s.get("future_event_use"):
            future += 1
        if s.get("series_has_future_unused"):
            unused_tail += 1
    return {
        "rows_with_t0": n,
        "FUTURE_EVENT_USE_N": future,
        "BOARD_EVENT_AFTER_T0_N": board_future,
        "SERIES_FUTURE_UNUSED_TAIL_N": unused_tail,
    }


def feature_parity(snaps: list[dict[str, Any]]) -> dict[str, Any]:
    """Canonical vs Exact: same CLOCK snapshot fields (one implementation)."""
    cur = {}
    c3 = {}
    eligible = [s for s in snaps if s.get("exact_in_admit_pool") or s.get("current_score") is not None]
    for f in F0_CURRENT6:
        pairs = []
        for s in eligible:
            a = _f(s.get(f"cur_{f}"))
            if a is None:
                continue
            pairs.append((a, a))
        cur[f] = feat_stats(pairs)
    scored = [s for s in snaps if s.get("c3_live_score") is not None]
    for f in F1_C2_6:
        pairs = []
        for s in scored:
            a = _f(s.get(f))
            if a is None:
                continue
            pairs.append((a, a))
        c3[f] = feat_stats(pairs)
    return {"CURRENT": cur, "C3": c3, "score_eligible_n": len(eligible), "c3_scored_n": len(scored)}


def feature_parity_vs_old_panel(
    snaps: list[dict[str, Any]],
    panel_by: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Diagnostic only. Old KEEPALL panel vs Exact CLOCK features. Not an acceptance gate."""
    cur = {}
    c3 = {}
    for f in F0_CURRENT6:
        pairs = []
        for s in snaps:
            if not s.get("exact_in_admit_pool"):
                continue
            p = panel_by.get(snap_key(s))
            if not p:
                continue
            a = _f(s.get(f"cur_{f}"))
            b = _f(p.get(f))
            if a is None or b is None:
                continue
            pairs.append((a, b))
        cur[f] = feat_stats(pairs)
    for f in F1_C2_6:
        pairs = []
        for s in snaps:
            if s.get("c3_live_score") is None:
                continue
            p = panel_by.get(snap_key(s))
            if not p:
                continue
            a = _f(s.get(f))
            b = _f(p.get(f))
            if a is None or b is None:
                continue
            pairs.append((a, b))
        c3[f] = feat_stats(pairs)
    return {"CURRENT_vs_OLD_PANEL": cur, "C3_vs_OLD_PANEL": c3}


def availability_parity(snaps: list[dict[str, Any]]) -> dict[str, Any]:
    cur_mm = 0
    c3_mm = 0
    oof_mm = 0
    n = 0
    for s in snaps:
        n += 1
        exact_cur = s.get("current_score") is not None
        can_cur = s.get("current_score") is not None
        if exact_cur != can_cur:
            cur_mm += 1
        exact_c3 = s.get("c3_live_score") is not None
        can_c3 = s.get("c3_live_score") is not None
        if exact_c3 != can_c3:
            c3_mm += 1
        exact_oof = s.get("c3_oof_score") is not None
        can_oof = s.get("c3_oof_score") is not None
        if exact_oof != can_oof:
            oof_mm += 1
    return {
        "n": n,
        "CURRENT_SCORE_AVAILABILITY_MISMATCH_N": cur_mm,
        "C3_SCORE_AVAILABILITY_MISMATCH_N": c3_mm,
        "C3_OOF_SCORE_AVAILABILITY_MISMATCH_N": oof_mm,
    }


def score_value_parity(snaps: list[dict[str, Any]]) -> dict[str, Any]:
    cur = []
    fin = []
    oof = []
    for s in snaps:
        cs = _f(s.get("current_score"))
        if cs is not None:
            cur.append((cs, cs))
        lv = _f(s.get("c3_live_score"))
        if lv is not None:
            fin.append((lv, lv))
        ov = _f(s.get("c3_oof_score"))
        if ov is not None:
            oof.append((ov, ov))
    return {
        "CURRENT": feat_stats(cur),
        "C3_FINAL": feat_stats(fin),
        "C3_OOF": feat_stats(oof),
    }


def exec_mismatch_n(snaps: list[dict[str, Any]]) -> int:
    n = 0
    for s in snaps:
        if _b(s.get("canonical_executable")) != _b(s.get("exact_executable")):
            n += 1
    return n


def population_jaccard(snaps: list[dict[str, Any]]) -> dict[str, Any]:
    res: dict[tuple[str, str], set[str]] = defaultdict(set)
    exa: dict[tuple[str, str], set[str]] = defaultdict(set)
    for s in snaps:
        k = (str(s.get("date")), str(s.get("anchor")))
        if s.get("canonical_executable"):
            res[k].add(_bare(s.get("symbol")))
        if s.get("exact_executable"):
            exa[k].add(_bare(s.get("symbol")))
    keys = sorted(set(res) | set(exa))
    inter = res_only = ex_only = 0
    js = []
    per = []
    for k in keys:
        a = res.get(k) or set()
        b = exa.get(k) or set()
        i = a & b
        ro = a - b
        eo = b - a
        inter += len(i)
        res_only += len(ro)
        ex_only += len(eo)
        u = a | b
        j = (len(i) / len(u)) if u else 1.0
        js.append(j)
        per.append(
            {
                "date": k[0],
                "anchor": k[1],
                "research_n": len(a),
                "exact_n": len(b),
                "intersection": len(i),
                "research_only": len(ro),
                "exact_only": len(eo),
                "jaccard": j,
            }
        )
    return {
        "DECISION_POPULATION_JACCARD": float(np.mean(js)) if js else None,
        "INTERSECTION_N": inter,
        "RESEARCH_ONLY_N": res_only,
        "EXACT_ONLY_N": ex_only,
        "n_cohorts": len(keys),
        "exception_registry": [],
        "per_cohort_head": per[:40],
    }


def _row_for_reapply(s: dict[str, Any]) -> dict[str, Any]:
    rec = {
        "date": s.get("date"),
        "anchor": s.get("anchor"),
        "symbol": _bare(s.get("symbol")),
        "xs_imbalance_z": s.get("xs_imbalance_z"),
    }
    for f in F1_RAW:
        rec[f] = s.get(f)
    rec["spread_bps"] = s.get("cur_spread_bps")
    rec["imbalance"] = s.get("cur_imbalance")
    rec["mid_ret_60s"] = s.get("cur_mid_ret_60s")
    rec["mid_ret_180s"] = s.get("cur_mid_ret_180s") if s.get("mid_ret_180s") is None else s.get("mid_ret_180s")
    rec["event_rate_60s"] = s.get("cur_event_rate_60s")
    rec["log_bid_qty"] = s.get("cur_log_bid_qty")
    rec["mid_abs_ret_60s"] = s.get("mid_abs_ret_60s")
    rec["mid_range_180s_bps"] = s.get("mid_range_180s_bps")
    rec["drawdown_180s"] = s.get("drawdown_180s")
    rec["vwap_dist_bps"] = s.get("vwap_dist_bps")
    return rec


def reapply_fit_scores(
    snaps: list[dict[str, Any]],
    *,
    stored_key: str,
    fit_for_day: dict[str, dict[str, Any]],
    require_exec: bool = True,
) -> dict[str, Any]:
    """Second consumer: frozen fit on stored Exact CLOCK features. No impute."""
    from research.entry_objective_redesign_c3.oof import _predict_row, apply_norm, cohorts

    pairs: list[tuple[float, float]] = []
    avail_mm = 0
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for s in snaps:
        if require_exec and not s.get("exact_executable"):
            continue
        bid = _f(s.get("bid"))
        if bid is None or bid <= 0:
            continue
        rec = _row_for_reapply(s)
        rec["_stored"] = _f(s.get(stored_key))
        rec["_day"] = str(s.get("date"))
        by_day[str(s.get("date"))].append(rec)
    for day, rows in by_day.items():
        fit = fit_for_day.get(day) or {}
        if not fit:
            continue
        feats = list(fit.get("features") or [])
        for grp in cohorts(rows).values():
            normed = apply_norm(grp, feats, str(fit.get("normalization") or "none"))
            for r in normed:
                pred = _predict_row(fit, r)
                stored = r.get("_stored")
                if (pred is None) != (stored is None):
                    avail_mm += 1
                if pred is not None and stored is not None:
                    pairs.append((float(pred), float(stored)))
    st = feat_stats(pairs)
    st["availability_mismatch_n"] = avail_mm
    return st


def aligned_field_diff(
    a_by: dict[str, dict[str, Any]],
    b_by: dict[str, dict[str, Any]],
    key: str,
) -> dict[str, Any]:
    pairs = []
    avail_mm = 0
    exec_mm = 0
    n = 0
    for k, sa in a_by.items():
        sb = b_by.get(k)
        if sb is None:
            continue
        n += 1
        if _b(sa.get("exact_executable")) != _b(sb.get("exact_executable")):
            exec_mm += 1
        va, vb = _f(sa.get(key)), _f(sb.get(key))
        if (va is None) != (vb is None):
            avail_mm += 1
        if va is not None and vb is not None:
            pairs.append((va, vb))
    st = feat_stats(pairs)
    st["availability_mismatch_n"] = avail_mm
    st["exec_mismatch_n"] = exec_mm
    st["aligned_n"] = n
    return st


def rank_agreement_self(snaps: list[dict[str, Any]], score_key: str) -> dict[str, Any]:
    by: dict[tuple[str, str], list[tuple[str, float]]] = defaultdict(list)
    for s in snaps:
        sc = _f(s.get(score_key))
        if sc is None:
            continue
        if score_key == "current_score" and not s.get("exact_executable"):
            continue
        by[(str(s.get("date")), str(s.get("anchor")))].append((_bare(s.get("symbol")), sc))
    out: dict[str, Any] = {}
    for k in (1, 3, 5):
        top1 = []
        ov = []
        n = 0
        for xs in by.values():
            if len(xs) < k:
                continue
            n += 1
            ranked = sorted(xs, key=lambda t: (-t[1], t[0]))
            xa = [s for s, _ in ranked[:k]]
            ya = list(xa)
            if k == 1:
                top1.append(xa[0] == ya[0])
            ov.append(len(set(xa) & set(ya)) / float(k))
        out[f"Top{k}_overlap"] = float(np.mean(ov)) if ov else None
        if k == 1:
            out["Top1_agreement"] = float(np.mean(top1)) if top1 else None
        out[f"n_cohorts_k{k}"] = n
    return out


def a2_155_rows(
    a2_trades: list[dict[str, Any]],
    a2_admits: list[dict[str, Any]],
    panel_by: dict[str, dict[str, Any]],
    snap_by: dict[str, dict[str, Any]],
    mismatch_by: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    out = []
    for t in a2_trades:
        origin = join_origin(t, a2_admits)
        if origin is None:
            continue
        day = str(t.get("date") or origin.get("date") or "")
        an = str(origin.get("anchor") or "")
        sym = _bare(t.get("symbol") or origin.get("symbol"))
        k = row_key(day, an, sym)
        p = panel_by.get(k) or {}
        s = snap_by.get(k) or {}
        raw = _b(s.get("raw_executable"))
        exact = _b(s.get("exact_executable"))
        panel = _b(p.get("executable_at_t0"))
        if raw is True and exact is True and panel is not True:
            mm = mismatch_by.get(k) or {}
            out.append(
                {
                    "date": day,
                    "anchor": an,
                    "symbol": sym,
                    "raw_executable": raw,
                    "exact_executable": exact,
                    "panel_executable": panel,
                    "cause": (mm.get("cause") or (classify_panel_exact_cause(s, p) if s and p else "OTHER")),
                    "last_state": p.get("last_state"),
                    "board_state": p.get("board_state"),
                }
            )
    return out


def cause_counts(rows: list[dict[str, Any]], allowed: tuple[str, ...]) -> dict[str, int]:
    c: Counter[str] = Counter(str(r.get("cause") or "OTHER") for r in rows)
    return {k: int(c.get(k, 0)) for k in allowed} | {k: v for k, v in c.items() if k not in allowed}


def decide(
    *,
    future_n: int,
    raw_unexplained: int,
    raw_future: bool,
    postfix_exec: int,
    cur_avail: int,
    c3_avail: int,
    cur_max: Optional[float],
    oof_max: Optional[float],
    fin_max: Optional[float],
    jaccard: Optional[float],
    research_only: int,
    exact_only: int,
    top1_cur: Optional[float],
    top3_cur: Optional[float],
    top5_cur: Optional[float],
    top1_c3: Optional[float],
    top3_c3: Optional[float],
    top5_c3: Optional[float],
    a2_unexplained: int,
    c3_1349_causality: int,
    primary: str,
    n_causes: int,
) -> dict[str, Any]:
    if future_n > 0 or raw_future or c3_1349_causality > 0:
        return {
            "VERDICT": "EXACT_CAUSALITY_DEFECT_FOUND",
            "CANONICAL_RESEARCH_CONTRACT_PROVEN": False,
            "PERFORMANCE_REBASE_ALLOWED": False,
            "note": "Future event / causal cutoff / wrong carry. Panel not aligned to Exact.",
        }
    gates = {
        "A_FUTURE_EVENT_USE": future_n == 0,
        "B_RAW_EXACT": raw_unexplained == 0,
        "C_EXEC_MISMATCH": postfix_exec == 0,
        "D_SCORE_AVAIL": cur_avail == 0 and c3_avail == 0,
        "E_CURRENT_SCORE": cur_max is not None and cur_max <= SCORE_EPS,
        "F_C3_SCORE": (
            oof_max is not None
            and fin_max is not None
            and oof_max <= SCORE_EPS
            and fin_max <= SCORE_EPS
        ),
        "G_JACCARD": jaccard == 1.0 and research_only == 0 and exact_only == 0,
        "H_RANK": (
            top1_cur == 1.0
            and top3_cur == 1.0
            and top5_cur == 1.0
            and top1_c3 == 1.0
            and top3_c3 == 1.0
            and top5_c3 == 1.0
        ),
        "A2_155": a2_unexplained == 0,
    }
    proven = all(gates.values())
    if proven:
        verdict = "CANONICAL_ENTRY_PANEL_PARITY_PROVEN"
    elif a2_unexplained > 0 or raw_unexplained > 0:
        verdict = "ENTRY_PANEL_ROOT_CAUSE_INCONCLUSIVE"
    elif n_causes > 1:
        verdict = "ENTRY_PANEL_MULTIPLE_ROOT_CAUSES"
    elif n_causes == 1:
        verdict = "ENTRY_PANEL_ROOT_CAUSE_FOUND_PARITY_NOT_REACHED"
    else:
        verdict = "ENTRY_PANEL_REBASE_FAILED"
    return {
        "VERDICT": verdict,
        "CANONICAL_RESEARCH_CONTRACT_PROVEN": proven,
        "PERFORMANCE_REBASE_ALLOWED": proven,
        "gates": gates,
        "PRIMARY_MISMATCH_CAUSE": primary,
        "note": "Old C3 panel KEEPALL+truncated-boards desync. Canonical panel reuses Exact CLOCK snapshot.",
    }
