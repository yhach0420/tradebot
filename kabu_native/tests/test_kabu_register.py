"""Phase 155: kabu register capacity helpers (offline)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from api.kabu_register import (  # noqa: E402
    is_register_limit_error,
    parse_kabu_error_code,
    register_symbols_cleared,
)
from api.rest_client import KabuNativeApiError


def test_parse_kabu_error_code_from_json_message() -> None:
    exc = KabuNativeApiError('register HTTP 400: {"Code":4002006,"Message":"レジスト数エラー"}')
    assert parse_kabu_error_code(exc) == 4002006
    assert is_register_limit_error(exc)


def test_register_symbols_cleared_retries_after_limit() -> None:
    calls: list[str] = []

    class FakePush:
        def unregister_all(self) -> dict:
            calls.append("unregister")
            return {"RegistNum": 0}

        def register(self, symbols_spec) -> dict:
            calls.append("register")
            if calls.count("register") == 1:
                raise KabuNativeApiError(
                    'register HTTP 400: {"Code":4002006,"Message":"レジスト数エラー"}'
                )
            return {
                "RegistNum": len(symbols_spec),
                "Symbols": [{"Symbol": s, "Exchange": ex} for s, ex in symbols_spec],
            }

    specs = [("7203", 1), ("9984", 1)]
    out = register_symbols_cleared(FakePush(), specs, settle_sec=0.0, allow_reuse_if_match=False)
    assert out["ok"] is True
    assert out.get("recovered_from_register_limit") is True
    assert calls == ["unregister", "register", "unregister", "register"]


def test_register_symbols_cleared_fails_after_retry() -> None:
    class FakePush:
        def unregister_all(self) -> dict:
            return {"RegistNum": 0}

        def register(self, symbols_spec) -> dict:
            raise KabuNativeApiError('register HTTP 400: {"Code":4002006}')

    try:
        register_symbols_cleared(
            FakePush(), [("7203", 1)], settle_sec=0.0, allow_reuse_if_match=False
        )
        raise AssertionError("expected KabuNativeApiError")
    except KabuNativeApiError as e:
        msg = str(e)
        assert "4002006" in msg
        assert "FAILED" in msg or "failed" in msg.lower() or "retry" in msg.lower()


def test_safe_reuse_when_desired_matches(tmp_path: Path) -> None:
    from api.kabu_register import save_paper_register_state

    specs = [("7203", 1), ("9984", 1)]
    save_paper_register_state(
        tmp_path, symbols_spec=specs, regist_num=2, trading_date="20990101"
    )

    class BoomPush:
        def unregister_all(self):
            raise AssertionError("should reuse")

        def register(self, symbols_spec):
            raise AssertionError("should reuse")

    out = register_symbols_cleared(
        BoomPush(),
        specs,
        native_root=tmp_path,
        trading_date="20990101",
        settle_sec=0.0,
        allow_reuse_if_match=True,
    )
    assert out.get("reused_existing") is True
    assert out.get("ok") is True


def test_unregister_delayed_zero_readback() -> None:
    from api.kabu_register import unregister_all_until_zero

    n = {"i": 0}

    class DelayedPush:
        def unregister_all(self):
            n["i"] += 1
            return {"RegistNum": 0 if n["i"] >= 2 else 3}

    out = unregister_all_until_zero(DelayedPush(), settle_sec=0.0, max_attempts=3)
    assert out.get("readback_zero") is True
    assert n["i"] == 2


def test_register_limit_deferred_clear_honest_message(tmp_path: Path) -> None:
    """True Capture direct-owner (PASSIVE_DUAL applied) still defers; message honest."""
    from small_paper.market_capture_sidecar import (
        HEARTBEAT_FILE,
        MANIFEST_FILE,
        PID_FILE_NAME,
        STATUS_FILE,
        capture_day_dir,
    )

    day = capture_day_dir(tmp_path, "20990101")
    day.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    (day / PID_FILE_NAME).write_text(str(pid), encoding="utf-8")
    symbols = [{"symbol": f"{1000 + i}.T", "exchange": 1} for i in range(50)]
    payload = {
        "capture_session_id": "c1",
        "trading_date": "20990101",
        "provenance": "LIVE_KABU_PUSH_CAPTURE",
        "scheduled_end_at": "2099-01-01T15:35:00+09:00",
        "pid": pid,
        "registered_symbols": symbols,
        "applied": True,
        "registration_verified": True,
        "topology": "PASSIVE_DUAL_WEBSOCKET",
        "ingress": "kabu_direct",
    }
    (day / MANIFEST_FILE).write_text(json.dumps(payload), encoding="utf-8")
    (day / STATUS_FILE).write_text(
        json.dumps(
            {
                "capture_status": "CAPTURE_ONLINE",
                "pid": pid,
                "applied": True,
                "registration_verified": True,
                "topology": "PASSIVE_DUAL_WEBSOCKET",
                "ingress": "kabu_direct",
            }
        ),
        encoding="utf-8",
    )
    (day / HEARTBEAT_FILE).write_text(
        json.dumps({"pid": pid, "status": "CAPTURE_ONLINE", "topology": "PASSIVE_DUAL_WEBSOCKET"}),
        encoding="utf-8",
    )
    (tmp_path / "runtime").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "market_registration_manifest.json").write_text(
        json.dumps({"registered_symbols": symbols, "generation_id": "g1", "applied": True}),
        encoding="utf-8",
    )

    class FakePush:
        def unregister_all(self) -> dict:
            # force_clear_on_limit=True will still clear on 4002006 even if initially deferred
            return {"RegistNum": 0}

        def register(self, symbols_spec) -> dict:
            raise KabuNativeApiError('register HTTP 400: {"Code":4002006}')

    # With force_clear_on_limit, recovery still attempts clear; final failure after retry
    try:
        register_symbols_cleared(
            FakePush(),
            [("7203", 1)],
            native_root=tmp_path,
            trading_date="20990101",
            settle_sec=0.0,
            allow_reuse_if_match=False,
            force_clear_on_limit=True,
        )
        raise AssertionError("expected error")
    except KabuNativeApiError as e:
        msg = str(e)
        assert "4002006" in msg
        assert "Cleared via unregister/all and retried once" not in msg


def test_ready_for_fanout_allows_clear(tmp_path: Path) -> None:
    from small_paper.market_capture_sidecar import (
        HEARTBEAT_FILE,
        MANIFEST_FILE,
        PID_FILE_NAME,
        STATUS_FILE,
        capture_day_dir,
    )
    from small_paper.registration_lifetime import clear_first_allowed_for_register

    day = capture_day_dir(tmp_path, "20990101")
    day.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    (day / PID_FILE_NAME).write_text(str(pid), encoding="utf-8")
    symbols = [{"symbol": f"{1000 + i}.T", "exchange": 1} for i in range(50)]
    (day / MANIFEST_FILE).write_text(
        json.dumps(
            {
                "capture_session_id": "c1",
                "trading_date": "20990101",
                "provenance": "LIVE_KABU_PUSH_CAPTURE",
                "scheduled_end_at": "2099-01-01T15:35:00+09:00",
                "pid": pid,
                "registered_symbols": symbols,
                "applied": False,
                "topology": "SINGLE_INGRESS_LOCAL_FANOUT",
                "ingress": "paper_fanout",
            }
        ),
        encoding="utf-8",
    )
    (day / STATUS_FILE).write_text(
        json.dumps(
            {
                "capture_status": "CAPTURE_READY_FOR_FANOUT",
                "pid": pid,
                "applied": False,
                "topology": "SINGLE_INGRESS_LOCAL_FANOUT",
                "ingress": "paper_fanout",
            }
        ),
        encoding="utf-8",
    )
    (day / HEARTBEAT_FILE).write_text(
        json.dumps(
            {
                "pid": pid,
                "status": "CAPTURE_READY_FOR_FANOUT",
                "topology": "SINGLE_INGRESS_LOCAL_FANOUT",
                "ingress": "paper_fanout",
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "runtime").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "market_registration_manifest.json").write_text(
        json.dumps({"registered_symbols": symbols, "applied": False}),
        encoding="utf-8",
    )
    assert clear_first_allowed_for_register(tmp_path, trading_date="20990101") is True

    push = MagicMock()
    push.unregister_all.return_value = {"RegistNum": 0}
    push.register.return_value = {
        "RegistNum": 1,
        "Symbols": [{"Symbol": "7203", "Exchange": 1}],
    }
    out = register_symbols_cleared(
        push,
        [("7203", 1)],
        native_root=tmp_path,
        trading_date="20990101",
        settle_sec=0.0,
        allow_reuse_if_match=False,
    )
    assert out.get("clear_first_effective") is True
    push.unregister_all.assert_called()


def test_receiving_via_fanout_owner_false(tmp_path: Path) -> None:
    from small_paper.registration_lifetime import is_live_capture_registration_owner_active
    from small_paper.market_capture_sidecar import (
        HEARTBEAT_FILE,
        MANIFEST_FILE,
        PID_FILE_NAME,
        STATUS_FILE,
        capture_day_dir,
    )

    day = capture_day_dir(tmp_path, "20990101")
    day.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    (day / PID_FILE_NAME).write_text(str(pid), encoding="utf-8")
    symbols = [{"symbol": f"{1000 + i}.T", "exchange": 1} for i in range(50)]
    for name, status in (
        (MANIFEST_FILE, None),
        (STATUS_FILE, "CAPTURE_ONLINE"),
        (HEARTBEAT_FILE, "CAPTURE_ONLINE"),
    ):
        body = {
            "capture_session_id": "c1",
            "trading_date": "20990101",
            "provenance": "LIVE_KABU_PUSH_CAPTURE",
            "scheduled_end_at": "2099-01-01T15:35:00+09:00",
            "pid": pid,
            "registered_symbols": symbols,
            "topology": "SINGLE_INGRESS_LOCAL_FANOUT",
            "ingress": "paper_fanout",
            "applied": False,
        }
        if status:
            body["capture_status"] = status
            body["status"] = status
        (day / name).write_text(json.dumps(body), encoding="utf-8")
    (tmp_path / "runtime").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "market_registration_manifest.json").write_text(
        json.dumps({"registered_symbols": symbols}), encoding="utf-8"
    )
    d = is_live_capture_registration_owner_active(tmp_path, trading_date="20990101")
    assert d.active is False
    assert "fanout" in d.reason or "paper_owns" in d.reason


if __name__ == "__main__":
    test_parse_kabu_error_code_from_json_message()
    test_register_symbols_cleared_retries_after_limit()
    test_register_symbols_cleared_fails_after_retry()
    print("test_kabu_register: ok")
