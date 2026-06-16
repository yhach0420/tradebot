"""
Phase393: Post-paper daily artifact organizer.

Scans legacy ``results/reports/`` and copies same-day artifacts into
``results/daily/YYYYMMDD/{runtime,live_candidate,research,archive}/``.

Legacy reports/ is never deleted or moved. Read paths unchanged.
"""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from storage.results_paths import (
    daily_dir,
    daily_live_candidate_dir,
    daily_runtime_dir,
    legacy_reports_dir,
)

JST = ZoneInfo("Asia/Tokyo")
log = logging.getLogger(__name__)

CUMULATIVE_LIVE_PREFIXES: tuple[str, ...] = ("phase273_", "phase274_")
CUMULATIVE_RESEARCH_PREFIXES: tuple[str, ...] = ("phase255_", "phase262_", "phase263_", "phase409_")


def _mtime_day_jst(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, JST).strftime("%Y%m%d")


def _json_contains_day(obj: Any, day: str) -> bool:
    if obj == day:
        return True
    if isinstance(obj, dict):
        for key in ("day", "day_stamp", "validation_day"):
            if str(obj.get(key) or "") == day:
                return True
        period_days = obj.get("period_days")
        if isinstance(period_days, list) and day in period_days:
            return True
        last_run = obj.get("last_run")
        if isinstance(last_run, dict) and str(last_run.get("day") or "") == day:
            return True
        for value in obj.values():
            if _json_contains_day(value, day):
                return True
    elif isinstance(obj, list):
        for item in obj:
            if _json_contains_day(item, day):
                return True
    return False


def _summary_json_contains_day(path: Path, day: str) -> bool:
    if path.suffix.lower() != ".json":
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    return _json_contains_day(payload, day)


def _is_cumulative_shadow(name: str) -> bool:
    return name.startswith(CUMULATIVE_LIVE_PREFIXES + CUMULATIVE_RESEARCH_PREFIXES)


def _classify_cumulative(name: str) -> Optional[str]:
    if name.startswith(CUMULATIVE_LIVE_PREFIXES):
        return "live_candidate"
    if name.startswith(CUMULATIVE_RESEARCH_PREFIXES):
        return "research"
    return None


def _classify_dated_filename(name: str) -> str:
    if name.startswith(
        (
            "daily_runner_summary_",
            "daily_runner_commands_",
            "phase148_",
            "universe_",
            "features_",
            "small_paper_safety_",
            "phase113_",
            "opening_dynamic50_",
            "small_paper_gate_diagnosis_",
        )
    ):
        return "runtime"
    if name.startswith(("phase335_lite_", "phase335_")):
        return "research"
    if name.startswith(CUMULATIVE_LIVE_PREFIXES):
        return "live_candidate"
    if name.startswith(CUMULATIVE_RESEARCH_PREFIXES):
        return "research"
    return "archive"


def _should_include_cumulative(path: Path, day: str) -> bool:
    try:
        if _mtime_day_jst(path) == day:
            return True
    except OSError:
        return False
    return _summary_json_contains_day(path, day)


def _daily_category_dir(repo_root: Path, day: str, category: str) -> Path:
    if category == "runtime":
        return daily_runtime_dir(repo_root, day)
    if category == "live_candidate":
        return daily_live_candidate_dir(repo_root, day)
    if category == "research":
        return daily_dir(repo_root, day) / "research"
    return daily_dir(repo_root, day) / "archive"


def _copy_file(src: Path, dest: Path) -> Optional[str]:
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return None
    except Exception as exc:
        return f"organize_copy_failed:{src}:{type(exc).__name__}:{exc}"


def organize_daily_artifacts(repo_root: Path, day: str) -> dict[str, Any]:
    """
    Copy same-day report artifacts into ``results/daily/YYYYMMDD/``.

    Never raises. Returns manifest dict (also written to
    ``_daily_artifact_manifest.json``).
    """
    day = str(day)
    repo_root = Path(repo_root)
    reports = legacy_reports_dir(repo_root)
    daily_root = daily_dir(repo_root, day)

    files_by_category: dict[str, list[str]] = {
        "runtime": [],
        "live_candidate": [],
        "research": [],
        "archive": [],
    }
    warnings: list[str] = []
    copied_count = 0
    skipped_count = 0

    if not reports.is_dir():
        warnings.append(f"organize_skip_missing_reports:{reports}")
        manifest = _build_manifest(
            repo_root=repo_root,
            day=day,
            copied_count=0,
            skipped_count=0,
            warning_count=len(warnings),
            files_by_category=files_by_category,
            warnings=warnings,
        )
        _write_manifest(daily_root, manifest)
        return manifest

    for entry in sorted(reports.iterdir(), key=lambda p: p.name):
        if not entry.is_file():
            continue
        name = entry.name
        category: Optional[str] = None

        if day in name:
            category = _classify_dated_filename(name)
        elif _is_cumulative_shadow(name):
            if _should_include_cumulative(entry, day):
                category = _classify_cumulative(name)
            else:
                skipped_count += 1
                continue
        else:
            skipped_count += 1
            continue

        if category is None:
            skipped_count += 1
            continue

        dest = _daily_category_dir(repo_root, day, category) / name
        err = _copy_file(entry, dest)
        if err:
            warnings.append(err)
            log.warning(err)
            continue

        files_by_category[category].append(name)
        copied_count += 1

    manifest = _build_manifest(
        repo_root=repo_root,
        day=day,
        copied_count=copied_count,
        skipped_count=skipped_count,
        warning_count=len(warnings),
        files_by_category=files_by_category,
        warnings=warnings,
    )
    _write_manifest(daily_root, manifest)
    return manifest


def _build_manifest(
    *,
    repo_root: Path,
    day: str,
    copied_count: int,
    skipped_count: int,
    warning_count: int,
    files_by_category: dict[str, list[str]],
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "phase": "393-Daily-Artifact-Organizer",
        "day": day,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "copied_count": copied_count,
        "skipped_count": skipped_count,
        "warning_count": warning_count,
        "files_by_category": files_by_category,
        "warnings": warnings,
        "legacy_reports_dir": str(legacy_reports_dir(repo_root)),
        "daily_dir": str(daily_dir(repo_root, day)),
    }


def _write_manifest(daily_root: Path, manifest: dict[str, Any]) -> None:
    try:
        daily_root.mkdir(parents=True, exist_ok=True)
        path = daily_root / "_daily_artifact_manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except Exception as exc:
        log.warning("organize_manifest_write_failed: %s", exc)
