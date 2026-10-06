"""Thesis-aligned EXIT: structural target, invalidation next-open, 15:20. No EMA/VWAP/time-stop."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import in_lunch
from research.support_resistance_mechanism_to_complete_strategy_v1 import SESSION_FLAT, SHARES, X1_STRESS_BPS
from research.support_resistance_matched_separation_not_a_strategy_v1.outcomes import next_entry_i


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def next_avail_i(rec: dict[str, Any], i: int) -> int | None:
    """Next non-lunch bar, including 15:20 (slot-release / flatten)."""
    n = int(rec["n"])
    for j in range(int(i) + 1, n):
        t = rec["t"][j]
        if in_lunch(t):
            continue
        if _finite(rec["o"][j]) and float(rec["o"][j]) > 0:
            return j
    return None


def _bps(move: float, px: float) -> float:
    return float(move) / float(px) * 10_000.0


def simulate_path(
    rec: dict[str, Any],
    *,
    entry_i: int,
    side: str,
    inv_lo: float,
    inv_hi: float,
    target_price: float | None,
) -> dict[str, Any]:
    n = int(rec["n"])
    ei = int(entry_i)
    if ei < 0 or ei >= n or not _finite(rec["o"][ei]) or float(rec["o"][ei]) <= 0:
        return {"ok": False, "reason": "no_entry"}
    px = float(rec["o"][ei])
    long = str(side) == "LONG"
    pending_inv = False
    inv_k = ei
    ambiguous = False
    mfe_bps = 0.0
    mae_bps = 0.0
    hold = 0

    def _pack(
        *,
        k: int,
        exit_px: float,
        reason: str,
        fill_kind: str,
        decision_k: int,
        release_k: int,
        target_filled: bool,
    ) -> dict[str, Any]:
        signed = (float(exit_px) - px) if long else (px - float(exit_px))
        yen = signed * float(SHARES)
        bps = _bps(signed, px)
        stress = yen - (float(X1_STRESS_BPS) / 10_000.0) * px * float(SHARES)
        giveback = float(mfe_bps) - float(bps) if mfe_bps > 0 else 0.0
        return {
            "ok": True,
            "entry_i": ei,
            "entry_t": rec["t"][ei],
            "entry_price": px,
            "exit_i": int(k),
            "exit_t": rec["t"][k],
            "exit_price": float(exit_px),
            "exit_reason": reason,
            "exit_fill_kind": fill_kind,
            "exit_decision_i": int(decision_k),
            "exit_decision_t": rec["t"][decision_k],
            "slot_release_i": int(release_k),
            "slot_release_t": rec["t"][release_k],
            "hold_min": int(hold),
            "mfe_bps": float(mfe_bps),
            "mae_bps": float(mae_bps),
            "realized_bps": float(bps),
            "mfe_realized_giveback_bps": float(giveback),
            "gross_yen": float(yen),
            "stress_yen": float(stress),
            "target_filled": bool(target_filled),
            "target_invalidation_ambiguous": bool(ambiguous),
            "exit_retroactive": False,
        }

    for k in range(ei, n):
        t = rec["t"][k]
        if in_lunch(t):
            continue
        o, h, l, c = rec["o"][k], rec["h"][k], rec["l"][k], rec["c"][k]
        if not (_finite(o) and _finite(h) and _finite(l) and _finite(c) and float(o) > 0):
            continue
        o, h, l, c = float(o), float(h), float(l), float(c)

        if str(t) >= SESSION_FLAT:
            if pending_inv:
                return _pack(
                    k=k,
                    exit_px=o,
                    reason="invalidation",
                    fill_kind="next_open",
                    decision_k=int(inv_k),
                    release_k=k,
                    target_filled=False,
                )
            return _pack(
                k=k,
                exit_px=o,
                reason="session_close",
                fill_kind="session_open",
                decision_k=k,
                release_k=k,
                target_filled=False,
            )

        if pending_inv:
            return _pack(
                k=k,
                exit_px=o,
                reason="invalidation",
                fill_kind="next_open",
                decision_k=int(inv_k),
                release_k=k,
                target_filled=False,
            )

        if long:
            mfe_bps = max(mfe_bps, _bps(h - px, px))
            mae_bps = min(mae_bps, _bps(l - px, px))
        else:
            mfe_bps = max(mfe_bps, _bps(px - l, px))
            mae_bps = min(mae_bps, _bps(px - h, px))

        inv = (c < float(inv_lo)) if long else (c > float(inv_hi))
        tgt = False
        if target_price is not None and k > ei and _finite(target_price):
            tgt = (h >= float(target_price)) if long else (l <= float(target_price))

        if tgt and inv:
            ambiguous = True
            pending_inv = True
            inv_k = k
            hold += 1
            continue
        if tgt:
            rel = next_avail_i(rec, k)
            if rel is None:
                rel = k
            return _pack(
                k=k,
                exit_px=float(target_price),
                reason="structural_target",
                fill_kind="historical_touch",
                decision_k=k,
                release_k=int(rel),
                target_filled=True,
            )
        if inv:
            pending_inv = True
            inv_k = k
        hold += 1

    last = n - 1
    while last >= ei and (in_lunch(rec["t"][last]) or not _finite(rec["c"][last])):
        last -= 1
    if last < ei:
        return {"ok": False, "reason": "no_exit_bar"}
    px_last = rec["c"][last] if pending_inv else rec["c"][last]
    reason = "invalidation" if pending_inv else "session_close"
    return _pack(
        k=last,
        exit_px=float(px_last),
        reason=reason,
        fill_kind="last_bar_close",
        decision_k=last,
        release_k=last,
        target_filled=False,
    )


def attach_exit(signal: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
    path = simulate_path(
        rec,
        entry_i=int(signal["entry_i"]),
        side=str(signal["side"]),
        inv_lo=float(signal["inv_lo"]),
        inv_hi=float(signal["inv_hi"]),
        target_price=signal.get("target_price"),
    )
    out = dict(signal)
    out.update(path)
    same = bool(int(signal["entry_i"]) == int(signal["decision_i"]))
    out["same_bar_entry"] = same
    out["entry_eligible_bar"] = rec["t"][int(signal["entry_i"])]
    out["entry_time"] = rec["t"][int(signal["entry_i"])]
    out["decision_available_at"] = rec["t"][int(signal["entry_i"])]
    if path.get("ok"):
        out["exit_execution_time"] = path.get("exit_t")
        out["exit_decision_time"] = path.get("exit_decision_t")
        out["invalidation_reason"] = "thesis_close_through" if path.get("exit_reason") == "invalidation" else None
        out["attribution"] = _attribution(path)
    return out


def _attribution(path: dict[str, Any]) -> str:
    reason = str(path.get("exit_reason") or "")
    mfe = float(path.get("mfe_bps") or 0.0)
    if reason == "structural_target":
        return "target_reached"
    if reason == "session_close":
        return "session_close"
    if reason == "invalidation":
        if mfe < 10:
            return "never_progressed"
        if mfe < 40:
            return "small_progress_then_invalidated"
        return "large_excursion_then_invalidated"
    return reason or "other"


def a1_fill_path(
    rec: dict[str, Any],
    *,
    fill_i: int,
    fill_price: float,
    side: str,
    inv_lo: float,
    inv_hi: float,
    target_price: float | None,
) -> dict[str, Any]:
    """Passive diagnostic: filled on first-test bar at limit. Target cannot fill on fill bar."""
    path = simulate_path(
        rec,
        entry_i=int(fill_i),
        side=side,
        inv_lo=inv_lo,
        inv_hi=inv_hi,
        target_price=target_price,
    )
    if not path.get("ok"):
        return path
    px = float(fill_price)
    long = str(side) == "LONG"
    signed = (float(path["exit_price"]) - px) if long else (px - float(path["exit_price"]))
    yen = signed * float(SHARES)
    bps = _bps(signed, px)
    stress = yen - (float(X1_STRESS_BPS) / 10_000.0) * px * float(SHARES)
    path["entry_price"] = px
    path["entry_fill_kind"] = "historical_passive_limit_approx"
    path["gross_yen"] = float(yen)
    path["stress_yen"] = float(stress)
    path["realized_bps"] = float(bps)
    path["classification"] = "PASSIVE_FILL_NOT_ACTUAL_PROVEN"
    path["actual_fill_proven"] = False
    return path


__all__ = ["simulate_path", "attach_exit", "next_avail_i", "next_entry_i", "a1_fill_path"]
