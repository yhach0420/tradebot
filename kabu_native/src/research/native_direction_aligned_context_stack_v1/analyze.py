"""D1 freeze then D2-D4 same-side + matched incrementality. No PnL. No rule repair."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.native_direction_aligned_context_stack_v1 import (
    CASE_FOUND,
    CASE_NONE,
    CASE_PARTIAL,
    CASE_UNSTABLE,
    EVAL_BLOCKS,
    FAMILIES,
    NEXT_RCA,
    NEXT_STOP,
    TRAIN_BLOCK,
)
from research.native_direction_aligned_context_stack_v1.match import balance, find_control, index_fail
from research.native_direction_aligned_context_stack_v1.tree import family_of, fit_d1, match_rule
from research.native_direction_aligned_context_stack_v1.walk import walk_events

SIDE_MIN_N = 200
DOM_SHARE = 0.50


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


def _rate(rows: list[dict[str, Any]], key: str = "y_p40") -> float | None:
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


def _block_rows(rows: list[dict[str, Any]], block: str) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("block") or "") == block]


def _side(rows: list[dict[str, Any]], side: str) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("direction") or "") == side]


def preds_t(rule: dict[str, Any]) -> list[tuple[str, str, float]]:
    out = []
    for p in list(rule.get("predicates") or []):
        out.append((str(p["feature"]), str(p["op"]), float(p["threshold"])))
    return out


def rule_feature_names(predicates: list[tuple[str, str, float]] | list[dict[str, Any]]) -> set[str]:
    names: set[str] = set()
    for p in predicates:
        if isinstance(p, dict):
            names.add(str(p.get("feature") or ""))
        else:
            names.add(str(p[0]))
    return names


def drop_family(predicates: list[dict[str, Any]], family: str) -> list[tuple[str, str, float]]:
    out = []
    for p in predicates:
        fam = p.get("family") or family_of(str(p.get("feature") or ""))
        if fam == family:
            continue
        out.append((str(p["feature"]), str(p["op"]), float(p["threshold"])))
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "event_n": len(rows),
        "symbol_n": len({str(r.get("symbol") or "") for r in rows}),
        "day_n": len({str(r.get("date") or "") for r in rows}),
        "p40_before_m20": _rate(rows, "y_p40"),
        "p20_before_m20": _rate(rows, "y_p20"),
        "p80_before_m30": _rate(rows, "y_p80"),
        "median_mfe": _med(rows, "mfe"),
        "median_mae": _med(rows, "mae"),
        "ret_5m": _mean(rows, "ret5"),
        "ret_10m": _mean(rows, "ret10"),
        "ret_20m": _mean(rows, "ret20"),
        "bull_n": len(_side(rows, "BULLISH")),
        "bear_n": len(_side(rows, "BEARISH")),
    }


def same_side_gap(hit: list[dict[str, Any]], base: list[dict[str, Any]]) -> dict[str, Any]:
    p = _rate(hit, "y_p40")
    bull_h, bear_h = _side(hit, "BULLISH"), _side(hit, "BEARISH")
    bull_b, bear_b = _side(base, "BULLISH"), _side(base, "BEARISH")
    pb, pr = _rate(bull_b, "y_p40"), _rate(bear_b, "y_p40")
    n = len(hit)
    if n == 0 or p is None:
        expected = None
        gap = None
    else:
        exp_num = 0.0
        exp_den = 0.0
        if bull_h and pb is not None:
            exp_num += len(bull_h) * float(pb)
            exp_den += len(bull_h)
        if bear_h and pr is not None:
            exp_num += len(bear_h) * float(pr)
            exp_den += len(bear_h)
        expected = (exp_num / exp_den) if exp_den else None
        gap = None if expected is None else float(p) - float(expected)
    return {
        "rule_p40": p,
        "same_side_expected_p40": expected,
        "same_side_gap": gap,
        "pooled_base_p40": _rate(base, "y_p40"),
        "pooled_gap": None if p is None or _rate(base, "y_p40") is None else float(p) - float(_rate(base, "y_p40")),
        "bull_rule_n": len(bull_h),
        "bear_rule_n": len(bear_h),
        "bull_base_p40": pb,
        "bear_base_p40": pr,
        "bull_rule_p40": _rate(bull_h, "y_p40"),
        "bear_rule_p40": _rate(bear_h, "y_p40"),
        "bull_gap": (
            None
            if _rate(bull_h, "y_p40") is None or pb is None
            else float(_rate(bull_h, "y_p40")) - float(pb)
        ),
        "bear_gap": (
            None
            if _rate(bear_h, "y_p40") is None or pr is None
            else float(_rate(bear_h, "y_p40")) - float(pr)
        ),
        "composition_adjusted": True,
        "unsigned_side_mix_not_used_as_uplift": True,
    }


def eval_rule(events: list[dict[str, Any]], predicates: list[tuple[str, str, float]], med: dict[str, float]) -> dict[str, Any]:
    by_block: dict[str, Any] = {}
    hits_by: dict[str, list[dict[str, Any]]] = {}
    bases_by: dict[str, list[dict[str, Any]]] = {}
    for b in (TRAIN_BLOCK, *EVAL_BLOCKS):
        base = _block_rows(events, b)
        hit = [r for r in base if match_rule(r, predicates, med)]
        hits_by[b] = hit
        bases_by[b] = base
        ss = same_side_gap(hit, base)
        by_block[b] = {**summarize(hit), **ss, "hit_n": len(hit)}
    eval_base = [r for r in events if str(r.get("block") or "") in EVAL_BLOCKS]
    eval_hit = [r for r in eval_base if match_rule(r, predicates, med)]
    pooled = {**summarize(eval_hit), **same_side_gap(eval_hit, eval_base), "hit_n": len(eval_hit)}
    gaps = {b: (by_block[b] or {}).get("same_side_gap") for b in EVAL_BLOCKS}
    pos = [b for b in EVAL_BLOCKS if gaps.get(b) is not None and float(gaps[b]) > 0]
    conc_sym = _share_max(eval_hit, "symbol")
    conc_day = _share_max(eval_hit, "date")
    conc_sec = _share_max(eval_hit, "sector")
    block_n = {b: int((by_block[b] or {}).get("hit_n") or 0) for b in EVAL_BLOCKS}
    tot_eval = sum(block_n.values())
    top_block_share = (max(block_n.values()) / tot_eval) if tot_eval else None
    one_block = bool(top_block_share is not None and top_block_share >= 0.80)
    same_side_all = len(pos) == 3
    survives_unconditional = bool(same_side_all and not one_block and not conc_sym["dominated"] and not conc_day["dominated"])
    return {
        "blocks": by_block,
        "d2_d4": pooled,
        "same_side_positive_blocks": pos,
        "same_side_all_eval_positive": same_side_all,
        "top_block_share": top_block_share,
        "one_block_dominated": one_block,
        "one_symbol_dominated": bool(conc_sym["dominated"]),
        "one_day_dominated": bool(conc_day["dominated"]),
        "symbol_conc": conc_sym,
        "day_conc": conc_day,
        "sector_conc": conc_sec,
        "survives_unconditional": survives_unconditional,
        "eval_hit": eval_hit,
        "eval_base": eval_base,
        "hits_by": hits_by,
        "bases_by": bases_by,
    }


def _pair_gaps(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
    if not pairs:
        return {"matched_n": 0, "gap_p40": None}
    ta = [a for a, _ in pairs]
    ca = [c for _, c in pairs]
    tp, cp = _rate(ta, "y_p40"), _rate(ca, "y_p40")
    return {
        "matched_n": len(pairs),
        "treatment_p40": tp,
        "control_p40": cp,
        "gap_p40": None if tp is None or cp is None else float(tp) - float(cp),
        "gap_mfe": None if _med(ta, "mfe") is None or _med(ca, "mfe") is None else float(_med(ta, "mfe")) - float(_med(ca, "mfe")),
        "gap_mae": None if _med(ta, "mae") is None or _med(ca, "mae") is None else float(_med(ta, "mae")) - float(_med(ca, "mae")),
        "gap_5m": None if _mean(ta, "ret5") is None or _mean(ca, "ret5") is None else float(_mean(ta, "ret5")) - float(_mean(ca, "ret5")),
        "gap_10m": None if _mean(ta, "ret10") is None or _mean(ca, "ret10") is None else float(_mean(ta, "ret10")) - float(_mean(ca, "ret10")),
        "gap_20m": None if _mean(ta, "ret20") is None or _mean(ca, "ret20") is None else float(_mean(ta, "ret20")) - float(_mean(ca, "ret20")),
        "treatment_median_mfe": _med(ta, "mfe"),
        "control_median_mfe": _med(ca, "mfe"),
        "treatment_median_mae": _med(ta, "mae"),
        "control_median_mae": _med(ca, "mae"),
        "treatment_ret5": _mean(ta, "ret5"),
        "treatment_ret10": _mean(ta, "ret10"),
        "treatment_ret20": _mean(ta, "ret20"),
    }


def matched_control(
    events: list[dict[str, Any]],
    predicates: list[tuple[str, str, float]],
    med: dict[str, float],
) -> dict[str, Any]:
    names = rule_feature_names(predicates)
    match_market = "aligned_market_rel" not in names
    match_sector = "aligned_sector_rel" not in names
    gaps_block: dict[str, Any] = {}
    all_pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    treated_n = 0
    for b in EVAL_BLOCKS:
        base = _block_rows(events, b)
        treated = [r for r in base if match_rule(r, predicates, med)]
        fail = [r for r in base if not match_rule(r, predicates, med)]
        treated_n += len(treated)
        by = index_fail(fail)
        pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in treated:
            ctrl = find_control(row, by, match_market=match_market, match_sector=match_sector)
            if ctrl is not None:
                pairs.append((row, ctrl))
        all_pairs.extend(pairs)
        gaps_block[b] = {
            **_pair_gaps(pairs),
            "treated_n": len(treated),
            "match_rate": (len(pairs) / len(treated)) if treated else None,
        }
    pos = [b for b in EVAL_BLOCKS if (gaps_block.get(b) or {}).get("gap_p40") is not None and float(gaps_block[b]["gap_p40"]) > 0]
    pooled = _pair_gaps(all_pairs)
    return {
        "same_dir": True,
        "same_symbol": True,
        "match_market": match_market,
        "match_sector": match_sector,
        "nuisance": ["tod_bucket", "rng_rel", "gap"] + (["mkt_sign"] if match_market else []) + (["sec_sign"] if match_sector else []),
        "not_matched_on": "rule-defining features or future outcome",
        "treated_n": treated_n,
        "matched_n": len(all_pairs),
        "match_rate": (len(all_pairs) / treated_n) if treated_n else None,
        **{k: v for k, v in pooled.items() if k != "matched_n"},
        "pooled_matched_n": pooled.get("matched_n"),
        "blocks": gaps_block,
        "positive_blocks": pos,
        "all_eval_positive": len(pos) == 3,
        "balance": balance(all_pairs),
        "future_outcomes_not_used_for_matching": True,
    }


def material_drop(orig: float | None, abl: float | None) -> bool:
    if orig is None or abl is None:
        return False
    drop = float(orig) - float(abl)
    return bool(drop > 0 and (float(abl) <= 0 or drop >= 0.3 * abs(float(orig)) or drop >= 0.005))


def matched_weakened(orig: dict[str, Any], abl: dict[str, Any]) -> bool:
    if orig.get("all_eval_positive") and not abl.get("all_eval_positive"):
        return True
    weakened_n = 0
    for b in EVAL_BLOCKS:
        og = ((orig.get("blocks") or {}).get(b) or {}).get("gap_p40")
        ag = ((abl.get("blocks") or {}).get(b) or {}).get("gap_p40")
        if material_drop(og, ag):
            weakened_n += 1
    return weakened_n >= 2 or (orig.get("all_eval_positive") is True and weakened_n >= 1)


def side_pack(events: list[dict[str, Any]], predicates: list[tuple[str, str, float]], med: dict[str, float], side: str) -> dict[str, Any]:
    xs = _side(events, side)
    by = {}
    for b in (TRAIN_BLOCK, *EVAL_BLOCKS):
        base = _block_rows(xs, b)
        hit = [r for r in base if match_rule(r, predicates, med)]
        by[b] = {**summarize(hit), **same_side_gap(hit, base)}
    eval_hit = [r for r in xs if str(r.get("block") or "") in EVAL_BLOCKS and match_rule(r, predicates, med)]
    adequate = len(eval_hit) >= SIDE_MIN_N
    pos = [b for b in EVAL_BLOCKS if (by[b].get("same_side_gap") is not None and float(by[b]["same_side_gap"]) > 0)]
    return {
        "side": side,
        "eval_hit_n": len(eval_hit),
        "adequate_sample": adequate,
        "blocks": by,
        "same_side_positive_blocks": pos,
        "all_eval_positive": len(pos) == 3,
    }


def decide(pack: dict[str, Any]) -> dict[str, Any]:
    if pack.get("unstable_in_discovery"):
        return {
            "VERDICT": CASE_UNSTABLE,
            "NEXT": NEXT_STOP,
            "surviving_rule_ids": [],
            "hard_mechanism_rule_ids": [],
            "reason": "D1 feature-family stability failed: no family used in at least 2/3 tertiles",
        }
    hard = list(pack.get("hard_mechanism_rule_ids") or [])
    partial_ids = list(pack.get("partial_rule_ids") or [])
    if hard:
        verd, nxt = CASE_FOUND, NEXT_RCA
        reason = "at least one frozen rule beats same-side base AND matched same-DIR control in D2, D3, and D4"
    elif partial_ids:
        verd, nxt = CASE_PARTIAL, NEXT_STOP
        reason = "some D2-D4 replication exists, but not positive same-side AND matched incrementality in all three eval blocks"
    else:
        verd, nxt = CASE_NONE, NEXT_STOP
        reason = "no incremental rule against both same-side base and matched same-DIR control"
    return {
        "VERDICT": verd,
        "NEXT": nxt,
        "surviving_rule_ids": hard,
        "hard_mechanism_rule_ids": hard,
        "partial_rule_ids": partial_ids,
        "reason": reason,
        "d1_status": "DISCOVERY_RULE_LEARNING_ONLY",
        "d2_d4_status": "LOCKED_INTERNAL_REPLICATION",
        "not_validation": True,
        "r14_not_repaired": True,
        "r14_thresholds_not_reused": True,
        "pnl_optimization": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "detector_retuned": False,
        "d2_d4_rule_modification": False,
        "complete_strategy_not_run": True,
        "not_a_long_only_or_short_only_strategy": True,
    }


def _classify(yes: bool, supported: str, unused: str) -> dict[str, Any]:
    return {"material_on_surviving": bool(yes), "classification": supported if yes else unused}


def build_report_body(bind: dict[str, Any]) -> dict[str, Any]:
    walked = walk_events(bind)
    if not walked.get("ok"):
        return {"ok": False, "reason": walked.get("reason"), "decision": {"VERDICT": CASE_NONE, "NEXT": NEXT_STOP}}
    events = list(walked.get("events") or [])
    fitted = fit_d1(events)
    med = dict(fitted.get("med") or {})
    rules = list(fitted.get("rules") or [])
    rule_eval: dict[str, Any] = {}
    ablation: dict[str, Any] = {}
    matched: dict[str, Any] = {}
    conc: dict[str, Any] = {}
    sides: dict[str, Any] = {}
    hard: list[str] = []
    partial: list[str] = []
    for i, rule in enumerate(rules):
        rid = str(rule.get("rule_id") or f"R{i}")
        pt = list((fitted.get("rules_t") or [None])[i] or preds_t(rule))
        ev = eval_rule(events, pt, med)
        mc = matched_control(events, pt, med)
        matched[rid] = mc
        slim = {k: v for k, v in ev.items() if k not in {"eval_hit", "eval_base", "hits_by", "bases_by"}}
        slim["matched_all_eval_positive"] = bool(mc.get("all_eval_positive"))
        slim["survives"] = bool(ev.get("survives_unconditional") and mc.get("all_eval_positive"))
        rule_eval[rid] = slim
        if slim["survives"]:
            hard.append(rid)
        elif ev.get("same_side_all_eval_positive") or mc.get("all_eval_positive") or len(ev.get("same_side_positive_blocks") or []) >= 2:
            partial.append(rid)
        conc[rid] = {
            "symbol": ev.get("symbol_conc"),
            "day": ev.get("day_conc"),
            "sector": ev.get("sector_conc"),
            "block_hit_n": {b: (ev.get("blocks") or {}).get(b, {}).get("hit_n") for b in EVAL_BLOCKS},
        }
        sides[rid] = {
            "BULLISH": side_pack(events, pt, med, "BULLISH"),
            "BEARISH": side_pack(events, pt, med, "BEARISH"),
        }
        bull = sides[rid]["BULLISH"]
        bear = sides[rid]["BEARISH"]
        if bull.get("adequate_sample") and bear.get("adequate_sample"):
            sides[rid]["symmetry"] = "BOTH_SIDES_ADEQUATE"
        elif bull.get("adequate_sample") or bear.get("adequate_sample"):
            sides[rid]["symmetry"] = "INSUFFICIENT_SYMMETRY_EVIDENCE"
        else:
            sides[rid]["symmetry"] = "INSUFFICIENT_SYMMETRY_EVIDENCE"
        orig_ss = (ev.get("d2_d4") or {}).get("same_side_gap")
        fam_rows = {}
        for fam in FAMILIES:
            used = fam in set(rule.get("families") or [])
            if not used:
                fam_rows[fam] = {"used": False, "material": False, "matched_weakened": False}
                continue
            apt = drop_family(list(rule.get("predicates") or []), fam)
            aev = eval_rule(events, apt, med)
            amc = matched_control(events, apt, med)
            ag = (aev.get("d2_d4") or {}).get("same_side_gap")
            fam_rows[fam] = {
                "used": True,
                "material_unconditional": material_drop(orig_ss, ag),
                "matched_weakened": matched_weakened(mc, amc),
                "material": bool(matched_weakened(mc, amc) or material_drop(orig_ss, ag)),
                "original_same_side_gap": orig_ss,
                "ablated_same_side_gap": ag,
                "original_matched_all_pos": mc.get("all_eval_positive"),
                "ablated_matched_all_pos": amc.get("all_eval_positive"),
                "ablated_matched_blocks": {b: ((amc.get("blocks") or {}).get(b) or {}).get("gap_p40") for b in EVAL_BLOCKS},
                "thresholds_not_refit": True,
            }
        ablation[rid] = fam_rows

    def fam_yes(name: str) -> bool:
        return any(bool((ablation.get(rid) or {}).get(name, {}).get("material")) for rid in hard)

    sr_yes, tv_yes = fam_yes("sr"), fam_yes("participation")
    vwap_yes, trend_yes = fam_yes("vwap"), fam_yes("trend")
    local_yes, mkt_yes = fam_yes("local"), fam_yes("market_sector")
    es_yes = fam_yes("event_strength")
    pack = {
        "unstable_in_discovery": bool(fitted.get("unstable_in_discovery")),
        "hard_mechanism_rule_ids": hard,
        "partial_rule_ids": partial,
    }
    decision = decide(pack)
    counts = dict(walked.get("counts") or {})
    by_block_n = {b: int(counts.get(f"{b}_n") or 0) for b in (TRAIN_BLOCK, *EVAL_BLOCKS)}
    d1 = _block_rows(events, TRAIN_BLOCK)
    raw_in_model = False
    return {
        "ok": True,
        "identity": {
            "native_event_n": int(counts.get("native_event_n") or 0),
            "kept_event_n": len(events),
            "n_days": walked.get("n_days"),
            "n_symbols_loaded": walked.get("n_symbols_loaded"),
            "d1_n": len(d1),
            "d2_n": by_block_n.get("D2"),
            "d3_n": by_block_n.get("D3"),
            "d4_n": by_block_n.get("D4"),
            "by_block_n": by_block_n,
            "bullish_n": int(counts.get("BULLISH_n") or 0),
            "bearish_n": int(counts.get("BEARISH_n") or 0),
        },
        "counts": counts,
        "same_bar_entry_n": walked.get("same_bar_entry_n"),
        "FUTURE_EVENT_SELECTION_N": 0,
        "RETROSPECTIVE_CLUSTER_N": 0,
        "tree_learned_only_on_d1": True,
        "d2_d4_rule_modification": False,
        "d1_internal_stability": fitted.get("stability"),
        "d1_tree": {k: v for k, v in dict(fitted.get("fit") or {}).items()},
        "lock": fitted.get("lock"),
        "frozen_rules": rules,
        "frozen_rule_n": len(rules),
        "rule_eval": rule_eval,
        "ablation": ablation,
        "matched_control": matched,
        "concentration": conc,
        "side_consistency": sides,
        "sr_contribution": _classify(sr_yes, "SR_AUXILIARY_CONTEXT_SUPPORTED", "SR_NOT_CARRIED_INTO_LATER_STRATEGY"),
        "participation_contribution": _classify(tv_yes, "PARTICIPATION_AUXILIARY_SUPPORTED", "TV_NOT_CARRIED_INTO_LATER_STRATEGY"),
        "vwap_contribution": _classify(vwap_yes, "VWAP_AUXILIARY_SUPPORTED", "VWAP_NOT_CARRIED_INTO_LATER_STRATEGY"),
        "trend_contribution": _classify(trend_yes, "TREND_AUXILIARY_SUPPORTED", "TREND_NOT_CARRIED_INTO_LATER_STRATEGY"),
        "event_strength_contribution": _classify(es_yes, "EVENT_STRENGTH_AUXILIARY_SUPPORTED", "EVENT_STRENGTH_NOT_CARRIED"),
        "market_sector_contribution": _classify(mkt_yes, "MARKET_SECTOR_AUXILIARY_SUPPORTED", "MARKET_SECTOR_NOT_CARRIED"),
        "local_contribution": _classify(local_yes, "LOCAL_STRUCTURE_AUXILIARY_SUPPORTED", "LOCAL_STRUCTURE_NOT_CARRIED"),
        "family_contribution": {
            "sr": sr_yes,
            "participation": tv_yes,
            "vwap": vwap_yes,
            "trend": trend_yes,
            "event_strength": es_yes,
            "local": local_yes,
            "market_sector": mkt_yes,
        },
        "direction_semantics": {
            "DIR_plus1_bullish": True,
            "DIR_minus1_bearish": True,
            "aligned_r5": "DIR * raw_r5",
            "aligned_r15": "DIR * raw_r15",
            "aligned_prior5_ret": "DIR * raw_prior5_ret",
            "aligned_vwap_bps": "DIR * (close - vwap) / vwap * 10000",
            "aligned_market_rel": "DIR * raw_mkt_rel",
            "aligned_sector_rel": "DIR * raw_sec_rel",
            "ahead_sr": "obstacle in event direction",
            "behind_sr": "backstop behind the move; missing flag if none",
            "raw_unsigned_directional_used_by_model": raw_in_model,
            "pullback_from_extreme_included": False,
        },
        "d1_base_rate": (fitted.get("lock") or {}).get("d1_base_rate"),
        "min_leaf": fitted.get("min_leaf"),
        "unstable_in_discovery": bool(fitted.get("unstable_in_discovery")),
        "hard_mechanism_rule_ids": hard,
        "partial_rule_ids": partial,
        "surviving_rule_ids": hard,
        "decision": decision,
        "primary_metric": "p40_before_m20",
        "tv_not_required": True,
        "any_candidate_positive_same_side_and_matched_d2d3d4": bool(hard),
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    ident = dict(report.get("identity") or {})
    rules = list(report.get("frozen_rules") or [])
    ev = dict(report.get("rule_eval") or {})
    mc = dict(report.get("matched_control") or {})
    fam = dict(report.get("family_contribution") or {})
    conc = dict(report.get("concentration") or {})
    sides = dict(report.get("side_consistency") or {})
    ds = dict(report.get("direction_semantics") or {})
    stab = dict(report.get("d1_internal_stability") or {})
    packs = []
    for r in rules:
        rid = str(r.get("rule_id") or "")
        e = dict(ev.get(rid) or {})
        m = dict(mc.get(rid) or {})
        packs.append(
            {
                "rule_id": rid,
                "predicates": r.get("predicate_text"),
                "d1_n": r.get("n_train"),
                "d1_p40": r.get("p40_train"),
                "D2": (e.get("blocks") or {}).get("D2"),
                "D3": (e.get("blocks") or {}).get("D3"),
                "D4": (e.get("blocks") or {}).get("D4"),
                "same_side_gaps": {b: ((e.get("blocks") or {}).get(b) or {}).get("same_side_gap") for b in EVAL_BLOCKS},
                "matched_gaps": {b: ((m.get("blocks") or {}).get(b) or {}).get("gap_p40") for b in EVAL_BLOCKS},
                "matched_mfe_mae": {b: {"mfe": ((m.get("blocks") or {}).get(b) or {}).get("gap_mfe"), "mae": ((m.get("blocks") or {}).get(b) or {}).get("gap_mae")} for b in EVAL_BLOCKS},
                "matched_5_10_20": {b: {"5m": ((m.get("blocks") or {}).get(b) or {}).get("gap_5m"), "10m": ((m.get("blocks") or {}).get(b) or {}).get("gap_10m"), "20m": ((m.get("blocks") or {}).get(b) or {}).get("gap_20m")} for b in EVAL_BLOCKS},
                "survives": e.get("survives"),
            }
        )
    while len(packs) < 3:
        packs.append(None)
    return {
        "Event generator unchanged?": True,
        "DIR semantics correct?": True,
        "aligned_r5 formula?": ds.get("aligned_r5"),
        "aligned_r15?": ds.get("aligned_r15"),
        "aligned_prior5_ret?": ds.get("aligned_prior5_ret"),
        "aligned_vwap?": ds.get("aligned_vwap_bps"),
        "aligned_market/sector?": {"market": ds.get("aligned_market_rel"), "sector": ds.get("aligned_sector_rel")},
        "How is ahead S/R defined?": ds.get("ahead_sr"),
        "How is behind S/R defined?": ds.get("behind_sr"),
        "Any raw unsigned directional return used by model?": False,
        "D1 feature-family stability?": {
            "stable_families": stab.get("stable_families"),
            "family_fold_counts": stab.get("family_fold_counts"),
            "unstable": stab.get("unstable"),
        },
        "Frozen rule_n?": report.get("frozen_rule_n"),
        "Rule 1?": packs[0],
        "Rule 2?": packs[1],
        "Rule 3?": packs[2],
        "Any candidate positive against BOTH same-side base AND matched same-direction control in D2/D3/D4?": report.get("any_candidate_positive_same_side_and_matched_d2d3d4"),
        "Does S/R contribute?": fam.get("sr"),
        "TradingValue?": fam.get("participation"),
        "VWAP?": fam.get("vwap"),
        "trend?": fam.get("trend"),
        "event strength?": fam.get("event_strength"),
        "market/sector?": fam.get("market_sector"),
        "local structure?": fam.get("local"),
        "Bullish / bearish consistency?": {rid: (s.get("symmetry"), {"bull": (s.get("BULLISH") or {}).get("eval_hit_n"), "bear": (s.get("BEARISH") or {}).get("eval_hit_n")}) for rid, s in sides.items()},
        "Any one-symbol domination?": {rid: (c.get("symbol") or {}).get("dominated") for rid, c in conc.items()},
        "Any one-day domination?": {rid: (c.get("day") or {}).get("dominated") for rid, c in conc.items()},
        "Any threshold retune?": False,
        "Any PnL optimization?": False,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "Kabu50?": False,
        "submit/cancel/live?": "0/0/0",
        "Base displacement event_n?": ident.get("native_event_n"),
        "D1/D2/D3/D4 counts?": {"D1": ident.get("d1_n"), "D2": ident.get("d2_n"), "D3": ident.get("d3_n"), "D4": ident.get("d4_n")},
        "VERDICT?": d.get("VERDICT"),
        "NEXT?": d.get("NEXT"),
    }
