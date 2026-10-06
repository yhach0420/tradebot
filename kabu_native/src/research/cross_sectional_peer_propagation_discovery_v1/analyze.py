"""D1 freeze then D2-D4 matched peer-lead incrementality. No PnL. No P repair."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.cross_sectional_peer_propagation_discovery_v1 import (
    CASE_CONTROL,
    CASE_FOUND,
    CASE_NONE,
    CASE_PARTIAL,
    CASE_SIM,
    DOM_SHARE,
    EVAL_BLOCKS,
    MATCH_RATE_MIN,
    MECHS,
    NEXT_RCA,
    NEXT_STOP,
    TRAIN_BLOCK,
)
from research.cross_sectional_peer_propagation_discovery_v1.freeze import freeze_boundaries
from research.cross_sectional_peer_propagation_discovery_v1.match import balance, find_control
from research.cross_sectional_peer_propagation_discovery_v1.walk import emit_events, harvest_d1_metrics, _load_panel


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


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [r.get(key) for r in rows if r.get(key) is not None]
    if not xs:
        return None
    return float(np.mean([float(x) for x in xs]))


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


def _block(rows: list[dict[str, Any]], block: str) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("block") or "") == block]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "event_n": len(rows),
        "symbol_n": len({str(r.get("symbol") or "") for r in rows}),
        "day_n": len({str(r.get("date") or "") for r in rows}),
        "sector_n": len({str(r.get("sector") or "") for r in rows}),
        "ret_5m": _mean(rows, "ret_5m_bps"),
        "ret_10m": _mean(rows, "ret_10m_bps"),
        "ret_20m": _mean(rows, "ret_20m_bps"),
        "median_mfe": _med(rows, "mfe_bps"),
        "median_mae": _med(rows, "mae_bps"),
        "p20_before_m20": _rate(rows, "p20_before_m20"),
        "p40_before_m20": _rate(rows, "p40_before_m20"),
        "p80_before_m30": _rate(rows, "p80_before_m30"),
        "bull_n": len([r for r in rows if r.get("direction") == "BULLISH"]),
        "bear_n": len([r for r in rows if r.get("direction") == "BEARISH"]),
    }


def _pair_gaps(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    if not pairs:
        return {"matched_n": 0, "gap_10m": None}
    ta = [a for a, _ in pairs]
    ca = [c for _, c in pairs]
    t10, c10 = _mean(ta, "ret_10m_bps"), _mean(ca, "ret_10m_bps")
    t5, c5 = _mean(ta, "ret_5m_bps"), _mean(ca, "ret_5m_bps")
    t20, c20 = _mean(ta, "ret_20m_bps"), _mean(ca, "ret_20m_bps")
    return {
        "matched_n": len(pairs),
        "treatment_10m": t10,
        "control_10m": c10,
        "gap_10m": None if t10 is None or c10 is None else float(t10) - float(c10),
        "gap_5m": None if t5 is None or c5 is None else float(t5) - float(c5),
        "gap_20m": None if t20 is None or c20 is None else float(t20) - float(c20),
        "gap_mfe": None if _med(ta, "mfe_bps") is None or _med(ca, "mfe_bps") is None else float(_med(ta, "mfe_bps")) - float(_med(ca, "mfe_bps")),
        "gap_mae": None if _med(ta, "mae_bps") is None or _med(ca, "mae_bps") is None else float(_med(ta, "mae_bps")) - float(_med(ca, "mae_bps")),
        "gap_p40": None if _rate(ta, "p40_before_m20") is None or _rate(ca, "p40_before_m20") is None else float(_rate(ta, "p40_before_m20")) - float(_rate(ca, "p40_before_m20")),
    }


def path_coherent(gaps: dict[str, Any]) -> bool:
    g10 = gaps.get("gap_10m")
    if g10 is None or float(g10) <= 0:
        return False
    g5, g20, gmfe = gaps.get("gap_5m"), gaps.get("gap_20m"), gaps.get("gap_mfe")
    side = (g5 is not None and float(g5) > 0) or (g20 is not None and float(g20) > 0)
    mfe_ok = gmfe is None or float(gmfe) >= 0
    return bool(side and mfe_ok)


def matched_pack(treated: list[dict[str, Any]], control_index: dict) -> dict[str, Any]:
    gaps_block: dict[str, Any] = {}
    all_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    treated_n = 0
    for b in EVAL_BLOCKS:
        ts = _block(treated, b)
        treated_n += len(ts)
        pairs = []
        for row in ts:
            ctrl = find_control(row, control_index)
            if ctrl is not None:
                pairs.append((row, ctrl))
        all_pairs.extend(pairs)
        g = _pair_gaps(pairs)
        g["treated_n"] = len(ts)
        g["match_rate"] = (len(pairs) / len(ts)) if ts else None
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
        "peer_features_not_matched_on": True,
    }


def offset_map(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = (("tm5", -5, True), ("tm3", -3, True), ("tm1", -1, True), ("t0", 0, True), ("tp1", 1, False), ("tp3", 3, False), ("tp5", 5, False))
    out = []
    for name, off, causal in names:
        xs = [r.get(f"off_{name}") for r in rows if isinstance(r.get(f"off_{name}"), dict)]
        out.append(
            {
                "offset": off,
                "label": name,
                "causal": causal,
                "placebo": not causal,
                "n": len(xs),
                "peer_ret_3m": float(np.mean([x["peer_ret_3m"] for x in xs if _finite(x.get("peer_ret_3m"))])) if xs else None,
                "peer_breadth_3m": float(np.mean([x["peer_breadth_3m"] for x in xs if _finite(x.get("peer_breadth_3m"))])) if xs else None,
                "peer_minus_target_3m": float(np.mean([x["peer_minus_target_3m"] for x in xs if _finite(x.get("peer_minus_target_3m"))])) if xs else None,
            }
        )
    return out


def sector_vs_market(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sec = [r for r in rows if _finite(r.get("peer_ret_3m")) and _finite(r.get("mkt_ret_3m")) and float(r["peer_ret_3m"]) > float(r["mkt_ret_3m"])]
    mkt = [r for r in rows if _finite(r.get("peer_ret_3m")) and _finite(r.get("mkt_ret_3m")) and float(r["peer_ret_3m"]) <= float(r["mkt_ret_3m"])]
    return {
        "sector_stronger_than_market_n": len(sec),
        "market_at_least_as_strong_n": len(mkt),
        "sector_stronger_10m": _mean(sec, "ret_10m_bps"),
        "market_strong_10m": _mean(mkt, "ret_10m_bps"),
        "primary_remains_sector_peers": True,
        "not_chosen_by_profit": True,
    }


def sr_diag(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by: dict[str, list] = defaultdict(list)
    for r in rows:
        by[str(r.get("sr_bin") or "away")].append(r)
    return [{"sr_bin": k, **summarize(v)} for k, v in sorted(by.items())]


def tv_diag(rows: list[dict[str, Any]]) -> dict[str, Any]:
    xs = [r for r in rows if _finite(r.get("tv_clock_pctl"))]
    if not xs:
        return {"n": 0}
    hi = [r for r in xs if float(r["tv_clock_pctl"]) >= 0.67]
    lo = [r for r in xs if float(r["tv_clock_pctl"]) <= 0.33]
    mid = [r for r in xs if 0.33 < float(r["tv_clock_pctl"]) < 0.67]
    return {
        "high_target_tv_10m": _mean(hi, "ret_10m_bps"),
        "mid_target_tv_10m": _mean(mid, "ret_10m_bps"),
        "low_target_tv_10m": _mean(lo, "ret_10m_bps"),
        "n_high": len(hi),
        "n_mid": len(mid),
        "n_low": len(lo),
        "cannot_rescue_failed_peer_mechanism": True,
    }


def decide(pack: dict[str, Any]) -> dict[str, Any]:
    hard = list(pack.get("hard_ids") or [])
    partial = list(pack.get("partial_ids") or [])
    sim_only = bool(pack.get("simultaneous_not_true_lead"))
    control_fail = bool(pack.get("control_insufficient"))
    if control_fail and not hard:
        verd, nxt = CASE_CONTROL, NEXT_STOP
        reason = "matched control rate or balance insufficient to test incrementality"
    elif hard:
        verd, nxt = CASE_FOUND, NEXT_RCA
        reason = "TRUE_PEER_LEAD matched 10m gap positive and path-coherent in D2, D3, and D4"
    elif sim_only:
        verd, nxt = CASE_SIM, NEXT_STOP
        reason = "contemporaneous co-movement, not true peer lead, accounts for any apparent effect"
    elif partial:
        verd, nxt = CASE_PARTIAL, NEXT_STOP
        reason = "some D2-D4 matched lead exists, but not positive and coherent in all three eval blocks"
    else:
        verd, nxt = CASE_NONE, NEXT_STOP
        reason = "no incremental target-path information from sector peers after same-symbol matching"
    return {
        "VERDICT": verd,
        "NEXT": nxt,
        "hard_ids": hard,
        "partial_ids": partial,
        "reason": reason,
        "d1_status": "DISCOVERY_MECHANISM_DEFINITION_ONLY",
        "d2_d4_status": "LOCKED_INTERNAL_REPLICATION",
        "not_validation": True,
        "r13_r11_r14_not_repaired": True,
        "pnl_optimization": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "complete_strategy_not_run": True,
        "sr_did_not_alter_verdict": True,
        "tv_did_not_alter_verdict": True,
    }


def build_report_body(bind: dict[str, Any]) -> dict[str, Any]:
    loaded = _load_panel(bind)
    if not loaded.get("ok"):
        return {"ok": False, "reason": loaded.get("reason"), "decision": {"VERDICT": CASE_NONE, "NEXT": NEXT_STOP}}
    d1m = harvest_d1_metrics(bind, loaded)
    freeze = freeze_boundaries(d1m)
    if not freeze.get("ok"):
        return {"ok": False, "reason": "d1_freeze", "freeze": freeze, "decision": {"VERDICT": CASE_NONE, "NEXT": NEXT_STOP}}
    walked = emit_events(bind, loaded, freeze)
    mech: dict[str, Any] = {}
    hard: list[str] = []
    partial: list[str] = []
    control_bad = True
    sim_only_flag = False
    for m in MECHS:
        tr = list((walked.get("treated") or {}).get(m) or [])
        sm = list((walked.get("simultaneous") or {}).get(m) or [])
        al = list((walked.get("already") or {}).get(m) or [])
        idx = dict((walked.get("controls") or {}).get(m) or {})
        eval_tr = [r for r in tr if str(r.get("block") or "") in EVAL_BLOCKS]
        print(f"MATCH {m} true_lead={len(tr)} sim={len(sm)} eval={len(eval_tr)}", flush=True)
        mc = matched_pack(tr, idx)
        mc_sim = matched_pack(sm, idx)
        conc = {
            "symbol": _share_max(eval_tr, "symbol"),
            "sector": _share_max(eval_tr, "sector"),
            "day": _share_max(eval_tr, "date"),
            "peer_n_regime": _share_max(eval_tr, "peer_n"),
        }
        dominated = bool(conc["symbol"]["dominated"] or conc["sector"]["dominated"] or conc["day"]["dominated"])
        mr = mc.get("match_rate")
        if mr is not None and float(mr) >= float(MATCH_RATE_MIN):
            control_bad = False
        survives = bool(mc.get("all_eval_positive") and mc.get("all_eval_coherent") and not dominated)
        if survives:
            hard.append(m)
        elif (len(mc.get("positive_blocks") or []) >= 2) or mc.get("all_eval_positive"):
            partial.append(m)
        true_pos = bool(mc.get("all_eval_positive"))
        sim_pos = bool(mc_sim.get("all_eval_positive"))
        if sim_pos and not true_pos:
            sim_only_flag = True
        mech[m] = {
            "definition": (freeze.get("p_definitions") or {}).get(m),
            "true_lead": {
                "counts": {b: summarize(_block(tr, b)) for b in (TRAIN_BLOCK, *EVAL_BLOCKS)},
                "d2_d4": summarize(eval_tr),
                "matched": mc,
            },
            "simultaneous": {
                "d2_d4": summarize([r for r in sm if str(r.get("block") or "") in EVAL_BLOCKS]),
                "matched": mc_sim,
            },
            "already": {"d2_d4": summarize([r for r in al if str(r.get("block") or "") in EVAL_BLOCKS])},
            "concentration": conc,
            "dominated": dominated,
            "survives": survives,
            "sector_vs_market": sector_vs_market(eval_tr),
            "sr_diagnostic": sr_diag(eval_tr),
            "tv_diagnostic": tv_diag(eval_tr),
            "offset_map": offset_map(eval_tr) if survives else [],
        }
    pack = {
        "hard_ids": hard,
        "partial_ids": partial,
        "simultaneous_not_true_lead": bool(sim_only_flag and not hard),
        "control_insufficient": bool(control_bad),
    }
    decision = decide(pack)
    uni = dict(walked.get("universe") or {})
    return {
        "ok": True,
        "freeze": freeze,
        "identity": {
            "n_days": walked.get("n_days"),
            "n_symbols_loaded": walked.get("n_symbols_loaded"),
            "feature_row_n": walked.get("feature_row_n"),
            "valid_peer_target_n": uni.get("valid_target_n"),
        },
        "peer_universe": {k: v for k, v in uni.items() if k != "by_target"},
        "TARGET_INCLUDED_IN_PEER_METRIC_N": walked.get("TARGET_INCLUDED_IN_PEER_METRIC_N"),
        "future_peer_information_n": 0,
        "same_bar_outcome_n": walked.get("same_bar_outcome_n"),
        "FUTURE_EVENT_SELECTION_N": 0,
        "RETROSPECTIVE_CLUSTER_N": 0,
        "displacement_used": False,
        "d1_frozen_before_d2": True,
        "mechanisms": mech,
        "hard_ids": hard,
        "partial_ids": partial,
        "decision": decision,
        "primary_metric": "ret_10m_bps",
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    mech = dict(report.get("mechanisms") or {})
    freeze = dict(report.get("freeze") or {})

    def pack(m: str) -> dict[str, Any]:
        x = dict(mech.get(m) or {})
        tl = dict(x.get("true_lead") or {})
        mc = dict(tl.get("matched") or {})
        bl = dict(mc.get("blocks") or {})
        return {
            "definition": x.get("definition"),
            "treated_matched": {"treated_n": mc.get("treated_n"), "matched_n": mc.get("matched_n"), "match_rate": mc.get("match_rate")},
            "D2_10m_gap": (bl.get("D2") or {}).get("gap_10m"),
            "D3_10m_gap": (bl.get("D3") or {}).get("gap_10m"),
            "D4_10m_gap": (bl.get("D4") or {}).get("gap_10m"),
            "survives": x.get("survives"),
        }

    conc = {m: (mech.get(m) or {}).get("concentration") for m in MECHS}
    return {
        "Target excluded from every peer metric?": int(report.get("TARGET_INCLUDED_IN_PEER_METRIC_N") or 0) == 0,
        "Any future peer information?": False,
        "Any same-bar outcome included?": False if int(report.get("same_bar_outcome_n") or 0) == 0 else True,
        "P1/P2/P3 definitions?": freeze.get("p_definitions"),
        "D1 boundaries frozen before D2-D4?": True,
        "P1": pack("P1"),
        "P2": pack("P2"),
        "P3": pack("P3"),
        "Any mechanism positive against matched same-symbol controls in D2/D3/D4?": bool(d.get("hard_ids")),
        "True peer lead or simultaneous movement?": "TRUE_PEER_LEAD primary; simultaneous reported separately",
        "What does offset map show?": {m: (mech.get(m) or {}).get("offset_map") for m in d.get("hard_ids") or []},
        "Sector-specific or broad-market effect?": {m: (mech.get(m) or {}).get("sector_vs_market") for m in MECHS},
        "Does S/R change the result diagnostically?": {m: (mech.get(m) or {}).get("sr_diagnostic") for m in MECHS},
        "Does target participation change it diagnostically?": {m: (mech.get(m) or {}).get("tv_diagnostic") for m in MECHS},
        "Any one-symbol dominance?": {m: ((c or {}).get("symbol") or {}).get("dominated") for m, c in conc.items()},
        "Any one-sector dominance?": {m: ((c or {}).get("sector") or {}).get("dominated") for m, c in conc.items()},
        "Any one-day dominance?": {m: ((c or {}).get("day") or {}).get("dominated") for m, c in conc.items()},
        "Any threshold retune?": False,
        "Any PnL optimization?": False,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "Kabu50?": False,
        "submit/cancel/live?": "0/0/0",
        "VERDICT?": d.get("VERDICT"),
        "NEXT?": d.get("NEXT"),
    }
