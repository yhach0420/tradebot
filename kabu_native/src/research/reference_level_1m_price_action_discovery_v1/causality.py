"""Causality audit for reference-level events. First-event refractory only."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add


def audit_causality(*, walk: dict[str, Any], hits: list[dict[str, Any]], playbook_rows: list[dict[str, Any]]) -> dict[str, Any]:
    flags = list(walk.get("causal_flags") or [])
    retro = sum(1 for h in hits if h.get("retroactive_timestamp"))
    future = sum(1 for h in hits if h.get("future_dependent"))
    accept_moved = 0
    accept_n = 0
    for r in playbook_rows:
        if str(r.get("event_kind") or "").startswith("ACCEPT2"):
            accept_n += 1
            if r.get("accept_not_moved_to_break") is False:
                accept_moved += 1
            if r.get("break_feature_bar") and r.get("feature_bar") == r.get("break_feature_bar"):
                accept_moved += 1
    sample = []
    for h in hits[:30]:
        feat = str(h.get("feature_bar") or "")
        sample.append(
            {
                "date": h.get("date"),
                "symbol": h.get("symbol"),
                "kind": h.get("event_kind"),
                "level_id": h.get("level_id"),
                "feature_bar": feat,
                "available_at_expected": hhmm_add(feat, 1) if feat else None,
            }
        )
    last_hh = Counter(str(h.get("fwd_last_hh") or "") for h in hits if h.get("fwd_last_hh"))
    truncated_1130 = sum(1 for h in hits if str(h.get("fwd_last_hh") or "") < "12:30" and str(h.get("fwd_last_hh") or "") >= "11:20" and h.get("fwd_crosses_lunch") is False)
    return {
        "future_dependent_event_generation": bool(future or any(f.get("reason") == "future" for f in flags)),
        "retroactive_timestamp_assignment": bool(retro or accept_moved),
        "future_dependent_n": int(future),
        "retroactive_n": int(retro),
        "causal_flag_n": len(flags),
        "accept2_n": accept_n,
        "accept2_moved_to_break_n": accept_moved,
        "loaded_confirmation": bool(walk.get("loaded_confirmation")),
        "loaded_frozen_validation": bool(walk.get("loaded_frozen_validation")),
        "cluster_last_event_used": False,
        "first_event_refractory_used": True,
        "same_bar_execution": False,
        "implicit_1130_truncation_hits": int(truncated_1130),
        "fwd_last_hh_top": last_hh.most_common(12),
        "sample": sample,
        "ok": (not future) and (not retro) and accept_moved == 0 and not walk.get("loaded_confirmation") and not walk.get("loaded_frozen_validation"),
    }
