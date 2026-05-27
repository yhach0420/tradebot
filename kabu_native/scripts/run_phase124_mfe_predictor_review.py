#!/usr/bin/env python3
"""Phase 124: MFE>0.15 predictor at fade time (review only)."""

from __future__ import annotations

import argparse
import csv
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


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for row in rows:
        for k in row:
            if k not in cols:
                cols.append(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            flat = dict(row)
            if isinstance(flat.get("rule"), dict):
                flat["rule_json"] = json.dumps(flat.pop("rule"), ensure_ascii=False)
            w.writerow(flat)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 124 MFE predictor review")
    parser.add_argument("--session-dir", action="append", default=None)
    parser.add_argument("--max-sessions", type=int, default=4)
    parser.add_argument("--day-stamp", default=None)
    args = parser.parse_args()

    _bootstrap()
    from research.mfe_mae_exit_review import discover_sessions
    from research.mfe_predictor_review import analyze_mfe_predictor

    if args.session_dir:
        session_dirs = [Path(p) for p in args.session_dir]
    else:
        session_dirs = discover_sessions(SMALL_PAPER, max_sessions=args.max_sessions)

    if not session_dirs:
        print(json.dumps({"error": "no sessions"}, ensure_ascii=True))
        return 1

    result = analyze_mfe_predictor(session_dirs)
    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")

    out_json = REPORTS / "phase124_mfe_predictor_review.json"
    cmp_csv = REPORTS / "phase124_positive_negative_comparison.csv"
    rules_csv = REPORTS / "phase124_rule_search_results.csv"
    top_csv = REPORTS / "phase124_top_predictive_rules.csv"

    report: dict[str, Any] = {
        "phase": 124,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "day_stamp": day_stamp,
        "verdict": result["verdict"],
        "verdict_notes": result["verdict_notes"],
        "verdict_options": {
            "A": "predictable_extension_candidate",
            "B": "weak_predictive_signal",
            "C": "need_additional_features",
            "D": "not_predictable",
        },
        "fade_trade_count": result["fade_trade_count"],
        "positive_count": result["positive_count"],
        "negative_count": result["negative_count"],
        "label_threshold_mfe_pct": result["label_threshold_mfe_pct"],
        "available_features": result["available_features"],
        "skipped_features": result["skipped_features"],
        "positive_negative_comparison": result["positive_negative_comparison"][:25],
        "top_predictive_rules": result["top_predictive_rules"],
        "methodology": {
            "label": f"mfe_pct > {result['label_threshold_mfe_pct']} (structural trade MFE at fade exit)",
            "rule_features_exclude": ["mfe_pct", "mfe_so_far"],
            "extension_value_metric": "selected_total_delta = sum hold60_delta for rule-selected trades",
            "no_implementation": True,
        },
        "outputs": {
            "json": _rel(out_json),
            "comparison_csv": _rel(cmp_csv),
            "rules_csv": _rel(rules_csv),
            "top_rules_csv": _rel(top_csv),
        },
    }

    out_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(cmp_csv, result["positive_negative_comparison"])
    _write_csv(rules_csv, result["rule_search_results"])
    _write_csv(top_csv, result["top_predictive_rules"])

    best = result["top_predictive_rules"][0] if result["top_predictive_rules"] else {}
    print(
        json.dumps(
            {
                "verdict": result["verdict"],
                "positive": result["positive_count"],
                "best_rule": best.get("description"),
                "precision": best.get("precision"),
                "recall": best.get("recall"),
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
