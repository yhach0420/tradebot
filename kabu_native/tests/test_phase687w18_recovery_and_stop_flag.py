"""Phase687W18 — Recovery probe completion + stale operator_stop isolation."""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from small_paper.capture_child_cleanup import (
    classify_operator_stop_for_process,
    cleanup_owned_capture,
    parse_operator_stop_flag,
    prepare_day_dir_operator_stop_for_spawn,
    query_process,
    record_owned_from_spawn,
    request_graceful_stop,
)
from small_paper.market_capture_registration import coordinate_registration
from small_paper.market_capture_sidecar import (
    OPERATOR_STOP,
    MarketCaptureSidecar,
    capture_day_dir,
    spawn_sidecar_process,
    wait_capture_online,
)
from small_paper.operational_recovery import (
    evaluate_config_sha_match,
    evaluate_prior_session_artifacts,
    evaluate_recovery_readiness,
    probe_workspace_recovery,
    dryrun_ready_evidence,
)
from small_paper.paper_trade_checked_runner import PaperTradeCheckedRunner

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
CFG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"


def _design(native: Path, *, ok: bool = True) -> None:
    p = (
        native
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"pass": ok, "mismatch_count": 0 if ok else 1}), encoding="utf-8")


def _write_prior_session(
    native: Path,
    *,
    day: str = "20260710",
    session_id: str = "PRIOR1",
    recon: str = "OK",
    mismatch: int = 0,
    seal_status: str = "SEALED_VALID",
    seal_entries: int = 14,
    missing_required: int = 0,
    omit_manifest: bool = False,
    omit_seal: bool = False,
    corrupt_seal: bool = False,
) -> dict[str, Path]:
    root = native / "results" / "small_paper" / day / f"live_session_{session_id}"
    safety = root / "live_order_safety"
    safety.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {"root": root, "safety": safety}
    if not omit_manifest:
        man = {
            "session_id": session_id,
            "trading_day": day,
            "started_at": f"{day[:4]}-{day[4:6]}-{day[6:]}T09:00:00+09:00",
            "ended_at": f"{day[:4]}-{day[4:6]}-{day[6:]}T15:30:00+09:00",
            "live_trading_enabled": False,
            "order_enabled": False,
            "production_approval_status": "NOT_AUTHORIZED",
            "reconciliation_status": recon,
            "reconciliation_mismatch": mismatch,
            "kill_switch_events": 0,
            "config_sha256": "abc",
            "git_commit": "deadbeef",
            "sealed": True,
            "session_seal_status": seal_status,
        }
        paths["manifest"] = safety / "session_manifest.json"
        paths["manifest"].write_text(json.dumps(man, indent=2), encoding="utf-8")
    if not omit_seal:
        seal_path = root / "session_seal.json"
        if corrupt_seal:
            seal_path.write_text("{not-json", encoding="utf-8")
        else:
            seal = {
                "session_seal_status": seal_status,
                "entry_count": seal_entries,
                "required_count": seal_entries,
                "required_artifact_missing_count": missing_required,
                "session_id": session_id,
                "trading_date": day,
                "entries": [{"relative_path": "x", "sha256": "0" * 64}],
            }
            seal_path.write_text(json.dumps(seal, indent=2), encoding="utf-8")
        paths["seal"] = seal_path
    return paths


def _pin_config(native: Path, *, match: bool = True) -> Path:
    cfg = native / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("live_trading_enabled: false\norder_enabled: false\n", encoding="utf-8")
    from small_paper.operational_recovery import config_sha256

    dig = config_sha256(cfg)
    pin = cfg.parent / "production_config_sha256.pin"
    pin.write_text((dig if match else "0" * 64) + "\n", encoding="utf-8")
    return cfg


def test_recovery_valid_artifacts_pass(tmp_path: Path):
    _design(tmp_path, ok=True)
    cfg = _pin_config(tmp_path, match=True)
    _write_prior_session(tmp_path)
    result = probe_workspace_recovery(tmp_path, trading_date="20260713", config_path=cfg)
    assert result["probe_mode"] == "pre_start_prior_session"
    assert result["same_day_seal_required"] is False
    assert result["session_manifest_valid"] is True
    assert result["session_seal_valid"] is True
    assert result["reconciliation_state"] in ("OK", "PASS", "CLEAN")
    assert result.get("artifact_trace", {}).get("config_sha", {}).get("match") is True
    assert result.get("config_sha_match") is True
    codes = {b["code"] for b in result["blockers"]}
    assert "SESSION_MANIFEST_INVALID" not in codes
    assert "SESSION_SEAL_INVALID" not in codes
    assert "RECONCILIATION_ISSUE" not in codes
    assert "DESIGN_CONFIG_MISMATCH" not in codes
    # HARD_FAIL probe may be env-dependent
    if "SUBMIT_HARD_FAIL_MISSING" in codes:
        pytest.skip("KabuBrokerAdapter HARD_FAIL unavailable in this environment")
    assert result["exit_code"] == 0
    assert result["recovery_ready"] is True


def test_recovery_manifest_missing_blocks(tmp_path: Path):
    _design(tmp_path, ok=True)
    cfg = _pin_config(tmp_path, match=True)
    paths = _write_prior_session(tmp_path, omit_manifest=True)
    # seal alone still discovers? discover looks for session_manifest — without it, no prior
    # Create empty live_order_safety so we force prior with missing manifest via direct eval
    session = {
        "manifest_path": str(paths["safety"] / "session_manifest.json"),
        "seal_path": str(paths["root"] / "session_seal.json"),
        "safety_dir": str(paths["safety"]),
        "session_root": str(paths["root"]),
    }
    ev = evaluate_prior_session_artifacts(session)
    assert ev["session_manifest_valid"] is False
    assert ev["detail"]["manifest_status"] == "missing"


def test_recovery_seal_invalid_blocks(tmp_path: Path):
    _design(tmp_path, ok=True)
    cfg = _pin_config(tmp_path, match=True)
    _write_prior_session(tmp_path, seal_status="INVALID", seal_entries=0, missing_required=3)
    result = probe_workspace_recovery(tmp_path, trading_date="20260713", config_path=cfg)
    assert result["session_seal_valid"] is False
    assert any(b["code"] == "SESSION_SEAL_INVALID" for b in result["blockers"])
    assert result["exit_code"] == 2


def test_recovery_reconciliation_ng_blocks(tmp_path: Path):
    _design(tmp_path, ok=True)
    cfg = _pin_config(tmp_path, match=True)
    _write_prior_session(tmp_path, recon="FAIL", mismatch=2)
    result = probe_workspace_recovery(tmp_path, trading_date="20260713", config_path=cfg)
    assert any(b["code"] == "RECONCILIATION_ISSUE" for b in result["blockers"])
    assert result["exit_code"] == 2


def test_recovery_config_sha_mismatch_blocks(tmp_path: Path):
    _design(tmp_path, ok=True)
    cfg = _pin_config(tmp_path, match=False)
    _write_prior_session(tmp_path)
    result = probe_workspace_recovery(tmp_path, trading_date="20260713", config_path=cfg)
    assert result["config_sha_match"] is False
    assert any(b["code"] == "DESIGN_CONFIG_MISMATCH" for b in result["blockers"])


def test_recovery_0750_no_same_day_seal_required(tmp_path: Path):
    _design(tmp_path, ok=True)
    cfg = _pin_config(tmp_path, match=True)
    # Same-day incomplete session must NOT be selected as prior
    today = "20260713"
    _write_prior_session(tmp_path, day=today, session_id="TODAY", omit_seal=True)
    result = probe_workspace_recovery(tmp_path, trading_date=today, config_path=cfg)
    assert result["same_day_seal_required"] is False
    assert result["probe_mode"] == "pre_start_no_prior_session"
    assert result["session_seal_valid"] is True  # N/A pre-start, not required
    codes = {b["code"] for b in result["blockers"]}
    assert "SESSION_SEAL_INVALID" not in codes


def test_recovery_no_hardcoded_false_unknown(tmp_path: Path):
    _design(tmp_path, ok=True)
    cfg = _pin_config(tmp_path, match=True)
    result = probe_workspace_recovery(tmp_path, trading_date="20260713", config_path=cfg)
    assert result["probe_mode"] != "workspace_fail_closed"
    # Evidence must come from artifact_trace, not silent hardcoded False without status
    assert "artifact_trace" in result
    assert result["artifact_trace"]["prior_eval"]["status"] == "not_applicable_pre_start"
    assert result["reconciliation_state"] == "OK"


def test_stale_flag_allows_new_capture(tmp_path: Path):
    day = "20990505"
    out = capture_day_dir(tmp_path, day)
    out.mkdir(parents=True, exist_ok=True)
    old = (datetime.now(JST) - timedelta(hours=7)).isoformat(timespec="seconds")
    (out / OPERATOR_STOP).write_text(f"stop\nrequested_at={old}\npid=1\n", encoding="utf-8")
    prep = prepare_day_dir_operator_stop_for_spawn(out, spawn_started_at=datetime.now(JST))
    assert prep["action"].startswith("archived")
    assert not (out / OPERATOR_STOP).is_file()
    coordinate_registration(
        tmp_path, day, expected_symbols=[str(7200 + i) for i in range(50)], apply_register=False, test_mode=True
    )
    spawn = spawn_sidecar_process(native_root=tmp_path, trading_date=day, synthetic=True, synthetic_events=25)
    owned = record_owned_from_spawn(spawn, native_root=tmp_path)
    try:
        wait = wait_capture_online(tmp_path, day, timeout_sec=20)
        assert wait["ok"] is True
        # still online briefly (not immediately sealed by stale flag)
        st = json.loads((out / "capture_status.json").read_text(encoding="utf-8"))
        assert st.get("capture_status") in ("CAPTURE_ONLINE", "CAPTURE_NO_MARKET_EVENTS", "CAPTURE_COMPLETE")
        assert st.get("final") is not True or st.get("capture_status") != "CAPTURE_ONLINE"
    finally:
        cleanup_owned_capture(owned, reason="test_teardown", skip_capture_wait=True)
        assert not query_process(owned.pid).get("exists")


def test_new_flag_stops_capture(tmp_path: Path):
    day = "20990506"
    coordinate_registration(
        tmp_path, day, expected_symbols=[str(7200 + i) for i in range(50)], apply_register=False, test_mode=True
    )
    spawn = spawn_sidecar_process(native_root=tmp_path, trading_date=day, synthetic=True, synthetic_events=80)
    owned = record_owned_from_spawn(spawn, native_root=tmp_path)
    try:
        assert wait_capture_online(tmp_path, day, timeout_sec=20)["ok"] is True
        out = capture_day_dir(tmp_path, day)
        request_graceful_stop(out, pid=owned.pid, reason="test_stop")
        deadline = time.time() + 20
        while time.time() < deadline and not (out / "capture_seal.json").is_file():
            time.sleep(0.2)
        assert (out / "capture_seal.json").is_file()
    finally:
        cleanup_owned_capture(owned, reason="test_teardown", skip_capture_wait=True)


def test_malformed_flag_fail_safe(tmp_path: Path):
    flag = tmp_path / OPERATOR_STOP
    flag.write_text("requested_at=NOT_A_DATE\ngarbage\n", encoding="utf-8")
    parsed = parse_operator_stop_flag(flag)
    assert parsed["malformed"] is True
    started = datetime.now(JST)
    cls = classify_operator_stop_for_process(flag, process_started_at=started)
    assert cls["action"] == "ignore"
    assert cls["classification"] == "malformed"


def test_foreign_live_flag_not_deleted(tmp_path: Path):
    day = tmp_path / "day"
    day.mkdir()
    # Use current process as "foreign live" with capture-like cmdline simulation via leave path:
    # prepare leaves when pid exists AND cmdline matches markers OR empty.
    # Our pytest pid cmdline won't match markers — so force leave by mocking is hard.
    # Instead: write flag with pid=os.getpid() and patch query to look like capture.
    from unittest.mock import patch

    flag = day / OPERATOR_STOP
    flag.write_text(
        f"stop\nrequested_at={(datetime.now(JST) - timedelta(hours=1)).isoformat(timespec='seconds')}\npid={os.getpid()}\n",
        encoding="utf-8",
    )
    with patch(
        "small_paper.capture_child_cleanup.query_process",
        return_value={
            "exists": True,
            "cmdline": "python -m small_paper.market_capture_sidecar --native-root X",
            "create_time": "x",
            "parent_pid": 1,
            "name": "python.exe",
        },
    ):
        prep = prepare_day_dir_operator_stop_for_spawn(day, spawn_started_at=datetime.now(JST))
    assert prep["action"] == "leave"
    assert prep["classification"] == "foreign_live"
    assert flag.is_file()


def test_ctrl_c_stops_owned_capture(tmp_path: Path):
    def run(cmd, env, cwd):
        s = cmd if isinstance(cmd, str) else " ".join(cmd)
        if "disk_guard_report" in s:
            return 0, '{"disk_state":"OK","disk_usage_pct":40.0}', ""
        return 0, "{}\n", ""

    design = (
        tmp_path
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    design.parent.mkdir(parents=True, exist_ok=True)
    design.write_text('{"pass":true,"mismatch_count":0}', encoding="utf-8")
    r = PaperTradeCheckedRunner(
        native_root=tmp_path,
        repo_root=REPO,
        run_command=run,
        capture_synthetic=True,
        skip_capture_wait=True,
        skip_w4s=True,
        config_path=CFG,
    )

    def boom():
        raise KeyboardInterrupt()

    r.step_cache_prebuild = boom  # type: ignore[method-assign]
    code = r.run()
    assert code == 130
    assert r._owned_capture is not None
    assert not query_process(r._owned_capture.pid).get("exists")


def test_sidecar_ignores_stale_flag_in_process(tmp_path: Path):
    day = tmp_path / "data" / "market_capture" / "20990507"
    day.mkdir(parents=True)
    old = (datetime.now(JST) - timedelta(hours=2)).isoformat(timespec="seconds")
    (day / OPERATOR_STOP).write_text(f"stop\nrequested_at={old}\n", encoding="utf-8")
    sc = MarketCaptureSidecar(native_root=tmp_path, trading_date="20990507", synthetic=True, synthetic_events=0)
    sc.out_dir = day
    sc.process_started_at = datetime.now(JST)
    assert sc._should_stop() is False


def test_synthetic_residual_zero_after_cleanup(tmp_path: Path):
    day = "20990508"
    coordinate_registration(
        tmp_path, day, expected_symbols=[str(7200 + i) for i in range(50)], apply_register=False, test_mode=True
    )
    spawn = spawn_sidecar_process(native_root=tmp_path, trading_date=day, synthetic=True, synthetic_events=20)
    owned = record_owned_from_spawn(spawn, native_root=tmp_path)
    wait_capture_online(tmp_path, day, timeout_sec=20)
    cleanup_owned_capture(owned, reason="test_teardown", skip_capture_wait=True)
    assert not query_process(owned.pid).get("exists")


def test_demo_ready_still_works():
    assert evaluate_recovery_readiness(dryrun_ready_evidence())["exit_code"] == 0


def test_config_sha_sources(tmp_path: Path):
    cfg = _pin_config(tmp_path, match=True)
    m = evaluate_config_sha_match(tmp_path, config_path=cfg)
    assert m["match"] is True
    assert m["status"] == "ok"
    assert m["disk_sha256"]
    assert m["pin_sha256"]
