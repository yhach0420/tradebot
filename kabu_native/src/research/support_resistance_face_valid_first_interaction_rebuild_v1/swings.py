"""Causal swing-reversal confirmation. Pivot is used only after a later 0.75 ATR close-away."""
from __future__ import annotations

from typing import Any

from research.support_resistance_face_valid_first_interaction_rebuild_v1 import CONFIRM_ATR


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def new_swing_state() -> dict[str, Any]:
    return {"leg": "ANY", "ph": None, "pl": None}


def _pending(day: dict[str, Any], *, side: str) -> dict[str, Any]:
    return {
        "pivot_date": str(day["date"]),
        "pivot_price": float(day["high"] if side == "HIGH" else day["low"]),
        "volume": float(day.get("volume") or 0),
        "range": float(day.get("range") or 0),
    }


def _rx(
    *,
    symbol: str,
    role: str,
    pending: dict[str, Any],
    confirmation_date: str,
    available_from: str,
    atr: float,
    close: float,
) -> dict[str, Any]:
    px = float(pending["pivot_price"])
    move = (px - close) if role == "RESISTANCE" else (close - px)
    return {
        "symbol": symbol,
        "role": role,
        "pivot_date": pending["pivot_date"],
        "pivot_price": px,
        "price": px,
        "confirmation_date": confirmation_date,
        "available_from": available_from,
        "atr_at_confirm": atr,
        "confirm_atr_mult": CONFIRM_ATR,
        "move_away_atr": (move / atr) if atr > 0 else None,
        "volume": pending.get("volume"),
        "range": pending.get("range"),
        "future_pivot": False,
        "same_day_confirmation": False,
        "independent_swing_cycle": True,
    }


def step_swings(
    st: dict[str, Any],
    day: dict[str, Any],
    *,
    atr: float,
    next_session: str | None,
    symbol: str,
) -> list[dict[str, Any]]:
    """Confirm a pending extreme only on a later session after a 0.75 ATR close-away. Alternating cycles."""
    if not next_session or not _finite(atr) or atr <= 0:
        return []
    d = str(day["date"])
    h, l, c = float(day["high"]), float(day["low"]), float(day["close"])
    thr = float(CONFIRM_ATR) * float(atr)
    out: list[dict[str, Any]] = []
    leg = str(st.get("leg") or "ANY")

    if leg in ("ANY", "HIGH"):
        if st["ph"] is None or h >= float(st["ph"]["pivot_price"]):
            st["ph"] = _pending(day, side="HIGH")
    if leg in ("ANY", "LOW"):
        if st["pl"] is None or l <= float(st["pl"]["pivot_price"]):
            st["pl"] = _pending(day, side="LOW")

    cands: list[tuple[str, str]] = []
    ph, pl = st.get("ph"), st.get("pl")
    if ph and d > str(ph["pivot_date"]) and c <= float(ph["pivot_price"]) - thr:
        if leg in ("ANY", "HIGH"):
            cands.append(("HIGH", str(ph["pivot_date"])))
    if pl and d > str(pl["pivot_date"]) and c >= float(pl["pivot_price"]) + thr:
        if leg in ("ANY", "LOW"):
            cands.append(("LOW", str(pl["pivot_date"])))
    if not cands:
        return out
    cands.sort(key=lambda t: t[1])
    pick = cands[0][0]
    if pick == "HIGH":
        out.append(_rx(symbol=symbol, role="RESISTANCE", pending=ph, confirmation_date=d, available_from=str(next_session), atr=float(atr), close=c))
        st["leg"] = "LOW"
        st["ph"] = None
        st["pl"] = _pending(day, side="LOW")
    else:
        out.append(_rx(symbol=symbol, role="SUPPORT", pending=pl, confirmation_date=d, available_from=str(next_session), atr=float(atr), close=c))
        st["leg"] = "HIGH"
        st["pl"] = None
        st["ph"] = _pending(day, side="HIGH")
    return out
