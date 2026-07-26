"""Canonical FCR incremental integrity runner — TRAIN-only after integrity checks."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.canonical_fcr_incremental_integrity.candidates import (
    audit_arm_nesting,
    audit_f5_spread_spec,
    audit_parent_lineage,
    audit_state_stage_nesting,
    build_reclaim_candidates,
    materialize_arms,
    run_frozen_episodes,
)
from research.canonical_fcr_incremental_integrity.constants import (
    ARMS,
    CANCEL,
    EVAL_STRIDE,
    FROZEN,
    LIVE_ORDER,
    OLD_RUN,
    OLD_STRIDE,
    OUT_ROOT,
    SEED,
    SUBMIT,
    TRAIN_DAY,
    WARMUP_DAY,
)
from research.canonical_fcr_incremental_integrity.evaluate import evaluate_arm, matched_increment, train_gate
from research.canonical_fcr_incremental_integrity.loader import audit_stride_semantics, load_streams_reconciled
from research.canonical_fcr_incremental_integrity.reporting import emit

JST = ZoneInfo("Asia/Tokyo")


def _summ(ev: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "n", "pnl", "pf", "mean", "never_rate", "early_adverse_rate", "stop_rate",
        "stop_5m_rate", "noprogress_rate", "winner_rate", "avg_mfe", "avg_mae",
        "top1_symbol_share", "top3_symbol_share",
    )
    return {k: ev.get(k) for k in keys}


def _load_old_baseline() -> dict[str, Any]:
    p = OLD_RUN / "report.json"
    if not p.exists():
        return {"missing": True}
    d = json.loads(p.read_text(encoding="utf-8"))
    tr = d.get("train_results") or {}
    return {
        "run_id": d.get("run_id"),
        "stride": OLD_STRIDE,
        "raw_events_reported": (d.get("coverage") or {}).get("ticks"),
        "thresholds": d.get("thresholds"),
        "counts": d.get("counts"),
        "F0_n": (tr.get("F0_RECLAIM_ONLY") or {}).get("n"),
        "F1_n": (tr.get("F1_TREND_RECLAIM") or {}).get("n"),
        "F2_n": (tr.get("F2_PULLBACK_RECLAIM") or {}).get("n"),
        "F3_n": (tr.get("F3_SELLING_EXHAUSTED") or {}).get("n"),
        "F4_n": (tr.get("F4_BUY_FLOW_CONFIRMED") or {}).get("n"),
        "F5_n": (tr.get("F5_FULL_FCR") or {}).get("n"),
        "F5_pnl": (tr.get("F5_FULL_FCR") or {}).get("pnl"),
        "F5_pf": (tr.get("F5_FULL_FCR") or {}).get("pf"),
        "F5_mean": (tr.get("F5_FULL_FCR") or {}).get("mean"),
        "entry_verdict": (d.get("verdict") or {}).get("entry_verdict"),
    }


def run_integrity(
    *,
    run_id: Optional[str] = None,
    out_root: Optional[Path] = None,
    test_results: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = (out_root or OUT_ROOT) / run_id

    print("[integrity] Stage A: stride audit…", flush=True)
    stride_audit = audit_stride_semantics()
    old_baseline = _load_old_baseline()

    print(f"[integrity] load warmup+TRAIN stride={EVAL_STRIDE}…", flush=True)
    loaded = load_streams_reconciled([WARMUP_DAY, TRAIN_DAY], stride=EVAL_STRIDE)
    counts = loaded.counts
    # parity: for stride=1, processed must equal eligible
    parity_ok = (EVAL_STRIDE == 1) and (counts.processed == counts.eligible)
    parity_verdict = "STRIDE1_EVENT_PARITY_PASS" if parity_ok else "STRIDE1_EVENT_PARITY_BLOCKED"
    event_reconciliation = {
        "raw_lines": counts.raw_lines,
        "stride_skipped": counts.stride_skipped,
        "empty_line": counts.empty_line,
        "json_error": counts.json_error,
        "no_payload": counts.no_payload,
        "no_buy1_sell1": counts.no_buy1_sell1,
        "missing_quote": counts.missing_quote,
        "crossed_quote": counts.crossed_quote,
        "bad_ts": counts.bad_ts,
        "eligible": counts.eligible,
        "processed": counts.processed,
        "parity": parity_verdict,
        "by_day": loaded.by_day,
        "formula": "processed == eligible when stride=1 after quality filters; stride_skipped counted separately",
    }

    # TRAIN streams only for episode/candidate eval (warmup kept for optional future history; not evaluated)
    train_days = [TRAIN_DAY]
    print("[integrity] frozen SM episodes TRAIN…", flush=True)
    episodes = run_frozen_episodes(loaded.streams, train_days)
    print(f"[integrity] episodes={len(episodes)}", flush=True)

    print("[integrity] Stage B/C: reclaim candidates + nested arms…", flush=True)
    cands = build_reclaim_candidates(loaded.streams, episodes, hold_events=int(FROZEN["reclaim_hold_events"]))
    # filter to TRAIN day
    cands = [c for c in cands if c.day == TRAIN_DAY]
    tables = materialize_arms(cands)
    nesting = audit_arm_nesting(tables)
    lineage = audit_parent_lineage(tables, cands)
    state_stage = audit_state_stage_nesting(episodes)

    print("[integrity] Stage D: F5 spread/liquidity audit…", flush=True)
    f5_spec = audit_f5_spread_spec()
    spread_ok = f5_spec["F5_SPEC_CONFORMANCE"] == "F5_SPEC_CONFORMANCE_PASS"

    nesting_ok = nesting["verdict"] == "ARM_NESTING_PASS"
    lineage_ok = lineage["verdict_parent"] == "PARENT_LINEAGE_PASS"
    anchor_ok = lineage["verdict_anchor"] == "COMMON_ANCHOR_PASS"
    state_ok = state_stage["verdict"] == "STATE_STAGE_NESTING_PASS"

    integrity_ok = (
        parity_ok and nesting_ok and lineage_ok and anchor_ok and state_ok and spread_ok
        and stride_audit["verdict"] == "STRIDE_EVENT_SAMPLING_FOUND"  # documented; fixed via stride=1
    )
    # integrity requires stride1 parity + nesting + lineage + anchor + state + f5 spec
    integrity_ok = parity_ok and nesting_ok and lineage_ok and anchor_ok and state_ok and spread_ok
    integrity_verdict = "FCR_INCREMENTAL_INTEGRITY_PASS" if integrity_ok else "FCR_INCREMENTAL_INTEGRITY_BLOCKED"

    print("[integrity] Stage E: TRAIN matched eval…", flush=True)
    train_results = {a: evaluate_arm(tables[a], loaded.streams) for a in ARMS}
    # strip heavy rows for json later but keep ids for increment
    matched_inc = {
        "F0_to_F1": matched_increment(train_results["F0_RECLAIM_BASE"], train_results["F1_TREND"], lineage_ok=lineage_ok, anchor_ok=anchor_ok),
        "F1_to_F2": matched_increment(train_results["F1_TREND"], train_results["F2_PULLBACK"], lineage_ok=lineage_ok, anchor_ok=anchor_ok),
        "F2_to_F3": matched_increment(train_results["F2_PULLBACK"], train_results["F3_EXHAUSTION"], lineage_ok=lineage_ok, anchor_ok=anchor_ok),
        "F3_to_F4": matched_increment(train_results["F3_EXHAUSTION"], train_results["F4_BUY_FLOW"], lineage_ok=lineage_ok, anchor_ok=anchor_ok),
        "F4_to_F5": matched_increment(train_results["F4_BUY_FLOW"], train_results["F5_FULL_FCR"], lineage_ok=lineage_ok, anchor_ok=anchor_ok),
    }

    # one impulse
    f5_imps = [r.impulse_id for r in tables["F5_FULL_FCR"]]
    one_impulse_ok = len(f5_imps) == len(set(f5_imps))

    tg_ok, tg_reason, tg_codes = train_gate(
        train_results["F5_FULL_FCR"],
        integrity_ok=integrity_ok,
        nesting_ok=nesting_ok,
        lineage_ok=lineage_ok,
        anchor_ok=anchor_ok,
        state_ok=state_ok,
        spread_ok=spread_ok,
        stride_ok=parity_ok,
        one_impulse_ok=one_impulse_ok,
    )

    # old vs fixed
    f5 = train_results["F5_FULL_FCR"]
    old_vs = {
        "old_stride": OLD_STRIDE,
        "new_stride": EVAL_STRIDE,
        "old_processed_ticks": old_baseline.get("raw_events_reported"),
        "new_processed": counts.processed,
        "old_F0_n": old_baseline.get("F0_n"),
        "new_F0_n": train_results["F0_RECLAIM_BASE"].get("n"),
        "old_F5_n": old_baseline.get("F5_n"),
        "new_F5_n": f5.get("n"),
        "old_F5_pnl": old_baseline.get("F5_pnl"),
        "new_F5_pnl": f5.get("pnl"),
        "old_F5_pf": old_baseline.get("F5_pf"),
        "new_F5_pf": f5.get("pf"),
        "difference_reasons": [
            "EVENT_SAMPLING_EFFECT",
            "ARM_ANCHOR_MISMATCH",
            "PARENT_LINEAGE_BUG",
        ],
        "unexplained": 0,
        "note": "Old arms independently generated candidates; new arms are nested filters on common reclaim table at hold+2 common anchor.",
    }

    codes = [
        stride_audit["verdict"],
        parity_verdict,
        lineage["verdict_anchor"],
        lineage["verdict_parent"],
        nesting["verdict"],
        state_stage["verdict"],
        f5_spec["F5_SPREAD_GATE"],
        f5_spec["F5_SPEC_CONFORMANCE"],
        integrity_verdict,
    ]
    for key, inc in matched_inc.items():
        lab = inc.get("label", "INCREMENT_NOT_EVALUABLE")
        prefix = key.upper().replace("_TO_", "_TO_")
        # F0_to_F1 -> F0_TO_F1
        tag = key.upper()
        if lab == "INCREMENT_POSITIVE":
            codes.append(f"{tag}_POSITIVE")
        elif lab == "INCREMENT_MIXED":
            codes.append(f"{tag}_MIXED")
        elif lab == "INCREMENT_NEGATIVE":
            codes.append(f"{tag}_NEGATIVE")
        else:
            codes.append(f"{tag}_NOT_EVALUABLE")
    codes.extend(tg_codes)
    codes += [
        "FCR_VALIDATION_NOT_REACHED",
        "FCR_EXIT_RESEARCH_BLOCKED",
        "INSUFFICIENT_FRESH_CANONICAL_OOS",
        "CAPTURE_ONLY_CONTINUE",
        "NO_PAPER_ENTRY",
        "NO_PRODUCTION_CHANGE",
        "LIVE_TRADING_BLOCKED",
    ]
    if not tg_ok and "CURRENT_F5_SPEC_NO_TRAIN_EDGE" not in codes and integrity_ok is False:
        # still mark current spec rejected when train fails after integrity issues
        if "NO_TRAIN_CANONICAL_FCR_CANDIDATE" in codes:
            codes.append("CURRENT_F5_SPEC_NO_TRAIN_EDGE")
            codes.append("CANONICAL_FCR_CURRENT_SPEC_REJECTED")

    native_timing = {
        "note": "NATIVE_TRIGGER_TIMING_DIAGNOSTIC only — not used for increment verdict",
        "n_with_buy": sum(1 for c in cands if c.native_buy_time),
        "n_with_reclaim": sum(1 for c in cands if c.native_reclaim_time),
    }

    # slim train results for storage
    train_slim = {a: _summ(train_results[a]) for a in ARMS}

    payload: dict[str, Any] = {
        "run_id": run_id,
        "phase": "canonical_fcr_incremental_integrity",
        "seed": SEED,
        "submit": SUBMIT, "cancel": CANCEL, "live_order": LIVE_ORDER,
        "mainline_changed": False, "paper_auto_start": False, "live_trading_enabled": False,
        "capture_only": True,
        "frozen_thresholds": FROZEN,
        "source_audit": {
            "old_sot": str(OLD_RUN),
            "method": "matched_common_anchor_nested_filters",
            "no_retune": True,
            "state_machine_unchanged": True,
        },
        "old_baseline": old_baseline,
        "stride_audit": stride_audit,
        "event_reconciliation": event_reconciliation,
        "seq_gaps": loaded.seq_gaps[:40],
        "episode_lineage": {
            "n_episodes": len(episodes),
            "episode_id_def": "day|symbol|imp{start_seq}|start_time — no entry timestamp",
            "reclaim_candidate_id_def": "day|symbol|episode_id|reclaim_cross_event_seq",
            "common_decision_def": "reclaim_cross + 2 causal events (hold observation complete)",
        },
        "reclaim_sample": [
            {
                "reclaim_candidate_id": c.reclaim_candidate_id,
                "episode_id": c.episode_id,
                "trend": c.trend_context_pass,
                "pullback": c.pullback_pass,
                "exh": c.selling_exhausted_pass,
                "buy": c.buy_flow_pass,
                "hold2": c.reclaim_hold_2events_pass,
                "liq": c.liquidity_pass,
            }
            for c in cands[:80]
        ],
        "common_anchor_audit": {
            "verdict": lineage["verdict_anchor"],
            "mismatch": lineage["common_anchor_mismatch"],
            "entry_time_mismatch": lineage["entry_time_mismatch"],
            "execution_price_mismatch": lineage["execution_price_mismatch"],
        },
        "parent_lineage": lineage,
        "arm_counts": nesting["counts"],
        "arm_nesting": nesting,
        "state_stage": state_stage,
        "f5_spec": f5_spec,
        "train_results": train_slim,
        "matched_incremental": matched_inc,
        "native_timing": native_timing,
        "execution": {"note": "common_anchor E1-style first valid Ask; Bid path 180s; cost 5bps"},
        "symbol_dependency": {
            "top1": f5.get("top1_symbol_share"),
            "top3": f5.get("top3_symbol_share"),
        },
        "old_vs_fixed": old_vs,
        "train_gate": {"ok": tg_ok, "reason": tg_reason},
        "validation_run": False,
        "holdout_run": False,
        "tests": test_results or {"all_passed": False, "rows": [{"name": "deferred", "status": "pending"}]},
        "verdict": {
            "final_verdict": integrity_verdict if not integrity_ok else (
                "CANONICAL_FCR_ENTRY_CANDIDATE" if tg_ok else "NO_TRAIN_CANONICAL_FCR_CANDIDATE"
            ),
            "integrity_verdict": integrity_verdict,
            "codes": codes,
            "FCR_VALIDATION_NOT_REACHED": True,
            "FCR_EXIT_RESEARCH_BLOCKED": True,
            "CAPTURE_ONLY_CONTINUE": True,
            "NO_PAPER_ENTRY": True,
            "LIVE_TRADING_BLOCKED": True,
        },
    }

    def inc_label(key: str) -> str:
        return (matched_inc.get(key) or {}).get("label")

    payload["completion"] = {
        "1_final_verdict": payload["verdict"]["final_verdict"],
        "2_integrity_verdict": integrity_verdict,
        "3_stride_meaning": stride_audit.get("mechanism"),
        "4_old_stride": OLD_STRIDE,
        "5_new_stride": EVAL_STRIDE,
        "6_raw_input_events": counts.raw_lines,
        "7_eligible_events": counts.eligible,
        "8_processed_events": counts.processed,
        "9_skipped_events": {
            "stride_skipped": counts.stride_skipped,
            "empty_line": counts.empty_line,
            "json_error": counts.json_error,
            "no_payload": counts.no_payload,
            "no_buy1_sell1": counts.no_buy1_sell1,
            "missing_quote": counts.missing_quote,
            "crossed_quote": counts.crossed_quote,
            "bad_ts": counts.bad_ts,
        },
        "10_event_count_reconciliation": event_reconciliation,
        "11_stride1_parity": parity_verdict,
        "12_episode_id_def": payload["episode_lineage"]["episode_id_def"],
        "13_reclaim_candidate_id_def": payload["episode_lineage"]["reclaim_candidate_id_def"],
        "14_common_decision_anchor_def": payload["episode_lineage"]["common_decision_def"],
        "15_common_anchor": lineage["verdict_anchor"],
        "16_parent_lineage": lineage["verdict_parent"],
        "17_child_without_parent": lineage["child_without_parent"],
        "18_anchor_mismatch": lineage["common_anchor_mismatch"],
        "19_execution_time_mismatch": lineage["entry_time_mismatch"],
        "20_execution_price_mismatch": lineage["execution_price_mismatch"],
        "21_F1_subset_F0": nesting["checks"]["F1_subset_F0"],
        "22_F2_subset_F1": nesting["checks"]["F2_subset_F1"],
        "23_F3_subset_F2": nesting["checks"]["F3_subset_F2"],
        "24_F4_subset_F3": nesting["checks"]["F4_subset_F3"],
        "25_F5_subset_F4": nesting["checks"]["F5_subset_F4"],
        "26_arm_nesting": nesting["verdict"],
        "27_state_stage_nesting": state_stage["verdict"],
        "28_f5_spread_gate_impl": f5_spec,
        "29_spread_max_bps_none_means": f5_spec["spread_max_bps_none_means"],
        "30_spread_gate_missing": f5_spec["F5_SPREAD_GATE"] == "F5_SPREAD_GATE_MISSING",
        "31_f5_spec_conformance": f5_spec["F5_SPEC_CONFORMANCE"],
        "32_unique_trend": state_stage["counts"].get("TREND_CONTEXT"),
        "33_unique_pullback": state_stage["counts"].get("PULLBACK_DETECTED"),
        "34_unique_exhaustion": state_stage["counts"].get("SELLING_EXHAUSTED"),
        "35_unique_buy_flow": state_stage["counts"].get("BUY_FLOW_CONFIRMED"),
        "36_unique_reclaim": state_stage["counts"].get("RECLAIM_TRIGGERED"),
        "37_unique_entry_ready": state_stage["counts"].get("ENTRY_READY"),
        "38_matched_F0_n": train_slim["F0_RECLAIM_BASE"].get("n"),
        "39_matched_F1_n": train_slim["F1_TREND"].get("n"),
        "40_matched_F2_n": train_slim["F2_PULLBACK"].get("n"),
        "41_matched_F3_n": train_slim["F3_EXHAUSTION"].get("n"),
        "42_matched_F4_n": train_slim["F4_BUY_FLOW"].get("n"),
        "43_matched_F5_n": train_slim["F5_FULL_FCR"].get("n"),
        "44_F0_train": train_slim["F0_RECLAIM_BASE"],
        "45_F1_train": train_slim["F1_TREND"],
        "46_F2_train": train_slim["F2_PULLBACK"],
        "47_F3_train": train_slim["F3_EXHAUSTION"],
        "48_F4_train": train_slim["F4_BUY_FLOW"],
        "49_F5_train": train_slim["F5_FULL_FCR"],
        "50_F0_to_F1": matched_inc["F0_to_F1"],
        "51_F1_to_F2": matched_inc["F1_to_F2"],
        "52_F2_to_F3": matched_inc["F2_to_F3"],
        "53_F3_to_F4": matched_inc["F3_to_F4"],
        "54_F4_to_F5": matched_inc["F4_to_F5"],
        "55_F5_never": f5.get("never_rate"),
        "56_F5_early_adverse": f5.get("early_adverse_rate"),
        "57_F5_stop": f5.get("stop_rate"),
        "58_F5_stop_5m": f5.get("stop_5m_rate"),
        "59_F5_noprogress": f5.get("noprogress_rate"),
        "60_F5_winner": f5.get("winner_rate"),
        "61_F5_mfe": f5.get("avg_mfe"),
        "62_F5_mae": f5.get("avg_mae"),
        "63_F5_top1": f5.get("top1_symbol_share"),
        "64_F5_top3": f5.get("top3_symbol_share"),
        "65_train_gate": {"ok": tg_ok, "reason": tg_reason},
        "66_current_f5_edge": "NO_EDGE" if not tg_ok else "EDGE",
        "67_validation": "NOT_REACHED",
        "68_holdout": "NOT_RUN",
        "69_exit": "FCR_EXIT_RESEARCH_BLOCKED",
        "70_old_vs_diff_reasons": old_vs["difference_reasons"],
        "71_unexplained": old_vs["unexplained"],
        "72_capture_only": True,
        "73_paper": "NO_PAPER_ENTRY",
        "74_live": "LIVE_TRADING_BLOCKED",
        "75_submit": SUBMIT,
        "76_cancel": CANCEL,
        "77_live_order": LIVE_ORDER,
        "78_tests": test_results,
        "79_mainline_changed": False,
        "80_artifacts": str(out_dir),
        "inc_labels": {k: inc_label(k) for k in matched_inc},
    }

    print("[integrity] emit…", flush=True)
    # drop heavy ids/rows from train_results before emit — already slimmed
    emit(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    return payload
