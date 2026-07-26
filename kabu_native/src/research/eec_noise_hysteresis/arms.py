"""Comparison arms A0–A7 for EC2 noise/hysteresis study."""
from __future__ import annotations

from dataclasses import replace
from typing import Any, Optional, Sequence

from research.entry_exit_contract.contract import EntryContract
from research.entry_exit_contract.exits import path_for_contract, simulate_matched_exit
from research.entry_exit_contract_integrity.execution import execution_ladder, summarize_reality
from research.eec_noise_hysteresis.confirm import confirm_entry
from research.eec_noise_hysteresis.hysteresis import simulate_hysteresis_exit
from research.eec_noise_hysteresis.path_util import path_with_lookback
from research.pbv2_zero_base_revalidation.metrics import pnl_metric_block
from research.price_flow_exit_integrity.dd import summarize_dd
from research.price_flow_exit_integrity.dependency import dependency_audit
from research.price_flow_exit_integrity.portfolio import filter_no_overlap, replay_cap5
from research.price_flow_exit_integrity.trades import SimTrade
from research.volume_confirmed_impulse_entry.push_loader import PushTick
from research.eec_noise_hysteresis.constants import CAP


# arm -> (confirm_mode, exit_mode)
ARM_SPEC = {
    "A0": ("N0", "matched"),  # current EC2 immediate entry + matched exit (baseline)
    "A1": ("N1", "matched"),  # confirmation only
    "A2": ("N0", "hysteresis"),  # hysteresis only
    "A3": ("N1", "hysteresis"),  # confirm + hysteresis
    "A4": ("N1", "hysteresis"),  # price persist only
    "A5": ("N2", "hysteresis"),
    "A6": ("N3", "hysteresis"),
    "A7": ("N4", "hysteresis"),
}


def simulate_arm(
    c: EntryContract,
    ticks: Sequence[PushTick],
    *,
    arm: str,
    tick_mult: float,
    range_mult: float,
    spread_mult: float,
) -> Optional[dict[str, Any]]:
    # full path with pre-entry lookback for noise range; confirm only after entry_time
    path_full, _ = path_with_lookback(ticks, c.entry_time)
    if not path_full:
        return None
    path0 = path_for_contract(c, ticks)  # post-entry only (A0 matched baseline)
    conf_mode, exit_mode = ARM_SPEC[arm]
    conf = confirm_entry(
        c,
        path_full,
        mode=conf_mode,
        tick_mult=tick_mult,
        range_mult=range_mult,
        spread_mult=spread_mult,
    )
    if not conf.confirmed:
        return {
            "arm": arm,
            "day": c.day,
            "symbol": c.symbol,
            "episode_id": c.episode_id,
            "setup_id": c.setup_id,
            "confirmed": False,
            "false_entry": False,
            "confirm_delay_sec": None,
            "lost_opportunity": conf.lost_opportunity,
            "pnl_5bps": 0.0,
            "skip": True,
            "exit_reason": "NO_CONFIRM",
            "entry_time": c.entry_time.isoformat(),
            "session": c.session,
            "entry_price": c.entry_price,
            "hold_sec": 0.0,
            "execution": {},
            "warning_to_recovery": 0,
            "warning_to_invalidation": 0,
            "invalidation_to_exit_sec": None,
            "false_invalidation": False,
        }

    # path around confirmation (lookback for noise; exit sim skips bars before entry)
    path_h, _ = path_with_lookback(ticks, conf.entry_time)
    if not path_h:
        return None
    # post-confirm bars for matched exit / MFE
    path_post = [b for b in path_h if b.t >= conf.entry_time]
    if not path_post:
        return None

    if arm == "A0" or (exit_mode == "matched" and conf_mode == "N0"):
        if not path0:
            return None
        ex = simulate_matched_exit(c, path0)
        entry_time, entry_price = c.entry_time, c.entry_price
        exit_time, exit_price = ex.exit_time, ex.exit_price
        reason = ex.exit_reason
        pnl = float(ex.pnl_5bps)
        hold = float(ex.hold_sec)
        w2r = w2i = 0
        inv_lat = None
        false_inv = False
        ladder_path = path0
    elif exit_mode == "matched":
        c2 = replace(
            c,
            entry_time=conf.entry_time,
            entry_price=conf.entry_price,
            entry_signal_time=c.entry_signal_time,
        )
        ex = simulate_matched_exit(c2, path_post)
        entry_time, entry_price = conf.entry_time, conf.entry_price
        exit_time, exit_price = ex.exit_time, ex.exit_price
        reason = ex.exit_reason
        pnl = float(ex.pnl_5bps)
        hold = float(ex.hold_sec)
        w2r = w2i = 0
        inv_lat = None
        false_inv = False
        ladder_path = path_post
    else:
        hyst = simulate_hysteresis_exit(
            entry_time=conf.entry_time,
            entry_price=conf.entry_price,
            reclaim=float(c.levels["reclaim_level"]),
            pullback_low=float(c.levels["pullback_low"]),
            path=path_h,
            tick_mult=tick_mult,
            range_mult=range_mult,
            spread_mult=spread_mult,
            immediate=False,
        )
        entry_time, entry_price = conf.entry_time, conf.entry_price
        exit_time, exit_price = hyst.exit_time, hyst.exit_price
        reason = hyst.exit_reason
        pnl = float(hyst.pnl_5bps)
        hold = float(hyst.hold_sec)
        w2r, w2i = hyst.warning_to_recovery, hyst.warning_to_invalidation
        inv_lat = hyst.invalidation_to_exit_sec
        false_inv = hyst.false_invalidation
        ladder_path = path_post

    # false entry: confirmed but never economic path (no positive mfe after entry)
    from research.price_flow_exit.entries import FixedEntry
    from research.price_flow_exit.path_mfe import compute_executable_mfe

    fe = FixedEntry(
        day=c.day,
        symbol=c.symbol,
        entry_time=entry_time,
        entry_price=entry_price,
        entry_method="EC2",
        cohort="EC2",
        setup_id=c.setup_id,
    )
    mfe = compute_executable_mfe(fe, ladder_path)
    false_entry = bool(mfe.mfe_5bps is not None and mfe.mfe_5bps <= 0)

    ladder = execution_ladder(
        replace(c, entry_time=entry_time, entry_price=entry_price),
        ladder_path,
        exit_time=exit_time,
        exit_price=exit_price,
    )
    return {
        "arm": arm,
        "day": c.day,
        "symbol": c.symbol,
        "episode_id": c.episode_id,
        "setup_id": c.setup_id,
        "session": c.session,
        "confirmed": True,
        "skip": False,
        "false_entry": false_entry,
        "confirm_delay_sec": conf.delay_sec,
        "lost_opportunity": conf.lost_opportunity,
        "entry_time": entry_time.isoformat(),
        "exit_time": exit_time.isoformat(),
        "entry_price": entry_price,
        "exit_price": exit_price,
        "exit_reason": reason,
        "pnl_5bps": pnl,
        "hold_sec": hold,
        "executable_mfe_pct_5bps": mfe.mfe_5bps,
        "warning_to_recovery": w2r,
        "warning_to_invalidation": w2i,
        "invalidation_to_exit_sec": inv_lat,
        "false_invalidation": false_inv,
        "execution": ladder,
        "strategy_id": "EC2",
    }


def summarize_arm(rows: Sequence[dict[str, Any]], *, oos_days: Sequence[str]) -> dict[str, Any]:
    traded = [r for r in rows if not r.get("skip")]
    skipped = [r for r in rows if r.get("skip")]
    pnls = [float(r["pnl_5bps"]) for r in traded]
    block = pnl_metric_block(pnls, pnls) if pnls else {"n": 0, "total_pnl_5bps": 0.0, "PF_5bps": None}
    trades = []
    for r in traded:
        from datetime import datetime

        trades.append(
            SimTrade(
                day=r["day"],
                symbol=r["symbol"],
                entry_time=datetime.fromisoformat(r["entry_time"]),
                exit_time=datetime.fromisoformat(r["exit_time"]),
                entry_price=float(r["entry_price"]),
                exit_price=float(r["exit_price"]),
                exit_reason=str(r.get("exit_reason") or ""),
                pnl_5bps=float(r["pnl_5bps"]),
                hold_sec=float(r.get("hold_sec") or 0),
                entry_method="EC2",
                cohort="EC2",
                setup_id=str(r.get("setup_id") or ""),
                impulse_episode_id=str(r.get("episode_id") or ""),
                breakout_episode_id=str(r.get("episode_id") or ""),
                pbv2=False,
                vcie=True,
                mode=str(r.get("arm") or ""),
                session=str(r.get("session") or "AM"),
            )
        )
    dd = summarize_dd(trades) if trades else {}
    reality = {
        "R0": summarize_reality(traded, "R0_pnl_5bps"),
        "R1": summarize_reality(traded, "R1_pnl_5bps"),
        "R2": summarize_reality(traded, "R2_pnl_5bps"),
        "R3": summarize_reality(traded, "R3_pnl_5bps"),
    }
    dep = dependency_audit(trades, label="arm") if trades else {}
    n_days = max(1, len(oos_days))
    by_day = {}
    for t in trades:
        by_day[t.day] = by_day.get(t.day, 0.0) + t.pnl_5bps
    # CAP5
    cands_f, _ = filter_no_overlap(sorted(trades, key=lambda t: (t.entry_time, t.setup_id)))
    cap = replay_cap5(cands_f, portfolio_id="ARM", cap=CAP)
    cap_s = cap.summary()
    return {
        "n_candidates": len(rows),
        "n_traded": len(traded),
        "n_skipped_no_confirm": len(skipped),
        "false_entry_n": sum(1 for r in traded if r.get("false_entry")),
        "false_invalidation_n": sum(1 for r in traded if r.get("false_invalidation")),
        "true_invalidation_n": sum(1 for r in traded if "invalid" in str(r.get("exit_reason") or "").lower()),
        "warning_to_recovery_n": sum(int(r.get("warning_to_recovery") or 0) for r in traded),
        "warning_to_invalidation_n": sum(int(r.get("warning_to_invalidation") or 0) for r in traded),
        "mean_confirm_delay_sec": round(
            sum(float(r["confirm_delay_sec"]) for r in traded if r.get("confirm_delay_sec") is not None)
            / max(1, sum(1 for r in traded if r.get("confirm_delay_sec") is not None)),
            2,
        )
        if any(r.get("confirm_delay_sec") is not None for r in traded)
        else None,
        "lost_opportunity_n": sum(1 for r in rows if r.get("lost_opportunity")),
        "trades_per_day": round(len(traded) / n_days, 2),
        "pos_days": sum(1 for v in by_day.values() if v > 0),
        "neg_days": sum(1 for v in by_day.values() if v < 0),
        "reality": reality,
        "dependency_blocked": bool(dep.get("dependency_blocked")),
        "cap5": {
            "accepted": cap_s.get("accepted"),
            "pnl_5bps": cap_s.get("pnl_5bps"),
            "PF_5bps": cap_s.get("PF_5bps"),
            "max_dd_trade_sequence": cap_s.get("max_dd_trade_sequence"),
            "cap_blocked": cap_s.get("cap_blocked"),
        },
        "sample_rows": traded[:40],
        **block,
        "dd_trade_sequence_max_dd": dd.get("trade_sequence_max_dd"),
    }


def run_arms_for_noise(
    contracts: Sequence[EntryContract],
    push_by_day: dict,
    *,
    oos_days: Sequence[str],
    noise: dict[str, float],
    arms: Sequence[str] = ("A0", "A1", "A2", "A3", "A4", "A5", "A6", "A7"),
) -> dict[str, Any]:
    out = {}
    for arm in arms:
        rows = []
        for c in contracts:
            if c.day not in oos_days:
                continue
            ticks = (push_by_day.get(c.day) or {}).get(c.symbol) or []
            if not ticks:
                continue
            r = simulate_arm(
                c,
                ticks,
                arm=arm,
                tick_mult=noise["tick_mult"],
                range_mult=noise["range_mult"],
                spread_mult=noise["spread_mult"],
            )
            if r:
                rows.append(r)
        out[arm] = summarize_arm(rows, oos_days=oos_days)
        out[arm]["arm"] = arm
        out[arm]["noise"] = dict(noise)
    return out
