"""Offline CURRENT-preserving temporal/regime information expansion. No Runtime write. No Paper."""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
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
from research.am_entry_fixed_spec_oof import (
    V2_CURRENT_MAX_DD,
    V2_CURRENT_NET_PNL,
    V2_CURRENT_PF,
    V2_CURRENT_TRADE_N,
)
from research.am_entry_fixed_spec_oof.oof import evaluate_policy
from research.am_entry_information_expansion import (
    AVAILABLE_REP_MIN,
    CLASS_LOSS,
    CLASS_NEUTRAL,
    CLASS_WIN,
    MAX_WORKERS,
    TARGET,
    X14_BUNDLE,
)
from research.am_entry_profit_improvement import (
    C14_CHANGED,
    C14_ID,
    CANCEL_N,
    COMMON_AM_PM_MODEL_ALLOWED,
    COMMON_AM_PM_TARGET_ALLOWED,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    FEATURE_SEARCH_N,
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
from research.am_entry_profit_improvement.publish import kv_rows
from research.am_entry_temporal_regime_information import (
    A0,
    A1,
    A2,
    A3,
    A4,
    A5,
    ANALYSIS_ID,
    ARM_IDS,
    ARM_N,
    AUGMENT_MAX_PER_COHORT,
    CORE_STATE_FEATURES,
    CURRENT_PRIORITY,
    FROZEN_ARM,
    PRIOR_AUGMENT_NET,
    PRIOR_AUGMENT_PF,
    PRIOR_AUGMENT_TRADE_N,
    PRIOR_EX_BEST_AUGMENT_DAY,
    PRIOR_EX_TOP3_AUGMENT_DAYS,
    PRIOR_NEG_DAYS,
    PRIOR_OVERLAY_MAX_DD,
    PRIOR_OVERLAY_NET,
    PRIOR_OVERLAY_PF,
    PRIOR_PAIRED_MEDIAN,
    PRIOR_POS_DAYS,
    REGIME_FEATURES,
    TEMPORAL_FEATURES,
)
from research.am_entry_temporal_regime_information.analyze import (
    CORE_ARMS,
    REGIME_ARMS,
    TEMPORAL_ARMS,
    a0_trade_diagnostics,
    bundle_incremental,
    decide_case,
    increment_sort_key,
    increment_vs_a0,
    pick_best,
)
from research.am_entry_temporal_regime_information.cohort_state import attach_all_cohort_state, missingness_lodo
from research.am_expanded_entry_risk_integration.analyze import profit_concentration
from research.am_expanded_entry_risk_integration.ensemble import joint_map
from research.am_entry_temporal_regime_information.oof import arm_spec_grid, process_oof_scores
from research.am_entry_temporal_regime_information.precommit import extra_features_for, precommit_spec, print_precommit, spec_sha256
from research.am_entry_temporal_regime_information.publish import OUT, build_markdown, write_artifacts
from research.am_utility_augment_execution_risk.analyze import preservation_pass
from research.canonical_entry_performance_rebase.analyze import _f, row_key, session_of
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
LABELED_X14 = (
    NATIVE / "results" / "research" / "_work_cache" / "am_entry_information_expansion" / "labeled_am_x14.json"
)
A0_PROBA = NATIVE / "results" / "research" / "_work_cache" / "am_expanded_entry_risk_integration"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_entry_temporal_regime_information"

INTEGRITY_ZERO_KEYS = (
    "OUTER_HELDOUT_FIT_LEAK_N",
    "FUTURE_FEATURE_USE_N",
    "TARGET_CONTAMINATION_N",
    "PM_ROWS_USED_N",
    "FEATURE_SEARCH_N",
    "FEATURE_SUBSET_SEARCH_N",
    "DATE_FEATURE_USE_N",
    "FUTURE_REGIME_USE_N",
    "MODEL_SEARCH_N",
    "HYPERPARAMETER_SEARCH_N",
    "SCORE_THRESHOLD_SEARCH_N",
    "REGIME_THRESHOLD_SEARCH_N",
    "TIME_WINDOW_SEARCH_N",
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


def _save_json(path: Path, body: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")


def _pool(fn, jobs: list[dict], label: str, key: str) -> list[dict]:
    if not jobs:
        return []
    out = []
    workers = min(MAX_WORKERS, len(jobs))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, job): job.get(key) for job in jobs}
        for fut in as_completed(futs):
            ident = futs[fut]
            try:
                body = fut.result()
            except Exception as exc:
                body = {"ok": False, key: ident, "blocker": f"{type(exc).__name__}:{exc}"}
            out.append(body)
            print(
                f"done {label} {body.get(key) or ident} ok={body.get('ok')} blocker={body.get('blocker')}",
                flush=True,
            )
    return out


def _slim(pack: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pack.items() if k not in ("trades", "daily")}


def _arm_fill_rate(admitted: int, fill_n: int) -> float | None:
    return (float(fill_n) / float(admitted)) if admitted else None


def _empty_required(*, verdict: str, nxt: str, base_parity: bool = False) -> dict[str, Any]:
    req: dict[str, Any] = {
        "BASE_PARITY": base_parity,
        "ARM_N": ARM_N,
        "OUTER_FOLD_N": 18,
        "PASS_ARM_N": 0,
        "BEST_PASS_ARM": None,
        "BEST_PASS_NET": None,
        "BEST_PASS_PF": None,
        "BEST_PASS_DD": None,
        "BEST_PASS_PAIRED_MEDIAN": None,
        "BEST_PASS_EX_BEST": None,
        "BEST_PASS_EX_TOP3": None,
        "BEST_INFORMATION_ARM_VS_A0": None,
        "TEMPORAL_INFORMATION_INCREMENTAL": False,
        "MARKET_REGIME_INFORMATION_INCREMENTAL": False,
        "CORE_STATE_INFORMATION_INCREMENTAL": False,
        "CURRENT_PRESERVATION_PASS_ALL": False,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": verdict,
        "NEXT": nxt,
    }
    for prefix in ("A0", "A1", "A2", "A3", "A4", "A5"):
        for suffix in ("NET", "PF", "DD", "PAIRED_MEDIAN"):
            req[f"{prefix}_{suffix}"] = None
    return req


def _req_arm(arm: dict[str, Any] | None, prefix: str) -> dict[str, Any]:
    a = arm or {}
    return {
        f"{prefix}_NET": a.get("OVERLAY_NET_PNL"),
        f"{prefix}_PF": a.get("OVERLAY_PF"),
        f"{prefix}_DD": a.get("OVERLAY_MAX_DD"),
        f"{prefix}_PAIRED_MEDIAN": a.get("PAIRED_MEDIAN_DAILY_DELTA"),
    }


def _integrity(msg: str, extra: dict | None = None, *, base_parity: bool = False) -> int:
    decision = decide_case(
        integrity_ok=False,
        arms=[],
        current_net=0.0,
        current_pf=0.0,
        current_dd=0.0,
    )
    required = _empty_required(
        verdict=str(decision.get("VERDICT") or "AM_TEMPORAL_REGIME_INFORMATION_INTEGRITY_FAILED"),
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
        "n_extra_features": len(extra_features_for(arm_id)),
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


def _date_feature_n(names: list[str] | tuple[str, ...]) -> int:
    n = 0
    for f in names:
        s = str(f).lower()
        if "date" in s or "weekday" in s or s in {"dow", "calendar", "yyyymmdd"}:
            n += 1
    return n


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get(
        "PYTHONPATH", ""
    )
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
    if int(ARM_N) != 6 or list(ARM_IDS) != [A0, A1, A2, A3, A4, A5]:
        return _integrity("STOP. Arm set drifted.")
    if TRUE_OOS is not False or int(NEW_FORWARD_N) != 0:
        return _integrity("STOP. TRUE_OOS / NEW_FORWARD_N drifted.")
    if int(SUBMIT_N) != 0 or int(CANCEL_N) != 0 or int(LIVE_ORDER_N) != 0 or PAPER_OPERATED is not False:
        return _integrity("STOP. submit/cancel/live/paper drifted.")
    if not LABELED_X14.is_file():
        return _integrity("STOP. labeled_am_x14.json missing.")

    spec = precommit_spec()
    sha = spec_sha256(spec)
    print_precommit(spec, sha)
    print("AM CURRENT-preserving temporal/regime information. 6 arms. 18-day OOF. No score search.", flush=True)

    rows = list(_load(LABELED_X14).get("rows") or [])
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
    print("BASE_PARITY true", flush=True)

    rows, state_meta = attach_all_cohort_state(rows)
    if int(state_meta.get("AM_SLOT_UNKNOWN_N") or 0) != 0:
        return _integrity("STOP. AM_SLOT_INDEX not on frozen CLOCK_GRID.", extra={"state": state_meta}, base_parity=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    rows_path = CACHE / "labeled_am_regime.json"
    _save_json(rows_path, {"session": "AM", "target": TARGET, "rows": rows})

    date_n = _date_feature_n(extra_features_for(A5))
    leak = {
        "OUTER_HELDOUT_FIT_LEAK_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
        "TARGET_CONTAMINATION_N": 0,
        "PM_ROWS_USED_N": 0,
        "FEATURE_SEARCH_N": FEATURE_SEARCH_N,
        "FEATURE_SUBSET_SEARCH_N": 0,
        "DATE_FEATURE_USE_N": int(date_n),
        "FUTURE_REGIME_USE_N": 0,
        "MODEL_SEARCH_N": 0,
        "HYPERPARAMETER_SEARCH_N": 0,
        "SCORE_THRESHOLD_SEARCH_N": 0,
        "REGIME_THRESHOLD_SEARCH_N": 0,
        "TIME_WINDOW_SEARCH_N": 0,
        "WAIT_SEARCH_N": WAIT_SEARCH_N,
        "EXIT_CHANGE_N": 0,
        "CURRENT_POLICY_CHANGE_N": 0,
        "ORACLE_SELECTION_USE_N": 0,
        "SUBMIT_N": SUBMIT_N,
        "CANCEL_N": CANCEL_N,
        "LIVE_ORDER_N": LIVE_ORDER_N,
        "NEW_FEATURE_N": NEW_FEATURE_N,
        "REPRESENTATION_SELECTION_N": 0,
        "POSTHOC_MODEL_ADDITION_N": 0,
        "C14_CHANGED": C14_CHANGED,
        "W5_RUNTIME_ADOPTED": W5_RUNTIME_ADOPTED,
        "PAPER_OPERATED": PAPER_OPERATED,
        "RUNTIME_CHANGED": RUNTIME_CHANGED,
        "COMMON_AM_PM_MODEL_ALLOWED": COMMON_AM_PM_MODEL_ALLOWED,
        "COMMON_AM_PM_TARGET_ALLOWED": COMMON_AM_PM_TARGET_ALLOWED,
        "PRIOR_POLICY_REPLAY_OK": False,
    }
    if date_n:
        return _integrity("STOP. Date/weekday feature leaked into extra bundle.", extra={"integrity": leak}, base_parity=True)

    jobs = []
    allowed = set(ARM_IDS)
    for arm_id in ARM_IDS:
        specs = arm_spec_grid(arm_id)
        if len(specs) != int(REPRESENTATION_N):
            return _integrity("STOP. Representation count drifted.", extra={"arm": arm_id}, base_parity=True)
        for s in specs:
            sid = str(s.get("spec_id"))
            rid = str(s.get("representation_id") or "")
            job = {
                "spec_id": sid,
                "spec": s,
                "days": list(ELIGIBLE_DAYS),
                "rows_path": str(rows_path),
                "cache_path": str(CACHE / f"{sid.replace('|', '_')}_oof_proba.json"),
            }
            if arm_id == A0:
                job["reuse_cache_path"] = str(A0_PROBA / f"EXPANDED_X14_RF_{rid.replace('|', '_')}_oof_proba.json")
            jobs.append(job)
    print(f"oof jobs={len(jobs)} workers={min(MAX_WORKERS, len(jobs))}", flush=True)
    got = _pool(process_oof_scores, jobs, "OOF", "spec_id")
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != len(jobs):
        return _integrity(
            "STOP. OOF probability replay failed.",
            extra={"fail": [(b.get("spec_id"), b.get("blocker")) for b in fail]},
            base_parity=True,
        )

    by_arm: dict[str, dict[str, dict[str, Any]]] = {a: {} for a in ARM_IDS}
    for b in got:
        aid = str(b.get("architecture_id") or "")
        if aid not in allowed:
            leak["POSTHOC_MODEL_ADDITION_N"] += 1
        ig = b.get("integrity") or {}
        leak["OUTER_HELDOUT_FIT_LEAK_N"] += int(ig.get("HELDOUT_FIT_LEAK_N") or 0)
        leak["PM_ROWS_USED_N"] = max(int(leak["PM_ROWS_USED_N"] or 0), int(ig.get("PM_ROWS_USED_N") or 0))
        leak["TARGET_CONTAMINATION_N"] = max(
            int(leak["TARGET_CONTAMINATION_N"] or 0), int(ig.get("TARGET_CONTAMINATION_N") or 0)
        )
        leak["HYPERPARAMETER_SEARCH_N"] += int(ig.get("HYPERPARAMETER_SEARCH_N") or 0)
        if int(b.get("fold_n") or 0) != 18:
            leak["OUTER_HELDOUT_FIT_LEAK_N"] += 1
        sample = next(iter((b.get("scores") or {}).values()), None)
        if not (isinstance(sample, dict) and "P_WIN" in (sample or {})):
            return _integrity("STOP. OOF cache missing class probabilities.", extra={"spec_id": b.get("spec_id")}, base_parity=True)
        by_arm.setdefault(aid, {})[str(b.get("representation_id"))] = dict(b.get("scores") or {})
    if leak["POSTHOC_MODEL_ADDITION_N"]:
        return _integrity("STOP. Post-hoc model addition.", extra={"integrity": leak}, base_parity=True)
    for arm_id in ARM_IDS:
        if len(by_arm.get(arm_id) or {}) != int(REPRESENTATION_N):
            return _integrity("STOP. Missing representation OOF.", extra={"arm": arm_id}, base_parity=True)

    integrity_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO_KEYS)
    if not integrity_ok:
        return _integrity("STOP. Integrity counters non-zero.", extra={"integrity": leak}, base_parity=True)

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
            base_parity=True,
        )

    a0_specs = arm_spec_grid(A0)
    a0_rep_ids = [str(s.get("representation_id")) for s in a0_specs]
    a0_tagged = attach_ensemble(rows, joint_map(by_arm[A0]), a0_rep_ids)
    a0_rec = _eval_arm(a0_tagged, baseline, base_pack, A0)
    prior_ok = (
        int(a0_rec.get("AUGMENT_TRADE_N") or -1) == int(PRIOR_AUGMENT_TRADE_N)
        and _close(a0_rec.get("AUGMENT_NET_PNL"), PRIOR_AUGMENT_NET, PARITY_ABS_TOL)
        and _close(_pf_num(a0_rec.get("AUGMENT_PF")), PRIOR_AUGMENT_PF, 1e-12)
        and _close(a0_rec.get("OVERLAY_NET_PNL"), PRIOR_OVERLAY_NET, PARITY_ABS_TOL)
        and _close(_pf_num(a0_rec.get("OVERLAY_PF")), PRIOR_OVERLAY_PF, 1e-12)
        and _close(a0_rec.get("OVERLAY_MAX_DD"), PRIOR_OVERLAY_MAX_DD, PARITY_ABS_TOL)
        and int(a0_rec.get("PAIRED_POS_DAYS") or -1) == int(PRIOR_POS_DAYS)
        and int(a0_rec.get("PAIRED_NEG_DAYS") or -1) == int(PRIOR_NEG_DAYS)
        and _close(a0_rec.get("PAIRED_MEDIAN_DAILY_DELTA"), PRIOR_PAIRED_MEDIAN, PARITY_ABS_TOL)
        and _close(a0_rec.get("TOTAL_AUGMENT_PNL_EX_BEST_DAY"), PRIOR_EX_BEST_AUGMENT_DAY, PARITY_ABS_TOL)
        and _close(a0_rec.get("TOTAL_AUGMENT_PNL_EX_TOP3_DAYS"), PRIOR_EX_TOP3_AUGMENT_DAYS, PARITY_ABS_TOL)
    )
    leak["PRIOR_POLICY_REPLAY_OK"] = bool(prior_ok)
    print(
        f"A0 prior replay ok={prior_ok} aug_n={a0_rec.get('AUGMENT_TRADE_N')} "
        f"aug_net={a0_rec.get('AUGMENT_NET_PNL')} ov_net={a0_rec.get('OVERLAY_NET_PNL')} "
        f"ov_pf={a0_rec.get('OVERLAY_PF')} ex_best={a0_rec.get('TOTAL_AUGMENT_PNL_EX_BEST_DAY')}",
        flush=True,
    )
    if not prior_ok:
        return _integrity(
            "STOP. Prior EXPANDED_X14_RF policy did not reproduce as A0.",
            extra={
                "integrity": leak,
                "a0": {k: v for k, v in a0_rec.items() if k not in ("overlay_daily", "augment_daily", "preservation", "gates", "augment_trades")},
            },
            base_parity=True,
        )

    arm_rows = [a0_rec]
    print(
        f"arm {A0} overlay_net={a0_rec.get('OVERLAY_NET_PNL')} pf={a0_rec.get('OVERLAY_PF')} "
        f"dd={a0_rec.get('OVERLAY_MAX_DD')} pass={a0_rec.get('ARM_PASS')}",
        flush=True,
    )
    for arm_id in (A1, A2, A3, A4, A5):
        specs = arm_spec_grid(arm_id)
        rep_ids = [str(s.get("representation_id")) for s in specs]
        tagged = attach_ensemble(rows, joint_map(by_arm[arm_id]), rep_ids)
        rec = _eval_arm(tagged, baseline, base_pack, arm_id)
        rec.update(increment_vs_a0(rec, a0_rec))
        arm_rows.append(rec)
        print(
            f"arm {arm_id} overlay_net={rec.get('OVERLAY_NET_PNL')} pf={rec.get('OVERLAY_PF')} "
            f"dd={rec.get('OVERLAY_MAX_DD')} paired_med={rec.get('PAIRED_MEDIAN_DAILY_DELTA')} "
            f"d_net={rec.get('DELTA_NET_VS_X14_BASE')} d_ex_top3={rec.get('DELTA_EX_TOP3_VS_X14_BASE')} "
            f"pres={rec.get('CURRENT_PRESERVATION_PASS')} pass={rec.get('ARM_PASS')}",
            flush=True,
        )
    a0_rec.update(increment_vs_a0(a0_rec, a0_rec))

    preservation_all = all(bool(a.get("CURRENT_PRESERVATION_PASS")) for a in arm_rows)
    pass_arms = [a for a in arm_rows if bool(a.get("ARM_PASS"))]
    best_pass = pick_best(pass_arms) if pass_arms else None
    others = [a for a in arm_rows if str(a.get("architecture_id")) != A0]
    best_info = sorted(others, key=increment_sort_key)[0] if others else None
    current_net = float(base_pack.get("net_pnl_yen_100") or 0.0)
    current_pf = _pf_num(base_pack.get("profit_factor"))
    current_dd = float(base_pack.get("max_drawdown_yen_100") or 0.0)
    decision = decide_case(
        integrity_ok=True,
        arms=arm_rows,
        current_net=current_net,
        current_pf=current_pf,
        current_dd=current_dd,
    )
    by = {str(a.get("architecture_id")): a for a in arm_rows}
    required = {
        "BASE_PARITY": True,
        "ARM_N": ARM_N,
        "OUTER_FOLD_N": 18,
        **_req_arm(by.get(A0), "A0"),
        **_req_arm(by.get(A1), "A1"),
        **_req_arm(by.get(A2), "A2"),
        **_req_arm(by.get(A3), "A3"),
        **_req_arm(by.get(A4), "A4"),
        **_req_arm(by.get(A5), "A5"),
        "PASS_ARM_N": len(pass_arms),
        "BEST_PASS_ARM": (best_pass or {}).get("architecture_id") if pass_arms else None,
        "BEST_PASS_NET": (best_pass or {}).get("OVERLAY_NET_PNL") if pass_arms else None,
        "BEST_PASS_PF": (best_pass or {}).get("OVERLAY_PF") if pass_arms else None,
        "BEST_PASS_DD": (best_pass or {}).get("OVERLAY_MAX_DD") if pass_arms else None,
        "BEST_PASS_PAIRED_MEDIAN": (best_pass or {}).get("PAIRED_MEDIAN_DAILY_DELTA") if pass_arms else None,
        "BEST_PASS_EX_BEST": (best_pass or {}).get("EX_BEST_DAY_PNL_DELTA") if pass_arms else None,
        "BEST_PASS_EX_TOP3": (best_pass or {}).get("EX_TOP3_DAYS_PNL_DELTA") if pass_arms else None,
        "BEST_INFORMATION_ARM_VS_A0": (best_info or {}).get("architecture_id"),
        "TEMPORAL_INFORMATION_INCREMENTAL": bundle_incremental(arm_rows, TEMPORAL_ARMS),
        "MARKET_REGIME_INFORMATION_INCREMENTAL": bundle_incremental(arm_rows, REGIME_ARMS),
        "CORE_STATE_INFORMATION_INCREMENTAL": bundle_incremental(arm_rows, CORE_ARMS),
        "CURRENT_PRESERVATION_PASS_ALL": bool(preservation_all),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "PRECOMMIT_SPEC_SHA256": sha,
        "DEVELOPMENT_CANDIDATE": (best_pass or {}).get("architecture_id") if pass_arms else None,
        "CASE": decision.get("CASE"),
    }

    rows_by_key = {row_key(r): r for r in rows}
    diag_feats = list((*TEMPORAL_FEATURES, *REGIME_FEATURES, *CORE_STATE_FEATURES))
    diagnostics = a0_trade_diagnostics(list(a0_rec.get("augment_trades") or []), rows_by_key, diag_feats)
    miss_rows = missingness_lodo(rows, diag_feats, list(ELIGIBLE_DAYS))

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
        {
            "architecture_id": a.get("architecture_id"),
            "DELTA_NET_VS_X14_BASE": a.get("DELTA_NET_VS_X14_BASE"),
            "DELTA_PF_VS_X14_BASE": a.get("DELTA_PF_VS_X14_BASE"),
            "DELTA_DD_VS_X14_BASE": a.get("DELTA_DD_VS_X14_BASE"),
            "DELTA_PAIRED_MEDIAN_VS_X14_BASE": a.get("DELTA_PAIRED_MEDIAN_VS_X14_BASE"),
            "DELTA_POS_MINUS_NEG_DAYS_VS_X14_BASE": a.get("DELTA_POS_MINUS_NEG_DAYS_VS_X14_BASE"),
            "DELTA_EX_BEST_VS_X14_BASE": a.get("DELTA_EX_BEST_VS_X14_BASE"),
            "DELTA_EX_TOP3_VS_X14_BASE": a.get("DELTA_EX_TOP3_VS_X14_BASE"),
        }
        for a in arm_rows
        if str(a.get("architecture_id")) != A0
    ]
    extra = {
        "precommit": spec,
        "decision": decision,
        "classes": class_n,
        "state_meta": state_meta,
        "current": _slim(base_pack),
        "arms": arm_sheet,
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
        "arms": arm_sheet,
        "increment": inc_sheet,
        "diagnostics": diagnostics,
        "missingness": miss_rows,
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
