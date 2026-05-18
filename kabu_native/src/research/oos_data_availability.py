"""
Phase 41: OOS data availability inventory and window resolution (no new EXIT logic).

Fixes invalid date ranges (e.g. may_late end < start), surfaces no_data vs valid_window.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.phase37_validation import _latest_trading_date, _trading_days_between

MAY_LATE_START = "2026-05-16"
MARCH_PREFIX = "2026-03"
APRIL_START = "2026-04-01"
APRIL_END = "2026-04-30"
DEFAULT_LATEST_DAYS = 10


def collect_trading_days(data_roots: Sequence[Path]) -> list[str]:
    """Union of YYYY-MM-DD dirs across all roots."""
    found: set[str] = set()
    for root in data_roots:
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if not child.is_dir() or len(child.name) != 10:
                continue
            try:
                date.fromisoformat(child.name)
            except ValueError:
                continue
            found.add(child.name)
    return sorted(found)


def _count_symbol_files(day_dir: Path) -> int:
    if not day_dir.is_dir():
        return 0
    return sum(1 for p in day_dir.iterdir() if p.suffix.lower() == ".csv")


def _scan_root(root: Path) -> dict[str, Any]:
    if not root.is_dir():
        return {
            "path": str(root),
            "exists": False,
            "trading_day_count": 0,
            "first_day": None,
            "last_day": None,
            "sample_symbol_files_on_last_day": 0,
        }
    days = collect_trading_days([root])
    last_sample = 0
    if days:
        last_sample = _count_symbol_files(root / days[-1])
    return {
        "path": str(root),
        "exists": True,
        "trading_day_count": len(days),
        "first_day": days[0] if days else None,
        "last_day": days[-1] if days else None,
        "trading_days": days,
        "sample_symbol_files_on_last_day": last_sample,
    }


def _scan_push_jsonl(path: Path) -> dict[str, Any]:
    if not path.is_dir():
        return {"path": str(path), "exists": False, "file_count": 0, "total_bytes": 0}
    files = [p for p in path.iterdir() if p.is_file()]
    total = sum(p.stat().st_size for p in files)
    return {
        "path": str(path),
        "exists": True,
        "file_count": len(files),
        "total_bytes": total,
        "sample_files": [p.name for p in sorted(files)[:10]],
    }


def build_data_availability_for_oos(
    *,
    data_roots: Sequence[Path],
    push_jsonl_paths: Sequence[Path],
) -> dict[str, Any]:
    merged_days = collect_trading_days(data_roots)
    roots_detail = [_scan_root(r) for r in data_roots]
    push_detail = [_scan_push_jsonl(p) for p in push_jsonl_paths]
    march = [d for d in merged_days if d.startswith(MARCH_PREFIX)]
    april = [d for d in merged_days if APRIL_START <= d <= APRIL_END]
    may_late = [d for d in merged_days if d >= MAY_LATE_START]
    latest = merged_days[-1] if merged_days else None

    return {
        "phase": 41,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "latest_trading_date": latest,
        "merged_trading_day_count": len(merged_days),
        "merged_first_day": merged_days[0] if merged_days else None,
        "merged_last_day": latest,
        "merged_trading_days": merged_days,
        "intraday_1m_roots": roots_detail,
        "push_jsonl": push_detail,
        "coverage_notes": {
            "march_available": bool(march),
            "march_day_count": len(march),
            "april_available_day_count": len(april),
            "may_late_available_day_count": len(may_late),
            "may_late_blocked_reason": (
                None
                if may_late
                else (
                    f"no_trading_days_on_or_after_{MAY_LATE_START}; "
                    f"latest_available={latest}"
                )
            ),
        },
    }


def _window_no_data(
    window_id: str,
    *,
    reason: str,
    start: Optional[str] = None,
    end: Optional[str] = None,
    latest_available: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "id": window_id,
        "window_id": window_id,
        "status": "no_data",
        "reason": reason,
        "start": start,
        "end": end,
        "latest_available": latest_available,
        "run_dir": None,
    }


def _window_valid(
    window_id: str,
    *,
    start: str,
    end: str,
    trading_days: list[str],
    run_dir: Optional[str] = None,
) -> dict[str, Any]:
    return {
        "id": window_id,
        "window_id": window_id,
        "status": "valid_window",
        "start": start,
        "end": end,
        "trading_day_count": len(trading_days),
        "trading_days": trading_days,
        "run_dir": run_dir,
    }


def resolve_oos_windows_with_status(
    data_roots: Sequence[Path],
    *,
    latest_days: int = DEFAULT_LATEST_DAYS,
) -> list[dict[str, Any]]:
    """
    Resolve OOS windows with explicit status (valid_window | no_data).

    Fixes may_late when latest < start (no invalid end < start ranges).
    Always emits oos_latest and oos_may_late entries.
    """
    all_days = collect_trading_days(data_roots)
    latest = all_days[-1] if all_days else _latest_trading_date(data_roots)
    out: list[dict[str, Any]] = []

    march_days = [d for d in all_days if d.startswith(MARCH_PREFIX)]
    if march_days:
        out.append(
            _window_valid(
                "oos_march",
                start=march_days[0],
                end=march_days[-1],
                trading_days=march_days,
            )
        )
    else:
        out.append(
            _window_no_data(
                "oos_march",
                reason="no_march_trading_days_in_intraday_1m",
                start="2026-03-01",
                end="2026-03-31",
                latest_available=latest,
            )
        )

    april_days = [d for d in all_days if APRIL_START <= d <= APRIL_END]
    if april_days:
        out.append(
            _window_valid(
                "oos_april",
                start=april_days[0],
                end=april_days[-1],
                trading_days=april_days,
            )
        )
    else:
        out.append(
            _window_no_data(
                "oos_april",
                reason="no_april_trading_days_in_intraday_1m",
                start=APRIL_START,
                end=APRIL_END,
                latest_available=latest,
            )
        )

    may_late_days = [d for d in all_days if d >= MAY_LATE_START]
    if may_late_days:
        out.append(
            _window_valid(
                "oos_may_late",
                start=may_late_days[0],
                end=may_late_days[-1],
                trading_days=may_late_days,
            )
        )
    else:
        out.append(
            _window_no_data(
                "oos_may_late",
                reason=(
                    f"no_trading_days_on_or_after_{MAY_LATE_START}; "
                    f"accumulate_data_through_{MAY_LATE_START}_or_later"
                ),
                start=MAY_LATE_START,
                end=None,
                latest_available=latest,
            )
        )

    if len(all_days) >= 1:
        n = min(int(latest_days), len(all_days))
        slice_days = all_days[-n:]
        out.append(
            _window_valid(
                "oos_latest",
                start=slice_days[0],
                end=slice_days[-1],
                trading_days=slice_days,
            )
        )
    else:
        out.append(
            _window_no_data(
                "oos_latest",
                reason="no_trading_days_in_intraday_1m",
                latest_available=None,
            )
        )

    return out


def build_latest_oos_window_report(
    *,
    data_roots: Sequence[Path],
    window_runs: Sequence[Mapping[str, Any]],
    latest_days: int = DEFAULT_LATEST_DAYS,
) -> dict[str, Any]:
    """Merge resolved windows with optional replay run_dir paths."""
    resolved = resolve_oos_windows_with_status(data_roots, latest_days=latest_days)
    run_by_id = {
        str(w.get("window_id") or w.get("id")): w
        for w in window_runs
        if w.get("run_dir")
    }
    windows: list[dict[str, Any]] = []
    for spec in resolved:
        wid = str(spec["window_id"])
        merged = dict(spec)
        extra = run_by_id.get(wid)
        if extra:
            merged["run_dir"] = str(extra.get("run_dir"))
            merged["replay_completed"] = True
        else:
            merged.setdefault("replay_completed", False)
        windows.append(merged)

    latest_spec = next((w for w in windows if w["window_id"] == "oos_latest"), None)
    return {
        "phase": 41,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "latest_trading_date": _latest_trading_date(data_roots),
        "latest_days": latest_days,
        "oos_latest": latest_spec,
        "windows": windows,
    }


def run_valid_oos_replays(
    *,
    symbols: Sequence[str],
    data_roots: Sequence[Path],
    repo_root: Path,
    output_base: Path,
    tier: str = "B",
    only_window_ids: Optional[Sequence[str]] = None,
) -> list[dict[str, Any]]:
    """Replay logic lab for valid_window specs only; record skips for no_data."""
    from research.phase37_validation import run_logic_lab_for_window

    resolved = resolve_oos_windows_with_status(data_roots)
    day_key = datetime.now().strftime("%Y%m%d")
    results: list[dict[str, Any]] = []

    for spec in resolved:
        wid = str(spec["window_id"])
        if only_window_ids and wid not in only_window_ids:
            continue
        if spec["status"] != "valid_window":
            results.append({**spec, "replay_skipped": True})
            continue
        start, end = spec["start"], spec["end"]
        days = _trading_days_between(start, end, data_roots)
        if not days:
            results.append(
                {
                    **spec,
                    "status": "no_data",
                    "reason": f"no_on_disk_days_between_{start}_and_{end}",
                    "replay_skipped": True,
                }
            )
            continue
        out = output_base / day_key / wid
        path = run_logic_lab_for_window(
            start=start,
            end=end,
            symbols=symbols,
            data_roots=data_roots,
            output_dir=out,
            repo_root=repo_root,
            tier=tier,
        )
        results.append(
            {
                **spec,
                "run_dir": str(path),
                "replay_skipped": False,
                "replay_completed": True,
            }
        )
    return results
