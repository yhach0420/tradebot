"""Tests for Phase392 results path resolver and dual-write helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from storage.results_paths import (
    category_for_filename,
    copy_to_daily_and_category,
    cumulative_target_for_file,
    daily_runtime_dir,
    daily_target_for_file,
    dual_write_output_paths,
    legacy_reports_dir,
    top_research_dir,
)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("daily_runner_summary_20260615.json", "runtime"),
        ("universe_core10_dynamic40_price_risk_am_20260615.csv", "runtime"),
        ("phase273_live_config_shadow_summary.json", "live_candidate"),
        ("phase274_live_config_transition_report.md", "live_candidate"),
        ("phase255_sector_heat_forward_shadow_summary.json", "research"),
        ("phase262_risk_sizing_forward_summary.json", "research"),
        ("phase263_report.md", "research"),
        ("phase335_lite_realtime_board_shadow_ticks_20260615.csv", "research"),
        ("phase335_realtime_board_shadow_summary_20260615.json", "research"),
        ("phase999_unknown_review.json", "archive"),
    ],
)
def test_category_for_filename(filename: str, expected: str) -> None:
    assert category_for_filename(filename) == expected


def test_daily_and_cumulative_targets(tmp_path: Path) -> None:
    repo = tmp_path
    day = "20260615"
    name = "daily_runner_summary_20260615.json"
    assert daily_target_for_file(repo, name, day) == daily_runtime_dir(repo, day) / name
    assert cumulative_target_for_file(repo, name) is None

    research_name = "phase255_sector_heat_forward_shadow_summary.json"
    assert daily_target_for_file(repo, research_name, day).parent.name == "research"
    assert cumulative_target_for_file(repo, research_name) == top_research_dir(repo) / research_name


def test_copy_to_daily_and_category_runtime(tmp_path: Path) -> None:
    repo = tmp_path
    day = "20260615"
    legacy = legacy_reports_dir(repo)
    legacy.mkdir(parents=True)
    src = legacy / "features_20260615.csv"
    src.write_text("a,b\n1,2\n", encoding="utf-8")

    warnings = copy_to_daily_and_category(src, repo, day)
    assert warnings == []

    dest = daily_runtime_dir(repo, day) / src.name
    assert dest.is_file()
    assert dest.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
    assert not (top_research_dir(repo) / src.name).exists()


def test_copy_to_daily_and_category_research_dual(tmp_path: Path) -> None:
    repo = tmp_path
    day = "20260615"
    legacy = legacy_reports_dir(repo)
    legacy.mkdir(parents=True)
    src = legacy / "phase262_risk_sizing_forward_summary.json"
    src.write_text("{}", encoding="utf-8")

    warnings = copy_to_daily_and_category(src, repo, day)
    assert warnings == []

    daily_dest = daily_target_for_file(repo, src.name, day)
    top_dest = cumulative_target_for_file(repo, src.name)
    assert daily_dest.is_file()
    assert top_dest is not None and top_dest.is_file()


def test_copy_missing_returns_warning(tmp_path: Path) -> None:
    repo = tmp_path
    missing = legacy_reports_dir(repo) / "nope.csv"
    warnings = copy_to_daily_and_category(missing, repo, "20260615")
    assert len(warnings) == 1
    assert warnings[0].startswith("dual_write_skip_missing:")


def test_dual_write_output_paths(tmp_path: Path) -> None:
    repo = tmp_path
    day = "20260615"
    legacy = legacy_reports_dir(repo)
    legacy.mkdir(parents=True)
    p1 = legacy / "phase273_live_config_shadow_summary.json"
    p2 = legacy / "phase274_live_config_transition_summary.json"
    p1.write_text("{}", encoding="utf-8")
    p2.write_text("{}", encoding="utf-8")

    warnings = dual_write_output_paths(repo, day, {"a": p1, "b": p2})
    assert warnings == []
    assert daily_target_for_file(repo, p1.name, day).is_file()
    assert cumulative_target_for_file(repo, p1.name) is not None


def test_unknown_file_goes_to_archive(tmp_path: Path) -> None:
    repo = tmp_path
    day = "20260615"
    legacy = legacy_reports_dir(repo)
    legacy.mkdir(parents=True)
    src = legacy / "phase390_misc.json"
    src.write_text("{}", encoding="utf-8")

    copy_to_daily_and_category(src, repo, day)
    archive_daily = repo / "kabu_native" / "results" / "daily" / day / "archive" / src.name
    archive_top = repo / "kabu_native" / "results" / "archive" / src.name
    assert archive_daily.is_file()
    assert archive_top.is_file()
