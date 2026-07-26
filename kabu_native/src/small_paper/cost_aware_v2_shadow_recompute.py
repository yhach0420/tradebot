"""Offline Cost-Aware V2 exit join + summary recompute (Paper / observe-only)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from small_paper.cost_aware_entry_v2_shadow import (
    CostAwareV2ShadowState,
    finalize_pending_exits,
    summarize_state,
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def recompute_cost_aware_v2_session(session_dir: Path) -> dict[str, Any]:
    """Re-join official exits into embedded V2 by_key and return updated summary block."""
    session_dir = Path(session_dir)
    summary_path = session_dir / "small_paper_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    block = summary.get("cost_aware_entry_v2_shadow")
    if not isinstance(block, Mapping):
        return {
            "enabled": False,
            "observe_only": True,
            "evaluated_candidates": 0,
            "join_success_count": 0,
            "join_failed_count": 0,
            "pending_count": 0,
            "status": "NO_STATE",
        }

    st = CostAwareV2ShadowState(
        enabled=bool(block.get("enabled", True)),
        enabled_source=str(block.get("enabled_source") or "recompute"),
    )
    by_key = block.get("by_key") or {}
    if isinstance(by_key, Mapping):
        st.by_key = {str(k): dict(v) for k, v in by_key.items() if isinstance(v, Mapping)}

    exits = [e for e in _load_jsonl(session_dir / "small_paper_events.jsonl") if e.get("event_type") == "observer_exit"]
    finalize_pending_exits(st, exits, session_force_close=True)
    out = summarize_state(st)
    am_pm = summary.get("am_pm_session")
    if isinstance(am_pm, Mapping):
        kind = str(am_pm.get("kind") or "").upper()
        if kind in ("AM", "PM"):
            out["session_kind"] = kind
    out["by_key"] = {k: dict(v) for k, v in st.by_key.items()}
    out["session_dir"] = str(session_dir)
    out["observe_only"] = True
    out["mainline_pnl_included"] = False
    out["canonical_pnl_mixed"] = False
    out["submit"] = 0
    out["cancel"] = 0
    out["live_order"] = 0
    return out
