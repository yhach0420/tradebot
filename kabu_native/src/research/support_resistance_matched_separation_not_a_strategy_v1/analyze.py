"""Transportability and executable-timing report. Not a strategy. No PnL selection."""
from __future__ import annotations

from typing import Any

from research.support_resistance_first_interaction_matched_causal_test_v1.stats import evaluate_question, pair_stats
from research.support_resistance_matched_separation_not_a_strategy_v1 import (
    ANALYSIS_ID,
    CASE_A_ONLY,
    CASE_BIND,
    CASE_BOTH,
    CASE_CONSUMED,
    CASE_C_ONLY,
    CASE_HASH,
    CASE_INFERENCE,
    CASE_NOT_TRANSPORT,
    EVAL_BLOCKS,
    EXPECTED_PRIMARY_FIRST_TEST_N,
    LUNCH_POLICY,
    NEXT_COMPLETE,
    NEXT_HASH,
    NEXT_STOP,
    NEXT_BIND,
    PARENT_VERDICT,
    PLACEBO_RATIO_MIN,
    PRIMARY_METRIC,
)
from research.support_resistance_matched_separation_not_a_strategy_v1.inference import joint_inference
from research.support_resistance_matched_separation_not_a_strategy_v1.matchability import matchability_audit
from research.support_resistance_matched_separation_not_a_strategy_v1.propensity import block_gaps, overlap_weighted
from research.support_resistance_matched_separation_not_a_strategy_v1.walk import walk_executable


def _q(pairs: list[dict[str, Any]], *, pop: str, question: str) -> list[dict[str, Any]]:
    return [r for r in pairs if str(r.get("population") or "") == pop and str(r.get("question") or "") == question]


def _sign_ok(d: dict[str, Any] | None) -> bool:
    vals = [v for v in (d or {}).values() if v is not None]
    if len(vals) < 3:
        return False
    return all(float(v) > 0 for v in vals) or all(float(v) < 0 for v in vals)


def _remap_exec(rows: list[dict[str, Any]], *, prefix: str, matched_key: str, question: str) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if r.get(f"{prefix}p20_before_m20") is None:
            continue
        out.append(
            {
                "question": question,
                "date": r.get("date"),
                "symbol": r.get("symbol"),
                "block": r.get("block"),
                "matched": bool(r.get(matched_key)),
                "tr_p20_before_m20": r.get(f"{prefix}p20_before_m20"),
                "tr_p40_before_m20": r.get(f"{prefix}p40_before_m20"),
                "tr_p80_before_m30": r.get(f"{prefix}p80_before_m30"),
                "tr_mfe_bps": r.get(f"{prefix}mfe_bps"),
                "tr_mae_bps": r.get(f"{prefix}mae_bps"),
                "tr_end_bps": r.get(f"{prefix}end_bps"),
                "ct_p20_before_m20": r.get(f"{prefix}ct_p20_before_m20"),
                "ct_p40_before_m20": r.get(f"{prefix}ct_p40_before_m20"),
                "ct_p80_before_m30": r.get(f"{prefix}ct_p80_before_m30"),
                "ct_mfe_bps": r.get(f"{prefix}ct_mfe_bps"),
                "ct_mae_bps": r.get(f"{prefix}ct_mae_bps"),
                "ct_end_bps": r.get(f"{prefix}ct_end_bps"),
            }
        )
    return out


def exit_architecture() -> dict[str, Any]:
    return {
        "optimized_in_this_phase": False,
        "profit_target_mining": False,
        "ema_vwap_fixed10m_v27_trailing_tested": False,
        "A_support_bounce": {
            "entry_thesis": "support holds",
            "invalidation": "completed loss below support zone",
            "natural_upside_reference": "next pre-known overhead resistance, if one exists",
        },
        "A_resistance_rejection": {
            "entry_thesis": "resistance holds",
            "invalidation": "completed loss above resistance zone",
            "natural_downside_reference": "next pre-known underlying support, if one exists",
        },
        "C_resistance_break_retest_hold": {
            "entry_thesis": "old resistance became support",
            "invalidation": "completed loss back through former resistance zone",
            "natural_objective": "next pre-known resistance, if one exists",
        },
        "C_support_break_retest_hold": {
            "entry_thesis": "old support became resistance",
            "invalidation": "completed loss back through former support zone",
            "natural_objective": "next pre-known support, if one exists",
        },
    }


def decide(
    *,
    bind_ok: bool,
    freeze_ok: bool,
    a: dict[str, Any],
    c: dict[str, Any],
) -> dict[str, Any]:
    if not bind_ok:
        return {"CASE": "BIND", "VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "is_strategy": False}
    if not freeze_ok:
        return {"CASE": "HASH", "VERDICT": CASE_HASH, "NEXT": NEXT_HASH, "is_strategy": False}
    a_ok = bool(a.get("proceed"))
    c_ok = bool(c.get("proceed"))
    if a_ok and c_ok:
        return {"CASE": "BOTH", "VERDICT": CASE_BOTH, "NEXT": NEXT_COMPLETE, "is_strategy": False, "selected_by_effect_size": False}
    if a_ok:
        return {"CASE": "A_ONLY", "VERDICT": CASE_A_ONLY, "NEXT": NEXT_COMPLETE, "is_strategy": False, "selected_by_effect_size": False}
    if c_ok:
        return {"CASE": "C_ONLY", "VERDICT": CASE_C_ONLY, "NEXT": NEXT_COMPLETE, "is_strategy": False, "selected_by_effect_size": False}
    if a.get("inference_fail") and c.get("inference_fail"):
        return {"CASE": "INFERENCE", "VERDICT": CASE_INFERENCE, "NEXT": NEXT_STOP, "is_strategy": False}
    if a.get("transport_fail") and c.get("transport_fail"):
        return {"CASE": "NOT_TRANSPORT", "VERDICT": CASE_NOT_TRANSPORT, "NEXT": NEXT_STOP, "is_strategy": False}
    if a.get("consumed") and c.get("consumed"):
        return {"CASE": "CONSUMED", "VERDICT": CASE_CONSUMED, "NEXT": NEXT_STOP, "is_strategy": False}
    if a.get("inference_fail") or c.get("inference_fail"):
        return {"CASE": "INFERENCE", "VERDICT": CASE_INFERENCE, "NEXT": NEXT_STOP, "is_strategy": False}
    if a.get("transport_fail") or c.get("transport_fail"):
        return {"CASE": "NOT_TRANSPORT", "VERDICT": CASE_NOT_TRANSPORT, "NEXT": NEXT_STOP, "is_strategy": False}
    if a.get("consumed") or c.get("consumed"):
        return {"CASE": "CONSUMED", "VERDICT": CASE_CONSUMED, "NEXT": NEXT_STOP, "is_strategy": False}
    return {"CASE": "NOT_TRANSPORT", "VERDICT": CASE_NOT_TRANSPORT, "NEXT": NEXT_STOP, "is_strategy": False}


def _gate_a(*, matched, joint, placebo_ratio, overlap, matchability, a1_fill, a2, blocks) -> dict[str, Any]:
    sep = bool(matched.get("powered") and matched.get("separation_on_primary_metric"))
    inf = bool((joint.get("pigeonhole") or {}).get("positive") or (joint.get("pigeonhole") or {}).get("excludes_zero") and (joint.get("pigeonhole") or {}).get("p50", 0) > 0)
    holm_p = joint.get("holm_p")
    inf_adj = bool(holm_p is not None and float(holm_p) < 0.05)
    ratio_ok = bool(placebo_ratio is not None and float(placebo_ratio) >= PLACEBO_RATIO_MIN)
    ov_ok = bool((overlap or {}).get("ok") and float((overlap or {}).get("gap_p20_before_m20") or 0) > 0)
    not_only_match = ov_ok
    a2_ok = bool(a2.get("powered") and a2.get("separation_on_primary_metric"))
    exec_ok = bool(a1_fill or a2_ok)
    dir_ok = _sign_ok(blocks)
    inference_fail = not (inf or inf_adj)
    transport_fail = not ov_ok
    consumed = sep and not exec_ok
    proceed = bool(sep and (inf or inf_adj) and ratio_ok and not_only_match and exec_ok and dir_ok)
    return {
        "proceed": proceed,
        "matched_separation": sep,
        "joint_inference": inf or inf_adj,
        "holm_ok": inf_adj,
        "exceeds_placebo": ratio_ok,
        "overlap_agrees": ov_ok,
        "not_only_matchability_artifact": not_only_match,
        "a1_or_a2": exec_ok,
        "a1_fillable": a1_fill,
        "a2_retains": a2_ok,
        "block_stable": dir_ok,
        "inference_fail": inference_fail,
        "transport_fail": transport_fail and not proceed,
        "consumed": consumed and not proceed,
        "matchability_material": bool((matchability or {}).get("MATCHABILITY_SELECTION_IS_MATERIAL")),
    }


def _gate_c(*, matched, joint, placebo, overlap, c1, blocks) -> dict[str, Any]:
    sep = bool(matched.get("powered") and matched.get("separation_on_primary_metric"))
    inf = bool((joint.get("pigeonhole") or {}).get("positive"))
    holm_p = joint.get("holm_p")
    inf_adj = bool(holm_p is not None and float(holm_p) < 0.05)
    plc_null = True
    if placebo:
        gap = (placebo.get("d2_d4") or {}).get("gap_p20_before_m20")
        sep_p = placebo.get("separation_on_primary_metric")
        plc_null = (not sep_p) or (gap is not None and abs(float(gap)) < 0.05)
    ov_ok = bool((overlap or {}).get("ok") and float((overlap or {}).get("gap_p20_before_m20") or 0) > 0)
    c1_ok = bool(c1.get("powered") and c1.get("separation_on_primary_metric"))
    dir_ok = _sign_ok(blocks)
    proceed = bool(sep and (inf or inf_adj) and plc_null and c1_ok and dir_ok)
    return {
        "proceed": proceed,
        "matched_separation": sep,
        "joint_inference": inf or inf_adj,
        "holm_ok": inf_adj,
        "placebo_null_or_smaller": plc_null,
        "overlap_agrees": ov_ok,
        "c1_retains": c1_ok,
        "block_stable": dir_ok,
        "inference_fail": not (inf or inf_adj),
        "transport_fail": (not ov_ok) and not proceed,
        "consumed": sep and (not c1_ok) and not proceed,
    }


def build_report_body(bind: dict[str, Any]) -> dict[str, Any]:
    freeze = dict(bind.get("freeze") or {})
    freeze_ok = bool(freeze.get("detector_hash_match")) and bool(freeze.get("state_machine_hash_match")) and not bool(freeze.get("retuned"))
    if not bind.get("ok"):
        return {"ok": False, "decision": decide(bind_ok=False, freeze_ok=freeze_ok, a={}, c={}), "freeze": freeze}
    walked = walk_executable(bind)
    if not walked.get("ok"):
        return {"ok": False, "reason": walked.get("reason"), "freeze": freeze}
    pairs = list(walked.get("pairs") or [])
    pools = list(walked.get("pools") or [])
    counts = dict(walked.get("counts") or {})
    identity_ok = int(counts.get("primary_first_test_n") or 0) == int(EXPECTED_PRIMARY_FIRST_TEST_N)
    freeze_ok = freeze_ok and identity_ok
    pa = _q(pairs, pop="PRIMARY", question="A")
    pb = _q(pairs, pop="PRIMARY", question="B")
    pc = _q(pairs, pop="PRIMARY", question="C")
    pd = _q(pairs, pop="PRIMARY", question="D")
    pla = _q(pairs, pop="PLACEBO", question="A")
    plc = _q(pairs, pop="PLACEBO", question="C")
    qa = evaluate_question(pa, question="A")
    qb = evaluate_question(pb, question="B")
    qc = evaluate_question(pc, question="C")
    qd = evaluate_question(pd, question="D")
    qpla = evaluate_question(pla, question="A")
    qplc = evaluate_question(plc, question="C")
    a2_src = [r for r in pa if r.get("a2_confirmed")]
    c1_src = [r for r in pc if r.get("c1_confirmed")]
    qa2 = evaluate_question(_remap_exec(a2_src, prefix="a2_", matched_key="a2_matched", question="A2"), question="A2")
    qc1 = evaluate_question(_remap_exec(c1_src, prefix="c1_", matched_key="c1_matched", question="C1"), question="C1")
    ma = matchability_audit(pa, question="A")
    mc = matchability_audit(pc, question="C")
    ova = overlap_weighted(pa, pools, question="A")
    ovc = overlap_weighted(pc, pools, question="C")
    joint = joint_inference({"A": pa, "B": pb, "C": pc, "D": pd})
    holm = dict((joint.get("multiple_testing") or {}).get("holm") or {})
    real_a = (qa.get("bootstrap_date") or {}).get("p50")
    plc_a = (qpla.get("bootstrap_date") or {}).get("p50")
    ratio = (float(real_a) / float(plc_a)) if real_a is not None and plc_a not in (None, 0) else None
    a1_fill_n = sum(1 for r in pa if str(r.get("block") or "") in EVAL_BLOCKS and r.get("a1_fillable_approx"))
    a1_n = sum(1 for r in pa if str(r.get("block") or "") in EVAL_BLOCKS)
    a1_fill_rate = (a1_fill_n / a1_n) if a1_n else None
    a_blocks = block_gaps(pa, question="A", matched_only=True)
    c_blocks = block_gaps(pc, question="C", matched_only=True)
    a2_blocks = block_gaps(_remap_exec(a2_src, prefix="a2_", matched_key="a2_matched", question="A2"), question="A2", matched_only=True)
    c1_blocks = block_gaps(_remap_exec(c1_src, prefix="c1_", matched_key="c1_matched", question="C1"), question="C1", matched_only=True)
    consumed = None
    if (qa.get("d2_d4") or {}).get("gap_p20_before_m20") and (qa2.get("d2_d4") or {}).get("gap_p20_before_m20") is not None:
        g0 = float((qa.get("d2_d4") or {}).get("gap_p20_before_m20"))
        g1 = float((qa2.get("d2_d4") or {}).get("gap_p20_before_m20"))
        consumed = None if g0 == 0 else 1.0 - (g1 / g0)
    gate_a = _gate_a(
        matched=qa,
        joint={"pigeonhole": ((joint.get("by_question") or {}).get("A") or {}).get("pigeonhole"), "holm_p": holm.get("A")},
        placebo_ratio=ratio,
        overlap=ova,
        matchability=ma,
        a1_fill=bool(a1_fill_rate is not None and a1_fill_rate >= 0.5),
        a2=qa2,
        blocks=a_blocks,
    )
    gate_c = _gate_c(
        matched=qc,
        joint={"pigeonhole": ((joint.get("by_question") or {}).get("C") or {}).get("pigeonhole"), "holm_p": holm.get("C")},
        placebo=qplc,
        overlap=ovc,
        c1=qc1,
        blocks=c_blocks,
    )
    res_a = evaluate_question([r for r in pa if r.get("as_resistance")], question="A_res")
    sup_a = evaluate_question([r for r in pa if not r.get("as_resistance")], question="A_sup")
    res_c = evaluate_question([r for r in pc if r.get("as_resistance")], question="C_res")
    sup_c = evaluate_question([r for r in pc if not r.get("as_resistance")], question="C_sup")
    decision = decide(bind_ok=True, freeze_ok=freeze_ok, a=gate_a, c=gate_c)
    same_bar = int(walked.get("same_bar_entry_n") or 0)
    n_pairs = len(pairs)
    step = max(1, n_pairs // 2500) if n_pairs else 1
    return {
        "ok": True,
        "analysis_id": ANALYSIS_ID,
        "parent_verdict": PARENT_VERDICT,
        "lunch_policy": LUNCH_POLICY,
        "primary_metric": PRIMARY_METRIC,
        "detector_retuned": False,
        "is_strategy": False,
        "b_d_promoted": False,
        "freeze": freeze,
        "counts": counts,
        "identity_ok": identity_ok,
        "questions_primary": {"A": qa, "B": qb, "C": qc, "D": qd},
        "questions_placebo": {"A": qpla, "C": qplc},
        "matchability_A": ma,
        "matchability_C": mc,
        "overlap_A": ova,
        "overlap_C": ovc,
        "inference": joint,
        "placebo_calibration": {
            "A_REAL_EFFECT": real_a,
            "A_PLACEBO_RESIDUAL": plc_a,
            "A_REAL_TO_PLACEBO_RATIO": ratio,
            "C_REAL_EFFECT": (qc.get("bootstrap_date") or {}).get("p50"),
            "C_PLACEBO_RESIDUAL": (qplc.get("bootstrap_date") or {}).get("p50"),
            "did_not_subtract_mechanically": True,
        },
        "A1": {
            "classification": "HISTORICAL_PASSIVE_EXECUTION_APPROXIMATION",
            "actual_fill_proven": False,
            "fillable_approx_rate": a1_fill_rate,
            "fillable_n": a1_fill_n,
            "n": a1_n,
            "same_bar_placement": False,
        },
        "A2": {
            "event": qa2,
            "confirmed_n": len(a2_src),
            "event_loss_break_n": sum(1 for r in pa if r.get("a2_event_loss") == "BREAK"),
            "event_loss_unresolved_n": sum(1 for r in pa if r.get("a2_event_loss") == "UNRESOLVED"),
            "edge_consumed_fraction": consumed,
            "A_FIRST_TEST_EFFECT": (qa.get("d2_d4") or {}).get("gap_p20_before_m20"),
            "A_CONFIRMED_ENTRY_EFFECT": (qa2.get("d2_d4") or {}).get("gap_p20_before_m20"),
        },
        "C1": {"event": qc1, "n": len(c1_src)},
        "blocks": {
            "A_matched": a_blocks,
            "A_overlap_weighted": (ova or {}).get("block_gap_p20"),
            "A2_executable": a2_blocks,
            "C_matched": c_blocks,
            "C_overlap_weighted": (ovc or {}).get("block_gap_p20"),
            "C1_executable": c1_blocks,
        },
        "direction_split": {"A_resistance": res_a, "A_support": sup_a, "C_resistance": res_c, "C_support": sup_c},
        "exit_architecture": exit_architecture(),
        "gate_A": gate_a,
        "gate_C": gate_c,
        "same_bar_entry_n": same_bar,
        "decision": decision,
        "n_days": walked.get("n_days"),
        "n_symbols_loaded": walked.get("n_symbols_loaded"),
        "forbidden_loaded": False,
        "pairs_compact": [
            {
                "question": r.get("question"),
                "population": r.get("population"),
                "symbol": r.get("symbol"),
                "date": r.get("date"),
                "block": r.get("block"),
                "matched": r.get("matched"),
                "as_resistance": r.get("as_resistance"),
                "tr_p20_before_m20": r.get("tr_p20_before_m20"),
                "ct_p20_before_m20": r.get("ct_p20_before_m20"),
                "a2_p20_before_m20": r.get("a2_p20_before_m20"),
                "c1_p20_before_m20": r.get("c1_p20_before_m20"),
            }
            for r in pairs[::step][:2500]
        ],
        "all_pairs": pairs,
        "all_pools_n": len(pools),
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    ma = dict(report.get("matchability_A") or {})
    mc = dict(report.get("matchability_C") or {})
    ova = dict(report.get("overlap_A") or {})
    ovc = dict(report.get("overlap_C") or {})
    inf = dict(report.get("inference") or {})
    byq = dict(inf.get("by_question") or {})
    holm = dict((inf.get("multiple_testing") or {}).get("holm") or {})
    cal = dict(report.get("placebo_calibration") or {})
    a2 = dict(report.get("A2") or {})
    c1 = dict(report.get("C1") or {})
    a1 = dict(report.get("A1") or {})
    blocks = dict(report.get("blocks") or {})
    split = dict(report.get("direction_split") or {})
    return {
        "Is A matchability selection material?": ma.get("MATCHABILITY_SELECTION_IS_MATERIAL"),
        "Is C matchability selection material?": mc.get("MATCHABILITY_SELECTION_IS_MATERIAL"),
        "Does overlap-weighted full common-support analysis agree with matched pairs?": {
            "A_matched_gap": ((report.get("questions_primary") or {}).get("A") or {}).get("d2_d4", {}).get("gap_p20_before_m20"),
            "A_overlap_gap": ova.get("gap_p20_before_m20"),
            "C_matched_gap": ((report.get("questions_primary") or {}).get("C") or {}).get("d2_d4", {}).get("gap_p20_before_m20"),
            "C_overlap_gap": ovc.get("gap_p20_before_m20"),
            "A_agree_sign": bool(
                ((report.get("questions_primary") or {}).get("A") or {}).get("d2_d4", {}).get("gap_p20_before_m20")
                and ova.get("gap_p20_before_m20")
                and float(((report.get("questions_primary") or {}).get("A") or {}).get("d2_d4", {}).get("gap_p20_before_m20")) * float(ova.get("gap_p20_before_m20")) > 0
            ),
            "C_agree_sign": bool(
                ((report.get("questions_primary") or {}).get("C") or {}).get("d2_d4", {}).get("gap_p20_before_m20")
                and ovc.get("gap_p20_before_m20")
                and float(((report.get("questions_primary") or {}).get("C") or {}).get("d2_d4", {}).get("gap_p20_before_m20")) * float(ovc.get("gap_p20_before_m20")) > 0
            ),
        },
        "Effective sample size?": {"A": ova.get("effective_sample_size"), "C": ovc.get("effective_sample_size")},
        "Any extreme weights?": {"A": ova.get("extreme_weights"), "C": ovc.get("extreme_weights")},
        "Does true two-way clustered inference still support A?": ((byq.get("A") or {}).get("pigeonhole") or {}).get("positive"),
        "C?": ((byq.get("C") or {}).get("pigeonhole") or {}).get("positive"),
        "After multiple-testing adjustment?": {"A_holm_p": holm.get("A"), "C_holm_p": holm.get("C"), "B_holm_p": holm.get("B"), "D_holm_p": holm.get("D")},
        "A real/placebo magnitude ratio?": cal.get("A_REAL_TO_PLACEBO_RATIO"),
        "Does A2 retain separation after waiting for confirmed rejection?": ((a2.get("event") or {}).get("separation_on_primary_metric")),
        "How much A edge is consumed before executable entry?": a2.get("edge_consumed_fraction"),
        "Does C1 retain separation from next executable bar?": ((c1.get("event") or {}).get("separation_on_primary_metric")),
        "A1 passive approximation fillability?": a1.get("fillable_approx_rate"),
        "Any same-bar entry?": report.get("same_bar_entry_n"),
        "D2 / D3 / D4 same direction?": {
            "A_matched": blocks.get("A_matched"),
            "A_overlap_weighted": blocks.get("A_overlap_weighted"),
            "A2_executable": blocks.get("A2_executable"),
            "C_matched": blocks.get("C_matched"),
            "C_overlap_weighted": blocks.get("C_overlap_weighted"),
            "C1_executable": blocks.get("C1_executable"),
        },
        "Support and resistance same?": {
            "A_res_sep": (split.get("A_resistance") or {}).get("separation_on_primary_metric"),
            "A_sup_sep": (split.get("A_support") or {}).get("separation_on_primary_metric"),
            "C_res_sep": (split.get("C_resistance") or {}).get("separation_on_primary_metric"),
            "C_sup_sep": (split.get("C_support") or {}).get("separation_on_primary_metric"),
        },
        "Any PnL optimization?": False,
        "Any strategy selected?": False,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "submit/cancel/live?": "0/0/0",
        "VERDICT?": d.get("VERDICT"),
        "NEXT?": d.get("NEXT"),
        "gate_A": report.get("gate_A"),
        "gate_C": report.get("gate_C"),
    }
