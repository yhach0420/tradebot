"""
Phase 38: Expanded regime validation (crash / gap / liquidity).
"""

from __future__ import annotations

import csv
import statistics
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.entry_v2 import MOMENTUM_V13_COMBINED_REFERENCE
from research.phase37_validation import REGIME_LABELS, _load_day_market_proxy, classify_regime
from research.research_exit_criteria import (
    _as_float,
    _load_csv,
    _load_json,
    _market_structure_consistency,
    _trade_metrics_from_rows,
)

EXPANDED_REGIME_LABELS = REGIME_LABELS + (
    "crash_like",
    "gap_up",
    "gap_down",
    "low_liquidity",
    "high_liquidity",
)


def _prev_trading_day(day: str, data_roots: Sequence[Path]) -> Optional[str]:
    dlist: list[str] = []
    for root in data_roots:
        if not root.is_dir():
            continue
        dlist = sorted(
            c.name for c in root.iterdir() if c.is_dir() and len(c.name) == 10 and c.name < day
        )
        if dlist:
            return dlist[-1]
    return None


def _symbol_day_close(day: str, symbol: str, data_roots: Sequence[Path]) -> Optional[float]:
    for root in data_roots:
        p = root / day / f"{symbol}.csv"
        if not p.is_file():
            continue
        with p.open(encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue
        for r in reversed(rows):
            c = _as_float(r.get("close"))
            if c:
                return c
    return None


def _day_liquidity_score(day: str, data_roots: Sequence[Path], symbols: Sequence[str]) -> float:
    """Proxy: count of symbols with sufficient bars / median bar count."""
    counts: list[int] = []
    for sym in symbols[:30]:
        for root in data_roots:
            p = root / day / f"{sym}.csv"
            if p.is_file():
                with p.open(encoding="utf-8", newline="") as f:
                    counts.append(sum(1 for _ in csv.DictReader(f)))
                break
    return float(statistics.median(counts)) if counts else 0.0


def classify_expanded_regime(
    day: str,
    proxy: Mapping[str, Any],
    *,
    data_roots: Sequence[Path],
    symbols: Sequence[str],
    prev_close_median: Optional[float] = None,
    open_median: Optional[float] = None,
    liquidity: Optional[float] = None,
) -> str:
    ret = float(proxy.get("median_return_pct") or 0)
    rng = proxy.get("median_range_pct")

    if ret <= -1.2 or (rng is not None and rng >= 2.0 and ret < -0.5):
        return "crash_like"

    if prev_close_median and open_median and prev_close_median > 0:
        gap_pct = ((open_median - prev_close_median) / prev_close_median) * 100.0
        if gap_pct >= 0.6:
            return "gap_up"
        if gap_pct <= -0.6:
            return "gap_down"

    if liquidity is not None:
        if liquidity < 120:
            return "low_liquidity"
        if liquidity >= 280:
            return "high_liquidity"

    return classify_regime(ret, rng)


def build_expanded_regime_validation(
    run_dir: Path,
    *,
    data_roots: Sequence[Path],
    focus_profile: str = MOMENTUM_V13_COMBINED_REFERENCE,
    profiles: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    from research.phase37_validation import VALIDATION_PROFILES

    profiles = list(profiles or VALIDATION_PROFILES)
    trades = _load_csv(run_dir / "trades_by_profile.csv")
    ps = _load_json(run_dir / "profile_summary.json") or {}
    symbols = list(ps.get("symbols") or [])

    days = sorted({str(t.get("trade_date", ""))[:10] for t in trades if t})
    days = [d for d in days if d]

    day_regimes: dict[str, dict[str, Any]] = {}
    for d in days:
        proxy = _load_day_market_proxy(d, data_roots, symbols=symbols or None)
        if proxy is None:
            continue
        prev = _prev_trading_day(d, data_roots)
        prev_closes = []
        if prev and symbols:
            for sym in symbols[:15]:
                pc = _symbol_day_close(prev, sym, data_roots)
                if pc:
                    prev_closes.append(pc)
        prev_med = statistics.median(prev_closes) if prev_closes else None
        open_vals = []
        for sym in symbols[:15]:
            for root in data_roots:
                p = root / d / f"{sym}.csv"
                if p.is_file():
                    with p.open(encoding="utf-8", newline="") as f:
                        rows = list(csv.DictReader(f))
                    if rows:
                        o = _as_float(rows[0].get("open"))
                        if o:
                            open_vals.append(o)
                    break
        open_med = statistics.median(open_vals) if open_vals else None
        liq = _day_liquidity_score(d, data_roots, symbols)
        reg = classify_expanded_regime(
            d,
            proxy,
            data_roots=data_roots,
            symbols=symbols,
            prev_close_median=prev_med,
            open_median=open_med,
            liquidity=liq,
        )
        day_regimes[d] = {**proxy, "liquidity_proxy": liq, "regime": reg}

    per_profile: dict[str, Any] = {}
    durability: dict[str, Any] = {}

    for profile in profiles:
        grp = [t for t in trades if str(t.get("profile")) == profile]
        by_regime: dict[str, list[Mapping[str, Any]]] = {r: [] for r in EXPANDED_REGIME_LABELS}
        for t in grp:
            d = str(t.get("trade_date", ""))[:10]
            reg = (day_regimes.get(d) or {}).get("regime", "sideways")
            by_regime.setdefault(reg, []).append(t)

        regime_stats: dict[str, Any] = {}
        consistencies: list[float] = []
        for reg, rtrades in by_regime.items():
            if not rtrades:
                regime_stats[reg] = {"trade_count": 0}
                continue
            msc = _market_structure_consistency(rtrades, profile)
            tm = _trade_metrics_from_rows(rtrades, profile)
            mc = _as_float(msc.get("momentum_continuation_consistency"))
            pc = _as_float(msc.get("continuation_persistence_consistency"))
            ba = _as_float(msc.get("bearish_accumulation_consistency"))
            if mc is not None:
                consistencies.append(mc)
            regime_stats[reg] = {
                "trade_count": len(rtrades),
                "profit_factor": tm.get("profit_factor"),
                "momentum_continuation_consistency": mc,
                "continuation_persistence_durability": pc,
                "bearish_accumulation_durability": ba,
            }

        per_profile[profile] = {"regime_stats": regime_stats}

    focus_stats = per_profile.get(focus_profile, {}).get("regime_stats", {})
    for reg in ("crash_like", "gap_up", "gap_down", "low_liquidity", "high_liquidity"):
        st = focus_stats.get(reg) or {}
        durability[reg] = {
            "trade_count": st.get("trade_count", 0),
            "momentum_continuation_durability": st.get("momentum_continuation_consistency"),
            "persistence_durability": st.get("continuation_persistence_durability"),
            "bearish_accumulation_durability": st.get("bearish_accumulation_durability"),
        }

    return {
        "phase": 38,
        "run_dir": str(run_dir),
        "focus_profile": focus_profile,
        "expanded_regime_definitions": {
            "crash_like": "large down day or high range with negative return",
            "gap_up": "median open gap >= +0.6% vs prior close",
            "gap_down": "median open gap <= -0.6%",
            "low_liquidity": "median intraday bar count < 120",
            "high_liquidity": "median intraday bar count >= 280",
            "base_regimes": list(REGIME_LABELS),
        },
        "day_regimes": day_regimes,
        "profiles": per_profile,
        "focus_durability": durability,
        "persistence_survives_oos": all(
            (durability.get(r, {}).get("momentum_continuation_durability") or 0) >= 0.45
            for r in ("uptrend", "sideways", "high_liquidity")
            if (durability.get(r, {}).get("trade_count") or 0) > 0
        ),
    }
