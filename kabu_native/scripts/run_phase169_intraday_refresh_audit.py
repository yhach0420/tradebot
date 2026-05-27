#!/usr/bin/env python3
"""
Phase169: Audit intraday refresh execution in live sessions.

Reads:
- kabu_native/results/small_paper/20260527/live_session_*/errors.jsonl
- live_session_config.json / small_paper_summary.json
- refresh universe CSVs (reports) if present

Outputs (under kabu_native/results/reports/):
- phase169_intraday_refresh_audit.json
- phase169_refresh_symbol_changes.csv
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


@dataclass(frozen=True)
class SessionSpec:
    label: str
    session_dir: Path
    expected_refresh_hhmm: str


DAY = "20260527"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_jsonl(path: Path) -> Iterable[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return out


def _symbols_from_universe_csv(path: Path) -> list[str]:
    if not path.is_file():
        return []
    syms: list[str] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("symbol") or "").strip().upper()
            if sym:
                syms.append(sym)
    return syms


def _diff(before: list[str], after: list[str]) -> tuple[list[str], list[str]]:
    b = set(before)
    a = set(after)
    added = sorted(a - b)
    removed = sorted(b - a)
    return added, removed


def main() -> int:
    repo_root = Path(__file__).resolve().parents[2]
    reports_dir = repo_root / "kabu_native" / "results" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    base = repo_root / "kabu_native" / "results" / "small_paper" / DAY
    sessions = [
        SessionSpec("am", base / "live_session_082953", "10:00"),
        SessionSpec("pm", base / "live_session_122531", "14:30"),
    ]

    change_rows: list[dict[str, Any]] = []
    audit: dict[str, Any] = {"phase": 169, "day": DAY, "sessions": {}, "verdict": "D"}

    executed_1000 = False
    executed_1430 = False
    any_failed = False

    for spec in sessions:
        cfg = _read_json(spec.session_dir / "live_session_config.json")
        summary = _read_json(spec.session_dir / "small_paper_summary.json")
        errs = list(_iter_jsonl(spec.session_dir / "errors.jsonl"))

        intraday_events = [
            e
            for e in errs
            if e.get("error_type") == "intraday_refresh"
            and str(e.get("refresh_time") or "") == spec.expected_refresh_hhmm
        ]
        intraday_any = [e for e in errs if e.get("error_type") == "intraday_refresh"]
        intraday_api_errors = [
            e
            for e in errs
            if e.get("error_type") == "api_error"
            and "intraday_refresh" in str(e.get("operation") or "")
        ]
        # failures that would stop the run before refresh completes
        stop_reason = str(summary.get("stop_reason") or "")

        # Determine started/completed/failed signals (best-effort)
        refresh_completed = bool(intraday_events)
        refresh_failed = bool(intraday_api_errors) or stop_reason in (
            "open_symbols_exceed_cap",
            "register_count_over_50",
            "register_failed",
        )
        refresh_started = refresh_completed  # no explicit "started" log exists today

        # Symbol diffs: compare base universe vs refresh universe CSV (what would be applied)
        universe_csv = Path(str(cfg.get("universe_csv_path") or ""))
        before_syms = _symbols_from_universe_csv(universe_csv) if universe_csv.is_file() else []
        before_count = len(before_syms) if before_syms else int(cfg.get("symbol_count") or 0)

        refresh_csv_guess = ""
        if spec.label == "am":
            refresh_csv_guess = str(reports_dir / f"universe_core10_dynamic40_price_risk_am_refresh1000_{DAY}.csv")
        else:
            refresh_csv_guess = str(reports_dir / f"universe_core10_dynamic40_price_risk_pm_refresh1430_{DAY}.csv")
        refresh_csv = Path(refresh_csv_guess)
        after_syms = _symbols_from_universe_csv(refresh_csv) if refresh_csv.is_file() else []
        after_count = len(after_syms) if after_syms else None
        added, removed = _diff(before_syms, after_syms) if before_syms and after_syms else ([], [])

        reg_count_logged: Optional[int] = None
        refresh_event_time: Optional[str] = None
        open_symbols_logged: Optional[list[str]] = None
        merge_meta: Optional[dict[str, Any]] = None
        if intraday_events:
            ev0 = intraday_events[0]
            refresh_event_time = str(ev0.get("event_time") or "")
            try:
                reg_count_logged = int(ev0.get("register_count") or 0)
            except (TypeError, ValueError):
                reg_count_logged = None
            open_symbols_logged = list(ev0.get("open_symbols") or [])
            merge_meta = ev0.get("merge")

        audit["sessions"][spec.label] = {
            "session_dir": str(spec.session_dir),
            "expected_refresh_hhmm": spec.expected_refresh_hhmm,
            "refresh_started": refresh_started,
            "refresh_completed": refresh_completed,
            "refresh_failed": refresh_failed,
            "refresh_event_time": refresh_event_time,
            "register_count_before": before_count,
            "register_count_after": reg_count_logged,
            "universe_csv": str(universe_csv) if universe_csv else None,
            "refresh_csv": str(refresh_csv) if refresh_csv.is_file() else None,
            "refresh_csv_symbol_count": after_count,
            "symbol_changes_count": {"added": len(added), "removed": len(removed)},
            "stop_reason": stop_reason,
            "open_symbols_logged": open_symbols_logged,
            "merge_meta": merge_meta,
            "errors_intraday_refresh_total": len(intraday_any),
            "errors_intraday_refresh_matched": len(intraday_events),
            "errors_intraday_api_errors": intraday_api_errors[:3],
        }

        # write per-symbol change rows (even if refresh didn't execute; it's a hypothetical diff)
        for sym in removed:
            change_rows.append(
                {
                    "session": spec.label,
                    "refresh_hhmm": spec.expected_refresh_hhmm,
                    "change": "removed_on_refresh_csv",
                    "symbol": sym,
                }
            )
        for sym in added:
            change_rows.append(
                {
                    "session": spec.label,
                    "refresh_hhmm": spec.expected_refresh_hhmm,
                    "change": "added_on_refresh_csv",
                    "symbol": sym,
                }
            )

        if refresh_failed:
            any_failed = True
        if spec.label == "am" and refresh_completed:
            executed_1000 = True
        if spec.label == "pm" and refresh_completed:
            executed_1430 = True

    # Verdict selection
    if any_failed:
        verdict = "E"
    elif executed_1000 and executed_1430:
        verdict = "A"
    elif executed_1000 and not executed_1430:
        verdict = "B"
    elif executed_1430 and not executed_1000:
        verdict = "C"
    else:
        verdict = "D"
    audit["verdict"] = verdict
    audit["verdict_options"] = {
        "A": "both_refresh_executed",
        "B": "only_1000_executed",
        "C": "only_1430_executed",
        "D": "refresh_not_triggered",
        "E": "refresh_failed",
    }

    out_json = reports_dir / "phase169_intraday_refresh_audit.json"
    out_csv = reports_dir / "phase169_refresh_symbol_changes.csv"

    out_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["session", "refresh_hhmm", "change", "symbol"])
        w.writeheader()
        for row in change_rows:
            w.writerow(row)

    print(json.dumps({"verdict": verdict, "outputs": {"json": str(out_json), "csv": str(out_csv)}}))
    return 0 if verdict in ("A", "B", "C", "D", "E") else 1


if __name__ == "__main__":
    raise SystemExit(main())

