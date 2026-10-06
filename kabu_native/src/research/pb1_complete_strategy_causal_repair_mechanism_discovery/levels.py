"""Restore frozen numeric reference level without future prices."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_complete_strategy_economic_failure_decomposition.path import parse_identity


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f and f > 0


def restore_level(
    trade: dict[str, Any],
    *,
    or_high: Any,
    or_low: Any,
    prior_dailies: list[dict[str, Any]],
    rec: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ident = parse_identity(str(trade.get("execution_id") or ""))
    fam = str(ident.get("location_family") or "")
    sub = str(ident.get("location_subtype") or "")
    side = str(trade.get("side") or "")
    bull = side.lower() in {"bull", "long", "1"}
    source = None
    level = None
    token = sub.upper()
    if fam.startswith("B") or token.startswith("OR"):
        level = float(or_high) if bull and _finite(or_high) else (float(or_low) if (not bull) and _finite(or_low) else None)
        source = "OR_BOUNDARY"
    last = prior_dailies[-1] if prior_dailies else None
    if last:
        if token in {"PDC", "PRIOR_DAY_CLOSE"} or "PDC" in token:
            level = float(last["close"]) if _finite(last.get("close")) else level
            source = "PDC"
        elif "PDH" in token:
            level = float(last["high"]) if _finite(last.get("high")) else level
            source = "PDH"
        elif "PDL" in token:
            level = float(last["low"]) if _finite(last.get("low")) else level
            source = "PDL"
    if "D5H" in token:
        hs = [float(d["high"]) for d in prior_dailies[-5:] if _finite(d.get("high"))]
        if hs:
            level = float(max(hs))
            source = "D5H"
    if "D5L" in token:
        ls = [float(d["low"]) for d in prior_dailies[-5:] if _finite(d.get("low"))]
        if ls:
            level = float(min(ls))
            source = "D5L"
    if rec is not None and "VWAP" in token:
        parts = str(trade.get("execution_id") or "").split("|")
        loc_t = str(parts[8])[:5] if len(parts) > 8 else ""
        from research.pb1_complete_strategy_causal_repair_mechanism_discovery.bars import enrich_1m

        one = enrich_1m(rec)
        times = list(one.get("t") or [])
        if loc_t in times:
            i = times.index(loc_t)
            vw = float(one["vwap"][i]) if i < len(one["vwap"]) else None
            if _finite(vw):
                level = float(vw)
                source = "SESSION_VWAP_AT_LOCATION_MINT"
    identifiable = _finite(level)
    return {
        "location_family": fam,
        "location_subtype": sub,
        "level": float(level) if identifiable else None,
        "level_source": source if identifiable else None,
        "identifiable": bool(identifiable),
        "asf_status": "LEVEL_RESTORED" if identifiable else "ASF_EARLY_REPAIR_NOT_IDENTIFIABLE",
    }
