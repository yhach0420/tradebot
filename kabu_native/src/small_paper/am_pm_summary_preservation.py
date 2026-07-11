"""
Phase653: Preserve AM/PM session summaries (storage only).

Copies small_paper_summary.json to session-kind-specific filenames at session end
and mirrors to results/reports/daily_runner/ when the daily runner finishes.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Mapping, Optional

SESSION_SUMMARY_AM = "small_paper_summary_am.json"
SESSION_SUMMARY_PM = "small_paper_summary_pm.json"


def session_kind_from_summary(summary: Mapping[str, Any]) -> str:
    am_pm = summary.get("am_pm_session")
    if isinstance(am_pm, Mapping):
        kind = str(am_pm.get("kind") or "").strip().lower()
        if kind in ("am", "pm"):
            return kind
    kind = str(summary.get("session_kind") or "").strip().lower()
    if kind in ("am", "pm"):
        return kind
    return ""


def session_kind_from_session_cfg(session_cfg: Mapping[str, Any]) -> str:
    am_pm = session_cfg.get("am_pm_session")
    if isinstance(am_pm, Mapping):
        kind = str(am_pm.get("kind") or "").strip().lower()
        if kind in ("am", "pm"):
            return kind
    return ""


def _session_copy_name(session_kind: str) -> Optional[str]:
    kind = str(session_kind or "").strip().lower()
    if kind == "am":
        return SESSION_SUMMARY_AM
    if kind == "pm":
        return SESSION_SUMMARY_PM
    return None


def preserve_session_summary_copy(
    output_dir: Path,
    *,
    session_kind: str,
) -> Optional[Path]:
    """Copy small_paper_summary.json -> small_paper_summary_{am|pm}.json in session dir."""
    dest_name = _session_copy_name(session_kind)
    if not dest_name:
        return None
    src = output_dir / "small_paper_summary.json"
    if not src.is_file():
        return None
    dest = output_dir / dest_name
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return dest


def _best_session_summary_path(session_dir: Path, *, session_kind: str) -> Optional[Path]:
    if not session_dir.is_dir():
        return None
    kind = str(session_kind).lower()
    preferred = session_dir / (SESSION_SUMMARY_AM if kind == "am" else SESSION_SUMMARY_PM)
    if preferred.is_file():
        return preferred
    fallback = session_dir / "small_paper_summary.json"
    return fallback if fallback.is_file() else None


def daily_runner_summary_paths(repo_root: Path, day_stamp: str) -> tuple[Path, Path]:
    base = Path(repo_root) / "kabu_native" / "results" / "reports" / "daily_runner"
    return (
        base / f"daily_summary_am_{day_stamp}.json",
        base / f"daily_summary_pm_{day_stamp}.json",
    )


def preserve_daily_runner_summaries(
    repo_root: Path,
    *,
    day_stamp: str,
    am_session_dir: Optional[Path] = None,
    pm_session_dir: Optional[Path] = None,
) -> dict[str, Optional[str]]:
    """Mirror AM/PM session summaries into results/reports/daily_runner/."""
    am_daily, pm_daily = daily_runner_summary_paths(repo_root, day_stamp)
    am_daily.parent.mkdir(parents=True, exist_ok=True)

    out: dict[str, Optional[str]] = {
        "am_summary_path": None,
        "pm_summary_path": None,
    }

    am_src = _best_session_summary_path(am_session_dir, session_kind="am") if am_session_dir else None
    if am_src is not None:
        shutil.copy2(am_src, am_daily)
        out["am_summary_path"] = str(am_daily)

    pm_src = _best_session_summary_path(pm_session_dir, session_kind="pm") if pm_session_dir else None
    if pm_src is not None:
        shutil.copy2(pm_src, pm_daily)
        out["pm_summary_path"] = str(pm_daily)

    return out


def preserve_session_summary_at_end(
    output_dir: Path,
    *,
    session_cfg: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> Optional[Path]:
    kind = session_kind_from_summary(summary) or session_kind_from_session_cfg(session_cfg)
    if not kind:
        return None
    return preserve_session_summary_copy(output_dir, session_kind=kind)


def rel_path(repo_root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(repo_root)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def load_summary_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
