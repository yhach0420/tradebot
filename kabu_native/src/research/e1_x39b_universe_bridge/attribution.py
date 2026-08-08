"""Added-symbol attribution and displacement analysis."""
from __future__ import annotations

from collections import defaultdict
from typing import Any


def added_symbol_attribution(
    *,
    added_symbol_days: list[dict[str, str]],
    bridge_events: list[dict],
    legacy_events: list[dict],
) -> dict[str, Any]:
    """
    Track AM-only symbol-days (not in old CANDIDATE_SYMBOL_POOL).
    No post-hoc removal.
    """
    added_set = {(r["date"], r["symbol"]) for r in added_symbol_days}
    bridge_by_key = defaultdict(list)
    for e in bridge_events:
        bridge_by_key[(e["date"], e["symbol"])].append(e)

    legacy_adm_by_clock: dict[tuple, list[str]] = defaultdict(list)
    for e in legacy_events:
        if e.get("admitted"):
            legacy_adm_by_clock[(e["date"], float(e["signal_time"]))].append(e["symbol"])

    bridge_adm_by_clock: dict[tuple, list[str]] = defaultdict(list)
    for e in bridge_events:
        if e.get("admitted"):
            bridge_adm_by_clock[(e["date"], float(e["signal_time"]))].append(e["symbol"])

    rows = []
    total_pnl = 0.0
    total_fills = 0
    total_admitted = 0
    displacements = []

    for date, symbol in sorted(added_set):
        evs = bridge_by_key.get((date, symbol), [])
        # if no events: planned but data unavailable / no aggressive-eligible signal
        feature_valid = 0
        scores = []
        ranks = []
        admitted = 0
        fills = 0
        pnl = 0.0
        for e in evs:
            # feature-valid if score finite
            sc = e.get("alloc_score")
            if sc is not None and sc == sc and sc != float("-inf"):
                feature_valid += 1
            if sc is not None:
                scores.append(float(sc) if sc == sc else float("-inf"))
            if e.get("admitted"):
                admitted += 1
                total_admitted += 1
                # displacement: who was admitted in legacy at same clock but not in bridge
                clock = (date, float(e["signal_time"]))
                lost = sorted(set(legacy_adm_by_clock.get(clock, [])) - set(bridge_adm_by_clock.get(clock, [])))
                gained_here = symbol in bridge_adm_by_clock.get(clock, [])
                if gained_here and lost:
                    displacements.append({
                        "date": date,
                        "signal_time": float(e["signal_time"]),
                        "added_symbol": symbol,
                        "displaced_old_candidates": lost,
                        "bridge_admitted": sorted(bridge_adm_by_clock.get(clock, [])),
                        "legacy_admitted": sorted(legacy_adm_by_clock.get(clock, [])),
                    })
            if e.get("accepted"):
                fills += 1
                total_fills += 1
            pnl += float(e.get("realized_pnl_yen") or 0.0)
        total_pnl += pnl
        rows.append({
            "date": date,
            "symbol": symbol,
            "anchors_evaluated": len(evs),
            "feature_valid_anchors": feature_valid,
            "scores_sample": scores[:5],
            "admitted": admitted,
            "fills": fills,
            "pnl_yen": pnl,
            "data_events_present": len(evs) > 0,
        })

    return {
        "added_symbol_day_n": len(added_set),
        "rows": rows,
        "total_pnl_yen": total_pnl,
        "total_fills": total_fills,
        "total_admitted": total_admitted,
        "displacements": displacements,
        "displacement_n": len(displacements),
        "post_hoc_removal": False,
    }
