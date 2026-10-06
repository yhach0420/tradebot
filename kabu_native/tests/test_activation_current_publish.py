"""Activation publish requires a matching pre-paper certification."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from runner.am_pm_daily_runner import DailyRunnerOptions, DailyRunnerState, pilot_command_argv
from small_paper.activation_current_publish import (
    REASON_ACTIVATION_ID,
    REASON_ACTIVATION_SHA,
    REASON_CANDIDATE,
    REASON_EXECUTION_FAMILY,
    REASON_FAIL,
    REASON_INVENTORY,
    REASON_MISSING,
    REASON_ORDER_CONTRACT,
    REASON_SESSION_EXECUTOR,
    REASON_STALE,
    assess_pre_paper_certification,
    publish_current_activation,
)
from small_paper.paper_full_day_certification import (
    CURRENT_CERT_PASS,
    evaluate_activation_scoped_certification,
)
from small_paper.paper_primary_activation import (
    ACTIVATION_V14_ID,
    ACTIVATION_V15_ID,
    COMPLETE_STRATEGY_SHA,
    ENTRY_SHA,
    EXIT_SHA,
)
from small_paper.v1r_activation_binding import OUT

NEW_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_TEST"
OLD_ID = "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_OLD"
SHA = "a" * 64
PARENT_SHA = "b" * 64
OTHER_SHA = "c" * 64
DIGEST = "d" * 64


def _manifest(**overrides: object) -> dict:
    body = {
        "activation_id": NEW_ID,
        "sha256": SHA,
        "parent_activation_sha": PARENT_SHA,
        "candidate_name": "EVENT_TIME_IMPULSE_COMPLETE_STRATEGY_FIXED_ENTRY_SUPPORT_CANDIDATE_V1",
        "execution_family": "X1_IMMEDIATE_ASK",
        "session_executor": "FixedSupportX1SessionExecutor",
        "runtime_inventory_digest": DIGEST,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "submit_cancel_live": "0/0/0",
        "order_enabled": False,
        "live_trading_enabled": False,
    }
    body.update(overrides)
    return body


def _cert(**overrides: object) -> dict:
    body = {
        "verdict": CURRENT_CERT_PASS,
        "failed_tests": [],
        "activation_id": NEW_ID,
        "activation_sha": SHA,
        "candidate_name": "EVENT_TIME_IMPULSE_COMPLETE_STRATEGY_FIXED_ENTRY_SUPPORT_CANDIDATE_V1",
        "execution_family": "X1_IMMEDIATE_ASK",
        "session_executor": "FixedSupportX1SessionExecutor",
        "inventory_digest": DIGEST,
        "submit_cancel_live": "0/0/0",
    }
    body.update(overrides)
    return body


def _write_old_selector(directory: Path) -> Path:
    path = directory / "active_v1r_activation.json"
    path.write_text(
        json.dumps({"activation_id": OLD_ID, "activation_sha": PARENT_SHA}) + "\n",
        encoding="utf-8",
    )
    return path


def _stage(tmp_path: Path, *, cert: dict | None) -> tuple[Path, Path, Path]:
    manifest_path = tmp_path / f"{NEW_ID}.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    selector = _write_old_selector(tmp_path)
    cert_dir = tmp_path / "certs"
    if cert is not None:
        dest = cert_dir / "by_activation" / f"{NEW_ID}.json"
        dest.parent.mkdir(parents=True)
        dest.write_text(json.dumps(cert), encoding="utf-8")
    return manifest_path, selector, cert_dir


def _publish(tmp_path: Path, *, cert: dict | None) -> tuple[dict, Path]:
    manifest_path, selector, cert_dir = _stage(tmp_path, cert=cert)
    before = selector.read_bytes()
    result = publish_current_activation(
        manifest_path=manifest_path,
        selector_path=selector,
        cert_dir=cert_dir,
    )
    if not result["ok"]:
        assert selector.read_bytes() == before
        assert json.loads(selector.read_text(encoding="utf-8"))["activation_id"] == OLD_ID
    return result, selector


def test_missing_cert_does_not_publish(tmp_path: Path) -> None:
    result, _selector = _publish(tmp_path, cert=None)
    assert result["reason"] == REASON_MISSING
    assert result["published"] is False


def test_fail_cert_does_not_publish(tmp_path: Path) -> None:
    result, _selector = _publish(tmp_path, cert=_cert(verdict="FIXED_ENTRY_SUPPORT_RUNTIME_PRE_PAPER_CERTIFICATION_FAIL_V1", failed_tests=["DEMO_X1_LEDGER"]))
    assert result["reason"] == REASON_FAIL


def test_activation_id_mismatch_does_not_publish(tmp_path: Path) -> None:
    result, _selector = _publish(tmp_path, cert=_cert(activation_id="OTHER_ACTIVATION"))
    assert result["reason"] == REASON_ACTIVATION_ID


def test_activation_sha_mismatch_does_not_publish(tmp_path: Path) -> None:
    result, _selector = _publish(tmp_path, cert=_cert(activation_sha=OTHER_SHA))
    assert result["reason"] == REASON_ACTIVATION_SHA


def test_stale_parent_cert_does_not_publish(tmp_path: Path) -> None:
    result, _selector = _publish(tmp_path, cert=_cert(activation_sha=PARENT_SHA))
    assert result["reason"] == REASON_STALE


def test_candidate_mismatch_does_not_publish(tmp_path: Path) -> None:
    result, _selector = _publish(tmp_path, cert=_cert(candidate_name="OTHER_CANDIDATE"))
    assert result["reason"] == REASON_CANDIDATE


def test_execution_family_mismatch_does_not_publish(tmp_path: Path) -> None:
    result, _selector = _publish(tmp_path, cert=_cert(execution_family="OTHER_FAMILY"))
    assert result["reason"] == REASON_EXECUTION_FAMILY


def test_session_executor_mismatch_does_not_publish(tmp_path: Path) -> None:
    result, _selector = _publish(tmp_path, cert=_cert(session_executor="OtherExecutor"))
    assert result["reason"] == REASON_SESSION_EXECUTOR


def test_inventory_mismatch_does_not_publish(tmp_path: Path) -> None:
    result, _selector = _publish(tmp_path, cert=_cert(inventory_digest="e" * 64))
    assert result["reason"] == REASON_INVENTORY


def test_valid_cert_publishes_and_keeps_order_contract(tmp_path: Path) -> None:
    result, selector = _publish(tmp_path, cert=_cert())
    assert result["ok"] is True
    assert result["submit_cancel_live"] == "0/0/0"
    body = json.loads(selector.read_text(encoding="utf-8"))
    assert body["activation_id"] == NEW_ID
    assert body["activation_sha"] == SHA
    manifest = json.loads((tmp_path / f"{NEW_ID}.json").read_text(encoding="utf-8"))
    assert manifest["submit"] == 0 and manifest["cancel"] == 0 and manifest["live"] == 0


def test_order_contract_violation_does_not_publish(tmp_path: Path) -> None:
    manifest_path, selector, cert_dir = _stage(tmp_path, cert=_cert())
    manifest = _manifest(submit=1, submit_cancel_live="1/0/0")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    before = selector.read_bytes()
    result = publish_current_activation(
        manifest_path=manifest_path,
        selector_path=selector,
        cert_dir=cert_dir,
    )
    assert result["reason"] == REASON_ORDER_CONTRACT
    assert selector.read_bytes() == before


def test_published_activation_passes_checked_runner_gate(tmp_path: Path) -> None:
    result, _selector = _publish(tmp_path, cert=_cert())
    assert result["ok"] is True
    current = {
        "activation_id": NEW_ID,
        "activation_sha": SHA,
        "candidate_name": "EVENT_TIME_IMPULSE_COMPLETE_STRATEGY_FIXED_ENTRY_SUPPORT_CANDIDATE_V1",
        "execution_family": "X1_IMMEDIATE_ASK",
        "session_executor": "FixedSupportX1SessionExecutor",
        "inventory_digest": DIGEST,
    }
    assert evaluate_activation_scoped_certification(
        cert_dir=tmp_path / "certs",
        current=current,
    ) == 0


def test_gate_still_fail_closes_missing_and_mismatch(tmp_path: Path) -> None:
    current = {
        "activation_id": NEW_ID,
        "activation_sha": SHA,
        "candidate_name": "EVENT_TIME_IMPULSE_COMPLETE_STRATEGY_FIXED_ENTRY_SUPPORT_CANDIDATE_V1",
        "execution_family": "X1_IMMEDIATE_ASK",
        "session_executor": "FixedSupportX1SessionExecutor",
        "inventory_digest": DIGEST,
    }
    assert evaluate_activation_scoped_certification(cert_dir=tmp_path / "empty", current=current) == 2
    cert_dir = tmp_path / "certs"
    dest = cert_dir / "by_activation" / f"{NEW_ID}.json"
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps(_cert(activation_sha=OTHER_SHA)), encoding="utf-8")
    assert evaluate_activation_scoped_certification(cert_dir=cert_dir, current=current) == 2
    dest.write_text(json.dumps(_cert(inventory_digest="e" * 64)), encoding="utf-8")
    assert evaluate_activation_scoped_certification(cert_dir=cert_dir, current=current) == 2


def test_morning_missing_cert_is_blocked_before_paper(tmp_path: Path) -> None:
    """The 20261001 gate path: current activation with no cert exits 2 and does not publish."""
    manifest_path, selector, cert_dir = _stage(tmp_path, cert=None)
    current = {
        "activation_id": NEW_ID,
        "activation_sha": SHA,
        "candidate_name": "EVENT_TIME_IMPULSE_COMPLETE_STRATEGY_FIXED_ENTRY_SUPPORT_CANDIDATE_V1",
        "execution_family": "X1_IMMEDIATE_ASK",
        "session_executor": "FixedSupportX1SessionExecutor",
        "inventory_digest": DIGEST,
    }
    assert evaluate_activation_scoped_certification(cert_dir=cert_dir, current=current) == 2
    before = selector.read_bytes()
    refused = publish_current_activation(
        manifest_path=manifest_path,
        selector_path=selector,
        cert_dir=cert_dir,
    )
    assert refused["reason"] == REASON_MISSING
    assert selector.read_bytes() == before
    _stage_cert = cert_dir / "by_activation" / f"{NEW_ID}.json"
    _stage_cert.parent.mkdir(parents=True, exist_ok=True)
    _stage_cert.write_text(json.dumps(_cert()), encoding="utf-8")
    published = publish_current_activation(
        manifest_path=manifest_path,
        selector_path=selector,
        cert_dir=cert_dir,
    )
    assert published["ok"] is True
    assert evaluate_activation_scoped_certification(cert_dir=cert_dir, current=current) == 0


def test_offline_paper_runner_argv_is_dry_run(tmp_path: Path) -> None:
    state = DailyRunnerState(
        options=DailyRunnerOptions(day_stamp="20261001", enable_intraday_refresh=False),
        repo_root=tmp_path,
        native_root=tmp_path / "kabu_native",
        reports_dir=tmp_path / "reports",
        push_root=tmp_path / "push",
        trade_date=date(2026, 10, 1),
    )
    argv = pilot_command_argv(state, session="am", universe_rel="universe.csv")
    assert "--dry-run" in argv
    assert "--source" in argv and argv[argv.index("--source") + 1] == "live"
    assert "--output-date" in argv and argv[argv.index("--output-date") + 1] == "20261001"
    assert "--wait-until-session" in argv
    assert not any(token in argv for token in ("--live-order", "--submit", "--enable-live"))


def test_strategy_identity_constants_unchanged() -> None:
    assert ENTRY_SHA == "c93c69f0edf84f5ec03b145ee17e25ef5bc2c4ecaa08cfb5e1c6fca7ec052ba9"
    assert EXIT_SHA == "8f8ddb3d47190b96df84eb83d9e5f694bfd034ebc650a8a86a85822b5bcb8175"
    assert COMPLETE_STRATEGY_SHA == "3002fdada09206568dd6de5e43f6e2c3317a55e40df2d66cdefef317a7c0fd0a"
    assert ACTIVATION_V14_ID == "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V14"
    assert ACTIVATION_V15_ID == "FIXED_ENTRY_SUPPORT_PAPER_PRIMARY_ACTIVATION_V15"


def test_sealed_v14_pair_matches_without_republish() -> None:
    manifest = json.loads((OUT / f"{ACTIVATION_V14_ID}.json").read_text(encoding="utf-8"))
    cert_path = (
        OUT.parents[0]
        / "paper_runtime_full_day_certification"
        / "by_activation"
        / f"{ACTIVATION_V14_ID}.json"
    )
    cert = json.loads(cert_path.read_text(encoding="utf-8"))
    assert assess_pre_paper_certification(manifest, cert) == ""
    assert manifest["entry_sha"] == ENTRY_SHA
    assert manifest["exit_sha"] == EXIT_SHA
    assert manifest["complete_strategy_sha"] == COMPLETE_STRATEGY_SHA
    assert manifest["submit_cancel_live"] == "0/0/0"
