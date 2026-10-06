"""Materialize ALL Discovery-unseen V3.2 events. Symbol-date overlap vs exclusion manifest."""
from __future__ import annotations

import json
from typing import Any

from research.pb1_v3_2_discovery_unseen_face_verify.isolation import V32_CACHE, V32_OUT


def _sd(e: dict[str, Any]) -> tuple[str, str]:
    return (str(e.get("symbol")), str(e.get("date")))


def _slim(e: dict[str, Any], *, sample_id: int) -> dict[str, Any]:
    route = e.get("route") if isinstance(e.get("route"), dict) else {}
    return {
        "sample_id": sample_id,
        "symbol": str(e.get("symbol")),
        "date": str(e.get("date")),
        "block": e.get("block"),
        "direction": str(e.get("direction")),
        "DIR": e.get("DIR"),
        "trigger_time": e.get("trigger_t"),
        "entry_time": e.get("entry_t"),
        "trigger_t": e.get("trigger_t"),
        "entry_t": e.get("entry_t"),
        "break_t": e.get("break_t"),
        "retest_t": e.get("retest_t"),
        "auction": e.get("auction"),
        "open_state": e.get("open_state"),
        "or_close_loc": e.get("or_close_loc"),
        "trigger_primary": e.get("trigger_primary"),
        "structural_route": e.get("structural_route"),
        "zone_class": e.get("zone_class"),
        "defended_level_type": e.get("defended_level_type"),
        "planned_R": e.get("planned_R"),
        "NORMAL_1M_RANGE": e.get("NORMAL_1M_RANGE"),
        "planned_R_over_NORMAL_1M_RANGE": e.get("planned_R_over_NORMAL_1M_RANGE"),
        "trigger_dir_close_loc": e.get("trigger_dir_close_loc"),
        "trigger_body_over_NORMAL_1M_RANGE": e.get("trigger_body_over_NORMAL_1M_RANGE"),
        "trigger_range_over_NORMAL_1M_RANGE": e.get("trigger_range_over_NORMAL_1M_RANGE"),
        "expand_vs_retest": e.get("expand_vs_retest"),
        "reclaim_move_over_NORMAL_1M_RANGE": e.get("reclaim_move_over_NORMAL_1M_RANGE"),
        "reaccel_reason": e.get("reaccel_reason"),
        "nearest_opposing": (route or {}).get("nearest") or e.get("nearest_opp_at_break"),
        "future_hidden": True,
        "prior_sample_reused": False,
        "independent_face_sample": True,
        "sampled": False,
        "balanced": False,
        "cherry_picked": False,
    }


def load_manifest_symbol_dates() -> set[tuple[str, str]]:
    path = V32_OUT / "face_review_exclusion_manifest.json"
    if not path.is_file():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    out: set[tuple[str, str]] = set()
    for raw in list(payload.get("face_review_exclusion_keys") or []):
        parts = str(raw).split("|")
        if len(parts) >= 2:
            out.add((parts[0], parts[1]))
    return out


def bind_ban_symbol_dates(bind: dict[str, Any]) -> set[tuple[str, str]]:
    ban: set[tuple[str, str]] = set()
    for a, b, _c in set(bind.get("v1_dev_keys") or set()):
        ban.add((str(a), str(b)))
    for name in ("v2_verify_keys", "trigger_rca96_keys", "structure_rca96_keys", "v3_face_keys"):
        for tup in bind.get(name) or set():
            if len(tup) >= 2:
                ban.add((str(tup[0]), str(tup[1])))
    ban |= set(bind.get("v3_face_symbol_dates") or set())
    return ban


def materialize_unseen(bind: dict[str, Any]) -> dict[str, Any]:
    walked = json.loads((V32_CACHE / "walked.json").read_text(encoding="utf-8"))
    events = list(walked.get("events") or [])
    ban = bind_ban_symbol_dates(bind)
    reported = [e for e in events if _sd(e) not in ban]
    manifest_sd = load_manifest_symbol_dates()
    overlap = [e for e in reported if _sd(e) in manifest_sd]
    keep = [e for e in reported if _sd(e) not in manifest_sd]
    keep = sorted(keep, key=lambda e: (str(e.get("date")), str(e.get("symbol")), str(e.get("direction")), str(e.get("trigger_t"))))
    sample = []
    for i, e in enumerate(keep, start=1):
        row = dict(e)
        slim = _slim(e, sample_id=i)
        row.update(slim)
        sample.append(row)
    return {
        "ok": True,
        "reported_discovery_unseen_n": len(reported),
        "materialized_unseen_n": len(reported),
        "manifest_symbol_date_n": len(manifest_sd),
        "overlap_n": len(overlap),
        "overlap_keys": [f"{e.get('symbol')}|{e.get('date')}" for e in overlap],
        "remaining_unseen_n": len(sample),
        "reviewed_universe_n": len(sample),
        "all_remaining_used": True,
        "sampled": False,
        "balanced": False,
        "cherry_picked": False,
        "comparison_grain": "SYMBOL_DATE",
        "sample": sample,
        "walked_setup_n": len(events),
        "same_bar_entry_n": int(walked.get("same_bar_entry_n") or 0),
        "failed_push_leak_n": int(walked.get("failed_push_leak_n") or 0),
        "risk_invalid_n": int(walked.get("risk_invalid_n") or 0),
        "failed_push_archive_n": len(list(walked.get("failed_push_archive") or [])),
    }
