"""Evaluate frozen live-causal 5m SMA playbook. No tree. No PnL. No rule repair after D2."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cross_sectional_peer_propagation_discovery_v1.match import balance, find_control
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1 import (
    CASE_FOUND,
    CASE_NONE,
    CASE_PARTIAL,
    CASE_SEMANTICS,
    DOM_SHARE,
    EVAL_BLOCKS,
    MATCH_RATE_MIN,
    MIN_DAY_N,
    MIN_SYMBOL_N,
    NEGLIGIBLE_ABS_BPS,
    NEXT_RCA,
    NEXT_STOP,
    TRAIN_BLOCK,
)
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.freeze import freeze_payload
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.match import index_by_key
from research.mtf_5min_sma5_25_75_with_1min_trigger_v1.walk import emit_events


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


def _med(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [float(r[key]) for r in rows if _finite(r.get(key))]
    if not xs:
        return None
    return float(np.median(xs))


def _block(rows: list[dict[str, Any]], block: str) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("block") or "") == block]


def _share_max(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    by: dict[str, int] = defaultdict(int)
    for r in rows:
        by[str(r.get(key) or "")] += 1
    if not by:
        return {"top": None, "n": 0, "share": None, "dominated": False}
    top, n = max(by.items(), key=lambda kv: kv[1])
    tot = len(rows)
    share = n / tot if tot else None
    return {"top": top, "n": n, "share": share, "dominated": bool(share is not None and share >= DOM_SHARE)}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "event_n": len(rows),
        "symbol_n": len({str(r.get("symbol") or "") for r in rows}),
        "day_n": len({str(r.get("date") or "") for r in rows}),
        "bull_n": len([r for r in rows if r.get("direction") == "BULLISH"]),
        "bear_n": len([r for r in rows if r.get("direction") == "BEARISH"]),
        "ret_5m": _mean(rows, "ret_5m_bps"),
        "ret_10m": _mean(rows, "ret_10m_bps"),
        "ret_20m": _mean(rows, "ret_20m_bps"),
        "mfe": _med(rows, "mfe_bps"),
        "mae": _med(rows, "mae_bps"),
        "time_to_mfe": _mean(rows, "time_to_mfe"),
        "time_to_invalidation": _mean(rows, "time_to_invalidation"),
        "time_to_sma25_fail": _mean(rows, "time_to_sma25_fail"),
        "risk_bps": _med(rows, "risk_bps"),
        "mfe_over_risk": _med(rows, "mfe_over_risk"),
        "signal_to_entry_bps": _mean(rows, "signal_to_entry_bps"),
        "reclaim_n": len([r for r in rows if r.get("reclaim")]),
        "swing_break_n": len([r for r in rows if r.get("swing_break")]),
        "sma25_failed_n": len([r for r in rows if r.get("sma25_failed")]),
        "confirmed_agrees_n": len([r for r in rows if r.get("confirmed_stack_agrees")]),
    }


def _pair_gaps(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    if not pairs:
        return {"matched_n": 0, "gap_10m": None}
    ta = [a for a, _ in pairs]
    ca = [c for _, c in pairs]
    t10, c10 = _mean(ta, "ret_10m_bps"), _mean(ca, "ret_10m_bps")
    t5, c5 = _mean(ta, "ret_5m_bps"), _mean(ca, "ret_5m_bps")
    t20, c20 = _mean(ta, "ret_20m_bps"), _mean(ca, "ret_20m_bps")
    tmfe, cmfe = _med(ta, "mfe_bps"), _med(ca, "mfe_bps")
    tmae, cmae = _med(ta, "mae_bps"), _med(ca, "mae_bps")
    trisk, crisk = _med(ta, "mfe_over_risk"), _med(ca, "mfe_over_risk")
    return {
        "matched_n": len(pairs),
        "treatment_10m": t10,
        "control_10m": c10,
        "gap_5m": None if t5 is None or c5 is None else float(t5) - float(c5),
        "gap_10m": None if t10 is None or c10 is None else float(t10) - float(c10),
        "gap_20m": None if t20 is None or c20 is None else float(t20) - float(c20),
        "gap_mfe": None if tmfe is None or cmfe is None else float(tmfe) - float(cmfe),
        "gap_mae": None if tmae is None or cmae is None else float(tmae) - float(cmae),
        "gap_mfe_over_risk": None if trisk is None or crisk is None else float(trisk) - float(crisk),
    }


def path_coherent(gaps: dict[str, Any]) -> bool:
    g10 = gaps.get("gap_10m")
    if g10 is None or float(g10) <= 0:
        return False
    g5, g20, gmfe = gaps.get("gap_5m"), gaps.get("gap_20m"), gaps.get("gap_mfe")
    side = (g5 is not None and float(g5) > 0) or (g20 is not None and float(g20) > 0)
    mfe_ok = gmfe is None or float(gmfe) >= 0
    return bool(side and mfe_ok)


def matched_pack(treated: list[dict[str, Any]], control_rows: list[dict[str, Any]]) -> dict[str, Any]:
    idx = index_by_key(control_rows)
    gaps_block: dict[str, Any] = {}
    all_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    treated_n = 0
    for b in EVAL_BLOCKS:
        ts = _block(treated, b)
        treated_n += len(ts)
        pairs = []
        for row in ts:
            ctrl = find_control(row, idx)
            if ctrl is not None:
                pairs.append((row, ctrl))
        all_pairs.extend(pairs)
        g = _pair_gaps(pairs)
        g["treated_n"] = len(ts)
        g["match_rate"] = (len(pairs) / len(ts)) if ts else None
        g["path_coherent"] = path_coherent(g)
        gaps_block[b] = g
    pos = [b for b in EVAL_BLOCKS if (gaps_block.get(b) or {}).get("gap_10m") is not None and float(gaps_block[b]["gap_10m"]) > 0]
    coh = [b for b in EVAL_BLOCKS if path_coherent(gaps_block.get(b) or {})]
    pooled = _pair_gaps(all_pairs)
    return {
        "treated_n": treated_n,
        "matched_n": len(all_pairs),
        "match_rate": (len(all_pairs) / treated_n) if treated_n else None,
        **{k: v for k, v in pooled.items() if k != "matched_n"},
        "blocks": gaps_block,
        "positive_blocks": pos,
        "coherent_blocks": coh,
        "all_eval_positive": len(pos) == 3,
        "all_eval_coherent": len(coh) == 3,
        "balance": balance(all_pairs),
        "future_outcomes_not_used_for_matching": True,
        "matched_on_5m_ma_structure": False,
    }


def _diag_split(rows: list[dict[str, Any]], pred) -> dict[str, Any]:
    yes = [r for r in rows if pred(r)]
    no = [r for r in rows if not pred(r)]
    pack = matched_pack(yes, no) if yes and no else {"matched_n": 0, "all_eval_positive": False, "all_eval_coherent": False, "blocks": {}, "gap_10m": None}
    return {
        "yes_n": len(yes),
        "no_n": len(no),
        "yes": summarize(yes),
        "no": summarize(no),
        "matched": pack,
        "adds_all_eval": bool(pack.get("all_eval_positive") and pack.get("all_eval_coherent")),
        "descriptive_only": True,
        "cannot_rescue_primary": True,
    }


def _block_gaps(pack: dict[str, Any]) -> dict[str, Any]:
    bl = dict(pack.get("blocks") or {})
    return {b: (bl.get(b) or {}).get("gap_10m") for b in EVAL_BLOCKS}


def economically_material(ab: dict[str, Any]) -> bool:
    """Predeclared: min D2/D3/D4 matched 10m gap must exceed NEGLIGIBLE_ABS_BPS. Not searched."""
    bl = dict(ab.get("blocks") or {})
    xs = []
    for b in EVAL_BLOCKS:
        g = (bl.get(b) or {}).get("gap_10m")
        if g is None:
            return False
        xs.append(float(g))
    if any(v <= 0 for v in xs):
        return False
    return float(min(xs)) >= float(NEGLIGIBLE_ABS_BPS)


def decide(pack: dict[str, Any]) -> dict[str, Any]:
    ab = dict(pack.get("a_vs_b") or {})
    ac = dict((pack.get("sma25") or {}).get("matched") or pack.get("a_vs_c") or {})
    dominated = bool(pack.get("dominated"))
    control_bad = bool(pack.get("control_insufficient"))
    coverage = bool(pack.get("coverage_ok"))
    semantics_broken = bool(pack.get("semantics_broken"))
    live_gate = bool(ab.get("all_eval_positive") and ab.get("all_eval_coherent"))
    conf_gate = bool((pack.get("confirmed_a_vs_b") or {}).get("all_eval_positive") and (pack.get("confirmed_a_vs_b") or {}).get("all_eval_coherent"))
    conf_compared = bool(pack.get("confirmed_comparable"))
    disagree = bool(conf_compared and live_gate != conf_gate)
    material = economically_material(ab)
    c_adds = bool((pack.get("sma25") or {}).get("adds_all_eval"))
    if semantics_broken or disagree:
        verd, nxt = CASE_SEMANTICS, NEXT_STOP
        reason = "live-causal 5m MA semantics are not reproducible against completed-bar confirmed SMA, or a future 5m close / lunch synthetic was detected"
    elif control_bad or not coverage:
        verd, nxt = CASE_NONE, NEXT_STOP
        reason = "insufficient matched coverage to test whether 5m SMA structure adds to the same 1m trigger"
    elif live_gate and material and not dominated:
        verd, nxt = CASE_FOUND, NEXT_RCA
        reason = "live-causal 5m SMA5/25/75 pullback+1m trigger beats the same 1m trigger without 5m structure on next-open 10m in D2/D3/D4, path-coherent, and not economically negligible"
    elif live_gate and not material:
        verd, nxt = CASE_PARTIAL, NEXT_STOP
        reason = "A vs B signs survive D2/D3/D4 but the matched 10m gap is economically negligible"
    elif len(ab.get("positive_blocks") or []) >= 2 or ab.get("all_eval_positive"):
        verd, nxt = CASE_PARTIAL, NEXT_STOP
        reason = "some D2-D4 incrementality exists, but not positive and path-coherent in all three eval blocks"
    else:
        verd, nxt = CASE_NONE, NEXT_STOP
        reason = "5m SMA5/25/75 aligned pullback does not add stable executable path separation vs the same 1m trigger"
    return {
        "VERDICT": verd,
        "NEXT": nxt,
        "reason": reason,
        "structure_adds_to_same_trigger": bool(live_gate and material),
        "sma25_pullback_adds": c_adds,
        "economically_material": material,
        "live_vs_confirmed_disagree": disagree,
        "confirmed_gate": conf_gate,
        "live_gate": live_gate,
        "vwap_adds": bool((pack.get("vwap") or {}).get("adds_all_eval")),
        "sr_adds": bool((pack.get("sr") or {}).get("adds_all_eval")),
        "tv_adds": bool((pack.get("tv") or {}).get("adds_all_eval")),
        "ma_period_tuned": False,
        "aux_filter_optimized": False,
        "threshold_pnl_tuned": False,
        "peer_rescue": False,
        "pnl_optimization": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "complete_strategy_not_run": True,
        "diagnostics_did_not_alter_primary_a_vs_b": True,
        "chose_better_of_live_vs_confirmed": False,
        "a_vs_c_reported": True,
        "ac_all_eval_positive": bool(ac.get("all_eval_positive")),
        "ac_all_eval_coherent": bool(ac.get("all_eval_coherent")),
    }


def _timeframe_comparison(bind: dict[str, Any], live_ab: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    parent = dict(bind.get("parent_report") or {})
    one_m = {
        "architecture": "1-minute SMA5/25/75 pullback playbook",
        "VERDICT": parent.get("VERDICT"),
        "NEXT": parent.get("NEXT"),
        "a_vs_b_10m": parent.get("a_vs_b_10m"),
        "structure_adds": parent.get("structure_adds"),
        "setup_n": parent.get("setup_n"),
        "unconditional_10m": parent.get("unconditional_10m"),
        "closed": True,
    }
    five_m = {
        "architecture": "live-causal 5-minute SMA5/25/75 + 1-minute trigger",
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "a_vs_b_10m": _block_gaps(live_ab),
        "structure_adds": decision.get("structure_adds_to_same_trigger"),
        "pooled": False,
    }
    one_failed = one_m.get("VERDICT") == "SMA5_25_75_PULLBACK_PARTIAL_V1" or one_m.get("structure_adds") is False
    five_found = decision.get("VERDICT") == CASE_FOUND
    if five_found and one_failed:
        interp = "prior 1-minute SMA failure is consistent with a timeframe-semantics problem"
    elif decision.get("VERDICT") in {CASE_NONE, CASE_PARTIAL} and one_failed:
        interp = "result does not differ materially: both 1m and 5m SMA5/25/75 playbooks fail the D2/D3/D4 incrementality gate"
    elif five_found and not one_failed:
        interp = "both architectures would be mechanisms; they were not pooled"
    else:
        interp = "5m live-causal SMA result differs in verdict or path from the closed 1m SMA playbook; they were not pooled"
    return {
        "pooled": False,
        "one_minute": one_m,
        "five_minute": five_m,
        "differs_materially_from_1m_sma_test": bool(decision.get("VERDICT") != one_m.get("VERDICT")),
        "timeframe_semantics_interpretation": interp,
    }


def build_report_body(bind: dict[str, Any]) -> dict[str, Any]:
    freeze = freeze_payload()
    walked = emit_events(bind)
    if not walked.get("ok"):
        return {"ok": False, "reason": walked.get("reason"), "freeze": freeze, "decision": {"VERDICT": CASE_NONE, "NEXT": NEXT_STOP}}
    ev = dict(walked.get("events") or {})
    a = list(ev.get("A") or [])
    b = list(ev.get("B") or [])
    c = list(ev.get("C") or [])
    a_conf = list(ev.get("A_conf") or [])
    b_conf = list(ev.get("B_conf") or [])
    print(f"MATCH A={len(a)} B={len(b)} C={len(c)} A_conf={len(a_conf)} B_conf={len(b_conf)}", flush=True)
    a_vs_b = matched_pack(a, b)
    a_vs_c = matched_pack(a, c)
    conf_ab = matched_pack(a_conf, b_conf) if a_conf and b_conf else {"matched_n": 0, "all_eval_positive": False, "all_eval_coherent": False, "blocks": {}, "match_rate": None}
    eval_a = [r for r in a if str(r.get("block") or "") in EVAL_BLOCKS]
    conc = {
        "symbol": _share_max(eval_a, "symbol"),
        "day": _share_max(eval_a, "date"),
        "direction": _share_max(eval_a, "direction"),
    }
    dominated = bool(conc["symbol"]["dominated"] or conc["day"]["dominated"])
    mr = a_vs_b.get("match_rate")
    control_bad = mr is None or float(mr) < float(MATCH_RATE_MIN)
    coverage_ok = len({r.get("symbol") for r in eval_a}) >= int(MIN_SYMBOL_N) and len({r.get("date") for r in eval_a}) >= int(MIN_DAY_N)
    conf_mr = conf_ab.get("match_rate")
    confirmed_comparable = bool(a_conf) and conf_mr is not None and float(conf_mr) >= float(MATCH_RATE_MIN)
    vwap = _diag_split(eval_a, lambda r: bool(r.get("vwap_near") or r.get("vwap_hold") or r.get("vwap_reclaim")))
    sr = _diag_split(eval_a, lambda r: bool(r.get("sr_touch")))
    tv = _diag_split(eval_a, lambda r: bool(r.get("participation_sequence")))
    audit = dict(walked.get("bars5_audit") or {})
    semantics_broken = (
        int(walked.get("FUTURE_5M_CLOSE_USAGE_N") or 0) != 0
        or int(audit.get("lunch_synthetic_n") or 0) != 0
        or int(audit.get("overnight_synthetic_n") or 0) != 0
        or int(audit.get("live_sma_defined_n") or 0) == 0
    )
    pack = {
        "a_vs_b": a_vs_b,
        "a_vs_c": a_vs_c,
        "sma25": {"matched": a_vs_c, "adds_all_eval": bool(a_vs_c.get("all_eval_positive") and a_vs_c.get("all_eval_coherent"))},
        "vwap": vwap,
        "sr": sr,
        "tv": tv,
        "dominated": dominated,
        "control_insufficient": control_bad,
        "coverage_ok": coverage_ok,
        "semantics_broken": semantics_broken,
        "confirmed_a_vs_b": conf_ab,
        "confirmed_comparable": confirmed_comparable,
    }
    decision = decide(pack)
    tf = _timeframe_comparison(bind, a_vs_b, decision)
    by_block = {bname: summarize(_block(a, bname)) for bname in (TRAIN_BLOCK, *EVAL_BLOCKS)}
    agree_n = len([r for r in a if r.get("confirmed_stack_agrees")])
    return {
        "ok": True,
        "freeze": freeze,
        "identity": {"n_days": walked.get("n_days"), "n_symbols_loaded": walked.get("n_symbols_loaded")},
        "setups": {
            "A": summarize(a),
            "B": summarize(b),
            "C": summarize(c),
            "A_conf": summarize(a_conf),
            "B_conf": summarize(b_conf),
            "A_eval": summarize(eval_a),
            "by_block": by_block,
        },
        "a_vs_b": a_vs_b,
        "a_vs_c": a_vs_c,
        "sma25_pullback": pack["sma25"],
        "confirmed_a_vs_b": conf_ab,
        "confirmed_agree_rate": (agree_n / len(a)) if a else None,
        "vwap": vwap,
        "sr": sr,
        "tv": tv,
        "concentration": conc,
        "dominated": dominated,
        "same_bar_entry_n": walked.get("same_bar_entry_n"),
        "FUTURE_SETUP_SELECTION_N": walked.get("FUTURE_SETUP_SELECTION_N"),
        "FUTURE_5M_CLOSE_USAGE_N": walked.get("FUTURE_5M_CLOSE_USAGE_N"),
        "dead_before_entry_n": walked.get("dead_before_entry_n"),
        "bars5_audit": audit,
        "timeframe_comparison": tf,
        "displacement_used": False,
        "peer_used": False,
        "decision": decision,
        "primary_metric": "next_open_ret_10m_bps",
        "primary_ma": "LIVE_CAUSAL_5M_SMA",
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    s = dict(report.get("setups") or {})
    a = dict(s.get("A") or {})
    ab = dict(report.get("a_vs_b") or {})
    ac = dict(report.get("a_vs_c") or {})
    bl = dict(ab.get("blocks") or {})
    cl = dict(ac.get("blocks") or {})
    audit = dict(report.get("bars5_audit") or {})
    tf = dict(report.get("timeframe_comparison") or {})
    sma = dict(report.get("sma25_pullback") or {})
    return {
        "5m bars constructed causally?": True,
        "Any future 5m close used?": False if int(report.get("FUTURE_5M_CLOSE_USAGE_N") or 0) == 0 else True,
        "Does MA history span sessions?": bool(audit.get("ma_history_spans_sessions", True)),
        "Any lunch synthetic bars?": False if int(audit.get("lunch_synthetic_n") or 0) == 0 else True,
        "Live causal MA semantics?": "LIVE_CAUSAL_5M_SMA = prior completed 5m closes + current 1m close as provisional 5m close",
        "A setup_n?": a.get("event_n"),
        "bull/bear?": {"bull": a.get("bull_n"), "bear": a.get("bear_n")},
        "D1/D2/D3/D4?": {k: (dict((s.get("by_block") or {}).get(k) or {}).get("event_n")) for k in ("D1", "D2", "D3", "D4")},
        "A vs B matched 10m:": {b: (bl.get(b) or {}).get("gap_10m") for b in ("D2", "D3", "D4")},
        "A vs C:": {b: (cl.get(b) or {}).get("gap_10m") for b in ("D2", "D3", "D4")},
        "Does 5m SMA structure add to same 1m trigger?": d.get("structure_adds_to_same_trigger"),
        "Does SMA25 pullback add inside 5m aligned trend?": sma.get("adds_all_eval"),
        "Next-open MFE/MAE?": {
            "mfe": a.get("mfe"),
            "mae": a.get("mae"),
            "time_to_mfe": a.get("time_to_mfe"),
            "time_to_invalidation": a.get("time_to_invalidation"),
            "time_to_sma25_fail": a.get("time_to_sma25_fail"),
        },
        "Structural MFE/risk?": a.get("mfe_over_risk"),
        "How much movement before entry?": a.get("signal_to_entry_bps"),
        "Does confirmed-5m MA diagnostic agree with live-causal semantics?": (not bool(d.get("live_vs_confirmed_disagree"))),
        "Does result differ materially from previous 1m SMA test?": tf.get("differs_materially_from_1m_sma_test"),
        "Any MA period optimization?": False,
        "Any auxiliary filter optimization?": False,
        "Any PnL optimization?": False,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "submit/cancel/live?": "0/0/0",
        "VERDICT?": d.get("VERDICT"),
        "NEXT?": d.get("NEXT"),
        "same_bar_entry_n": report.get("same_bar_entry_n"),
        "FUTURE_5M_CLOSE_USAGE_N": report.get("FUTURE_5M_CLOSE_USAGE_N"),
    }
