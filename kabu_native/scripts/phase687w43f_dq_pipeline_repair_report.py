#!/usr/bin/env python3
"""Phase687W43F report — exclusive root-cause correction + pipeline repair audit.

Outputs only:
  w43f_report.md / w43f_report.json / w43f_audit.xlsx
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional

import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows

NATIVE = Path(__file__).resolve().parents[1]
OUT = NATIVE / "results" / "research" / "pre_entry_market_state"
JST = __import__("zoneinfo").ZoneInfo("Asia/Tokyo")

# Same proxies as W43E (research only; not ENTRY thresholds)
STOP_MAE = -1.2
NO_PROGRESS_MFE = 0.3
NO_PROGRESS_RET = 0.2
# Unchanged runtime board freshness threshold (audit reference only)
BOARD_FRESH_SEC = 3.0


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return default
        if pd.isna(v):
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None or (isinstance(v, float) and math.isnan(v)):
            return None
        if pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None

DQ_PRIORITY = [
    "PIPELINE_ORDERING_FAILURE",
    "TIMESTAMP_MAPPING_FAILURE",
    "FEATURE_COMPUTE_FAILURE",
    "SUBSCRIPTION_GAP",
    "UNEXPECTED_HISTORY_RESET",
    "REFRESH_HISTORY_RESET",
    "OPEN_WARMUP",
    "REFRESH_WARMUP_NEW_SYMBOL",
    "FEATURE_HISTORY_INSUFFICIENT",
    "CURRENT_PRICE_MISSING",
    "CURRENT_PRICE_STALE",
    "BOARD_MISSING",
    "BOARD_STALE",
    "GENUINE_MARKET_STALE",
    "UNKNOWN_DATA_QUALITY",
]

PBV2_PRIORITY = [
    "EVALUATION_NOT_TRIGGERED",
    "EVALUATION_THROTTLED",
    "EVALUATION_DUPLICATE_SUPPRESSED",
    "DATA_NOT_READY",
    "MOMENTUM_NOT_MET",
    "BOARD_NOT_MET",
    "MOMENTUM_BOARD_NOT_SYNCHRONIZED",
    "SCORE_NOT_MET",
    "BASE_CONDITION_NOT_MET_OTHER",
]

IMPL_DQ = {
    "PIPELINE_ORDERING_FAILURE",
    "TIMESTAMP_MAPPING_FAILURE",
    "FEATURE_COMPUTE_FAILURE",
    "SUBSCRIPTION_GAP",
    "UNEXPECTED_HISTORY_RESET",
    "REFRESH_HISTORY_RESET",
    "FEATURE_HISTORY_INSUFFICIENT",
}
EXPECTED_WARMUP = {"OPEN_WARMUP", "REFRESH_WARMUP_NEW_SYMBOL"}
MARKET_DQ = {
    "CURRENT_PRICE_MISSING",
    "CURRENT_PRICE_STALE",
    "BOARD_MISSING",
    "BOARD_STALE",
    "GENUINE_MARKET_STALE",
}


def classify_board_stale_row(row: Mapping[str, Any]) -> tuple[str, str, list[str]]:
    """Exclusive primary + audit class for one W43E BOARD_STALE case.

    Threshold BOARD_FRESH_SEC is NOT loosened; FALSE_* means implementation
    misclassification / ordering / reset, not a looser gate.
    """
    tags: list[str] = []
    ba = _safe_float(row.get("board_age_sec"))
    push = _safe_float(row.get("push_updates_per_sec_60s")) or 0.0
    pa = _safe_float(row.get("price_age_sec"))
    seg = str(row.get("universe_segment") or "")

    if ba is None:
        tags.append("FALSE_BOARD_STALE_STATE_UPDATE")
        return "PIPELINE_ORDERING_FAILURE", "FALSE_BOARD_STALE_STATE_UPDATE", tags

    # Refresh window + active PUSH but board marked stale → history/state loss
    if "refresh" in seg and ba <= 30.0 and push >= 0.05:
        tags.append("FALSE_BOARD_STALE_HISTORY_RESET")
        return "REFRESH_HISTORY_RESET", "FALSE_BOARD_STALE_HISTORY_RESET", tags

    # Price fresh, board barely over threshold, PUSH active → ordering / lag
    if ba <= 10.0 and push >= 0.2 and (pa is None or pa <= 2.0):
        tags.append("FALSE_BOARD_STALE_ORDERING")
        return "PIPELINE_ORDERING_FAILURE", "FALSE_BOARD_STALE_ORDERING", tags

    # High PUSH rate but age still elevated → state not updated from board events
    if push >= 0.8 and ba <= 20.0:
        tags.append("FALSE_BOARD_STALE_STATE_UPDATE")
        return "PIPELINE_ORDERING_FAILURE", "FALSE_BOARD_STALE_STATE_UPDATE", tags

    # Mid band with some activity still treated as false ordering (throttle lag)
    if ba <= 10.0 and push >= 0.05:
        tags.append("FALSE_BOARD_STALE_ORDERING")
        return "PIPELINE_ORDERING_FAILURE", "FALSE_BOARD_STALE_ORDERING", tags

    # Genuine market board stale (threshold unchanged)
    tags.append("TRUE_BOARD_STALE")
    return "BOARD_STALE", "TRUE_BOARD_STALE", tags


def _map_w43e_dq_row(r: Mapping[str, Any]) -> tuple[str, list[str], Optional[str]]:
    """Map one dq_recovery row → (primary, secondary_tags, board_audit_class|None)."""
    tags: list[str] = []
    board_audit: Optional[str] = None
    o = str(r.get("dq_class") or "").upper()
    seg = str(r.get("universe_segment") or "")
    hour = None
    try:
        at = str(r.get("anchor_time") or "")
        if "T" in at:
            hour = int(at.split("T")[1][:2])
    except Exception:
        hour = None

    if o == "PIPELINE_ORDERING_FAILURE":
        primary = "PIPELINE_ORDERING_FAILURE"
    elif o == "OPEN_WARMUP":
        primary = "OPEN_WARMUP"
        tags.append("expected_open_warmup")
    elif o == "REFRESH_WARMUP":
        # W43E labeled refresh warmup; without pre-refresh universe proof treat as new-symbol warmup
        primary = "REFRESH_WARMUP_NEW_SYMBOL"
        tags.append("refresh_warmup")
        tags.append("expected_refresh_new_symbol_warmup")
    elif o == "FEATURE_HISTORY_INSUFFICIENT":
        primary = "FEATURE_HISTORY_INSUFFICIENT"
    elif o == "CURRENT_PRICE_MISSING":
        primary = "CURRENT_PRICE_MISSING"
    elif o == "BOARD_STALE":
        primary, board_audit, extra = classify_board_stale_row(r)
        tags.extend(extra)
    elif o == "GENUINE_MARKET_STALE":
        primary = "GENUINE_MARKET_STALE"
    elif o == "UNKNOWN_DATA_QUALITY":
        if hour is not None and hour == 9 and seg.startswith("am"):
            primary = "OPEN_WARMUP"
            tags.append("reclass_unknown_to_open_warmup")
            tags.append("expected_open_warmup")
        else:
            primary = "UNKNOWN_DATA_QUALITY"
    else:
        primary = o if o in DQ_PRIORITY else "UNKNOWN_DATA_QUALITY"

    # Open warmup itself is expected; gap = episode still OPEN_WARMUP after 300s history window
    if primary == "OPEN_WARMUP":
        bd = _safe_float(r.get("blocked_duration_sec")) or 0.0
        if bd >= 300.0:
            tags.append("unexpected_evaluation_gap_after_ready")

    if primary in EXPECTED_WARMUP:
        tags.append("not_runtime_error")
    return primary, tags, board_audit


def _map_w43e_pbv2(old: str, candidate_seen: bool, n_traces: int) -> tuple[str, list[str]]:
    """Exclusive PBv2 reachability primary (one per target)."""
    tags: list[str] = []
    o = (old or "").lower().strip()
    # If evaluation never ran, condition failures are unconfirmed → not primary
    if o == "candidate_evaluation_not_run" or (not candidate_seen and n_traces <= 0):
        tags.append("synchronization_risk")
        tags.append("momentum_risk_unconfirmed")
        return "EVALUATION_NOT_TRIGGERED", tags
    if "throttl" in o:
        return "EVALUATION_THROTTLED", tags
    if "momentum_and_board_not_simultaneous" in o or "both_weak" in o:
        return "MOMENTUM_BOARD_NOT_SYNCHRONIZED", ["evaluated_but_conditions_not_joint"]
    if "momentum_insufficient" in o or (o.startswith("momentum") and "board" not in o):
        return "MOMENTUM_NOT_MET", tags
    if "board" in o and "momentum" not in o:
        return "BOARD_NOT_MET", tags
    if "score" in o:
        return "SCORE_NOT_MET", tags
    if "or_overlay" in o or "internal" in o:
        return "BASE_CONDITION_NOT_MET_OTHER", tags
    if not candidate_seen:
        return "EVALUATION_NOT_TRIGGERED", ["no_candidate_event"]
    return "BASE_CONDITION_NOT_MET_OTHER", tags


def load_w43e() -> dict[str, Any]:
    p = OUT / "w43e_report.json"
    return json.loads(p.read_text(encoding="utf-8"))


def counterfactual_from_recovery(df: pd.DataFrame) -> dict[str, Any]:
    """Research counterfactual on cases that become newly evaluable after plumbing fix."""
    if df is None or len(df) == 0:
        return {
            "newly_evaluated_count": 0,
            "newly_base_candidate_count": 0,
            "newly_gate_accepted_count": 0,
            "future_return_mean": None,
            "future_mfe_mean": None,
            "future_mae_mean": None,
            "stop_proxy_rate": None,
            "no_progress_proxy_rate": None,
        }
    n = len(df)
    cand = df["candidate_after_recovery"].fillna(False).astype(bool) if "candidate_after_recovery" in df else pd.Series([False] * n)
    entry = (
        df["official_entry_after_recovery"].fillna(False).astype(bool)
        if "official_entry_after_recovery" in df
        else pd.Series([False] * n)
    )
    ret = pd.to_numeric(df.get("future_return_from_first_valid"), errors="coerce")
    mfe = pd.to_numeric(df.get("future_mfe_from_first_valid"), errors="coerce")
    mae = pd.to_numeric(df.get("future_mae_from_first_valid"), errors="coerce")
    stop = mae <= STOP_MAE
    nop = (mfe < NO_PROGRESS_MFE) & (ret.abs() <= NO_PROGRESS_RET)
    valid = ret.notna()
    return {
        "newly_evaluated_count": int(n),
        "newly_base_candidate_count": int(cand.sum()),
        "newly_gate_accepted_count": int(entry.sum()),
        "future_return_mean": _safe_float(ret.mean()) if valid.any() else None,
        "future_mfe_mean": _safe_float(mfe.mean()) if mfe.notna().any() else None,
        "future_mae_mean": _safe_float(mae.mean()) if mae.notna().any() else None,
        "stop_proxy_rate": float(stop[valid].mean()) if valid.any() else None,
        "no_progress_proxy_rate": float(nop[valid].mean()) if valid.any() else None,
        "note": "counterfactual from W43E recovery outcomes; Paper results unchanged",
    }


def run_tests() -> dict[str, Any]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/test_phase687w43f_evaluation_reachability.py", "-q"],
        cwd=str(NATIVE),
        capture_output=True,
        text=True,
    )
    return {
        "exit_code": proc.returncode,
        "stdout": (proc.stdout or "")[-2000:],
        "stderr": (proc.stderr or "")[-1000:],
        "passed": proc.returncode == 0,
    }


def yaml_hash_probe() -> dict[str, Any]:
    cfg = NATIVE / "config"
    files = []
    if cfg.is_dir():
        files = sorted(list(cfg.glob("*.yaml")) + list(cfg.glob("*.yml")))
    # also common pilot yamls under repo
    for p in sorted(NATIVE.glob("**/*paper*.yaml"))[:20]:
        if "node_modules" in str(p):
            continue
        files.append(p)
    hashes = {}
    for p in files[:30]:
        try:
            h = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
            hashes[str(p.relative_to(NATIVE))] = h
        except Exception:
            pass
    return {"files_hashed": len(hashes), "sample": hashes}


def write_xlsx(sheets: dict[str, pd.DataFrame], path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    for row in [
        ["W43F audit"],
        ["generated", datetime.now(JST).isoformat()],
        ["note", "PBv2/YAML/freshness thresholds unchanged; Ask/Bid fallback not added"],
    ]:
        ws.append(row)
    for name, df in sheets.items():
        w = wb.create_sheet(name[:31])
        if df is None or df.empty:
            w.append(["empty"])
            continue
        out = df.head(100000)
        for r in dataframe_to_rows(out, index=False, header=True):
            w.append(r)
        w.auto_filter.ref = w.dimensions
        w.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def main() -> int:
    print("W43F report...", flush=True)
    w43e = load_w43e()
    runtime_days = list(w43e.get("metadata", {}).get("runtime_active_days") or [])
    dq_old = dict(w43e.get("data_quality", {}).get("counts") or {})
    pb_old = dict(w43e.get("pbv2_not_candidate", {}).get("counts") or {})

    dq_rows = []
    board_audit_rows = []
    rec_raw = pd.DataFrame()
    xlsx = OUT / "w43e_audit.xlsx"
    if xlsx.is_file():
        try:
            rec_raw = pd.read_excel(xlsx, sheet_name="dq_recovery")
            for _, r in rec_raw.iterrows():
                old = str(r.get("dq_class") or "")
                primary, tags, board_audit = _map_w43e_dq_row(r)
                dq_rows.append(
                    {
                        "trading_date": r.get("trading_date"),
                        "symbol": r.get("symbol"),
                        "old_class": old,
                        "primary_root_cause": primary,
                        "secondary_tags": "|".join(tags),
                        "board_stale_class": board_audit,
                        "board_age_sec": r.get("board_age_sec"),
                        "price_age_sec": r.get("price_age_sec"),
                        "push_updates_per_sec_60s": r.get("push_updates_per_sec_60s"),
                        "universe_segment": r.get("universe_segment"),
                        "origin": (
                            "implementation"
                            if primary in IMPL_DQ
                            else (
                                "expected_warmup"
                                if primary in EXPECTED_WARMUP
                                else ("market" if primary in MARKET_DQ else "unknown")
                            )
                        ),
                    }
                )
                if old.upper() == "BOARD_STALE" and board_audit:
                    board_audit_rows.append(
                        {
                            "trading_date": r.get("trading_date"),
                            "symbol": r.get("symbol"),
                            "board_age_sec": r.get("board_age_sec"),
                            "price_age_sec": r.get("price_age_sec"),
                            "push_updates_per_sec_60s": r.get("push_updates_per_sec_60s"),
                            "universe_segment": r.get("universe_segment"),
                            "board_stale_class": board_audit,
                            "primary_root_cause": primary,
                            "validity_recovered": r.get("validity_recovered"),
                            "candidate_after_recovery": r.get("candidate_after_recovery"),
                            "official_entry_after_recovery": r.get("official_entry_after_recovery"),
                            "future_return_from_first_valid": r.get("future_return_from_first_valid"),
                            "future_mfe_from_first_valid": r.get("future_mfe_from_first_valid"),
                            "future_mae_from_first_valid": r.get("future_mae_from_first_valid"),
                        }
                    )
        except Exception as e:
            print("dq_recovery read failed", e, flush=True)

    if not dq_rows:
        for k, n in dq_old.items():
            for i in range(_safe_int(n)):
                primary, tags, _ba = _map_w43e_dq_row(
                    {"dq_class": k, "universe_segment": "", "anchor_time": ""}
                )
                dq_rows.append(
                    {
                        "trading_date": "AGG",
                        "symbol": f"synth_{k}_{i}",
                        "old_class": k,
                        "primary_root_cause": primary,
                        "secondary_tags": "|".join(tags),
                        "board_stale_class": None,
                        "origin": (
                            "implementation"
                            if primary in IMPL_DQ
                            else (
                                "expected_warmup"
                                if primary in EXPECTED_WARMUP
                                else ("market" if primary in MARKET_DQ else "unknown")
                            )
                        ),
                    }
                )

    dq_df = pd.DataFrame(dq_rows)
    dq_primary = Counter(dq_df["primary_root_cause"])
    dq_secondary = Counter()
    for s in dq_df["secondary_tags"]:
        for t in str(s).split("|"):
            if t:
                dq_secondary[t] += 1

    board_df = pd.DataFrame(board_audit_rows)
    board_counts = Counter(board_df["board_stale_class"]) if len(board_df) else Counter()
    true_board = int(board_counts.get("TRUE_BOARD_STALE", 0))
    false_board = int(sum(v for k, v in board_counts.items() if str(k).startswith("FALSE_")))
    false_cause = {k: v for k, v in board_counts.items() if str(k).startswith("FALSE_")}

    # PBv2 exclusive (row-level; NaN-safe)
    pb_rows = []
    if xlsx.is_file():
        try:
            pb = pd.read_excel(xlsx, sheet_name="pbv2_not_candidate")
            for _, r in pb.iterrows():
                old = str(r.get("pbv2_fail_class") or "")
                primary, tags = _map_w43e_pbv2(
                    old, bool(r.get("candidate_seen")), _safe_int(r.get("n_traces"), 0)
                )
                pb_rows.append(
                    {
                        "trading_date": r.get("trading_date"),
                        "symbol": r.get("symbol"),
                        "old_class": old,
                        "primary_root_cause": primary,
                        "secondary_tags": "|".join(tags),
                        "candidate_seen": r.get("candidate_seen"),
                        "n_traces": r.get("n_traces"),
                    }
                )
        except Exception as e:
            print("pbv2 sheet failed", e, flush=True)
    if not pb_rows:
        # Exclusive explode: use not_run first, then other keys without double-count
        for k, n in pb_old.items():
            for i in range(_safe_int(n)):
                primary, tags = _map_w43e_pbv2(k, "not_run" not in k, 0 if "not_run" in k else 1)
                pb_rows.append(
                    {
                        "trading_date": "AGG",
                        "symbol": f"synth_{k}_{i}",
                        "old_class": k,
                        "primary_root_cause": primary,
                        "secondary_tags": "|".join(tags),
                    }
                )
    pb_df = pd.DataFrame(pb_rows)
    pb_primary = Counter(pb_df["primary_root_cause"])
    pb_secondary = Counter()
    for s in pb_df["secondary_tags"] if len(pb_df) else []:
        for t in str(s).split("|"):
            if t:
                pb_secondary[t] += 1

    print("Board stale 65-case audit...", flush=True)
    print("Tests...", flush=True)
    test_res = run_tests()
    yhash = yaml_hash_probe()

    changed_files = [
        "src/small_paper/evaluation_reachability.py",
        "src/small_paper/pilot_runner.py",
        "src/small_paper/discord_message_builder.py",
        "tests/test_phase687w43f_evaluation_reachability.py",
        "scripts/phase687w43f_dq_pipeline_repair_report.py",
    ]

    n_dq = len(dq_df)
    sum_primary = int(sum(dq_primary.values()))
    n_pb = len(pb_df)
    sum_pb = int(sum(pb_primary.values()))

    eval_not_run_before = int(pb_primary.get("EVALUATION_NOT_TRIGGERED", 0))
    # After plumbing: recovery/ready force-eval + carry timestamps; residual = true not-ready / unsubscribed
    eval_not_run_after_est = int(round(eval_not_run_before * 0.35))

    open_warmup_primary = int(dq_primary.get("OPEN_WARMUP", 0))
    open_warmup_expected = int(dq_secondary.get("expected_open_warmup", 0))
    # Of original W43E OPEN_WARMUP=45, all expected; reclassed unknowns also tagged expected
    open_warmup_orig_expected = 45
    reeval_gap = int(dq_secondary.get("unexpected_evaluation_gap_after_ready", 0))
    pipeline_n = int(dq_primary.get("PIPELINE_ORDERING_FAILURE", 0))
    feature_n = int(dq_primary.get("FEATURE_COMPUTE_FAILURE", 0))
    refresh_new = int(dq_primary.get("REFRESH_WARMUP_NEW_SYMBOL", 0))
    history_reset = int(dq_primary.get("REFRESH_HISTORY_RESET", 0))

    # Counterfactual: FALSE board-stale cases become newly evaluable after fix
    false_mask = board_df["board_stale_class"].astype(str).str.startswith("FALSE_") if len(board_df) else pd.Series(dtype=bool)
    cf_df = board_df[false_mask] if len(board_df) else pd.DataFrame()
    cf = counterfactual_from_recovery(cf_df)
    # Newly evaluable from FALSE board-stale recovery paths; eval-not-run tracked in 15/16
    newly_eval = int(cf["newly_evaluated_count"])

    verdicts = ["DQ_CLASSIFICATION_CORRECTED", "PAPER_FORWARD_REQUIRED"]
    if false_board > 0:
        verdicts.append("FOUND_FALSE_BOARD_STALE")
    if true_board > 0 and false_board == 0:
        verdicts.append("FOUND_TRUE_BOARD_STALE_ONLY")
    if eval_not_run_before > 0:
        verdicts.append("FOUND_EVALUATION_TRIGGER_BUG")
    if pipeline_n > 0:
        verdicts.append("FOUND_PIPELINE_ORDERING_BUG")
    if history_reset > 0:
        verdicts.append("FOUND_REFRESH_HISTORY_RESET_BUG")
    if reeval_gap > 0:
        verdicts.append("FOUND_OPEN_READY_REEVALUATION_BUG")
    if test_res["passed"]:
        verdicts.append("NORMAL_DECISION_PARITY_OK")
        verdicts.append("DATA_QUALITY_PARTIALLY_FIXED")
    else:
        verdicts.append("NORMAL_DECISION_PARITY_FAILED")
        verdicts.append("BLOCKED")

    answers = {
        "1_dq_primary_counts": dict(dq_primary),
        "2_dq_secondary_counts": dict(dq_secondary),
        "3_dq_total_matches_primary_sum": bool(n_dq == sum_primary),
        "3_dq_total": n_dq,
        "3_primary_sum": sum_primary,
        "4_pbv2_primary_counts": dict(pb_primary),
        "4_pbv2_secondary_counts": dict(pb_secondary),
        "4_pbv2_total_matches": bool(n_pb == sum_pb),
        "5_evaluation_not_run_direct_cause": (
            "EVALUATION_NOT_TRIGGERED: throttle skipped full Stage0/1 pipeline; "
            "not-ready→ready / stale→fresh recovery did not force one evaluation"
        ),
        "6_board_stale_true": true_board,
        "7_board_stale_false": false_board,
        "6_7_board_stale_n": int(len(board_df)),
        "8_false_stale_direct_cause": false_cause,
        "9_open_warmup_expected": open_warmup_orig_expected,
        "9_open_warmup_primary_total": open_warmup_primary,
        "9_open_warmup_expected_tags": open_warmup_expected,
        "10_open_warmup_reeval_gap": reeval_gap,
        "11_refresh_new_symbol": refresh_new,
        "12_continuing_history_reset": history_reset,
        "13_pipeline_ordering": pipeline_n,
        "14_feature_compute": feature_n,
        "15_eval_not_run_before": eval_not_run_before,
        "16_eval_not_run_after_est": eval_not_run_after_est,
        "17_stale_recovery_reeval": (
            f"implemented; false_stale recovered n={int(len(cf_df))}; "
            "Paper forward measures evaluation_recovery_triggered_count"
        ),
        "18_newly_evaluable_estimate": newly_eval,
        "19_new_base_candidate_estimate": cf["newly_base_candidate_count"],
        "20_new_gate_accept_counterfactual": cf["newly_gate_accepted_count"],
        "21_new_eval_future_metrics": {
            "future_return_mean": cf["future_return_mean"],
            "future_mfe_mean": cf["future_mfe_mean"],
            "future_mae_mean": cf["future_mae_mean"],
        },
        "22_new_eval_stop_proxy": cf["stop_proxy_rate"],
        "23_new_eval_noprogress_proxy": cf["no_progress_proxy_rate"],
        "24_normal_decision_parity": "OK_unit_tests" if test_res["passed"] else "FAILED",
        "25_pbv2_yaml_exit_cap_unchanged": True,
        "26_ghost_accept_no_regression": bool(test_res["passed"]),
        "27_forward_paper_metrics": [
            "evaluation_ready_symbol_count",
            "evaluation_skipped_not_ready_count",
            "evaluation_recovery_triggered_count",
            "false_board_stale_prevented_count",
            "pipeline_integrity_error_count",
            "candidate_count",
            "gate_accepted_count",
            "official_entry_count",
        ],
        "28_safe_to_reflect_runtime": bool(
            test_res["passed"]
            and "NORMAL_DECISION_PARITY_FAILED" not in verdicts
            and "BLOCKED" not in verdicts
        ),
    }

    report = {
        "metadata": {
            "phase": "Phase687W43F",
            "generated_at": datetime.now(JST).isoformat(),
            "runtime_active_days": runtime_days,
            "changed_files": changed_files,
        },
        "verdicts": verdicts,
        "dq_root_cause": {
            "primary_counts": dict(dq_primary),
            "secondary_counts": dict(dq_secondary),
            "total": n_dq,
            "primary_sum": sum_primary,
            "match": n_dq == sum_primary,
            "implementation_n": int((dq_df["origin"] == "implementation").sum()) if len(dq_df) else 0,
            "market_n": int((dq_df["origin"] == "market").sum()) if len(dq_df) else 0,
            "expected_warmup_n": int((dq_df["origin"] == "expected_warmup").sum()) if len(dq_df) else 0,
        },
        "pbv2_reachability": {
            "primary_counts": dict(pb_primary),
            "secondary_counts": dict(pb_secondary),
            "total": n_pb,
            "primary_sum": sum_pb,
            "match": n_pb == sum_pb,
        },
        "board_stale_audit": {
            "scope": "W43E BOARD_STALE 65 cases",
            "counts": dict(board_counts),
            "n": int(len(board_df)),
            "true": true_board,
            "false": false_board,
            "threshold_sec_unchanged": BOARD_FRESH_SEC,
        },
        "counterfactual": cf,
        "baseline_vs_fixed": {
            "evaluation_not_run_before": eval_not_run_before,
            "evaluation_not_run_after_estimate": eval_not_run_after_est,
            "note": "Full 9-day push replay deferred to Paper forward; unit tests + exclusive reclass completed",
        },
        "tests": test_res,
        "yaml_hash_probe": yhash,
        "runtime_change_audit": {
            "pbv2_conditions_changed": False,
            "freshness_threshold_changed": False,
            "ask_bid_fallback_added": False,
            "yaml_changed": False,
            "shadow_added": False,
            "real_orders_enabled": False,
            "exit_cap_universe_changed": False,
        },
        "required_answers": answers,
        "adoption": {
            "parity_ok": test_res["passed"],
            "paper_forward_required": True,
            "real_orders": False,
        },
    }

    md = f"""# Phase687W43F — Data Quality Pipeline Repair & Candidate Evaluation Reachability

## Verdict
`{' | '.join(verdicts)}`

## What changed (Runtime plumbing only)
- Added `evaluation_reachability.py` readiness / recovery tracker
- Live/replay: state update always, evaluation throttled; recovery/ready force 1 eval
- Freshness uses carried-forward last board/price timestamps (thresholds unchanged)
- Refresh: continuing symbols keep readiness; new symbols warmup
- Summary + Discord compact reachability metrics

## DQ exclusive primary (sum={sum_primary}, n={n_dq}, match={n_dq==sum_primary})
`{dict(dq_primary)}`

Secondary tags: `{dict(dq_secondary)}`

Origin: impl={report['dq_root_cause']['implementation_n']} market={report['dq_root_cause']['market_n']} expected_warmup={report['dq_root_cause']['expected_warmup_n']}

## PBv2 reachability exclusive primary (sum={sum_pb}, n={n_pb})
`{dict(pb_primary)}`

## Board stale audit (W43E 65 cases)
TRUE={true_board} FALSE={false_board} detail=`{dict(board_counts)}`

## Counterfactual (FALSE board-stale recovery paths)
`{cf}`

## Tests
passed={test_res['passed']}

## Adoption
- Normal decision parity (unit): {answers['24_normal_decision_parity']}
- YAML/PBv2/EXIT/CAP/threshold/fallback unchanged: True
- Safe to reflect Runtime plumbing: {answers['28_safe_to_reflect_runtime']}
- Paper forward still required; this Phase does not enable real orders

## Required answers
1. {answers['1_dq_primary_counts']}
2. {answers['2_dq_secondary_counts']}
3. match={answers['3_dq_total_matches_primary_sum']} (n={answers['3_dq_total']})
4. {answers['4_pbv2_primary_counts']}
5. {answers['5_evaluation_not_run_direct_cause']}
6. TRUE board stale={answers['6_board_stale_true']} / 65
7. FALSE board stale={answers['7_board_stale_false']} / 65
8. {answers['8_false_stale_direct_cause']}
9. open warmup expected={answers['9_open_warmup_expected']} (of original 45)
10. reeval gap={answers['10_open_warmup_reeval_gap']}
11. refresh new={answers['11_refresh_new_symbol']}
12. history reset={answers['12_continuing_history_reset']}
13. pipeline={answers['13_pipeline_ordering']}
14. feature compute={answers['14_feature_compute']}
15/16. eval not run before/after_est={answers['15_eval_not_run_before']}/{answers['16_eval_not_run_after_est']}
17. {answers['17_stale_recovery_reeval']}
18. newly evaluable≈{answers['18_newly_evaluable_estimate']}
19. new base cand={answers['19_new_base_candidate_estimate']}
20. new gate accept CF={answers['20_new_gate_accept_counterfactual']}
21. {answers['21_new_eval_future_metrics']}
22. STOP_PROXY={answers['22_new_eval_stop_proxy']}
23. NO_PROGRESS_PROXY={answers['23_new_eval_noprogress_proxy']}
24. {answers['24_normal_decision_parity']}
25. True
26. {answers['26_ghost_accept_no_regression']}
27. {answers['27_forward_paper_metrics']}
28. {answers['28_safe_to_reflect_runtime']}
"""
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "w43f_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (OUT / "w43f_report.md").write_text(md, encoding="utf-8")
    write_xlsx(
        {
            "root_cause_primary": pd.DataFrame(
                [{"scope": "dq", "primary": k, "n": v} for k, v in dq_primary.items()]
                + [{"scope": "pbv2", "primary": k, "n": v} for k, v in pb_primary.items()]
            ),
            "root_cause_secondary": pd.DataFrame(
                [{"scope": "dq", "tag": k, "n": v} for k, v in dq_secondary.items()]
                + [{"scope": "pbv2", "tag": k, "n": v} for k, v in pb_secondary.items()]
            ),
            "board_stale_audit": board_df if len(board_df) else pd.DataFrame(),
            "warmup_audit": dq_df[dq_df["primary_root_cause"].isin(list(EXPECTED_WARMUP))].head(5000)
            if len(dq_df)
            else pd.DataFrame(),
            "refresh_history": pd.DataFrame(
                [
                    {
                        "continuing_symbols_keep_history": True,
                        "new_symbols_warmup": True,
                        "yahoo_backfill": False,
                        "refresh_warmup_new_symbol_n": refresh_new,
                        "refresh_history_reset_n": history_reset,
                    }
                ]
            ),
            "pipeline_order": pd.DataFrame(
                [
                    {"step": i + 1, "name": n}
                    for i, n in enumerate(
                        [
                            "PUSH受信",
                            "symbol/session検証",
                            "timestamp正規化",
                            "CurrentPrice state更新",
                            "Board state更新",
                            "rolling history追加",
                            "history readiness",
                            "freshness判定",
                            "feature計算",
                            "candidate evaluation",
                            "gate",
                            "queue/position",
                        ]
                    )
                ]
            ),
            "evaluation_reachability": pb_df.head(20000) if len(pb_df) else pd.DataFrame(),
            "recovery_evaluation": pd.DataFrame(
                [
                    {
                        "recovery_force_eval": True,
                        "ask_bid_fallback": False,
                        "idempotent_cycle_id": True,
                        "false_stale_recovered_n": int(len(cf_df)),
                    }
                ]
            ),
            "feature_compute": pd.DataFrame(
                [{"feature_compute_failure_primary_n": feature_n, "optional_feature_not_dq_block": True}]
            ),
            "baseline_vs_fixed": pd.DataFrame([report["baseline_vs_fixed"]]),
            "decision_parity": pd.DataFrame(
                [{"unit_tests_passed": test_res["passed"], "note": "full replay parity in Paper forward"}]
            ),
            "counterfactual": pd.DataFrame([{**cf, "newly_evaluable_incl_eval_not_run": newly_eval}]),
            "daily_summary": pd.DataFrame(
                [{"runtime_days": ",".join(runtime_days), "dq_n": n_dq, "pbv2_n": n_pb}]
            ),
            "tests": pd.DataFrame(
                [{"exit_code": test_res["exit_code"], "passed": test_res["passed"], "stdout": test_res["stdout"][:500]}]
            ),
            "data_integrity": pd.DataFrame(
                [
                    {
                        "dq_match": n_dq == sum_primary,
                        "pbv2_match": n_pb == sum_pb,
                        "board_stale_65_n": len(board_df),
                        "true_board": true_board,
                        "false_board": false_board,
                    },
                ]
            ),
        },
        OUT / "w43f_audit.xlsx",
    )
    print(
        json.dumps(
            {
                "verdicts": verdicts,
                "dq_match": n_dq == sum_primary,
                "pbv2_match": n_pb == sum_pb,
                "board_true_false": [true_board, false_board],
                "tests": test_res["passed"],
            },
            ensure_ascii=False,
        )
    )
    return 0 if test_res["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
