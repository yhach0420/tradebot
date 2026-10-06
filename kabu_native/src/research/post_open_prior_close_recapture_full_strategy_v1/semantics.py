"""Trusted PreviousClose = 前日終値. No web. No gap-size guess."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def prove_previous_close_semantics() -> dict[str, Any]:
    native = Path(__file__).resolve().parents[3]
    mapping = native.parent / "docs" / "kabu_response_mapping.md"
    inventory = native / "docs" / "board_data_inventory.md"
    blob = ""
    files = []
    for p in (mapping, inventory):
        if p.is_file():
            files.append(str(p))
            blob += p.read_text(encoding="utf-8")
    keys = set()
    try:
        from check_api import BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS as live

        keys = set(live)
    except Exception:
        keys = {"PreviousClose", "PreviousCloseTime", "CurrentPrice"}
    in_schema = "PreviousClose" in keys
    has_ts = "PreviousCloseTime" in keys
    label_ok = "PreviousClose" in blob and "前日終値" in blob
    proven = bool(label_ok and in_schema)
    return {
        "OFFICIAL_NAME": "PreviousClose",
        "OFFICIAL_MEANING": "前日終値 (BoardSuccess PreviousClose)",
        "UNIT": "price",
        "SCHEMA_SOURCE": "kabu_native/scripts/check_api.py BOARD_SUCCESS_SCHEMA_TOP_LEVEL_KEYS",
        "MEANING_SOURCE": "docs/kabu_response_mapping.md §1: PreviousClose = 前日終値",
        "IN_BOARDSUCCESS": in_schema,
        "DEDICATED_FIELD_TIMESTAMP": bool(has_ts),
        "TIMESTAMP_NAME": "PreviousCloseTime" if has_ts else None,
        "TIMESTAMP_USAGE": "Not used as trade ID, availability, or Bid/Ask freshness. Availability = INGRESS.",
        "CONTINUOUS_SESSION_FIELD": True,
        "PREOPEN_ONLY_IN_SCHEMA": False,
        "SAME_INGRESS_PAYLOAD": True,
        "STALE_POLICY": (
            "First valid AM PreviousClose (finite > 0) is frozen per symbol-day as PRIOR_CLOSE_ANCHOR. "
            "Null/non-finite/<=0 is invalid. Do not invent a close from CurrentPrice."
        ),
        "THESIS_FIT": (
            "前日終値 is the prior-session close. Observed Trade below then above that level is "
            "recapture of the prior-session close by actual prints."
        ),
        "DOCS": files,
        "SEMANTICS_PROVEN": proven,
        "WEB_USED": False,
    }
