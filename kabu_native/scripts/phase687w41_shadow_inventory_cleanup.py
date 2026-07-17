#!/usr/bin/env python3
"""Phase687W41: Shadow Inventory Cleanup (research labels only).

MAINLINE / ENTRY / EXIT / orders / Shadow adoption unchanged.
Reflects W40 results + readiness IHC recompute (20260716 AM).
"""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPORTS = NATIVE / "results" / "reports"
W40 = REPORTS / "phase687w40_shadow_history_summary"
OUT = REPORTS / "phase687w41_shadow_inventory_cleanup"
JST = ZoneInfo("Asia/Tokyo")

PRECISION_NOTE = (
    "3 trades blocked. "
    "Portfolio replay Δ -3900. "
    "Rejected because profitable trades were removed. "
    "Not recommended."
)

ECONOMICS_NOTE = (
    "44 evaluable. "
    "0 trades blocked. "
    "No fire. "
    "Δ0. "
    "RETIRE_CANDIDATE."
)

# Already removed in prior phases (YAML/668) — stay REMOVED
REMOVED = {
    "exit_shadow_monitor_t2",
    "exit_shadow_monitor_t2_t3",
    "exit_shadow_monitor_t3",
    "pbv2_rise5_shadow",
    "vwap_shadow_reject",
}

PROMOTED = {"pbv2_flat_band_shadow"}

# W41 new retirements + W40 disabled REMOVE candidates not yet REMOVED
RETIRE_CANDIDATE = {
    "readiness_precision_shadow",
    "readiness_economics_shadow",
    "board_collapse_profit_exit",
    "loss_acceleration_exit",
    "low_liquidity_shadow",
    "profit_protect_exit",
}

# Research-only archive — keep in inventory, no Discord ops monitoring
KEEP = {
    "phase632_pbv2_profit_filter",
    "phase633_combo_soft_robustness",
    "phase634_pbv2_rise5_full_period",
    "phase643_position_sizing_shadow",
    "phase647_momentum_low_trend",
    "phase648_rise5_rise10_analysis",
    "phase649_flat_band_guard",
    "readiness_refined_h_shadow",
}

# Among ACTIVE: only Priority A continues future monitoring
PRIORITY_A = [
    "board_dynamic_trailing_shadow",
    "flat_weak_range_shadow",
    "pullback_misread_guard_shadow",
    "microsequence_recovery_fail_shadow",
    "realtime_board_exit_shadow",
    "imbalance_shadow",
]

PRIORITY_B = [
    "entry_expectancy_score_shadow",
    "entry_price_risk_guard_shadow",
    "limit_up_proximity_entry_guard_shadow",
    "extended_entry_shadow",
    "equity_dynamic_stop_shadow",
    "post_entry_forward_shadow",
    "volume_gate_relaxation_shadow",
    "trading_value_shadow_gate",
    "quality_formula_shadow",
]

PRIORITY_C = [
    "board_imbalance_shadow",
    "boundary_forward_shadow",
    "classic_momentum_forward_shadow",
    "live_config_forward_shadow",
    "live_config_transition_shadow",
    "risk_sizing_forward_shadow",
    "sector_heat_forward_shadow",
    "stop_low_mfe_guard_net_shadow",
]

RETIRE_REASONS = {
    "readiness_precision_shadow": (
        "Evaluable 44; block 3; PnL -8100→-12000; Δ-3900; "
        "removed winners (6996.T/9278.T/3994.T); portfolio replay no improvement. "
        + PRECISION_NOTE
    ),
    "readiness_economics_shadow": (
        "Evaluable 44; block 0; no fire; Δ0. " + ECONOMICS_NOTE
    ),
    "board_collapse_profit_exit": "Disabled EXIT candidate; non-positive cumulative delta (W40).",
    "loss_acceleration_exit": "Disabled EXIT candidate; non-positive cumulative delta (W40).",
    "low_liquidity_shadow": "Disabled ENTRY reject; non-positive cumulative delta (W40).",
    "profit_protect_exit": "Disabled EXIT candidate; non-positive cumulative delta (W40).",
}


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _wc(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if v is None else v) for k, v in ((k, r.get(k)) for k in cols)})


def _wm(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def assign_status(shadow_id: str, prior_status: str) -> str:
    if shadow_id in REMOVED:
        return "REMOVED"
    if shadow_id in PROMOTED:
        return "PROMOTED"
    if shadow_id in RETIRE_CANDIDATE:
        return "RETIRE_CANDIDATE"
    if shadow_id in KEEP or prior_status == "RESEARCH_ONLY":
        return "KEEP"
    if prior_status in ("ACTIVE_SHADOW", "DISABLED"):
        # DISABLED without explicit RETIRE stays KEEP only if listed; else ACTIVE
        if prior_status == "DISABLED":
            return "RETIRE_CANDIDATE"
        return "ACTIVE"
    if prior_status in ("REMOVED", "PROMOTED_TO_MAINLINE"):
        return "REMOVED" if prior_status == "REMOVED" else "PROMOTED"
    return "KEEP"


def assign_priority(shadow_id: str, status: str) -> str:
    if status != "ACTIVE":
        return ""
    if shadow_id in PRIORITY_A:
        return "A"
    if shadow_id in PRIORITY_B:
        return "B"
    if shadow_id in PRIORITY_C:
        return "C"
    return "C"


def priority_reason(shadow_id: str, priority: str) -> str:
    if not priority:
        return ""
    reasons = {
        "board_dynamic_trailing_shadow": "Largest exit-overlay Δ; mainline-adjacent monitoring",
        "flat_weak_range_shadow": "Positive CAP replay Δ; multi-session presence",
        "pullback_misread_guard_shadow": "Negative CAP/summary Δ; watch for retire vs keep",
        "microsequence_recovery_fail_shadow": "Small positive CAP; IHC lane still live",
        "realtime_board_exit_shadow": "Board EXIT family; ops-relevant overlay",
        "imbalance_shadow": "Live imbalance path; summary PF observability",
    }
    if priority == "A":
        return reasons.get(shadow_id, "Ops-relevant signal; Priority A monitor")
    if priority == "B":
        return "Wired/active but weak multi-day evidence; deprioritized"
    return "No material fire/Δ in W40 consolidation; archive-level observe only"


def patch_w40() -> dict[str, Any]:
    """Remove Precision 'no change' wording; record corrected portfolio replay note."""
    note = PRECISION_NOTE
    report_path = W40 / "phase687w40_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    readiness = report.setdefault("required_answers", {}).setdefault("11_readiness", {})
    prec = readiness.setdefault("precision", {})
    prec["w41_correction"] = {
        "text": note,
        "evaluable": 44,
        "blocked": 3,
        "blocked_symbols": ["6996.T", "9278.T", "3994.T"],
        "portfolio_replay_before_pnl": -8100.0,
        "portfolio_replay_after_pnl": -12000.0,
        "portfolio_replay_delta_yen": -3900.0,
        "note": (
            "W40 event-flag CAP showed Δ0 due to missing readiness_precision_shadow_block "
            "on accepted rows; IHC recompute + CAP removal is Δ-3900."
        ),
    }
    # Correct misleading CAP0 narrative fields used as 「変化なし」
    if "cap_replay" in prec:
        prec["cap_replay"]["w41_corrected_delta_pnl"] = -3900.0
        prec["cap_replay"]["w41_note"] = note
    if "total" in prec:
        prec["total"]["cap_delta_pnl_w41_corrected"] = -3900.0
        prec["total"]["w41_note"] = note
    readiness["precision_summary_text"] = note
    report["required_answers"]["11_readiness_text"] = {
        "precision": note,
        "economics": "0 trades blocked. Δ0. No fire. (true no-change via block=0)",
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")

    for md_name in ("phase687w40_decision.md", "shadow_decision.md"):
        md_path = W40 / md_name
        text = md_path.read_text(encoding="utf-8")
        old = "11. Readiness: precision CAPΔ=0.0 economics CAPΔ=0.0"
        new = (
            "11. Readiness Precision: "
            + note
            + " | Economics: 0 trades blocked; Δ0 (block=0)."
        )
        if old in text:
            text = text.replace(old, new)
        else:
            # idempotent / alternate
            text = text.replace(
                "precision CAPΔ=0.0 economics CAPΔ=0.0",
                "Precision: " + note + " | Economics: block=0 Δ0",
            )
        md_path.write_text(text, encoding="utf-8")

    # status matrix reason for precision
    matrix_path = W40 / "shadow_status_matrix.csv"
    rows = _read_csv(matrix_path)
    for r in rows:
        if r.get("shadow_id") == "readiness_precision_shadow":
            r["cap_replay_delta_pnl"] = "-3900.0"
            r["reason"] = note
        if r.get("shadow_id") == "readiness_economics_shadow":
            r["reason"] = "0 trades blocked; no fire; Δ0 (true no-change via block=0)"
    _wc(matrix_path, rows)

    inv_path = W40 / "shadow_inventory.csv"
    if inv_path.exists():
        inv = _read_csv(inv_path)
        for r in inv:
            if r.get("shadow_id") == "readiness_precision_shadow":
                r["recommendation_reason"] = note
                r.pop("reason", None)
            elif r.get("shadow_id") == "readiness_economics_shadow":
                r["recommendation_reason"] = "0 trades blocked; no fire; Δ0 (block=0)"
                r.pop("reason", None)
            else:
                r.pop("reason", None)
        # Preserve original column order without accidental extra "reason"
        cols = [
            "shadow_id",
            "name",
            "first_phase",
            "purpose",
            "kind",
            "category_raw",
            "entry_or_exit",
            "runtime_or_research",
            "registry_status",
            "registry_decision",
            "phase668_decision",
            "phase668_rationale",
            "status",
            "recommendation",
            "recommendation_reason",
        ]
        with inv_path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            w.writeheader()
            for r in inv:
                w.writerow({k: r.get(k, "") for k in cols})

    return {"precision_note": note, "patched": True}


def build_inventory() -> dict[str, Any]:
    matrix = _read_csv(W40 / "shadow_status_matrix.csv")
    total_by_id = {r["shadow_id"]: r for r in _read_csv(W40 / "shadow_total_summary.csv")}

    inventory: list[dict[str, Any]] = []
    for r in matrix:
        sid = r["shadow_id"]
        prior = r.get("status") or ""
        status = assign_status(sid, prior)
        # Force explicit sets (overrides edge cases)
        if sid in REMOVED:
            status = "REMOVED"
        elif sid in PROMOTED:
            status = "PROMOTED"
        elif sid in RETIRE_CANDIDATE:
            status = "RETIRE_CANDIDATE"
        elif sid in KEEP:
            status = "KEEP"
        elif prior == "ACTIVE_SHADOW":
            status = "ACTIVE"

        priority = assign_priority(sid, status)
        prior_eval = "RESEARCH_ONLY" if prior == "RESEARCH_ONLY" else prior
        if sid in ("readiness_precision_shadow", "readiness_economics_shadow"):
            # User-facing evaluation transition for readiness lanes
            prior_eval = "RESEARCH_ONLY"

        reason = RETIRE_REASONS.get(sid) or r.get("reason") or ""
        if status == "ACTIVE":
            reason = priority_reason(sid, priority)
        elif status == "KEEP":
            reason = reason or "Research-only archive; keep in inventory, not ops-monitored"
        elif status == "PROMOTED":
            reason = "Already promoted / mainline equivalent"
        elif status == "REMOVED":
            reason = reason or "Previously retired; do not re-enable"

        tot = total_by_id.get(sid, {})
        inventory.append(
            {
                "shadow_id": sid,
                "name": r.get("name") or sid,
                "first_phase": r.get("first_phase"),
                "kind": r.get("kind"),
                "prior_status_w40": prior,
                "prior_evaluation": prior_eval,
                "status": status,
                "priority": priority,
                "future_monitor": "YES" if priority == "A" else "NO",
                "cumulative_delta_yen": tot.get("cumulative_delta_yen") or r.get("cumulative_delta_yen"),
                "cap_replay_delta_pnl": (
                    "-3900.0"
                    if sid == "readiness_precision_shadow"
                    else (tot.get("cap_delta_pnl") or r.get("cap_replay_delta_pnl"))
                ),
                "reason": reason,
                "mainline_changed": False,
                "shadow_adopted": False,
            }
        )

    # Sanity: every former ACTIVE_SHADOW not reclassified must be ACTIVE
    active_ids = {r["shadow_id"] for r in inventory if r["status"] == "ACTIVE"}
    expected_active = set(PRIORITY_A + PRIORITY_B + PRIORITY_C)
    missing = expected_active - active_ids
    extra = active_ids - expected_active
    if missing or extra:
        raise SystemExit(f"ACTIVE set mismatch missing={sorted(missing)} extra={sorted(extra)}")

    return {"inventory": inventory}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    w40_patch = patch_w40()
    built = build_inventory()
    inv = built["inventory"]

    counts = {
        "total": len(inv),
        "ACTIVE": sum(1 for r in inv if r["status"] == "ACTIVE"),
        "KEEP": sum(1 for r in inv if r["status"] == "KEEP"),
        "RETIRE_CANDIDATE": sum(1 for r in inv if r["status"] == "RETIRE_CANDIDATE"),
        "PROMOTED": sum(1 for r in inv if r["status"] == "PROMOTED"),
        "REMOVED": sum(1 for r in inv if r["status"] == "REMOVED"),
    }
    pri = {
        "A": sorted(r["shadow_id"] for r in inv if r.get("priority") == "A"),
        "B": sorted(r["shadow_id"] for r in inv if r.get("priority") == "B"),
        "C": sorted(r["shadow_id"] for r in inv if r.get("priority") == "C"),
    }

    _wc(OUT / "shadow_inventory_updated.csv", inv)
    _wc(
        OUT / "shadow_status_summary.csv",
        [
            {"status": "ACTIVE", "count": counts["ACTIVE"]},
            {"status": "KEEP", "count": counts["KEEP"]},
            {"status": "RETIRE_CANDIDATE", "count": counts["RETIRE_CANDIDATE"]},
            {"status": "PROMOTED", "count": counts["PROMOTED"]},
            {"status": "REMOVED", "count": counts["REMOVED"]},
            {"status": "TOTAL", "count": counts["total"]},
        ],
    )
    _wc(
        OUT / "shadow_priority_matrix.csv",
        [
            {
                "shadow_id": r["shadow_id"],
                "name": r["name"],
                "status": r["status"],
                "priority": r["priority"],
                "future_monitor": r["future_monitor"],
                "reason": r["reason"],
            }
            for r in inv
            if r["status"] == "ACTIVE"
        ],
    )
    _wc(
        OUT / "shadow_retirement_candidates.csv",
        [
            {
                "shadow_id": r["shadow_id"],
                "name": r["name"],
                "prior_status_w40": r["prior_status_w40"],
                "prior_evaluation": r["prior_evaluation"],
                "status": r["status"],
                "cap_replay_delta_pnl": r["cap_replay_delta_pnl"],
                "cumulative_delta_yen": r["cumulative_delta_yen"],
                "reason": r["reason"],
            }
            for r in inv
            if r["status"] == "RETIRE_CANDIDATE"
        ],
    )

    required = {
        "1_shadow_total": counts["total"],
        "2_active": counts["ACTIVE"],
        "3_keep": counts["KEEP"],
        "4_retire_candidates": counts["RETIRE_CANDIDATE"],
        "5_promoted": counts["PROMOTED"],
        "6_removed": counts["REMOVED"],
        "7_priority_a": pri["A"],
        "8_priority_b": pri["B"],
        "9_priority_c": pri["C"],
        "10_submit_cancel": {"submit": 0, "cancel": 0},
        "11_mainline_unchanged": True,
    }

    report = {
        "phase": "Phase687W41",
        "title": "Shadow Inventory Cleanup",
        "verdict": ["SHADOW_INVENTORY_CLEANED", "RETIREMENT_CANDIDATES_CONFIRMED"],
        "generated_at": datetime.now(JST).isoformat(),
        "constraints": {
            "mainline_changed": False,
            "entry_exit_changed": False,
            "real_orders_changed": False,
            "shadow_adopted": False,
            "submit": 0,
            "cancel": 0,
        },
        "w40_patch": w40_patch,
        "readiness": {
            "precision": {
                "prior_evaluation": "RESEARCH_ONLY",
                "status": "RETIRE_CANDIDATE",
                "evaluable": 44,
                "blocked": 3,
                "pnl_before": -8100,
                "pnl_after": -12000,
                "delta_yen": -3900,
                "note": PRECISION_NOTE,
            },
            "economics": {
                "prior_evaluation": "RESEARCH_ONLY",
                "status": "RETIRE_CANDIDATE",
                "evaluable": 44,
                "blocked": 0,
                "delta_yen": 0,
                "note": ECONOMICS_NOTE,
            },
        },
        "counts": counts,
        "priorities": pri,
        "future_monitor_only_priority_a": True,
        "required_answers": required,
        "note": (
            "Status labels are inventory/research only. "
            "No runtime Shadow enable/disable, no MAINLINE/ENTRY/EXIT/order changes."
        ),
    }
    _wj(OUT / "phase687w41_report.json", report)

    md = f"""# Phase687W41 Shadow Inventory Cleanup

## Verdict: `SHADOW_INVENTORY_CLEANED` / `RETIREMENT_CANDIDATES_CONFIRMED`

### Constraints
- MAINLINE unchanged: **True**
- ENTRY/EXIT unchanged: **True**
- Real orders unchanged: **True**
- Shadow adoption: **none**
- submit/cancel: **0/0**

### Required answers
1. Shadow total: **{counts['total']}**
2. Active: **{counts['ACTIVE']}**
3. KEEP: **{counts['KEEP']}**
4. RETIRE候補: **{counts['RETIRE_CANDIDATE']}**
5. PROMOTED: **{counts['PROMOTED']}**
6. REMOVED: **{counts['REMOVED']}**
7. Priority A: `{pri['A']}`
8. Priority B: `{pri['B']}`
9. Priority C: `{pri['C']}`
10. submit/cancel: **0/0**
11. MAINLINE unchanged: **True**

### Readiness transitions
- `readiness_precision_shadow`: RESEARCH_ONLY → **RETIRE_CANDIDATE** — {PRECISION_NOTE}
- `readiness_economics_shadow`: RESEARCH_ONLY → **RETIRE_CANDIDATE** — {ECONOMICS_NOTE}

### W40 correction
- Removed Precision 「変化なし」 / CAPΔ=0 narrative.
- Replaced with: {PRECISION_NOTE}

### Future monitoring
- Only **Priority A** Active shadows continue monitoring.
- Priority B/C remain ACTIVE in inventory but are not future-monitor targets.
- KEEP = research archive (not ops-monitored).
- RETIRE_CANDIDATE / REMOVED / PROMOTED are not Priority-monitored.

### Method
- Seeded from W40 `shadow_status_matrix.csv`
- No runtime config / YAML Shadow enable changes in this phase
"""
    _wm(OUT / "decision.md", md)
    print(json.dumps({"out": str(OUT), "counts": counts, "priority_a": pri["A"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
