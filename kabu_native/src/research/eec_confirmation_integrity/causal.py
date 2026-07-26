"""A1 confirmation causal audit + first-in-episode confirmation."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Optional, Sequence

from research.eec_confirmation_integrity.constants import (
    ASK_QTY_MIN,
    FROZEN_NOISE,
    HORIZON_SEC,
    MAX_CONFIRM_SEC,
)
from research.eec_confirmation_integrity.expiry import (
    find_episode_expiry,
    session_close_at,
    session_of,
)
from research.eec_noise_hysteresis.confirm import confirm_entry
from research.eec_noise_hysteresis.noise import compute_noise_band, tick_size
from research.eec_noise_hysteresis.path_util import path_with_lookback
from research.entry_exit_contract.contract import EntryContract
from research.entry_exit_contract.exits import simulate_matched_exit
from research.pbv2_zero_base_revalidation.util import pnl_5bps
from research.price_flow_exit.path_mfe import PathBar
from research.volume_confirmed_impulse_entry.push_loader import PushTick


def ask_status(b: PathBar) -> str:
    if b.ask is None or b.ask <= 0:
        return "NOT_EVALUABLE_ASK_MISSING"
    if b.bid is not None and float(b.ask) <= float(b.bid):
        return "NOT_EVALUABLE_ASK_CROSSED"
    if b.ask_qty is None:
        return "NOT_EVALUABLE_ASKQTY_MISSING"
    if float(b.ask_qty) < ASK_QTY_MIN:
        return "NOT_EVALUABLE_ASKQTY_LT_100"
    return "OK"


def _ask_ok(b: PathBar) -> bool:
    return ask_status(b) == "OK"


def first_n1_price_confirm(
    c: EntryContract,
    path: Sequence[PathBar],
    *,
    expire_at: datetime,
    noise: dict[str, float] = FROZEN_NOISE,
) -> Optional[dict[str, Any]]:
    """First N1 price persist before expire (timing/causal layer; no Ask gate)."""
    reclaim = float(c.levels["reclaim_level"])
    above = 0
    for i, b in enumerate(path):
        if b.t < c.entry_time:
            continue
        if b.t > expire_at:
            break
        nb = compute_noise_band(
            path, i, tick_mult=noise["tick_mult"], range_mult=noise["range_mult"], spread_mult=noise["spread_mult"]
        )
        if not nb["ok"]:
            above = 0
            continue
        band = float(nb["noise_band"])
        if b.px > reclaim + band:
            above += 1
        else:
            above = 0
        if above < 2:
            continue
        return {
            "i": i,
            "t": b.t,
            "px": float(b.px),
            "ask": float(b.ask) if b.ask is not None else None,
            "bid": float(b.bid) if b.bid is not None else None,
            "ask_qty": float(b.ask_qty) if b.ask_qty is not None else None,
            "ask_status": ask_status(b),
            "noise_band": band,
            "delay_sec": (b.t - c.entry_time).total_seconds(),
        }
    return None


def _new_setup_before(c: EntryContract, peers: Sequence[EntryContract], confirm_t: datetime) -> bool:
    pl0 = float(c.levels["pullback_low"])
    rl0 = float(c.levels["reclaim_level"])
    for p in peers:
        if p.setup_id == c.setup_id or p.symbol != c.symbol or p.day != c.day:
            continue
        if not (c.entry_time < p.entry_time <= confirm_t):
            continue
        pl1 = float(p.levels["pullback_low"])
        rl1 = float(p.levels["reclaim_level"])
        if abs(pl1 - pl0) / max(pl0, 1e-9) > 0.0025 or abs(rl1 - rl0) / max(rl0, 1e-9) > 0.0035:
            return True
    return False


def audit_candidate(
    c: EntryContract,
    ticks: Sequence[PushTick],
    *,
    peers: Sequence[EntryContract],
    noise: dict[str, float] = FROZEN_NOISE,
) -> dict[str, Any]:
    path, entry_i = path_with_lookback(ticks, c.entry_time)
    if not path:
        return {
            "setup_id": c.setup_id,
            "episode_id": c.episode_id,
            "day": c.day,
            "symbol": c.symbol,
            "causal_ok": False,
            "execution_ok": False,
            "candidate_expired_reason": "EMPTY_PATH",
            "confirmed_raw": False,
            "confirmed_strict": False,
        }

    exp = find_episode_expiry(c, path, entry_i=entry_i)
    conf_raw = confirm_entry(
        c, path, mode="N1", tick_mult=noise["tick_mult"], range_mult=noise["range_mult"], spread_mult=noise["spread_mult"]
    )
    price_conf = first_n1_price_confirm(c, path, expire_at=exp.t, noise=noise)

    delay_raw = conf_raw.delay_sec if conf_raw.confirmed else None
    confirm_t = price_conf["t"] if price_conf else (conf_raw.entry_time if conf_raw.confirmed else None)
    delay_s = price_conf["delay_sec"] if price_conf else delay_raw
    confirm_sess = session_of(confirm_t) if confirm_t else None
    same_session = bool(confirm_sess == c.session) if confirm_sess else False

    flags = {
        "confirmation_before_episode_end": bool(price_conf and price_conf["t"] <= exp.t),
        "confirmation_before_invalidation": bool(price_conf and price_conf["t"] <= exp.t),
        "confirmation_after_new_pullback": False,
        "confirmation_after_new_reclaim_setup": False,
        "confirmation_after_data_gap": False,
        "confirmation_after_refresh": False,
        "confirmation_after_session_break": False,
    }

    reject_reason = None
    same_episode = True
    if not price_conf:
        if conf_raw.confirmed:
            d = float(conf_raw.delay_sec or 0)
            if d > MAX_CONFIRM_SEC or d > float(c.expected_horizon_sec):
                reject_reason = "late_confirmation_gt_180"
            elif session_of(conf_raw.entry_time) != c.session:
                reject_reason = "cross_session"
                flags["confirmation_after_session_break"] = True
            elif conf_raw.entry_time > exp.t:
                reject_reason = f"after_expiry:{exp.reason}"
                if exp.reason == "pullback_low_break":
                    flags["confirmation_after_new_pullback"] = True
                if exp.reason == "session_break":
                    flags["confirmation_after_session_break"] = True
                if exp.reason in ("data_gap", "data_gap_or_refresh"):
                    flags["confirmation_after_data_gap"] = True
                    flags["confirmation_after_refresh"] = exp.reason == "data_gap_or_refresh"
            else:
                reject_reason = "no_price_persist_before_expiry"
        else:
            reject_reason = "no_confirmation"
    else:
        d = float(price_conf["delay_sec"])
        if d > MAX_CONFIRM_SEC or d > float(c.expected_horizon_sec):
            reject_reason = "late_confirmation_gt_180"
        elif not same_session:
            reject_reason = "cross_session"
            flags["confirmation_after_session_break"] = True
        elif confirm_t >= session_close_at(c.entry_time):
            reject_reason = "across_session_close"
        elif _new_setup_before(c, peers, confirm_t):
            reject_reason = "new_reclaim_setup"
            flags["confirmation_after_new_reclaim_setup"] = True
            same_episode = False
        else:
            reject_reason = None

    causal_ok = reject_reason is None and price_conf is not None
    ask_st = price_conf["ask_status"] if price_conf else None
    execution_ok = bool(causal_ok and ask_st == "OK")

    # v3 raw entry realism probe
    v3_crossed = False
    if conf_raw.confirmed:
        for b in path:
            if b.t >= conf_raw.entry_time:
                v3_crossed = b.ask is not None and b.bid is not None and float(b.ask) <= float(b.bid)
                break

    row = {
        "setup_id": c.setup_id,
        "episode_id": c.episode_id,
        "day": c.day,
        "symbol": c.symbol,
        "session": c.session,
        "original_candidate_time": c.entry_time.isoformat(),
        "confirmation_time": confirm_t.isoformat() if confirm_t else None,
        "confirmation_delay_sec": delay_s,
        "expected_horizon_sec": float(c.expected_horizon_sec),
        "original_episode_start": c.entry_time.isoformat(),
        "original_episode_end": exp.t.isoformat(),
        "episode_end_reason": exp.reason,
        "confirmation_session": confirm_sess,
        "same_session": same_session,
        "same_episode": same_episode and causal_ok,
        "confirmed_raw": bool(conf_raw.confirmed),
        "confirmed_strict": bool(causal_ok),
        "causal_ok": causal_ok,
        "execution_ok": execution_ok,
        "ask_status_at_confirm": ask_st,
        "v3_raw_used_crossed_ask": v3_crossed,
        "candidate_expired_reason": exp.reason if not causal_ok else None,
        "reject_reason": reject_reason,
        "noise": dict(noise),
        **flags,
    }
    if price_conf:
        row["strict_ask"] = price_conf["ask"]
        row["strict_ask_qty"] = price_conf["ask_qty"]
        row["strict_noise_band"] = price_conf["noise_band"]
        row["confirm_i"] = price_conf["i"]
        row["confirm_px"] = price_conf["px"]
    return row


def simulate_ask_trade(
    c: EntryContract,
    ticks: Sequence[PushTick],
    *,
    confirm_t: datetime,
    entry_ask: float,
    path: Sequence[PathBar],
) -> Optional[dict[str, Any]]:
    c2 = replace(c, entry_time=confirm_t, entry_price=float(entry_ask), entry_signal_time=c.entry_signal_time)
    path_post = [b for b in path if b.t >= confirm_t]
    if not path_post:
        return None
    ex = simulate_matched_exit(c2, path_post)
    exit_px = float(ex.exit_price)
    for b in path_post:
        if b.t >= ex.exit_time:
            if b.bid is not None and b.bid > 0:
                exit_px = float(b.bid)
            break
    return {
        "entry_time": confirm_t.isoformat(),
        "exit_time": ex.exit_time.isoformat(),
        "entry_price": float(entry_ask),
        "exit_price": exit_px,
        "exit_reason": ex.exit_reason,
        "pnl_5bps": pnl_5bps(float(entry_ask), exit_px),
        "hold_sec": float(ex.hold_sec),
        "expected_achieved": bool(ex.expected_achieved),
    }


def next_ask_after(path: Sequence[PathBar], t0: datetime, *, within_sec: Optional[float] = None) -> Optional[float]:
    for b in path:
        if b.t <= t0:
            continue
        if within_sec is not None and (b.t - t0).total_seconds() > within_sec:
            return None
        if _ask_ok(b):
            return float(b.ask)
    return None


def ask_plus_ticks(ask: float, n: int = 1) -> float:
    return float(ask) + n * tick_size(ask)
