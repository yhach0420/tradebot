"""Trusted PreviousClose = 前日終値. No web. No guessed formula."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def prove_previous_close_semantics() -> dict[str, Any]:
    native = Path(__file__).resolve().parents[3]
    mapping = native.parent / "docs" / "kabu_response_mapping.md"
    check = native / "scripts" / "check_api.py"
    blob = ""
    files = []
    for p in (mapping, check):
        if p.is_file():
            files.append(str(p))
            blob += p.read_text(encoding="utf-8")
    in_schema = "PreviousClose" in blob
    label = "PreviousClose" in blob and "前日終値" in blob
    has_time = "PreviousCloseTime" in blob
    proven = bool(in_schema and label)
    return {
        "OFFICIAL_NAME": "PreviousClose",
        "OFFICIAL_MEANING": "前日終値 (BoardSuccess PreviousClose)",
        "TIME_FIELD": "PreviousCloseTime" if has_time else None,
        "SCHEMA_SOURCE": "kabu_native/scripts/check_api.py BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS",
        "MEANING_SOURCE": "docs/kabu_response_mapping.md §1: PreviousClose = 前日終値",
        "IN_BOARDSUCCESS": in_schema,
        "DEDICATED_FIELD_TIMESTAMP": has_time,
        "AVAILABILITY": "INGRESS as-of BoardSuccess snapshot. PreviousCloseTime is the prior-close date, not trade identity.",
        "CURRENT_PRICE_TIME_AS_TRADE_ID": False,
        "PREVIOUS_CLOSE_TIME_AS_TRADE_ID": False,
        "FILES": files,
        "SEMANTICS_PROVEN": proven,
        "NOTE": (
            "PreviousClose is the prior-session close reference. The strategy state is Observed Trade "
            "relative to that reference, not a change in PreviousClose itself."
        ),
    }
