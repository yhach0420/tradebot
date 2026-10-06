"""20260827 capture quality. OPERATIONAL_VALIDATION_ONLY is not an exclusion."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from research.anchor_vs_event_driven.run_comparison import _load_json, find_capture_dir, historical_universe

NATIVE = Path(__file__).resolve().parents[3]


def _int(v: Any) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return 0


def capture_quality(day: str, *, inv_row: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    cap = find_capture_dir(day)
    uni, uni_src = historical_universe(day, cap) if cap is not None else ([], "missing")
    comp = _load_json(cap / "capture_completeness.json") if cap is not None else {}
    seal = _load_json(cap / "seal.json") if cap is not None else {}
    statusj = _load_json(cap / "status.json") if cap is not None else {}
    dropped = _int(comp.get("dropped_event_count"))
    ts_reg = _int(comp.get("timestamp_regression_count"))
    holes = _int(comp.get("largest_gap_sec"))
    am = bool(comp.get("coverage_am"))
    pm = bool(comp.get("coverage_pm"))
    uni_n = len(uni)
    if inv_row:
        am = bool(inv_row.get("am_coverage") if inv_row.get("am_coverage") is not None else am)
        pm = bool(inv_row.get("pm_coverage") if inv_row.get("pm_coverage") is not None else pm)
        if inv_row.get("universe_n"):
            uni_n = int(inv_row.get("universe_n") or uni_n)
        dropped = _int(inv_row.get("dropped_event_count") or dropped)
    seq_first = (inv_row or {}).get("first_seq")
    seq_gap = False
    try:
        if seq_first is not None and int(seq_first) != 1:
            seq_gap = True
    except (TypeError, ValueError):
        seq_gap = True
    if dropped > 0 or holes > 0:
        seq_gap = True
    persistence = bool(comp.get("heartbeat_until_finalize")) and bool(comp.get("seal_pass") or seal.get("seal_pass"))
    causal = ts_reg == 0 and not bool(comp.get("session_mixing"))
    status = str(comp.get("status") or seal.get("completeness", {}).get("status") or "")
    checks = {
        "AM_coverage_complete": am,
        "PM_coverage_complete": pm,
        "universe_50": uni_n == 50,
        "seq_gap_0": (not seq_gap) and dropped == 0,
        "drop_hole_0": dropped == 0 and holes == 0,
        "timestamp_integrity": ts_reg == 0,
        "causal_order": causal,
        "session_persistence_complete": persistence,
        "COMPLETE_CAPTURE": status == "COMPLETE_CAPTURE",
    }
    fail = [k for k, v in checks.items() if not v]
    # OPVAL-only must not exclude.
    opval_only = True
    include = not fail
    return {
        "date": day,
        "capture_path": str(cap) if cap else "",
        "universe_n": uni_n,
        "universe_source": uni_src,
        "status": status,
        "dropped_event_count": dropped,
        "largest_gap_sec": holes,
        "timestamp_regression_count": ts_reg,
        "session_mixing": bool(comp.get("session_mixing")),
        "upstream_disconnect_count": _int(comp.get("upstream_disconnect_count")),
        "OPERATIONAL_VALIDATION_ONLY_NOT_EXCLUSION": opval_only,
        "PNL_NOT_USED_FOR_ADOPTION": True,
        "checks": checks,
        "fail": fail,
        "PASS": include,
        "DECISION": "INCLUDED" if include else "EXCLUDED",
        "status_json_ok": bool(statusj),
    }
