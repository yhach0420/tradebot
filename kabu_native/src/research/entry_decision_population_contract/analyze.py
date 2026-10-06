"""Contract verification: origin pending, three-way executable, score/population parity."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

import numpy as np

from research.anchor_timing_robustness.grid import hm_epoch
from research.anchor_vs_event_driven.run_comparison import _bare
from research.c3_execution_coupling_audit.analyze import admit_clock
from research.entry_decision_population_contract import (
    EXPECTED_A2_CLOSED,
    F1_RAW,
    SCORE_EPS,
    WAIT_SEC,
)
from research.entry_objective_redesign_c3.oof import ranking_pop


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
    if isinstance(v, str):
        if v.lower() in {"true", "1"}:
            return True
        if v.lower() in {"false", "0", ""}:
            return False
    try:
        return bool(v)
    except Exception:
        return None


def row_key(date: Any, anchor: Any, symbol: Any) -> str:
    return f"{date}|{anchor}|{_bare(symbol)}"


def snap_key(r: dict[str, Any]) -> str:
    return row_key(r.get("date"), r.get("anchor"), r.get("symbol"))


def panel_key(r: dict[str, Any]) -> str:
    return row_key(r.get("date"), r.get("anchor"), r.get("symbol"))


def t0_of(day: str, anchor: str) -> Optional[float]:
    try:
        h, m = str(anchor).split(":")
        return float(hm_epoch(day, int(h), int(m)))
    except Exception:
        return None


def join_origin(trade: dict[str, Any], admits: list[dict[str, Any]], *, wait: float = WAIT_SEC) -> Optional[dict[str, Any]]:
    """PENDING origin. WAIT window on pending.anchor t0. Not nearest-anchor, not fill_time clock."""
    day = str(trade.get("date") or "")
    sym = _bare(trade.get("symbol"))
    ft = _f(trade.get("fill_time"))
    if ft is None or not day:
        return None
    cands: list[dict[str, Any]] = []
    for a in admits:
        if str(a.get("date") or day) != day:
            continue
        if _bare(a.get("symbol")) != sym:
            continue
        an = str(a.get("anchor") or "")
        ts = t0_of(day, an)
        if ts is None:
            continue
        if ts - 1e-6 <= ft <= ts + float(wait) + 0.5:
            cands.append(a)
    if not cands:
        return None
    cands.sort(key=lambda a: t0_of(day, str(a.get("anchor"))) or 0.0)
    return cands[-1]


def abs_diffs(pairs: list[tuple[float, float]]) -> dict[str, Any]:
    if not pairs:
        return {"n": 0, "max_abs_diff": None, "mean_abs_diff": None, "n_match_eps": 0}
    ds = [abs(a - b) for a, b in pairs]
    return {
        "n": len(ds),
        "max_abs_diff": float(max(ds)),
        "mean_abs_diff": float(sum(ds) / len(ds)),
        "n_match_eps": int(sum(1 for d in ds if d <= SCORE_EPS)),
    }


def three_way(raw: Optional[bool], panel: Optional[bool], exact: Optional[bool]) -> dict[str, Any]:
    def mm(a: Optional[bool], b: Optional[bool]) -> Optional[bool]:
        if a is None or b is None:
            return None
        return bool(a) == bool(b)

    return {
        "raw": raw,
        "panel": panel,
        "exact": exact,
        "RAW_vs_PANEL_MATCH": mm(raw, panel),
        "RAW_vs_EXACT_MATCH": mm(raw, exact),
        "PANEL_vs_EXACT_MATCH": mm(panel, exact),
    }


def classify_155_cause(
    *,
    origin: Optional[dict[str, Any]],
    origin_panel_exec: Optional[bool],
    origin_exact_exec: Optional[bool],
    origin_raw_exec: Optional[bool],
    fill_clock: str,
    origin_anchor: str,
    packed_anchor: str,
    fill_in_ranking: bool,
    origin_in_ranking: bool,
) -> str:
    if origin is None:
        return "PROVENANCE_MISSING"
    if origin_in_ranking and not fill_in_ranking and fill_clock != origin_anchor:
        return "FILL_TIME_JOIN_ARTIFACT"
    if origin_exact_exec is False:
        return "EXACT_ELIGIBILITY_BYPASS"
    if origin_exact_exec is True and origin_raw_exec is True and origin_panel_exec is not True:
        return "PANEL_RECONSTRUCTION_MISMATCH"
    if origin_exact_exec is True and origin_raw_exec is False:
        return "EXACT_ELIGIBILITY_BYPASS"
    if origin_panel_exec is True and origin_exact_exec is True and origin_raw_exec is True and not fill_in_ranking:
        return "FILL_TIME_JOIN_ARTIFACT"
    if packed_anchor and origin_anchor and packed_anchor != origin_anchor:
        return "CLOCK_REMAP_ARTIFACT"
    if origin_exact_exec is True and origin_raw_exec is True and origin_panel_exec is True:
        return "PENDING_CREATED_WHEN_EXECUTABLE"
    if origin_exact_exec is not None and origin_panel_exec is not None and bool(origin_exact_exec) != bool(origin_panel_exec):
        return "PANEL_DECISION_TIME_MISMATCH"
    return "OTHER"


def build_indexes(
    panel: list[dict[str, Any]],
    snaps: list[dict[str, Any]],
    oof_by: dict[str, float],
    final_by: dict[str, float],
) -> dict[str, Any]:
    panel_by = {panel_key(r): r for r in panel}
    snap_by = {snap_key(r): r for r in snaps}
    ranking = ranking_pop(panel)
    rank_keys = {panel_key(r) for r in ranking}
    return {
        "panel_by": panel_by,
        "snap_by": snap_by,
        "ranking": ranking,
        "rank_keys": rank_keys,
        "oof_by": oof_by,
        "final_by": final_by,
    }


def lane_origins(
    trades: list[dict[str, Any]],
    admits: list[dict[str, Any]],
    idx: dict[str, Any],
    *,
    lane: str,
) -> dict[str, Any]:
    panel_by = idx["panel_by"]
    snap_by = idx["snap_by"]
    rank_keys = idx["rank_keys"]
    oof_by = idx["oof_by"]
    final_by = idx["final_by"]
    found = 0
    missing = 0
    rows = []
    exec_then_non = 0
    for t in trades:
        day = str(t.get("date") or "")
        origin = join_origin(t, admits)
        ft = _f(t.get("fill_time"))
        fill_clock = admit_clock(day, ft) if ft is not None else ""
        packed = str(t.get("anchor_time") or t.get("anchor") or "")
        if origin is None:
            missing += 1
            rows.append(
                {
                    "lane": lane,
                    "trade_id": t.get("trade_id"),
                    "date": day,
                    "symbol": _bare(t.get("symbol")),
                    "origin_found": False,
                    "cause_155": "PROVENANCE_MISSING" if lane == "A2" else None,
                }
            )
            continue
        found += 1
        an = str(origin.get("anchor") or "")
        k = row_key(day, an, t.get("symbol"))
        p = panel_by.get(k) or {}
        s = snap_by.get(k) or {}
        kf = row_key(day, fill_clock, t.get("symbol"))
        pf = panel_by.get(kf) or {}
        sf = snap_by.get(kf) or {}
        tw = three_way(_b(s.get("raw_executable")), _b(p.get("executable_at_t0")), _b(s.get("exact_executable")))
        used = _f(origin.get("score"))
        oof = oof_by.get(k)
        fin = final_by.get(k)
        panel_cur = _f(p.get("current_score"))
        snap_cur = _f(s.get("current_score"))
        origin_exec = _b(s.get("exact_executable"))
        fill_panel_exec = _b(pf.get("executable_at_t0"))
        fill_exact = _b(sf.get("exact_executable"))
        if origin_exec is True and str(fill_clock) != str(an) and (
            fill_panel_exec is False or fill_exact is False
        ):
            exec_then_non += 1
        cause = None
        if lane == "A2":
            cause = classify_155_cause(
                origin=origin,
                origin_panel_exec=_b(p.get("executable_at_t0")),
                origin_exact_exec=origin_exec,
                origin_raw_exec=_b(s.get("raw_executable")),
                fill_clock=fill_clock,
                origin_anchor=an,
                packed_anchor=packed,
                fill_in_ranking=kf in rank_keys,
                origin_in_ranking=k in rank_keys,
            )
        rows.append(
            {
                "lane": lane,
                "trade_id": t.get("trade_id"),
                "pending_id": origin.get("pending_id") or f"{day}|{an}|{_bare(t.get('symbol'))}|PENDING",
                "decision_id": origin.get("decision_id") or k,
                "date": day,
                "symbol": _bare(t.get("symbol")),
                "origin_found": True,
                "decision_anchor_time": an,
                "packed_anchor_time": packed,
                "fill_time": ft,
                "fill_clock": fill_clock,
                "exit_time": t.get("exit_time"),
                "used_score": used,
                "panel_current_score": panel_cur,
                "exact_current_score": snap_cur,
                "panel_c3_oof": oof,
                "panel_c3_final": fin,
                "c3_live_score": _f(s.get("c3_live_score")),
                "c3_feature_complete": s.get("c3_feature_complete"),
                **tw,
                "origin_in_ranking_pop": k in rank_keys,
                "fill_in_ranking_pop": kf in rank_keys,
                "cause_155": cause,
                "state_changed_before_fill": bool(origin_exec is True and fill_panel_exec is False),
            }
        )
    exact_true = sum(1 for r in rows if r.get("exact") is True)
    exact_false = sum(1 for r in rows if r.get("origin_found") and r.get("exact") is False)
    raw_true = sum(1 for r in rows if r.get("raw") is True)
    raw_false = sum(1 for r in rows if r.get("origin_found") and r.get("raw") is False)
    panel_true = sum(1 for r in rows if r.get("panel") is True)
    panel_false = sum(1 for r in rows if r.get("origin_found") and r.get("panel") is False)
    rp_m = sum(1 for r in rows if r.get("RAW_vs_PANEL_MATCH") is False)
    re_m = sum(1 for r in rows if r.get("RAW_vs_EXACT_MATCH") is False)
    pe_m = sum(1 for r in rows if r.get("PANEL_vs_EXACT_MATCH") is False)
    score_present = sum(1 for r in rows if r.get("used_score") is not None)
    oof_present = sum(1 for r in rows if r.get("panel_c3_oof") is not None)
    fin_present = sum(1 for r in rows if r.get("panel_c3_final") is not None)
    used_vs_oof = abs_diffs(
        [(float(r["used_score"]), float(r["panel_c3_oof"])) for r in rows if r.get("used_score") is not None and r.get("panel_c3_oof") is not None]
    )
    used_vs_fin = abs_diffs(
        [(float(r["used_score"]), float(r["panel_c3_final"])) for r in rows if r.get("used_score") is not None and r.get("panel_c3_final") is not None]
    )
    used_vs_live = abs_diffs(
        [(float(r["used_score"]), float(r["c3_live_score"])) for r in rows if r.get("used_score") is not None and r.get("c3_live_score") is not None]
    )
    cur_pairs = abs_diffs(
        [(float(r["panel_current_score"]), float(r["exact_current_score"])) for r in rows if r.get("panel_current_score") is not None and r.get("exact_current_score") is not None]
    )
    return {
        "lane": lane,
        "CLOSED_N": len(trades),
        "ORIGIN_FOUND_N": found,
        "ORIGIN_MISSING_N": missing,
        "EXACT_EXEC_TRUE_N": exact_true,
        "EXACT_EXEC_FALSE_N": exact_false,
        "RAW_EXEC_TRUE_N": raw_true,
        "RAW_EXEC_FALSE_N": raw_false,
        "PANEL_EXEC_TRUE_N": panel_true,
        "PANEL_EXEC_FALSE_N": panel_false,
        "RAW_vs_PANEL_MISMATCH_N": rp_m,
        "RAW_vs_EXACT_MISMATCH_N": re_m,
        "PANEL_vs_EXACT_MISMATCH_N": pe_m,
        "EXEC_TRUE_THEN_NONEXEC_AT_FILL_N": exec_then_non,
        "USED_SCORE_N": score_present,
        "PANEL_C3_OOF_AT_ORIGIN_N": oof_present,
        "PANEL_C3_FINAL_AT_ORIGIN_N": fin_present,
        "used_vs_oof": used_vs_oof,
        "used_vs_final": used_vs_fin,
        "used_vs_c3_live": used_vs_live,
        "current_score_parity": cur_pairs,
        "rows": rows,
    }


def reclassify_155(a2: dict[str, Any], idx: dict[str, Any]) -> dict[str, Any]:
    """The reconciliation 155: fill-clock join not in ranking_pop."""
    rank_keys = idx["rank_keys"]
    panel_by = idx["panel_by"]
    members = []
    for r in a2.get("rows") or []:
        if not r.get("origin_found"):
            if r.get("cause_155") == "PROVENANCE_MISSING":
                members.append(r)
            continue
        day = r.get("date")
        fill_clock = r.get("fill_clock")
        kf = row_key(day, fill_clock, r.get("symbol"))
        p = panel_by.get(kf)
        if kf in rank_keys:
            continue
        # ranking_pop miss at fill-clock: the 155 set
        members.append(r)
    causes = Counter(str(m.get("cause_155") or "OTHER") for m in members)
    primary = None
    if causes:
        primary = causes.most_common(1)[0][0]
    return {
        "A2_155_N": len(members),
        "A2_155_CAUSE_COUNTS": dict(causes),
        "A2_155_PRIMARY_CAUSE": primary,
        "note": (
            "Set = A2 CLOSED whose fill-clock panel row is not ranking_pop "
            "(same 155 as fill-time join). Cause uses pending origin, not fill_time as decision key."
        ),
    }


def snap_three_way(snaps: list[dict[str, Any]], panel_by: dict[str, dict[str, Any]]) -> dict[str, Any]:
    n = rp = re = pe = 0
    rp_m = re_m = pe_m = 0
    for s in snaps:
        p = panel_by.get(snap_key(s))
        if p is None:
            continue
        n += 1
        tw = three_way(_b(s.get("raw_executable")), _b(p.get("executable_at_t0")), _b(s.get("exact_executable")))
        if tw["RAW_vs_PANEL_MATCH"] is not None:
            rp += 1
            if tw["RAW_vs_PANEL_MATCH"] is False:
                rp_m += 1
        if tw["RAW_vs_EXACT_MATCH"] is not None:
            re += 1
            if tw["RAW_vs_EXACT_MATCH"] is False:
                re_m += 1
        if tw["PANEL_vs_EXACT_MATCH"] is not None:
            pe += 1
            if tw["PANEL_vs_EXACT_MATCH"] is False:
                pe_m += 1
    return {
        "compared_n": n,
        "RAW_vs_PANEL_MATCH_N": rp - rp_m,
        "RAW_vs_PANEL_MISMATCH_N": rp_m,
        "RAW_vs_EXACT_MATCH_N": re - re_m,
        "RAW_vs_EXACT_MISMATCH_N": re_m,
        "PANEL_vs_EXACT_MATCH_N": pe - pe_m,
        "PANEL_vs_EXACT_MISMATCH_N": pe_m,
    }


def current_score_parity(snaps: list[dict[str, Any]], panel_by: dict[str, dict[str, Any]]) -> dict[str, Any]:
    pairs = []
    match = miss = 0
    for s in snaps:
        p = panel_by.get(snap_key(s))
        if not p:
            continue
        a = _f(s.get("current_score"))
        b = _f(p.get("current_score"))
        if a is None or b is None:
            continue
        pairs.append((a, b))
        if abs(a - b) <= SCORE_EPS:
            match += 1
        else:
            miss += 1
    d = abs_diffs(pairs)
    d["CURRENT_SCORE_MATCH_N"] = match
    d["CURRENT_SCORE_MISMATCH_N"] = miss
    return d


def rank_agreement(
    snaps: list[dict[str, Any]],
    panel: list[dict[str, Any]],
    *,
    score_a: str,
    score_b: str,
    ks: tuple[int, ...] = (1, 3, 5),
) -> dict[str, Any]:
    pb = defaultdict(list)
    sb = defaultdict(list)
    for r in panel:
        sc = _f(r.get(score_b))
        if sc is None:
            continue
        pb[(str(r.get("date")), str(r.get("anchor")))].append((_bare(r.get("symbol")), sc))
    for r in snaps:
        sc = _f(r.get(score_a))
        if sc is None:
            continue
        if not r.get("exact_executable"):
            continue
        sb[(str(r.get("date")), str(r.get("anchor")))].append((_bare(r.get("symbol")), sc))
    out: dict[str, Any] = {}
    for k in ks:
        top1_ag = []
        ov = []
        n = 0
        for key, xs in sb.items():
            ys = pb.get(key) or []
            if len(xs) < k or len(ys) < k:
                continue
            n += 1
            xa = [s for s, _ in sorted(xs, key=lambda t: (-t[1], t[0]))[:k]]
            ya = [s for s, _ in sorted(ys, key=lambda t: (-t[1], t[0]))[:k]]
            if k == 1:
                top1_ag.append(xa[0] == ya[0])
            ov.append(len(set(xa) & set(ya)) / float(k))
        out[f"Top{k}_overlap_mean"] = float(np.mean(ov)) if ov else None
        if k == 1:
            out["Top1_agreement"] = float(np.mean(top1_ag)) if top1_ag else None
        out[f"n_cohorts_k{k}"] = n
    return out


def decision_jaccard(
    snaps: list[dict[str, Any]],
    panel: list[dict[str, Any]],
    *,
    research: str,
    exact_pred: str,
) -> dict[str, Any]:
    """research=executable|ranking. exact_pred=exact_executable|exact_in_admit_pool|c3_scored."""
    res: dict[tuple[str, str], set[str]] = defaultdict(set)
    exa: dict[tuple[str, str], set[str]] = defaultdict(set)
    if research == "ranking":
        src = ranking_pop(panel)
        for r in src:
            res[(str(r.get("date")), str(r.get("anchor")))].add(_bare(r.get("symbol")))
    else:
        for r in panel:
            if r.get("executable_at_t0"):
                res[(str(r.get("date")), str(r.get("anchor")))].add(_bare(r.get("symbol")))
    for s in snaps:
        keep = False
        if exact_pred == "c3_scored":
            keep = s.get("c3_live_score") is not None
        elif exact_pred == "exact_executable":
            keep = bool(s.get("exact_executable"))
        else:
            keep = bool(s.get("exact_in_admit_pool"))
        if not keep:
            continue
        exa[(str(s.get("date")), str(s.get("anchor")))].add(_bare(s.get("symbol")))
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
        "mode": research,
        "DECISION_POPULATION_JACCARD": float(np.mean(js)) if js else None,
        "INTERSECTION_N": inter,
        "RESEARCH_ONLY_N": res_only,
        "EXACT_ONLY_N": ex_only,
        "n_cohorts": len(keys),
        "DECISION_POPULATION_MATCH": bool(res_only == 0 and ex_only == 0),
        "per_cohort_head": per[:40],
    }


def c3_missing_handling(
    ranking: list[dict[str, Any]],
    oof_by: dict[str, float],
    snap_by: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    missing = []
    for r in ranking:
        k = panel_key(r)
        if k in oof_by:
            continue
        missing.append(r)
    counts: Counter[str] = Counter()
    by_anchor: Counter[str] = Counter()
    miss_n: Counter[str] = Counter()
    feat: Counter[str] = Counter()
    live_scored = 0
    for r in missing:
        k = panel_key(r)
        s = snap_by.get(k) or {}
        reason = str(s.get("c3_missing_reason") or "NOT_EVALUATED")
        if s.get("c3_live_score") is not None:
            live_scored += 1
            reason = "OTHER"
        elif reason not in {
            "EXCLUDED_BEFORE_RANK",
            "FALLBACK_CURRENT_SCORE",
            "FALLBACK_OTHER_SCORE",
            "ZERO_SCORE",
            "NAN_INCLUDED",
            "NOT_EVALUATED",
            "OTHER",
        }:
            reason = "OTHER" if reason else "NOT_EVALUATED"
        counts[reason] += 1
        an = str(r.get("anchor") or "")
        miss_n[an] += 1
        for f in F1_RAW:
            if _f(r.get(f)) is None:
                feat[f] += 1
                break
    return {
        "C3_SCORE_MISSING_N": len(missing),
        "C3_MISSING_SCORE_HANDLING_COUNTS": dict(counts),
        "LIVE_C3_SCORE_WHEN_PANEL_OOF_MISSING_N": live_scored,
        "code_path": (
            "C3LookupEngine: non-executable skipped before rank; collected executable names "
            "with non-finite _predict_row are not added to simulate_joint. No CURRENT fallback, "
            "no zero fill, no NaN in rank. C3LookupEngine caches source_series at first CLOCK."
        ),
        "missing_lead_feature": dict(feat),
        "missing_by_anchor": dict(miss_n),
    }


def coverage_by_anchor(snaps: list[dict[str, Any]]) -> dict[str, Any]:
    tot: Counter[str] = Counter()
    exe: Counter[str] = Counter()
    c3: Counter[str] = Counter()
    for s in snaps:
        an = str(s.get("anchor") or "")
        tot[an] += 1
        if s.get("exact_executable"):
            exe[an] += 1
            if s.get("c3_feature_complete") and s.get("c3_live_score") is not None:
                c3[an] += 1
    rates = {}
    for an in tot:
        e = exe[an]
        rates[an] = {
            "snaps": tot[an],
            "exact_executable_n": e,
            "c3_scored_n": c3[an],
            "C3_SCORE_COVERAGE_RATE": (c3[an] / e) if e else None,
        }
    return rates


def decide(
    *,
    a2: dict[str, Any],
    c3o: dict[str, Any],
    c3f: dict[str, Any],
    c155: dict[str, Any],
    jac_exec: dict[str, Any],
    jac_rank: dict[str, Any],
    jac_a2: dict[str, Any],
    tw: dict[str, Any],
    cur_par: dict[str, Any],
    origin_complete: bool,
) -> dict[str, Any]:
    a2_ok = a2.get("CLOSED_N") == EXPECTED_A2_CLOSED and a2.get("ORIGIN_MISSING_N") == 0
    c3_ok = c3o.get("ORIGIN_MISSING_N") == 0 and c3f.get("ORIGIN_MISSING_N") == 0
    if not origin_complete or not a2_ok or not c3_ok:
        return {
            "VERDICT": "ENTRY_DECISION_PROVENANCE_INCOMPLETE",
            "PRIMARY_CAUSE": "ORIGIN_PROVENANCE_INCOMPLETE",
            "EXACT_CONTRACT_MISMATCH": True,
            "DECISION_POPULATION_MATCH": False,
            "EXECUTION_AWARE_OBJECTIVE_ALLOWED": False,
        }
    join_only = c155.get("A2_155_PRIMARY_CAUSE") == "FILL_TIME_JOIN_ARTIFACT" and (
        c155.get("A2_155_CAUSE_COUNTS") or {}
    ).get("FILL_TIME_JOIN_ARTIFACT") == c155.get("A2_155_N")
    pop_match = bool(jac_a2.get("DECISION_POPULATION_MATCH"))
    exec_tw_ok = int(tw.get("PANEL_vs_EXACT_MISMATCH_N") or 0) == 0 and int(tw.get("RAW_vs_EXACT_MISMATCH_N") or 0) == 0
    score_ok = int(cur_par.get("CURRENT_SCORE_MISMATCH_N") or 0) == 0
    a2_pending_all_exec = int(a2.get("EXACT_EXEC_FALSE_N") or 0) == 0
    mismatch = (
        not pop_match
        or not exec_tw_ok
        or not score_ok
        or not a2_pending_all_exec
        or int(a2.get("PANEL_vs_EXACT_MISMATCH_N") or 0) > 0
        or int(c3f.get("PANEL_C3_FINAL_AT_ORIGIN_N") or 0) < int(c3f.get("CLOSED_N") or 0)
        or int(c3o.get("PANEL_C3_OOF_AT_ORIGIN_N") or 0) < int(c3o.get("CLOSED_N") or 0)
        or not jac_rank.get("DECISION_POPULATION_MATCH")
    )
    if join_only and not mismatch:
        return {
            "VERDICT": "ENTRY_DECISION_CONTRACT_PROVEN_JOIN_FIXED",
            "PRIMARY_CAUSE": "FILL_TIME_JOIN_ARTIFACT",
            "EXACT_CONTRACT_MISMATCH": False,
            "DECISION_POPULATION_MATCH": True,
            "EXECUTION_AWARE_OBJECTIVE_ALLOWED": True,
        }
    if not mismatch and exec_tw_ok and pop_match:
        return {
            "VERDICT": "ENTRY_DECISION_CONTRACT_PROVEN",
            "PRIMARY_CAUSE": "CONTRACT_IDENTICAL",
            "EXACT_CONTRACT_MISMATCH": False,
            "DECISION_POPULATION_MATCH": True,
            "EXECUTION_AWARE_OBJECTIVE_ALLOWED": True,
        }
    primary = c155.get("A2_155_PRIMARY_CAUSE") or "DECISION_POPULATION_MISMATCH"
    if int(c3f.get("PANEL_C3_FINAL_AT_ORIGIN_N") or 0) < int(c3f.get("CLOSED_N") or 0):
        primary = "C3_EXACT_SCORE_POPULATION_NE_PANEL"
    return {
        "VERDICT": "ENTRY_DECISION_CONTRACT_MISMATCH",
        "PRIMARY_CAUSE": primary,
        "EXACT_CONTRACT_MISMATCH": True,
        "DECISION_POPULATION_MATCH": bool(pop_match),
        "EXECUTION_AWARE_OBJECTIVE_ALLOWED": False,
        "jac_exec_match": jac_exec.get("DECISION_POPULATION_MATCH"),
        "jac_a2_match": jac_a2.get("DECISION_POPULATION_MATCH"),
        "jac_rank_match": jac_rank.get("DECISION_POPULATION_MATCH"),
    }
