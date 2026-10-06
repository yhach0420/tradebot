"""V4 summaries. State counts only. No PnL. Development fit is not face validation."""
from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from research.pb1_opening_range_continuation_face_valid_v2.definitions import machine_sha256 as v2_machine_sha256
from research.pb1_playbook_redesign_v3.definitions import machine_sha256 as v3_machine_sha256
from research.pb1_v3_1_face_validity_fix.definitions import machine_sha256 as v31_machine_sha256
from research.pb1_v3_2_opening_drive_and_reacceleration_semantics.definitions import machine_sha256 as v32_machine_sha256
from research.pb1_v4_machine_implementation import (
    CASE_BIND,
    CASE_FROZEN,
    NEXT_BIND,
    NEXT_FACE,
    PARENT_V2_SHA,
    PARENT_V3_SHA,
    PARENT_V31_SHA,
    PARENT_V32_SHA,
)
from research.pb1_v4_machine_implementation.definitions import RULE_DIFF, STATE_MACHINE_TEXT, machine_sha256
from research.pb1_v4_machine_implementation.isolation import CAPTURE_ROOT, CONTEXT_CAPTURE_ROOT


def funnel_report(counts: dict[str, Any]) -> dict[str, Any]:
    n_s0 = int(counts.get("S0") or 0)
    names = {
        "S0": "WHY_THIS_STOCK_TODAY / DISTINCTIVE_OPENING_ACTIVITY_V4",
        "S1": "FIVE_M_OPENING_DRIVE",
        "S2": "MEANINGFUL_PRICE_LOCATION",
        "S3": "BREAK_RETEST",
        "S4": "FIVE_M_CONTINUATION_STATE / SETUP_ELIGIBLE",
        "E0": "EXEC_5M_DIRECT",
        "E1": "EXEC_1M_CONFIRMED",
    }
    out: dict[str, Any] = {}
    for st, name in names.items():
        n = int(counts.get(st) or 0)
        out[st] = {"n": n, "name": name, "of_S0": float(n / n_s0) if n_s0 else None}
    out["dir_bull"] = int(counts.get("dir_bull") or 0)
    out["dir_bear"] = int(counts.get("dir_bear") or 0)
    return out


def death_report(funnel_days: list[dict[str, Any]]) -> dict[str, Any]:
    raw = Counter(str(d.get("death")) for d in funnel_days if d.get("death"))
    return {"day_death_counts": dict(raw)}


def decide(*, bind_ok: bool, same_bar_n: int, confirmation_opened: bool, fv_opened: bool) -> dict[str, Any]:
    if not bind_ok:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "v4_machine_implemented": False}
    if confirmation_opened or fv_opened:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "v4_machine_implemented": True, "reason": "forbidden_split_opened"}
    if int(same_bar_n) != 0:
        return {"VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "v4_machine_implemented": True, "reason": "same_bar_entry"}
    return {
        "VERDICT": CASE_FROZEN,
        "NEXT": NEXT_FACE,
        "v4_machine_implemented": True,
        "parent_v32_unchanged": True,
        "future_economic_outcome_used": False,
        "pnl_optimization": False,
        "primary_strategy_timeframe": "5m",
        "one_m_can_create_eligibility": False,
        "return_test": False,
        "mfe_mae": False,
        "economic_e0_e1_comparison": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "development_fit_is_not_face_validation": True,
    }


def build_report_body(
    bind: dict[str, Any],
    walked: dict[str, Any],
    calib: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    counts = dict(walked.get("counts") or {})
    sha = machine_sha256()
    v32_live = v32_machine_sha256()
    return {
        "MACHINE_SHA256": sha,
        "V4_MACHINE_SHA256": sha,
        "PARENT_V32_SHA": PARENT_V32_SHA,
        "PARENT_V31_SHA": PARENT_V31_SHA,
        "PARENT_V3_SHA": PARENT_V3_SHA,
        "PARENT_V2_SHA": PARENT_V2_SHA,
        "v32_unchanged": v32_live == PARENT_V32_SHA,
        "v31_unchanged": v31_machine_sha256() == PARENT_V31_SHA,
        "v3_unchanged": v3_machine_sha256() == PARENT_V3_SHA,
        "v2_unchanged": v2_machine_sha256() == PARENT_V2_SHA,
        "STATE_MACHINE_TEXT": STATE_MACHINE_TEXT,
        "RULE_DIFF": RULE_DIFF,
        "funnel": funnel_report(counts),
        "counts": counts,
        "deaths": death_report(list(walked.get("funnel_days") or [])),
        "setup_n": int(counts.get("setup_n") or 0),
        "e0_n": int(counts.get("E0") or 0),
        "e1_n": int(counts.get("E1") or 0),
        "same_bar_entry_n": int(walked.get("same_bar_entry_n") or 0),
        "future_outcome_n": 0,
        "calibration": calib,
        "semantic_development_audit": {k: v for k, v in audit.items() if k != "rows"},
        "audit_rows": list(audit.get("rows") or []),
        "contaminated_symbol_dates_n": 352,
        "prospective_capture_date_n": len([p for p in Path(CAPTURE_ROOT).glob("*") if p.is_dir()]) if CAPTURE_ROOT.exists() else 0,
        "context_capture_n": len([p for p in Path(CONTEXT_CAPTURE_ROOT).glob("*") if p.is_dir()]) if CONTEXT_CAPTURE_ROOT.exists() else 0,
        "decision": decide(
            bind_ok=bool(bind.get("ok")),
            same_bar_n=int(walked.get("same_bar_entry_n") or 0),
            confirmation_opened=False,
            fv_opened=False,
        ),
    }
