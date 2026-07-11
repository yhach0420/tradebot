"""Phase 155: kabu register capacity helpers (offline)."""

from __future__ import annotations

import sys
from pathlib import Path

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
            return {"RegistNum": len(symbols_spec)}

    specs = [("7203", 1), ("9984", 1)]
    out = register_symbols_cleared(FakePush(), specs)
    assert out["ok"] is True
    assert out.get("recovered_from_register_limit") is True
    assert calls == ["unregister", "register", "unregister", "register"]


def test_register_symbols_cleared_fails_after_retry() -> None:
    class FakePush:
        def unregister_all(self) -> dict:
            return {}

        def register(self, symbols_spec) -> dict:
            raise KabuNativeApiError('register HTTP 400: {"Code":4002006}')

    try:
        register_symbols_cleared(FakePush(), [("7203", 1)])
        raise AssertionError("expected KabuNativeApiError")
    except KabuNativeApiError as e:
        msg = str(e)
        assert "retried once" in msg or "after unregister retry" in msg
        assert "4002006" in msg


if __name__ == "__main__":
    test_parse_kabu_error_code_from_json_message()
    test_register_symbols_cleared_retries_after_limit()
    test_register_symbols_cleared_fails_after_retry()
    print("test_kabu_register: ok")
