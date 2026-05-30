"""
Phase209: Trading value sweet-band shadow gate — logging only.

Flags accepts in TV 100億–1000億 band (1e10 <= TV < 1e11).
Does NOT affect entry decisions or hard-reject any candidate.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

# Fixed band thresholds (Phase206–209; not tuned per session)
TV_LT = 1e8
TV_MID_HI = 1e10
TV_SWEET_LO = 1e10
TV_SWEET_HI = 1e11

SHADOW_FIELD_KEYS = (
    "trading_value_band",
    "tv_sweet_band_flag",
)

SUMMARY_FIELD_KEYS = (
    "sweet_band_trade_count",
    "sweet_band_pf",
    "sweet_band_total_pnl",
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def classify_trading_value_band(tv: Optional[float]) -> str:
    if tv is None or tv <= 0:
        return "missing"
    if tv < TV_LT:
        return "lt_1e8"
    if tv < TV_MID_HI:
        return "1e8_1e10"
    if tv < TV_SWEET_HI:
        return "1e10_1e11"
    return "ge_1e11"


def is_sweet_band(tv: Optional[float]) -> bool:
    return tv is not None and TV_SWEET_LO <= tv < TV_SWEET_HI


def compute_trading_value_shadow_fields(trade: Mapping[str, Any]) -> dict[str, Any]:
    tv = _float(trade.get("trading_value"))
    band = classify_trading_value_band(tv)
    return {
        "trading_value_band": band,
        "tv_sweet_band_flag": band == "1e10_1e11",
    }


def _pf(pnls: Sequence[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return wins / gl


def pnl_map_from_events(events: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], float]:
    out: dict[tuple[str, str], float] = {}
    for ev in events:
        if ev.get("event_type") != "observer_exit":
            continue
        pnl = _float(ev.get("pnl_pct"))
        key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))
        if pnl is not None and key[1]:
            out[key] = pnl
    return out


def finalize_session_trading_value_shadow(
    accepted_rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assign shadow band fields and return summary metrics for small_paper_summary.json."""
    for row in accepted_rows:
        row.update(compute_trading_value_shadow_fields(row))
    for ev in events:
        if ev.get("event_type") != "accepted":
            continue
        ev.update(compute_trading_value_shadow_fields(ev))

    pnl_by_key = pnl_map_from_events(events)
    sweet_pnls: list[float] = []
    for row in accepted_rows:
        if not row.get("tv_sweet_band_flag"):
            continue
        key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
        pnl = pnl_by_key.get(key)
        if pnl is not None:
            sweet_pnls.append(float(pnl))

    pf = _pf(sweet_pnls)
    return {
        "trading_value_shadow_gate_enabled": True,
        "sweet_band_trade_count": len(sweet_pnls),
        "sweet_band_pf": round(pf, 4) if pf is not None and pf != float("inf") else pf,
        "sweet_band_total_pnl": round(sum(sweet_pnls), 4) if sweet_pnls else 0.0,
    }
