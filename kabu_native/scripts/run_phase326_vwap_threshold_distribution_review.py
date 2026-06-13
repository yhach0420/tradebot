#!/usr/bin/env python3
"""
Phase326-lite: VWAP deviation percentile distribution for stop_hit vs good_exit.

Output: phase326_vwap_threshold_distribution_review.json
"""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase326_vwap_threshold_distribution_review.json"
DAY = "20260608"
JST = ZoneInfo("Asia/Tokyo")

SESSIONS = {
    "am": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_080642",
    "pm": REPO / f"kabu_native/results/small_paper/{DAY}/live_session_122548",
}

GOOD_EXIT_REASONS = frozenset(
    {
        "trailing_mfe_exit",
        "overlap_replaced_review",
        "morning_session_close",
        "afternoon_session_close",
    }
)
PERCENTILES = (10, 25, 50, 75, 90)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _percentile(values: list[float], p: int) -> Optional[float]:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return round(xs[0], 4)
    rank = (p / 100.0) * (len(xs) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(xs) - 1)
    frac = rank - lo
    return round(xs[lo] + frac * (xs[hi] - xs[lo]), 4)


def _percentile_block(values: list[float]) -> dict[str, Any]:
    clean = [v for v in values if v is not None]
    if not clean:
        return {"n": 0, "percentiles": {f"p{p}": None for p in PERCENTILES}}
    return {
        "n": len(clean),
        "min": round(min(clean), 4),
        "max": round(max(clean), 4),
        "mean": round(sum(clean) / len(clean), 4),
        "percentiles": {f"p{p}": _percentile(clean, p) for p in PERCENTILES},
    }


def _vwap_ref(entry_price: float, entry_vwap_dev_pct: float) -> float:
    return entry_price / (1.0 + entry_vwap_dev_pct / 100.0)


def _current_vwap_dev_pct(exit_price: float, vwap_ref: float) -> float:
    if vwap_ref <= 0:
        return 0.0
    return round((exit_price - vwap_ref) / vwap_ref * 100.0, 4)


def _load_cohorts() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    stop_hit: list[dict[str, Any]] = []
    good_exit: list[dict[str, Any]] = []

    for session_label, session_dir in SESSIONS.items():
        path = session_dir / "small_paper_events.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                if row.get("event_type") != "observer_exit":
                    continue
                reason = str(row.get("exit_reason") or "")
                if reason != "stop_hit" and reason not in GOOD_EXIT_REASONS:
                    continue

                entry = _float(row.get("entry_price")) or 0.0
                exit_p = _float(row.get("exit_price")) or _float(row.get("current_price")) or 0.0
                entry_dev = _float(row.get("entry_vwap_dev_pct"))
                if entry_dev is None:
                    continue

                vwap = _vwap_ref(entry, entry_dev)
                current_dev = _current_vwap_dev_pct(exit_p, vwap)

                rec = {
                    "session": session_label,
                    "symbol": str(row.get("symbol") or ""),
                    "entry_time": str(row.get("entry_time") or ""),
                    "exit_reason": reason,
                    "entry_price": entry,
                    "exit_price": exit_p,
                    "entry_vwap_dev_pct": round(entry_dev, 4),
                    "current_vwap_dev_pct": current_dev,
                }
                if reason == "stop_hit":
                    stop_hit.append(rec)
                else:
                    good_exit.append(rec)

    return stop_hit, good_exit


def _cohort_summary(name: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    entry_vals = [r["entry_vwap_dev_pct"] for r in rows]
    current_vals = [r["current_vwap_dev_pct"] for r in rows]
    return {
        "cohort": name,
        "trade_count": len(rows),
        "entry_vwap_dev_pct": _percentile_block(entry_vals),
        "current_vwap_dev_pct": _percentile_block(current_vals),
    }


def main() -> int:
    stop_hit, good_exit = _load_cohorts()

    report = {
        "phase": 326,
        "title": "vwap_threshold_distribution_review",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "constraint": "analysis only; no logic changes",
        "target_date": DAY,
        "source": "20260608 observer_exit (Phase321 stop_hit + good_exit cohorts)",
        "methodology": {
            "entry_vwap_dev_pct": "from observer_exit row at entry",
            "current_vwap_dev_pct": (
                "(exit_price - vwap_ref) / vwap_ref * 100; "
                "vwap_ref = entry_price / (1 + entry_vwap_dev_pct/100)"
            ),
            "percentile_method": "linear interpolation on sorted values",
            "percentiles": list(PERCENTILES),
        },
        "cohorts": {
            "stop_hit": _cohort_summary("stop_hit", stop_hit),
            "good_exit": _cohort_summary("good_exit", good_exit),
        },
        "phase321_alignment": {
            "stop_hit_count": len(stop_hit),
            "expected_stop_hit_count": 41,
            "good_exit_count": len(good_exit),
            "expected_good_exit_count": 128,
        },
        "threshold_hints_for_phase325": {
            "contraction_entry_gt_pct": 0.5,
            "contraction_current_lte_pct": 0.2,
            "stop_hit_entry_p50": _percentile([r["entry_vwap_dev_pct"] for r in stop_hit], 50),
            "good_exit_entry_p50": _percentile([r["entry_vwap_dev_pct"] for r in good_exit], 50),
            "stop_hit_current_p50": _percentile([r["current_vwap_dev_pct"] for r in stop_hit], 50),
            "good_exit_current_p50": _percentile([r["current_vwap_dev_pct"] for r in good_exit], 50),
        },
    }

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(f"stop_hit={len(stop_hit)} good_exit={len(good_exit)}")
    for cohort_name, rows in (("stop_hit", stop_hit), ("good_exit", good_exit)):
        e = report["cohorts"][cohort_name]["entry_vwap_dev_pct"]["percentiles"]
        c = report["cohorts"][cohort_name]["current_vwap_dev_pct"]["percentiles"]
        print(f"  {cohort_name} entry p50={e['p50']} current p50={c['p50']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
