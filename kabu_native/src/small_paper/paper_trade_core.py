"""
Phase616: PaperTradeCore — canonical hot path (decision path only).
"""

from __future__ import annotations

from typing import Final

# Ordered Core hot path (Extension must not alter decisions on this path).
PAPER_TRADE_CORE_HOT_PATH: Final[tuple[str, ...]] = (
    "PUSH",
    "enrich_payload",
    "freshness",
    "PBv2",
    "OR",
    "ObserverPaperBook",
    "LiveWriter",
)

CORE_PIPELINE_STEPS: Final[tuple[tuple[str, str, str], ...]] = (
    ("push.iter_messages", "storage/push_stream", "Receive WebSocket PUSH"),
    ("LiveFeatureBridge.update+enrich_payload", "small_paper/live_feature_bridge", "Feature enrich"),
    ("compute_entry_freshness+evaluate_entry_data_freshness", "small_paper/entry_scan_controller", "Freshness gate"),
    ("ExposureGate.evaluate_entry", "research/exposure_gate", "PBv2 gate"),
    ("_maybe_try_or_overlay_entry", "small_paper/or_overlay_entry", "OR overlay if PBv2 reject"),
    ("ObserverPositionTracker.register_entry/on_tick", "small_paper/observer_position_tracker", "Paper book"),
    ("LiveSessionWriter.append_event", "small_paper/live_writer", "Events + summary"),
)


class PaperTradeCore:
    """Marker for the decision-critical pipeline; orchestration remains in pilot_runner."""

    hot_path = PAPER_TRADE_CORE_HOT_PATH
    steps = CORE_PIPELINE_STEPS
