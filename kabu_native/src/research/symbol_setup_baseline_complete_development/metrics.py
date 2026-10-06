"""Frozen metric definitions applied to one replay. No second strategy."""
from __future__ import annotations

import math
import statistics
from typing import Any, Optional

from research.symbol_setup_baseline_complete_strategy_precommit.runner import ledger_sha, max_drawdown_yen, profit_factor, trade_bps

REASONS = (
    "FAST_SLOW_CROSS_LOSS",
    "SLOW_TREND_SLOPE_LOSS",
    "BOTH_TREND_COMPONENTS_LOST",
    "FAIL_CLOSE_INVALID_DATA",
    "SESSION_FAIL_CLOSE",
)


def _med(xs: list[float]) -> Optional[float]:
    return float(statistics.median(xs)) if xs else None


def _mean(xs: list[float]) -> Optional[float]:
    return float(sum(xs) / len(xs)) if xs else None


def _pack(trades: list[dict[str, Any]]) -> dict[str, Any]:
    nets = [float(t["net_pnl_yen"]) for t in trades]
    bps = [float(t["pnl_bps"]) for t in trades]
    holds = [float(t["hold_sec"]) for t in trades]
    ordered = sorted(trades, key=lambda t: (float(t["exit_time"]), str(t["symbol"]), float(t["fill_time"])))
    wins = [x for x in nets if x > 0.0]
    losses = [x for x in nets if x < 0.0]
    flats = [x for x in nets if x == 0.0]
    gross_profit = float(sum(wins))
    gross_loss = float(sum(losses))
    pf = profit_factor(nets)
    return {
        "trade_n": len(trades),
        "gross_pnl_yen": float(sum(nets)),
        "explicit_additional_cost_yen": 0.0,
        "net_pnl_yen": float(sum(nets)),
        "gross_profit_yen": gross_profit,
        "gross_loss_yen": gross_loss,
        "PF": None if pf is None else ("+inf" if math.isinf(pf) else float(pf)),
        "win_n": len(wins),
        "loss_n": len(losses),
        "flat_n": len(flats),
        "win_rate": (len(wins) / len(trades)) if trades else None,
        "mean_net_trade_yen": _mean(nets),
        "median_net_trade_yen": _med(nets),
        "mean_net_bps": _mean(bps),
        "median_net_bps": _med(bps),
        "max_drawdown_yen": max_drawdown_yen([float(t["net_pnl_yen"]) for t in ordered]),
        "mean_hold_sec": _mean(holds),
        "median_hold_sec": _med(holds),
    }


def _days(trades: list[dict[str, Any]], dates: list[str]) -> dict[str, float]:
    out = {d: 0.0 for d in dates}
    for t in trades:
        out[str(t["date"])] = out.get(str(t["date"]), 0.0) + float(t["net_pnl_yen"])
    return out


def _day_signs(trades: list[dict[str, Any]], dates: list[str]) -> dict[str, int]:
    pnl = _days(trades, dates)
    pos = sum(1 for d in dates if pnl[d] > 0.0)
    neg = sum(1 for d in dates if pnl[d] < 0.0)
    flat = sum(1 for d in dates if pnl[d] == 0.0)
    return {"positive_day_n": pos, "negative_day_n": neg, "flat_day_n": flat, "eligible_session_n": len(dates)}


def _group(trades: list[dict[str, Any]], dates: list[str]) -> dict[str, Any]:
    chosen = [t for t in trades if str(t["date"]) in set(dates)]
    body = _pack(chosen)
    body.update(_day_signs(chosen, dates))
    return body


def _reason_row(trades: list[dict[str, Any]], reason: str) -> dict[str, Any]:
    xs = [t for t in trades if t["exit_reason"] == reason]
    nets = [float(t["net_pnl_yen"]) for t in xs]
    bps = [float(t["pnl_bps"]) for t in xs]
    holds = [float(t["hold_sec"]) for t in xs]
    return {
        "exit_reason": reason,
        "trade_n": len(xs),
        "net_pnl_yen": float(sum(nets)),
        "mean_net_yen": _mean(nets),
        "median_net_yen": _med(nets),
        "mean_bps": _mean(bps),
        "median_bps": _med(bps),
        "mean_hold_sec": _mean(holds),
        "median_hold_sec": _med(holds),
    }


def _annotate(port_trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for raw in port_trades:
        src = raw.get("src") or raw
        entry = float(raw["fill_price"])
        exit_px = float(raw["exit_price"])
        rows.append(
            {
                "date": str(raw["date"]),
                "symbol": str(raw["symbol"]),
                "signal_time": float(src.get("signal_time") or src.get("t0")),
                "pending_time": float(src.get("pending_time") or src.get("t0")),
                "fill_time": float(raw["fill_time"]),
                "entry_price": entry,
                "entry_source": "SIMPLE_TECH_V1_PASSIVE_BID_W5",
                "exit_time": float(raw["exit_time"]),
                "exit_price": exit_px,
                "exit_reason": str(raw.get("exit_reason") or ""),
                "thesis_first_observation": src.get("thesis_first_observation"),
                "thesis_lost_at": src.get("thesis_lost_at"),
                "thesis_loss_reason": src.get("thesis_loss_reason"),
                "hold_sec": float(raw["exit_time"]) - float(raw["fill_time"]),
                "net_pnl_yen": float(raw["pnl_yen_100"]),
                "pnl_yen_100": float(raw["pnl_yen_100"]),
                "pnl_bps": trade_bps(entry, exit_px),
            }
        )
    rows.sort(key=lambda t: (t["date"], t["symbol"], t["fill_time"]))
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    reentry_ok = True
    for row in rows:
        key = (row["date"], row["symbol"])
        prev = seen.get(key)
        if prev is None:
            row["entry_number_for_symbol"] = 1
        else:
            row["entry_number_for_symbol"] = int(prev["entry_number_for_symbol"]) + 1
            if float(prev["exit_time"]) > float(row["signal_time"]) + 1e-9:
                reentry_ok = False
        seen[key] = row
    for row in rows:
        row["reentry_slot_release_ok"] = reentry_ok
    return rows


def summarize(run: dict[str, Any], *, dates: list[str], original: list[str], extension: list[str], folds: dict[str, list[str]]) -> dict[str, Any]:
    port = run["portfolio"]
    trades = _annotate(list(port["trades"]))
    primary = _pack(trades)
    pending = int(port["admitted_n"])
    fill_n = int(port["fill_n"])
    unclosed = [
        s
        for s in run["signals"]
        if s.get("WOULD_FILL") and not s.get("closed")
    ]
    funnel = {
        "candidate_signal_n": int(run["counts"]["s5"]),
        "board_pass_n": int(run["counts"]["s6"]),
        "execution_eligible_n": int(run["counts"]["s7"]),
        "pending_n": pending,
        "expired_n": int(port["expired_n"]),
        "fill_n": fill_n,
        "fill_rate": (fill_n / pending) if pending else None,
        "CAP_blocked_n": int(port["cap_blocked"]),
        "same_symbol_blocked_n": int(port["same_symbol_blocked"]),
        "first_entry_n": sum(1 for t in trades if int(t["entry_number_for_symbol"]) == 1),
        "reentry_n": sum(1 for t in trades if int(t["entry_number_for_symbol"]) > 1),
        "exit_n": len(trades),
        "open_position_end_n": int(port["open_leftover_n"]) + len(unclosed),
        "all_filled_positions_closed": int(port["open_leftover_n"]) + len(unclosed) == 0,
    }
    reentry_rows = [t for t in trades if int(t["entry_number_for_symbol"]) > 1]
    first_rows = [t for t in trades if int(t["entry_number_for_symbol"]) == 1]
    daily = []
    by_day: dict[str, list[dict[str, Any]]] = {d: [] for d in dates}
    for t in trades:
        by_day.setdefault(str(t["date"]), []).append(t)
    sig_by_day: dict[str, int] = {d: 0 for d in dates}
    pend_ids = {id(c) for c in port["candidates"] if c.get("s8_pending")}
    for s in run["signals"]:
        sig_by_day[str(s["date"])] = sig_by_day.get(str(s["date"]), 0) + 1
    admitted_by_day: dict[str, int] = {d: 0 for d in dates}
    for c in port["candidates"]:
        if c.get("s8_pending"):
            admitted_by_day[str(c["date"])] = admitted_by_day.get(str(c["date"]), 0) + 1
    _ = pend_ids
    for day in dates:
        xs = by_day.get(day, [])
        pack = _pack(xs)
        daily.append(
            {
                "date": day,
                "universe_n": 50,
                "signal_n": int(sig_by_day.get(day, 0)),
                "pending_n": int(admitted_by_day.get(day, 0)),
                "fill_n": len(xs),
                "trade_n": len(xs),
                "net_pnl_yen": pack["net_pnl_yen"],
                "PF": pack["PF"],
                "wins": pack["win_n"],
                "losses": pack["loss_n"],
                "maxDD": pack["max_drawdown_yen"],
            }
        )
    by_sym: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        by_sym.setdefault(str(t["symbol"]), []).append(t)
    symbols = []
    for sym in sorted(by_sym):
        xs = by_sym[sym]
        pack = _pack(xs)
        symbols.append(
            {
                "symbol": sym,
                "trade_n": pack["trade_n"],
                "net_pnl_yen": pack["net_pnl_yen"],
                "PF": pack["PF"],
                "mean_net_yen": pack["mean_net_trade_yen"],
                "mean_bps": pack["mean_net_bps"],
                "wins": pack["win_n"],
                "losses": pack["loss_n"],
            }
        )
    ranked = sorted(trades, key=lambda t: float(t["net_pnl_yen"]), reverse=True)
    gross_profit = float(primary["gross_profit_yen"])
    top1 = ranked[0] if ranked else None
    top5 = ranked[:5]
    day_pnl = _days(trades, dates)
    top_day = max(dates, key=lambda d: day_pnl[d]) if dates else None
    top_sym = max(symbols, key=lambda r: float(r["net_pnl_yen"])) if symbols else None

    def _frac(part: float) -> Optional[float]:
        if gross_profit <= 0.0 or part <= 0.0:
            return None
        return float(part) / gross_profit

    top1_pnl = float(top1["net_pnl_yen"]) if top1 else 0.0
    top5_pnl = float(sum(float(t["net_pnl_yen"]) for t in top5))
    top_day_pnl = float(day_pnl[top_day]) if top_day else 0.0
    top_sym_pnl = float(top_sym["net_pnl_yen"]) if top_sym else 0.0
    conc = {
        "top1_trade_pnl": top1_pnl,
        "top5_trade_pnl": top5_pnl,
        "top1_trade_fraction_of_total_positive_profit": _frac(top1_pnl),
        "top5_trade_fraction_of_total_positive_profit": _frac(sum(max(0.0, float(t["net_pnl_yen"])) for t in top5)),
        "top1_day": top_day,
        "top1_day_pnl": top_day_pnl,
        "top1_day_fraction": _frac(top_day_pnl),
        "top1_symbol": None if top_sym is None else top_sym["symbol"],
        "top1_symbol_pnl": top_sym_pnl,
        "top1_symbol_fraction": _frac(top_sym_pnl),
        "pnl_excluding_top1_trade": float(primary["net_pnl_yen"]) - top1_pnl,
        "pnl_excluding_top5_trades": float(primary["net_pnl_yen"]) - top5_pnl,
        "pnl_excluding_top1_day": float(primary["net_pnl_yen"]) - top_day_pnl,
        "pnl_excluding_top1_symbol": float(primary["net_pnl_yen"]) - top_sym_pnl,
    }
    groups = {
        "ORIGINAL18": _group(trades, original),
        "EXTENSION17": _group(trades, extension),
        "FOLD_A": _group(trades, folds["FOLD_A"]),
        "FOLD_B": _group(trades, folds["FOLD_B"]),
        "FOLD_C": _group(trades, folds["FOLD_C"]),
    }
    signal_rows = [
        {"date": s["date"], "symbol": s["symbol"], "t0": s["signal_time"], "would_fill": bool(s["WOULD_FILL"]), "fill_t": s.get("fill_t")}
        for s in sorted(run["signals"], key=lambda r: (str(r["date"]), float(r["signal_time"]), str(r["symbol"])))
    ]
    fill_rows = [r for r in signal_rows if r["would_fill"]]
    hashes = {
        "signal_ledger_sha256": ledger_sha(signal_rows),
        "fill_ledger_sha256": ledger_sha(fill_rows),
        "trade_ledger_sha256": ledger_sha(trades),
        "daily_summary_sha256": ledger_sha(daily),
    }
    return {
        "funnel": funnel,
        "primary": primary,
        "reasons": [_reason_row(trades, name) for name in REASONS],
        "groups": groups,
        "daily": daily,
        "symbols": symbols,
        "concentration": conc,
        "reentry": {
            "first_entry_trade_n": len(first_rows),
            "first_entry_net_pnl": float(sum(float(t["net_pnl_yen"]) for t in first_rows)),
            "reentry_trade_n": len(reentry_rows),
            "reentry_net_pnl": float(sum(float(t["net_pnl_yen"]) for t in reentry_rows)),
            "reentry_after_slot_release_only": all(bool(t["reentry_slot_release_ok"]) for t in trades),
        },
        "trades": trades,
        "hashes": hashes,
    }


def gates(summary: dict[str, Any]) -> dict[str, Any]:
    p = summary["primary"]
    g = summary["groups"]
    pf = p["PF"]
    pf_ok = pf == "+inf" or (isinstance(pf, (int, float)) and float(pf) > 1.0)
    dd = p["max_drawdown_yen"]
    fold_pos = sum(1 for name in ("FOLD_A", "FOLD_B", "FOLD_C") if float(g[name]["net_pnl_yen"]) > 0.0)
    out = {
        "G1": p["net_pnl_yen"] is not None and float(p["net_pnl_yen"]) > 0.0,
        "G2": bool(pf_ok),
        "G3": p["mean_net_trade_yen"] is not None and float(p["mean_net_trade_yen"]) > 0.0,
        "G4": p["median_net_trade_yen"] is not None and float(p["median_net_trade_yen"]) >= 0.0,
        "G5": isinstance(dd, (int, float)) and math.isfinite(float(dd)),
        "G6": fold_pos >= 2,
        "G7": float(g["ORIGINAL18"]["net_pnl_yen"]) > 0.0 and float(g["EXTENSION17"]["net_pnl_yen"]) > 0.0,
    }
    out["all_pass"] = all(out[k] for k in ("G1", "G2", "G3", "G4", "G5", "G6", "G7"))
    return out
