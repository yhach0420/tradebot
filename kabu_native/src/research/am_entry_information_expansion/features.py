"""Join frozen X14 expansion bundle. No subset search. No new construction."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.am_entry_information_expansion import X14_BUNDLE
from research.canonical_entry_performance_rebase.analyze import _f, row_key

HARVEST_REBOUND = "rebound_from_low_180s_bps"


def _x14_from_harvest(h: dict[str, Any]) -> dict[str, Any]:
    rebound = h.get("rebound_from_recent_low_bps")
    if rebound is None:
        rebound = h.get(HARVEST_REBOUND)
    return {
        "distance_from_vwap_bps": _f(h.get("distance_from_vwap_bps")),
        "rebound_from_recent_low_bps": _f(rebound),
        "volume_rate_60s": _f(h.get("volume_rate_60s")),
        "trading_value_delta_60s": _f(h.get("trading_value_delta_60s")),
        "volume_percentile_60s": _f(h.get("volume_percentile_60s")),
        "trading_value_percentile_180s": _f(h.get("trading_value_percentile_180s")),
    }


def load_harvest(feat_dir: Path, days: list[str]) -> tuple[list[dict[str, Any]], int]:
    harvest: list[dict[str, Any]] = []
    future_n = 0
    for day in days:
        fp = feat_dir / f"{day}_FEAT.json"
        if not fp.is_file():
            return [], -1
        body = json.loads(fp.read_text(encoding="utf-8"))
        if not (body.get("ok") and str(body.get("date") or "") == str(day)):
            return [], -1
        future_n += int(body.get("future_event_use_n") or 0)
        harvest.extend(list(body.get("rows") or []))
    return harvest, future_n


def join_x14(rows: list[dict[str, Any]], harvest: list[dict[str, Any]]) -> dict[str, Any]:
    by = {row_key(h): h for h in harvest}
    miss = 0
    future_n = 0
    complete = 0
    out = []
    for r in rows:
        rec = dict(r)
        h = by.get(row_key(r))
        if h is None:
            miss += 1
            for k in X14_BUNDLE:
                rec[k] = None
        else:
            future_n += int(h.get("future_event_use_n") or 0)
            rec.update(_x14_from_harvest(h))
            if all(_f(rec.get(k)) is not None for k in X14_BUNDLE):
                complete += 1
        out.append(rec)
    return {
        "rows": out,
        "JOIN_MISS_N": miss,
        "FUTURE_FEATURE_USE_N": future_n,
        "X14_COMPLETE_N": complete,
    }
