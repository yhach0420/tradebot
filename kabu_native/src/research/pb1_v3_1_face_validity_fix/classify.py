"""Blinded human labels for independent V3.1 face sample. Not machine self-match."""
from __future__ import annotations

from collections import Counter
from typing import Any


def _lab(
    pattern: str,
    *,
    drive: bool,
    breakout: bool,
    leave: bool,
    r_gt_noise: bool,
    first_pb: bool,
    defended: bool,
    room: bool,
    reclaim: bool,
    note: str,
) -> dict[str, Any]:
    return {
        "pattern": pattern,
        "Q_directional_opening_drive": drive,
        "Q_genuine_breakout": breakout,
        "Q_meaningful_leave": leave,
        "Q_structural_r_gt_1m_noise": r_gt_noise,
        "Q_real_first_pullback": first_pb,
        "Q_coherent_defense": defended,
        "Q_visible_room": room,
        "Q_reclaim_reasonable": reclaim,
        "note": note,
        "future_used": False,
    }


# Filled after actual inspection of independent unseen charts only.
HUMAN_LABELS: dict[int, dict[str, Any]] = {}


def apply_labels(sample: list[dict[str, Any]]) -> dict[str, Any]:
    labeled: list[dict[str, Any]] = []
    missing = 0
    for ev in sample:
        sid = int(ev.get("sample_id") or 0)
        lab = dict(HUMAN_LABELS.get(sid) or {})
        reviewed = bool(lab)
        if not reviewed:
            missing += 1
        labeled.append(
            {
                "sample_id": sid,
                "symbol": ev.get("symbol"),
                "date": ev.get("date"),
                "block": ev.get("block"),
                "direction": ev.get("direction"),
                "structural_route": ev.get("structural_route"),
                "open_state": ev.get("open_state"),
                "NORMAL_1M_RANGE": ev.get("NORMAL_1M_RANGE"),
                "planned_R": ev.get("planned_R"),
                "planned_R_over_NORMAL_1M_RANGE": ev.get("planned_R_over_NORMAL_1M_RANGE"),
                "max_away_over_NORMAL_1M_RANGE": ev.get("max_away_over_NORMAL_1M_RANGE"),
                "reclaim_move_over_NORMAL_1M_RANGE": ev.get("reclaim_move_over_NORMAL_1M_RANGE"),
                "chart": f"sample_{sid:02d}_{ev.get('symbol')}_{ev.get('date')}_{ev.get('direction')}.png",
                "reviewed": reviewed,
                "machine_self_match_used": False,
                "independent_face_sample": True,
                **lab,
            }
        )
    n = sum(1 for r in labeled if r.get("reviewed"))
    patterns = Counter(str(r.get("pattern")) for r in labeled if r.get("reviewed"))
    clear = int(patterns.get("CLEAR_CONTINUATION") or 0)
    drive_ag = sum(1 for r in labeled if r.get("reviewed") and r.get("Q_directional_opening_drive") is True)
    route_ag = sum(
        1
        for r in labeled
        if r.get("reviewed") and r.get("Q_visible_room") is True and str(r.get("structural_route")) == "STRUCTURAL_ROUTE_CLEAR"
    )
    reclaim_ok = sum(1 for r in labeled if r.get("reviewed") and r.get("Q_reclaim_reasonable") is True)
    return {
        "reviewed_n": n,
        "sample_n": len(labeled),
        "missing_label_n": missing,
        "actual_manual_blinded_review": bool(n == len(labeled) and missing == 0 and n > 0),
        "machine_self_match_used": False,
        "independent_face_sample": bool(len(labeled) > 0),
        "CLEAR_CONTINUATION": clear,
        "QUESTIONABLE": int(patterns.get("QUESTIONABLE") or 0),
        "NOT_CONTINUATION": int(patterns.get("NOT_CONTINUATION") or 0),
        "clear_share": float(clear / n) if n else None,
        "questionable_share": float((patterns.get("QUESTIONABLE") or 0) / n) if n else None,
        "not_share": float((patterns.get("NOT_CONTINUATION") or 0) / n) if n else None,
        "opening_drive_human_agreement": float(drive_ag / n) if n else None,
        "structural_route_human_agreement": float(route_ag / n) if n else None,
        "valid_reclaim_share": float(reclaim_ok / n) if n else None,
        "rows": labeled,
        "future_hidden": True,
        "outcome_used": False,
    }
