"""Historical replay with D−1 rolling history only (no same-day future)."""
from __future__ import annotations

from typing import Any, Optional

from . import (
    DESIGN_DAYS,
    FORBIDDEN_ALPHA_DAYS,
    LOOKBACK_MAX,
    MIN_EXEC_ANCHORS,
    MIN_HISTORY_DAYS,
    MIN_JUMP_N,
    MIN_SPREAD_N,
    PARITY_SYMBOLS,
    PER_SYMBOL_NOTIONAL_FRAC,
    PER_TRADE_RISK_FRAC,
    RISK_ONLY_DAY,
    TARGET_SYMBOL,
)
from .measure import MeasurementInput, measure, required_capital_by_execution_risk, required_capital_by_notional
from .panel import load_x10_panel


def _med(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    ys = sorted(xs)
    return float(ys[len(ys) // 2])


def _prior(day: str, days: list[str]) -> list[str]:
    return [d for d in days if d < day][-LOOKBACK_MAX:]


def rolling_components(
    symbol: str,
    day: str,
    hist: list[str],
    panel: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    """D−1 history aggregates — never include day itself or future."""
    assert day not in hist
    assert all(h < day for h in hist)
    assert day not in FORBIDDEN_ALPHA_DAYS
    assert RISK_ONLY_DAY not in hist  # risk-only never used as alpha/parity history
    rows = [panel[(symbol, d)] for d in hist if (symbol, d) in panel]
    spreads = [r["p95_spread_cost_yen_100"] for r in rows if r["p95_spread_cost_yen_100"] is not None]
    jumps = [r["p95_down_bid_jump_yen_100"] for r in rows if r["p95_down_bid_jump_yen_100"] is not None]
    execs = [r["exec_loss_5s_p95"] for r in rows if r["exec_loss_5s_p95"] is not None]
    last = rows[-1] if rows else None
    spread_n = sum(r["n_spread_obs"] for r in rows)
    jump_n = sum(r["n_jump_obs"] for r in rows)
    exec_n = sum(r["n_exec_anchors"] for r in rows)
    history_ok = (
        len(rows) >= MIN_HISTORY_DAYS
        and spread_n >= MIN_SPREAD_N
        and jump_n >= MIN_JUMP_N
        and exec_n >= MIN_EXEC_ANCHORS
    )
    return {
        "history_days": hist,
        "history_end": hist[-1] if hist else None,
        "n_hist_rows": len(rows),
        "spread_n": spread_n,
        "jump_n": jump_n,
        "exec_n": exec_n,
        "history_support_status": "OK" if history_ok else "RISK_HISTORY_INSUFFICIENT",
        "rolling_spread_cost_p95": _med(spreads),
        "rolling_down_bid_jump_p95": _med(jumps),
        "rolling_executable_loss_5s_p95": _med(execs),
        "asof_reference_price": last["reference_price"] if last else None,
        "asof_one_lot_notional_yen": last["one_lot_notional_yen"] if last else None,
        "asof_tick_size": last["tick_size_yen"] if last else None,
        "asof_one_tick_risk_yen_100": last["one_tick_risk_yen_100"] if last else None,
        "asof_median_spread_cost_yen_100": last["median_spread_cost_yen_100"] if last else None,
        "asof_bid_qty_p10": last["p10_best_bid_qty"] if last else None,
        "asof_ask_qty_p10": last["p10_best_ask_qty"] if last else None,
        "no_same_day_future": True,
    }


def replay_symbol_day(
    symbol: str,
    day: str,
    panel: dict[tuple[str, str], dict[str, Any]],
    *,
    all_days: list[str] | None = None,
) -> dict[str, Any]:
    """Replay one symbol-day using D−1 rolling + measure()."""
    days = list(all_days or DESIGN_DAYS)
    hist = _prior(day, days)
    roll = rolling_components(symbol, day, hist, panel)
    # Same-day panel row is for reference/display only — NOT mixed into rolling.
    same = panel.get((symbol, day)) or {}

    # Event contract: use as-of ask proxy = asof_reference (D−1 close) as best_ask
    # for day-level observer; document vs X10 same-day ref×100 difference.
    ref = roll["asof_reference_price"]
    tick = roll["asof_tick_size"]
    ask = ref  # proxy for day-level historical observer
    bid = None
    if ask is not None and roll["asof_median_spread_cost_yen_100"] is not None:
        bid = ask - (roll["asof_median_spread_cost_yen_100"] / 100.0)
    elif ask is not None and tick is not None:
        bid = ask - tick

    bq = roll["asof_bid_qty_p10"]
    aq = roll["asof_ask_qty_p10"]
    # if p10 missing, treat as unknown → measure will flag depth
    inp = MeasurementInput(
        symbol=symbol,
        event_time=f"{day}T12:00:00+09:00",
        best_bid=bid,
        best_ask=ask,
        best_bid_qty=bq,
        best_ask_qty=aq,
        bid_time=f"{day}T12:00:00+09:00",
        ask_time=f"{day}T12:00:00+09:00",
        reference_price=ref,
        tick_size=tick,
        board_age_sec=0.0 if roll["n_hist_rows"] else None,
        rolling_spread_cost_p95=roll["rolling_spread_cost_p95"],
        rolling_down_bid_jump_p95=roll["rolling_down_bid_jump_p95"],
        rolling_executable_loss_5s_p95=roll["rolling_executable_loss_5s_p95"],
    )
    if roll["history_support_status"] != "OK":
        # force insufficient history into measure by clearing rolling if truly empty
        if roll["rolling_spread_cost_p95"] is None and roll["rolling_down_bid_jump_p95"] is None and roll["rolling_executable_loss_5s_p95"] is None:
            pass  # measure will set RISK_HISTORY_INSUFFICIENT

    out = measure(inp)
    est = out.estimated_execution_risk_yen
    notional = out.one_lot_notional_yen
    # also expose X11-style asof notional (D−1 close×100) for parity
    asof_n = roll["asof_one_lot_notional_yen"]
    return {
        "date": day,
        "symbol": symbol,
        "reference_price": ref,
        "reference_price_source": "previous_session_official_close" if ref is not None else None,
        "best_ask": ask,
        "one_lot_notional_yen": notional,
        "asof_one_lot_notional_yen": asof_n,
        "same_day_panel_notional_yen": same.get("one_lot_notional_yen"),
        "one_tick_risk_yen_100": out.one_tick_risk_yen_100,
        "current_spread_cost_yen_100": out.current_spread_cost_yen_100,
        "spread_cost_yen_100": out.current_spread_cost_yen_100,
        "down_bid_jump_p95": roll["rolling_down_bid_jump_p95"],
        "executable_loss_5s_p95": roll["rolling_executable_loss_5s_p95"],
        "rolling_spread_cost_p95": roll["rolling_spread_cost_p95"],
        "estimated_execution_risk_yen": est,
        "execution_risk": out.execution_risk,
        "strategy_loss_risk": out.strategy_loss_risk,
        "total_trade_risk": out.total_trade_risk,
        "notional_required_capital": required_capital_by_notional(asof_n) if asof_n else (
            required_capital_by_notional(notional) if notional else None
        ),
        "execution_risk_required_capital": (
            required_capital_by_execution_risk(est) if est is not None else None
        ),
        "history_support_status": roll["history_support_status"],
        "measurement_status": out.measurement_status,
        "reason_codes": out.reason_codes,
        "board_age_sec": out.board_age_sec,
        "bid_depth_pass": out.bid_depth_pass,
        "ask_depth_pass": out.ask_depth_pass,
        "board_freshness_pass": out.board_freshness_pass,
        "capital_policy_status": "CAPITAL_POLICY_NOT_EVALUATED",
        "no_same_day_future": roll["no_same_day_future"],
        "history_end": roll["history_end"],
        "mode": "OBSERVER_ONLY",
        "enforcement": False,
        "entry_blocking": False,
    }


def run_historical_replay(
    *,
    symbols: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    panel = load_x10_panel()
    # integrity: no forbidden days in panel keys
    for (_s, d) in panel:
        if d in FORBIDDEN_ALPHA_DAYS:
            raise RuntimeError(f"forbidden day leaked into panel: {d}")
    syms = symbols or PARITY_SYMBOLS
    days = list(DESIGN_DAYS)
    daily: list[dict[str, Any]] = []
    for day in days:
        for sym in syms:
            if (sym, day) not in panel and not any((sym, h) in panel for h in _prior(day, days)):
                continue
            # need at least some history or same-day presence for reporting
            hist = _prior(day, days)
            if not any((sym, h) in panel for h in hist) and (sym, day) not in panel:
                continue
            if not any((sym, h) in panel for h in hist):
                # no prior history — still emit NOT_EVALUABLE row if same-day exists
                if (sym, day) not in panel:
                    continue
            daily.append(replay_symbol_day(sym, day, panel, all_days=days))

    by_sym: dict[str, list[dict]] = {}
    for r in daily:
        by_sym.setdefault(r["symbol"], []).append(r)

    symbol_metrics = []
    for sym, rows in sorted(by_sym.items()):
        ests = [float(r["estimated_execution_risk_yen"]) for r in rows if r.get("estimated_execution_risk_yen") is not None]
        notions = [float(r["asof_one_lot_notional_yen"] or r["one_lot_notional_yen"])
                   for r in rows if (r.get("asof_one_lot_notional_yen") or r.get("one_lot_notional_yen")) is not None]
        symbol_metrics.append({
            "symbol": sym,
            "n_days": len(rows),
            "one_lot_notional_median": _med(notions),
            "estimated_execution_risk_median": _med(ests),
            "n_evaluable": sum(1 for r in rows if r.get("history_support_status") == "OK"),
        })

    kioxia = [r for r in daily if r["symbol"] == TARGET_SYMBOL]
    return {
        "days": days,
        "symbols": list(syms),
        "daily": daily,
        "symbol_metrics": symbol_metrics,
        "kioxia_285A": kioxia,
        "forbidden_days_opened": False,
        "risk_only_alpha_used": False,
        "rounding_contract": {
            "display_round": "float; parity allows display rounding / decimal conversion",
            "one_lot_notional_event": "best_ask × 100",
            "one_lot_notional_asof_parity": "D−1 reference_price × 100 (X10/X11)",
            "estimated_execution_risk": "max(median prior p95 spread, jump, exec5s)",
            "no_future_history": True,
        },
    }
