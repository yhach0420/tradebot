"""E1_X5 BASE binding for Phase B comparison gates (Phase A-R1 §10).

Binds the comparison base to concrete artifacts + SHAs. If the base cannot be
compared under the same window-inclusion mask, the run stops with
NOT_COMPARABLE_BASE => P1_R1_BLOCKED (never silently swaps to another base).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .store import sha256_file

PLAN21_REPORT = (
    Path.home() / "e1x6_research_store" / "published"
    / "e1_x6_plan21_day_robust_20260721_20260731" / "report.json"
)
PLAN21_REPORT_SHA_EXPECTED = (
    "a007b1d760cdffc284ef54d45f95edb95f4b93883c922814fd5cb5021c2938cf"
)

DD_FORMULA = (
    "realized_sequence_max_dd: max drawdown of cumulative net PnL in JST exit "
    "order (tie: exit_time, then symbol) — day_robust_gates.realized_sequence_max_dd"
)
STOP_LOSS_FORMULA = (
    "stop_loss_total: sum of negative net pnl over exit_reason==STOP trades — "
    "day_robust_gates.stop_loss_total"
)


def build_base_binding(new_mask_included_windows: list[str]) -> dict[str, Any]:
    """Verify + bind the E1_X5 base. Returns binding dict with 'comparable' flag."""
    if not PLAN21_REPORT.is_file():
        return {"comparable": False, "reason": "NOT_COMPARABLE_BASE: plan21 report.json missing"}
    sha = sha256_file(PLAN21_REPORT)
    if sha != PLAN21_REPORT_SHA_EXPECTED:
        return {"comparable": False,
                "reason": f"NOT_COMPARABLE_BASE: plan21 report sha mismatch {sha[:16]}"}
    rep = json.loads(PLAN21_REPORT.read_text(encoding="utf-8"))
    s1 = rep.get("stage1") or {}
    base = s1.get("base_x5") or {}
    if not base:
        return {"comparable": False, "reason": "NOT_COMPARABLE_BASE: base_x5 missing in stage1"}

    # stage-1 mask: 17 included AM/PM windows (7/21 AM excluded). Comparability
    # requires the new mask's included-window set to equal the stage-1 set.
    stage1_windows = sorted(
        f"{d}_{ap}"
        for d in ("20260721", "20260722", "20260723", "20260724", "20260727",
                  "20260728", "20260729", "20260730", "20260731")
        for ap in ("AM", "PM")
        if not (d == "20260721" and ap == "AM")
    )
    new_set = sorted(new_mask_included_windows)
    comparable = new_set == stage1_windows
    return {
        "comparable": comparable,
        "reason": "" if comparable else (
            "NOT_COMPARABLE_BASE: included-window sets differ; "
            f"stage1={stage1_windows} new={new_set}"
        ),
        "artifact_path": str(PLAN21_REPORT),
        "artifact_sha256": sha,
        "stage1_run_id": s1.get("run_id"),
        "stage1_p1_sha256": s1.get("p1_sha256"),
        "stage1_registry_sha256": s1.get("registry_sha256"),
        "analysis_mask": "stage-1 session mask: 17 included AM/PM windows (20260721 AM excluded)",
        "source_binding": "stage-1 P1 lock (p1_sha256 above) freezes stage-1 source manifest",
        "cost_bps_once": rep.get("cost_bps_once"),
        "lot": 100,
        "cap": 5,
        "dd_formula": DD_FORMULA,
        "stop_loss_formula": STOP_LOSS_FORMULA,
        "base_metrics": {
            "completed_trades": base.get("n"),
            "pnl": base.get("pnl"),
            "max_dd": base.get("max_dd"),
            "stop_loss_total": base.get("stop_loss_total"),
        },
        "comparable_scope": (
            "day-level totals over the identical 17 included AM/PM windows; "
            "grid-level R1 constraints (600s horizon etc.) apply to candidates "
            "only and are documented as a difference, not silently hidden"
        ),
    }
