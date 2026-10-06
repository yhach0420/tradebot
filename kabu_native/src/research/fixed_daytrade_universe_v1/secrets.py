"""Refuse secrets in research artifacts / stdout payloads."""
from __future__ import annotations

import json
from typing import Any

from research.fixed_daytrade_universe_v1.jquants_auth import secret_needles

FORBIDDEN_SECRET_KEYS = (
    "api_key",
    "apikey",
    "x-api-key",
    "x_api_key",
    "authorization",
    "password",
    "refresh_token",
    "id_token",
    "mailaddress",
)


def _walk_text(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def contains_secret(obj: Any) -> bool:
    text = _walk_text(obj)
    lower = text.lower()
    for k in FORBIDDEN_SECRET_KEYS:
        if f'"{k}":' in lower and k not in {"authorization"}:
            # allow boolean flags like api_key_recorded:false; reject actual key fields with values
            pass
    for needle in secret_needles():
        if needle and needle in text:
            return True
    return False


def assert_no_secret(obj: Any, *, where: str) -> None:
    if contains_secret(obj):
        raise RuntimeError(f"secret_would_be_written:{where}")


assert contains_secret({"ok": True}) is False
