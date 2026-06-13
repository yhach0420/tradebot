#!/usr/bin/env python3
"""
Phase355: Validate pullback Dynamic40 production guard via push-replay.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "kabu_native" / "results" / "reports"


def _bootstrap() -> None:
    native = REPO / "kabu_native"
    for p in (native / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _find_push_dir(day: str) -> Path | None:
    day_dash = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    candidates = [
        REPO / "kabu_native" / "data" / "push_jsonl" / day_dash,
        REPO / "kabu_native" / "data" / "push_jsonl" / day,
        REPO / "kabu_native" / "results" / "small_paper" / day,
    ]
    for base in candidates:
        if not base.is_dir():
            continue
        for sub in sorted(base.rglob("push_jsonl")):
            if sub.is_dir() and any(sub.glob("*.jsonl")):
                return sub
        if any(base.glob("*.jsonl")):
            return base
    return None


def _run_replay(day: str, session_label: str) -> dict[str, Any]:
    _bootstrap()
    from small_paper.config import load_pilot_config
    from small_paper.pilot_runner import run_push_replay_dry_run

    push_dir = _find_push_dir(day)
    if push_dir is None:
        return {"day": day, "session": session_label, "ok": False, "error": "push_dir_not_found"}

    cfg_path = (
        REPO
        / "kabu_native"
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    config = load_pilot_config(cfg_path)
    out = (
        REPO
        / "kabu_native"
        / "results"
        / "small_paper"
        / day
        / f"phase355_replay_{session_label}"
    )
    result = run_push_replay_dry_run(
        config,
        push_dir=push_dir,
        output_dir=out,
        repo_root=REPO,
        poll_interval_sec=0.0,
        streaming_push_replay=True,
    )
    summary = dict(result.summary)
    rejects = [
        r
        for r in result.events
        if r.get("event_type") == "rejected"
        and r.get("gate_reject_reason") == "pullback_misread_dynamic40_guard"
    ]
    core_rejects = [
        r
        for r in rejects
        if str(r.get("universe_slot") or "") == "core"
    ]
    dyn_rejects = [
        r
        for r in rejects
        if str(r.get("universe_slot") or "") == "dynamic"
    ]
    sym_6976 = [r for r in rejects if str(r.get("symbol") or "") == "6976.T"]
    return {
        "day": day,
        "session": session_label,
        "ok": True,
        "push_dir": str(push_dir),
        "output_dir": str(out),
        "pullback_misread_dynamic40_guard_enabled": summary.get(
            "pullback_misread_dynamic40_guard_enabled"
        ),
        "pullback_misread_dynamic40_reject_count": summary.get(
            "pullback_misread_dynamic40_reject_count"
        ),
        "pullback_misread_dynamic40_reject_symbols": summary.get(
            "pullback_misread_dynamic40_reject_symbols"
        ),
        "pullback_misread_guard_shadow_delta_yen": summary.get(
            "pullback_misread_guard_shadow_delta_yen"
        ),
        "observer_total_pnl_yen_100": summary.get("observer_total_pnl_yen_100"),
        "reject_count_pullback_guard": len(rejects),
        "dynamic40_reject_count": len(dyn_rejects),
        "core10_reject_count": len(core_rejects),
        "6976_reject_count": len(sym_6976),
        "core10_reject_violation": len(core_rejects) > 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase355 pullback guard rollout validation")
    parser.add_argument("--day", default="20260612", help="YYYYMMDD for 6/12 AM check")
    parser.add_argument(
        "--recent-days",
        default="20260603,20260604,20260605",
        help="Comma-separated YYYYMMDD for recent-3-day check",
    )
    args = parser.parse_args()

    results: list[dict[str, Any]] = []
    am = _run_replay(args.day, "am")
    results.append(am)
    for day in [d.strip() for d in args.recent_days.split(",") if d.strip()]:
        results.append(_run_replay(day, "recent"))

    summary = {
        "phase": 355,
        "title": "Pullback Misread Dynamic40 Guard Production Rollout Validation",
        "am_20260612": am,
        "recent_replays": [r for r in results if r.get("session") == "recent"],
        "pass_checks": {
            "am_core10_not_rejected": not am.get("core10_reject_violation", True),
            "am_guard_enabled": bool(am.get("pullback_misread_dynamic40_guard_enabled")),
            "am_has_dynamic_rejects": int(am.get("dynamic40_reject_count") or 0) > 0,
        },
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / "phase355_pullback_guard_rollout_validation.json"
    out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
