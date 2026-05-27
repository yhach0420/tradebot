"""Phase 148 bugfix: session dir diff detection (list/set) and live_session_* paths."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from runner.am_pm_daily_runner import (  # noqa: E402
    DailyRunnerOptions,
    DailyRunnerState,
    diff_new_session_dirs,
    discover_session_dir,
    make_state,
    run_pilot_session,
    _list_session_dirs,
    _pilot_failed_hard,
    _run_daily_runner_body,
)


def _path(name: str) -> Path:
    return Path(f"/tmp/small_paper/20260525/{name}")


def test_diff_before_list_after_set() -> None:
    before = [_path("live_session_075700")]
    after = {_path("live_session_075700"), _path("live_session_075733")}
    new = diff_new_session_dirs(before, after)
    assert [p.name for p in new] == ["live_session_075733"]


def test_diff_before_set_after_list() -> None:
    before = {_path("live_full_session_080000")}
    after = [_path("live_full_session_080000"), _path("live_full_session_081000")]
    new = diff_new_session_dirs(before, after)
    assert [p.name for p in new] == ["live_full_session_081000"]


def test_diff_no_new_dirs() -> None:
    dirs = [_path("live_session_075733")]
    assert diff_new_session_dirs(dirs, dirs) == []
    assert diff_new_session_dirs(set(dirs), set(dirs)) == []


def test_diff_multiple_new_dirs() -> None:
    before = set()
    after = [_path("live_session_075733"), _path("live_session_075800")]
    new = diff_new_session_dirs(before, after)
    assert [p.name for p in new] == ["live_session_075733", "live_session_075800"]


def test_list_session_dirs_includes_live_session_prefix(tmp_path: Path) -> None:
    day = tmp_path / "kabu_native" / "results" / "small_paper" / "20260525"
    (day / "live_session_075733").mkdir(parents=True)
    (day / "live_full_session_081000").mkdir(parents=True)
    (day / "other_dir").mkdir(parents=True)
    state = make_state(
        tmp_path,
        tmp_path / "kabu_native",
        DailyRunnerOptions(day_stamp="20260525"),
    )
    names = sorted(p.name for p in _list_session_dirs(state))
    assert names == ["live_full_session_081000", "live_session_075733"]


def test_discover_live_session_dir_with_summary(tmp_path: Path) -> None:
    day = tmp_path / "kabu_native" / "results" / "small_paper" / "20260525"
    sess = day / "live_session_075733"
    sess.mkdir(parents=True)
    (sess / "small_paper_summary.json").write_text(
        json.dumps({"am_pm_session": {"kind": "am", "session_end": "11:25"}}),
        encoding="utf-8",
    )
    state = make_state(
        tmp_path,
        tmp_path / "kabu_native",
        DailyRunnerOptions(day_stamp="20260525"),
    )
    found = discover_session_dir(state, kind="am")
    assert found is not None
    assert found.name == "live_session_075733"


def test_pilot_ok_without_summary_allows_pm_transition() -> None:
    live = {
        "exit_code": 0,
        "pilot_ok": True,
        "session_detection_ok": False,
        "warning": "session_dir_not_detected",
        "ok": True,
    }
    assert not _pilot_failed_hard(live)


def test_run_pilot_session_does_not_raise_on_list_set(tmp_path: Path) -> None:
    day = tmp_path / "kabu_native" / "results" / "small_paper" / "20260525"
    before = day / "live_session_075700"
    before.mkdir(parents=True)
    after = day / "live_session_075733"
    state = make_state(
        tmp_path,
        tmp_path / "kabu_native",
        DailyRunnerOptions(day_stamp="20260525", dry_run_only=False),
    )
    state.am_prep = {"am_csv": "kabu_native/results/reports/universe_core10_dynamic40_am_20260525.csv"}

    class FakeProc:
        returncode = 0

    calls = {"n": 0}

    def fake_list(st: DailyRunnerState) -> list[Path]:
        calls["n"] += 1
        if calls["n"] == 1:
            return [before]
        after.mkdir(parents=True, exist_ok=True)
        (after / "small_paper_summary.json").write_text(
            json.dumps({"am_pm_session": {"kind": "am"}}),
            encoding="utf-8",
        )
        return [before, after]

    with patch("runner.am_pm_daily_runner.subprocess.run", return_value=FakeProc()):
        with patch("runner.am_pm_daily_runner._list_session_dirs", side_effect=fake_list):
            result = run_pilot_session(state, session="am")

    assert "error" not in result
    assert result["pilot_ok"] is True
    assert result["session_dir"] is not None


def test_am_detection_warning_pm_prep_reached(tmp_path: Path) -> None:
    """Real-run path: AM exit 0 + missing summary -> warning, PM prep still runs."""
    state = make_state(
        tmp_path,
        tmp_path / "kabu_native",
        DailyRunnerOptions(
            day_stamp="20260521",
            skip_safety=True,
            skip_kabu=True,
            dry_run_only=False,
            skip_pm=False,
        ),
    )
    am_live = {
        "exit_code": 0,
        "pilot_ok": True,
        "session_detection_ok": False,
        "warning": "session_dir_not_detected",
        "ok": True,
    }

    with patch("runner.am_pm_daily_runner.preflight", return_value=True):
        with patch(
            "runner.am_pm_daily_runner.build_am_universe",
            return_value={
                "ok": True,
                "am_csv": "kabu_native/results/reports/universe_core10_dynamic40_am_20260521.csv",
            },
        ):
            with patch("runner.am_pm_daily_runner.run_pilot_session", return_value=am_live):
                with patch(
                    "runner.am_pm_daily_runner.wait_until_hhmm",
                    return_value={"skipped": True},
                ):
                    with patch("runner.am_pm_daily_runner.build_pm_universe") as pm_build:
                        pm_build.return_value = {"ok": False, "error": "stop_here"}
                        rc = _run_daily_runner_body(state)

    assert rc == 2
    assert state.stopped_reason == "pm_universe"
    assert state.sessions.get("am_warning") == "session_dir_not_detected"
    pm_build.assert_called_once()
