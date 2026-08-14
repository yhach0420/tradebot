"""Fail-closed Capture reuse + no-spawn proofs."""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from small_paper.ingress_run_identity import (
    ROLE_MARKET_INGRESS_SERVICE,
    STATUS_SCHEMA_VERSION,
)
from small_paper.market_ingress_reuse import (
    EXPECTED_UNIVERSE_N,
    attach_existing_ingress,
    validate_reusable_ingress,
)
from small_paper.paper_trade_checked_runner import PaperTradeCheckedRunner


def _write_day(
    day_dir: Path,
    *,
    pid: int = 7112,
    state: str = "WAITING_FIRST_PUSH",
    desired: int = 0,
    registered: int = 0,
    launch_nonce: str = "nonce-reuse-test",
    start_ident: str = "20260815090000.000000+540",
) -> None:
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "ingress.pid").write_text(str(pid), encoding="utf-8")
    spawn = {
        "pid": pid,
        "trading_date": day_dir.name,
        "launch_nonce": launch_nonce,
        "ingress_run_id": f"ingrun_{day_dir.name}_{launch_nonce[:16]}",
        "activation_id": "TEST_ACTIVATION",
        "activation_sha": "abc123",
        "bus_identity": f"tcp://127.0.0.1:18730|{day_dir.name}|{launch_nonce}",
        "process_start_identity": start_ident,
        "cmd": [
            "python",
            "-m",
            "small_paper.market_ingress_service",
            "--native-root",
            str(day_dir.parents[2]),
            "--trading-date",
            day_dir.name,
        ],
        "synthetic": False,
    }
    (day_dir / "ingress_spawn.json").write_text(json.dumps(spawn), encoding="utf-8")
    status = {
        "pid": pid,
        "state": state,
        "desired_symbol_count": desired,
        "registered_symbol_count": registered,
        "raw_last_sequence": 0,
        "paper_consumer_lag": 0,
        "entry_blocked": True,
        "entry_block_reason": "WAITING_FIRST_PUSH",
        "status_schema_version": STATUS_SCHEMA_VERSION,
        "activation_id": "TEST_ACTIVATION",
        "activation_sha": "abc123",
        "ingress_run_id": spawn["ingress_run_id"],
        "launch_nonce": launch_nonce,
        "process_start_identity": start_ident,
        "trading_date": day_dir.name,
        "role": ROLE_MARKET_INGRESS_SERVICE,
        "bus_identity": spawn["bus_identity"],
        "status_written_unix": time.time(),
    }
    (day_dir / "ingress_status.json").write_text(json.dumps(status), encoding="utf-8")


def _live(pid: int = 7112, start_ident: str = "20260815090000.000000+540") -> dict:
    return {"exists": True, "cmdline": "", "create_time": start_ident, "pid": pid}


def test_validate_reuse_ok_pending_register(tmp_path: Path) -> None:
    native = tmp_path / "kabu_native"
    day = "20260727"
    day_dir = native / "data" / "market_capture" / day
    _write_day(day_dir, pid=7112, desired=0, registered=0)
    with patch("small_paper.market_ingress_reuse.query_process", return_value=_live()):
        r = validate_reusable_ingress(
            native_root=native,
            trading_date=day,
            expected_symbol_count=50,
            expected_pid=7112,
        )
    assert r["ok"] is True
    assert r["spawned"] is False
    assert r["runtime_register_pending"] is True
    assert r["pid"] == 7112


def test_validate_reuse_ok_50_50(tmp_path: Path) -> None:
    native = tmp_path / "kabu_native"
    day = "20260727"
    day_dir = native / "data" / "market_capture" / day
    _write_day(day_dir, pid=7112, state="RUNNING", desired=50, registered=50)
    with patch("small_paper.market_ingress_reuse.query_process", return_value=_live()):
        r = validate_reusable_ingress(
            native_root=native,
            trading_date=day,
            expected_symbol_count=EXPECTED_UNIVERSE_N,
            expected_pid=7112,
        )
    assert r["ok"] is True
    assert r["runtime_register_pending"] is False


@pytest.mark.parametrize(
    "kwargs,reason_substr",
    [
        ({"expected_symbol_count": 40}, "universe_expected_not_50"),
        ({"expected_pid": 9999}, "status_pid_mismatch"),
    ],
)
def test_validate_fail_close_universe_or_pid(tmp_path: Path, kwargs, reason_substr) -> None:
    native = tmp_path / "kabu_native"
    day = "20260727"
    _write_day(native / "data" / "market_capture" / day, pid=7112)
    base = {"native_root": native, "trading_date": day, "expected_symbol_count": 50}
    base.update(kwargs)
    with patch("small_paper.market_ingress_reuse.query_process", return_value=_live()):
        r = validate_reusable_ingress(**base)
    assert r["ok"] is False
    assert reason_substr in str(r["reason"])


def test_validate_fail_close_pid_dead(tmp_path: Path) -> None:
    native = tmp_path / "kabu_native"
    day = "20260727"
    _write_day(native / "data" / "market_capture" / day, pid=7112)
    with patch("small_paper.market_ingress_reuse.query_process", return_value={"exists": False}):
        r = validate_reusable_ingress(
            native_root=native, trading_date=day, expected_symbol_count=50, expected_pid=7112
        )
    assert r["ok"] is False
    assert "pid_dead" in str(r["reason"]) or "pid_not_running" in str(r["reason"])


def test_validate_fail_close_universe_partial(tmp_path: Path) -> None:
    native = tmp_path / "kabu_native"
    day = "20260727"
    _write_day(native / "data" / "market_capture" / day, pid=7112, desired=50, registered=10)
    with patch("small_paper.market_ingress_reuse.query_process", return_value=_live()):
        r = validate_reusable_ingress(
            native_root=native, trading_date=day, expected_symbol_count=50, expected_pid=7112
        )
    assert r["ok"] is False
    assert "universe_not_50_50" in r["reason"]


def test_validate_reuse_ok_register_retry_pending_streaming(tmp_path: Path) -> None:
    """desired=50/registered=0 + REGISTER_FAILED but online/streaming → allow reuse attach."""
    native = tmp_path / "kabu_native"
    day = "20260804"
    day_dir = native / "data" / "market_capture" / day
    _write_day(day_dir, pid=30100, state="WAITING_FIRST_PUSH", desired=50, registered=0)
    status = json.loads((day_dir / "ingress_status.json").read_text(encoding="utf-8"))
    status["entry_block_reason"] = "REGISTER_FAILED"
    status["raw_last_sequence"] = 100
    status["bus"] = {"publish_ok": 50, "tcp_clients": 0}
    (day_dir / "ingress_status.json").write_text(json.dumps(status), encoding="utf-8")
    with patch("small_paper.market_ingress_reuse.query_process", return_value=_live()):
        r = validate_reusable_ingress(
            native_root=native,
            trading_date=day,
            expected_symbol_count=50,
            expected_pid=30100,
        )
    assert r["ok"] is True
    assert r["register_retry_pending"] is True
    assert r["runtime_register_pending"] is True


def test_attach_does_not_spawn(tmp_path: Path) -> None:
    native = tmp_path / "kabu_native"
    day = "20260727"
    _write_day(native / "data" / "market_capture" / day, pid=7112)
    with patch("small_paper.market_ingress_reuse.query_process", return_value=_live()):
        with patch("subprocess.Popen") as popen:
            r = attach_existing_ingress(
                native_root=native, trading_date=day, expected_symbol_count=50, expected_pid=7112
            )
            assert r["ok"] is True
            popen.assert_not_called()


def test_checked_runner_reuse_skips_spawn(tmp_path: Path) -> None:
    native = tmp_path / "kabu_native"
    day = "20260727"
    day_dir = native / "data" / "market_capture" / day
    _write_day(day_dir, pid=7112)
    (native / "src" / "small_paper").mkdir(parents=True, exist_ok=True)
    runner = PaperTradeCheckedRunner(
        repo_root=tmp_path,
        native_root=native,
        skip_paper=True,
        skip_w4s=True,
        reuse_capture=True,
        reuse_capture_pid=7112,
        no_pause=True,
    )
    runner.trading_date = day
    runner.capture["registration"] = {"expected_count": 50}
    runner.capture["universe"] = {"symbol_count": 50}
    runner.universe_prebuild = {"symbol_count": 50}

    with patch("small_paper.market_ingress_protocol.market_ingress_v2_enabled", return_value=True):
        with patch("small_paper.market_ingress_reuse.query_process", return_value=_live()):
            with patch("small_paper.market_ingress_spawn.spawn_ingress_process") as spawn:
                with patch("subprocess.Popen") as popen:
                    ok = runner.step_start_capture()
                    assert ok is True
                    spawn.assert_not_called()
                    popen.assert_not_called()
                    assert runner.capture.get("reused") is True
                    assert runner.capture.get("pid") == 7112
                    assert runner._owned_capture is None


def test_checked_runner_reuse_fail_close_no_spawn(tmp_path: Path) -> None:
    native = tmp_path / "kabu_native"
    day = "20260727"
    _write_day(native / "data" / "market_capture" / day, pid=7112)
    runner = PaperTradeCheckedRunner(
        repo_root=tmp_path,
        native_root=native,
        skip_paper=True,
        skip_w4s=True,
        reuse_capture=True,
        reuse_capture_pid=7112,
        no_pause=True,
    )
    runner.trading_date = day
    runner.capture["registration"] = {"expected_count": 50}
    with patch("small_paper.market_ingress_protocol.market_ingress_v2_enabled", return_value=True):
        with patch("small_paper.market_ingress_reuse.query_process", return_value={"exists": False}):
            with patch("small_paper.market_ingress_spawn.spawn_ingress_process") as spawn:
                ok = runner.step_start_capture()
                assert ok is False
                spawn.assert_not_called()
                assert runner._owned_capture is None


def test_cleanup_skips_reused_capture(tmp_path: Path) -> None:
    runner = PaperTradeCheckedRunner(
        repo_root=tmp_path,
        native_root=tmp_path / "kabu_native",
        skip_paper=True,
        reuse_capture=True,
        no_pause=True,
    )
    runner.capture["reused"] = True
    runner.capture["pid"] = 7112
    out = runner.cleanup_owned_capture(reason="normal_exit")
    assert out.get("skipped") is True
    assert out.get("skip_reason") == "reused_capture_not_owned"
