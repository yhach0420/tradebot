#!/usr/bin/env python3
"""
Phase178: Replay-only review for excluding extremely low-liquidity accepted trades.

Compare:
- A: current (no extra filter; uses existing structural_trades.csv)
- B: add low-liquidity exclusion (post-hoc) based on acceptance-time liquidity fields in small_paper_events.csv

Constraints:
- fixed thresholds only (no tuning)
- replay-only: reads historical session dirs under kabu_native/results/small_paper/

Writes:
- kabu_native/results/reports/phase178_low_liquidity_filter_review.json
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional


BASE = Path("kabu_native/results/small_paper")
OUT = Path("kabu_native/results/reports/phase178_low_liquidity_filter_review.json")

# Fixed thresholds (Phase178; do not tune per day)
TRADING_VALUE_MIN = 1e8
TURNOVER_PROXY_MIN = 0.002

# Phase173/174 reference set (7 sessions)
SESSIONS = [
    BASE / "20260519" / "live_full_session_081047",
    BASE / "20260520" / "live_full_session_080745",
    BASE / "20260520" / "push_replay_001932",
    BASE / "20260520" / "push_replay_231314",
    BASE / "20260521" / "live_full_session_081418",
    BASE / "20260522" / "live_full_session_081229",
    BASE / "20260525" / "live_session_075733",
]


def _f(x: Any) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _pf(win: float, loss: float) -> Optional[float]:
    gl = abs(loss)
    if gl <= 0:
        return None if win <= 0 else float("inf")
    return win / gl


def _mean(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return sum(xs) / len(xs)


def _quantiles(xs: list[float]) -> dict[str, Optional[float]]:
    if not xs:
        return {"p10": None, "p50": None, "p90": None}
    ys = sorted(xs)
    def q(p: float) -> float:
        if len(ys) == 1:
            return ys[0]
        idx = (len(ys) - 1) * p
        lo = int(math.floor(idx))
        hi = int(math.ceil(idx))
        if lo == hi:
            return ys[lo]
        w = idx - lo
        return ys[lo] * (1 - w) + ys[hi] * w
    return {"p10": q(0.10), "p50": q(0.50), "p90": q(0.90)}


def _accept_key(symbol: str, entry_time: str) -> str:
    return f"{symbol}|{entry_time}"


def _load_accept_liquidity(events_csv: Path) -> dict[str, dict[str, Any]]:
    """
    Build map: (symbol, entry_time) -> liquidity snapshot at acceptance time.
    """
    out: dict[str, dict[str, Any]] = {}
    if not events_csv.is_file():
        return out
    with events_csv.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            if str(row.get("event_type") or "") != "accepted":
                continue
            sym = str(row.get("symbol") or "").strip()
            ent = str(row.get("entry_time") or "").strip()
            if not sym or not ent:
                continue
            k = _accept_key(sym, ent)
            out[k] = {
                "symbol": sym,
                "entry_time": ent,
                "current_price": _f(row.get("current_price")),
                "daytrade_suitability_score": _f(row.get("daytrade_suitability_score")),
                "atr_pct": _f(row.get("atr_pct")),
                "intraday_range_pct": _f(row.get("intraday_range_pct")),
                "trading_value": _f(row.get("trading_value")),
                "turnover_proxy": _f(row.get("turnover_proxy")),
            }
    return out


def _passes_liquidity(liq: dict[str, Any]) -> bool:
    tv = _f(liq.get("trading_value"))
    to = _f(liq.get("turnover_proxy"))
    if tv is not None and tv < TRADING_VALUE_MIN:
        return False
    if to is not None and to < TURNOVER_PROXY_MIN:
        return False
    return True


def _load_trades(trades_csv: Path) -> list[dict[str, Any]]:
    if not trades_csv.is_file():
        return []
    out: list[dict[str, Any]] = []
    with trades_csv.open("r", encoding="utf-8", newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            out.append(row)
    return out


def _summarize(trades: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [_f(t.get("realized_pnl_pct")) or 0.0 for t in trades]
    wins = sum(x for x in pnls if x > 0)
    losses = sum(x for x in pnls if x < 0)
    pf = _pf(wins, losses)
    mfe_caps: list[float] = []
    stop_hit = 0
    reasons = Counter()
    liquidity_tv: list[float] = []
    liquidity_to: list[float] = []

    for t in trades:
        reasons[str(t.get("close_reason") or "")] += 1
        if str(t.get("close_reason") or "") == "stop_hit":
            stop_hit += 1
        mfe = _f(t.get("mfe_pct"))
        pnl = _f(t.get("realized_pnl_pct"))
        if mfe is not None and mfe > 0 and pnl is not None:
            mfe_caps.append(pnl / mfe)
        # Liquidity fields are injected by caller when available.
        tv = _f(t.get("_trading_value"))
        to = _f(t.get("_turnover_proxy"))
        if tv is not None:
            liquidity_tv.append(tv)
        if to is not None:
            liquidity_to.append(to)

    return {
        "trade_count": len(trades),
        "total_pnl": round(sum(pnls), 4),
        "avg_pnl": round(sum(pnls) / max(1, len(pnls)), 4) if trades else 0.0,
        "total_win_pnl": round(wins, 4),
        "total_loss_pnl": round(losses, 4),
        "pf": round(pf, 4) if pf is not None and pf not in (float("inf"),) else pf,
        "mfe_capture_avg": round(_mean(mfe_caps), 4) if mfe_caps else None,
        "stop_hit_count": int(stop_hit),
        "exit_reason_counts": dict(reasons),
        "liquidity_distribution": {
            "trading_value": {k: (round(v, 2) if v is not None else None) for k, v in _quantiles(liquidity_tv).items()},
            "turnover_proxy": {k: (round(v, 6) if v is not None else None) for k, v in _quantiles(liquidity_to).items()},
        },
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    per_session: list[dict[str, Any]] = []
    excluded_trade_rows: list[dict[str, Any]] = []

    agg_A: list[dict[str, Any]] = []
    agg_B: list[dict[str, Any]] = []

    for sdir in SESSIONS:
        sid = str(sdir.relative_to(BASE)).replace("\\", "/")
        trades_csv = sdir / "structural_trades.csv"
        events_csv = sdir / "small_paper_events.csv"
        if not trades_csv.is_file() or not events_csv.is_file():
            per_session.append({"session_id": sid, "ok": False, "reason": "missing_trades_or_events"})
            continue

        liq_map = _load_accept_liquidity(events_csv)
        trades = _load_trades(trades_csv)

        # Attach liquidity snapshot (if any) for reporting/distribution.
        for t in trades:
            k = _accept_key(str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
            liq = liq_map.get(k) or {}
            t["_trading_value"] = liq.get("trading_value")
            t["_turnover_proxy"] = liq.get("turnover_proxy")

        trades_A = trades
        trades_B: list[dict[str, Any]] = []
        low_tv_accept = 0
        low_to_accept = 0
        for t in trades:
            k = _accept_key(str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
            liq = liq_map.get(k)
            if liq is None:
                # If missing liquidity snapshot, keep (do not accidentally bias).
                trades_B.append(t)
                continue
            tv = _f(liq.get("trading_value"))
            to = _f(liq.get("turnover_proxy"))
            if tv is not None and tv < TRADING_VALUE_MIN:
                low_tv_accept += 1
            if to is not None and to < TURNOVER_PROXY_MIN:
                low_to_accept += 1
            if _passes_liquidity(liq):
                trades_B.append(t)
            else:
                excluded_trade_rows.append(
                    {
                        "session_id": sid,
                        "symbol": liq.get("symbol"),
                        "entry_time": liq.get("entry_time"),
                        "trading_value": tv,
                        "turnover_proxy": to,
                        "realized_pnl_pct": _f(t.get("realized_pnl_pct")),
                        "close_reason": t.get("close_reason"),
                        "excluded_reason": "low_liquidity",
                    }
                )

        sum_A = _summarize(trades_A)
        sum_B = _summarize(trades_B)

        per_session.append(
            {
                "session_id": sid,
                "ok": True,
                "A_current": sum_A,
                "B_low_liquidity_exclusion": sum_B,
                "low_trading_value_trade_count_A": low_tv_accept,
                "low_turnover_proxy_trade_count_A": low_to_accept,
            }
        )
        agg_A.extend(trades_A)
        agg_B.extend(trades_B)

    report = {
        "phase": 178,
        "verdict": "review_only",
        "constraints": [
            "replay_only",
            "fixed_thresholds_only",
            "no_parameter_search",
            "no_prod_yaml_change",
            "shadow_only",
        ],
        "filter_B": {
            "trading_value_min": TRADING_VALUE_MIN,
            "turnover_proxy_min": TURNOVER_PROXY_MIN,
            "note": "Excludes only extreme low-liquidity; does NOT exclude low-price/high-vol if liquidity is strong (e.g. 6659.T should pass).",
        },
        "aggregate": {
            "A_current": _summarize(agg_A),
            "B_low_liquidity_exclusion": _summarize(agg_B),
            "excluded_trade_count": len(excluded_trade_rows),
        },
        "per_session": per_session,
        "excluded_trades_sample": excluded_trade_rows[:200],
        "notes": [
            "This is a post-hoc replay analysis based on existing structural_trades.csv; it does not change entry logic.",
            "Liquidity fields are sourced from acceptance-time small_paper_events.csv rows; if missing, trade is kept in B to avoid bias.",
        ],
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

