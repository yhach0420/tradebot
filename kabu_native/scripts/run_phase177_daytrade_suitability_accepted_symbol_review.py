#!/usr/bin/env python3
"""
Phase177: Review accepted symbols for daytrade suitability (per-symbol).

Data source:
- small_paper_events.csv (contains daytrade_suitability_score, atr_pct, intraday_range_pct, trading_value, turnover_proxy)
- small_paper_summary.json (accepted_symbols list, thresholds)

Writes:
- kabu_native/results/reports/phase177_daytrade_suitability_accepted_symbol_review.json
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional


OUT = Path("kabu_native/results/reports/phase177_daytrade_suitability_accepted_symbol_review.json")

SESSIONS = [
    Path("kabu_native/results/small_paper/20260528/live_session_082247"),
    Path("kabu_native/results/small_paper/20260528/live_session_122515"),
]

FOCUS = {"6659.T", "3656.T", "2693.T", "6969.T", "3777.T"}


def _f(x: Any) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


def _i(x: Any) -> Optional[int]:
    try:
        if x is None or x == "":
            return None
        return int(float(x))
    except (TypeError, ValueError):
        return None


@dataclass
class SymAgg:
    symbol: str
    accepted_count: int = 0
    last_current_price: Optional[float] = None
    daytrade_suitability_score_last: Optional[float] = None
    daytrade_suitability_score_min: Optional[float] = None
    daytrade_suitability_score_max: Optional[float] = None
    atr_pct_last: Optional[float] = None
    intraday_range_pct_last: Optional[float] = None
    trading_value_last: Optional[float] = None
    turnover_proxy_last: Optional[float] = None
    mfe_max: Optional[float] = None
    mfe_avg: Optional[float] = None
    mae_max: Optional[float] = None
    mae_avg: Optional[float] = None
    # Exit reason counts are NOT available per symbol from current live writer outputs.
    trailing_mfe_exit_count: Optional[int] = None
    stop_hit_count: Optional[int] = None
    overlap_replaced_review_count: Optional[int] = None
    risk_flags: dict[str, Any] = None  # type: ignore[assignment]
    classification: str = ""
    notes: list[str] = None  # type: ignore[assignment]


def _update_minmax(cur_min: Optional[float], cur_max: Optional[float], v: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    if v is None:
        return cur_min, cur_max
    if cur_min is None or v < cur_min:
        cur_min = v
    if cur_max is None or v > cur_max:
        cur_max = v
    return cur_min, cur_max


def _mean(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return sum(xs) / len(xs)


def _classify(
    *,
    score: Optional[float],
    threshold: Optional[float],
    price: Optional[float],
    trading_value: Optional[float],
    turnover: Optional[float],
) -> tuple[str, dict[str, Any], list[str]]:
    notes: list[str] = []
    flags: dict[str, Any] = {}

    # Risk heuristics (shadow review; conservative)
    low_price = price is not None and price < 100  # not the gate min (50), but cautionary
    very_low_price = price is not None and price < 50
    thin_value = trading_value is not None and trading_value < 1e8
    very_thin_value = trading_value is not None and trading_value < 5e7
    low_turnover = turnover is not None and turnover < 0.002

    flags["low_price_caution_lt_100"] = bool(low_price)
    flags["very_low_price_lt_50_gate"] = bool(very_low_price)
    flags["thin_liquidity_trading_value_lt_1e8"] = bool(thin_value)
    flags["very_thin_liquidity_trading_value_lt_5e7"] = bool(very_thin_value)
    flags["low_turnover_proxy_lt_0p002"] = bool(low_turnover)

    # If we have a score+threshold, use it. Otherwise fall back to liquidity/price heuristics.
    if threshold is not None and score is not None:
        if score < threshold:
            notes.append("score_below_threshold")
            return "exclude_candidate", flags, notes
        if very_low_price or very_thin_value:
            notes.append("passes_score_but_price_or_liquidity_risky")
            return "watch", flags, notes
        if low_price or thin_value or low_turnover:
            notes.append("passes_score_with_cautions")
            return "watch", flags, notes
        return "keep", flags, notes

    notes.append("missing_daytrade_suitability_score_or_threshold_fallback_to_liquidity")
    # Fallback:
    if very_low_price:
        return "exclude_candidate", flags, notes
    if very_thin_value:
        return "exclude_candidate", flags, notes
    if thin_value or low_turnover:
        return "watch", flags, notes
    if low_price:
        return "watch", flags, notes
    # strong liquidity by value is a decent proxy
    if trading_value is not None and trading_value >= 1e9:
        return "keep", flags, notes
    return "watch", flags, notes


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)

    per_sym: dict[str, SymAgg] = {}
    session_meta: list[dict[str, Any]] = []
    threshold_any: Optional[float] = None

    for sdir in SESSIONS:
        summ_path = sdir / "small_paper_summary.json"
        ev_path = sdir / "small_paper_events.csv"
        summ = json.loads(summ_path.read_text(encoding="utf-8")) if summ_path.is_file() else {}
        if "daytrade_suitability_threshold" in summ and threshold_any is None:
            threshold_any = _f(summ.get("daytrade_suitability_threshold"))
        session_meta.append(
            {
                "session_dir": str(sdir).replace("\\", "/"),
                "generated_at": summ.get("generated_at"),
                "ended_at": summ.get("ended_at"),
                "accepted_count": summ.get("accepted_count"),
                "policy_label": summ.get("policy_label"),
                "structural_exit_policy": summ.get("structural_exit_policy"),
                "daytrade_suitability_threshold": summ.get("daytrade_suitability_threshold"),
            }
        )

        if not ev_path.is_file():
            continue
        # First collect accepted counts, but also keep the latest available daytrade/liq fields
        # from ANY row for that symbol (accepted rows sometimes omit these fields).
        with ev_path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            for r in reader:
                sym = str(r.get("symbol") or "").strip()
                if not sym:
                    continue
                et = str(r.get("event_type") or "")
                if et == "accepted":
                    agg = per_sym.get(sym)
                    if agg is None:
                        agg = SymAgg(symbol=sym, risk_flags={}, notes=[])
                        per_sym[sym] = agg
                    agg.accepted_count += 1
                # Only enrich metrics for symbols that were accepted at least once (same session),
                # or ones we have already seen as accepted in earlier rows.
                if sym not in per_sym:
                    continue
                agg = per_sym[sym]

                price = _f(r.get("current_price"))
                score = _f(r.get("daytrade_suitability_score"))
                atr = _f(r.get("atr_pct"))
                rng = _f(r.get("intraday_range_pct"))
                tv = _f(r.get("trading_value"))
                to = _f(r.get("turnover_proxy"))

                if price is not None:
                    agg.last_current_price = price
                if score is not None:
                    agg.daytrade_suitability_score_last = score
                    agg.daytrade_suitability_score_min, agg.daytrade_suitability_score_max = _update_minmax(
                        agg.daytrade_suitability_score_min, agg.daytrade_suitability_score_max, score
                    )
                if atr is not None:
                    agg.atr_pct_last = atr
                if rng is not None:
                    agg.intraday_range_pct_last = rng
                if tv is not None:
                    agg.trading_value_last = tv
                if to is not None:
                    agg.turnover_proxy_last = to

    # Second pass: compute mfe/mae aggregates by rescanning accepted rows
    mfe_bucket: dict[str, list[float]] = defaultdict(list)
    mae_bucket: dict[str, list[float]] = defaultdict(list)
    for sdir in SESSIONS:
        ev_path = sdir / "small_paper_events.csv"
        if not ev_path.is_file():
            continue
        with ev_path.open("r", encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                if str(r.get("event_type") or "") != "accepted":
                    continue
                sym = str(r.get("symbol") or "").strip()
                if not sym:
                    continue
                mfe = _f(r.get("rolling_mfe_pct"))
                mae = _f(r.get("rolling_mae_pct"))
                if mfe is not None:
                    mfe_bucket[sym].append(mfe)
                if mae is not None:
                    mae_bucket[sym].append(mae)

    # Finalize per symbol
    rows: list[dict[str, Any]] = []
    for sym, agg in sorted(per_sym.items(), key=lambda kv: (-kv[1].accepted_count, kv[0])):
        mfes = mfe_bucket.get(sym, [])
        maes = mae_bucket.get(sym, [])
        agg.mfe_max = max(mfes) if mfes else None
        agg.mfe_avg = _mean(mfes)
        agg.mae_max = min(maes) if maes else None  # MAE is usually <=0
        agg.mae_avg = _mean(maes)

        cls, flags, notes = _classify(
            score=agg.daytrade_suitability_score_last,
            threshold=threshold_any,
            price=agg.last_current_price,
            trading_value=agg.trading_value_last,
            turnover=agg.turnover_proxy_last,
        )
        agg.classification = cls
        agg.risk_flags = flags
        agg.notes = notes

        rows.append(asdict(agg))

    focus_rows = [r for r in rows if r["symbol"] in FOCUS]
    missing_focus = sorted([s for s in FOCUS if s not in per_sym])

    report = {
        "phase": 177,
        "date": "20260528",
        "sessions": session_meta,
        "limits": {
            "daytrade_suitability_threshold": threshold_any,
            "notes": [
                "Per-symbol exit reason counts (trailing_mfe_exit/stop_hit/overlap_replaced_review) are not recorded in small_paper_events.csv in current live writer output. Session-level aggregates exist in small_paper_summary.json.",
                "This report uses accepted events as the per-symbol evidence set and classifies keep/watch/exclude_candidate using score vs threshold plus conservative liquidity/price cautions.",
            ],
        },
        "focus_symbols": {
            "requested": sorted(list(FOCUS)),
            "found": [r["symbol"] for r in focus_rows],
            "missing": missing_focus,
            "rows": focus_rows,
        },
        "by_symbol": rows,
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

