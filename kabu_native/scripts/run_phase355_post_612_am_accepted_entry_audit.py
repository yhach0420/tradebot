#!/usr/bin/env python3
"""
Phase355-post: Audit accepted ENTRY rows from live_session_080806 (6/12 AM).

Reads existing small_paper_events.csv only — no push replay.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
OUT_DIR = REPO / "kabu_native" / "results" / "reports"
DEFAULT_SESSION = (
    REPO / "kabu_native" / "results" / "small_paper" / "20260612" / "live_session_080806"
)
DEFAULT_UNIVERSE = (
    REPO
    / "kabu_native"
    / "results"
    / "reports"
    / "universe_core10_dynamic40_price_risk_am_refresh1000_20260612.csv"
)


def _bootstrap() -> None:
    src = REPO / "kabu_native" / "src"
    for p in (src, REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def audit_accepted_entries(
    *,
    session_dir: Path,
    universe_csv: Path,
) -> dict[str, Any]:
    _bootstrap()
    from small_paper.pullback_misread_dynamic40_entry_guard import (
        attach_universe_fields,
        is_dynamic40_universe,
        load_symbol_universe_meta,
    )
    from small_paper.pullback_misread_entry_guard_shadow import would_block_pullback_misread_guard

    events_path = session_dir / "small_paper_events.csv"
    if not events_path.is_file():
        raise FileNotFoundError(f"missing events csv: {events_path}")

    universe = load_symbol_universe_meta(universe_csv)
    detail_rows: list[dict[str, Any]] = []
    core10_count = 0
    dynamic40_count = 0
    reject_count = 0
    core10_reject_count = 0
    dynamic40_reject_count = 0
    reject_symbols: set[str] = set()

    with events_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            if row.get("event_type") != "accepted":
                continue

            trade = dict(row)
            sym = str(trade.get("symbol") or "")
            attach_universe_fields(trade, universe.get(sym, {}))

            rise5 = _float(trade.get("entry_rise_5min_pct"))
            vwap_dev = _float(trade.get("entry_vwap_dev_pct"))
            slot = str(trade.get("universe_slot") or "")
            bucket = str(trade.get("universe_bucket") or "")
            is_dyn = is_dynamic40_universe(trade)
            is_core = slot == "core"

            if is_core:
                core10_count += 1
            if is_dyn:
                dynamic40_count += 1

            pullback_cond = would_block_pullback_misread_guard(
                {"entry_rise_5min_pct": rise5, "entry_vwap_dev_pct": vwap_dev}
            )
            would_reject = bool(is_dyn and pullback_cond)

            if would_reject:
                reject_count += 1
                reject_symbols.add(sym)
                if is_core:
                    core10_reject_count += 1
                if is_dyn:
                    dynamic40_reject_count += 1

            detail_rows.append(
                {
                    "symbol": sym,
                    "entry_time": trade.get("entry_time"),
                    "entry_rise_5min_pct": rise5,
                    "entry_vwap_dev_pct": vwap_dev,
                    "universe_slot": slot,
                    "universe_bucket": bucket,
                    "source_bucket": trade.get("source_bucket", ""),
                    "is_dynamic40": is_dyn,
                    "is_core10": is_core,
                    "pullback_condition_met": pullback_cond,
                    "would_be_rejected": would_reject,
                }
            )

    accepted_count = len(detail_rows)
    sym_6976_reject = sum(
        1 for r in detail_rows if r["symbol"] == "6976.T" and r["would_be_rejected"]
    )
    sym_6976_accepted = sum(1 for r in detail_rows if r["symbol"] == "6976.T")

    summary = {
        "phase": "355-post",
        "title": "6/12 AM accepted ENTRY replay audit (events.csv direct)",
        "session_dir": str(session_dir),
        "universe_csv": str(universe_csv),
        "accepted_count": accepted_count,
        "core10_count": core10_count,
        "dynamic40_count": dynamic40_count,
        "reject_count": reject_count,
        "reject_symbols": sorted(reject_symbols),
        "core10_reject_count": core10_reject_count,
        "dynamic40_reject_count": dynamic40_reject_count,
        "6976_accepted_count": sym_6976_accepted,
        "6976_would_reject_count": sym_6976_reject,
        "pass_checks": {
            "6976_in_reject_symbols": "6976.T" in reject_symbols,
            "core10_reject_zero": core10_reject_count == 0,
            "dynamic40_reject_positive": dynamic40_reject_count > 0,
        },
        "conclusion": (
            "Phase355 B guard would reject Dynamic40 pullback misread entries; "
            "Core10 untouched."
            if core10_reject_count == 0 and sym_6976_reject > 0
            else "Review audit rows — expectations not fully met."
        ),
    }
    return {"summary": summary, "detail_rows": detail_rows}


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase355-post 6/12 AM accepted ENTRY audit")
    parser.add_argument("--session-dir", type=Path, default=DEFAULT_SESSION)
    parser.add_argument("--universe-csv", type=Path, default=DEFAULT_UNIVERSE)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    result = audit_accepted_entries(
        session_dir=args.session_dir,
        universe_csv=args.universe_csv,
    )
    summary = result["summary"]
    detail_rows = result["detail_rows"]

    args.out_dir.mkdir(parents=True, exist_ok=True)
    detail_fields = [
        "symbol",
        "entry_time",
        "entry_rise_5min_pct",
        "entry_vwap_dev_pct",
        "universe_slot",
        "universe_bucket",
        "source_bucket",
        "is_dynamic40",
        "is_core10",
        "pullback_condition_met",
        "would_be_rejected",
    ]
    _write_csv(
        args.out_dir / "phase355_post_612_am_accepted_entry_audit.csv",
        detail_rows,
        detail_fields,
    )
    (args.out_dir / "phase355_post_612_am_accepted_entry_audit_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(json.dumps(summary, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
