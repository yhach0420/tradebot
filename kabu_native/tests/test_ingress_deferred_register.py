"""Deferred Kabu register: empty connect → later desired=50 must PUT once."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from small_paper.ingress_control_channel import write_desired_universe
from small_paper.market_capture_registration import notify_registration_refresh, read_registration_manifest
from small_paper.market_ingress_service import MarketIngressService, universe_symbol_hash


class _FakePush:
    def __init__(self, *, fail: bool = False, regist_override: int | None = None) -> None:
        self.calls: list[list[tuple[str, int]]] = []
        self.fail = fail
        self.regist_override = regist_override
        self.regist: list[tuple[str, int]] = []

    def register(self, symbols_spec: list[tuple[str, int]]) -> dict[str, Any]:
        self.calls.append(list(symbols_spec))
        if self.fail:
            from api.rest_client import KabuNativeApiError

            raise KabuNativeApiError("register HTTP 500: boom")
        have = {s for s, _ in self.regist}
        for s, ex in symbols_spec:
            if s not in have:
                self.regist.append((s, int(ex)))
                have.add(s)
        n = self.regist_override if self.regist_override is not None else len(symbols_spec)
        return {
            "RegistNum": n,
            "Symbols": [{"Symbol": s, "Exchange": int(ex)} for s, ex in symbols_spec],
            "RegistList": [{"Symbol": s, "Exchange": int(ex)} for s, ex in symbols_spec],
        }

    def unregister_all(self) -> dict[str, Any]:
        self.regist = []
        return {"RegistNum": 0, "Symbols": [], "RegistList": []}

    def fetch_regist_list(self) -> dict[str, Any]:
        return {
            "ok": True,
            "readonly": True,
            "reason": "push_fetch_regist_list",
            "symbols": [s for s, _ in self.regist],
            "http_status": 200,
        }


def _svc(tmp_path: Path) -> MarketIngressService:
    return MarketIngressService(
        native_root=tmp_path,
        trading_date="20260727",
        synthetic=False,
        enable_tcp_bus=False,
    )


def _symbols(n: int = 50) -> list[str]:
    return [f"{7200 + i}" for i in range(n)]


def test_empty_connect_then_desired_50_puts_once(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    push = _FakePush()
    svc._push_client = push
    # Simulate empty connect bookkeeping
    svc.registered_symbols = []
    svc._register_put_ok = False
    assert svc._should_skip_live_register() is True  # empty desired

    write_desired_universe(tmp_path, symbols=_symbols(50), generation=1001, trading_date="20260727")
    svc._poll_desired_universe()

    assert len(push.calls) == 1
    assert len(push.calls[0]) == 50
    assert svc._register_put_ok is True
    assert len(svc.registered_symbols) == 50
    assert svc.sm.entry_blocked is True
    assert svc.sm.entry_block_reason == "WAITING_FIRST_PUSH"
    ev = json.loads((tmp_path / "data" / "market_capture" / "20260727" / "ingress_register_api_trace.json").read_text(
        encoding="utf-8"
    ))
    assert ev["put_executed"] is True
    assert ev["verified"] is True
    assert ev["actual_count"] == 50
    assert ev["generation"] == 1001
    assert ev["universe_hash"] == universe_symbol_hash(_symbols(50))
    man = read_registration_manifest(tmp_path)
    assert man["registration_verified"] is True
    assert man["actual_count"] == 50
    assert man["owner"] == "MARKET_INGRESS_SERVICE"
    svc.writer.close()
    svc.bus.stop()


def test_same_desired_generation_does_not_duplicate_put(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    push = _FakePush()
    svc._push_client = push
    write_desired_universe(tmp_path, symbols=_symbols(50), generation=2002, trading_date="20260727")
    svc._poll_desired_universe()
    assert len(push.calls) == 1
    svc._poll_desired_universe()
    svc._poll_desired_universe()
    assert len(push.calls) == 1
    out = svc._maybe_register_desired_live(reason="repeat")
    assert out.get("skipped") is True
    assert len(push.calls) == 1
    svc.writer.close()
    svc.bus.stop()


def test_universe_generation_change_reregisters(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    push = _FakePush()
    svc._push_client = push
    syms_a = _symbols(50)
    write_desired_universe(tmp_path, symbols=syms_a, generation=3001, trading_date="20260727")
    svc._poll_desired_universe()
    assert len(push.calls) == 1
    syms_b = [f"{7300 + i}" for i in range(50)]
    write_desired_universe(tmp_path, symbols=syms_b, generation=3002, trading_date="20260727")
    svc._poll_desired_universe()
    assert len(push.calls) == 2
    assert [s for s, _ in push.calls[1]] == syms_b
    assert svc._last_register_generation == 3002
    svc.writer.close()
    svc.bus.stop()


def test_api_success_sets_verified_from_response(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    push = _FakePush()
    svc._push_client = push
    write_desired_universe(tmp_path, symbols=["7203", "6758"], generation=4001, trading_date="20260727")
    out = svc._poll_desired_universe() or svc._register_evidence
    assert svc._register_evidence["verified"] is True
    assert svc._register_evidence["response_body"]["RegistNum"] == 2
    assert svc._register_evidence["http_status"] == 200
    c = svc.readiness_conditions()
    assert c["registered_ok"] is True
    # First-PUSH not yet → ENTRY remains blocked
    assert svc.sm.entry_blocked is True
    svc.writer.close()
    svc.bus.stop()


def test_api_failure_fail_close(tmp_path: Path) -> None:
    svc = _svc(tmp_path)
    push = _FakePush(fail=True)
    svc._push_client = push
    write_desired_universe(tmp_path, symbols=_symbols(10), generation=5001, trading_date="20260727")
    svc._poll_desired_universe()
    assert svc._register_put_ok is False
    assert svc.registered_symbols == []
    assert svc._register_evidence["verified"] is False
    assert svc._register_evidence["actual_count"] == 0
    assert svc.sm.entry_blocked is True
    assert svc.sm.entry_block_reason == "REGISTER_FAILED"
    man = read_registration_manifest(tmp_path)
    assert man["registration_verified"] is False
    assert man["actual_count"] == 0
    c = svc.readiness_conditions()
    assert c["registered_ok"] is False
    svc.writer.close()
    svc.bus.stop()


def test_paper_match_alone_does_not_verify(tmp_path: Path) -> None:
    day = "20260727"
    out = notify_registration_refresh(
        tmp_path,
        trading_date=day,
        new_symbols=_symbols(50),
        verified=False,
        capture_day_dir=tmp_path / "data" / "market_capture" / day,
    )
    assert out["ok"] is True
    man = read_registration_manifest(tmp_path)
    assert man["registration_verified"] is False
    assert man["actual_count"] == 0
    assert man["status"] == "PLANNED_FOLLOWER"
    assert man.get("verification_source") == "paper_desired_only"


def test_registered_ok_false_when_desired_without_put(tmp_path: Path) -> None:
    """Regression: desired=50 must not imply registered_ok without PUT."""
    svc = _svc(tmp_path)
    svc.set_desired_universe(_symbols(50), generation=6001)
    assert len(svc.desired_symbols) == 50
    assert len(svc.registered_symbols) == 0
    assert svc._register_put_ok is False
    assert svc.readiness_conditions()["registered_ok"] is False
    svc.writer.close()
    svc.bus.stop()


def test_submit_cancel_live_not_touched_by_register_path(tmp_path: Path) -> None:
    """Ingress register path must not open live order channels (structural guard)."""
    src = Path(__file__).resolve().parents[1] / "src" / "small_paper" / "market_ingress_service.py"
    text = src.read_text(encoding="utf-8")
    for banned in ("send_order", "place_order", "cancel_order", "live_trading_enabled"):
        assert banned not in text
    svc = _svc(tmp_path)
    push = _FakePush()
    svc._push_client = push
    write_desired_universe(tmp_path, symbols=["7203"], generation=7001, trading_date="20260727")
    svc._poll_desired_universe()
    assert len(push.calls) == 1
    svc.writer.close()
    svc.bus.stop()


def test_connect_path_registers_when_desired_preloaded(tmp_path: Path) -> None:
    """Normal path: desired present before connect still PUTs via _execute_live_register."""
    svc = _svc(tmp_path)
    push = _FakePush()
    write_desired_universe(tmp_path, symbols=["7203", "6758"], generation=8001, trading_date="20260727")
    svc._poll_desired_universe_apply_only()
    assert svc.desired_symbols == ["7203", "6758"]
    svc._push_client = push
    out = svc._execute_live_register(push, reason="connect")
    assert out["put_executed"] is True
    assert out["verified"] is True
    assert len(push.calls) == 1
    # Second connect-equivalent with same gen must not duplicate
    out2 = svc._execute_live_register(push, reason="connect")
    assert out2.get("skipped") is True
    assert len(push.calls) == 1
    svc.writer.close()
    svc.bus.stop()
