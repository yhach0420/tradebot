"""Evaluate frozen SMA5/25/75 pullback playbook. No tree. No PnL. No rule repair after D2."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cross_sectional_peer_propagation_discovery_v1.match import balance, find_control
from research.sma5_25_75_trend_pullback_playbook_discovery_v1 import (
    CASE_FOUND,
    CASE_NONE,
    CASE_PARTIAL,
    DOM_SHARE,
    EVAL_BLOCKS,
    MATCH_RATE_MIN,
    MIN_DAY_N,
    MIN_SYMBOL_N,
    NEXT_RCA,
    NEXT_STOP,
    TRAIN_BLOCK,
)
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.freeze import freeze_payload
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.match import index_by_key
from research.sma5_25_75_trend_pullback_playbook_discovery_v1.walk import emit_events


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
        "risk_bps": _med(rows, "risk_bps"),
        "mfe_over_risk": _med(rows, "mfe_over_risk"),
        "signal_to_entry_bps": _mean(rows, "signal_to_entry_bps"),
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
    }


def _diag_split(rows: list[dict[str, Any]], pred) -> dict[str, Any]:
    yes = [r for r in rows if pred(r)]
    no = [r for r in rows if not pred(r)]
    pack = matched_pack(yes, no) if yes and no else {"matched_n": 0, "all_eval_positive": False, "blocks": {}, "gap_10m": None}
    return {
        "yes_n": len(yes),
        "no_n": len(no),
        "yes": summarize(yes),
        "no": summarize(no),
        "matched": pack,
        "adds_all_eval": bool(pack.get("all_eval_positive") and pack.get("all_eval_coherent")),
    }


def decide(pack: dict[str, Any]) -> dict[str, Any]:
    ab = dict(pack.get("a_vs_b") or {})
    dominated = bool(pack.get("dominated"))
    control_bad = bool(pack.get("control_insufficient"))
    coverage = bool(pack.get("coverage_ok"))
    if control_bad or not coverage:
        verd, nxt = CASE_NONE, NEXT_STOP
        reason = "insufficient matched coverage to test whether 5/25/75 structure adds to the same 1m trigger"
    elif ab.get("all_eval_positive") and ab.get("all_eval_coherent") and not dominated:
        verd, nxt = CASE_FOUND, NEXT_RCA
        reason = "aligned 5/25/75 pullback+trigger beats the same 1m trigger without structure on next-open 10m in D2/D3/D4"
    elif len(ab.get("positive_blocks") or []) >= 2 or ab.get("all_eval_positive"):
        verd, nxt = CASE_PARTIAL, NEXT_STOP
        reason = "some D2-D4 incrementality exists, but not positive and path-coherent in all three eval blocks"
    else:
        verd, nxt = CASE_NONE, NEXT_STOP
        reason = "5/25/75 aligned pullback does not add stable executable path separation vs the same 1m trigger"
    return {
        "VERDICT": verd,
        "NEXT": nxt,
        "reason": reason,
        "structure_adds_to_same_trigger": bool(ab.get("all_eval_positive") and ab.get("all_eval_coherent")),
        "sma25_pullback_adds": bool((pack.get("sma25") or {}).get("adds_all_eval")),
        "vwap_adds": bool((pack.get("vwap") or {}).get("adds_all_eval")),
        "sr_adds": bool((pack.get("sr") or {}).get("adds_all_eval")),
        "participation_adds": bool((pack.get("participation") or {}).get("adds_all_eval")),
        "ma_period_tuned": False,
        "threshold_pnl_tuned": False,
        "peer_rescue": False,
        "pnl_optimization": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "complete_strategy_not_run": True,
        "diagnostics_did_not_alter_primary_a_vs_b": True,
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
    print(f"MATCH A={len(a)} B={len(b)} C={len(c)}", flush=True)
    a_vs_b = matched_pack(a, b)
    sma25 = matched_pack(a, c)
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
    vwap = _diag_split(eval_a, lambda r: bool(r.get("vwap_near") or r.get("vwap_hold") or r.get("vwap_reclaim")))
    sr = _diag_split(eval_a, lambda r: bool(r.get("sr_touch")))
    part = _diag_split(eval_a, lambda r: bool(r.get("participation_sequence")))
    loc: dict[str, list] = defaultdict(list)
    for r in eval_a:
        loc[str(r.get("location_cat") or "none")].append(r)
    loc_rows = [{"cat": k, **summarize(v)} for k, v in sorted(loc.items())]
    pack = {
        "a_vs_b": a_vs_b,
        "sma25": {"matched": sma25, "adds_all_eval": bool(sma25.get("all_eval_positive") and sma25.get("all_eval_coherent"))},
        "vwap": vwap,
        "sr": sr,
        "participation": part,
        "dominated": dominated,
        "control_insufficient": control_bad,
        "coverage_ok": coverage_ok,
    }
    decision = decide(pack)
    by_block = {bname: summarize(_block(a, bname)) for bname in (TRAIN_BLOCK, *EVAL_BLOCKS)}
    return {
        "ok": True,
        "freeze": freeze,
        "identity": {"n_days": walked.get("n_days"), "n_symbols_loaded": walked.get("n_symbols_loaded")},
        "setups": {
            "A": summarize(a),
            "B": summarize(b),
            "C": summarize(c),
            "A_eval": summarize(eval_a),
            "by_block": by_block,
        },
        "a_vs_b": a_vs_b,
        "sma25_pullback": pack["sma25"],
        "vwap": vwap,
        "sr": sr,
        "participation": part,
        "location": loc_rows,
        "concentration": conc,
        "dominated": dominated,
        "same_bar_entry_n": walked.get("same_bar_entry_n"),
        "FUTURE_SETUP_SELECTION_N": walked.get("FUTURE_SETUP_SELECTION_N"),
        "dead_before_entry_n": walked.get("dead_before_entry_n"),
        "displacement_used": False,
        "peer_used": False,
        "decision": decision,
        "primary_metric": "next_open_ret_10m_bps",
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    s = dict(report.get("setups") or {})
    a = dict(s.get("A") or {})
    ab = dict(report.get("a_vs_b") or {})
    bl = dict(ab.get("blocks") or {})
    sma = dict(report.get("sma25_pullback") or {})
    return {
        "setup_n?": a.get("event_n"),
        "bull / bear n?": {"bull": a.get("bull_n"), "bear": a.get("bear_n")},
        "D1/D2/D3/D4?": {k: (dict((s.get("by_block") or {}).get(k) or {}).get("event_n")) for k in ("D1", "D2", "D3", "D4")},
        "Does 5/25/75 aligned structure add to the same 1m trigger?": d.get("structure_adds_to_same_trigger"),
        "Does SMA25 pullback matter?": sma.get("adds_all_eval"),
        "Does VWAP add inside the setup?": d.get("vwap_adds"),
        "Does S/R add inside the setup?": d.get("sr_adds"),
        "Does participation contraction→expansion add?": d.get("participation_adds"),
        "What is executable next-open MFE/MAE?": {"mfe": a.get("mfe"), "mae": a.get("mae"), "time_to_mfe": a.get("time_to_mfe"), "time_to_invalidation": a.get("time_to_invalidation")},
        "What is structural-risk-normalized MFE?": a.get("mfe_over_risk"),
        "How much move occurs before executable entry?": a.get("signal_to_entry_bps"),
        "A vs B D2/D3/D4 10m gap?": {b: (bl.get(b) or {}).get("gap_10m") for b in ("D2", "D3", "D4")},
        "Any future setup selection?": False if int(report.get("FUTURE_SETUP_SELECTION_N") or 0) == 0 else True,
        "Any MA period optimization?": False,
        "Any threshold PnL tuning?": False,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "submit/cancel/live?": "0/0/0",
        "VERDICT?": d.get("VERDICT"),
        "NEXT?": d.get("NEXT"),
        "same_bar_entry_n": report.get("same_bar_entry_n"),
    }
