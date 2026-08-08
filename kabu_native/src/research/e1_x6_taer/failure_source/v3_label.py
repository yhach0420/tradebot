"""V3 label contract: separate opportunity target vs scenario vs features."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional


def _scenario_group(scenario_id: Optional[str]) -> str:
    s = str(scenario_id or "")
    for i in range(1, 8):
        if s.startswith(f"S{i}_") or s == f"S{i}":
            return f"S{i}"
    return "UNKNOWN"


def _scenario_valid(scenario_id: Optional[str], s7_reason: Optional[str] = None) -> tuple[bool, Optional[str]]:
    """Unique S1–S7 classification; CONFLICTING_SCENARIO => invalid scenario only."""
    s = str(scenario_id or "")
    if not s:
        return False, "MISSING_SCENARIO"
    if s7_reason == "CONFLICTING_SCENARIO":
        return False, "CONFLICTING_SCENARIO"
    # Prior path used S7_CENSORED_OR_OTHER as a valid S7 bucket label
    if s.startswith("S7"):
        if s7_reason in ("CONFLICTING_SCENARIO",):
            return False, "CONFLICTING_SCENARIO"
        return True, None
    if any(s.startswith(f"S{i}_") for i in range(1, 7)):
        return True, None
    return False, "UNCLASSIFIED_SCENARIO"


def opportunity_target_valid(row: dict[str, Any]) -> tuple[bool, list[str]]:
    """Scenario ID is NOT used. Economic envelope fields must be present."""
    fails = []
    if row.get("entry_price") is None or not (float(row["entry_price"]) > 0):
        fails.append("NO_ENTRY_ASK")
    if not row.get("evaluable"):
        fails.append("NOT_EVALUABLE")
    for k in ("best_net_pnl_bps_300s", "worst_net_pnl_bps_300s", "adverse_before_best_bps"):
        if row.get(k) is None:
            fails.append(f"MISSING_{k.upper()}")
    # same symbol/day/session encoded in row construction; require fields present
    for k in ("symbol", "day", "session"):
        if not row.get(k):
            fails.append(f"MISSING_{k.upper()}")
    # integrity: if present must not be FAIL; V2 rows may omit — treat PASS if evaluable + metrics
    integ = row.get("integrity_status")
    if integ is None:
        integ = "PASS" if not fails else "FAIL"
    if integ == "FAIL":
        fails.append("INTEGRITY_FAIL")
    return (len(fails) == 0), fails


def build_label_audit(
    opp_reps: list[dict[str, Any]],
    s7_by_episode: Optional[dict[str, str]] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    s7_by_episode = s7_by_episode or {}
    rows = []
    target_fail_reasons = Counter()
    for r in opp_reps:
        tgt_ok, tgt_fails = opportunity_target_valid(r)
        for f in tgt_fails:
            target_fail_reasons[f] += 1
        scen = r.get("scenario_id_prior")
        s7_reason = s7_by_episode.get(r["episode_id"])
        scen_ok, scen_reason = _scenario_valid(scen, s7_reason)
        # path seconds
        path_sec = None
        if r.get("best_exit_time") is not None and r.get("entry_time") is not None:
            path_sec = float(r["best_exit_time"]) - float(r["entry_time"])
        elif r.get("time_to_net_positive_sec") is not None:
            path_sec = None
        rows.append({
            "cluster_id": r.get("overlap_cluster_id"),
            "episode_id": r["episode_id"],
            "setup_type": r.get("setup_type"),
            "day": r.get("day"),
            "symbol": r.get("symbol"),
            "session": r.get("session"),
            "opportunity_target_valid": tgt_ok,
            "scenario_label_valid": scen_ok,
            "scenario_invalid_reason": None if scen_ok else scen_reason,
            "scenario_group": _scenario_group(scen),
            "scenario_id_prior": scen,  # audit only, not a feature
            "best_net_pnl_bps_300s": r.get("best_net_pnl_bps_300s"),
            "best_net_pnl_bps_60s": r.get("best_net_pnl_bps_60s"),
            "best_net_pnl_bps_120s": r.get("best_net_pnl_bps_120s"),
            "adverse_before_best_bps": r.get("adverse_before_best_bps"),
            "time_to_net_positive_sec": r.get("time_to_net_positive_sec"),
            "path_seconds": path_sec,
            "path_complete": r.get("path_complete"),
            "integrity_status": r.get("integrity_status") or ("PASS" if tgt_ok else "FAIL"),
            "net_plus_5bps": (
                None if r.get("best_net_pnl_bps_300s") is None
                else bool(float(r["best_net_pnl_bps_300s"]) >= 5.0)
            ),
        })

    n = len(rows)
    tgt_n = sum(1 for x in rows if x["opportunity_target_valid"])
    scen_n = sum(1 for x in rows if x["scenario_label_valid"])
    both_split = sum(1 for x in rows if x["opportunity_target_valid"] and not x["scenario_label_valid"])
    summary = {
        "total_cluster_n": n,
        "opportunity_target_valid_n": tgt_n,
        "opportunity_target_valid_rate": tgt_n / n if n else 0.0,
        "scenario_label_valid_n": scen_n,
        "scenario_label_valid_rate": scen_n / n if n else 0.0,
        "target_valid_but_scenario_invalid_n": both_split,
        "target_invalid_reason_counts": dict(target_fail_reasons),
        "opportunity_label_contract": (
            "OPPORTUNITY_LABEL_CONTRACT_PASS" if tgt_n == n and n == 399
            else ("OPPORTUNITY_LABEL_CONTRACT_PASS" if tgt_n == n
                  else "OPPORTUNITY_LABEL_CONTRACT_PARTIAL")
        ),
    }
    if n == 399 and tgt_n == 399:
        summary["opportunity_label_contract"] = "OPPORTUNITY_LABEL_CONTRACT_PASS"
    return rows, summary
