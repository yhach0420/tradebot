"""Enrich an existing Cost-Aware V1 summary block with runtime-compatible joins.

Preserves the live-selected closed_trades / shadow_entries; does not re-select.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from small_paper.cost_aware_entry_shadow import (
    CostAwareShadowState,
    attach_runtime_compatible_to_closed_trades,
    summarize_state,
)
from small_paper.cost_aware_price_path import build_symbol_price_paths, parse_ts
from small_paper.cost_aware_shadow_recompute import _force_close_dt, _session_kind_from_dir


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def enrich_cost_aware_v1_from_session(session_dir: Path, *, trading_date: str) -> dict[str, Any]:
    """Attach runtime-compatible PnL to existing V1 closed_trades in summary."""
    session_dir = Path(session_dir)
    summary_path = session_dir / "small_paper_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    block = summary.get("cost_aware_entry_shadow")
    if not isinstance(block, Mapping):
        return {"enabled": False, "status": "NO_STATE", "shadow_entries": 0}

    session_kind = _session_kind_from_dir(session_dir, summary)
    force_close = _force_close_dt(trading_date, session_kind)

    events = _load_jsonl(session_dir / "small_paper_events.jsonl")
    price_paths = build_symbol_price_paths(events)
    official_exits = []
    for e in events:
        if e.get("event_type") != "observer_exit":
            continue
        ts = parse_ts(e.get("exit_time") or e.get("event_time"))
        try:
            px = float(e.get("exit_price") or 0)
        except (TypeError, ValueError):
            px = 0.0
        if ts:
            official_exits.append((ts, str(e.get("symbol") or ""), px, str(e.get("exit_reason") or "")))

    closed = list(block.get("closed_trades") or [])
    # If summary dropped trades, attempt to keep empty with explicit status.
    st = CostAwareShadowState()
    st.selection_cycles = int(block.get("selection_cycles") or 0)
    st.shadow_eligible = int(block.get("shadow_eligible") or block.get("eligible") or 0)
    st.stop_risk_reject = int(block.get("stop_risk_reject") or 0)
    st.same_snapshot_nofill = int(block.get("same_snapshot_nofill") or block.get("no_fill") or 0)
    st.later_fill = int(block.get("later_fill") or 0)
    st.never_filled = int(block.get("never_filled") or 0)
    st.shadow_entries = int(block.get("shadow_entries") or len(closed))
    st.official_match = int(block.get("official_entry_match") or 0)
    st.official_mismatch = int(block.get("official_entry_mismatch") or 0)
    # Preserve candidate event count for Discord/target display
    st.events = [{}] * int(block.get("candidates") or 0)

    if closed:
        enriched, join_stats = attach_runtime_compatible_to_closed_trades(
            closed,
            official_exits=official_exits,
            price_paths=price_paths,
            force_close_time=force_close,
        )
        st.closed_trades = enriched
    else:
        join_stats = {
            "join_success_count": 0,
            "join_failed_count": 0,
            "pending_count": 0,
            "delta_eligible_count": 0,
            "join_failure_reasons": {},
        }

    out = summarize_state(st)
    out.update(join_stats)
    out["session_kind"] = session_kind
    out["force_close_time"] = force_close.isoformat()
    out["session_dir"] = str(session_dir)
    out["enrich_mode"] = "existing_closed_trades"
    # Carry through observe-only identity
    out["blocks_real_entry"] = False
    out["observe_only"] = True
    out["real_block_count"] = 0
    return out
