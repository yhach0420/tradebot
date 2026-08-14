"""V14: teardown external-backup logger NameError fix.

Does not change Strategy / ENTRY / EXIT / Universe / MarketBus.
2026-08-14 V13 Operational Validation PASS is not revised.
"""
from __future__ import annotations

import inspect
import logging
from types import SimpleNamespace

import pytest

from runner.am_pm_daily_runner import _pilot_failed_hard
from small_paper.pilot_runner import (
    apply_external_backup_teardown_logging,
    pilot_process_exit_code,
    run_live_dry_run,
)

# Exact V13 pending-path statements from 2026-08-14 traceback
# (pilot_runner.py run_live_dry_run lines 10148 then 10153).
V13_PENDING_LOG_LINE = (
    'log.warning("external backup pending (D not connected): %s", ext.get("session"))'
)
V13_EXCEPT_LOG_LINE = 'log.warning("external backup error: %s", exc)'

# 8/14 live: D: not connected → EXTERNAL_BACKUP_PENDING (checked-runner warn).
EXT_20260814_PENDING = {
    "ok": False,
    "pending": True,
    "code": "EXTERNAL_BACKUP_PENDING",
    "session": "live_session_133728",
    "error": "D not connected",
}


def test_v13_nameerror_reproduced_on_bare_log() -> None:
    """V13 behavior: undefined `log` on pending warning, then again in except."""
    ns: dict = {"ext": dict(EXT_20260814_PENDING)}
    with pytest.raises(NameError, match=r"name 'log' is not defined"):
        exec(V13_PENDING_LOG_LINE, ns)
    try:
        exec(V13_PENDING_LOG_LINE, ns)
    except Exception as exc:
        ns["exc"] = exc
        with pytest.raises(NameError, match=r"name 'log' is not defined"):
            exec(V13_EXCEPT_LOG_LINE, ns)


def test_case_a_external_backup_ok_no_warning(caplog: pytest.LogCaptureFixture) -> None:
    summary: dict = {"fatal_error": False}
    with caplog.at_level(logging.WARNING, logger="small_paper.pilot_runner"):
        apply_external_backup_teardown_logging(summary, {"ok": True, "pending": False})
    assert summary["session_external_backup"]["ok"] is True
    assert "session_external_backup_error" not in summary
    assert not any("external backup" in r.getMessage() for r in caplog.records)
    assert summary.get("fatal_error") is False


def test_case_b_external_backup_pending_warning_no_nameerror(
    caplog: pytest.LogCaptureFixture,
) -> None:
    summary: dict = {"fatal_error": False}
    with caplog.at_level(logging.WARNING, logger="small_paper.pilot_runner"):
        apply_external_backup_teardown_logging(summary, EXT_20260814_PENDING)
    assert summary["session_external_backup"]["code"] == "EXTERNAL_BACKUP_PENDING"
    assert summary["session_external_backup"]["pending"] is True
    msgs = [r.getMessage() for r in caplog.records]
    assert any("external backup pending (D not connected)" in m for m in msgs)
    assert any("live_session_133728" in m for m in msgs)
    assert summary.get("fatal_error") is False
    assert "session_external_backup_error" not in summary


def test_case_b_backup_exception_records_pending_not_fatal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    summary: dict = {"fatal_error": False}
    with caplog.at_level(logging.WARNING, logger="small_paper.pilot_runner"):
        apply_external_backup_teardown_logging(
            summary, exc=RuntimeError("external_backup_timeout")
        )
    assert summary["session_external_backup"]["pending"] is True
    assert summary["session_external_backup"]["code"] == "EXTERNAL_BACKUP_PENDING"
    assert "external_backup_timeout" in summary["session_external_backup_error"]
    assert any("external backup error" in r.getMessage() for r in caplog.records)
    assert summary.get("fatal_error") is False


def test_case_c_daily_runner_exit_ok_with_backup_warning() -> None:
    summary = {
        "stop_reason": "afternoon_session_close",
        "session_validity": "VALID",
        "fatal_error": False,
        "session_external_backup": dict(EXT_20260814_PENDING),
        "include_in_strategy_metrics": True,
    }
    assert pilot_process_exit_code(summary) == 0
    live = {
        "exit_code": 0,
        "ok": True,
        "pilot_ok": True,
        "error": None,
        "fatal_error": False,
    }
    assert _pilot_failed_hard(live) is False


def test_case_d_teardown_order_preserved() -> None:
    src = inspect.getsource(run_live_dry_run)
    markers = [
        'task="discord_session_end"',
        'task="archive_session_copy"',
        'task="external_backup"',
        "apply_external_backup_teardown_logging",
        "finalize_session_seal_propagation",
    ]
    idx = [src.index(m) for m in markers]
    assert idx == sorted(idx)
    # Writer still persists summary after backup logging, before seal.
    backup_at = src.index("apply_external_backup_teardown_logging(summary, ext)")
    write_at = src.index("writer.write_summary(summary)", backup_at)
    seal_at = src.index("finalize_session_seal_propagation", write_at)
    assert backup_at < write_at < seal_at


def test_v13_bare_log_removed_from_external_backup_block() -> None:
    src = inspect.getsource(run_live_dry_run)
    start = src.index('task="external_backup"')
    end = src.index("finalize_session_seal_propagation", start)
    block = src[start:end]
    assert "log.warning" not in block
    assert "apply_external_backup_teardown_logging" in block


def test_helper_accepts_injected_logger() -> None:
    records: list[str] = []

    class _L:
        def warning(self, msg: str, *args: object) -> None:
            records.append(msg % args if args else msg)

    summary: dict = {}
    apply_external_backup_teardown_logging(
        summary, EXT_20260814_PENDING, logger=_L()
    )
    assert records
    assert "external backup pending" in records[0]


def test_submit_cancel_live_untouched() -> None:
    summary = {
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "stop_reason": "afternoon_session_close",
    }
    apply_external_backup_teardown_logging(summary, EXT_20260814_PENDING)
    assert summary["submit"] == 0
    assert summary["cancel"] == 0
    assert summary["live"] == 0
    assert SimpleNamespace(**summary)
