"""Blinded semantic labels from chart-visible structure only. Path joined after."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.pb1_opening_range_causal_path_test_v1.analyze import _finite


def label_from_visible(ev: dict[str, Any]) -> dict[str, Any]:
    """Uses only pre-entry, chart-visible fields. No future return."""
    brk = str(ev.get("break_t") or "")
    age = ev.get("break_to_retest_minutes")
    beyond = ev.get("break_beyond_over_or")
    away = ev.get("away_n")
    room_r = ev.get("target_distance_R")
    already = bool(ev.get("already_extended"))
    reason = str(ev.get("open_reason") or "")
    trig_ok = True
    labels = list(ev.get("trigger_labels") or [])
    primary = str(ev.get("trigger_primary") or "")
    if primary == "FAILED_PUSH_THEN_CLOSE_BACK" and ev.get("risk_state") == "RISK_INVALID":
        trig_ok = False
    if "09:40" <= brk <= "10:00":
        klass = "RANGE_RESOLUTION"
        trig_ok = trig_ok and False
    elif _finite(beyond) and float(beyond) < 0.10 and (not _finite(away) or float(away) <= 2):
        klass = "MICRO_BREAK"
        trig_ok = False
    elif _finite(age) and float(age) >= 20:
        klass = "STALE_RETEST"
        trig_ok = False
    elif already or (_finite(room_r) and float(room_r) < 0.50):
        klass = "ALREADY_EXTENDED"
    elif "reverse" in reason:
        klass = "FAILED_OPEN_REVERSAL"
        trig_ok = False
    elif brk >= "09:15" and brk <= "09:24" and (not _finite(age) or float(age) < 15):
        klass = "CLEAR_CONTINUATION"
    else:
        klass = "OTHER"
    return {
        "semantic": klass,
        "trigger_semantically_valid": bool(trig_ok),
        "note": f"visible break={brk} age={age} beyond/OR={beyond} labels={labels}",
        "future_used": False,
    }


def apply_labels(sample: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for e in sample:
        lab = label_from_visible(e)
        rows.append(
            {
                "sample_id": e.get("sample_id"),
                "symbol": e.get("symbol"),
                "date": e.get("date"),
                "block": e.get("block"),
                "direction": e.get("direction"),
                "trigger_primary": e.get("trigger_primary"),
                **lab,
            }
        )
    n = len(rows)
    c = Counter(str(r.get("semantic")) for r in rows)
    return {
        "sample_n": n,
        "reviewed_n": n,
        "future_hidden_during_review": True,
        "counts": dict(c),
        "CLEAR_CONTINUATION": int(c.get("CLEAR_CONTINUATION") or 0),
        "CLEAR_CONTINUATION_share": float(c.get("CLEAR_CONTINUATION", 0) / n) if n else None,
        "trigger_semantic_valid_n": int(sum(1 for r in rows if r.get("trigger_semantically_valid"))),
        "trigger_semantic_valid_share": float(sum(1 for r in rows if r.get("trigger_semantically_valid")) / n) if n else None,
        "rows": rows,
        "method": "chart-visible causal geometry only; no future path at labeling time",
    }


def reveal_path(labels: dict[str, Any], events_by_key: dict[tuple, dict[str, Any]]) -> dict[str, Any]:
    rows = []
    for r in list(labels.get("rows") or []):
        ev = events_by_key.get((str(r.get("symbol")), str(r.get("date")), str(r.get("direction"))))
        rows.append(
            {
                **r,
                "r10_bps": None if ev is None else ev.get("r10_bps"),
                "or_accept_fail": None if ev is None else ev.get("or_accept_fail"),
                "fp_plus_1_0R_before_fail": None if ev is None else ev.get("fp_plus_1_0R_before_fail"),
            }
        )
    by = {}
    for sem in sorted({str(r.get("semantic")) for r in rows}):
        vs = [r for r in rows if r.get("semantic") == sem]
        xs = [float(r["r10_bps"]) for r in vs if _finite(r.get("r10_bps"))]
        by[sem] = {
            "n": len(vs),
            "r10_mean": float(sum(xs) / len(xs)) if xs else None,
            "or_fail_share": float(sum(1 for r in vs if r.get("or_accept_fail")) / len(vs)) if vs else None,
        }
    return {"after_reveal": by, "rows": rows}
