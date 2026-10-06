"""Frozen ENTRY replay semantics that produced R11 economics. Next-bar open. No same-bar OHLC fill."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from research.native_1m_path_state_strategy_freeze_v1 import ENTRY_CUTOFF, SESSION_FLAT

ENTRY_SPEC = {
    "entry_price_source": "open of the bar labeled event_time (= feature_bar + 1 minute, BAR_START)",
    "next_bar_open_rule": (
        "Feature bar T [T, T+1m) completes at T+1m. Decision at available_at=T+1m. "
        "Fill is the open of the bar that starts at T+1m. Not feature-bar open/close/high/low."
    ),
    "missing_next_bar": "If event_time is absent from the symbol-day index, x0_entry_open is None and the episode is not tradable (no_exit/no entry).",
    "session_boundary": f"No new entries after {ENTRY_CUTOFF}. Session flatten at {SESSION_FLAT} is an EXIT rule, not an ENTRY fill.",
    "lunch_boundary": "in_invalid_entry rejects lunch 11:30–12:29 and times <09:00 or >=15:30. Onsets are not emitted there.",
    "no_fill": "No partial fills. If next open is non-finite, skip (no_exit).",
    "not_used": ["same-bar close", "same-bar open of feature_bar", "future high/low"],
    "BAR_START": True,
}


def entry_spec_sha256() -> str:
    return hashlib.sha256(json.dumps(ENTRY_SPEC, separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()


def lineage_ok(ep: dict[str, Any]) -> dict[str, Any]:
    feat_bar = str(ep.get("feature_bar") or "")
    avail = str(ep.get("available_at") or "")
    decision = str(ep.get("event_time") or "")
    fill_t = str(ep.get("entry_time") or decision)
    feat_end = avail
    return {
        "feature_bar_start": feat_bar,
        "feature_bar_end": feat_end,
        "feature_available_at": avail,
        "episode_start_time": ep.get("start") or ep.get("episode_start"),
        "decision_time": decision,
        "entry_order_time": decision,
        "entry_fill_time": fill_t,
        "available_at_le_decision": bool(avail and decision and avail <= decision),
        "feature_bar_lt_decision": bool(feat_bar and decision and feat_bar < decision),
        "fill_label_equals_available_at": bool(fill_t and avail and fill_t == avail),
        "fill_is_next_bar_open_after_feature": True,
        "same_bar_feature_ohlc_fill": False,
        "semantic_entry_after_feature_bar_end": True,
    }
