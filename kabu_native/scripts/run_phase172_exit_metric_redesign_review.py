#!/usr/bin/env python3
"""
Phase172 runner: exit metric redesign review (replay only).
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native = script.parents[1]
    repo = script.parents[2]
    for p in (native / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    return repo, native


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("note\nno_rows\n", encoding="utf-8")
        return
    fields: list[str] = []
    # stable field ordering: union of keys
    keys = set()
    for r in rows:
        keys |= set(r.keys())
    fields = [k for k in sorted(keys)]
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def main() -> int:
    repo, native = _bootstrap()
    reports = native / "results" / "reports"
    reports.mkdir(parents=True, exist_ok=True)

    session_dir = (
        repo
        / "kabu_native/results/small_paper/20260521/live_full_session_081418"
    )

    from research.phase172_exit_metric_redesign_review import evaluate_exit_policies

    out = evaluate_exit_policies(session_dir=session_dir)
    json_path = reports / "phase172_exit_metric_redesign_review.json"
    json_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

    _write_csv(reports / "phase172_exit_policy_scenarios.csv", out.get("scenario_metrics") or [])
    _write_csv(reports / "phase172_mfe_capture_analysis.csv", out.get("mfe_capture_analysis") or [])
    _write_csv(
        reports / "phase172_after_exit_reacceleration.csv",
        out.get("after_exit_reacceleration") or [],
    )
    _write_csv(
        reports / "phase172_exit_reason_failure_breakdown.csv",
        out.get("exit_reason_failure_breakdown") or [],
    )
    _write_csv(
        reports / "phase172_simple_exit_trade_details.csv",
        out.get("simple_exit_trade_details") or [],
    )

    # human-readable recommendation
    rec = {
        "phase": 172,
        "verdict": out.get("verdict"),
        "mode": out.get("mode"),
        "coverage_rate": out.get("price_path_coverage_rate"),
        "median_points_per_symbol": out.get("price_path_median_points_per_symbol"),
        "note": "Fixed scenario comparison only (no parameter search).",
    }
    md = (
        "## Phase172 recommendation\n\n"
        f"- **mode**: `{rec['mode']}`\n"
        f"- **verdict**: `{rec['verdict']}`\n"
        f"- price path coverage: `{rec['coverage_rate']}`\n"
        f"- median points/symbol: `{rec['median_points_per_symbol']}`\n\n"
        "Next step: repeat this review on multiple days/sessions to avoid single-day optimization.\n"
    )
    (reports / "phase172_recommendation.md").write_text(md, encoding="utf-8")

    print(json.dumps({"verdict": out.get("verdict"), "mode": out.get("mode"), "outputs": {"json": str(json_path)}}))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

