"""Tests for Phase393 daily artifact organizer."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from storage.daily_artifact_organizer import organize_daily_artifacts
from storage.results_paths import legacy_reports_dir

JST = ZoneInfo("Asia/Tokyo")


def _touch(path: Path, *, day: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    y = int(day[:4])
    m = int(day[4:6])
    d = int(day[6:8])
    ts = datetime(y, m, d, 12, 0, tzinfo=JST).timestamp()
    path.touch()
    import os

    os.utime(path, (ts, ts))


def test_organize_dated_runtime_files_only(tmp_path: Path) -> None:
    repo = tmp_path
    day = "20260615"
    other_day = "20260614"
    reports = legacy_reports_dir(repo)
    reports.mkdir(parents=True)
    keep_old = reports / f"daily_runner_summary_{other_day}.json"
    keep_old.write_text('{"old": true}', encoding="utf-8")
    today = reports / f"daily_runner_summary_{day}.json"
    today.write_text('{"today": true}', encoding="utf-8")
    universe = reports / f"universe_core10_dynamic40_price_risk_am_{day}.csv"
    universe.write_text("sym\n", encoding="utf-8")

    manifest = organize_daily_artifacts(repo, day)

    assert manifest["copied_count"] == 2
    assert keep_old.exists()
    assert (repo / "kabu_native/results/daily" / day / "runtime" / today.name).is_file()
    assert (repo / "kabu_native/results/daily" / day / "runtime" / universe.name).is_file()
    assert not (repo / "kabu_native/results/daily" / day / "runtime" / keep_old.name).exists()

    manifest_path = repo / "kabu_native/results/daily" / day / "_daily_artifact_manifest.json"
    assert manifest_path.is_file()
    loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert loaded["copied_count"] == 2
    assert "daily_runner_summary" in loaded["files_by_category"]["runtime"][0]


def test_organize_unknown_dated_goes_archive(tmp_path: Path) -> None:
    repo = tmp_path
    day = "20260615"
    reports = legacy_reports_dir(repo)
    reports.mkdir(parents=True)
    misc = reports / f"phase390_misc_{day}.json"
    misc.write_text("{}", encoding="utf-8")

    manifest = organize_daily_artifacts(repo, day)

    assert misc.name in manifest["files_by_category"]["archive"]
    assert (repo / "kabu_native/results/daily" / day / "archive" / misc.name).is_file()


def test_organize_cumulative_shadow_by_mtime(tmp_path: Path) -> None:
    repo = tmp_path
    day = "20260615"
    reports = legacy_reports_dir(repo)
    reports.mkdir(parents=True)
    p273 = reports / "phase273_live_config_shadow_summary.json"
    _touch(p273, day=day)
    p255 = reports / "phase255_sector_heat_forward_shadow_summary.json"
    _touch(p255, day=day)
    stale = reports / "phase274_live_config_transition_summary.json"
    _touch(stale, day="20260601")

    manifest = organize_daily_artifacts(repo, day)

    assert p273.name in manifest["files_by_category"]["live_candidate"]
    assert p255.name in manifest["files_by_category"]["research"]
    assert stale.name not in sum(manifest["files_by_category"].values(), [])
    assert (repo / "kabu_native/results/daily" / day / "live_candidate" / p273.name).is_file()
    assert (repo / "kabu_native/results/daily" / day / "research" / p255.name).is_file()


def test_organize_cumulative_shadow_by_summary_day(tmp_path: Path) -> None:
    repo = tmp_path
    day = "20260615"
    reports = legacy_reports_dir(repo)
    reports.mkdir(parents=True)
    p262 = reports / "phase262_risk_sizing_forward_summary.json"
    p262.write_text(
        json.dumps({"last_run": {"day": day, "status": "logged"}}),
        encoding="utf-8",
    )

    manifest = organize_daily_artifacts(repo, day)

    assert p262.name in manifest["files_by_category"]["research"]
    assert manifest["skipped_count"] >= 0


def test_organize_phase335_dated_to_research(tmp_path: Path) -> None:
    repo = tmp_path
    day = "20260615"
    reports = legacy_reports_dir(repo)
    reports.mkdir(parents=True)
    ticks = reports / f"phase335_lite_realtime_board_shadow_ticks_{day}.csv"
    ticks.write_text("h\n", encoding="utf-8")

    manifest = organize_daily_artifacts(repo, day)

    assert ticks.name in manifest["files_by_category"]["research"]
