"""
Phase392: Results directory layout helpers — dual-write only (Phase 1).

Legacy canonical read path remains ``kabu_native/results/reports/``.
"""

from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path
from typing import Iterable, Mapping, Optional

log = logging.getLogger(__name__)

_DAY_IN_NAME = re.compile(r"(20\d{6})")

RUNTIME_PREFIXES: tuple[str, ...] = (
    "daily_runner_summary_",
    "daily_runner_commands_",
    "phase148_",
    "universe_core10_dynamic40_",
    "universe_vol_liq_",
    "features_",
    "small_paper_safety_",
    "phase113_",
    "opening_dynamic50_",
    "small_paper_gate_diagnosis_",
)

LIVE_CANDIDATE_PREFIXES: tuple[str, ...] = (
    "phase273_",
    "phase274_",
)

RESEARCH_PREFIXES: tuple[str, ...] = (
    "phase255_",
    "phase262_",
    "phase263_",
    "phase335_lite_",
    "phase335_",
    "phase409_",
    "phase411_",
)

Category = str  # runtime | live_candidate | research | archive


def results_root(repo_root: Path) -> Path:
    return repo_root / "kabu_native" / "results"


def legacy_reports_dir(repo_root: Path) -> Path:
    return results_root(repo_root) / "reports"


def daily_dir(repo_root: Path, day: str) -> Path:
    return results_root(repo_root) / "daily" / str(day)


def daily_runtime_dir(repo_root: Path, day: str) -> Path:
    return daily_dir(repo_root, day) / "runtime"


def daily_live_candidate_dir(repo_root: Path, day: str) -> Path:
    return daily_dir(repo_root, day) / "live_candidate"


def daily_research_dir(repo_root: Path, day: str) -> Path:
    return daily_dir(repo_root, day) / "research"


def daily_archive_dir(repo_root: Path, day: str) -> Path:
    return daily_dir(repo_root, day) / "archive"


def top_live_candidate_dir(repo_root: Path) -> Path:
    return results_root(repo_root) / "live_candidate"


def top_research_dir(repo_root: Path) -> Path:
    return results_root(repo_root) / "research"


def top_archive_dir(repo_root: Path) -> Path:
    return results_root(repo_root) / "archive"


def category_for_filename(filename: str) -> Category:
    name = Path(filename).name
    for prefix in RUNTIME_PREFIXES:
        if name.startswith(prefix):
            return "runtime"
    for prefix in LIVE_CANDIDATE_PREFIXES:
        if name.startswith(prefix):
            return "live_candidate"
    for prefix in RESEARCH_PREFIXES:
        if name.startswith(prefix):
            return "research"
    return "archive"


def _daily_category_dir(repo_root: Path, day: str, category: Category) -> Path:
    if category == "runtime":
        return daily_runtime_dir(repo_root, day)
    if category == "live_candidate":
        return daily_live_candidate_dir(repo_root, day)
    if category == "research":
        return daily_research_dir(repo_root, day)
    return daily_archive_dir(repo_root, day)


def _top_category_dir(repo_root: Path, category: Category) -> Optional[Path]:
    if category == "runtime":
        return None
    if category == "live_candidate":
        return top_live_candidate_dir(repo_root)
    if category == "research":
        return top_research_dir(repo_root)
    return top_archive_dir(repo_root)


def daily_target_for_file(repo_root: Path, filename: str, day: str) -> Path:
    category = category_for_filename(filename)
    return _daily_category_dir(repo_root, day, category) / Path(filename).name


def cumulative_target_for_file(repo_root: Path, filename: str) -> Optional[Path]:
    category = category_for_filename(filename)
    top = _top_category_dir(repo_root, category)
    if top is None:
        return None
    return top / Path(filename).name


def copy_to_daily_and_category(
    src_path: Path,
    repo_root: Path,
    day: str,
) -> list[str]:
    """
    Copy ``src_path`` into the organized layout.

    Returns warning strings (empty on success). Never raises.
    """
    warnings: list[str] = []
    src = Path(src_path)
    if not src.is_file():
        warnings.append(f"dual_write_skip_missing:{src}")
        return warnings

    filename = src.name
    category = category_for_filename(filename)

    try:
        daily_dest = _daily_category_dir(repo_root, day, category) / filename
        daily_dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, daily_dest)

        top_dest_dir = _top_category_dir(repo_root, category)
        if top_dest_dir is not None:
            top_dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, top_dest_dir / filename)
    except Exception as exc:
        msg = f"dual_write_failed:{src}:{type(exc).__name__}:{exc}"
        warnings.append(msg)
        log.warning(msg)

    return warnings


def dual_write_output_paths(
    repo_root: Path,
    day: str,
    paths: Mapping[str, Path] | Iterable[Path],
) -> list[str]:
    """Dual-write multiple output paths after legacy write. Never raises."""
    warnings: list[str] = []
    if isinstance(paths, Mapping):
        iterable: Iterable[Path] = paths.values()
    else:
        iterable = paths
    for path in iterable:
        warnings.extend(copy_to_daily_and_category(Path(path), repo_root, day))
    return warnings


def dual_write_runtime_day_artifacts(
    repo_root: Path,
    day: str,
    *,
    explicit_paths: Iterable[Path] | None = None,
) -> list[str]:
    """Copy runtime-category artifacts for ``day`` from legacy reports (and explicit paths)."""
    warnings: list[str] = []
    to_copy: dict[str, Path] = {}

    if explicit_paths:
        for path in explicit_paths:
            p = Path(path)
            if p.is_file():
                to_copy[p.name] = p

    reports = legacy_reports_dir(repo_root)
    if reports.is_dir():
        for entry in reports.iterdir():
            if not entry.is_file():
                continue
            if day not in entry.name:
                continue
            if category_for_filename(entry.name) != "runtime":
                continue
            to_copy[entry.name] = entry

    for path in to_copy.values():
        warnings.extend(copy_to_daily_and_category(path, repo_root, day))

    return warnings


def infer_day_from_result(result: Mapping[str, object]) -> Optional[str]:
    last_run = result.get("last_run")
    if isinstance(last_run, Mapping):
        day = last_run.get("day")
        if day:
            return str(day)
    return None
