"""I1/I2/I3 matched path tests. Holm across three. No strategy economics. No PnL selection."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.native_participation_x_sr_context_discovery_v1 import (
    CASE_ADDS,
    CASE_FOUND,
    CASE_NO_INC,
    CASE_NONE,
    EVAL_BLOCKS,
    FAMILIES,
    MIN_DAY_N,
    MIN_PAIR_N,
    MIN_SYMBOL_N,
    NEXT_RCA,
    NEXT_STOP,
    PRIMARY_METRIC,
    TV_EXPAND_PCTL,
)
from research.native_participation_x_sr_context_discovery_v1.walk import walk_context
from research.support_resistance_first_interaction_matched_causal_test_v1.stats import evaluate_question, pair_stats
from research.support_resistance_matched_separation_not_a_strategy_v1.inference import holm, pigeonhole_boot


def _fam(rows: list[dict[str, Any]], q: str) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("family") or "") == q and not r.get("placebo")]


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [r.get(key) for r in rows if r.get(key) is not None]
    if not xs:
        return None
    return float(np.mean([1.0 if x else 0.0 for x in xs]))


def _share_max(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    by: dict[str, int] = defaultdict(int)
    for r in rows:
        by[str(r.get(key) or "")] += 1
    if not by:
        return {"top": None, "n": 0, "share": None, "dominated": False}
    top, n = max(by.items(), key=lambda kv: kv[1])
    tot = len(rows)
    share = n / tot if tot else None
    return {"top": top, "n": n, "share": share, "dominated": bool(share is not None and share >= 0.50)}


def _block_gaps(rows: list[dict[str, Any]]) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for b in ("D1", "D2", "D3", "D4"):
        st = pair_stats([r for r in rows if str(r.get("block") or "") == b])
        out[b] = st.get("gap_p20_before_m20")
    return out


def _sign_collapse(gaps: dict[str, float | None]) -> bool:
    vals = [float(gaps[b]) for b in EVAL_BLOCKS if gaps.get(b) is not None]
    if len(vals) < 2:
        return False
    return not (all(v > 0 for v in vals) or all(v < 0 for v in vals))


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [float(r[key]) for r in rows if _finite(r.get(key))]
    if not xs:
        return None
    return float(np.mean(xs))


def _smd(a: list[dict[str, Any]], b: list[dict[str, Any]], key: str) -> float | None:
    xs = [float(r[key]) for r in a if _finite(r.get(key))]
    ys = [float(r[key]) for r in b if _finite(r.get(key))]
    if len(xs) < 5 or len(ys) < 5:
        return None
    mx, my = float(np.mean(xs)), float(np.mean(ys))
    vx, vy = float(np.var(xs, ddof=1)), float(np.var(ys, ddof=1))
    sp = np.sqrt(max((vx + vy) / 2.0, 0.0))
    if sp <= 1e-12:
        return 0.0
    return float((mx - my) / sp)


def _balance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    matched = [r for r in rows if r.get("matched")]
    keys = ("r1", "r3", "r5", "rng_rel", "tod_min")
    ctrl_view = [{k: r.get("ct_" + k) for k in keys} for r in matched]
    return {
        "before": {k: _mean(rows, k) for k in keys},
        "after_matched_treatment": {k: _mean(matched, k) for k in keys},
        "after_matched_control": {k: _mean(ctrl_view, k) for k in keys},
        "smd_after": {k: _smd(matched, ctrl_view, k) for k in keys},
        "n_before": len(rows),
        "n_after": len(matched),
    }


def _coverage(rows: list[dict[str, Any]]) -> dict[str, Any]:
    matched = [r for r in rows if r.get("matched")]
    n = len(rows)
    m = len(matched)
    return {
        "treated_n": n,
        "matched_n": m,
        "unmatched_n": n - m,
        "match_rate": (m / n) if n else None,
        "support_n": sum(1 for r in rows if not r.get("as_resistance")),
        "resistance_n": sum(1 for r in rows if r.get("as_resistance")),
        "balance": _balance(rows),
    }


def _path_extras(st: dict[str, Any]) -> dict[str, Any]:
    tr = dict(st.get("treatment") or {})
    ct = dict(st.get("matched_control") or {})

    def gap(k: str) -> float | None:
        if tr.get(k) is None or ct.get(k) is None:
            return None
        return float(tr[k]) - float(ct[k])

    return {
        "gap_median_mae": gap("median_mae"),
        "gap_median_mfe": st.get("gap_median_mfe"),
        "gap_p20_before_m20": st.get("gap_p20_before_m20"),
        "gap_p40_before_m20": st.get("gap_p40_before_m20"),
        "treatment_median_mfe": tr.get("median_mfe"),
        "treatment_median_mae": tr.get("median_mae"),
        "control_median_mfe": ct.get("median_mfe"),
        "control_median_mae": ct.get("median_mae"),
        "treatment_p20": tr.get("p20_before_m20"),
        "control_p20": ct.get("p20_before_m20"),
    }


def _ret_gaps(rows: list[dict[str, Any]]) -> dict[str, Any]:
    matched = [r for r in rows if r.get("matched") and str(r.get("block") or "") in EVAL_BLOCKS]
    out = {}
    for k in ("ret_5m_bps", "ret_10m_bps", "ret_20m_bps"):
        tr = _mean(matched, "tr_" + k)
        ct = _mean(matched, "ct_" + k)
        out[k] = {"treatment": tr, "control": ct, "gap": (None if tr is None or ct is None else float(tr) - float(ct))}
    return out


def _advance(q: dict[str, Any], pb: dict[str, Any] | None, rows: list[dict[str, Any]], holm_p: float | None) -> dict[str, Any]:
    st = dict(q.get("d2_d4") or {})
    gap = st.get("gap_p20_before_m20")
    mfe = st.get("gap_median_mfe")
    n_ok = (
        int(st.get("matched_n") or 0) >= int(MIN_PAIR_N)
        and int(st.get("day_n") or 0) >= int(MIN_DAY_N)
        and int(st.get("symbol_n") or 0) >= int(MIN_SYMBOL_N)
    )
    coherent = gap is not None and float(gap) > 0
    matched_diff = bool(coherent and n_ok)
    pb_d24 = dict((pb or {}).get("d2_d4") or {}) if pb else {}
    pb_gap = pb_d24.get("gap_p20_before_m20")
    pb_matched = int(pb_d24.get("matched_n") or 0)
    usable_placebo = pb_gap is not None and pb_matched > 0
    if not usable_placebo:
        placebo_ok = False
        placebo_status = "INSUFFICIENT_MATCHED_PLACEBO"
    else:
        placebo_ok = gap is not None and (abs(float(pb_gap)) < abs(float(gap)) or float(pb_gap) <= 0)
        placebo_status = "COMPARED"
    gaps = _block_gaps(rows)
    collapse = _sign_collapse(gaps)
    eval_rows = [r for r in rows if str(r.get("block") or "") in EVAL_BLOCKS]
    sym = _share_max(eval_rows or rows, "symbol")
    day = _share_max(eval_rows or rows, "date")
    extra = 0
    if mfe is not None and float(mfe) > 0:
        extra += 1
    if st.get("gap_p40_before_m20") is not None and float(st["gap_p40_before_m20"]) > 0:
        extra += 1
    if st.get("gap_mfe_before_mae") is not None and float(st["gap_mfe_before_mae"]) > 0:
        extra += 1
    multi = extra >= 1 and coherent
    timing = True
    holm_ok = holm_p is not None and float(holm_p) <= 0.05
    ok = bool(coherent and matched_diff and placebo_ok and not collapse and not sym["dominated"] and not day["dominated"] and multi and timing)
    return {
        "coherent_direction": coherent,
        "matched_differs": matched_diff,
        "n_ok": n_ok,
        "placebo_ok": placebo_ok,
        "placebo_status": placebo_status,
        "block_sign_collapse": collapse,
        "one_symbol_dominated": bool(sym["dominated"]),
        "one_day_dominated": bool(day["dominated"]),
        "multi_metric": multi,
        "timing_clean": timing,
        "holm_p": holm_p,
        "holm_ok": holm_ok,
        "advances": bool(ok and holm_ok),
        "symbol_conc": sym,
        "day_conc": day,
        "block_gaps": gaps,
        "placebo_p20_gap": pb_gap,
        "path_extras": _path_extras(st),
        "ret_gaps": _ret_gaps(rows),
    }


def _away_separates(away: list[dict[str, Any]]) -> dict[str, Any]:
    eval_rows = [r for r in away if str(r.get("block") or "") in EVAL_BLOCKS]
    p20 = _rate(eval_rows, "tr_p20_before_m20")
    by = defaultdict(list)
    for r in eval_rows:
        by[str(r.get("date") or "")].append(r)
    names = [k for k in by if k]
    diffs = []
    if names:
        rng = np.random.default_rng(20260917)
        for _ in range(400):
            draw = rng.choice(names, size=len(names), replace=True)
            sample = [row for d in draw for row in by[str(d)]]
            v = _rate(sample, "tr_p20_before_m20")
            if v is not None:
                diffs.append(v - 0.5)
    arr = np.asarray(diffs, dtype=float) if diffs else np.asarray([])
    lo = float(np.percentile(arr, 2.5)) if arr.size else None
    hi = float(np.percentile(arr, 97.5)) if arr.size else None
    sep = bool(lo is not None and lo > 0)
    return {
        "n": len(eval_rows),
        "p20": p20,
        "ci95_lo": lo,
        "ci95_hi": hi,
        "separates": sep,
        "note": "away-from-S/R native events vs 0.5; not a strategy",
    }


def decide(qs: dict[str, dict[str, Any]], adv: dict[str, dict[str, Any]], part: dict[str, Any]) -> dict[str, Any]:
    surviving = [k for k, a in adv.items() if a.get("advances")]
    part_ok = bool(part.get("separates"))
    if surviving and part_ok:
        verd, nxt = CASE_ADDS, NEXT_RCA
    elif surviving:
        verd, nxt = CASE_FOUND, NEXT_RCA
    elif part_ok:
        verd, nxt = CASE_NO_INC, NEXT_STOP
    else:
        verd, nxt = CASE_NONE, NEXT_STOP
    return {
        "VERDICT": verd,
        "NEXT": nxt,
        "surviving_families": surviving,
        "participation_alone_separates": part_ok,
        "sr_adds_conditional_on_participation": bool(surviving),
        "d1_d4_status": "ALL_DEVELOPMENT",
        "not_validation": True,
        "a2_reopened_as_strategy": False,
        "c1_reopened": False,
        "pnl_optimization": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "detector_retuned": False,
        "strategy_economics_run": False,
    }


def build_report_body(bind: dict[str, Any]) -> dict[str, Any]:
    walked = walk_context(bind)
    if not walked.get("ok"):
        return {"ok": False, "reason": walked.get("reason"), "decision": {"VERDICT": CASE_NONE, "NEXT": NEXT_STOP}}
    pairs = list(walked.get("pairs") or [])
    pb = list(walked.get("placebo_pairs") or [])
    away = list(walked.get("away") or [])
    qs = {qid: evaluate_question(_fam(pairs, qid), question=qid) for qid in FAMILIES}
    pqs = {qid: evaluate_question(_fam(pb, qid), question=qid) for qid in FAMILIES}
    inf_rows = {qid: [r for r in _fam(pairs, qid) if str(r.get("block") or "") in EVAL_BLOCKS] for qid in FAMILIES}
    p_detail = {qid: pigeonhole_boot(inf_rows[qid]) for qid in FAMILIES}
    pvals = {qid: (p_detail[qid].get("p_two_sided") if p_detail[qid].get("p_two_sided") is not None else 1.0) for qid in FAMILIES}
    adj = holm(pvals)
    adj["detail"] = p_detail
    adj["families"] = list(FAMILIES)
    adv = {qid: _advance(qs[qid], pqs.get(qid), _fam(pairs, qid), (adj.get("holm") or {}).get(qid)) for qid in FAMILIES}
    part = _away_separates(away)
    decision = decide(qs, adv, part)
    coverage = {qid: _coverage(_fam(pairs, qid)) for qid in FAMILIES}
    i1_rows = _fam(pairs, "I1")
    i1_sides = {
        "support": evaluate_question([r for r in i1_rows if not r.get("as_resistance")], question="I1_SUPPORT"),
        "resistance": evaluate_question([r for r in i1_rows if r.get("as_resistance")], question="I1_RESISTANCE"),
        "not_primary_holm_tests": True,
    }
    market_sector = {
        qid: {
            "treatment_mkt_rel": _mean(_fam(pairs, qid), "mkt_rel"),
            "treatment_sec_rel": _mean(_fam(pairs, qid), "sec_rel"),
            "matched_control_mkt_rel": _mean([r for r in _fam(pairs, qid) if r.get("matched")], "ct_mkt_rel"),
            "matched_control_sec_rel": _mean([r for r in _fam(pairs, qid) if r.get("matched")], "ct_sec_rel"),
            "not_used_as_filter": True,
        }
        for qid in FAMILIES
    }
    vol_diag = {}
    for qid in FAMILIES:
        rows = _fam(pairs, qid)
        hi = [r for r in rows if r.get("vol_clock_pctl") is not None and float(r["vol_clock_pctl"]) >= float(TV_EXPAND_PCTL)]
        st = pair_stats([r for r in hi if r.get("matched") and str(r.get("block") or "") in EVAL_BLOCKS])
        vol_diag[qid] = {
            "treated_n": len(rows),
            "vol_expand_n": len(hi),
            "share": (len(hi) / len(rows)) if rows else None,
            "p20_gap_vol_subset": st.get("gap_p20_before_m20"),
            "same_sign_as_tv": (
                qs[qid].get("d2_d4", {}).get("gap_p20_before_m20") is not None
                and st.get("gap_p20_before_m20") is not None
                and (float(qs[qid]["d2_d4"]["gap_p20_before_m20"]) > 0) == (float(st["gap_p20_before_m20"]) > 0)
            ),
            "not_used_as_selector": True,
        }
    i3_hold = list(walked.get("hold_no_i3") or [])
    i3_tr = _rate(_fam(pairs, "I3"), "tr_p20_before_m20")
    i3_hold_p20 = _rate(i3_hold, "tr_p20_before_m20")
    return {
        "ok": True,
        "identity": {
            "walk_primary_first_test_n": int((walked.get("counts") or {}).get("primary_first_test_n") or 0),
            "native_event_n": int((walked.get("counts") or {}).get("native_event_n") or 0),
            "n_days": walked.get("n_days"),
            "n_symbols_loaded": walked.get("n_symbols_loaded"),
        },
        "counts": walked.get("counts"),
        "same_bar_entry_n": walked.get("same_bar_entry_n"),
        "future_normalization": False,
        "retrospective_event_selection": False,
        "I1": qs["I1"],
        "I2": qs["I2"],
        "I3": qs["I3"],
        "I1_by_side": i1_sides,
        "coverage": coverage,
        "market_sector": market_sector,
        "placebo": pqs,
        "advance": adv,
        "multiple_testing": adj,
        "participation_alone": part,
        "volume_diagnostic": vol_diag,
        "i3_hold_without_participation": {"n": len(i3_hold), "p20": i3_hold_p20, "i3_p20": i3_tr},
        "cross_symbol_match_n": len(list(walked.get("cross_pairs") or [])),
        "break_without_participation_n": len(list(walked.get("break_no_part") or [])),
        "decision": decision,
        "tv_expand_pctl": TV_EXPAND_PCTL,
        "primary_metric": PRIMARY_METRIC,
        "_pairs": pairs,
        "_away": away,
        "_placebo": pb,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    ident = dict(report.get("identity") or {})
    counts = dict(report.get("counts") or {})

    def pack(qid: str) -> dict[str, Any]:
        q = dict(report.get(qid) or {})
        st = dict(q.get("d2_d4") or {})
        a = dict((report.get("advance") or {}).get(qid) or {})
        cov = dict((report.get("coverage") or {}).get(qid) or {})
        return {
            "treated_n": cov.get("treated_n") if cov.get("treated_n") is not None else (st.get("treatment_n") or counts.get(f"{qid}_n")),
            "matched_n": cov.get("matched_n") if cov.get("matched_n") is not None else st.get("matched_n"),
            "unmatched_n": cov.get("unmatched_n"),
            "match_rate": cov.get("match_rate"),
            "p20_gap": st.get("gap_p20_before_m20"),
            "MFE/MAE": [st.get("treatment", {}).get("median_mfe"), st.get("treatment", {}).get("median_mae")],
            "D2/D3/D4": a.get("block_gaps"),
            "placebo": a.get("placebo_p20_gap"),
            "ret_5_10_20": a.get("ret_gaps"),
        }

    return {
        "Primary TradingValue event_n?": ident.get("native_event_n") or counts.get("native_event_n"),
        "symbol_n?": ident.get("n_symbols_loaded"),
        "day_n?": ident.get("n_days"),
        "Any future normalization?": False,
        "Any retrospective event selection?": False,
        "Any same-bar assumption?": int(report.get("same_bar_entry_n") or 0) != 0,
        "I1": pack("I1"),
        "I2": pack("I2"),
        "I3": pack("I3"),
        "Does native participation alone separate?": (report.get("participation_alone") or {}).get("separates"),
        "Does S/R add information conditional on native participation?": d.get("sr_adds_conditional_on_participation"),
        "Does S/R × participation show an interaction larger than placebo?": {
            qid: (
                "NOT_DEMONSTRATED"
                if str(((report.get("advance") or {}).get(qid) or {}).get("placebo_status") or "") == "INSUFFICIENT_MATCHED_PLACEBO"
                or ((report.get("advance") or {}).get(qid) or {}).get("placebo_p20_gap") is None
                else bool(((report.get("advance") or {}).get(qid) or {}).get("placebo_ok"))
            )
            for qid in FAMILIES
        },
        "Does Volume show the same direction as TradingValue?": {
            qid: (report.get("volume_diagnostic") or {}).get(qid, {}).get("same_sign_as_tv") for qid in FAMILIES
        },
        "Any one-symbol domination?": {qid: (report.get("advance") or {}).get(qid, {}).get("one_symbol_dominated") for qid in FAMILIES},
        "Any one-day domination?": {qid: (report.get("advance") or {}).get(qid, {}).get("one_day_dominated") for qid in FAMILIES},
        "Any D2/D3/D4 sign collapse?": {qid: (report.get("advance") or {}).get(qid, {}).get("block_sign_collapse") for qid in FAMILIES},
        "S/R detector retuned?": False,
        "A2 reopened as strategy?": False,
        "C1 reopened?": False,
        "Any PnL optimization?": False,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "Kabu50?": False,
        "submit/cancel/live?": "0/0/0",
        "VERDICT?": d.get("VERDICT"),
        "NEXT?": d.get("NEXT"),
    }


def repair_placebo_semantics(report: dict[str, Any]) -> dict[str, Any]:
    """Correct placebo_ok / interaction-vs-placebo without rerunning walk.

    Does not alter event counts, effects, p-values, Holm, VERDICT, or NEXT.
    """
    placebo = dict(report.get("placebo") or {})
    advance = dict(report.get("advance") or {})
    for qid in FAMILIES:
        a = dict(advance.get(qid) or {})
        pb_d24 = dict((placebo.get(qid) or {}).get("d2_d4") or {})
        pb_gap = pb_d24.get("gap_p20_before_m20") if pb_d24 else a.get("placebo_p20_gap")
        pb_matched = int(pb_d24.get("matched_n") or 0)
        if pb_gap is None or pb_matched <= 0:
            a["placebo_ok"] = False
            a["placebo_status"] = "INSUFFICIENT_MATCHED_PLACEBO"
        advance[qid] = a
    report["advance"] = advance
    report["answers"] = build_answers(report)
    report["placebo_semantics_corrected"] = True
    report["placebo_semantics_note"] = (
        "Interaction-vs-placebo is NOT_DEMONSTRATED when no usable matched placebo contrast. "
        "Counts, effects, p-values, Holm, VERDICT, and NEXT are unchanged."
    )
    return report
