"""Q1–Q4 STRUCTURAL × ECONOMIC classification for EC2 episodes."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from research.entry_exit_contract.contract import EntryContract
from research.entry_exit_contract.exits import path_for_contract, simulate_matched_exit
from research.entry_exit_contract_integrity.metrics import pnl_pct_5bps
from research.eec_noise_hysteresis.noise import tick_size
from research.price_flow_exit.entries import FixedEntry
from research.price_flow_exit.path_mfe import compute_executable_mfe
from research.volume_confirmed_impulse_entry.push_loader import PushTick


def _structural(c: EntryContract, path) -> bool:
    """Structural success = expected path toward pre_pullback_high within horizon."""
    target = float(c.levels.get("pre_pullback_high") or c.entry_price)
    for b in path:
        hold = (b.t - c.entry_time).total_seconds()
        if hold > c.expected_horizon_sec:
            break
        if b.px >= target * 0.999:
            return True
        if pnl_pct_5bps(c.entry_price, b.bid if b.bid else b.px) >= 0.08:
            # normalized progress proxy used in EC2 exit
            return True
    return False


def _economic(c: EntryContract, path) -> dict[str, Any]:
    fe = FixedEntry(
        day=c.day,
        symbol=c.symbol,
        entry_time=c.entry_time,
        entry_price=c.entry_price,
        entry_method="EC2",
        cohort="EC2",
        setup_id=c.setup_id,
    )
    mfe = compute_executable_mfe(fe, path)
    mfe_pct = mfe.mfe_5bps
    if mfe_pct is None or mfe_pct <= 0 or not mfe.quote_evaluable:
        return {"economic": False, "reason": "no_positive_mfe", "mfe_pct": mfe_pct, "mae_pct": mfe.mae_5bps}

    # 1tick: profit zone still exists somewhere on path
    ts = tick_size(c.entry_price)
    tick_profit = False
    held_events = 0
    held_sec = 0.0
    last_t = c.entry_time
    bid_qty_ok = False
    bid_qty_ne = True
    for b in path:
        if b.bid is None or b.bid <= 0:
            continue
        slip = float(b.bid) - ts
        if pnl_pct_5bps(c.entry_price, slip) > 0:
            tick_profit = True
            held_events += 1
            held_sec += max(0.0, (b.t - last_t).total_seconds())
        last_t = b.t
        if b.bid_qty is not None:
            bid_qty_ne = False
            if b.bid_qty >= 100:
                bid_qty_ok = True
    persist = held_events >= 2 or held_sec >= 3.0
    qty_ok = bid_qty_ok or bid_qty_ne
    ok = bool(tick_profit and persist and qty_ok)
    return {
        "economic": ok,
        "reason": "" if ok else "fail_tick_or_persist_or_qty",
        "mfe_pct": mfe_pct,
        "mae_pct": mfe.mae_5bps,
        "tick_profit_zone": tick_profit,
        "held_events": held_events,
        "held_sec": held_sec,
        "bid_qty_ok": bid_qty_ok,
        "bid_qty_ne": bid_qty_ne,
    }


def classify_population(
    contracts: Sequence[EntryContract],
    push_by_day: dict[str, dict[str, list[PushTick]]],
    *,
    oos_days: Sequence[str],
) -> dict[str, Any]:
    rows = []
    for c in contracts:
        if c.day not in oos_days:
            continue
        ticks = (push_by_day.get(c.day) or {}).get(c.symbol) or []
        if not ticks:
            continue
        path = path_for_contract(c, ticks)
        if not path:
            continue
        struct = _structural(c, path)
        econ = _economic(c, path)
        if struct and econ["economic"]:
            q = "Q1"
        elif struct and not econ["economic"]:
            q = "Q2"
        elif (not struct) and econ["economic"]:
            q = "Q3"
        else:
            q = "Q4"
        # baseline A0 pnl for reference
        ex = simulate_matched_exit(c, path)
        snap = c.entry_feature_snapshot or {}
        rows.append(
            {
                "quadrant": q,
                "structural": struct,
                "economic": econ["economic"],
                "day": c.day,
                "symbol": c.symbol,
                "episode_id": c.episode_id,
                "setup_id": c.setup_id,
                "entry_time": c.entry_time.isoformat(),
                "pullback_low": c.levels.get("pullback_low"),
                "reclaim_level": c.levels.get("reclaim_level"),
                "trend_reference": c.levels.get("trend_reference"),
                "mfe_pct": econ.get("mfe_pct"),
                "mae_pct": econ.get("mae_pct"),
                "a0_pnl_5bps": ex.pnl_5bps,
                "uptick_ratio_30s": snap.get("uptick_volume_ratio_30s"),
                "volume_impulse_10s": snap.get("volume_impulse_10s"),
                "spread_change_30s": snap.get("spread_change_30s"),
                "tick_acceleration_10s": snap.get("tick_acceleration_10s"),
                "price_slope_120s": snap.get("price_slope_120s"),
            }
        )

    by_q: dict[str, list] = defaultdict(list)
    for r in rows:
        by_q[r["quadrant"]].append(r)
    n = max(1, len(rows))
    summary = {}
    for q in ("Q1", "Q2", "Q3", "Q4"):
        xs = by_q.get(q) or []
        pnls = [float(x["a0_pnl_5bps"]) for x in xs]
        summary[q] = {
            "n": len(xs),
            "ratio": round(len(xs) / n, 4),
            "pnl_5bps": round(sum(pnls), 2),
            "mean_mfe": round(sum(float(x["mfe_pct"] or 0) for x in xs) / max(1, len(xs)), 4) if xs else None,
            "mean_mae": round(sum(float(x["mae_pct"] or 0) for x in xs) / max(1, len(xs)), 4) if xs else None,
            "mean_uptick": round(
                sum(float(x["uptick_ratio_30s"]) for x in xs if x.get("uptick_ratio_30s") is not None)
                / max(1, sum(1 for x in xs if x.get("uptick_ratio_30s") is not None)),
                4,
            )
            if any(x.get("uptick_ratio_30s") is not None for x in xs)
            else None,
        }
    by_day = defaultdict(lambda: defaultdict(int))
    by_sym = defaultdict(lambda: defaultdict(int))
    for r in rows:
        by_day[r["day"]][r["quadrant"]] += 1
        by_sym[r["symbol"]][r["quadrant"]] += 1
    return {
        "n": len(rows),
        "summary": summary,
        "by_day": {d: dict(v) for d, v in by_day.items()},
        "top_symbols_q2": sorted(
            ((s, v.get("Q2", 0)) for s, v in by_sym.items()), key=lambda kv: -kv[1]
        )[:15],
        "sample_rows": rows[:80],
        "rows": rows,
    }
