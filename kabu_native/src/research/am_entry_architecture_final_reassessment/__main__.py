"""Offline CURRENT-preserving B0/B1 dual-confirmation. No Runtime write. No Paper."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_current_utility_augment.analyze import overlay_gate, preservation_audit
from research.am_current_utility_augment.ensemble import attach_ensemble
from research.am_current_utility_augment.overlay import overlay_replay
from research.am_entry_architecture_final_reassessment import (
    A3_REUSE_ARM,
    A5_REUSE_ARM,
    ANALYSIS_ID,
    ARM_IDS,
    AUGMENT_MAX_PER_COHORT,
    B0,
    B1,
    C0,
    C1,
    C2,
    CONSENSUS_ARM_N,
    CURRENT_PRIORITY,
    FROZEN_ARM,
)
from research.am_entry_architecture_final_reassessment.analyze import (
    decide_case,
    expected_match,
    increment_vs,
    selection_change_diagnostic,
)
from research.am_entry_architecture_final_reassessment.consensus import (
    cohort_decisions,
    merge_model_fields,
    tag_consensus,
)
from research.am_entry_architecture_final_reassessment.precommit import precommit_spec, print_precommit, spec_sha256
from research.am_entry_architecture_final_reassessment.publish import OUT, build_markdown, kv_rows, write_artifacts
from research.am_entry_fixed_spec_oof import (
    V2_CURRENT_MAX_DD,
    V2_CURRENT_NET_PNL,
    V2_CURRENT_PF,
    V2_CURRENT_TRADE_N,
)
from research.am_entry_fixed_spec_oof.oof import evaluate_policy
from research.am_entry_information_expansion import AVAILABLE_REP_MIN, CLASS_LOSS, CLASS_NEUTRAL, CLASS_WIN, TARGET, X14_BUNDLE
from research.am_entry_profit_improvement import (
    C14_CHANGED,
    C14_ID,
    CANCEL_N,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    LIVE_ORDER_N,
    NEW_FEATURE_N,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    REPRESENTATION_N,
    RUNTIME_CHANGED,
    SESSION,
    SUBMIT_N,
    TRUE_OOS,
    W5_RUNTIME_ADOPTED,
    WAIT_SEARCH_N,
)
from research.am_entry_profit_improvement.analyze import freeze_parity, independent_top3
from research.am_entry_profit_improvement.metrics import _pf_num, economic_pack, paired_delta
from research.am_entry_temporal_regime_information.analyze import pick_best
from research.am_entry_temporal_regime_information.oof import arm_spec_grid, process_oof_scores
from research.am_expanded_entry_risk_integration.analyze import profit_concentration
from research.am_expanded_entry_risk_integration.ensemble import joint_map
from research.am_raw_event_incremental_selection import B0_EXPECTED, B1_EXPECTED
from research.am_utility_augment_execution_risk.analyze import preservation_pass
from research.canonical_entry_performance_rebase.analyze import session_of
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.passive_wait_policy_reassessment.analyze import _close
from research.wait5_session_target_learnability import POS_REP_MIN
from small_paper.v1r_native_entry_live import FEATURE_ORDER
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
LABELED_X14 = NATIVE / "results" / "research" / "_work_cache" / "am_entry_information_expansion" / "labeled_am_x14.json"
REGIME_LABELED = (
    NATIVE / "results" / "research" / "_work_cache" / "am_entry_temporal_regime_information" / "labeled_am_regime.json"
)
REGIME_PROBA = NATIVE / "results" / "research" / "_work_cache" / "am_entry_temporal_regime_information"

INTEGRITY_ZERO_KEYS = (
    "OUTER_HELDOUT_FIT_LEAK_N",
    "FUTURE_FEATURE_USE_N",
    "FUTURE_EVENT_USE_N",
    "TARGET_CONTAMINATION_N",
    "PM_ROWS_USED_N",
    "NEW_MODEL_N",
    "NEW_FEATURE_N",
    "NEW_TARGET_N",
    "MODEL_SEARCH_N",
    "HYPERPARAMETER_SEARCH_N",
    "SCORE_BLEND_N",
    "SCORE_THRESHOLD_SEARCH_N",
    "PROBABILITY_THRESHOLD_SEARCH_N",
    "REPRESENTATION_SELECTION_N",
    "WAIT_SEARCH_N",
    "EXIT_CHANGE_N",
    "CURRENT_POLICY_CHANGE_N",
    "ORACLE_SELECTION_USE_N",
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _slim(pack: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pack.items() if k not in ("trades", "daily")}


def _arm_fill_rate(admitted: int, fill_n: int) -> float | None:
    return (float(fill_n) / float(admitted)) if admitted else None


def _empty_required(*, verdict: str, nxt: str, base_parity: bool = False) -> dict[str, Any]:
    req: dict[str, Any] = {
        "BASE_PARITY": base_parity,
        "CONSENSUS_ARM_N": CONSENSUS_ARM_N,
        "OUTER_FOLD_N": 18,
        "SAME_TOP1_N": None,
        "SAME_TOP1_RATE": None,
        "B0_TOP1_B1_CONFIRMED_N": None,
        "B1_TOP1_B0_CONFIRMED_N": None,
        "PASS_ARM_N": 0,
        "BEST_PASS_ARM": None,
        "BEST_PASS_NET": None,
        "BEST_PASS_PF": None,
        "BEST_PASS_DD": None,
        "BEST_PASS_PAIRED_MEDIAN": None,
        "BEST_PASS_EX_BEST": None,
        "BEST_PASS_EX_TOP3": None,
        "CURRENT_PRESERVATION_PASS_ALL": False,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": verdict,
        "NEXT": nxt,
    }
    for prefix in ("C0", "C1", "C2"):
        for suffix in ("NET", "PF", "DD", "PAIRED_MEDIAN", "POS_DAYS", "NEG_DAYS", "EX_BEST", "EX_TOP3"):
            req[f"{prefix}_{suffix}"] = None
    return req


def _req_c(arm: dict[str, Any] | None, prefix: str) -> dict[str, Any]:
    a = arm or {}
    return {
        f"{prefix}_NET": a.get("OVERLAY_NET_PNL"),
        f"{prefix}_PF": a.get("OVERLAY_PF"),
        f"{prefix}_DD": a.get("OVERLAY_MAX_DD"),
        f"{prefix}_PAIRED_MEDIAN": a.get("PAIRED_MEDIAN_DAILY_DELTA"),
        f"{prefix}_POS_DAYS": a.get("PAIRED_POS_DAYS"),
        f"{prefix}_NEG_DAYS": a.get("PAIRED_NEG_DAYS"),
        f"{prefix}_EX_BEST": a.get("EX_BEST_DAY_PNL_DELTA"),
        f"{prefix}_EX_TOP3": a.get("EX_TOP3_DAYS_PNL_DELTA"),
    }


def _integrity(msg: str, extra: dict | None = None, *, base_parity: bool = False) -> int:
    decision = decide_case(
        integrity_ok=False,
        base_parity=base_parity,
        arms=[],
        current_net=0.0,
        current_pf=0.0,
        current_dd=0.0,
    )
    required = _empty_required(
        verdict=str(decision.get("VERDICT") or "AM_ENTRY_FINAL_REASSESSMENT_INTEGRITY_FAILED"),
        nxt=str(decision.get("NEXT") or "STOP"),
        base_parity=base_parity,
    )
    required["STOP_REASON"] = msg
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "decision": decision, **(extra or {})}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, {"integrity": kv_rows({"STOP_REASON": msg}), "precommit": [{"empty": True}]})
    print(msg, flush=True)
    return 2


def _eval_arm(
    tagged: list[dict[str, Any]],
    baseline: dict[str, Any],
    base_pack: dict[str, Any],
    arm_id: str,
) -> dict[str, Any]:
    overlay = overlay_replay(tagged, include_augment=True, augment_rank="utility")
    ov_trades = list(overlay.get("trades") or [])
    aug_trades = [t for t in ov_trades if str(t.get("arm") or "") == "AUGMENT"]
    ov_pack = economic_pack(ov_trades, list(ELIGIBLE_DAYS))
    aug_pack = economic_pack(aug_trades, list(ELIGIBLE_DAYS))
    paired = paired_delta(list(ov_pack.get("daily") or []), list(base_pack.get("daily") or []))
    pres = preservation_audit(baseline, overlay)
    pres["CURRENT_PRESERVATION_PASS"] = preservation_pass(pres)
    aug_adm = [a for a in (overlay.get("admissions") or []) if str(a.get("arm") or "") == "AUGMENT"]
    aug_fill = [f for f in (overlay.get("fills") or []) if str(f.get("arm") or "") == "AUGMENT"]
    candidates = list(overlay.get("augment_candidates") or [])
    gate = overlay_gate(
        ov_pack,
        base_pack,
        paired,
        preservation_pass=bool(pres.get("CURRENT_PRESERVATION_PASS")),
        integrity_ok=True,
    )
    conc = profit_concentration(list(aug_pack.get("daily") or []))
    rec = {
        "architecture_id": arm_id,
        "AUGMENT_CANDIDATE_N": len(candidates),
        "AUGMENT_ADMITTED_N": len(aug_adm),
        "AUGMENT_FILL_N": len(aug_fill),
        "AUGMENT_EXPIRED_N": max(len(aug_adm) - len(aug_fill), 0),
        "AUGMENT_FILL_RATE": _arm_fill_rate(len(aug_adm), len(aug_fill)),
        "AUGMENT_TRADE_N": aug_pack.get("trade_count"),
        "AUGMENT_NET_PNL": aug_pack.get("net_pnl_yen_100"),
        "AUGMENT_PF": aug_pack.get("profit_factor"),
        "AUGMENT_MAX_DD": aug_pack.get("max_drawdown_yen_100"),
        "AUGMENT_WIN_N": aug_pack.get("win_n"),
        "AUGMENT_LOSS_N": aug_pack.get("loss_n"),
        "AUGMENT_FLAT_N": aug_pack.get("flat_n"),
        "OVERLAY_TRADE_N": ov_pack.get("trade_count"),
        "OVERLAY_NET_PNL": ov_pack.get("net_pnl_yen_100"),
        "OVERLAY_PF": ov_pack.get("profit_factor"),
        "OVERLAY_MAX_DD": ov_pack.get("max_drawdown_yen_100"),
        "DELTA_PNL_VS_CURRENT": (ov_pack.get("net_pnl_yen_100") or 0.0) - (base_pack.get("net_pnl_yen_100") or 0.0),
        "DELTA_PF_VS_CURRENT": _pf_num(ov_pack.get("profit_factor")) - _pf_num(base_pack.get("profit_factor")),
        "DELTA_DD_VS_CURRENT": (ov_pack.get("max_drawdown_yen_100") or 0.0)
        - (base_pack.get("max_drawdown_yen_100") or 0.0),
        "PAIRED_POS_DAYS": paired.get("PAIRED_POS_DAYS"),
        "PAIRED_NEG_DAYS": paired.get("PAIRED_NEG_DAYS"),
        "PAIRED_ZERO_DAYS": paired.get("PAIRED_ZERO_DAYS"),
        "PAIRED_MEDIAN_DAILY_DELTA": paired.get("PAIRED_MEDIAN_DAILY_DELTA"),
        "EX_BEST_DAY_PNL_DELTA": paired.get("EX_BEST_DAY_PNL_DELTA"),
        "EX_TOP3_DAYS_PNL_DELTA": paired.get("EX_TOP3_DAYS_PNL_DELTA"),
        "TOTAL_AUGMENT_PNL_EX_BEST_DAY": conc.get("TOTAL_PNL_EX_BEST_AUGMENT_DAY"),
        "TOTAL_AUGMENT_PNL_EX_TOP3_DAYS": conc.get("TOTAL_PNL_EX_TOP3_AUGMENT_DAYS"),
        "CURRENT_PRESERVATION_PASS": pres.get("CURRENT_PRESERVATION_PASS"),
        "CURRENT_ADMISSION_LOST_N": pres.get("CURRENT_ADMISSION_LOST_N"),
        "CURRENT_FILL_LOST_N": pres.get("CURRENT_FILL_LOST_N"),
        "CURRENT_EXIT_MISMATCH_N": pres.get("CURRENT_EXIT_MISMATCH_N"),
        "CURRENT_PNL_MISMATCH_N": pres.get("CURRENT_PNL_MISMATCH_N"),
        "CURRENT_CAP_INTERFERENCE_N": pres.get("CURRENT_CAP_INTERFERENCE_N"),
        "CURRENT_SAME_SYMBOL_INTERFERENCE_N": pres.get("CURRENT_SAME_SYMBOL_INTERFERENCE_N"),
        "ARM_PASS": gate.get("AUGMENT_OVERLAY_PASS"),
        "gates": gate.get("gates"),
        "preservation": pres,
        "overlay_daily": list(ov_pack.get("daily") or []),
        "augment_daily": list(aug_pack.get("daily") or []),
        "augment_trades": aug_trades,
    }
    rec.update(conc)
    return rec


def _load_arm_scores(reuse_arm: str, rows_path: Path, leak: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str], str | None]:
    specs = arm_spec_grid(reuse_arm)
    if len(specs) != int(REPRESENTATION_N):
        return {}, [], "STOP. Representation count drifted."
    by_rep: dict[str, dict[str, Any]] = {}
    rep_ids = []
    for s in specs:
        rid = str(s.get("representation_id") or "")
        rep_ids.append(rid)
        cache = REGIME_PROBA / f"{reuse_arm}_{rid.replace('|', '_')}_oof_proba.json"
        job = {
            "spec_id": str(s.get("spec_id")),
            "spec": s,
            "days": list(ELIGIBLE_DAYS),
            "rows_path": str(rows_path),
            "cache_path": str(cache),
            "reuse_cache_path": str(cache),
        }
        body = process_oof_scores(job)
        if not body.get("ok"):
            return {}, [], f"STOP. OOF missing/failed for {reuse_arm} {rid}: {body.get('blocker')}"
        ig = body.get("integrity") or {}
        leak["OUTER_HELDOUT_FIT_LEAK_N"] += int(ig.get("HELDOUT_FIT_LEAK_N") or 0)
        leak["PM_ROWS_USED_N"] = max(int(leak["PM_ROWS_USED_N"] or 0), int(ig.get("PM_ROWS_USED_N") or 0))
        leak["TARGET_CONTAMINATION_N"] = max(
            int(leak["TARGET_CONTAMINATION_N"] or 0), int(ig.get("TARGET_CONTAMINATION_N") or 0)
        )
        leak["FUTURE_FEATURE_USE_N"] += int(ig.get("FUTURE_FEATURE_USE_N") or 0)
        leak["HYPERPARAMETER_SEARCH_N"] += int(ig.get("HYPERPARAMETER_SEARCH_N") or 0)
        leak["NEW_MODEL_N"] += int(ig.get("NEW_MODEL_N") or 0)
        sample = next(iter((body.get("scores") or {}).values()), None)
        if not (isinstance(sample, dict) and "P_WIN" in (sample or {})):
            return {}, [], f"STOP. OOF cache missing class probabilities for {reuse_arm} {rid}."
        if not body.get("reused_from") and not cache.is_file():
            leak["NEW_MODEL_N"] += 1
        by_rep[rid] = dict(body.get("scores") or {})
        print(f"oof {reuse_arm} {rid} n={len(by_rep[rid])} reused={bool(body.get('reused_from') or cache.is_file())}", flush=True)
    return by_rep, rep_ids, None


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get(
        "PYTHONPATH", ""
    )
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM ENTRY ARCHITECTURE FINAL REASSESSMENT V1", flush=True)
    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        return _integrity("STOP. Runtime WAIT_SEC drifted from 1.0.")
    if abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        return _integrity("STOP. DEV_WAIT_SEC drifted from 5.0.")
    if list(FEATURE_ORDER) != [
        "spread_bps",
        "imbalance",
        "mid_ret_60s",
        "mid_ret_180s",
        "event_rate_60s",
        "log_bid_qty",
    ]:
        return _integrity("STOP. FEATURE_ORDER drift.")
    if ENTRY_BINDING.get("rank_pass_gate") is not None:
        return _integrity("STOP. rank_pass_gate drift.")
    c14 = _load(C14)
    if str(c14.get("candidate_id") or "") != C14_ID:
        return _integrity("STOP. C14 identity mismatch.")
    if int(AUGMENT_MAX_PER_COHORT) != 1 or CURRENT_PRIORITY is not True:
        return _integrity("STOP. Augment contract drifted.")
    if FROZEN_ARM != "EXPANDED_X14_RF" or TARGET != "PROFITABLE_FILL_CLASS":
        return _integrity("STOP. Frozen model/target drifted.")
    if int(CONSENSUS_ARM_N) != 3 or list(ARM_IDS) != [B0, B1, C0, C1, C2]:
        return _integrity("STOP. Arm set drifted.")
    if TRUE_OOS is not False or int(NEW_FORWARD_N) != 0:
        return _integrity("STOP. TRUE_OOS / NEW_FORWARD_N drifted.")
    if int(SUBMIT_N) != 0 or int(CANCEL_N) != 0 or int(LIVE_ORDER_N) != 0 or PAPER_OPERATED is not False:
        return _integrity("STOP. submit/cancel/live/paper drifted.")
    if not REGIME_LABELED.is_file() and not LABELED_X14.is_file():
        return _integrity("STOP. labeled AM rows missing.")

    spec = precommit_spec()
    sha = spec_sha256(spec)
    print_precommit(spec, sha)

    if REGIME_LABELED.is_file():
        rows = list(_load(REGIME_LABELED).get("rows") or [])
        print(f"regime labeled cache-hit n={len(rows)}", flush=True)
    else:
        from research.am_entry_temporal_regime_information.cohort_state import attach_all_cohort_state

        rows = list(_load(LABELED_X14).get("rows") or [])
        rows, state_meta = attach_all_cohort_state(rows)
        if int(state_meta.get("AM_SLOT_UNKNOWN_N") or 0) != 0:
            return _integrity("STOP. AM_SLOT_INDEX not on frozen CLOCK_GRID.", extra={"state": state_meta})
    pm_n = sum(1 for r in rows if session_of(r) != "AM")
    if pm_n:
        return _integrity("STOP. PM rows in labeled AM.", extra={"PM_ROWS_USED_N": pm_n})
    class_n = {
        CLASS_WIN: sum(1 for r in rows if r.get(TARGET) == CLASS_WIN),
        CLASS_LOSS: sum(1 for r in rows if r.get(TARGET) == CLASS_LOSS),
        CLASS_NEUTRAL: sum(1 for r in rows if r.get(TARGET) == CLASS_NEUTRAL),
    }
    print(
        f"classes WIN={class_n[CLASS_WIN]} LOSS={class_n[CLASS_LOSS]} NEUTRAL={class_n[CLASS_NEUTRAL]} n={len(rows)}",
        flush=True,
    )
    top3 = independent_top3(rows)
    obs = {
        "AM_LABELED_N": len(rows),
        "AM_Y_FILL5_POS_N": sum(1 for r in rows if int(r.get("Y_FILL5") or 0) == 1),
        "AM_CURRENT_TOP3_FILL5_RATE": top3.get("AM_CURRENT_TOP3_FILL5_RATE"),
    }
    parity_pop = freeze_parity(obs)
    current_eval = evaluate_policy(rows, list(ELIGIBLE_DAYS), score_key="current_score")
    replay_ok = (
        bool(parity_pop.get("ok"))
        and int(current_eval.get("trade_count") or -1) == int(V2_CURRENT_TRADE_N)
        and _close(current_eval.get("net_pnl_yen_100"), V2_CURRENT_NET_PNL, PARITY_ABS_TOL)
        and _close(current_eval.get("profit_factor"), V2_CURRENT_PF, 1e-12)
        and _close(current_eval.get("max_drawdown_yen_100"), V2_CURRENT_MAX_DD, PARITY_ABS_TOL)
    )
    print("parity_pop", parity_pop.get("ok"), "replay_ok", replay_ok, flush=True)
    if not replay_ok:
        return _integrity("STOP. Frozen CURRENT parity did not reproduce.", extra={"parity_pop": parity_pop})

    leak = {
        "OUTER_HELDOUT_FIT_LEAK_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
        "FUTURE_EVENT_USE_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "PM_ROWS_USED_N": 0,
        "NEW_MODEL_N": 0,
        "NEW_FEATURE_N": NEW_FEATURE_N,
        "NEW_TARGET_N": 0,
        "MODEL_SEARCH_N": 0,
        "HYPERPARAMETER_SEARCH_N": 0,
        "SCORE_BLEND_N": 0,
        "SCORE_THRESHOLD_SEARCH_N": 0,
        "PROBABILITY_THRESHOLD_SEARCH_N": 0,
        "REPRESENTATION_SELECTION_N": 0,
        "WAIT_SEARCH_N": WAIT_SEARCH_N,
        "EXIT_CHANGE_N": 0,
        "CURRENT_POLICY_CHANGE_N": 0,
        "ORACLE_SELECTION_USE_N": 0,
        "SUBMIT_N": SUBMIT_N,
        "CANCEL_N": CANCEL_N,
        "LIVE_ORDER_N": LIVE_ORDER_N,
        "C14_CHANGED": C14_CHANGED,
        "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
        "PAPER_OPERATED": PAPER_OPERATED,
        "RUNTIME_CHANGED": RUNTIME_CHANGED,
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "BASE_PARITY": False,
        "OOF_REFIT_N": 0,
    }

    rows_path = REGIME_LABELED if REGIME_LABELED.is_file() else LABELED_X14
    b0_scores, b0_reps, err = _load_arm_scores(A3_REUSE_ARM, rows_path, leak)
    if err:
        return _integrity(err, extra={"integrity": leak})
    b1_scores, b1_reps, err = _load_arm_scores(A5_REUSE_ARM, rows_path, leak)
    if err:
        return _integrity(err, extra={"integrity": leak})
    if len(b0_scores) != int(REPRESENTATION_N) or len(b1_scores) != int(REPRESENTATION_N):
        return _integrity("STOP. Missing B0/B1 representation OOF.", extra={"integrity": leak})

    integrity_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO_KEYS)
    if not integrity_ok:
        return _integrity("STOP. Integrity counters non-zero.", extra={"integrity": leak})

    baseline = overlay_replay(rows, include_augment=False)
    base_pack = economic_pack(list(baseline.get("trades") or []), list(ELIGIBLE_DAYS))
    if (
        int(base_pack.get("trade_count") or -1) != int(V2_CURRENT_TRADE_N)
        or not _close(base_pack.get("net_pnl_yen_100"), V2_CURRENT_NET_PNL, PARITY_ABS_TOL)
        or not _close(_pf_num(base_pack.get("profit_factor")), V2_CURRENT_PF, 1e-12)
        or not _close(base_pack.get("max_drawdown_yen_100"), V2_CURRENT_MAX_DD, PARITY_ABS_TOL)
    ):
        return _integrity(
            "STOP. CURRENT-first replay drifted from frozen CURRENT.",
            extra={"base": _slim(base_pack)},
        )

    b0_tagged = attach_ensemble(rows, joint_map(b0_scores), b0_reps)
    b1_tagged = attach_ensemble(rows, joint_map(b1_scores), b1_reps)
    merged = merge_model_fields(b0_tagged, b1_tagged)
    dec = cohort_decisions(merged)
    geo = dict(dec.get("geometry") or {})
    print(
        f"geometry COHORT_N={geo.get('COHORT_N')} SAME_TOP1_N={geo.get('SAME_TOP1_N')} "
        f"B0_CONF={geo.get('B0_TOP1_B1_CONFIRMED_N')} B1_CONF={geo.get('B1_TOP1_B0_CONFIRMED_N')}",
        flush=True,
    )
    diag = selection_change_diagnostic(dec)

    tagged_by = {
        B0: b0_tagged,
        B1: b1_tagged,
        C0: tag_consensus(merged, set(dec.get("c0_keys") or set()), score_prefix="B0"),
        C1: tag_consensus(merged, set(dec.get("c1_keys") or set()), score_prefix="B1"),
        C2: tag_consensus(merged, set(dec.get("c2_keys") or set()), score_prefix="B0"),
    }
    arm_rows = []
    for arm_id in ARM_IDS:
        rec = _eval_arm(tagged_by[arm_id], baseline, base_pack, arm_id)
        arm_rows.append(rec)
        print(
            f"arm {arm_id} overlay_net={rec.get('OVERLAY_NET_PNL')} pf={rec.get('OVERLAY_PF')} "
            f"dd={rec.get('OVERLAY_MAX_DD')} paired_med={rec.get('PAIRED_MEDIAN_DAILY_DELTA')} "
            f"pos={rec.get('PAIRED_POS_DAYS')} neg={rec.get('PAIRED_NEG_DAYS')} "
            f"ex_best={rec.get('EX_BEST_DAY_PNL_DELTA')} ex_top3={rec.get('EX_TOP3_DAYS_PNL_DELTA')} "
            f"pres={rec.get('CURRENT_PRESERVATION_PASS')} pass={rec.get('ARM_PASS')}",
            flush=True,
        )

    by = {str(a.get("architecture_id")): a for a in arm_rows}
    b0_ok = expected_match(by.get(B0) or {}, B0_EXPECTED, abs_tol=PARITY_ABS_TOL)
    b1_ok = expected_match(by.get(B1) or {}, B1_EXPECTED, abs_tol=PARITY_ABS_TOL)
    base_parity = bool(b0_ok and b1_ok and replay_ok)
    leak["BASE_PARITY"] = base_parity
    leak["B0_PARITY"] = bool(b0_ok)
    leak["B1_PARITY"] = bool(b1_ok)
    print(f"BASE_PARITY={base_parity} B0={b0_ok} B1={b1_ok}", flush=True)
    if not base_parity:
        drop = ("overlay_daily", "augment_daily", "preservation", "gates", "augment_trades")
        return _integrity(
            "STOP. B0/B1 did not reproduce frozen A3/A5 economics.",
            extra={
                "integrity": leak,
                "b0": {k: v for k, v in (by.get(B0) or {}).items() if k not in drop},
                "b1": {k: v for k, v in (by.get(B1) or {}).items() if k not in drop},
            },
            base_parity=False,
        )

    c0_inc = increment_vs(by.get(C0) or {}, by.get(B0) or {}, arm_id="C0", vs="B0")
    c1_inc = increment_vs(by.get(C1) or {}, by.get(B1) or {}, arm_id="C1", vs="B1")
    c2_vs_b0 = increment_vs(by.get(C2) or {}, by.get(B0) or {}, arm_id="C2", vs="B0")
    c2_vs_b1 = increment_vs(by.get(C2) or {}, by.get(B1) or {}, arm_id="C2", vs="B1")
    by[C0].update(c0_inc)
    by[C1].update(c1_inc)
    by[C2].update(c2_vs_b0)
    by[C2].update(c2_vs_b1)

    preservation_all = all(bool(a.get("CURRENT_PRESERVATION_PASS")) for a in arm_rows)
    c_pass = [a for a in arm_rows if str(a.get("architecture_id")) in {C0, C1, C2} and bool(a.get("ARM_PASS"))]
    best_pass = pick_best(c_pass) if c_pass else None
    current_net = float(base_pack.get("net_pnl_yen_100") or 0.0)
    current_pf = _pf_num(base_pack.get("profit_factor"))
    current_dd = float(base_pack.get("max_drawdown_yen_100") or 0.0)
    decision = decide_case(
        integrity_ok=True,
        base_parity=True,
        arms=arm_rows,
        current_net=current_net,
        current_pf=current_pf,
        current_dd=current_dd,
    )
    required = {
        "BASE_PARITY": True,
        "CONSENSUS_ARM_N": CONSENSUS_ARM_N,
        "OUTER_FOLD_N": 18,
        "SAME_TOP1_N": geo.get("SAME_TOP1_N"),
        "SAME_TOP1_RATE": geo.get("SAME_TOP1_RATE"),
        "B0_TOP1_B1_CONFIRMED_N": geo.get("B0_TOP1_B1_CONFIRMED_N"),
        "B1_TOP1_B0_CONFIRMED_N": geo.get("B1_TOP1_B0_CONFIRMED_N"),
        **_req_c(by.get(C0), "C0"),
        **_req_c(by.get(C1), "C1"),
        **_req_c(by.get(C2), "C2"),
        "PASS_ARM_N": len(c_pass),
        "BEST_PASS_ARM": (best_pass or {}).get("architecture_id") if c_pass else None,
        "BEST_PASS_NET": (best_pass or {}).get("OVERLAY_NET_PNL") if c_pass else None,
        "BEST_PASS_PF": (best_pass or {}).get("OVERLAY_PF") if c_pass else None,
        "BEST_PASS_DD": (best_pass or {}).get("OVERLAY_MAX_DD") if c_pass else None,
        "BEST_PASS_PAIRED_MEDIAN": (best_pass or {}).get("PAIRED_MEDIAN_DAILY_DELTA") if c_pass else None,
        "BEST_PASS_EX_BEST": (best_pass or {}).get("EX_BEST_DAY_PNL_DELTA") if c_pass else None,
        "BEST_PASS_EX_TOP3": (best_pass or {}).get("EX_TOP3_DAYS_PNL_DELTA") if c_pass else None,
        "CURRENT_PRESERVATION_PASS_ALL": bool(preservation_all),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "PRECOMMIT_SPEC_SHA256": sha,
        "CASE": decision.get("CASE"),
        "SELECTED_ARM": (best_pass or {}).get("architecture_id") if c_pass else None,
    }

    daily_rows = []
    outer_rows = []
    for d in ELIGIBLE_DAYS:
        cday = next((x for x in (base_pack.get("daily") or []) if x.get("date") == d), {})
        rec = {"date": d, "CURRENT": cday.get("pnl_yen_100")}
        orec = {"outer_day": d, "CURRENT_PNL": cday.get("pnl_yen_100")}
        for a in arm_rows:
            ad = next((x for x in (a.get("overlay_daily") or []) if x.get("date") == d), {})
            gd = next((x for x in (a.get("augment_daily") or []) if x.get("date") == d), {})
            rec[str(a.get("architecture_id"))] = ad.get("pnl_yen_100")
            rec[f"{a.get('architecture_id')}_AUG"] = gd.get("pnl_yen_100")
            orec[f"{a.get('architecture_id')}_OVERLAY"] = ad.get("pnl_yen_100")
            orec[f"{a.get('architecture_id')}_AUGMENT"] = gd.get("pnl_yen_100")
        daily_rows.append(rec)
        outer_rows.append(orec)

    drop = ("overlay_daily", "augment_daily", "preservation", "gates", "augment_trades")
    arm_sheet = [{k: v for k, v in a.items() if k not in drop} for a in arm_rows]
    inc_sheet = [
        {"pair": "C0-B0", **c0_inc},
        {"pair": "C1-B1", **c1_inc},
        {"pair": "C2-B0", **c2_vs_b0},
        {"pair": "C2-B1", **c2_vs_b1},
    ]
    extra = {
        "precommit": spec,
        "decision": decision,
        "classes": class_n,
        "current": _slim(base_pack),
        "geometry": geo,
        "selection_change": diag,
        "arms": arm_sheet,
        "increment": inc_sheet,
        "gates": {a.get("architecture_id"): a.get("gates") for a in arm_rows},
        "integrity": leak,
        "SESSION": SESSION,
        "DEV_WAIT_SEC": DEV_WAIT_SEC,
        "RUNTIME_WAIT_SEC": WAIT_SEC,
        "POSITION_CAP": POSITION_CAP,
        "TARGET": TARGET,
        "FROZEN_ARM": FROZEN_ARM,
        "POSITIVE_REP_MIN": POS_REP_MIN,
        "AVAILABLE_REP_MIN": AVAILABLE_REP_MIN,
        "X14_BUNDLE": list(X14_BUNDLE),
        "ARM_IDS": list(ARM_IDS),
    }
    sheets = {
        "precommit": kv_rows({**spec, "PRECOMMIT_SPEC_SHA256": sha, "PRECOMMIT_LOCKED": True}),
        "geometry": kv_rows(geo),
        "arms": arm_sheet,
        "increment": inc_sheet,
        "selection_change": kv_rows(diag),
        "outer_folds": outer_rows,
        "daily_pnl": daily_rows,
        "integrity": kv_rows(leak),
    }
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "decision": decision, **extra}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, sheets)
    print(f"wrote {OUT / 'report.json'}", flush=True)
    print(f"VERDICT {required.get('VERDICT')}", flush=True)
    print(f"NEXT {required.get('NEXT')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
