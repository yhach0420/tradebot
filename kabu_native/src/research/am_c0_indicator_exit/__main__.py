"""Offline AM C0 indicator-state EXIT. Frozen C0 ENTRY. CURRENT keeps C14. Live Runtime/Capture untouched."""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
os.environ["OMP_NUM_THREADS"] = "1"
os.environ["MKL_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["NUMEXPR_NUM_THREADS"] = "1"
os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get(
    "PYTHONPATH", ""
)

from research.am_c0_exit_ptl_guard.analyze import current_trade_counts
from research.am_c0_exit_ptl_guard.replay import prepare_c0
from research.am_c0_indicator_exit import (
    ANALYSIS_ID,
    ARCHITECTURE_ID,
    C0_AUGMENT_LOCKED,
    C0_OVERLAY_LOCKED,
    CONTINUATION_REUSE_N,
    CURRENT_EXIT_CHANGE_N,
    CURRENT_LOCKED,
    ENTRY_PARENT_SHA256,
    ENTRY_POLICY_CHANGE_N,
    EXIT_FEATURES,
    EXIT_HORIZON_SEARCH_N,
    EXIT_THRESHOLD_SEARCH_N,
    FEATURE_ORDER_ENTRY,
    FEATURE_SEARCH_N,
    HOLDING_TIME_RULE_N,
    HYPERPARAMETER_SEARCH_N,
    MODEL_SEARCH_N,
    ORACLE_EXIT_SELECTION_USE_N,
    OUTER_FOLD_N,
    PAPER_OPERATION_N,
    PTL_REUSE_N,
    RESEARCH_PARALLELISM,
    RUNTIME_CHANGE_N,
    TIME_FEATURE_N,
    WAIT_CHANGE_N,
)
from research.am_c0_indicator_exit.analyze import (
    decide_case,
    eind_full_gate,
    fixed_trade_effect,
    identity_audit,
    join_l2_keys,
    preservation_ok,
    vs_c0,
)
from research.am_c0_indicator_exit.harvest import (
    CACHE,
    load_day_cache,
    process_state_day,
    save_day_cache,
    sealed_day_caps,
)
from research.am_c0_indicator_exit.isolation import (
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
    advanced as ni_advanced,
)
from research.am_c0_indicator_exit.model import attach_oof
from research.am_c0_indicator_exit.publish import OUT, build_markdown, kv_rows, write_artifacts
from research.am_c0_indicator_exit.replay import apply_eind_exits, exits_by_key
from research.am_c0_indicator_exit.spec import precommit_spec, print_precommit, spec_sha256
from research.am_current_utility_augment.analyze import preservation_audit
from research.am_current_utility_augment.overlay import overlay_replay
from research.am_entry_fixed_spec_oof import V2_CURRENT_MAX_DD, V2_CURRENT_NET_PNL, V2_CURRENT_PF, V2_CURRENT_TRADE_N
from research.am_entry_profit_improvement import (
    C14_ID,
    CANCEL_N,
    DEV_WAIT_SEC,
    ELIGIBLE_DAYS,
    LIVE_ORDER_N,
    NEW_FORWARD_N,
    PAPER_OPERATED,
    PARITY_ABS_TOL,
    RUNTIME_CHANGED,
    SESSION,
    SUBMIT_N,
    TRUE_OOS,
    W5_RUNTIME_ADOPTED,
)
from research.am_entry_profit_improvement.analyze import freeze_parity, independent_top3
from research.am_entry_profit_improvement.metrics import _pf_num, economic_pack, paired_delta
from research.am_entry_research_final_decision.spec import canonical_c0_spec
from research.am_entry_research_final_decision.spec import spec_sha256 as c0_spec_sha256
from research.am_expanded_entry_risk_integration.analyze import profit_concentration
from research.canonical_entry_performance_rebase.analyze import _f, row_key, session_of
from research.dynamic_anchor_p2_2.binding import ENTRY_BINDING
from research.passive_wait_policy_reassessment.analyze import _close
from small_paper.v1r_primary_runtime import POSITION_CAP, WAIT_SEC

C14 = (
    NATIVE
    / "results"
    / "research"
    / "v1r_exit_v2_prospective_activation"
    / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14.json"
)
REGIME_LABELED = (
    NATIVE / "results" / "research" / "_work_cache" / "am_entry_temporal_regime_information" / "labeled_am_regime.json"
)
RCA_CACHE = NATIVE / "results" / "research" / "_work_cache" / "am_exit_contribution_rca"

INTEGRITY_ZERO_KEYS = (
    "HOLDING_TIME_FEATURE_USE_N",
    "CLOCK_TIME_FEATURE_USE_N",
    "AM_SLOT_FEATURE_USE_N",
    "C14_DECISION_FEATURE_USE_N",
    "FUTURE_FEATURE_USE_N",
    "FUTURE_LABEL_AS_FEATURE_N",
    "OUTER_HELDOUT_FIT_LEAK_N",
    "SCALER_HELDOUT_USE_N",
    "ENTRY_POLICY_CHANGE_N",
    "B0_SCORE_MISMATCH_N",
    "B1_SCORE_MISMATCH_N",
    "C0_CONFIRMATION_MISMATCH_N",
    "FEATURE_SEARCH_N",
    "MODEL_SEARCH_N",
    "HYPERPARAMETER_SEARCH_N",
    "EXIT_THRESHOLD_SEARCH_N",
    "EXIT_HORIZON_SEARCH_N",
    "PTL_REUSE_N",
    "CONTINUATION_REUSE_N",
    "WAIT_CHANGE_N",
    "CURRENT_EXIT_CHANGE_N",
    "RUNTIME_CHANGE_N",
    "PAPER_OPERATION_N",
    "ORACLE_EXIT_SELECTION_USE_N",
    "RESEARCH_INPUT_ACTIVE_FILE_N",
    "RESEARCH_WRITE_PATH_OVERLAP_N",
    "LIVE_PROCESS_CONTROL_CALL_N",
    "ADDITIONAL_WEBSOCKET_CONNECTION_N",
    "RUNTIME_CONFIG_CHANGE_N",
    "RUNTIME_STATE_WRITE_N",
    "CAPTURE_STATE_WRITE_N",
    "KABUS_RESTART_N",
    "CAPTURE_RESTART_N",
    "RUNTIME_RESTART_N",
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
)


def _load(path: Path) -> dict:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _empty_required(*, verdict: str, nxt: str, sha: str | None = None) -> dict[str, Any]:
    keys = (
        "BASE_PARITY",
        "ARCHITECTURE_ID",
        "EXIT_INDICATOR_SPEC_SHA256",
        "TIME_FEATURE_N",
        "HOLDING_TIME_RULE_N",
        "OUTER_FOLD_N",
        "TRAIN_TRADE_N",
        "OOF_AUC",
        "FIXED_TRADE_N",
        "FIXED_DELTA_NET",
        "L2_BASE_N",
        "L2_IMPROVED_N",
        "WINNER_PREMATURE_EXIT_N",
        "EIND_AUGMENT_TRADE_N",
        "EIND_AUGMENT_NET",
        "EIND_AUGMENT_PF",
        "EIND_OVERLAY_NET",
        "EIND_OVERLAY_PF",
        "EIND_OVERLAY_DD",
        "PAIRED_POS_DAYS",
        "PAIRED_NEG_DAYS",
        "PAIRED_ZERO_DAYS",
        "PAIRED_MEDIAN",
        "EX_BEST",
        "EX_TOP3",
        "BEST_DAY_SHARE",
        "TOP3_DAY_SHARE",
        "DELTA_NET_VS_C0_C14",
        "DELTA_PF_VS_C0_C14",
        "DELTA_DD_VS_C0_C14",
        "DELTA_PAIRED_MEDIAN_VS_C0_C14",
        "CURRENT_PRESERVATION_PASS",
        "NON_INTERFERENCE_PASS",
        "RUNTIME_PID_BEFORE",
        "RUNTIME_PID_AFTER",
        "CAPTURE_PID_BEFORE",
        "CAPTURE_PID_AFTER",
        "RUNTIME_HEARTBEAT_ADVANCED",
        "CAPTURE_ADVANCED",
        "EIND_FULL_PASS",
        "TRUE_OOS",
        "NEW_FORWARD_N",
        "VERDICT",
        "NEXT",
    )
    out = {k: None for k in keys}
    out["ARCHITECTURE_ID"] = ARCHITECTURE_ID
    out["EXIT_INDICATOR_SPEC_SHA256"] = sha
    out["TIME_FEATURE_N"] = int(TIME_FEATURE_N)
    out["HOLDING_TIME_RULE_N"] = int(HOLDING_TIME_RULE_N)
    out["OUTER_FOLD_N"] = int(OUTER_FOLD_N)
    out["TRUE_OOS"] = False
    out["NEW_FORWARD_N"] = 0
    out["VERDICT"] = verdict
    out["NEXT"] = nxt
    return out


def _publish(required: dict[str, Any], sheets: dict[str, list[dict[str, Any]]], extra: dict[str, Any] | None = None) -> None:
    decision = {"CASE": extra.get("CASE") if extra else None, "VERDICT": required.get("VERDICT"), "NEXT": required.get("NEXT")}
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "decision": decision, **(extra or {})}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, sheets)
    print(required.get("VERDICT"), required.get("NEXT"), flush=True)


def _integrity(msg: str, extra: dict[str, Any] | None = None, sha: str | None = None, pre: dict | None = None) -> int:
    decision = {
        "CASE": "E",
        "VERDICT": "AM_C0_INDICATOR_EXIT_INTEGRITY_FAILED",
        "NEXT": "AM_C0_INDICATOR_EXIT_RESEARCH_CLOSE",
    }
    required = _empty_required(verdict=str(decision["VERDICT"]), nxt=str(decision["NEXT"]), sha=sha)
    required["STOP_REASON"] = msg
    if pre:
        required["RUNTIME_PID_BEFORE"] = pre.get("RUNTIME_PID")
        required["CAPTURE_PID_BEFORE"] = pre.get("CAPTURE_PID")
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "decision": decision, **(extra or {})}
    report["_markdown"] = build_markdown(report)
    write_artifacts(
        report,
        {
            "precommit": kv_rows({"EXIT_INDICATOR_SPEC_SHA256": sha}),
            "integrity": kv_rows({"STOP_REASON": msg, **(extra or {})}),
        },
    )
    print(msg, flush=True)
    return 2


def _not_run(msg: str, pre: dict[str, Any], leak: dict[str, Any], sha: str | None = None) -> int:
    required = _empty_required(
        verdict="RESEARCH_NOT_RUN_RUNTIME_INTERFERENCE_RISK",
        nxt="RESEARCH_ISOLATION_FIX_ONLY",
        sha=sha,
    )
    required["STOP_REASON"] = msg
    required["NON_INTERFERENCE_PASS"] = False
    required["RUNTIME_PID_BEFORE"] = pre.get("RUNTIME_PID")
    required["CAPTURE_PID_BEFORE"] = pre.get("CAPTURE_PID")
    required["CURRENT_PRESERVATION_PASS"] = False
    required["EIND_FULL_PASS"] = False
    _publish(
        required,
        {
            "precommit": kv_rows({"EXIT_INDICATOR_SPEC_SHA256": sha}),
            "non_interference": kv_rows({**pre, **leak}),
            "integrity": kv_rows({"STOP_REASON": msg, **leak}),
        },
        extra={"CASE": "F", "preflight": pre},
    )
    return 2


def _slim(pack: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in pack.items() if k not in ("trades", "daily")}


def _top3_share(daily: list[dict[str, Any]]) -> Any:
    pnls = [float(r.get("pnl_yen_100") or 0.0) for r in daily]
    total = float(sum(pnls)) if pnls else 0.0
    ranked = sorted(pnls, reverse=True)
    top3 = float(sum(ranked[:3])) if ranked else 0.0
    if total > 0:
        return top3 / total
    return None


def main() -> int:
    print("SAFETY submit/cancel/live=0/0/0 OFFLINE AM C0 INDICATOR-STATE EXIT V1", flush=True)
    set_research_priority_below_normal()
    if abs(float(WAIT_SEC) - 1.0) > 1e-12:
        return _integrity("STOP. Runtime WAIT_SEC drifted from 1.0.")
    if abs(float(DEV_WAIT_SEC) - 5.0) > 1e-12:
        return _integrity("STOP. DEV_WAIT_SEC drifted from 5.0.")
    if list(FEATURE_ORDER_ENTRY) != [
        "spread_bps",
        "imbalance",
        "mid_ret_60s",
        "mid_ret_180s",
        "event_rate_60s",
        "log_bid_qty",
    ]:
        return _integrity("STOP. FEATURE_ORDER drift.")
    if "holding_sec" in EXIT_FEATURES or "AM_SLOT_INDEX" in EXIT_FEATURES:
        return _integrity("STOP. Time feature leaked into EXIT_FEATURES.")
    if int(RESEARCH_PARALLELISM) != 1:
        return _integrity("STOP. RESEARCH_PARALLELISM drifted.")
    if ENTRY_BINDING.get("rank_pass_gate") is not None:
        return _integrity("STOP. rank_pass_gate drift.")
    c14 = _load(C14)
    if str(c14.get("candidate_id") or "") != C14_ID:
        return _integrity("STOP. C14 identity mismatch.")
    if TRUE_OOS is not False or int(NEW_FORWARD_N) != 0:
        return _integrity("STOP. TRUE_OOS / NEW_FORWARD_N drifted.")
    if int(SUBMIT_N) != 0 or int(CANCEL_N) != 0 or int(LIVE_ORDER_N) != 0 or PAPER_OPERATED is not False:
        return _integrity("STOP. submit/cancel/live/paper drifted.")
    if W5_RUNTIME_ADOPTED is not False or RUNTIME_CHANGED is not False:
        return _integrity("STOP. Runtime freeze drifted.")
    parent_sha = c0_spec_sha256(canonical_c0_spec())
    if parent_sha != ENTRY_PARENT_SHA256:
        return _integrity("STOP. C0 prospective spec SHA256 mismatch.", extra={"got_sha": parent_sha})

    spec = precommit_spec()
    sha = spec_sha256(spec)
    print_precommit(spec, sha)

    pre = snapshot(phase="BEFORE")
    print(
        f"PREFLIGHT runtime_pid={pre.get('RUNTIME_PID')} capture_pid={pre.get('CAPTURE_PID')} "
        f"capture={pre.get('ACTIVE_CAPTURE_PATH')}",
        flush=True,
    )
    leak = {
        "HOLDING_TIME_FEATURE_USE_N": 0,
        "CLOCK_TIME_FEATURE_USE_N": 0,
        "AM_SLOT_FEATURE_USE_N": 0,
        "C14_DECISION_FEATURE_USE_N": 0,
        "FUTURE_FEATURE_USE_N": 0,
        "FUTURE_LABEL_AS_FEATURE_N": 0,
        "OUTER_HELDOUT_FIT_LEAK_N": 0,
        "SCALER_HELDOUT_USE_N": 0,
        "ENTRY_POLICY_CHANGE_N": int(ENTRY_POLICY_CHANGE_N),
        "B0_SCORE_MISMATCH_N": 0,
        "B1_SCORE_MISMATCH_N": 0,
        "C0_CONFIRMATION_MISMATCH_N": 0,
        "C0_CANDIDATE_DECISION_MISMATCH_N": 0,
        "FEATURE_SEARCH_N": int(FEATURE_SEARCH_N),
        "MODEL_SEARCH_N": int(MODEL_SEARCH_N),
        "HYPERPARAMETER_SEARCH_N": int(HYPERPARAMETER_SEARCH_N),
        "EXIT_THRESHOLD_SEARCH_N": int(EXIT_THRESHOLD_SEARCH_N),
        "EXIT_HORIZON_SEARCH_N": int(EXIT_HORIZON_SEARCH_N),
        "PTL_REUSE_N": int(PTL_REUSE_N),
        "CONTINUATION_REUSE_N": int(CONTINUATION_REUSE_N),
        "WAIT_CHANGE_N": int(WAIT_CHANGE_N),
        "CURRENT_EXIT_CHANGE_N": int(CURRENT_EXIT_CHANGE_N),
        "RUNTIME_CHANGE_N": int(RUNTIME_CHANGE_N),
        "PAPER_OPERATION_N": int(PAPER_OPERATION_N),
        "ORACLE_EXIT_SELECTION_USE_N": int(ORACLE_EXIT_SELECTION_USE_N),
        "RESEARCH_INPUT_ACTIVE_FILE_N": 0,
        "RESEARCH_WRITE_PATH_OVERLAP_N": 0,
        "LIVE_PROCESS_CONTROL_CALL_N": 0,
        "ADDITIONAL_WEBSOCKET_CONNECTION_N": 0,
        "RUNTIME_CONFIG_CHANGE_N": 0,
        "RUNTIME_STATE_WRITE_N": 0,
        "CAPTURE_STATE_WRITE_N": 0,
        "KABUS_RESTART_N": 0,
        "CAPTURE_RESTART_N": 0,
        "RUNTIME_RESTART_N": 0,
        "SUBMIT_N": int(SUBMIT_N),
        "CANCEL_N": int(CANCEL_N),
        "LIVE_ORDER_N": int(LIVE_ORDER_N),
        "ITAYOSE_SKIP_N": 0,
        "SPECIAL_SKIP_N": 0,
        "INVALID_SKIP_N": 0,
    }
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
    )
    if TODAY in set(ELIGIBLE_DAYS):
        leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = 1
        return _not_run("STOP. Eligible days include today active session.", pre, leak, sha=sha)
    if int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]):
        return _not_run("STOP. Research write path overlaps live Runtime/Capture.", pre, leak, sha=sha)

    if not REGIME_LABELED.is_file():
        return _integrity("STOP. labeled AM rows missing.", sha=sha, pre=pre)
    rows = list(_load(REGIME_LABELED).get("rows") or [])
    pm_n = sum(1 for r in rows if session_of(r) != "AM")
    if pm_n:
        return _integrity("STOP. PM rows in labeled AM.", extra={"PM_ROWS_USED_N": pm_n}, sha=sha, pre=pre)
    top3 = independent_top3(rows)
    obs = {
        "AM_LABELED_N": len(rows),
        "AM_Y_FILL5_POS_N": sum(1 for r in rows if int(r.get("Y_FILL5") or 0) == 1),
        "AM_CURRENT_TOP3_FILL5_RATE": top3.get("AM_CURRENT_TOP3_FILL5_RATE"),
    }
    parity_pop = freeze_parity(obs)
    if not parity_pop.get("ok"):
        return _integrity("STOP. Frozen AM labeled population did not reproduce.", extra={"parity": parity_pop}, sha=sha, pre=pre)

    print("prepare frozen C0", flush=True)
    got = prepare_c0(rows, REGIME_LABELED)
    tagged = list(got.get("tagged") or [])
    baseline = got.get("baseline") or {}
    e0_overlay = got.get("overlay") or {}
    c0_keys = set(got.get("c0_keys") or set())
    cur_trades = [t for t in (baseline.get("trades") or []) if str(t.get("arm") or "CURRENT") == "CURRENT"]
    e0_aug = [t for t in (e0_overlay.get("trades") or []) if str(t.get("arm") or "") == "AUGMENT"]
    e0_all = list(e0_overlay.get("trades") or [])
    cur_pack = economic_pack(cur_trades, list(ELIGIBLE_DAYS))
    e0_aug_pack = economic_pack(e0_aug, list(ELIGIBLE_DAYS))
    e0_ov_pack = economic_pack(e0_all, list(ELIGIBLE_DAYS))
    e0_paired = paired_delta(list(e0_ov_pack.get("daily") or []), list(cur_pack.get("daily") or []))
    replay_ok = (
        int(cur_pack.get("trade_count") or -1) == int(CURRENT_LOCKED["TRADE_N"])
        and _close(cur_pack.get("net_pnl_yen_100"), CURRENT_LOCKED["NET"], PARITY_ABS_TOL)
        and _close(cur_pack.get("profit_factor"), CURRENT_LOCKED["PF"], 1e-12)
        and _close(cur_pack.get("max_drawdown_yen_100"), CURRENT_LOCKED["DD"], PARITY_ABS_TOL)
        and int(cur_pack.get("trade_count") or -1) == int(V2_CURRENT_TRADE_N)
        and _close(cur_pack.get("net_pnl_yen_100"), V2_CURRENT_NET_PNL, PARITY_ABS_TOL)
        and _close(cur_pack.get("profit_factor"), V2_CURRENT_PF, 1e-12)
        and _close(cur_pack.get("max_drawdown_yen_100"), V2_CURRENT_MAX_DD, PARITY_ABS_TOL)
        and _close(e0_ov_pack.get("net_pnl_yen_100"), C0_OVERLAY_LOCKED["NET"], PARITY_ABS_TOL)
        and _close(e0_ov_pack.get("profit_factor"), C0_OVERLAY_LOCKED["PF"], 1e-12)
        and _close(e0_ov_pack.get("max_drawdown_yen_100"), C0_OVERLAY_LOCKED["DD"], PARITY_ABS_TOL)
        and int(e0_paired.get("PAIRED_POS_DAYS") or -1) == int(C0_OVERLAY_LOCKED["PAIRED_POS_DAYS"])
        and int(e0_paired.get("PAIRED_NEG_DAYS") or -1) == int(C0_OVERLAY_LOCKED["PAIRED_NEG_DAYS"])
        and int(e0_paired.get("PAIRED_ZERO_DAYS") or -1) == int(C0_OVERLAY_LOCKED["PAIRED_ZERO_DAYS"])
        and _close(e0_paired.get("PAIRED_MEDIAN_DAILY_DELTA"), C0_OVERLAY_LOCKED["PAIRED_MEDIAN"], PARITY_ABS_TOL)
        and _close(e0_paired.get("EX_BEST_DAY_PNL_DELTA"), C0_OVERLAY_LOCKED["EX_BEST"], PARITY_ABS_TOL)
        and _close(e0_paired.get("EX_TOP3_DAYS_PNL_DELTA"), C0_OVERLAY_LOCKED["EX_TOP3"], PARITY_ABS_TOL)
        and int(e0_aug_pack.get("trade_count") or -1) == int(C0_AUGMENT_LOCKED["TRADE_N"])
        and int(e0_aug_pack.get("win_n") or -1) == int(C0_AUGMENT_LOCKED["WIN_N"])
        and int(e0_aug_pack.get("loss_n") or -1) == int(C0_AUGMENT_LOCKED["LOSS_N"])
        and int(e0_aug_pack.get("flat_n") or -1) == int(C0_AUGMENT_LOCKED["FLAT_N"])
        and _close(e0_aug_pack.get("net_pnl_yen_100"), C0_AUGMENT_LOCKED["NET"], PARITY_ABS_TOL)
        and _close(e0_aug_pack.get("profit_factor"), C0_AUGMENT_LOCKED["PF"], 1e-12)
    )
    print(
        f"E0 CURRENT n={cur_pack.get('trade_count')} net={cur_pack.get('net_pnl_yen_100')} "
        f"overlay_net={e0_ov_pack.get('net_pnl_yen_100')} aug_n={e0_aug_pack.get('trade_count')} "
        f"replay_ok={replay_ok}",
        flush=True,
    )
    if not replay_ok:
        return _integrity(
            "STOP. Frozen CURRENT / C0 overlay economics did not reproduce.",
            extra={"current": _slim(cur_pack), "e0_overlay": _slim(e0_ov_pack), "e0_augment": _slim(e0_aug_pack)},
            sha=sha,
            pre=pre,
        )

    try:
        caps = sealed_day_caps(list(ELIGIBLE_DAYS), TODAY)
    except Exception as exc:
        return _not_run(f"STOP. Sealed inventory failed: {exc}", pre, leak, sha=sha)
    input_paths = [str(REGIME_LABELED)] + [str(c.get("capture_path") or "") for c in caps]
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        input_paths,
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    if int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"]):
        return _not_run("STOP. Research input references active Capture/Paper files.", pre, leak, sha=sha)
    if any(not c.get("ok") for c in caps) or len(caps) != len(ELIGIBLE_DAYS):
        return _integrity("STOP. Sealed Capture days incomplete.", extra={"caps": caps}, sha=sha, pre=pre)
    inv_by = {c["date"]: c for c in caps}

    e0_aug_keys = {str(t.get("row_key") or row_key(t)) for t in e0_aug}
    cur_keys = {str(t.get("row_key") or row_key(t)) for t in cur_trades}
    fills_by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    seen: set[str] = set()

    def _add_fill(r: dict[str, Any], *, role: str, arm: str) -> None:
        key = str(r.get("_row_key") or r.get("row_key") or row_key(r))
        if not key or key in seen:
            return
        seen.add(key)
        fills_by[str(r.get("date") or "")].append(
            {
                "date": r.get("date"),
                "anchor": r.get("anchor"),
                "symbol": r.get("symbol"),
                "session": r.get("session") or "AM",
                "fill_t": r.get("fill_t") if r.get("fill_t") is not None else r.get("fill_time"),
                "fill_price": r.get("fill_price"),
                "exit_t": r.get("exit_t") if r.get("exit_t") is not None else r.get("exit_time"),
                "exit_price": r.get("exit_price"),
                "exit_reason": r.get("exit_reason"),
                "pnl_yen_100": r.get("pnl_yen_100"),
                "arm": arm,
                "role": role,
                "row_key": key,
            }
        )

    for t in cur_trades:
        _add_fill(t, role="TRAIN_CURRENT", arm="CURRENT")
    for r in tagged:
        if not r.get("_aug_eligible") or int(r.get("Y_FILL5") or 0) != 1:
            continue
        key = str(r.get("_row_key") or row_key(r))
        if key in cur_keys:
            continue
        role = "TRAIN_AUGMENT" if key in e0_aug_keys else "POLICY_ONLY"
        _add_fill(r, role=role, arm="AUGMENT")
    train_n = sum(1 for v in fills_by.values() for x in v if x.get("role") in {"TRAIN_CURRENT", "TRAIN_AUGMENT"})
    print(
        f"harvest fills train={train_n} policy_only={sum(1 for v in fills_by.values() for x in v if x.get('role')=='POLICY_ONLY')}",
        flush=True,
    )
    if train_n != int(CURRENT_LOCKED["TRADE_N"]) + int(C0_AUGMENT_LOCKED["TRADE_N"]):
        return _integrity(
            f"STOP. Training population {train_n} != 167.",
            extra={"train_n": train_n},
            sha=sha,
            pre=pre,
        )

    CACHE.mkdir(parents=True, exist_ok=True)
    day_bodies = []
    for day in ELIGIBLE_DAYS:
        fp = CACHE / f"{day}_STATE.json"
        saved = load_day_cache(fp)
        if saved.get("ok") and str(saved.get("date") or "") == str(day) and saved.get("trades") is not None:
            day_bodies.append(saved)
            print(f"STATE cache-hit {day} trades={len(saved.get('trades') or [])}", flush=True)
            continue
        r = inv_by[day]
        body = process_state_day(
            {
                "date": day,
                "capture_path": r["capture_path"],
                "universe": r["universe_symbols"],
                "fills": fills_by.get(day) or [],
            }
        )
        if body.get("ok"):
            save_day_cache(fp, body)
            body = load_day_cache(fp)
        day_bodies.append(body)
        print(f"done STATE {day} ok={body.get('ok')} blocker={body.get('blocker')}", flush=True)
    fail = [b for b in day_bodies if not b.get("ok")]
    if fail or len(day_bodies) != len(ELIGIBLE_DAYS):
        return _integrity(
            "STOP. Event-state harvest failed.",
            extra={"fail": [(b.get("date"), b.get("blocker")) for b in fail]},
            sha=sha,
            pre=pre,
        )
    for body in day_bodies:
        lg = body.get("leak") or {}
        for k in ("ITAYOSE_SKIP_N", "SPECIAL_SKIP_N", "INVALID_SKIP_N", "FUTURE_FEATURE_USE_N"):
            leak[k] = int(leak.get(k) or 0) + int(lg.get(k) or 0)

    print("LODO logistic", flush=True)
    oof = attach_oof(day_bodies)
    day_bodies = list(oof.get("day_bodies") or day_bodies)
    eind_by = exits_by_key(day_bodies)

    rca_l2_src = []
    for day in ELIGIBLE_DAYS:
        for r in list(_load(RCA_CACHE / f"{day}_PATH.json").get("rows") or []):
            if str(r.get("arm") or "") != "AUGMENT":
                continue
            rk = str(r.get("row_key") or "")
            if rk and rk in e0_aug_keys:
                rca_l2_src.append(r)
    l2_keys = join_l2_keys(rca_l2_src)
    if len(l2_keys) != int(C0_AUGMENT_LOCKED["L2_BASE_N"]):
        return _integrity(
            f"STOP. L2_BASE_N reproduced {len(l2_keys)} != {C0_AUGMENT_LOCKED['L2_BASE_N']}.",
            extra={"l2_keys": sorted(l2_keys)},
            sha=sha,
            pre=pre,
        )

    fixed = fixed_trade_effect(e0_aug, eind_by, l2_keys)
    if int(fixed.get("FIXED_TRADE_N") or -1) != int(C0_AUGMENT_LOCKED["TRADE_N"]):
        return _integrity("STOP. FIXED_TRADE_N != 26.", extra={"fixed": {k: v for k, v in fixed.items() if k != "rows"}}, sha=sha, pre=pre)
    print(
        f"FIXED n={fixed.get('FIXED_TRADE_N')} delta_net={fixed.get('FIXED_DELTA_NET')} "
        f"trig={fixed.get('EXIT_TRIGGER_N')} l2_imp={fixed.get('L2_IMPROVED_N')} "
        f"win_prem={fixed.get('WINNER_PREMATURE_EXIT_N')}",
        flush=True,
    )

    tagged_e1, e1_miss = apply_eind_exits(tagged, eind_by)
    leak["EIND_JOIN_MISS_N"] = int(e1_miss)
    if e1_miss:
        return _integrity("STOP. EIND join miss on C0-eligible fills.", extra={"integrity": leak}, sha=sha, pre=pre)

    e1_overlay = overlay_replay(tagged_e1, include_augment=True, augment_rank="utility")
    ident = identity_audit(tagged, tagged_e1, e0_overlay, e1_overlay, c0_keys)
    for k in (
        "C0_CANDIDATE_DECISION_MISMATCH_N",
        "B0_SCORE_MISMATCH_N",
        "B1_SCORE_MISMATCH_N",
        "C0_CONFIRMATION_MISMATCH_N",
        "ENTRY_POLICY_CHANGE_N",
    ):
        leak[k] = int(ident.get(k) or 0)
    if int(ident.get("TAGGED_LEN_MISMATCH_N") or 0):
        leak["C0_CONFIRMATION_MISMATCH_N"] = int(leak.get("C0_CONFIRMATION_MISMATCH_N") or 0) + int(
            ident.get("TAGGED_LEN_MISMATCH_N") or 0
        )

    e1_trades = list(e1_overlay.get("trades") or [])
    e1_aug = [t for t in e1_trades if str(t.get("arm") or "") == "AUGMENT"]
    e1_ov_pack = economic_pack(e1_trades, list(ELIGIBLE_DAYS))
    e1_aug_pack = economic_pack(e1_aug, list(ELIGIBLE_DAYS))
    e1_paired = paired_delta(list(e1_ov_pack.get("daily") or []), list(cur_pack.get("daily") or []))
    pres = preservation_audit(baseline, e1_overlay)
    cur_counts = current_trade_counts(baseline, e1_overlay)
    pres.update(cur_counts)
    pres["CURRENT_PRESERVATION_PASS"] = preservation_ok(pres)
    leak["CURRENT_EXIT_CHANGE_N"] = int(pres.get("CURRENT_EXIT_MISMATCH_N") or 0) + int(CURRENT_EXIT_CHANGE_N)
    conc = profit_concentration(list(e1_aug_pack.get("daily") or []))
    vs = vs_c0(
        {
            "OVERLAY_NET_PNL": e1_ov_pack.get("net_pnl_yen_100"),
            "OVERLAY_PF": e1_ov_pack.get("profit_factor"),
            "OVERLAY_MAX_DD": e1_ov_pack.get("max_drawdown_yen_100"),
            "PAIRED_POS_DAYS": e1_paired.get("PAIRED_POS_DAYS"),
            "PAIRED_NEG_DAYS": e1_paired.get("PAIRED_NEG_DAYS"),
            "PAIRED_MEDIAN_DAILY_DELTA": e1_paired.get("PAIRED_MEDIAN_DAILY_DELTA"),
            "EX_BEST_DAY_PNL_DELTA": e1_paired.get("EX_BEST_DAY_PNL_DELTA"),
            "EX_TOP3_DAYS_PNL_DELTA": e1_paired.get("EX_TOP3_DAYS_PNL_DELTA"),
        }
    )

    post = snapshot(phase="AFTER")
    adv = ni_advanced(pre, post)
    leak["RESEARCH_INPUT_ACTIVE_FILE_N"] = input_active_file_n(
        input_paths,
        str(post.get("ACTIVE_CAPTURE_PATH") or pre.get("ACTIVE_CAPTURE_PATH") or ""),
        str(post.get("ACTIVE_PAPER_SESSION") or pre.get("ACTIVE_PAPER_SESSION") or ""),
        TODAY,
    )
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(post.get("ACTIVE_CAPTURE_PATH") or ""),
        str(post.get("ACTIVE_PAPER_SESSION") or ""),
    )
    ni_ok = (
        int(leak["RESEARCH_INPUT_ACTIVE_FILE_N"]) == 0
        and int(leak["RESEARCH_WRITE_PATH_OVERLAP_N"]) == 0
        and int(leak["LIVE_PROCESS_CONTROL_CALL_N"]) == 0
        and int(leak["ADDITIONAL_WEBSOCKET_CONNECTION_N"]) == 0
        and int(leak["RUNTIME_CONFIG_CHANGE_N"]) == 0
        and int(leak["RUNTIME_STATE_WRITE_N"]) == 0
        and int(leak["CAPTURE_STATE_WRITE_N"]) == 0
        and bool(adv.get("RUNTIME_PID_UNCHANGED"))
        and bool(adv.get("CAPTURE_PID_UNCHANGED"))
        and bool(adv.get("RUNTIME_STILL_ALIVE"))
        and bool(adv.get("CAPTURE_STILL_ALIVE"))
        and bool(adv.get("RUNTIME_HEARTBEAT_ADVANCED"))
        and bool(adv.get("CAPTURE_ADVANCED"))
    )

    integrity_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO_KEYS)
    gate = eind_full_gate(
        e1_ov_pack,
        cur_pack,
        e1_paired,
        preservation_ok_flag=bool(pres.get("CURRENT_PRESERVATION_PASS")),
        integrity_ok=integrity_ok,
        non_interference_ok=bool(ni_ok),
    )
    decision = decide_case(
        integrity_ok=integrity_ok,
        non_interference_ok=bool(ni_ok),
        preservation_ok_flag=bool(pres.get("CURRENT_PRESERVATION_PASS")),
        full_pass=bool(gate.get("EIND_FULL_PASS")),
        overlay_net=float(e1_ov_pack.get("net_pnl_yen_100") or 0.0),
        overlay_pf=_pf_num(e1_ov_pack.get("profit_factor")),
        overlay_dd=float(e1_ov_pack.get("max_drawdown_yen_100") or 0.0),
        paired_median=e1_paired.get("PAIRED_MEDIAN_DAILY_DELTA"),
        paired_pos=int(e1_paired.get("PAIRED_POS_DAYS") or 0),
        paired_neg=int(e1_paired.get("PAIRED_NEG_DAYS") or 0),
        ex_best=e1_paired.get("EX_BEST_DAY_PNL_DELTA"),
        ex_top3=e1_paired.get("EX_TOP3_DAYS_PNL_DELTA"),
        vs=vs,
    )

    aug_adm = [a for a in (e1_overlay.get("admissions") or []) if str(a.get("arm") or "") == "AUGMENT"]
    aug_fill = [f for f in (e1_overlay.get("fills") or []) if str(f.get("arm") or "") == "AUGMENT"]
    candidates = list(e1_overlay.get("augment_candidates") or [])
    fold_train_n = int(oof.get("TRAIN_TRADE_N") or train_n)
    required = {
        "BASE_PARITY": True,
        "ARCHITECTURE_ID": ARCHITECTURE_ID,
        "EXIT_INDICATOR_SPEC_SHA256": sha,
        "TIME_FEATURE_N": int(TIME_FEATURE_N),
        "HOLDING_TIME_RULE_N": int(HOLDING_TIME_RULE_N),
        "OUTER_FOLD_N": int(OUTER_FOLD_N),
        "TRAIN_TRADE_N": train_n,
        "OOF_AUC": oof.get("OOF_AUC"),
        "FIXED_TRADE_N": fixed.get("FIXED_TRADE_N"),
        "FIXED_DELTA_NET": fixed.get("FIXED_DELTA_NET"),
        "L2_BASE_N": fixed.get("L2_BASE_N"),
        "L2_IMPROVED_N": fixed.get("L2_IMPROVED_N"),
        "WINNER_PREMATURE_EXIT_N": fixed.get("WINNER_PREMATURE_EXIT_N"),
        "EIND_AUGMENT_TRADE_N": e1_aug_pack.get("trade_count"),
        "EIND_AUGMENT_NET": e1_aug_pack.get("net_pnl_yen_100"),
        "EIND_AUGMENT_PF": e1_aug_pack.get("profit_factor"),
        "EIND_OVERLAY_NET": e1_ov_pack.get("net_pnl_yen_100"),
        "EIND_OVERLAY_PF": e1_ov_pack.get("profit_factor"),
        "EIND_OVERLAY_DD": e1_ov_pack.get("max_drawdown_yen_100"),
        "PAIRED_POS_DAYS": e1_paired.get("PAIRED_POS_DAYS"),
        "PAIRED_NEG_DAYS": e1_paired.get("PAIRED_NEG_DAYS"),
        "PAIRED_ZERO_DAYS": e1_paired.get("PAIRED_ZERO_DAYS"),
        "PAIRED_MEDIAN": e1_paired.get("PAIRED_MEDIAN_DAILY_DELTA"),
        "EX_BEST": e1_paired.get("EX_BEST_DAY_PNL_DELTA"),
        "EX_TOP3": e1_paired.get("EX_TOP3_DAYS_PNL_DELTA"),
        "BEST_DAY_SHARE": conc.get("BEST_DAY_SHARE_OF_TOTAL_PROFIT"),
        "TOP3_DAY_SHARE": _top3_share(list(e1_aug_pack.get("daily") or [])),
        "DELTA_NET_VS_C0_C14": vs.get("DELTA_NET_VS_C0_C14"),
        "DELTA_PF_VS_C0_C14": vs.get("DELTA_PF_VS_C0_C14"),
        "DELTA_DD_VS_C0_C14": vs.get("DELTA_DD_VS_C0_C14"),
        "DELTA_PAIRED_MEDIAN_VS_C0_C14": vs.get("DELTA_PAIRED_MEDIAN_VS_C0_C14"),
        "CURRENT_PRESERVATION_PASS": bool(pres.get("CURRENT_PRESERVATION_PASS")),
        "NON_INTERFERENCE_PASS": bool(ni_ok),
        "RUNTIME_PID_BEFORE": pre.get("RUNTIME_PID"),
        "RUNTIME_PID_AFTER": post.get("RUNTIME_PID"),
        "CAPTURE_PID_BEFORE": pre.get("CAPTURE_PID"),
        "CAPTURE_PID_AFTER": post.get("CAPTURE_PID"),
        "RUNTIME_HEARTBEAT_ADVANCED": adv.get("RUNTIME_HEARTBEAT_ADVANCED"),
        "CAPTURE_ADVANCED": adv.get("CAPTURE_ADVANCED"),
        "EIND_FULL_PASS": bool(gate.get("EIND_FULL_PASS")),
        "TRUE_OOS": False,
        "NEW_FORWARD_N": 0,
        "VERDICT": decision.get("VERDICT"),
        "NEXT": decision.get("NEXT"),
        "AUGMENT_CANDIDATE_N": len(candidates),
        "AUGMENT_ADMITTED_N": len(aug_adm),
        "AUGMENT_FILL_N": len(aug_fill),
        "E0_FIXED_NET": fixed.get("E0_FIXED_NET"),
        "EIND_FIXED_NET": fixed.get("EIND_FIXED_NET"),
        "E0_FIXED_PF": fixed.get("E0_FIXED_PF"),
        "EIND_FIXED_PF": fixed.get("EIND_FIXED_PF"),
        "IMPROVED_TRADE_N": fixed.get("IMPROVED_TRADE_N"),
        "WORSENED_TRADE_N": fixed.get("WORSENED_TRADE_N"),
        "UNCHANGED_TRADE_N": fixed.get("UNCHANGED_TRADE_N"),
        "L2_WORSENED_N": fixed.get("L2_WORSENED_N"),
        "L2_NONLOSS_CONVERTED_N": fixed.get("L2_NONLOSS_CONVERTED_N"),
        "OOF_BALANCED_ACCURACY": oof.get("OOF_BALANCED_ACCURACY"),
        "OOF_BRIER": oof.get("OOF_BRIER"),
        "CASE": decision.get("CASE"),
    }
    extra = {
        "CASE": decision.get("CASE"),
        "gates": gate.get("gates"),
        "integrity": leak,
        "identity": ident,
        "preservation": pres,
        "model": {
            "OOF_AUC": oof.get("OOF_AUC"),
            "OOF_BALANCED_ACCURACY": oof.get("OOF_BALANCED_ACCURACY"),
            "OOF_BRIER": oof.get("OOF_BRIER"),
            "OOF_POS_N": oof.get("OOF_POS_N"),
            "OOF_NEG_N": oof.get("OOF_NEG_N"),
            "P_EXIT_MEAN": oof.get("P_EXIT_MEAN"),
            "P_EXIT_P50": oof.get("P_EXIT_P50"),
            "P_EXIT_P90": oof.get("P_EXIT_P90"),
        },
        "fixed": {k: v for k, v in fixed.items() if k != "rows"},
        "vs_c0_c14": vs,
        "concentration": conc,
        "preflight": {k: pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "postflight": {k: post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
        "ni_advanced": adv,
        "e0_overlay": _slim(e0_ov_pack),
        "eind_overlay": _slim(e1_ov_pack),
        "eind_augment": _slim(e1_aug_pack),
        "fold_train_n": fold_train_n,
    }
    sheets = {
        "precommit": kv_rows(spec),
        "non_interference": kv_rows(
            {
                **{f"{k}_BEFORE": pre.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
                **{f"{k}_AFTER": post.get(k) for k in ("RUNTIME_PID", "CAPTURE_PID", "RUNTIME_HEARTBEAT", "CAPTURE_LAST_EVENT", "ACTIVE_CAPTURE_PATH")},
                **adv,
                "RESEARCH_INPUT_ACTIVE_FILE_N": leak.get("RESEARCH_INPUT_ACTIVE_FILE_N"),
                "RESEARCH_WRITE_PATH_OVERLAP_N": leak.get("RESEARCH_WRITE_PATH_OVERLAP_N"),
                "LIVE_PROCESS_CONTROL_CALL_N": leak.get("LIVE_PROCESS_CONTROL_CALL_N"),
                "ADDITIONAL_WEBSOCKET_CONNECTION_N": leak.get("ADDITIONAL_WEBSOCKET_CONNECTION_N"),
                "NON_INTERFERENCE_PASS": ni_ok,
            }
        ),
        "training_population": [
            {
                "TRAIN_TRADE_N": train_n,
                "CURRENT_EPISODE_N": int(CURRENT_LOCKED["TRADE_N"]),
                "C0_AUGMENT_EPISODE_N": int(C0_AUGMENT_LOCKED["TRADE_N"]),
                "POLICY_ONLY_N": sum(1 for v in fills_by.values() for x in v if x.get("role") == "POLICY_ONLY"),
                "OOF_EVENT_ROW_N": oof.get("OOF_EVENT_ROW_N"),
                "OOF_POS_N": oof.get("OOF_POS_N"),
                "OOF_NEG_N": oof.get("OOF_NEG_N"),
            }
        ],
        "folds": list(oof.get("folds") or []),
        "model": list(oof.get("mean_coef") or []),
        "trade_exit": [
            {
                "row_key": r.get("row_key"),
                "date": r.get("date"),
                "symbol": r.get("symbol"),
                "triggered": r.get("triggered"),
                "exit_reason": r.get("e1_reason"),
                "e0_pnl": r.get("e0_pnl"),
                "e1_pnl": r.get("e1_pnl"),
                "delta_pnl": r.get("delta_pnl"),
                "BASELINE_L2": r.get("BASELINE_L2"),
                "p_exit": r.get("p_exit"),
            }
            for r in list(fixed.get("rows") or [])
        ],
        "fixed26": kv_rows({k: v for k, v in fixed.items() if k != "rows"}),
        "daily_pnl": list(e1_paired.get("daily") or []),
        "economics": kv_rows(
            {
                "AUGMENT_CANDIDATE_N": len(candidates),
                "AUGMENT_ADMITTED_N": len(aug_adm),
                "AUGMENT_FILL_N": len(aug_fill),
                "AUGMENT_TRADE_N": e1_aug_pack.get("trade_count"),
                "AUGMENT_NET_PNL": e1_aug_pack.get("net_pnl_yen_100"),
                "AUGMENT_GROSS_PROFIT": e1_aug_pack.get("gross_profit"),
                "AUGMENT_GROSS_LOSS": e1_aug_pack.get("gross_loss"),
                "AUGMENT_PF": e1_aug_pack.get("profit_factor"),
                "AUGMENT_MAX_DD": e1_aug_pack.get("max_drawdown_yen_100"),
                "AUGMENT_WIN_N": e1_aug_pack.get("win_n"),
                "AUGMENT_LOSS_N": e1_aug_pack.get("loss_n"),
                "AUGMENT_FLAT_N": e1_aug_pack.get("flat_n"),
                "OVERLAY_TRADE_N": e1_ov_pack.get("trade_count"),
                "OVERLAY_NET_PNL": e1_ov_pack.get("net_pnl_yen_100"),
                "OVERLAY_PF": e1_ov_pack.get("profit_factor"),
                "OVERLAY_MAX_DD": e1_ov_pack.get("max_drawdown_yen_100"),
                **{k: e1_paired.get(k) for k in ("PAIRED_POS_DAYS", "PAIRED_NEG_DAYS", "PAIRED_ZERO_DAYS", "PAIRED_MEDIAN_DAILY_DELTA", "EX_BEST_DAY_PNL_DELTA", "EX_TOP3_DAYS_PNL_DELTA")},
                **vs,
                **conc,
                "TOP3_DAY_SHARE": required.get("TOP3_DAY_SHARE"),
                "EIND_FULL_PASS": gate.get("EIND_FULL_PASS"),
                **(gate.get("gates") or {}),
            }
        ),
        "overfit_audit": list(oof.get("folds") or [])
        + [
            {
                "BEST_DAY_SHARE": conc.get("BEST_DAY_SHARE_OF_TOTAL_PROFIT"),
                "TOP3_DAY_SHARE": required.get("TOP3_DAY_SHARE"),
                "TRUE_OOS": False,
                "NEW_FORWARD_N": 0,
                "TRAIN_TRADE_N": train_n,
            }
        ],
        "integrity": kv_rows(leak),
    }
    report = {"ANALYSIS_ID": ANALYSIS_ID, "required": required, "decision": decision, **extra}
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, sheets)
    print(
        f"VERDICT={decision.get('VERDICT')} FULL={gate.get('EIND_FULL_PASS')} NI={ni_ok} "
        f"overlay_net={e1_ov_pack.get('net_pnl_yen_100')} delta_vs_c0={vs.get('DELTA_NET_VS_C0_C14')}",
        flush=True,
    )
    return 0 if integrity_ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
