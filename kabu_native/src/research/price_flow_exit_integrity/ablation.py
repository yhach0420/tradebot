"""Fixed X6 component ablation (no new thresholds / no retune)."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional, Sequence

from research.pbv2_zero_base_revalidation.metrics import pnl_metric_block
from research.pbv2_zero_base_revalidation.util import pnl_5bps, yen100
from research.price_flow_exit.constants import HARD_STOP_PCT, NP_CURRENT_PNL_MAX, NP_REQUIRED_MFE_PCT, NP_START_SEC
from research.price_flow_exit.entries import FixedEntry
from research.price_flow_exit.exit_rules import (
    ExitParams,
    check_x1_failed_breakout,
    check_x2_no_follow,
    check_x3_break_even,
    check_x4_impulse_decay,
    check_x5_exhaustion,
    simulate_exit,
)
from research.price_flow_exit.path_mfe import ExitResult, PathBar, _exit_px, _ret, _ret_5bps, _session_close_time, bars_after_entry, simulate_x0
from research.price_flow_exit_integrity.constants import PATH_MAX_SEC
from research.price_flow_exit_integrity.trades import SimTrade
from research.volume_confirmed_impulse_entry.features import aggregate_to_seconds
from small_paper.board_dynamic_trailing_shadow import trailing_params_for_board_tier


def _vol_window(path: Sequence[PathBar], i: int, sec: float) -> Optional[float]:
    from research.price_flow_exit.exit_rules import _vol_window as vw

    return vw(path, i, sec)


def simulate_ablation(
    entry: FixedEntry,
    path: Sequence[PathBar],
    *,
    ablation: str,
    params: ExitParams,
) -> ExitResult:
    """
    A0 X6 full
    A1 X6 minus Break-Even
    A2 X6 minus No Follow-Through
    A3 X6 minus BE and NFT
    A4 Impulse Decay + Volume Exhaustion + trailing (no FB/BE/NFT)
    A5 X4
    A6 X5
    """
    if ablation == "A0":
        return simulate_exit(entry, path, mode="X6", params=params)
    if ablation == "A5":
        return simulate_exit(entry, path, mode="X4", params=params)
    if ablation == "A6":
        return simulate_exit(entry, path, mode="X5", params=params)
    if not path:
        return ExitResult(entry.entry_time, entry.entry_price, "PATH_EMPTY", 0.0, 0.0, 0.0, False, ["PATH_EMPTY"], False, True)

    use_fb = ablation in ("A1", "A2", "A3")  # still in partial X6
    use_be = ablation not in ("A1", "A3", "A4")
    use_nft = ablation not in ("A2", "A3", "A4")
    use_decay = True
    use_exh = True
    # A1/A2/A3 are X6-like with components removed; A4 is decay+exh+trail only
    if ablation in ("A1", "A2", "A3"):
        use_fb = True

    activate, giveback, _ = trailing_params_for_board_tier(entry.entry_imbalance_percentile)
    stop_px = entry.entry_price * (1.0 - HARD_STOP_PCT / 100.0)
    peak_pnl = 0.0
    trail_on = False
    peak_bid = None
    peak_vol30 = None
    peak_high = None
    armed_be = False
    peak_mfe5 = -1e9
    close_at = _session_close_time(entry.entry_time)
    mfe = 0.0

    for i, b in enumerate(path):
        px, used_bid, qne = _exit_px(b)
        hold = (b.t - entry.entry_time).total_seconds()
        pnl = _ret(entry.entry_price, px)
        mfe = max(mfe, pnl)
        peak_pnl = max(peak_pnl, pnl)
        if used_bid:
            peak_bid = px if peak_bid is None else max(peak_bid, px)
            peak_mfe5 = max(peak_mfe5, _ret_5bps(entry.entry_price, px))
            if peak_mfe5 >= params.be_arm_pct:
                armed_be = True
        peak_high = b.px if peak_high is None else max(peak_high, b.px)
        v30 = _vol_window(path, i, 30)
        if v30 is not None:
            peak_vol30 = v30 if peak_vol30 is None else max(peak_vol30, v30)

        if px <= stop_px or pnl <= -HARD_STOP_PCT:
            return ExitResult(b.t, px, "stop_hit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, ["stop_hit"], used_bid, qne)

        if use_fb and check_x1_failed_breakout(entry, path, i, params):
            return ExitResult(
                b.t, px, "failed_breakout_exit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, ["failed_breakout_exit"], used_bid, qne
            )
        if use_be and check_x3_break_even(entry, path, i, params, armed_be, peak_bid):
            return ExitResult(
                b.t, px, "break_even_protection", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, ["break_even_protection"], used_bid, qne
            )
        if use_nft and check_x2_no_follow(entry, path, i, params, peak_mfe5 if peak_mfe5 > -1e8 else 0.0):
            return ExitResult(
                b.t, px, "no_follow_through_exit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, ["no_follow_through_exit"], used_bid, qne
            )
        if use_exh and check_x5_exhaustion(entry, path, i, params, peak_vol30, peak_high):
            return ExitResult(
                b.t, px, "volume_exhaustion_exit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, ["volume_exhaustion_exit"], used_bid, qne
            )
        if use_decay and check_x4_impulse_decay(entry, path, i, params, peak_vol30, peak_bid):
            return ExitResult(
                b.t, px, "impulse_decay_exit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, ["impulse_decay_exit"], used_bid, qne
            )
        if hold >= NP_START_SEC and mfe < NP_REQUIRED_MFE_PCT and pnl < NP_CURRENT_PNL_MAX and not trail_on:
            return ExitResult(
                b.t, px, "no_progress_exit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, False, ["no_progress_exit"], used_bid, qne
            )
        if peak_pnl >= activate:
            trail_on = True
            if pnl <= peak_pnl * giveback:
                return ExitResult(
                    b.t, px, "trailing_mfe_exit", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, True, ["trailing_mfe_exit"], used_bid, qne
                )
        if close_at and b.t >= close_at:
            reason = "morning_session_close" if entry.entry_time.hour < 12 else "afternoon_session_close"
            return ExitResult(b.t, px, reason, yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, [reason], used_bid, qne)

    b = path[-1]
    px, used_bid, qne = _exit_px(b)
    hold = (b.t - entry.entry_time).total_seconds()
    return ExitResult(b.t, px, "path_end", yen100(entry.entry_price, px), pnl_5bps(entry.entry_price, px), hold, trail_on, ["path_end"], used_bid, qne)


def _post_mfe(entry: FixedEntry, path: Sequence[PathBar], exit_time) -> Optional[float]:
    peak = None
    for b in path:
        if b.t <= exit_time:
            continue
        if b.bid is not None:
            peak = b.bid if peak is None else max(peak, b.bid)
    if peak is None:
        return None
    return (peak - entry.entry_price) / entry.entry_price * 100.0


def run_x6_ablation(
    entries: Sequence[FixedEntry],
    push_by_day: dict[str, dict],
    *,
    params: ExitParams,
    oos_days: Sequence[str],
) -> dict[str, Any]:
    bars_cache: dict[tuple[str, str], list] = {}
    modes = ("A0", "A1", "A2", "A3", "A4", "A5", "A6")
    reason_attr: dict[str, dict[str, Any]] = defaultdict(lambda: {"n": 0, "pnl": 0.0, "gross_profit": 0.0, "gross_loss": 0.0, "mfe_sum": 0.0, "mfe_n": 0, "regret_sum": 0.0, "regret_n": 0, "lost_winner": 0})
    ablation_rows = []
    per_mode_trades: dict[str, list[dict[str, Any]]] = {m: [] for m in modes}

    ents = [e for e in entries if e.day in oos_days]
    for e in ents:
        ticks = (push_by_day.get(e.day) or {}).get(e.symbol) or []
        if not ticks:
            continue
        key = (e.day, e.symbol)
        if key not in bars_cache:
            bars_cache[key] = aggregate_to_seconds(ticks)
        path = bars_after_entry(bars_cache[key], e.entry_time, max_sec=PATH_MAX_SEC)
        if not path:
            continue
        x0 = simulate_x0(e, path)
        for m in modes:
            ex = simulate_ablation(e, path, ablation=m, params=params)
            post = _post_mfe(e, path, ex.exit_time)
            regret = None
            if post is not None and ex.exit_price > 0:
                regret = (post - _ret(e.entry_price, ex.exit_price))
            # approximate: remaining upside after exit vs exit return
            if post is not None:
                regret = max(0.0, post - _ret(e.entry_price, ex.exit_price))
            lost = bool(x0.pnl_5bps > 0 and ex.pnl_5bps < x0.pnl_5bps and (regret or 0) > 0.05)
            row = {
                "day": e.day,
                "symbol": e.symbol,
                "ablation": m,
                "exit_reason": ex.exit_reason,
                "pnl_5bps": ex.pnl_5bps,
                "hold_sec": ex.hold_sec,
                "post_mfe_pct": post,
                "early_exit_regret_pct": regret,
                "lost_winner": lost,
                "x0_pnl_5bps": x0.pnl_5bps,
            }
            per_mode_trades[m].append(row)
            if m == "A0":
                ra = reason_attr[ex.exit_reason]
                ra["n"] += 1
                ra["pnl"] += ex.pnl_5bps
                if ex.pnl_5bps >= 0:
                    ra["gross_profit"] += ex.pnl_5bps
                else:
                    ra["gross_loss"] += abs(ex.pnl_5bps)
                if post is not None:
                    ra["mfe_sum"] += post
                    ra["mfe_n"] += 1
                if regret is not None:
                    ra["regret_sum"] += regret
                    ra["regret_n"] += 1
                if lost:
                    ra["lost_winner"] += 1

    for m in modes:
        xs = per_mode_trades[m]
        pnls = [float(r["pnl_5bps"]) for r in xs]
        block = pnl_metric_block(pnls, pnls) if pnls else {"n": 0, "total_pnl_5bps": 0, "PF_5bps": None}
        regrets = [float(r["early_exit_regret_pct"]) for r in xs if r.get("early_exit_regret_pct") is not None]
        ablation_rows.append(
            {
                "ablation": m,
                "n": len(xs),
                "pnl_5bps": round(float(block.get("total_pnl_5bps") or 0), 2),
                "PF_5bps": block.get("PF_5bps"),
                "mean_early_exit_regret_pct": round(sum(regrets) / len(regrets), 4) if regrets else None,
                "lost_winner_n": sum(1 for r in xs if r.get("lost_winner")),
                "note": "fixed ablation; no retune / no best-of reselect on OOS",
            }
        )

    reason_rows = []
    for reason, ra in sorted(reason_attr.items(), key=lambda kv: -kv[1]["n"]):
        reason_rows.append(
            {
                "exit_reason": reason,
                "n": ra["n"],
                "pnl_5bps": round(ra["pnl"], 2),
                "gross_profit": round(ra["gross_profit"], 2),
                "gross_loss": round(ra["gross_loss"], 2),
                "mean_post_mfe_pct": round(ra["mfe_sum"] / ra["mfe_n"], 4) if ra["mfe_n"] else None,
                "mean_early_exit_regret_pct": round(ra["regret_sum"] / ra["regret_n"], 4) if ra["regret_n"] else None,
                "lost_winner": ra["lost_winner"],
            }
        )
    return {
        "ablation": ablation_rows,
        "reason_attribution": reason_rows,
        "sample_a0": per_mode_trades["A0"][:100],
    }
