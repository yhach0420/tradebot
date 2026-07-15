"""Phase687W19 — startup path / residual / clock / fault / restart certification tests."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from research.exposure_gate import ExposureGate
from small_paper.capture_child_cleanup import (
    prepare_day_dir_operator_stop_for_spawn,
    parse_operator_stop_flag,
)
from small_paper.market_capture_sidecar import OPERATOR_STOP, capture_day_dir
from small_paper.operational_recovery import probe_workspace_recovery
from small_paper.startup_e2e_certification import (
    expected_state_for_clock,
    prove_exposure_gate_reachable_under_injected_clock,
    scan_residuals,
    write_startup_path_inventory,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]


def test_startup_path_inventory_writable(tmp_path: Path):
    p = write_startup_path_inventory(tmp_path)
    assert p.is_file()
    text = p.read_text(encoding="utf-8")
    assert "capture_spawn_online" in text
    assert "paper_bat_pilot" in text


def test_clock_windows_matrix():
    m = {r["hhmm"]: r for r in [
        expected_state_for_clock(x) for x in (
            "0030", "0750", "0859", "0900", "0910", "1129", "1130",
            "1229", "1230", "1430", "1500", "1535",
        )
    ]}
    assert m["0750"]["paper_expected"] == "PAPER_WAIT_OR_WARMUP_EXPECTED"
    assert m["0750"]["entry_evaluation_possible"] is False
    assert m["0750"]["recovery_same_day_seal_required"] is False
    assert m["0910"]["entry_evaluation_possible"] is True
    assert m["1535"]["capture_should_continue"] is False
    assert m["1535"]["paper_expected"] == "PAPER_AFTER_SESSION_END_IMMEDIATE_EXIT"


def test_exposure_gate_reachable():
    out = prove_exposure_gate_reachable_under_injected_clock()
    assert out["exposure_gate_reachable"] is True
    from research.exposure_gate import ExposureGate, ExposureGateConfig

    d = ExposureGate(ExposureGateConfig()).evaluate_entry(
        {"profile": "small_paper", "symbol": "7203", "entry_price": 1000.0, "entry_time": "2026-07-14T09:10:00+09:00"},
        max_concurrent_positions=3,
    )
    assert d is not None
    assert hasattr(d, "accept")


def test_paper_bat_cmdline_escaping_windows():
    """Regression: paper bat must use shell string, not list2cmdline-escaped cmd /c."""
    import inspect
    import subprocess

    from small_paper import paper_trade_checked_runner as m

    src = inspect.getsource(m.PaperTradeCheckedRunner.step_start_paper)
    assert 'call \\"' not in src
    assert 'echo.| call "' in src
    # Must be a str assignment (shell=True path), not a ["cmd","/c", ...] list.
    assert 'cmdline = f\'echo.| call "' in src or 'cmdline = f"echo.| call' in src
    assert '["cmd", "/c"' not in src and "['cmd', '/c'" not in src
    # Prove list form would still be broken on Windows:
    bat = r"C:\Users\yhach\Documents\tradebotfile\run_paper_trade.bat"
    broken = subprocess.list2cmdline(
        ["cmd", "/c", f'echo.| call "{bat}" & exit /b %ERRORLEVEL%']
    )
    assert '\\"' in broken or '""' in broken


def test_daytrade_safety_uses_am_prebuild_cache_key():
    """Regression: safety must probe AM prebuild key, not ephemeral now-stamp."""
    import inspect

    from small_paper import safety as m

    src = inspect.getsource(m.check_daytrade_suitability_trial_config)
    assert "build_run_session_key" in src
    assert 'session="AM"' in src or "session='AM'" in src


def test_stale_flag_archived_before_spawn(tmp_path: Path):
    day = capture_day_dir(tmp_path, "20260714")
    day.mkdir(parents=True, exist_ok=True)
    (day / OPERATOR_STOP).write_text(
        "stop\nrequested_at=2026-07-14T01:00:00+09:00\n",
        encoding="utf-8",
    )
    prep = prepare_day_dir_operator_stop_for_spawn(
        day, spawn_started_at=datetime(2026, 7, 14, 7, 50, tzinfo=JST)
    )
    assert prep["action"].startswith("archived")
    assert not (day / OPERATOR_STOP).is_file()


def test_residual_scan_classifies_operator_stop(tmp_path: Path):
    day = tmp_path / "data" / "market_capture" / "20260714"
    day.mkdir(parents=True)
    (day / OPERATOR_STOP).write_text("stop\nrequested_at=2026-07-14T01:00:00+09:00\n", encoding="utf-8")
    items = scan_residuals(tmp_path, trading_date="20260714")
    kinds = {i.kind for i in items}
    assert "operator_stop" in kinds
    assert any(i.classification == "must_archive" for i in items if i.kind == "operator_stop")


def test_recovery_prestart_no_same_day_seal(tmp_path: Path):
    design = (
        tmp_path
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    design.parent.mkdir(parents=True, exist_ok=True)
    design.write_text(json.dumps({"pass": True}), encoding="utf-8")
    cfg = tmp_path / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("live_trading_enabled: false\norder_enabled: false\n", encoding="utf-8")
    from small_paper.operational_recovery import config_sha256

    (cfg.parent / "production_config_sha256.pin").write_text(config_sha256(cfg) + "\n", encoding="utf-8")
    r = probe_workspace_recovery(tmp_path, trading_date="20260714", config_path=cfg)
    assert r["same_day_seal_required"] is False
    assert "SESSION_SEAL_INVALID" not in {b["code"] for b in r["blockers"]}


def test_malformed_stop_parse():
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as td:
        p = Path(td) / OPERATOR_STOP
        p.write_text("requested_at=NOT_A_DATE\n", encoding="utf-8")
        parsed = parse_operator_stop_flag(p)
        assert parsed["malformed"] is True
