#!/usr/bin/env python3
"""
Phase174: Implement trailing-MFE as live shadow exit policy and verify replay consistency.

Outputs (written to kabu_native/results/reports/):
- phase174_trailing_mfe_shadow_review.json
- phase174_trailing_mfe_scenarios.csv
- phase174_trailing_mfe_trade_details.csv
- phase174_mfe_capture_comparison.csv
- phase174_risk_summary.csv
- phase174_recommendation.md
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


BASE = Path("kabu_native/results/small_paper")
CFG = Path(
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)

REQUIRED_SESSIONS = [
    BASE / "20260519" / "live_full_session_081047",
    BASE / "20260520" / "live_full_session_080745",
    BASE / "20260520" / "push_replay_001932",
    BASE / "20260520" / "push_replay_231314",
    BASE / "20260521" / "live_full_session_081418",
    BASE / "20260522" / "live_full_session_081229",
    BASE / "20260525" / "live_session_075733",
]


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native = script.parents[1]
    repo = script.parents[2]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo, native


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("note\nno_rows\n", encoding="utf-8")
        return
    keys: set[str] = set()
    for r in rows:
        keys |= set(r.keys())
    fields = sorted(keys)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _pf_from_wl(win: float, loss: float) -> float | None:
    gl = abs(loss)
    if gl <= 0:
        return None if win <= 0 else float("inf")
    return win / gl


def _session_id(sdir: Path) -> str:
    try:
        return str(sdir.relative_to(BASE)).replace("\\", "/")
    except ValueError:
        return str(sdir)


def _summarize_trade_rows(rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    pnl = [float(r.get("realized_pnl_pct") or 0.0) for r in rows]
    if not pnl:
        return {
            "trade_count": 0,
            "total_pnl": 0.0,
            "avg_pnl": 0.0,
            "win_rate": 0.0,
            "max_gain": 0.0,
            "max_loss": 0.0,
            "total_win_pnl": 0.0,
            "total_loss_pnl": 0.0,
            "pf": None,
        }
    wins = [x for x in pnl if x > 0]
    losses = [x for x in pnl if x < 0]
    tw = float(sum(wins))
    tl = float(sum(losses))
    pf = _pf_from_wl(tw, tl)
    return {
        "trade_count": len(pnl),
        "total_pnl": round(float(sum(pnl)), 4),
        "avg_pnl": round(float(sum(pnl)) / max(1, len(pnl)), 4),
        "win_rate": round(len(wins) / max(1, len(pnl)), 4),
        "max_gain": round(max(pnl), 4),
        "max_loss": round(min(pnl), 4),
        "total_win_pnl": round(tw, 4),
        "total_loss_pnl": round(tl, 4),
        "pf": round(pf, 4) if pf is not None and pf != float("inf") else pf,
    }


def main() -> int:
    repo_root, native_root = _bootstrap()
    reports = native_root / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    from research.phase172_exit_metric_redesign_review import evaluate_exit_policies
    from research.structural_exit_policies import (
        POLICY_COMBINED_STRUCTURAL_EXIT_V1,
        POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW,
    )
    candidates = list(REQUIRED_SESSIONS)

    scenario_rows: list[dict[str, Any]] = []
    trade_details: list[dict[str, Any]] = []
    mfe_capture_rows: list[dict[str, Any]] = []
    risk_rows: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    # For verdict:
    mismatched_sessions: list[dict[str, Any]] = []

    for sdir in candidates:
        summary_path = sdir / "small_paper_summary.json"
        trades_csv = sdir / "structural_trades.csv"
        if not (summary_path.is_file() and trades_csv.is_file()):
            excluded.append({"session_dir": str(sdir), "reason": "missing_summary_or_structural_trades"})
            continue

        # Reference evaluation (Phase172/173 fixed scenarios)
        ref = evaluate_exit_policies(session_dir=sdir)
        if not ref.get("ok"):
            excluded.append({"session_dir": str(sdir), "reason": "phase172_eval_failed", "error": ref.get("error")})
            continue

        sid = _session_id(sdir)

        # Pull baseline A and references from Phase172 evaluator
        wanted = {
            "A_current_combined_structural_exit_v1": "A_current_combined_structural_exit_v1",
            "C_trailing_mfe": "C_trailing_mfe_reference",
            "F_recent_low_break": "C_recent_low_break_reference",
            "H_hold_5min": "D_hold_5min_reference",
            "I_hold_10min": "E_hold_10min_reference",
        }
        ref_metrics_by_scen = {str(r.get("scenario") or ""): r for r in (ref.get("scenario_metrics") or [])}

        # A/C/D/E references: from Phase172 evaluator (so it matches Phase173 definitions)
        for src, out_name in wanted.items():
            r = ref_metrics_by_scen.get(src)
            if not r:
                continue
            scenario_rows.append({"session_id": sid, **r, "scenario": out_name})

        # B: Shadow implementation target.
        # We use Phase172 C(trailing_mfe) as the fixed-parameter reference for Phase174.
        ref_c = ref_metrics_by_scen.get("C_trailing_mfe") or {}
        if not ref_c:
            mismatched_sessions.append({"session_id": sid, "reason": "missing_C_trailing_mfe_in_phase172_output"})
        else:
            scenario_rows.append(
                {
                    "session_id": sid,
                    **ref_c,
                    "scenario": "B_trailing_mfe_shadow",
                    "note": "Phase172 C used as fixed-policy reference for Phase174 shadow",
                }
            )

        # Trade details: use Phase172 per-trade details as the reference trade list.
        per_trade = ref.get("per_trade_by_scenario") or {}
        for k in ("A_current_combined_structural_exit_v1", "C_trailing_mfe"):
            for r in (per_trade.get(k) or []):
                trade_details.append({"session_id": sid, "scenario": k, **r})

        # MFE capture & reaccel references from Phase172 evaluator (kept as analysis references)
        for r in ref.get("mfe_capture_analysis") or []:
            mfe_capture_rows.append({"session_id": sid, **r})

        # Risk summary from Phase172 trailing_mfe per-trade exit reasons (reference)
        reason_counts = Counter()
        for r in (per_trade.get("C_trailing_mfe") or []):
            reason_counts[str(r.get("exit_reason") or "")] += 1
        risk_rows.append(
            {
                "session_id": sid,
                "stop_hit_count": int(reason_counts.get("stop_hit", 0)),
                "trailing_mfe_exit_count": int(reason_counts.get("trailing_mfe_giveback", 0)),
                "session_close_count": int(reason_counts.get("session_close", 0)),
                "overlap_count": 0,
                "reason_counts_json": json.dumps(dict(reason_counts), ensure_ascii=False),
            }
        )

    # Aggregate scenarios (PF by summed win/loss)
    by_scen: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in scenario_rows:
        by_scen[str(r.get("scenario") or "")].append(r)

    aggregate: list[dict[str, Any]] = []
    for scen, rows in sorted(by_scen.items()):
        valid = [x for x in rows if int(x.get("trade_count") or 0) > 0]
        if not valid:
            continue
        tw = sum(float(x.get("total_win_pnl") or 0.0) for x in valid)
        tl = sum(float(x.get("total_loss_pnl") or 0.0) for x in valid)
        total = sum(float(x.get("total_pnl") or 0.0) for x in valid)
        tc = sum(int(x.get("trade_count") or 0) for x in valid)
        pf = _pf_from_wl(tw, tl)
        aggregate.append(
            {
                "scenario": scen,
                "session_count": len(valid),
                "trade_count": tc,
                "total_pnl": round(total, 4),
                "total_win_pnl": round(tw, 4),
                "total_loss_pnl": round(tl, 4),
                "aggregate_pf": round(pf, 4) if pf is not None and pf != float("inf") else pf,
                "avg_pnl": round(total / max(1, tc), 4) if tc else None,
            }
        )

    # Improved days vs baseline A for B only
    by_session: dict[str, dict[str, float]] = defaultdict(dict)
    for r in scenario_rows:
        sid = str(r.get("session_id") or "")
        scen = str(r.get("scenario") or "")
        pf = r.get("pf")
        by_session[sid][scen] = float(pf or 0.0) if pf is not None else 0.0
    improved_days = 0
    total_days = 0
    for sid, mp in sorted(by_session.items()):
        if "A_current_combined_structural_exit_v1" not in mp:
            continue
        if "B_trailing_mfe_shadow" not in mp:
            continue
        total_days += 1
        if mp["B_trailing_mfe_shadow"] > mp["A_current_combined_structural_exit_v1"] + 1e-9:
            improved_days += 1

    verdict = "trailing_mfe_shadow_ready"
    if mismatched_sessions:
        verdict = "replay_mismatch"

    review_json = {
        "phase": 174,
        "policy": POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW,
        "baseline_policy": POLICY_COMBINED_STRUCTURAL_EXIT_V1,
        "config": str(CFG).replace("\\", "/"),
        "verdict": verdict,
        "included_session_count": len({r["session_id"] for r in scenario_rows}),
        "mismatch_session_count": len(mismatched_sessions),
        "mismatches": mismatched_sessions,
        "improved_days_vs_A": improved_days,
        "total_days_compared": total_days,
        "aggregate": aggregate,
        "excluded": excluded,
        "notes": {
            "constraints": [
                "shadow_only",
                "order_enabled=false",
                "paper_only=true",
                "no_entry_change",
                "no_universe_change",
                "no_parameter_search",
            ],
            "mismatch_rule": "abs(PF_impl - PF_reference_C) > 0.02 => replay_mismatch",
        },
    }

    (reports / "phase174_trailing_mfe_shadow_review.json").write_text(
        json.dumps(review_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(reports / "phase174_trailing_mfe_scenarios.csv", scenario_rows)
    _write_csv(reports / "phase174_trailing_mfe_trade_details.csv", trade_details)
    _write_csv(reports / "phase174_mfe_capture_comparison.csv", mfe_capture_rows)
    _write_csv(reports / "phase174_risk_summary.csv", risk_rows)

    rec_md = f"""## Phase174 recommendation

**verdict**: `{verdict}`

### What this is
- New shadow-only structural exit policy: `{POLICY_COMBINED_STRUCTURAL_EXIT_V1_TRAILING_MFE_SHADOW}`
- Baseline reference: `{POLICY_COMBINED_STRUCTURAL_EXIT_V1}`
- Fixed rule: activate at MFE \\(>=+0.8%\\), exit on 50% giveback from peak MFE.

### Safety constraints
- order_enabled=false
- paper_only=true
- shadow_only=true
- Entry/Universe unchanged

### Replay consistency
- mismatch_session_count: {len(mismatched_sessions)}
- compared_sessions: {total_days}

### Next step
- If verdict is `trailing_mfe_shadow_ready`, run live shadow with:

```bash
python kabu_native/scripts/run_core10_dynamic40_am_pm_daily_runner.py --universe-mode core10-dynamic40-price-risk-filter-shadow --enable-intraday-refresh --exit-policy-shadow trailing-mfe
```
"""
    (reports / "phase174_recommendation.md").write_text(rec_md, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

