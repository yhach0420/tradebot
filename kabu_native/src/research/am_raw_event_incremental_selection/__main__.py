"""Offline CURRENT-preserving raw-event incremental selection. No Runtime write. No Paper."""
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
from research.am_entry_temporal_regime_information.cohort_state import attach_all_cohort_state
from research.am_expanded_entry_risk_integration.analyze import profit_concentration
from research.am_expanded_entry_risk_integration.ensemble import joint_map
from research.am_raw_event_incremental_selection import (
    A3_REUSE_ARM,
    A5_REUSE_ARM,
    ANALYSIS_ID,
    ARM_IDS,
    ARM_N,
    AUGMENT_MAX_PER_COHORT,
    B0,
    B0_EXPECTED,
    B1,
    B1_EXPECTED,
    CURRENT_PRIORITY,
    FROZEN_ARM,
    R0,
    R1,
    RAW_EVENT_AVAILABLE,
    RAW_WINDOW_SEC,
)
from research.am_raw_event_incremental_selection.analyze import (
    decide_case,
    expected_match,
    increment_raw_vs_base,
    pair_incremental_supported,
    pick_best,
)
from research.am_raw_event_incremental_selection.join import join_raw
from research.am_raw_event_incremental_selection.models import missingness_raw
from research.am_raw_event_incremental_selection.oof import arm_spec_grid, process_oof_scores
from research.am_raw_event_incremental_selection.precommit import (
    extra_features_for,
    precommit_spec,
    print_precommit,
    spec_sha256,
)
from research.am_raw_event_incremental_selection.publish import OUT, build_markdown, write_artifacts
from research.am_utility_augment_execution_risk.analyze import preservation_pass
from research.canonical_entry_performance_rebase.analyze import session_of
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.passive_wait_policy_reassessment.analyze import _close
from research.raw_event_prediction_probe import RAW_DESCRIPTOR_N, RAW_DESCRIPTORS
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
REGIME_LABELED = (
    NATIVE / "results" / "research" / "_work_cache" / "am_entry_temporal_regime_information" / "labeled_am_regime.json"
)
REGIME_PROBA = NATIVE / "results" / "research" / "_work_cache" / "am_entry_temporal_regime_information"
RAW_HARVEST = NATIVE / "results" / "research" / "_work_cache" / "raw_event_information_audit"
CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_raw_event_incremental_selection"

REUSE_ARM = {B0: A3_REUSE_ARM, B1: A5_REUSE_ARM}

INTEGRITY_ZERO_KEYS = (
    "OUTER_HELDOUT_FIT_LEAK_N",
    "FUTURE_EVENT_USE_N",
    "EVENT_TIME_AFTER_T0_N",
    "SESSION_CARRY_N",
    "ITAYOSE_EVENT_USE_N",
    "SPECIAL_EVENT_USE_N",
    "TARGET_CONTAMINATION_N",
    "PM_ROWS_USED_N",
    "RAW_DESCRIPTOR_SEARCH_N",
    "RAW_FAMILY_SEARCH_N",
    "RAW_WINDOW_SEARCH_N",
    "FEATURE_SUBSET_SEARCH_N",
    "MODEL_SEARCH_N",
    "HYPERPARAMETER_SEARCH_N",
    "SCORE_THRESHOLD_SEARCH_N",
    "WAIT_SEARCH_N",
    "EXIT_CHANGE_N",
    "CURRENT_POLICY_CHANGE_N",
    "ORACLE_SELECTION_USE_N",
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "JOIN_MISS_N",
    "DATE_FEATURE_USE_N",
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


def _date_feature_n(names: list[str] | tuple[str, ...]) -> int:
    n = 0
    for f in names:
        s = str(f).lower()
        if "date" in s or "weekday" in s or s in {"dow", "calendar", "yyyymmdd"}:
            n += 1
    return n


def _empty_required(*, verdict: str, nxt: str, base_parity: bool = False) -> dict[str, Any]:
    req: dict[str, Any] = {
        "BASE_PARITY": base_parity,
        "RAW_DESCRIPTOR_N": int(RAW_DESCRIPTOR_N),
        "ARM_N": ARM_N,
        "OUTER_FOLD_N": 18,
        "PASS_ARM_N": 0,
        "BEST_PASS_ARM": None,
        "RAW_INCREMENTAL_SUPPORTED": False,
        "CURRENT_PRESERVATION_PASS_ALL": False,
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": verdict,
        "NEXT": nxt,
    }
    for prefix in ("B0", "R0", "B1", "R1"):
        for suffix in ("NET", "PF", "DD", "PAIRED_MEDIAN"):
            req[f"{prefix}_{suffix}"] = None
    for prefix in ("R0", "R1"):
        for suffix in ("DELTA_NET", "DELTA_PF", "DELTA_DD", "DELTA_PAIRED_MEDIAN", "DELTA_POS_MINUS_NEG", "DELTA_EX_TOP3"):
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


def _req_delta(inc: dict[str, Any] | None, prefix: str) -> dict[str, Any]:
    a = inc or {}
    return {
        f"{prefix}_DELTA_NET": a.get("DELTA_NET_RAW_VS_BASE"),
        f"{prefix}_DELTA_PF": a.get("DELTA_PF_RAW_VS_BASE"),
        f"{prefix}_DELTA_DD": a.get("DELTA_DD_RAW_VS_BASE"),
        f"{prefix}_DELTA_PAIRED_MEDIAN": a.get("DELTA_PAIRED_MEDIAN_RAW_VS_BASE"),
        f"{prefix}_DELTA_POS_MINUS_NEG": a.get("DELTA_POS_MINUS_NEG_RAW_VS_BASE"),
        f"{prefix}_DELTA_EX_TOP3": a.get("DELTA_EX_TOP3_RAW_VS_BASE"),
    }


def _integrity(msg: str, extra: dict | None = None, *, base_parity: bool = False) -> int:
    decision = decide_case(
        integrity_ok=False,
        base_parity=base_parity,
        arms=[],
        current_net=0.0,
        current_pf=0.0,
        current_dd=0.0,
        raw_incremental_supported=False,
    )
    required = _empty_required(
        verdict=str(decision.get("VERDICT") or "AM_RAW_EVENT_INCREMENTAL_REASSESSMENT_INTEGRITY_FAILED"),
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


def _load_harvest() -> tuple[list[dict[str, Any]], dict[str, int], str | None]:
    harvest: list[dict[str, Any]] = []
    tot = {
        "FUTURE_EVENT_USE_N": 0,
        "EVENT_TIME_AFTER_T0_N": 0,
        "SESSION_CARRY_N": 0,
        "ITAYOSE_EVENT_USE_N": 0,
        "SPECIAL_EVENT_USE_N": 0,
    }
    for day in ELIGIBLE_DAYS:
        fp = RAW_HARVEST / f"{day}_RAW.json"
        saved = _load(fp)
        if not (saved.get("ok") and saved.get("date") == day and saved.get("rows")):
            return [], tot, f"STOP. Missing raw-event harvest cache for {day}."
        harvest.extend(list(saved.get("rows") or []))
        tot["FUTURE_EVENT_USE_N"] += int(saved.get("future_event_use_n") or 0)
        tot["EVENT_TIME_AFTER_T0_N"] += int(saved.get("event_time_after_t0_n") or 0)
        tot["SESSION_CARRY_N"] += int(saved.get("session_carry_n") or 0)
        tot["ITAYOSE_EVENT_USE_N"] += int(saved.get("itayose_event_use_n") or 0)
        tot["SPECIAL_EVENT_USE_N"] += int(saved.get("special_event_use_n") or 0)
        print(f"RAW cache-hit {day} rows={len(saved.get('rows') or [])}", flush=True)
    return harvest, tot, None


def main() -> int:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    os.environ.setdefault("OMP_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get(
        "PYTHONPATH", ""
    )
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM RAW EVENT INCREMENTAL SELECTION V2", flush=True)
    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        return _integrity("STOP. Runtime WAIT_SEC drifted from 1.0.")
    if abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        return _integrity("STOP. DEV_WAIT_SEC drifted from 5.0.")
    if abs(float(RAW_WINDOW_SEC) - 180.0) > 1e-12:
        return _integrity("STOP. RAW window drifted from 180s.")
    if len(RAW_DESCRIPTORS) != int(RAW_DESCRIPTOR_N):
        return _integrity("STOP. RAW_DESCRIPTORS drifted.")
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
    if int(ARM_N) != 4 or list(ARM_IDS) != [B0, R0, B1, R1]:
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

    if REGIME_LABELED.is_file():
        rows = list(_load(REGIME_LABELED).get("rows") or [])
        print(f"regime labeled cache-hit n={len(rows)}", flush=True)
    else:
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

    harvest, event_leak, harvest_err = _load_harvest()
    if harvest_err:
        return _integrity(harvest_err)
    rows, join_meta = join_raw(rows, harvest)
    print(
        f"join miss={join_meta.get('JOIN_MISS_N')} available={join_meta.get('RAW_EVENT_AVAILABLE_N')} "
        f"zero_event={join_meta.get('RAW_EVENT_ZERO_N')} labeled={join_meta.get('LABELED_N')}",
        flush=True,
    )

    CACHE.mkdir(parents=True, exist_ok=True)
    rows_path = CACHE / "labeled_am_raw.json"
    _save_json(
        rows_path,
        {
            "session": "AM",
            "target": TARGET,
            "raw_descriptors": list(RAW_DESCRIPTORS),
            "rows": rows,
            "join": join_meta,
        },
    )

    date_n = _date_feature_n(extra_features_for(R1))
    leak = {
        "OUTER_HELDOUT_FIT_LEAK_N": 0,
        "FUTURE_EVENT_USE_N": int(event_leak.get("FUTURE_EVENT_USE_N") or 0),
        "EVENT_TIME_AFTER_T0_N": int(event_leak.get("EVENT_TIME_AFTER_T0_N") or 0),
        "SESSION_CARRY_N": int(event_leak.get("SESSION_CARRY_N") or 0),
        "ITAYOSE_EVENT_USE_N": int(event_leak.get("ITAYOSE_EVENT_USE_N") or 0),
        "SPECIAL_EVENT_USE_N": int(event_leak.get("SPECIAL_EVENT_USE_N") or 0),
        "TARGET_CONTAMINATION_N": 0,
        "PM_ROWS_USED_N": 0,
        "JOIN_MISS_N": int(join_meta.get("JOIN_MISS_N") or 0),
        "RAW_DESCRIPTOR_SEARCH_N": 0,
        "RAW_FAMILY_SEARCH_N": 0,
        "RAW_WINDOW_SEARCH_N": 0,
        "FEATURE_SUBSET_SEARCH_N": 0,
        "FEATURE_SEARCH_N": FEATURE_SEARCH_N,
        "DATE_FEATURE_USE_N": int(date_n),
        "MODEL_SEARCH_N": 0,
        "HYPERPARAMETER_SEARCH_N": 0,
        "SCORE_THRESHOLD_SEARCH_N": 0,
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
        "BASE_PARITY": False,
    }
    if date_n:
        return _integrity("STOP. Date/weekday feature leaked into extra bundle.", extra={"integrity": leak})
    if int(leak["JOIN_MISS_N"] or 0) != 0:
        return _integrity("STOP. Raw harvest join miss.", extra={"integrity": leak, "join": join_meta})
    event_keys = (
        "FUTURE_EVENT_USE_N",
        "EVENT_TIME_AFTER_T0_N",
        "SESSION_CARRY_N",
        "ITAYOSE_EVENT_USE_N",
        "SPECIAL_EVENT_USE_N",
    )
    if any(int(leak.get(k) or 0) != 0 for k in event_keys):
        return _integrity("STOP. Raw event window integrity counters non-zero.", extra={"integrity": leak})

    jobs = []
    allowed = set(ARM_IDS)
    for arm_id in ARM_IDS:
        specs = arm_spec_grid(arm_id)
        if len(specs) != int(REPRESENTATION_N):
            return _integrity("STOP. Representation count drifted.", extra={"arm": arm_id})
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
            reuse_arm = REUSE_ARM.get(arm_id)
            if reuse_arm:
                job["reuse_cache_path"] = str(
                    REGIME_PROBA / f"{reuse_arm}_{rid.replace('|', '_')}_oof_proba.json"
                )
            jobs.append(job)
    print(f"oof jobs={len(jobs)} workers={min(MAX_WORKERS, len(jobs))}", flush=True)
    got = _pool(process_oof_scores, jobs, "OOF", "spec_id")
    fail = [b for b in got if not b.get("ok")]
    if fail or len(got) != len(jobs):
        return _integrity(
            "STOP. OOF probability replay failed.",
            extra={"fail": [(b.get("spec_id"), b.get("blocker")) for b in fail]},
            base_parity=False,
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
        leak["RAW_DESCRIPTOR_SEARCH_N"] += int(ig.get("RAW_DESCRIPTOR_SEARCH_N") or 0)
        leak["RAW_FAMILY_SEARCH_N"] += int(ig.get("RAW_FAMILY_SEARCH_N") or 0)
        leak["RAW_WINDOW_SEARCH_N"] += int(ig.get("RAW_WINDOW_SEARCH_N") or 0)
        if int(b.get("fold_n") or 0) != 18 and not b.get("reused_from"):
            if int(b.get("fold_n") or 0) != 18:
                leak["OUTER_HELDOUT_FIT_LEAK_N"] += 1
        sample = next(iter((b.get("scores") or {}).values()), None)
        if not (isinstance(sample, dict) and "P_WIN" in (sample or {})):
            return _integrity("STOP. OOF cache missing class probabilities.", extra={"spec_id": b.get("spec_id")})
        by_arm.setdefault(aid, {})[str(b.get("representation_id"))] = dict(b.get("scores") or {})
    if leak["POSTHOC_MODEL_ADDITION_N"]:
        return _integrity("STOP. Post-hoc model addition.", extra={"integrity": leak})
    for arm_id in ARM_IDS:
        if len(by_arm.get(arm_id) or {}) != int(REPRESENTATION_N):
            return _integrity("STOP. Missing representation OOF.", extra={"arm": arm_id})

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

    arm_rows = []
    for arm_id in ARM_IDS:
        specs = arm_spec_grid(arm_id)
        rep_ids = [str(s.get("representation_id")) for s in specs]
        tagged = attach_ensemble(rows, joint_map(by_arm[arm_id]), rep_ids)
        rec = _eval_arm(tagged, baseline, base_pack, arm_id)
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
        return _integrity(
            "STOP. B0/B1 did not reproduce frozen A3/A5 economics.",
            extra={
                "integrity": leak,
                "b0": {k: v for k, v in (by.get(B0) or {}).items() if k not in ("overlay_daily", "augment_daily", "preservation", "gates", "augment_trades")},
                "b1": {k: v for k, v in (by.get(B1) or {}).items() if k not in ("overlay_daily", "augment_daily", "preservation", "gates", "augment_trades")},
            },
            base_parity=False,
        )

    r0_inc = increment_raw_vs_base(by.get(R0) or {}, by.get(B0) or {})
    r1_inc = increment_raw_vs_base(by.get(R1) or {}, by.get(B1) or {})
    by[R0].update(r0_inc)
    by[R1].update(r1_inc)
    raw_inc = pair_incremental_supported(by[R0], by[B0]) or pair_incremental_supported(by[R1], by[B1])

    preservation_all = all(bool(a.get("CURRENT_PRESERVATION_PASS")) for a in arm_rows)
    pass_arms = [a for a in arm_rows if bool(a.get("ARM_PASS"))]
    raw_pass = [a for a in pass_arms if str(a.get("architecture_id")) in {R0, R1}]
    best_pass = pick_best(raw_pass) if raw_pass else (pick_best(pass_arms) if pass_arms else None)
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
        raw_incremental_supported=bool(raw_inc),
    )
    required = {
        "BASE_PARITY": True,
        "RAW_DESCRIPTOR_N": int(RAW_DESCRIPTOR_N),
        "ARM_N": ARM_N,
        "OUTER_FOLD_N": 18,
        **_req_arm(by.get(B0), "B0"),
        **_req_arm(by.get(R0), "R0"),
        **_req_arm(by.get(B1), "B1"),
        **_req_arm(by.get(R1), "R1"),
        **_req_delta(r0_inc, "R0"),
        **_req_delta(r1_inc, "R1"),
        "PASS_ARM_N": len(pass_arms),
        "BEST_PASS_ARM": (best_pass or {}).get("architecture_id") if pass_arms else None,
        "RAW_INCREMENTAL_SUPPORTED": bool(raw_inc),
        "CURRENT_PRESERVATION_PASS_ALL": bool(preservation_all),
        "TRUE_OOS": TRUE_OOS,
        "NEW_FORWARD_N": NEW_FORWARD_N,
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "PRECOMMIT_SPEC_SHA256": sha,
        "CASE": decision.get("CASE"),
        "RAW_DESCRIPTORS": list(RAW_DESCRIPTORS),
        "R0_INCREMENTAL_VS_B0": pair_incremental_supported(by[R0], by[B0]),
        "R1_INCREMENTAL_VS_B1": pair_incremental_supported(by[R1], by[B1]),
    }

    miss_rows = missingness_raw(rows, list(extra_features_for(R1)), list(ELIGIBLE_DAYS))
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
        {"pair": "R0-B0", **r0_inc},
        {"pair": "R1-B1", **r1_inc},
    ]
    extra = {
        "precommit": spec,
        "decision": decision,
        "classes": class_n,
        "join": join_meta,
        "current": _slim(base_pack),
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
        "RAW_DESCRIPTORS": list(RAW_DESCRIPTORS),
        "RAW_EVENT_AVAILABLE": RAW_EVENT_AVAILABLE,
        "RAW_WINDOW_SEC": RAW_WINDOW_SEC,
    }
    sheets = {
        "precommit": kv_rows({**spec, "PRECOMMIT_SPEC_SHA256": sha, "PRECOMMIT_LOCKED": True}),
        "raw_descriptors": [{"i": i + 1, "name": n} for i, n in enumerate(RAW_DESCRIPTORS)]
        + [{"i": 24, "name": RAW_EVENT_AVAILABLE, "note": "availability binary, not a searched descriptor"}],
        "arms": arm_sheet,
        "increment": inc_sheet,
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
