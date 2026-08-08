"""Risk metric aggregations — independent of PnL / ENTRY labels."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from . import EXEC_HORIZONS_SEC, FRESHNESS_MAX_SEC, GRID_SEC, LOT, MIN_SPREAD_DAYS, MIN_SPREAD_OBS
from .quotes import is_board_fresh, is_price_fresh
from .tick import CANONICAL_SOURCE, jpx_tick_size_yen


def _pct(xs: list[float], q: float) -> Optional[float]:
    if not xs:
        return None
    return float(np.quantile(np.asarray(xs, dtype=float), q))


def _mean_bool(flags: list[bool]) -> Optional[float]:
    if not flags:
        return None
    return float(sum(1 for x in flags if x) / len(flags))


def aggregate_symbol_day(
    day: str,
    symbol: str,
    rows: list[dict[str, Any]],
    ref: dict[str, Any],
    *,
    max_age: float = FRESHNESS_MAX_SEC,
) -> dict[str, Any]:
    """Compute Layer-A risk metrics for one symbol-day from slim quote rows."""
    out: dict[str, Any] = {
        "day": day,
        "symbol": symbol,
        "n_rows": len(rows),
        "reference_price": ref.get("reference_price"),
        "reference_price_source": ref.get("reference_price_source"),
        "asof_valid": ref.get("asof_valid"),
    }
    rp = ref.get("reference_price")
    if rp is None or not ref.get("asof_valid"):
        out["one_lot_notional_yen"] = None
        out["tick_size_yen"] = None
        out["one_tick_risk_yen_100"] = None
        out["status_notional"] = "NOT_EVALUABLE_REFERENCE_PRICE"
    else:
        tick = float(jpx_tick_size_yen(float(rp)))
        out["one_lot_notional_yen"] = float(rp) * LOT
        out["tick_size_yen"] = tick
        out["one_tick_risk_yen_100"] = tick * LOT
        out["status_notional"] = "OK"
        out["tick_size_source"] = CANONICAL_SOURCE

    spreads_yen: list[float] = []
    spreads_bps: list[float] = []
    spread_cost: list[float] = []
    bid_qtys: list[float] = []
    ask_qtys: list[float] = []
    bid_ge100: list[bool] = []
    ask_ge100: list[bool] = []
    bid_ge300: list[bool] = []
    ask_ge300: list[bool] = []
    price_fresh_flags: list[bool] = []
    board_fresh_flags: list[bool] = []
    both_fresh_flags: list[bool] = []
    price_ages: list[float] = []
    board_ages: list[float] = []
    down_jumps: list[float] = []
    down_jump_elapsed: list[float] = []

    prev_fresh_bid: Optional[float] = None
    prev_fresh_t: Optional[float] = None

    # timeline for exec-loss grid
    t_arr: list[float] = []
    bid_arr: list[float] = []
    ask_arr: list[float] = []
    board_ok_arr: list[bool] = []

    for r in rows:
        bid = float(r["bid"])
        ask = float(r["ask"])
        mid = 0.5 * (bid + ask)
        if mid <= 0 or ask < bid:
            # crossed — skip spread; still track for jumps if board fresh
            pass
        else:
            sy = ask - bid
            spreads_yen.append(sy)
            spreads_bps.append(sy / mid * 10000.0)
            spread_cost.append(sy * LOT)

        bq = r.get("bid_qty")
        aq = r.get("ask_qty")
        if bq is not None:
            bid_qtys.append(float(bq))
            bid_ge100.append(float(bq) >= LOT)
            bid_ge300.append(float(bq) >= 3 * LOT)
        if aq is not None:
            ask_qtys.append(float(aq))
            ask_ge100.append(float(aq) >= LOT)
            ask_ge300.append(float(aq) >= 3 * LOT)

        pa = r.get("price_age_sec")
        ba = r.get("board_age_sec")
        pf = is_price_fresh(pa, max_age)
        bf = is_board_fresh(ba, max_age)
        price_fresh_flags.append(pf)
        board_fresh_flags.append(bf)
        both_fresh_flags.append(pf and bf)
        if pa is not None:
            price_ages.append(float(pa))
        if ba is not None:
            board_ages.append(float(ba))

        t = float(r["t"])
        t_arr.append(t)
        bid_arr.append(bid)
        ask_arr.append(ask)
        board_ok_arr.append(bf)

        if bf:
            if prev_fresh_bid is not None and prev_fresh_t is not None:
                jump = prev_fresh_bid - bid  # positive = down
                if jump > 0:
                    down_jumps.append(jump)
                    down_jump_elapsed.append(t - prev_fresh_t)
            prev_fresh_bid = bid
            prev_fresh_t = t

    # spread summary
    if len(spreads_yen) < MIN_SPREAD_OBS:
        out["spread_status"] = "NOT_EVALUABLE_SPREAD_SUPPORT"
    else:
        out["spread_status"] = "OK"
    out["n_spread_obs"] = len(spreads_yen)
    out["median_spread_yen"] = _pct(spreads_yen, 0.50)
    out["p90_spread_yen"] = _pct(spreads_yen, 0.90)
    out["p95_spread_yen"] = _pct(spreads_yen, 0.95)
    out["max_spread_yen"] = max(spreads_yen) if spreads_yen else None
    out["median_spread_bps"] = _pct(spreads_bps, 0.50)
    out["p90_spread_bps"] = _pct(spreads_bps, 0.90)
    out["median_spread_cost_yen_100"] = _pct(spread_cost, 0.50)
    out["p95_spread_cost_yen_100"] = _pct(spread_cost, 0.95)

    # depth
    out["n_depth_obs"] = len(bid_qtys)
    out["p10_best_bid_qty"] = _pct(bid_qtys, 0.10)
    out["p10_best_ask_qty"] = _pct(ask_qtys, 0.10)
    out["p_best_bid_qty_ge_100"] = _mean_bool(bid_ge100)
    out["p_best_ask_qty_ge_100"] = _mean_bool(ask_ge100)
    out["p_best_bid_qty_ge_300"] = _mean_bool(bid_ge300)
    out["p_best_ask_qty_ge_300"] = _mean_bool(ask_ge300)

    # freshness
    out["price_fresh_rate"] = _mean_bool(price_fresh_flags)
    out["board_fresh_rate"] = _mean_bool(board_fresh_flags)
    out["both_fresh_rate"] = _mean_bool(both_fresh_flags)
    out["p50_price_age"] = _pct(price_ages, 0.50)
    out["p90_price_age"] = _pct(price_ages, 0.90)
    out["p50_board_age"] = _pct(board_ages, 0.50)
    out["p90_board_age"] = _pct(board_ages, 0.90)
    out["freshness_max_sec_reused"] = max_age

    # bid jumps
    out["n_jump_obs"] = len(down_jumps)
    jump100 = [j * LOT for j in down_jumps]
    out["p90_down_bid_jump_yen"] = _pct(down_jumps, 0.90)
    out["p95_down_bid_jump_yen"] = _pct(down_jumps, 0.95)
    out["max_down_bid_jump_yen"] = max(down_jumps) if down_jumps else None
    out["p90_down_bid_jump_yen_100"] = _pct(jump100, 0.90)
    out["p95_down_bid_jump_yen_100"] = _pct(jump100, 0.95)
    out["max_down_bid_jump_yen_100"] = max(jump100) if jump100 else None
    out["no_bid_event_count"] = None  # NOT_AVAILABLE_IN_CAPTURE
    out["special_quote_count"] = None
    out["halt_like_gap_count"] = None
    out["special_quote_status"] = "NOT_AVAILABLE_IN_CAPTURE"

    # executable short-horizon adverse risk on fixed 5s grid
    exec_stats = _exec_loss_grid(t_arr, bid_arr, ask_arr, board_ok_arr, max_age=max_age)
    out.update(exec_stats)
    return out


def _exec_loss_grid(
    t_arr: list[float],
    bid_arr: list[float],
    ask_arr: list[float],
    board_ok: list[bool],
    *,
    max_age: float,
) -> dict[str, Any]:
    """Buy at fresh ask on 5s grid; mark-to fresh bid at horizons. Risk only."""
    empty = {
        "n_exec_anchors": 0,
        **{f"exec_loss_yen_100_{int(h)}s_p50": None for h in EXEC_HORIZONS_SEC},
        **{f"exec_loss_yen_100_{int(h)}s_p90": None for h in EXEC_HORIZONS_SEC},
        **{f"exec_loss_yen_100_{int(h)}s_p95": None for h in EXEC_HORIZONS_SEC},
        **{f"exec_loss_yen_100_{int(h)}s_max": None for h in EXEC_HORIZONS_SEC},
        **{f"exec_loss_bps_{int(h)}s_p50": None for h in EXEC_HORIZONS_SEC},
    }
    if len(t_arr) < 2:
        return empty

    t = np.asarray(t_arr, dtype=float)
    bid = np.asarray(bid_arr, dtype=float)
    ask = np.asarray(ask_arr, dtype=float)
    ok = np.asarray(board_ok, dtype=bool)

    # last fresh index at or before each position
    last_fresh = np.full(len(t), -1, dtype=int)
    lf = -1
    for i in range(len(t)):
        if ok[i]:
            lf = i
        last_fresh[i] = lf

    t0 = float(t[0])
    t1 = float(t[-1])
    anchors = np.arange(t0, t1 + 1e-9, GRID_SEC)
    losses: dict[float, list[float]] = {h: [] for h in EXEC_HORIZONS_SEC}
    losses_bps: dict[float, list[float]] = {h: [] for h in EXEC_HORIZONS_SEC}
    n_anchors = 0

    for a in anchors:
        i = int(np.searchsorted(t, a, side="right") - 1)
        if i < 0:
            continue
        age = a - float(t[i])
        if age < 0 or age > max_age or not bool(ok[i]):
            continue
        entry_ask = float(ask[i])
        if entry_ask <= 0:
            continue
        n_anchors += 1
        for h in EXEC_HORIZONS_SEC:
            target = a + h
            j = int(np.searchsorted(t, target, side="right") - 1)
            if j < i:
                continue
            k = int(last_fresh[j])
            if k < i:
                continue
            if (target - float(t[k])) > max_age:
                continue
            fut_bid = float(bid[k])
            loss_yen = (entry_ask - fut_bid) * LOT
            loss_bps = (entry_ask - fut_bid) / entry_ask * 10000.0
            losses[h].append(loss_yen)
            losses_bps[h].append(loss_bps)

    out: dict[str, Any] = {"n_exec_anchors": n_anchors, "exec_grid_sec": GRID_SEC}
    for h in EXEC_HORIZONS_SEC:
        hs = int(h)
        xs = losses[h]
        bs = losses_bps[h]
        out[f"exec_loss_yen_100_{hs}s_p50"] = _pct(xs, 0.50)
        out[f"exec_loss_yen_100_{hs}s_p90"] = _pct(xs, 0.90)
        out[f"exec_loss_yen_100_{hs}s_p95"] = _pct(xs, 0.95)
        out[f"exec_loss_yen_100_{hs}s_max"] = max(xs) if xs else None
        out[f"exec_loss_bps_{hs}s_p50"] = _pct(bs, 0.50)
        out[f"n_exec_obs_{hs}s"] = len(xs)
    return out


def summarize_symbol(day_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Cross-day symbol risk summary."""
    if not day_rows:
        return {"status": "NO_DATA"}
    sym = day_rows[0]["symbol"]
    notionals = [r["one_lot_notional_yen"] for r in day_rows if r.get("one_lot_notional_yen") is not None]
    ticks = [r["one_tick_risk_yen_100"] for r in day_rows if r.get("one_tick_risk_yen_100") is not None]
    spread_ok_days = [r for r in day_rows if r.get("spread_status") == "OK"]

    def col(name: str) -> list[float]:
        return [float(r[name]) for r in day_rows if r.get(name) is not None]

    def median_of(name: str) -> Optional[float]:
        xs = col(name)
        return _pct(xs, 0.50) if xs else None

    # estimated execution risk components (median across days of daily p95)
    spread_p95 = col("p95_spread_cost_yen_100")
    jump_p95 = col("p95_down_bid_jump_yen_100")
    exec5_p95 = col("exec_loss_yen_100_5s_p95")

    est = None
    if spread_p95 or jump_p95 or exec5_p95:
        # use median of available component medians' max pattern per day
        daily_est = []
        for r in day_rows:
            comps = [r.get("p95_spread_cost_yen_100"), r.get("p95_down_bid_jump_yen_100"),
                     r.get("exec_loss_yen_100_5s_p95")]
            comps_f = [float(c) for c in comps if c is not None]
            if comps_f:
                daily_est.append(max(comps_f))
        est = _pct(daily_est, 0.50) if daily_est else None

    return {
        "symbol": sym,
        "n_days": len(day_rows),
        "n_spread_ok_days": len(spread_ok_days),
        "spread_days_ok": len(spread_ok_days) >= MIN_SPREAD_DAYS,
        "one_lot_notional_median": _pct(notionals, 0.50) if notionals else None,
        "one_lot_notional_p75": _pct(notionals, 0.75) if notionals else None,
        "one_lot_notional_p90": _pct(notionals, 0.90) if notionals else None,
        "one_lot_notional_max": max(notionals) if notionals else None,
        "one_tick_risk_yen_100_median": _pct(ticks, 0.50) if ticks else None,
        "spread_cost_p50": median_of("median_spread_cost_yen_100"),
        "spread_cost_p95": median_of("p95_spread_cost_yen_100"),
        "best_bid_qty_p10": median_of("p10_best_bid_qty"),
        "best_ask_qty_p10": median_of("p10_best_ask_qty"),
        "bid_depth_100_coverage": median_of("p_best_bid_qty_ge_100"),
        "ask_depth_100_coverage": median_of("p_best_ask_qty_ge_100"),
        "both_fresh_rate": median_of("both_fresh_rate"),
        "board_fresh_rate": median_of("board_fresh_rate"),
        "down_jump_yen_100_p95": median_of("p95_down_bid_jump_yen_100"),
        "down_jump_yen_100_max": max(col("max_down_bid_jump_yen_100")) if col("max_down_bid_jump_yen_100") else None,
        "exec_loss_5s_p50": median_of("exec_loss_yen_100_5s_p50"),
        "exec_loss_5s_p90": median_of("exec_loss_yen_100_5s_p90"),
        "exec_loss_5s_p95": median_of("exec_loss_yen_100_5s_p95"),
        "exec_loss_5s_max": max(col("exec_loss_yen_100_5s_max")) if col("exec_loss_yen_100_5s_max") else None,
        "exec_loss_1s_p95": median_of("exec_loss_yen_100_1s_p95"),
        "exec_loss_10s_p95": median_of("exec_loss_yen_100_10s_p95"),
        "exec_loss_30s_p95": median_of("exec_loss_yen_100_30s_p95"),
        "n_spread_obs_total": int(sum(r.get("n_spread_obs") or 0 for r in day_rows)),
        "n_depth_obs_total": int(sum(r.get("n_depth_obs") or 0 for r in day_rows)),
        "n_jump_obs_total": int(sum(r.get("n_jump_obs") or 0 for r in day_rows)),
        "n_exec_anchors_total": int(sum(r.get("n_exec_anchors") or 0 for r in day_rows)),
        "estimated_execution_risk_yen": est,
        "is_285A": sym == "285A",
    }
