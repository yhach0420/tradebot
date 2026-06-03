#!/usr/bin/env python3
"""
Phase241: max_concurrent reject expectancy review (review only).

Target: gate_reject_reason == "max_concurrent"

Aggregates:
- trade_count
- PF
- total_pnl_pct
- avg_pnl_pct
- win_rate
- stop_rate

Stratified by quality >= {0.75, 0.80, 0.85, 0.90}.

Notes:
- This script uses whatever PnL fields are present in reject events.
- It does NOT change production/YAML and does not introduce new features/scores.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native" / "results" / "reports" / "phase241_max_concurrent_review.json"

TARGET_REASON = "max_concurrent"
QUALITY_THRESHOLDS = (0.75, 0.80, 0.85, 0.90)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    jsonl = session_dir / "small_paper_events.jsonl"
    if jsonl.is_file():
        out: list[dict[str, Any]] = []
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out

    csv_path = session_dir / "small_paper_events.csv"
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    return []


def _discover_sessions(base: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not base.is_dir():
        return out
    for summary_path in sorted(base.rglob("small_paper_summary.json")):
        session_dir = summary_path.parent
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}
        out.append(
            {
                "session_id": session_dir.relative_to(base).as_posix(),
                "session_dir": str(session_dir),
                "mode": summary.get("mode"),
                "source": summary.get("source"),
            }
        )
    return out


def _extract_target_rejects(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ev in events:
        if str(ev.get("event_type") or "") != "rejected":
            continue
        if str(ev.get("gate_reject_reason") or "") != TARGET_REASON:
            continue
        rows.append(ev)
    return rows


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls: list[float] = []
    stops = 0
    for r in rows:
        pnl = _float(r.get("pnl_pct"))
        if pnl is None:
            continue
        pnls.append(float(pnl))
        if str(r.get("exit_reason") or "") == "stop_hit" or str(r.get("gate_reject_reason") or "") == "stop_hit":
            stops += 1
        if str(r.get("stop_hit") or "").lower() in ("true", "1", "yes"):
            stops += 1
    n = len(pnls)
    if n == 0:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "win_rate": None,
            "stop_rate": None,
        }
    wins = sum(1 for p in pnls if p > 0)
    pf = _pf(pnls)
    return {
        "trade_count": n,
        "profit_factor": pf if pf != float("inf") else pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 4),
        "win_rate": round(wins / n, 4),
        "stop_rate": round(stops / n, 4),
    }


def _quality_filter(rows: list[dict[str, Any]], thr: float) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        q = _float(r.get("continuation_quality_score"))
        if q is None:
            continue
        if q >= thr:
            out.append(r)
    return out


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sessions = _discover_sessions(SMALL_PAPER)

    all_rejects: list[dict[str, Any]] = []
    per_session_counts: list[dict[str, Any]] = []
    for sess in sessions:
        sdir = Path(sess["session_dir"])
        events = _load_events(sdir)
        rows = _extract_target_rejects(events)
        if rows:
            all_rejects.extend(rows)
        per_session_counts.append(
            {
                "session_id": sess["session_id"],
                "count": len(rows),
                "mode": sess.get("mode"),
                "source": sess.get("source"),
            }
        )

    report = {
        "phase": 241,
        "mode": "max_concurrent_reject_expectancy_review",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
        },
        "target": {
            "event_type": "rejected",
            "gate_reject_reason": TARGET_REASON,
        },
        "population": {
            "sessions_scanned": len(sessions),
            "matching_reject_events": len(all_rejects),
        },
        "metrics": {
            "all": _metrics(all_rejects),
            "quality_stratified": {
                f"quality_ge_{thr}": _metrics(_quality_filter(all_rejects, thr))
                for thr in QUALITY_THRESHOLDS
            },
        },
        "by_session_counts": per_session_counts,
        "notes": [
            "Uses pnl_pct present in rejected events (if any).",
            "If no max_concurrent rejects exist in the scanned sessions, metrics will be empty.",
        ],
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} rejects={len(all_rejects)} sessions={len(sessions)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

