"""
Phase181: Entry expectancy and feature review (post-hoc / replay only).

Pairs accepted events with observer_exit, computes post-entry returns from push_jsonl,
classifies loss patterns, and compares fixed post-hoc filter scenarios A–F.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

# Fixed scenario thresholds (documented priors — NOT tuned on review day)
# B: favorable bridge saturates at rolling_mfe / 0.003 → 1.0; 5× scale = extended entry
ROLLING_MFE_CAP = 0.015
# C: extended entry that fails to hold gain within 60s
EARLY_FAIL_ROLLING_MFE_MIN = 0.010
# D: high-volatility caution band (fixed round priors for review)
ATR_PCT_CAP = 20.0
INTRADAY_RANGE_PCT_CAP = 20.0
# E: entry price risk guard + phase177 caution
LOW_PRICE_YEN = 100.0
GATE_MIN_PRICE_YEN = 50.0
TICK_RATIO_CAP_PCT = 5.0
# Phase178 liquidity (also used in low_liq shadow)
TRADING_VALUE_MIN = 1e8
TURNOVER_PROXY_MIN = 0.002
# Loss pattern cutoffs
LOW_MFE_BEFORE_STOP_PCT = 0.3
SHORT_STOP_HOLD_SEC = 120.0

FOCUS_SYMBOLS = frozenset({"6203.T", "6659.T", "9348.T", "4888.T"})
COMPARE_SYMBOLS = frozenset({"3687.T", "4392.T", "3905.T", "7885.T"})

QUALITY_BANDS = (
    ("0.70_0.75", 0.70, 0.75),
    ("0.75_0.80", 0.75, 0.80),
    ("ge_0.80", 0.80, 1.01),
)


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _boolish(val: Any) -> bool:
    if val is True:
        return True
    if val in (False, None, ""):
        return False
    return str(val).strip().lower() in ("1", "true", "yes")


def _parse_ts(ts: str) -> float:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _pf(pnls: Sequence[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return wins / gl


def _mean(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    return sum(xs) / len(xs)


def _median(xs: Sequence[float]) -> Optional[float]:
    if not xs:
        return None
    return statistics.median(xs)


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    jsonl = session_dir / "small_paper_events.jsonl"
    rows: list[dict[str, Any]] = []
    if not jsonl.is_file():
        return rows
    with jsonl.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _push_dir_for_day(day_stamp: str, repo_root: Path) -> Path:
    y = f"{day_stamp[:4]}-{day_stamp[4:6]}-{day_stamp[6:8]}"
    return repo_root / "kabu_native" / "data" / "push_jsonl" / y


def _load_price_series(push_dir: Path, symbol: str) -> list[tuple[float, float]]:
    path = push_dir / f"{symbol}.jsonl"
    if not path.is_file():
        path = push_dir / f"{symbol.replace('.T', '')}.jsonl"
    if not path.is_file():
        return []
    out: list[tuple[float, float]] = []
    last_px: Optional[float] = None
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(str(rec.get("recorded_at") or ""))
            payload = rec.get("payload") or {}
            px = _float(payload.get("CurrentPrice"))
            if px is None:
                px = _float(payload.get("CalcPrice"))
            if px is None or px <= 0:
                if last_px is not None:
                    px = last_px
                else:
                    continue
            last_px = px
            out.append((ts, float(px)))
    out.sort(key=lambda x: x[0])
    return out


def _price_at_offset(
    series: Sequence[tuple[float, float]],
    entry_ts: float,
    entry_px: float,
    offset_sec: float,
    *,
    end_ts: Optional[float] = None,
) -> Optional[float]:
    if entry_px <= 0 or not series:
        return None
    target = entry_ts + offset_sec
    cap = end_ts if end_ts is not None else float("inf")
    px_at: Optional[float] = None
    for ts, px in series:
        if ts < entry_ts:
            continue
        if ts > cap:
            break
        if ts >= target:
            return px
        px_at = px
    if px_at is not None and target <= cap:
        return px_at
    return None


def _path_mfe_mae(
    series: Sequence[tuple[float, float]],
    entry_ts: float,
    entry_px: float,
    exit_ts: float,
) -> tuple[Optional[float], Optional[float]]:
    if entry_px <= 0:
        return None, None
    pnls: list[float] = []
    for ts, px in series:
        if ts < entry_ts:
            continue
        if ts > exit_ts:
            break
        pnls.append((px - entry_px) / entry_px * 100.0)
    if not pnls:
        return 0.0, 0.0
    return max(pnls), min(pnls)


def _return_pct(entry_px: float, px: Optional[float]) -> Optional[float]:
    if px is None or entry_px <= 0:
        return None
    return (px - entry_px) / entry_px * 100.0


def _tick_ratio_pct(price: float) -> float:
    from research.low_price_risk_review import jpx_tick_size_yen, tick_ratio_pct

    if price <= 0:
        return 0.0
    return tick_ratio_pct(price)


@dataclass
class EntryTradeRow:
    symbol: str
    entry_time: str
    exit_time: str
    entry_ts: float
    exit_ts: float
    entry_price: float
    exit_price: float
    pnl_pct: float
    exit_reason: str
    hold_sec: float
    r30_sec: Optional[float] = None
    r60_sec: Optional[float] = None
    r120_sec: Optional[float] = None
    r300_sec: Optional[float] = None
    max_mfe_until_exit: Optional[float] = None
    max_mae_until_exit: Optional[float] = None
    continuation_quality_score: Optional[float] = None
    momentum_continuation_score: Optional[float] = None
    favorable_continuation: Optional[float] = None
    rolling_mfe_pct: Optional[float] = None
    rolling_mae_pct: Optional[float] = None
    current_price: Optional[float] = None
    trading_value: Optional[float] = None
    turnover_proxy: Optional[float] = None
    atr_pct: Optional[float] = None
    intraday_range_pct: Optional[float] = None
    daytrade_suitability_score: Optional[float] = None
    low_liquidity_shadow_rejected: bool = False
    tick_ratio_pct: Optional[float] = None
    loss_patterns: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "entry_time": self.entry_time,
            "exit_time": self.exit_time,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "pnl_pct": round(self.pnl_pct, 4),
            "exit_reason": self.exit_reason,
            "hold_sec": round(self.hold_sec, 1),
            "r30_sec": round(self.r30_sec, 4) if self.r30_sec is not None else None,
            "r60_sec": round(self.r60_sec, 4) if self.r60_sec is not None else None,
            "r120_sec": round(self.r120_sec, 4) if self.r120_sec is not None else None,
            "r300_sec": round(self.r300_sec, 4) if self.r300_sec is not None else None,
            "max_mfe_until_exit": round(self.max_mfe_until_exit, 4)
            if self.max_mfe_until_exit is not None
            else None,
            "max_mae_until_exit": round(self.max_mae_until_exit, 4)
            if self.max_mae_until_exit is not None
            else None,
            "continuation_quality_score": self.continuation_quality_score,
            "momentum_continuation_score": self.momentum_continuation_score,
            "favorable_continuation": self.favorable_continuation,
            "rolling_mfe_pct": self.rolling_mfe_pct,
            "rolling_mae_pct": self.rolling_mae_pct,
            "current_price": self.current_price,
            "trading_value": self.trading_value,
            "turnover_proxy": self.turnover_proxy,
            "atr_pct": self.atr_pct,
            "intraday_range_pct": self.intraday_range_pct,
            "daytrade_suitability_score": self.daytrade_suitability_score,
            "low_liquidity_shadow_rejected": self.low_liquidity_shadow_rejected,
            "tick_ratio_pct": self.tick_ratio_pct,
            "loss_patterns": list(self.loss_patterns),
        }


def _classify_loss_patterns(row: EntryTradeRow) -> list[str]:
    patterns: list[str] = []
    if row.r30_sec is not None and row.r30_sec < 0:
        patterns.append("adverse_within_30s")
    if row.r60_sec is not None and row.r60_sec < 0:
        patterns.append("adverse_within_60s")
    if row.exit_reason == "stop_hit":
        mfe = row.max_mfe_until_exit if row.max_mfe_until_exit is not None else 0.0
        if mfe < LOW_MFE_BEFORE_STOP_PCT:
            patterns.append("low_mfe_before_stop")
        if row.hold_sec < SHORT_STOP_HOLD_SEC:
            patterns.append("short_hold_stop")
    rmfe = row.rolling_mfe_pct or 0.0
    if row.r60_sec is not None and row.r60_sec <= 0 and rmfe >= EARLY_FAIL_ROLLING_MFE_MIN:
        patterns.append("extended_entry_high_fail_60s")
    if row.max_mfe_until_exit is not None and row.max_mfe_until_exit <= 0.05:
        if row.r60_sec is not None and row.r60_sec <= 0:
            patterns.append("no_new_high_after_entry")
    return patterns


def _pair_trades(events: Sequence[Mapping[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    accepted = [e for e in events if e.get("event_type") == "accepted"]
    exits_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for e in events:
        if e.get("event_type") != "observer_exit":
            continue
        key = (str(e.get("symbol") or ""), str(e.get("entry_time") or ""))
        exits_by_key[key] = dict(e)
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for acc in accepted:
        key = (str(acc.get("symbol") or ""), str(acc.get("entry_time") or ""))
        ex = exits_by_key.get(key)
        if ex:
            pairs.append((dict(acc), ex))
    return pairs


def build_entry_trade_rows(
    session_dir: Path,
    *,
    repo_root: Path,
    day_stamp: str,
) -> list[EntryTradeRow]:
    events = _load_events(session_dir)
    pairs = _pair_trades(events)
    push_dir = _push_dir_for_day(day_stamp, repo_root)
    cache: dict[str, list[tuple[float, float]]] = {}
    rows: list[EntryTradeRow] = []

    for acc, ex in pairs:
        sym = str(acc.get("symbol") or "")
        ent = str(acc.get("entry_time") or "")
        ext = str(ex.get("exit_time") or "")
        ent_ts = _parse_ts(ent)
        ex_ts = _parse_ts(ext) or ent_ts + 300
        entry_px = _float(ex.get("entry_price")) or _float(acc.get("current_price")) or 0.0
        exit_px = _float(ex.get("exit_price")) or _float(ex.get("current_price")) or entry_px
        pnl = _float(ex.get("pnl_pct"))
        if pnl is None and entry_px > 0:
            pnl = (exit_px - entry_px) / entry_px * 100.0
        hold = _float(ex.get("hold_sec"))
        if hold is None:
            hold = max(0.0, ex_ts - ent_ts)

        if sym not in cache:
            cache[sym] = _load_price_series(push_dir, sym)
        series = cache[sym]

        r30 = _return_pct(
            entry_px, _price_at_offset(series, ent_ts, entry_px, 30, end_ts=ex_ts)
        )
        r60 = _return_pct(
            entry_px, _price_at_offset(series, ent_ts, entry_px, 60, end_ts=ex_ts)
        )
        r120 = _return_pct(
            entry_px, _price_at_offset(series, ent_ts, entry_px, 120, end_ts=ex_ts)
        )
        r300 = _return_pct(
            entry_px, _price_at_offset(series, ent_ts, entry_px, 300, end_ts=ex_ts)
        )
        mfe, mae = _path_mfe_mae(series, ent_ts, entry_px, ex_ts)

        price = _float(acc.get("current_price")) or entry_px
        tick_tr = _float(acc.get("tick_ratio_pct"))
        if tick_tr is None and price:
            tick_tr = _tick_ratio_pct(price)

        row = EntryTradeRow(
            symbol=sym,
            entry_time=ent,
            exit_time=ext,
            entry_ts=ent_ts,
            exit_ts=ex_ts,
            entry_price=entry_px,
            exit_price=exit_px,
            pnl_pct=float(pnl or 0.0),
            exit_reason=str(ex.get("exit_reason") or ""),
            hold_sec=float(hold or 0.0),
            r30_sec=r30,
            r60_sec=r60,
            r120_sec=r120,
            r300_sec=r300,
            max_mfe_until_exit=mfe,
            max_mae_until_exit=mae,
            continuation_quality_score=_float(acc.get("continuation_quality_score")),
            momentum_continuation_score=_float(acc.get("momentum_continuation_score")),
            favorable_continuation=_float(acc.get("favorable_continuation")),
            rolling_mfe_pct=_float(acc.get("rolling_mfe_pct")),
            rolling_mae_pct=_float(acc.get("rolling_mae_pct")),
            current_price=price,
            trading_value=_float(acc.get("trading_value")),
            turnover_proxy=_float(acc.get("turnover_proxy")),
            atr_pct=_float(acc.get("atr_pct")),
            intraday_range_pct=_float(acc.get("intraday_range_pct")),
            daytrade_suitability_score=_float(acc.get("daytrade_suitability_score")),
            low_liquidity_shadow_rejected=_boolish(acc.get("low_liquidity_shadow_rejected")),
            tick_ratio_pct=tick_tr,
        )
        row.loss_patterns = _classify_loss_patterns(row)
        rows.append(row)
    return rows


def _scenario_keep(row: EntryTradeRow, scenario: str) -> bool:
    rmfe = row.rolling_mfe_pct or 0.0
    atr = row.atr_pct or 0.0
    ir = row.intraday_range_pct or 0.0
    px = row.current_price or row.entry_price or 0.0
    tick = row.tick_ratio_pct or 0.0

    b_fail = rmfe >= ROLLING_MFE_CAP
    c_fail = rmfe >= EARLY_FAIL_ROLLING_MFE_MIN and (
        row.r60_sec is not None and row.r60_sec <= 0
    )
    d_fail = atr >= ATR_PCT_CAP or ir >= INTRADAY_RANGE_PCT_CAP
    e_fail = px < LOW_PRICE_YEN or px < GATE_MIN_PRICE_YEN or tick > TICK_RATIO_CAP_PCT

    if scenario == "A":
        return True
    if scenario == "B":
        return not b_fail
    if scenario == "C":
        return not c_fail
    if scenario == "D":
        return not d_fail
    if scenario == "E":
        return not e_fail
    if scenario == "F":
        return not (b_fail or c_fail or d_fail)
    return True


def _summarize_trades(trades: Sequence[EntryTradeRow]) -> dict[str, Any]:
    if not trades:
        return {"trade_count": 0}
    pnls = [t.pnl_pct for t in trades]
    return {
        "trade_count": len(trades),
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(_mean(pnls) or 0.0, 4),
        "profit_factor": round(_pf(pnls), 4) if _pf(pnls) not in (None, float("inf")) else _pf(pnls),
        "stop_hit_count": sum(1 for t in trades if t.exit_reason == "stop_hit"),
        "trailing_mfe_exit_count": sum(1 for t in trades if t.exit_reason == "trailing_mfe_exit"),
        "overlap_count": sum(1 for t in trades if t.exit_reason == "overlap_replaced_review"),
        "avg_r30_sec": round(_mean([t.r30_sec for t in trades if t.r30_sec is not None]) or 0, 4),
        "avg_r60_sec": round(_mean([t.r60_sec for t in trades if t.r60_sec is not None]) or 0, 4),
        "avg_r120_sec": round(_mean([t.r120_sec for t in trades if t.r120_sec is not None]) or 0, 4),
        "avg_r300_sec": round(_mean([t.r300_sec for t in trades if t.r300_sec is not None]) or 0, 4),
    }


def _feature_means(trades: Sequence[EntryTradeRow]) -> dict[str, Optional[float]]:
    fields = (
        "continuation_quality_score",
        "momentum_continuation_score",
        "favorable_continuation",
        "rolling_mfe_pct",
        "rolling_mae_pct",
        "current_price",
        "trading_value",
        "turnover_proxy",
        "atr_pct",
        "intraday_range_pct",
        "daytrade_suitability_score",
    )
    out: dict[str, Optional[float]] = {}
    for f in fields:
        xs = [getattr(t, f) for t in trades if getattr(t, f) is not None]
        xs = [float(x) for x in xs]
        out[f] = round(_mean(xs), 4) if xs else None
    out["low_liquidity_shadow_rejected_rate"] = round(
        sum(1 for t in trades if t.low_liquidity_shadow_rejected) / max(1, len(trades)), 4
    )
    return out


def _symbol_verdict(
    sym_trades: Sequence[EntryTradeRow],
    *,
    all_trades: Sequence[EntryTradeRow],
) -> str:
    if not sym_trades:
        return "insufficient_data"
    sym_pnls = [t.pnl_pct for t in sym_trades]
    sym_avg = _mean(sym_pnls) or 0.0
    other = [t for t in all_trades if t.symbol != sym_trades[0].symbol]
    other_avg = _mean([t.pnl_pct for t in other]) or 0.0

    avg_suit = _mean(
        [t.daytrade_suitability_score for t in sym_trades if t.daytrade_suitability_score is not None]
    )
    avg_tv = _mean([t.trading_value for t in sym_trades if t.trading_value is not None])
    thin = avg_tv is not None and avg_tv < TRADING_VALUE_MIN

    timing_signals = sum(
        1
        for t in sym_trades
        if (t.rolling_mfe_pct or 0) >= EARLY_FAIL_ROLLING_MFE_MIN
        or (t.r30_sec is not None and t.r30_sec < 0)
        or "extended_entry_high_fail_60s" in t.loss_patterns
    )
    selection_signals = sum(
        1
        for t in sym_trades
        if thin
        or (t.atr_pct or 0) >= ATR_PCT_CAP
        or (t.current_price or 0) < LOW_PRICE_YEN
        or t.low_liquidity_shadow_rejected
    )

    if sym_avg < other_avg - 0.3 and timing_signals >= selection_signals:
        return "entry_timing_miss"
    if sym_avg < other_avg - 0.3 and selection_signals > timing_signals:
        return "selection_miss"
    if timing_signals > 0 and selection_signals > 0:
        return "mixed"
    if sym_avg >= 0:
        return "acceptable"
    return "mixed"


def evaluate_entry_expectancy_review(
    session_dir: Path,
    *,
    repo_root: Path,
    day_stamp: str,
) -> dict[str, Any]:
    summary_path = session_dir / "small_paper_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}

    trades = build_entry_trade_rows(session_dir, repo_root=repo_root, day_stamp=day_stamp)
    trade_dicts = [t.to_dict() for t in trades]

    by_sym: dict[str, list[EntryTradeRow]] = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t)

    symbol_stats: list[dict[str, Any]] = []
    for sym in sorted(by_sym.keys()):
        grp = by_sym[sym]
        pnls = [t.pnl_pct for t in grp]
        symbol_stats.append(
            {
                "symbol": sym,
                "trade_count": len(grp),
                "total_pnl_pct": round(sum(pnls), 4),
                "avg_pnl_pct": round(_mean(pnls) or 0.0, 4),
                "stop_hit_count": sum(1 for t in grp if t.exit_reason == "stop_hit"),
                "trailing_mfe_exit_count": sum(
                    1 for t in grp if t.exit_reason == "trailing_mfe_exit"
                ),
                "avg_r30_sec": round(
                    _mean([t.r30_sec for t in grp if t.r30_sec is not None]) or 0, 4
                ),
                "avg_r60_sec": round(
                    _mean([t.r60_sec for t in grp if t.r60_sec is not None]) or 0, 4
                ),
                "avg_r120_sec": round(
                    _mean([t.r120_sec for t in grp if t.r120_sec is not None]) or 0, 4
                ),
                "avg_r300_sec": round(
                    _mean([t.r300_sec for t in grp if t.r300_sec is not None]) or 0, 4
                ),
                "focus_symbol": sym in FOCUS_SYMBOLS,
                "compare_symbol": sym in COMPARE_SYMBOLS,
            }
        )

    stop_trades = [t for t in trades if t.exit_reason == "stop_hit"]
    win_trades = [t for t in trades if t.pnl_pct > 0]
    high_q_loss = [
        t
        for t in trades
        if (t.continuation_quality_score or 0) >= 0.75 and t.pnl_pct < 0
    ]

    pattern_counts = Counter(p for t in trades for p in t.loss_patterns)

    feature_buckets: dict[str, Any] = {}
    for name, lo, hi in QUALITY_BANDS:
        grp = [
            t
            for t in trades
            if t.continuation_quality_score is not None and lo <= t.continuation_quality_score < hi
        ]
        feature_buckets[f"quality_{name}"] = _summarize_trades(grp)

    rmfe_bands = (
        ("lt_0.010", 0.0, 0.010),
        ("0.010_0.015", 0.010, 0.015),
        ("ge_0.015", 0.015, 999.0),
    )
    for name, lo, hi in rmfe_bands:
        grp = [
            t for t in trades if t.rolling_mfe_pct is not None and lo <= t.rolling_mfe_pct < hi
        ]
        feature_buckets[f"rolling_mfe_{name}"] = _summarize_trades(grp)

    scenarios: dict[str, Any] = {}
    scenario_defs = {
        "A": "current (no post-hoc filter)",
        "B": f"exclude rolling_mfe_pct>={ROLLING_MFE_CAP} (extended entry)",
        "C": f"exclude rolling_mfe>={EARLY_FAIL_ROLLING_MFE_MIN} AND r60<=0",
        "D": f"exclude atr>={ATR_PCT_CAP} OR intraday_range>={INTRADAY_RANGE_PCT_CAP}",
        "E": f"exclude price<{LOW_PRICE_YEN} OR tick_ratio>{TICK_RATIO_CAP_PCT}",
        "F": "exclude B OR C OR D conditions combined",
    }
    base = _summarize_trades(trades)
    for sid, desc in scenario_defs.items():
        kept = [t for t in trades if _scenario_keep(t, sid)]
        excluded = [t for t in trades if not _scenario_keep(t, sid)]
        kept_sum = _summarize_trades(kept)
        scenarios[sid] = {
            "description": desc,
            "kept": kept_sum,
            "excluded_count": len(excluded),
            "excluded_total_pnl_pct": round(sum(t.pnl_pct for t in excluded), 4),
            "excluded_stop_hit_count": sum(1 for t in excluded if t.exit_reason == "stop_hit"),
            "delta_total_pnl_vs_A": round(
                (kept_sum.get("total_pnl_pct") or 0) - (base.get("total_pnl_pct") or 0),
                4,
            ),
            "delta_stop_hit_vs_A": (kept_sum.get("stop_hit_count") or 0)
            - (base.get("stop_hit_count") or 0),
        }

    shadow_entry_features = [
        "entry_rolling_mfe_pct",
        "entry_rolling_mae_pct",
        "r30_sec",
        "r60_sec",
        "max_mfe_until_exit",
        "max_mae_until_exit",
        "tick_ratio_pct_at_entry",
        "extended_entry_flag",
        "early_adverse_30s_flag",
        "early_adverse_60s_flag",
        "low_mfe_before_stop_flag",
    ]

    return {
        "phase": 181,
        "mode": "entry_expectancy_feature_review_post_hoc",
        "day_stamp": day_stamp,
        "session_dir": str(session_dir).replace("\\", "/"),
        "session_summary": {
            "accepted_count": summary.get("accepted_count"),
            "structural_exit_reason_counts": summary.get("structural_exit_reason_counts"),
            "policy_label": summary.get("policy_label"),
            "structural_exit_policy": summary.get("structural_exit_policy"),
        },
        "paired_trade_count": len(trades),
        "fixed_thresholds": {
            "ROLLING_MFE_CAP": ROLLING_MFE_CAP,
            "EARLY_FAIL_ROLLING_MFE_MIN": EARLY_FAIL_ROLLING_MFE_MIN,
            "ATR_PCT_CAP": ATR_PCT_CAP,
            "INTRADAY_RANGE_PCT_CAP": INTRADAY_RANGE_PCT_CAP,
            "LOW_PRICE_YEN": LOW_PRICE_YEN,
            "TICK_RATIO_CAP_PCT": TICK_RATIO_CAP_PCT,
            "note": "Fixed priors only; not tuned on review day.",
        },
        "aggregate": _summarize_trades(trades),
        "symbol_stats": symbol_stats,
        "focus_symbol_detail": {
            sym: {
                "stats": next((s for s in symbol_stats if s["symbol"] == sym), {}),
                "trades": [t.to_dict() for t in by_sym.get(sym, [])],
                "verdict": _symbol_verdict(by_sym.get(sym, []), all_trades=trades),
            }
            for sym in sorted(FOCUS_SYMBOLS)
        },
        "compare_symbol_detail": {
            sym: {
                "stats": next((s for s in symbol_stats if s["symbol"] == sym), {}),
                "trades": [t.to_dict() for t in by_sym.get(sym, [])],
            }
            for sym in sorted(COMPARE_SYMBOLS)
        },
        "verdict_6203": _symbol_verdict(by_sym.get("6203.T", []), all_trades=trades),
        "verdict_6659": _symbol_verdict(by_sym.get("6659.T", []), all_trades=trades),
        "loss_pattern_counts": dict(pattern_counts),
        "stop_hit_common_features": _feature_means(stop_trades),
        "winning_trade_common_features": _feature_means(win_trades),
        "high_quality_losing_trades": {
            "count": len(high_q_loss),
            "common_features": _feature_means(high_q_loss),
            "sample": [t.to_dict() for t in high_q_loss[:10]],
        },
        "hypothesis_checks": {
            "high_rolling_mfe_losers": _summarize_trades(
                [t for t in trades if (t.rolling_mfe_pct or 0) >= ROLLING_MFE_CAP and t.pnl_pct < 0]
            ),
            "high_vol_losers": _summarize_trades(
                [
                    t
                    for t in trades
                    if (
                        (t.atr_pct or 0) >= ATR_PCT_CAP
                        or (t.intraday_range_pct or 0) >= INTRADAY_RANGE_PCT_CAP
                    )
                    and t.pnl_pct < 0
                ]
            ),
            "low_price_losers": _summarize_trades(
                [
                    t
                    for t in trades
                    if (t.current_price or 0) < LOW_PRICE_YEN and t.pnl_pct < 0
                ]
            ),
            "adequate_liquidity_losers": _summarize_trades(
                [
                    t
                    for t in trades
                    if (t.trading_value or 0) >= TRADING_VALUE_MIN
                    and (t.turnover_proxy or 0) >= TURNOVER_PROXY_MIN
                    and t.pnl_pct < 0
                ]
            ),
        },
        "feature_bucket_expectancy": feature_buckets,
        "post_hoc_scenarios": scenarios,
        "trades": trade_dicts,
        "next_shadow_logging_entry_features": shadow_entry_features,
        "constraints": {
            "no_time_based_cooldown": True,
            "no_parameter_search": True,
            "no_hard_reject_implementation": True,
            "review_only": True,
        },
    }
