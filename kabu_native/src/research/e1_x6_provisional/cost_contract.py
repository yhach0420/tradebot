"""Canonical round-trip cost contract for E1_X6 research (single application).

Plan SoT: 往復5bpsを1回だけ適用.
Frozen E1_X5 econ(): cost = entry * LOT * COST_RATE  (COST_RATE=0.0005 => 5bps once).

FORBIDDEN: COST_RATE*2 / 10bps dual definition.
"""
from __future__ import annotations

from typing import Any, Optional

from small_paper.e1_x5_forward_shadow import COST_RATE as X5_COST_RATE
from small_paper.e1_x5_forward_shadow import LOT as X5_LOT
from small_paper.e1_x5_forward_shadow import econ as x5_econ

# Single canonical definition (must match plan + frozen E1_X5)
ROUNDTRIP_COST_BPS = 5.0
COST_RATE = float(X5_COST_RATE)  # 0.0005
LOT = int(X5_LOT)  # 100


class CostContractMismatch(RuntimeError):
    """Frozen E1_X5 economics disagree with the plan 5bps-once contract."""


def yen_roundtrip_cost(price: float) -> float:
    """Total yen cost for LOT shares at `price` (round-trip 5bps, applied once)."""
    return float(price) * LOT * COST_RATE


def post_cost_label_bps(mid0: float, mid1: float) -> float:
    """Flat-price return post-cost label in bps: (mid1/mid0-1)*10000 - 5.0."""
    if mid0 is None or float(mid0) <= 0:
        raise ValueError("mid0 must be positive")
    return (float(mid1) / float(mid0) - 1.0) * 10000.0 - ROUNDTRIP_COST_BPS


def net_pnl_yen(entry: float, exit_px: float) -> dict[str, float]:
    """Same yen economics as frozen E1_X5 econ() — single shared function path."""
    return dict(x5_econ(float(entry), float(exit_px)))


def verify_frozen_e1_x5_cost_contract() -> dict[str, Any]:
    """Assert frozen E1_X5 matches plan; stop with COST_CONTRACT_MISMATCH if not.

    Reference cases from plan / user lock:
      - flat mid move 0 => label = -5.0 bps
      - price 1000, LOT 100, zero move => cost = 50 yen
    """
    errors: list[str] = []

    if abs(COST_RATE - 0.0005) > 1e-15:
        errors.append(f"COST_RATE={COST_RATE} expected 0.0005")
    if LOT != 100:
        errors.append(f"LOT={LOT} expected 100")
    if abs(ROUNDTRIP_COST_BPS - 5.0) > 1e-12:
        errors.append(f"ROUNDTRIP_COST_BPS={ROUNDTRIP_COST_BPS} expected 5.0")

    # Implied bps from COST_RATE must be 5.0 once (NOT *2)
    implied_bps = COST_RATE * 10000.0
    if abs(implied_bps - ROUNDTRIP_COST_BPS) > 1e-9:
        errors.append(
            f"implied_bps_from_COST_RATE={implied_bps} != ROUNDTRIP_COST_BPS={ROUNDTRIP_COST_BPS} "
            "(would indicate dual 10bps definition)"
        )

    # Forbidden dual formula check (documentation trap from prior builder)
    dual_bps = COST_RATE * 2 * 10000.0
    if abs(dual_bps - ROUNDTRIP_COST_BPS) < 1e-9:
        errors.append("ROUNDTRIP_COST_BPS equals COST_RATE*2*10000 (10bps dual) — forbidden")

    cost_1000 = yen_roundtrip_cost(1000.0)
    if abs(cost_1000 - 50.0) > 1e-9:
        errors.append(f"yen_roundtrip_cost(1000)={cost_1000} expected 50.0")

    label_flat = post_cost_label_bps(1000.0, 1000.0)
    if abs(label_flat - (-5.0)) > 1e-9:
        errors.append(f"post_cost_label_bps(flat)={label_flat} expected -5.0")

    # Frozen X5 econ must match (no inventing a different cost)
    x5 = x5_econ(1000.0, 1000.0)
    if abs(float(x5["cost_yen_100"]) - 50.0) > 1e-9:
        errors.append(
            f"frozen e1_x5 econ cost_yen_100={x5['cost_yen_100']} != 50.0 -> COST_CONTRACT_MISMATCH"
        )
    if abs(float(x5["net_pnl_yen_100"]) - (-50.0)) > 1e-9:
        errors.append(
            f"frozen e1_x5 econ net_pnl_yen_100={x5['net_pnl_yen_100']} != -50.0 -> COST_CONTRACT_MISMATCH"
        )
    if abs(float(x5["net_bps"]) - (-5.0)) > 1e-9:
        errors.append(
            f"frozen e1_x5 econ net_bps={x5['net_bps']} != -5.0 -> COST_CONTRACT_MISMATCH"
        )

    shared = net_pnl_yen(1000.0, 1000.0)
    if shared != x5 and (
        abs(shared["cost_yen_100"] - x5["cost_yen_100"]) > 1e-12
        or abs(shared["net_pnl_yen_100"] - x5["net_pnl_yen_100"]) > 1e-12
    ):
        errors.append("net_pnl_yen diverged from frozen x5_econ")

    report = {
        "status": "COST_CONTRACT_OK" if not errors else "COST_CONTRACT_MISMATCH",
        "ROUNDTRIP_COST_BPS": ROUNDTRIP_COST_BPS,
        "COST_RATE": COST_RATE,
        "LOT": LOT,
        "yen_cost_at_1000": cost_1000,
        "label_flat_bps": label_flat,
        "frozen_e1_x5_econ_flat": x5,
        "errors": errors,
    }
    if errors:
        raise CostContractMismatch(
            "COST_CONTRACT_MISMATCH: " + "; ".join(errors) + f" | detail={report}"
        )
    return report
