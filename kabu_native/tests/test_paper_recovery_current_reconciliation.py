"""Current readonly re-evaluation of historical broker-only recovery residue.

Historical BROKER_ONLY rows stay on disk. A later Paper start may proceed only
when a current readonly book shows those symbols and order ids are no longer
active. Unreadable broker state stays blocked. Local-only residue stays blocked
without a broker re-query.
"""

from __future__ import annotations

import json
from pathlib import Path

from small_paper.operational_recovery import (
    acquire_published_readonly_token,
    compare_historical_broker_only_to_current,
    historical_broker_only_items,
    kabu_order_is_active,
    probe_workspace_recovery,
)


def _design(native: Path) -> None:
    p = (
        native
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"pass": True, "mismatch_count": 0}), encoding="utf-8")


def _pin(native: Path) -> Path:
    cfg = native / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("live_trading_enabled: false\norder_enabled: false\n", encoding="utf-8")
    from small_paper.operational_recovery import config_sha256

    (cfg.parent / "production_config_sha256.pin").write_text(config_sha256(cfg) + "\n", encoding="utf-8")
    return cfg


def _prior(native: Path, rows: list[dict], *, mismatch: int | None = None) -> Path:
    root = native / "results" / "small_paper" / "20260910" / "live_session_122536"
    safety = root / "live_order_safety"
    safety.mkdir(parents=True, exist_ok=True)
    man = {
        "session_id": "live_session_122536",
        "trading_day": "20260910",
        "started_at": "2026-09-10T12:25:40+09:00",
        "ended_at": "2026-09-10T15:23:01+09:00",
        "production_approval_status": "NOT_AUTHORIZED",
        "live_trading_enabled": False,
        "order_enabled": False,
        "reconciliation_status": "OK",
        "reconciliation_mismatch": len(rows) if mismatch is None else mismatch,
        "submit_count": 0,
        "cancel_count": 0,
        "kill_switch_events": 0,
        "sealed": True,
        "synthetic": False,
        "session_provenance": "LIVE_PAPER_RUNTIME",
        "config_sha256": "abc",
        "git_commit": "deadbeef",
    }
    (safety / "session_manifest.json").write_text(json.dumps(man), encoding="utf-8")
    text = "".join(json.dumps(r) + "\n" for r in rows)
    (safety / "broker_reconciliation.jsonl").write_text(text, encoding="utf-8")
    seal = {
        "session_seal_status": "SEALED_VALID",
        "entry_count": 14,
        "required_count": 14,
        "required_artifact_missing_count": 0,
        "session_id": "live_session_122536",
    }
    (root / "session_seal.json").write_text(json.dumps(seal), encoding="utf-8")
    return safety


def _rows() -> list[dict]:
    return [
        {"type": "BROKER_ONLY_POSITION", "symbol": "5801.T", "broker": 100},
        {"type": "BROKER_ONLY_ORDER", "symbol": "5801.T", "broker_order_id": "OID-A"},
    ]


def test_terminal_kabu_order_is_not_active():
    assert kabu_order_is_active({"State": "5", "OrderQty": 100, "CumQty": 0}) is False
    assert kabu_order_is_active({"State": "3", "OrderQty": 100, "CumQty": 100}) is False
    assert kabu_order_is_active({"State": "1", "OrderQty": 100, "CumQty": 0}) is True
    assert kabu_order_is_active({"State": "", "OrderQty": 100}) is True


def test_historical_mismatch_current_clean_is_ready(tmp_path: Path):
    _design(tmp_path)
    cfg = _pin(tmp_path)
    safety = _prior(tmp_path, _rows())
    journal = (safety / "broker_reconciliation.jsonl").read_text(encoding="utf-8")
    result = probe_workspace_recovery(
        tmp_path,
        trading_date="20260928",
        config_path=cfg,
        current_book_reader=lambda: {
            "readable": True,
            "positions": {},
            "active_order_ids": [],
            "submit_calls": 0,
            "cancel_calls": 0,
            "live_order_calls": 0,
        },
    )
    assert result["recovery_ready"] is True
    assert result["exit_code"] == 0
    decision = result["artifact_trace"]["current_readonly_reconciliation"]
    assert decision["result"] == "CURRENT_CLEAN"
    assert (safety / "broker_reconciliation.jsonl").read_text(encoding="utf-8") == journal


def test_historical_mismatch_same_position_blocks(tmp_path: Path):
    _design(tmp_path)
    cfg = _pin(tmp_path)
    _prior(tmp_path, _rows())
    result = probe_workspace_recovery(
        tmp_path,
        trading_date="20260928",
        config_path=cfg,
        current_book_reader=lambda: {
            "readable": True,
            "positions": {"5801.T": 100},
            "active_order_ids": [],
            "submit_calls": 0,
            "cancel_calls": 0,
            "live_order_calls": 0,
        },
    )
    assert result["recovery_ready"] is False
    assert result["exit_code"] == 2
    assert result["artifact_trace"]["current_readonly_reconciliation"]["result"] == "CURRENT_UNRESOLVED"


def test_historical_mismatch_active_order_blocks(tmp_path: Path):
    _design(tmp_path)
    cfg = _pin(tmp_path)
    _prior(tmp_path, _rows())
    result = probe_workspace_recovery(
        tmp_path,
        trading_date="20260928",
        config_path=cfg,
        current_book_reader=lambda: {
            "readable": True,
            "positions": {},
            "active_order_ids": ["OID-A"],
            "submit_calls": 0,
            "cancel_calls": 0,
            "live_order_calls": 0,
        },
    )
    assert result["exit_code"] == 2
    assert result["artifact_trace"]["current_readonly_reconciliation"]["active_historical_order_n"] == 1


def test_unreadable_broker_blocks(tmp_path: Path):
    _design(tmp_path)
    cfg = _pin(tmp_path)
    _prior(tmp_path, _rows())
    result = probe_workspace_recovery(
        tmp_path,
        trading_date="20260928",
        config_path=cfg,
        current_book_reader=lambda: {
            "readable": False,
            "error": "TokenUnavailable",
            "submit_calls": 0,
            "cancel_calls": 0,
            "live_order_calls": 0,
        },
    )
    assert result["exit_code"] == 2
    assert result["artifact_trace"]["current_readonly_reconciliation"]["result"] == "CURRENT_UNREADABLE"


def test_no_prior_session_unchanged(tmp_path: Path):
    _design(tmp_path)
    cfg = _pin(tmp_path)
    called = {"n": 0}

    def _reader():
        called["n"] += 1
        return {"readable": True, "positions": {}, "active_order_ids": []}

    result = probe_workspace_recovery(
        tmp_path,
        trading_date="20260928",
        config_path=cfg,
        current_book_reader=_reader,
    )
    assert result["probe_mode"] == "pre_start_no_prior_session"
    assert result["exit_code"] == 0
    assert called["n"] == 0


def test_local_only_does_not_use_current_reader(tmp_path: Path):
    _design(tmp_path)
    cfg = _pin(tmp_path)
    _prior(tmp_path, [{"type": "LOCAL_ONLY_POSITION", "symbol": "6758.T", "local": 100}])
    called = {"n": 0}

    def _reader():
        called["n"] += 1
        return {"readable": True, "positions": {}, "active_order_ids": []}

    result = probe_workspace_recovery(
        tmp_path,
        trading_date="20260928",
        config_path=cfg,
        current_book_reader=_reader,
    )
    assert result["exit_code"] == 2
    assert called["n"] == 0


def test_waits_for_published_ingress_token_and_rejects_issue():
    calls = {"n": 0}

    def _acquire(**_kwargs):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("TokenUnavailable")
        return {"token": "published", "issued": False}

    token, err, waited = acquire_published_readonly_token(
        native_root=Path("."),
        trading_date="20260928",
        wait=True,
        wait_sec=2.0,
        poll_sec=0.01,
        acquire=_acquire,
        sleeper=lambda _s: None,
    )
    assert token == "published"
    assert err == ""
    assert waited is True
    assert calls["n"] == 3

    def _issue(**_kwargs):
        return {"token": "x", "issued": True}

    token, err, _waited = acquire_published_readonly_token(
        native_root=Path("."),
        trading_date="20260928",
        wait=False,
        acquire=_issue,
    )
    assert token == ""
    assert err == "token_issue_forbidden"


def test_standalone_recovery_does_not_poll_without_ingress():
    calls = {"n": 0}

    def _acquire(**_kwargs):
        calls["n"] += 1
        raise RuntimeError("TokenUnavailable")

    token, err, waited = acquire_published_readonly_token(
        native_root=Path("."),
        trading_date="20260928",
        wait=False,
        acquire=_acquire,
    )
    assert token == ""
    assert err == "RuntimeError"
    assert waited is False
    assert calls["n"] == 1


def test_compare_clean_and_items_roundtrip(tmp_path: Path):
    safety = _prior(tmp_path, _rows())
    items = historical_broker_only_items(safety)
    assert items["order_ids"] == ["OID-A"]
    clean = compare_historical_broker_only_to_current(
        items,
        {"readable": True, "positions": {}, "active_order_ids": []},
    )
    assert clean["result"] == "CURRENT_CLEAN"
