"""Phase687W17 — historical distinctions; updated for W18 probe completion.

W18 replaced hardcoded fail-closed probe with real artifact evaluation.
These tests keep non-regression checks that remain valid.
"""

from __future__ import annotations

import json
from pathlib import Path

from small_paper.capture_child_cleanup import should_stop_on_shutdown
from small_paper.market_capture_sidecar import OPERATOR_STOP, MarketCaptureSidecar
from small_paper.operational_recovery import (
    RecoveryReadinessEvidence,
    dryrun_ready_evidence,
    evaluate_recovery_readiness,
    probe_workspace_recovery,
)


def test_pre_start_probe_no_hardcoded_workspace_fail_closed(tmp_path: Path):
    design = (
        tmp_path
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    design.parent.mkdir(parents=True, exist_ok=True)
    design.write_text(json.dumps({"pass": True, "mismatch_count": 0}), encoding="utf-8")
    cfg = tmp_path / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("live_trading_enabled: false\norder_enabled: false\n", encoding="utf-8")
    from small_paper.operational_recovery import config_sha256

    (cfg.parent / "production_config_sha256.pin").write_text(config_sha256(cfg) + "\n", encoding="utf-8")
    result = probe_workspace_recovery(tmp_path, trading_date="20260713", config_path=cfg)
    assert result["probe_mode"] == "pre_start_no_prior_session"
    assert result["same_day_seal_required"] is False
    assert "artifact_trace" in result
    codes = {b["code"] for b in result["blockers"]}
    # Must NOT invent SESSION_* invalid without a prior session
    assert "SESSION_MANIFEST_INVALID" not in codes
    assert "SESSION_SEAL_INVALID" not in codes
    assert "RECONCILIATION_ISSUE" not in codes


def test_demo_ready_exit_0_independent_of_capture_events():
    result = evaluate_recovery_readiness(dryrun_ready_evidence())
    assert result["exit_code"] == 0
    assert result["recovery_ready"] is True
    assert result["blockers"] == []


def test_capture_event_count_not_in_recovery_evidence():
    fields = set(RecoveryReadinessEvidence.__dataclass_fields__.keys())
    assert "event_count" not in fields
    assert "capture_status" not in fields


def test_paper_block_continue_policy_live():
    stop, why = should_stop_on_shutdown(
        reason="normal_exit",
        paper_blocked_capture_continues=True,
        synthetic=False,
        skip_capture_wait=False,
    )
    assert stop is False
    assert why == "paper_blocked_capture_continues"


def test_operator_stop_causes_should_stop_when_fresh(tmp_path: Path):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    from small_paper.capture_child_cleanup import request_graceful_stop

    JST = ZoneInfo("Asia/Tokyo")
    day = tmp_path / "data" / "market_capture" / "20990101"
    day.mkdir(parents=True)
    sc = MarketCaptureSidecar(
        native_root=tmp_path,
        trading_date="20990101",
        synthetic=True,
        synthetic_events=0,
        operator_stop_check=True,
    )
    sc.out_dir = day
    sc.process_started_at = datetime.now(JST) - timedelta(seconds=5)
    request_graceful_stop(day, reason="test")
    assert sc._should_stop() is True


def test_stale_operator_stop_ignored(tmp_path: Path):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    JST = ZoneInfo("Asia/Tokyo")
    day = tmp_path / "data" / "market_capture" / "20990102"
    day.mkdir(parents=True)
    old = (datetime.now(JST) - timedelta(hours=3)).isoformat(timespec="seconds")
    (day / OPERATOR_STOP).write_text(f"stop\nrequested_at={old}\n", encoding="utf-8")
    sc = MarketCaptureSidecar(
        native_root=tmp_path,
        trading_date="20990102",
        synthetic=True,
        synthetic_events=0,
        operator_stop_check=True,
    )
    sc.out_dir = day
    sc.process_started_at = datetime.now(JST)
    assert sc._should_stop() is False
