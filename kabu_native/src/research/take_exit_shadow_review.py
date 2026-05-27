"""
Phase 151: Replay A/B for combined_structural_exit_v1_take_exit_shadow.
"""

from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.small_paper_performance_review import _load_events, _load_json
from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
from research.structural_observer_review import (
    _exit_reason_rows,
    _legacy_virtual_hold_summary,
    _session_end_time,
    _summarize_structural_trades,
    _trade_to_row,
    replay_combined_structural_exit,
)
from research.take_exit_shadow import POLICY_COMBINED_STRUCTURAL_EXIT_V1_TAKE_EXIT_SHADOW


def _scenario_metrics(trades: Sequence[Any], label: str, scenario_id: str) -> dict[str, Any]:
    m = _summarize_structural_trades(trades)
    dist = m.get("exit_reason_distribution") or {}
    return {
        "scenario_id": scenario_id,
        "scenario": label,
        "trade_count": m.get("structural_trade_count"),
        "structural_pf": m.get("structural_pf"),
        "avg_pnl_pct": m.get("structural_avg_pnl"),
        "win_rate": m.get("structural_win_rate"),
        "max_loss_pct": m.get("structural_max_loss"),
        "max_gain_pct": m.get("structural_max_gain"),
        "take_exit_count": int(dist.get("take_exit", 0)),
        "stop_hit_count": int(dist.get("stop_hit", 0)),
        "momentum_fade_exit_count": int(dist.get("momentum_fade_exit", 0)),
        "quality_decay_exit_count": int(dist.get("quality_decay_exit", 0)),
        "session_end_count": int(dist.get("session_end", 0))
        + int(dist.get("morning_session_close", 0))
        + int(dist.get("afternoon_session_close", 0)),
        "overlap_replaced_count": int(dist.get("overlap_replaced_review", 0)),
        "take_before_exit_rate": m.get("take_before_exit_rate"),
        "take_to_exit_pnl_delta": m.get("take_to_exit_pnl_delta"),
        "exit_reason_distribution": dict(dist),
    }


def determine_phase151_verdict(
    combined: Mapping[str, Any],
    shadow: Mapping[str, Any],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    c_pf = float(combined.get("structural_pf") or 0)
    s_pf = float(shadow.get("structural_pf") or 0)
    c_avg = float(combined.get("avg_pnl_pct") or 0)
    s_avg = float(shadow.get("avg_pnl_pct") or 0)
    c_max_loss = float(combined.get("max_loss_pct") or -999)
    s_max_loss = float(shadow.get("max_loss_pct") or -999)

    notes.append(f"combined PF={c_pf:.4f} shadow PF={s_pf:.4f}")
    notes.append(f"combined avg={c_avg:.4f} shadow avg={s_avg:.4f}")
    notes.append(f"take_exit_count={shadow.get('take_exit_count')}")

    if s_pf >= 1.0 and s_avg > c_avg and s_max_loss >= c_max_loss:
        return "take_exit_shadow_promising", notes
    if s_pf > c_pf + 0.1 and s_avg > c_avg:
        return "take_exit_improves_but_not_enough", notes
    if s_pf <= c_pf:
        return "take_exit_not_helpful", notes + ["Shadow did not beat combined on PF."]
    return "take_exit_improves_but_not_enough", notes


def run_phase151_take_exit_shadow_review(
    session_dir: Path,
    *,
    pilot_config: Any,
    reports_dir: Path,
) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    events = _load_events(session_dir)
    summary = _load_json(session_dir / "small_paper_summary.json") or {}
    interval = float(summary.get("poll_interval_sec") or 5.0)
    session_end = _session_end_time(events)

    combined_trades, _ = replay_combined_structural_exit(
        events,
        pilot_config=pilot_config,
        poll_interval_sec=interval,
        session_end=session_end,
        structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    )
    shadow_trades, _ = replay_combined_structural_exit(
        events,
        pilot_config=pilot_config,
        poll_interval_sec=interval,
        session_end=session_end,
        structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1_TAKE_EXIT_SHADOW,
    )

    legacy = _legacy_virtual_hold_summary(events)
    combined_m = _scenario_metrics(combined_trades, "combined_structural_exit_v1", "A")
    shadow_m = _scenario_metrics(
        shadow_trades, "combined_structural_exit_v1_take_exit_shadow", "B"
    )
    legacy_m = {
        "scenario_id": "C",
        "scenario": "legacy_virtual_hold_reference",
        "structural_pf": legacy.get("legacy_virtual_hold_pf"),
        "avg_pnl_pct": legacy.get("legacy_virtual_hold_avg_pnl_pct"),
        "trade_count": legacy.get("legacy_virtual_hold_trade_count"),
        "note": "reference_only_not_deployable",
    }

    scenarios = [combined_m, shadow_m, legacy_m]
    verdict, notes = determine_phase151_verdict(combined_m, shadow_m)

    shadow_rows = [_trade_to_row(t) for t in shadow_trades]
    exit_reason_rows = _exit_reason_rows(_summarize_structural_trades(shadow_trades), shadow_trades)

    report: dict[str, Any] = {
        "phase": 151,
        "mode": "take_exit_shadow_replay_review",
        "what_if_only": True,
        "shadow_only": True,
        "session_dir": str(session_dir),
        "session_date": "20260525",
        "policy": POLICY_COMBINED_STRUCTURAL_EXIT_V1_TAKE_EXIT_SHADOW,
        "production_policy_unchanged": POLICY_COMBINED_STRUCTURAL_EXIT_V1,
        "verdict": verdict,
        "verdict_options": {
            "A": "take_exit_shadow_promising",
            "B": "take_exit_improves_but_not_enough",
            "C": "take_exit_not_helpful",
            "D": "runner_support_missing",
        },
        "verdict_notes": notes,
        "scenarios": scenarios,
        "delta_pf": round(float(shadow_m.get("structural_pf") or 0) - float(combined_m.get("structural_pf") or 0), 4),
        "delta_avg_pnl": round(float(shadow_m.get("avg_pnl_pct") or 0) - float(combined_m.get("avg_pnl_pct") or 0), 4),
        "legacy_virtual_hold": legacy,
        "runner_support": {
            "replay_combined_ok": len(combined_trades) > 0,
            "replay_shadow_ok": len(shadow_trades) > 0,
            "live_observer_take_exit": True,
            "shadow_config": "kabu_native/configs/small_paper_pilot_q070_cap3_take_exit_shadow.yaml",
        },
    }

    reports_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(reports_dir / "phase151_take_exit_shadow_trades.csv", shadow_rows)
    _write_csv(reports_dir / "phase151_take_exit_shadow_exit_reasons.csv", exit_reason_rows)
    _write_csv(reports_dir / "phase151_exit_whatif_scenarios.csv", scenarios)
    _write_csv(
        reports_dir / "phase151_take_exit_shadow_session_summary.csv",
        [
            {
                "metric": k,
                "combined_v1": combined_m.get(k),
                "take_exit_shadow": shadow_m.get(k),
                "legacy_vh": legacy_m.get(k),
            }
            for k in (
                "structural_pf",
                "avg_pnl_pct",
                "win_rate",
                "max_loss_pct",
                "max_gain_pct",
                "take_exit_count",
                "momentum_fade_exit_count",
                "quality_decay_exit_count",
                "stop_hit_count",
                "take_before_exit_rate",
                "take_to_exit_pnl_delta",
            )
        ],
    )
    (reports_dir / "phase151_take_exit_shadow_review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    report["output_files"] = {
        "json": str(reports_dir / "phase151_take_exit_shadow_review.json"),
        "trades_csv": str(reports_dir / "phase151_take_exit_shadow_trades.csv"),
        "exit_reasons_csv": str(reports_dir / "phase151_take_exit_shadow_exit_reasons.csv"),
        "scenarios_csv": str(reports_dir / "phase151_exit_whatif_scenarios.csv"),
        "session_summary_csv": str(reports_dir / "phase151_take_exit_shadow_session_summary.csv"),
    }
    return report


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})
