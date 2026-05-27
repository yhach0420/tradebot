"""
Phase 81: Liquidity / volatility metrics for accepted structural trades at entry.
"""

from __future__ import annotations

import json
import statistics
from bisect import bisect_right
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

# Tier thresholds aligned with phase81 universe audit
TIER_LARGE_JPY = 500_000_000_000
TIER_MID_JPY = 100_000_000_000


def _tier(mc: Optional[float]) -> str:
    if mc is None or mc <= 0:
        return "unknown"
    if mc >= TIER_LARGE_JPY:
        return "large"
    if mc >= TIER_MID_JPY:
        return "mid"
    return "small"


def _tier_ja(tier: str) -> str:
    return {"large": "大型", "mid": "中型", "small": "小型", "unknown": "不明"}.get(tier, tier)


def _parse_ts(iso: str) -> float:
    try:
        return datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def metrics_from_payload(payload: Mapping[str, Any], *, entry_price: float) -> dict[str, Optional[float]]:
    high = _float(payload.get("HighPrice")) or 0.0
    low = _float(payload.get("LowPrice")) or 0.0
    prev = _float(payload.get("PreviousClose")) or 0.0
    open_px = _float(payload.get("OpeningPrice")) or 0.0
    px = entry_price if entry_price > 0 else (_float(payload.get("CurrentPrice")) or 0.0)
    ref = prev if prev > 0 else (open_px if open_px > 0 else px)

    intraday_range_pct: Optional[float] = None
    if ref > 0 and high >= low:
        intraday_range_pct = (high - low) / ref * 100.0

    atr_pct: Optional[float] = None
    if px > 0 and high >= low:
        if prev > 0:
            tr = max(high - low, abs(high - prev), abs(low - prev))
        else:
            tr = high - low
        atr_pct = tr / px * 100.0

    return {
        "intraday_range_pct": round(intraday_range_pct, 4) if intraday_range_pct is not None else None,
        "atr_pct": round(atr_pct, 4) if atr_pct is not None else None,
        "trading_volume": _float(payload.get("TradingVolume")),
        "trading_value_jpy": _float(payload.get("TradingValue")),
        "market_cap_jpy": _float(payload.get("TotalMarketValue")),
    }


def load_push_tick_series(push_dir: Path, symbols: set[str]) -> dict[str, list[tuple[float, dict[str, Optional[float]]]]]:
    """Per symbol: sorted (timestamp, metrics_at_tick) including entry-relevant fields."""
    out: dict[str, list[tuple[float, dict[str, Optional[float]]]]] = {}
    for sym in symbols:
        path = push_dir / f"{sym}.jsonl"
        if not path.is_file():
            path = push_dir / f"{sym.replace('.T', '')}.jsonl"
        if not path.is_file():
            continue
        series: list[tuple[float, dict[str, Optional[float]]]] = []
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = _parse_ts(rec.get("recorded_at") or "")
                payload = rec.get("payload") or {}
                px = _float(payload.get("CurrentPrice")) or 0.0
                series.append((ts, metrics_from_payload(payload, entry_price=px)))
        series.sort(key=lambda x: x[0])
        out[sym] = series
    return out


def lookup_metrics_at_entry(
    series: Sequence[tuple[float, dict[str, Optional[float]]]],
    entry_ts: float,
) -> dict[str, Optional[float]]:
    if not series:
        return {}
    times = [t for t, _ in series]
    i = bisect_right(times, entry_ts) - 1
    if i < 0:
        i = 0
    return dict(series[i][1])


def trade_outcome(pnl_pct: Optional[float]) -> str:
    if pnl_pct is None:
        return "unknown"
    if pnl_pct > 0:
        return "win"
    if pnl_pct < 0:
        return "loss"
    return "flat"


def build_accepted_trade_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    push_dir: Path,
    sym_caps: Optional[Mapping[str, float]] = None,
) -> list[dict[str, Any]]:
    symbols = {str(t.get("symbol") or "") for t in trades if t.get("symbol")}
    series_map = load_push_tick_series(push_dir, symbols)
    rows: list[dict[str, Any]] = []
    for t in trades:
        sym = str(t.get("symbol") or "")
        ent = str(t.get("entry_time") or "")
        ent_ts = _parse_ts(ent)
        entry_px = _float(t.get("entry_price")) or 0.0
        pnl = _float(t.get("realized_pnl_pct"))
        push_m = lookup_metrics_at_entry(series_map.get(sym, []), ent_ts)
        mc = push_m.get("market_cap_jpy") or (sym_caps or {}).get(sym)
        tier = _tier(mc)
        outcome = trade_outcome(pnl)
        rows.append(
            {
                "symbol": sym,
                "entry_time": ent,
                "entry_price": entry_px,
                "close_reason": t.get("close_reason"),
                "realized_pnl_pct": pnl,
                "trade_outcome": outcome,
                "market_cap_tier": tier,
                "tier_ja": _tier_ja(tier),
                "market_cap_jpy": mc,
                "market_cap_jpy_trillion": round(mc / 1e12, 4) if mc else None,
                "intraday_range_pct": push_m.get("intraday_range_pct"),
                "atr_pct": push_m.get("atr_pct"),
                "trading_volume": push_m.get("trading_volume"),
                "trading_value_jpy": push_m.get("trading_value_jpy"),
                "trading_value_jpy_oku": round((push_m.get("trading_value_jpy") or 0) / 1e8, 2)
                if push_m.get("trading_value_jpy")
                else None,
                "continuation_quality_score": _float(t.get("continuation_quality_score")),
            }
        )
    return rows


def _summarize_segment(
    rows: Sequence[Mapping[str, Any]],
    *,
    segment: str,
    segment_ja: str = "",
) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {"segment": segment, "segment_ja": segment_ja or segment, "trade_count": 0}

    def col(name: str) -> list[float]:
        return [float(r[name]) for r in rows if r.get(name) is not None]

    def stats(name: str) -> dict[str, Optional[float]]:
        vals = col(name)
        if not vals:
            return {"mean": None, "median": None}
        return {
            "mean": round(statistics.mean(vals), 4),
            "median": round(statistics.median(vals), 4),
        }

    ir = stats("intraday_range_pct")
    atr = stats("atr_pct")
    vol = stats("trading_volume")
    tv = stats("trading_value_jpy")
    mc = stats("market_cap_jpy")
    pnl = stats("realized_pnl_pct")
    wins = sum(1 for r in rows if r.get("trade_outcome") == "win")
    losses = sum(1 for r in rows if r.get("trade_outcome") == "loss")

    return {
        "segment": segment,
        "segment_ja": segment_ja or segment,
        "trade_count": n,
        "win_count": wins,
        "loss_count": losses,
        "win_rate": round(wins / n, 4) if n else None,
        "intraday_range_pct_mean": ir["mean"],
        "intraday_range_pct_median": ir["median"],
        "atr_pct_mean": atr["mean"],
        "atr_pct_median": atr["median"],
        "trading_volume_mean": vol["mean"],
        "trading_volume_median": vol["median"],
        "trading_value_jpy_mean": tv["mean"],
        "trading_value_jpy_median": tv["median"],
        "market_cap_jpy_mean": mc["mean"],
        "market_cap_jpy_median": mc["median"],
        "realized_pnl_pct_mean": pnl["mean"],
        "realized_pnl_pct_median": pnl["median"],
    }


def build_liquidity_comparison(trade_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_tier: dict[str, list[dict[str, Any]]] = {}
    by_outcome: dict[str, list[dict[str, Any]]] = {}
    for r in trade_rows:
        by_tier.setdefault(str(r.get("market_cap_tier") or "unknown"), []).append(dict(r))
        by_outcome.setdefault(str(r.get("trade_outcome") or "unknown"), []).append(dict(r))

    tier_rows = [
        _summarize_segment(by_tier[t], segment=t, segment_ja=_tier_ja(t))
        for t in ("large", "mid", "small", "unknown")
        if t in by_tier
    ]
    outcome_rows = [
        _summarize_segment(by_outcome[o], segment=o, segment_ja={"win": "勝ち", "loss": "負け", "flat": "引分"}.get(o, o))
        for o in ("win", "loss", "flat")
        if o in by_outcome
    ]

    cross: list[dict[str, Any]] = []
    for tier in ("large", "mid", "small"):
        for outcome in ("win", "loss"):
            sub = [
                r
                for r in trade_rows
                if r.get("market_cap_tier") == tier and r.get("trade_outcome") == outcome
            ]
            if sub:
                cross.append(
                    _summarize_segment(
                        sub,
                        segment=f"{tier}_{outcome}",
                        segment_ja=f"{_tier_ja(tier)}×{'勝ち' if outcome == 'win' else '負け'}",
                    )
                )

    return {
        "by_market_cap_tier": tier_rows,
        "by_trade_outcome": outcome_rows,
        "by_tier_and_outcome": cross,
        "metric_notes": {
            "intraday_range_pct": "(HighPrice-LowPrice)/PreviousClose*100 at entry tick",
            "atr_pct": "TrueRange/entry_price*100 (TR=max(H-L,|H-PrevClose|,|L-PrevClose|)) at entry tick",
            "trading_volume": "session cumulative TradingVolume at entry",
            "trading_value_jpy": "session cumulative TradingValue at entry",
            "market_cap_jpy": "TotalMarketValue at entry",
        },
    }
