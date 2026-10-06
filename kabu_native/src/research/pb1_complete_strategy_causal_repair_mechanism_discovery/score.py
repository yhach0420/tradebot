"""Apply a causal event as next-open EXIT. Same rule for winners and losers."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1 import X1_TAX_BPS
from research.pb1_v4_complete_strategy_build_and_economic_validation.clocks import hhmm_to_min, next_open_after
from research.pb1_v4_complete_strategy_build_and_economic_validation.fill import apply_x1_tax, signed_gross
from research.pb1_v4_complete_strategy_economic_failure_decomposition.path import dir_bps


def _bps(side: str, entry: float, px: float) -> float | None:
    return dir_bps(side=side, entry=entry, px=px)


def v1_bps(trade: dict[str, Any]) -> dict[str, Any]:
    side = str(trade["side"])
    entry = float(trade["entry_px"])
    gross = _bps(side, entry, float(trade["exit_px"]))
    net = None if gross is None else float(gross) - float(X1_TAX_BPS)
    return {
        "v1_gross_bps": gross,
        "v1_net_bps": net,
        "v1_gross_yen": float(trade.get("gross_pnl_yen") or 0),
        "v1_net_yen": float(trade.get("net_pnl_yen") or 0),
        "v1_exit_t": str(trade.get("exit_t") or "")[:5],
        "v1_exit_px": float(trade.get("exit_px") or 0),
    }


def candidate_exit(trade: dict[str, Any], rec: dict[str, Any] | None, event_t: str | None) -> dict[str, Any]:
    base = v1_bps(trade)
    out = {
        **base,
        "fired": bool(event_t),
        "event_t": str(event_t)[:5] if event_t else None,
        "used_candidate": False,
        "candidate_exit_t": base["v1_exit_t"],
        "candidate_exit_px": base["v1_exit_px"],
        "cand_gross_bps": base["v1_gross_bps"],
        "cand_net_bps": base["v1_net_bps"],
        "cand_gross_yen": base["v1_gross_yen"],
        "cand_net_yen": base["v1_net_yen"],
        "delta_gross_bps": 0.0,
        "delta_net_bps": 0.0,
        "delta_net_yen": 0.0,
        "earlier": False,
    }
    if rec is None or not event_t:
        return out
    nxt = next_open_after(rec, after_t=str(event_t)[:5], allow_pm=True)
    if not nxt:
        return out
    v1_t = str(trade.get("exit_t") or "")[:5]
    if str(nxt["t"])[:5] >= v1_t:
        return out
    if hhmm_to_min(str(nxt["t"])) is None:
        return out
    side = str(trade["side"])
    entry = float(trade["entry_px"])
    px = float(nxt["px"])
    gross_yen = signed_gross(side=side, entry_px=entry, exit_px=px)
    tax = apply_x1_tax(gross_yen=gross_yen, entry_px=entry)
    gross_bps = _bps(side, entry, px)
    net_bps = None if gross_bps is None else float(gross_bps) - float(X1_TAX_BPS)
    out.update(
        {
            "used_candidate": True,
            "earlier": True,
            "candidate_exit_t": str(nxt["t"])[:5],
            "candidate_exit_px": px,
            "cand_gross_bps": gross_bps,
            "cand_net_bps": net_bps,
            "cand_gross_yen": float(gross_yen),
            "cand_net_yen": float(tax["net_pnl_yen"]),
            "delta_gross_bps": float(gross_bps or 0) - float(base["v1_gross_bps"] or 0),
            "delta_net_bps": float(net_bps or 0) - float(base["v1_net_bps"] or 0),
            "delta_net_yen": float(tax["net_pnl_yen"]) - float(base["v1_net_yen"] or 0),
        }
    )
    return out
