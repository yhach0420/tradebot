#!/usr/bin/env python3
"""Phase 127: fade_watch shadow policy design + A/B replay test report."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]
NATIVE = ROOT / "kabu_native"
REPORTS = NATIVE / "results" / "reports"
SMALL_PAPER = NATIVE / "results" / "small_paper"
SHADOW_CONFIG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_fade_watch_shadow.yaml"


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(ROOT))
    except ValueError:
        return str(p)


def _metrics(trades: list[Any]) -> dict[str, Any]:
    from research.structural_observer_review import _summarize_structural_trades

    m = _summarize_structural_trades(trades)
    return {
        "pf": m.get("structural_pf"),
        "avg_pnl": m.get("structural_avg_pnl"),
        "total_pnl": round(sum(t.realized_pnl_pct for t in trades), 4) if trades else 0.0,
        "win_rate": m.get("structural_win_rate"),
        "trade_count": m.get("structural_trade_count"),
        "session_close_count": m.get("session_end_exit_count"),
    }


def _fade_watch_stats(trades: list[Any], baseline: list[Any]) -> dict[str, Any]:
    fw = [t for t in trades if getattr(t, "fade_watch_entered", False)]
    base_by_key = {(t.symbol, t.entry_time): t for t in baseline}
    improved = worsened = 0
    for t in fw:
        b = base_by_key.get((t.symbol, t.entry_time))
        if not b:
            continue
        if t.realized_pnl_pct > b.realized_pnl_pct:
            improved += 1
        elif t.realized_pnl_pct < b.realized_pnl_pct:
            worsened += 1
    return {
        "fade_watch_count": len(fw),
        "fade_watch_improved_count": improved,
        "fade_watch_worsened_count": worsened,
    }


def _verdict(runner_ok: bool, review_ok: bool, comparison: dict[str, Any]) -> tuple[str, list[str]]:
    notes: list[str] = []
    if not runner_ok:
        return "runner_support_missing", notes + ["shadow replay failed"]
    if not review_ok:
        return "review_support_missing", notes + ["review exit reasons not recognized"]
    delta = float(comparison.get("delta_total_pnl") or 0)
    worsened = int(comparison.get("fade_watch_worsened_count") or 0)
    fw_n = int(comparison.get("fade_watch_count") or 0)
    notes.append(f"delta_total_pnl={delta:.4f} fade_watch={fw_n} worsened={worsened}")
    if fw_n == 0:
        return "fade_watch_runtime_risk", notes + ["no fade_watch entries in replay"]
    if delta >= 0 and worsened <= max(1, fw_n // 2):
        return "fade_watch_shadow_ready", notes
    if delta < 0:
        return "fade_watch_runtime_risk", notes + ["shadow underperforms baseline total_pnl"]
    return "fade_watch_shadow_ready", notes + ["marginal gain; monitor shadow sessions"]


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 127 fade_watch shadow")
    parser.add_argument("--max-sessions", type=int, default=4)
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.fade_watch_shadow import (
        FADE_WATCH_EXIT_REASONS,
        FADE_WATCH_TRIGGER_REASONS,
        POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW,
        is_fade_watch_review_reason,
    )
    from research.mfe_mae_exit_review import discover_sessions
    from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1
    from research.structural_observer_review import (
        _load_events,
        _session_end_time,
        replay_combined_structural_exit,
    )
    from small_paper.config import load_pilot_config

    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")
    config = load_pilot_config(SHADOW_CONFIG)
    sessions = discover_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    design = {
        "phase": 127,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "shadow_policy": POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW,
        "baseline_policy": POLICY_COMBINED_STRUCTURAL_EXIT_V1,
        "shadow_config": _rel(SHADOW_CONFIG),
        "production_pilot_unchanged": True,
        "trigger_reasons": sorted(FADE_WATCH_TRIGGER_REASONS),
        "fade_watch_exit_reasons": sorted(FADE_WATCH_EXIT_REASONS),
        "review_recognized_reasons": sorted(r for r in FADE_WATCH_EXIT_REASONS if is_fade_watch_review_reason(r)),
        "no_fixed_time_exit": True,
        "state_machine": {
            "enter_on": list(FADE_WATCH_TRIGGER_REASONS),
            "continue_on": [
                "reacceleration_detected",
                "new_high_after_fade",
                "new_mfe_created",
                "momentum_recovery",
            ],
            "exit_on": [
                "giveback_exceeded",
                "breakdown_detected",
                "no_new_high_and_momentum_down",
                "fade_watch_session_close",
            ],
        },
        "logging_fields": [
            "fade_watch_entered",
            "fade_watch_entry_time",
            "fade_watch_initial_reason",
            "fade_watch_exit_reason",
            "reacceleration_detected",
            "new_high_after_fade",
            "new_mfe_created",
            "momentum_recovery",
            "giveback_exceeded",
            "breakdown_detected",
            "fade_watch_hold_sec",
        ],
    }

    runner_ok = True
    review_ok = all(is_fade_watch_review_reason(r) for r in FADE_WATCH_EXIT_REASONS)
    per_session: list[dict[str, Any]] = []
    all_a: list[Any] = []
    all_b: list[Any] = []

    for sdir in sessions:
        events = _load_events(sdir)
        if not events:
            continue
        interval = float(config.poll_interval_sec or 5.0)
        session_end = _session_end_time(events)
        try:
            trades_a, _ = replay_combined_structural_exit(
                events,
                pilot_config=config,
                poll_interval_sec=interval,
                session_end=session_end,
                structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1,
            )
            trades_b, log_b = replay_combined_structural_exit(
                events,
                pilot_config=config,
                poll_interval_sec=interval,
                session_end=session_end,
                structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1_FADE_WATCH_SHADOW,
            )
        except Exception as exc:
            runner_ok = False
            per_session.append({"session_dir": str(sdir), "error": str(exc)})
            continue

        all_a.extend(trades_a)
        all_b.extend(trades_b)
        ma = _metrics(trades_a)
        mb = _metrics(trades_b)
        fw = _fade_watch_stats(trades_b, trades_a)
        exit_reasons_b = sorted({t.close_reason for t in trades_b})
        per_session.append(
            {
                "session_id": str(sdir.relative_to(SMALL_PAPER)) if sdir.parent.parent else sdir.name,
                "A_current": ma,
                "B_fade_watch_shadow": mb,
                **fw,
                "shadow_exit_reasons": exit_reasons_b,
                "fade_watch_events": sum(1 for e in log_b if str(e.get("event_kind", "")).startswith("fade_watch")),
            }
        )

    ma = _metrics(all_a)
    mb = _metrics(all_b)
    fw = _fade_watch_stats(all_b, all_a)
    comparison = {
        **ma,
        "B_pf": mb.get("pf"),
        "B_avg_pnl": mb.get("avg_pnl"),
        "B_total_pnl": mb.get("total_pnl"),
        "B_win_rate": mb.get("win_rate"),
        "B_trade_count": mb.get("trade_count"),
        "delta_total_pnl": round(float(mb.get("total_pnl") or 0) - float(ma.get("total_pnl") or 0), 4),
        **fw,
        "B_session_close_count": mb.get("session_close_count"),
    }

    verdict, notes = _verdict(runner_ok, review_ok, comparison)

    design_path = REPORTS / "phase127_fade_watch_shadow_design.json"
    test_path = REPORTS / "phase127_fade_watch_shadow_test_report.json"

    design["verdict_options"] = {
        "A": "fade_watch_shadow_ready",
        "B": "review_support_missing",
        "C": "runner_support_missing",
        "D": "fade_watch_runtime_risk",
    }
    design_path.write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")

    report = {
        "phase": 127,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": verdict,
        "verdict_notes": notes,
        "runner_ok": runner_ok,
        "review_ok": review_ok,
        "comparison": comparison,
        "sessions": per_session,
        "outputs": {
            "design_json": _rel(design_path),
            "test_report_json": _rel(test_path),
        },
    }
    test_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({"verdict": verdict, "comparison": comparison}, ensure_ascii=True))
    return 0 if runner_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
