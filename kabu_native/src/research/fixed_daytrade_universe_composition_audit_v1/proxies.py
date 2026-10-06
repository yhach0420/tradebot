"""Daily OHLC intraday proxies. Same-session geometry vs previous close. Not future return."""
from __future__ import annotations

from typing import Any

from research.fixed_daytrade_universe_v1.schema import finite_number
from research.fixed_daytrade_universe_composition_audit_v1.stats import median, percentile


def session_proxies(*, prev_close: float, open_: float, high: float, low: float, close: float) -> dict[str, float]:
    if prev_close == 0.0:
        raise ValueError("prev_close_zero")
    return {
        "range_over_prev_close": (high - low) / prev_close,
        "abs_body_over_prev_close": abs(close - open_) / prev_close,
        "gap_over_prev_close": (open_ - prev_close) / prev_close,
        "open_to_high_over_prev_close": (high - open_) / prev_close,
        "open_to_low_over_prev_close": (low - open_) / prev_close,
        "close_to_close": (close - prev_close) / prev_close,
    }


def series_proxies(bars: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(bars, key=lambda r: str(r.get("date") or ""))
    rows: list[dict[str, Any]] = []
    prev_c = None
    prev_adj = None
    for raw in ordered:
        o = finite_number(raw.get("open"))
        h = finite_number(raw.get("high"))
        l = finite_number(raw.get("low"))
        c = finite_number(raw.get("close"))
        adj = finite_number(raw.get("adj_close"))
        if o is None or h is None or l is None or c is None:
            prev_c = c
            prev_adj = adj if adj is not None else c
            continue
        if prev_c is None or prev_c == 0.0:
            prev_c = c
            prev_adj = adj if adj is not None else c
            continue
        px = session_proxies(prev_close=prev_c, open_=o, high=h, low=l, close=c)
        base_adj = prev_adj if prev_adj not in {None, 0.0} else prev_c
        use_adj = adj if adj is not None else c
        px["adj_close_to_close"] = (use_adj - base_adj) / base_adj if base_adj else None
        px["date"] = raw.get("date")
        px["va"] = finite_number(raw.get("trading_value"))
        px["vo"] = finite_number(raw.get("volume"))
        rows.append(px)
        prev_c = c
        prev_adj = use_adj
    keys = (
        "range_over_prev_close",
        "abs_body_over_prev_close",
        "gap_over_prev_close",
        "open_to_high_over_prev_close",
        "open_to_low_over_prev_close",
    )
    summary = {}
    for k in keys:
        vals = [r[k] for r in rows if r.get(k) is not None]
        abs_vals = [abs(v) for v in vals]
        summary[k] = {
            "n": len(vals),
            "median": median(vals),
            "median_abs": median(abs_vals),
            "p80_abs": percentile(abs_vals, 0.80),
        }
    rets = [r["adj_close_to_close"] for r in rows if r.get("adj_close_to_close") is not None]
    raw_rets = [r["close_to_close"] for r in rows if r.get("close_to_close") is not None]
    return {
        "session_n": len(ordered),
        "proxy_n": len(rows),
        "summary": summary,
        "returns_adj": rets,
        "returns_raw": raw_rets,
        "dates": [r["date"] for r in rows],
        "va": [r.get("va") for r in rows],
    }


assert abs(session_proxies(prev_close=100.0, open_=101.0, high=104.0, low=99.0, close=102.0)["range_over_prev_close"] - 0.05) < 1e-12
assert abs(session_proxies(prev_close=100.0, open_=101.0, high=104.0, low=99.0, close=102.0)["open_to_low_over_prev_close"] + 0.02) < 1e-12
