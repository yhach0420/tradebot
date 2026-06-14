"""
Phase261-Risk-Aware-Position-Sizing-Audit.

Shadow evaluation of risk-based position sizing vs equity-only caps (Phase260B).
Observation only — no Runtime / Universe / Entry / YAML changes.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import (
    _float,
    _norm_symbol,
    _pf,
    _write_csv,
    load_trades_by_day,
)
from research.phase374_dynamic40_universe_quality_review import resolve_pnl_yen_100
from research.position_exposure_audit import _percentile, _win_rate

JST = ZoneInfo("Asia/Tokyo")


def _bool(val: Any) -> bool:
    if isinstance(val, bool):
        return val
    if val in (None, ""):
        return False
    return str(val).lower() in {"1", "true", "yes", "y"}


EQUITY_LEVELS: tuple[int, ...] = (
    1_000_000,
    3_000_000,
    5_000_000,
    10_000_000,
)

SIZING_POLICIES: tuple[str, ...] = (
    "fixed_100_shares",
    "equity_30pct_cap",
    "risk_1pct_equity",
    "risk_2pct_equity",
    "volatility_adjusted",
    "hybrid_equity30_risk1",
)

FORWARD_SIZING_POLICIES: tuple[str, ...] = (
    "fixed_100_shares",
    "risk_1pct_equity",
    "risk_2pct_equity",
    "hybrid_equity30_risk1",
)

MIN_TRADE_OVERLAP_DAYS = 10

MIN_LOT = 100
HIGH_PRICE_THRESHOLD = 3000.0
LOW_PRICE_THRESHOLD = 1000.0
DEFAULT_STOP_DISTANCE_PCT = 1.20
HARD_STOP_ABS = DEFAULT_STOP_DISTANCE_PCT / 100.0

ENTRY_RISK_SIZING_FIELDS = [
    "day",
    "symbol",
    "equity_yen",
    "sizing_policy",
    "entry_price",
    "intraday_range_pct",
    "atr_proxy_pct",
    "recent_volatility_pct",
    "mae_pct",
    "mfe_pct",
    "stop_distance_pct",
    "risk_per_100_shares_yen",
    "position_value_100",
    "shares_shadow",
    "position_value",
    "position_ratio",
    "risk_budget_used",
    "pnl_yen_100",
    "pnl_yen_scaled",
    "skipped_due_to_risk",
    "skipped_due_to_min_lot",
]

POLICY_BY_EQUITY_FIELDS = [
    "equity_yen",
    "sizing_policy",
    "entry_count",
    "skipped_count",
    "total_pnl_yen_scaled",
    "profit_factor",
    "win_rate",
    "max_loss_yen_scaled",
    "pnl_stddev",
    "avg_position_ratio",
    "p95_position_ratio",
    "avg_risk_budget_used",
    "high_price_pnl_scaled",
    "low_price_pnl_scaled",
    "high_volatility_pnl_scaled",
    "low_volatility_pnl_scaled",
]

VOLATILITY_BUCKET_FIELDS = [
    "equity_yen",
    "sizing_policy",
    "volatility_bucket",
    "entry_count",
    "skipped_count",
    "total_pnl_yen_scaled",
    "profit_factor",
    "win_rate",
    "avg_shares_shadow",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _stddev(values: Sequence[float]) -> Optional[float]:
    if len(values) < 2:
        return 0.0 if values else None
    return round(statistics.stdev(values), 2)


def _parse_ts(raw: str) -> Optional[datetime]:
    if not raw or not str(raw).strip():
        return None
    text = str(raw).strip()
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _day_to_iso(day: str) -> str:
    d = str(day)
    if len(d) == 8 and d.isdigit():
        return f"{d[:4]}-{d[4:6]}-{d[6:8]}"
    return d


def intraday_roots(repo_root: Path) -> list[Path]:
    return [
        repo_root / "data" / "intraday_1m",
        repo_root / "kabu_native" / "data" / "intraday_1m",
    ]


def resolve_intraday_path(repo_root: Path, *, day: str, symbol: str) -> Optional[Path]:
    fname = f"{_norm_symbol(symbol)}.csv"
    iso_day = _day_to_iso(day)
    for root in intraday_roots(repo_root):
        candidate = root / iso_day / fname
        if candidate.is_file():
            return candidate
    return None


def load_intraday_bars(path: Path) -> list[dict[str, Any]]:
    bars: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                ts = _parse_ts(str(row.get("timestamp_utc") or row.get("timestamp") or ""))
                if ts is None:
                    continue
                try:
                    bars.append(
                        {
                            "ts": ts,
                            "open": _float(row.get("open")) or 0.0,
                            "high": _float(row.get("high")) or 0.0,
                            "low": _float(row.get("low")) or 0.0,
                            "close": _float(row.get("close")) or 0.0,
                        }
                    )
                except (TypeError, ValueError):
                    continue
    except OSError:
        return []
    bars.sort(key=lambda b: b["ts"])
    return bars


def compute_intraday_metrics(
    bars: Sequence[Mapping[str, Any]],
    *,
    entry_ts: Optional[datetime],
    entry_price: float,
) -> dict[str, Optional[float]]:
    if not bars or entry_price <= 0:
        return {
            "intraday_range_pct": None,
            "atr_proxy_pct": None,
            "recent_volatility_pct": None,
        }
    subset = list(bars)
    if entry_ts is not None:
        subset = [b for b in bars if b["ts"] <= entry_ts.astimezone(b["ts"].tzinfo)]
    if not subset:
        subset = list(bars[: min(30, len(bars))])

    highs = [float(b["high"]) for b in subset if float(b["high"]) > 0]
    lows = [float(b["low"]) for b in subset if float(b["low"]) > 0]
    intraday_range_pct = None
    if highs and lows:
        intraday_range_pct = round((max(highs) - min(lows)) / entry_price * 100.0, 4)

    tr_pcts: list[float] = []
    closes: list[float] = []
    for b in subset[-20:]:
        hi = float(b["high"])
        lo = float(b["low"])
        cl = float(b["close"])
        if cl > 0 and hi >= lo:
            tr_pcts.append((hi - lo) / cl * 100.0)
            closes.append(cl)
    atr_proxy_pct = round(sum(tr_pcts) / len(tr_pcts), 4) if tr_pcts else None

    rets: list[float] = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            rets.append((closes[i] - closes[i - 1]) / closes[i - 1] * 100.0)
    recent_volatility_pct = round(statistics.pstdev(rets), 4) if len(rets) >= 2 else None

    return {
        "intraday_range_pct": intraday_range_pct,
        "atr_proxy_pct": atr_proxy_pct,
        "recent_volatility_pct": recent_volatility_pct,
    }


def stop_distance_pct_from_trade(
    *,
    mae_pct: Optional[float],
    atr_proxy_pct: Optional[float],
) -> float:
    mae = abs(mae_pct) if mae_pct is not None else 0.0
    atr = atr_proxy_pct if atr_proxy_pct is not None else 0.0
    return round(max(DEFAULT_STOP_DISTANCE_PCT, mae, atr), 4)


def risk_per_100_shares_yen(entry_price: float, stop_pct: float) -> float:
    return round(entry_price * MIN_LOT * (stop_pct / 100.0), 2)


def resolve_overlap_days(reports_dir: Path) -> list[str]:
    phase260b = _load_json(reports_dir / "phase260b_equity_position_sizing_summary.json")
    days = list((phase260b.get("summary") or {}).get("trade_overlap_days") or [])
    return sorted(days)


def enrich_base_entries(
    trades_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    overlap_days: Sequence[str],
    repo_root: Path,
    median_volatility: float,
) -> list[dict[str, Any]]:
    intraday_cache: dict[tuple[str, str], list[dict[str, Any]]] = {}
    entries: list[dict[str, Any]] = []

    for day in overlap_days:
        for row in trades_by_day.get(day) or []:
            ep = _float(row.get("entry_price"))
            if ep is None or ep <= 0:
                continue
            sym = _norm_symbol(str(row.get("symbol") or ""))
            pnl = _float(row.get("pnl_yen_100"))
            if pnl is None:
                pnl = resolve_pnl_yen_100(dict(row))
            mae = _float(row.get("mae_pct"))
            mfe = _float(row.get("mfe_pct"))
            entry_ts = _parse_ts(str(row.get("entry_time") or ""))

            cache_key = (day, sym)
            if cache_key not in intraday_cache:
                path = resolve_intraday_path(repo_root, day=day, symbol=sym)
                intraday_cache[cache_key] = load_intraday_bars(path) if path else []
            intraday = compute_intraday_metrics(
                intraday_cache[cache_key],
                entry_ts=entry_ts,
                entry_price=ep,
            )
            stop_pct = stop_distance_pct_from_trade(
                mae_pct=mae,
                atr_proxy_pct=_float(intraday.get("atr_proxy_pct")),
            )
            risk100 = risk_per_100_shares_yen(ep, stop_pct)
            recent_vol = _float(intraday.get("recent_volatility_pct")) or median_volatility
            vol_scale = 1.0
            if recent_vol > 0 and median_volatility > 0:
                vol_scale = max(0.25, min(1.0, median_volatility / recent_vol))

            entries.append(
                {
                    "day": day,
                    "symbol": sym,
                    "entry_price": round(ep, 4),
                    "entry_time": str(row.get("entry_time") or ""),
                    "pnl_yen_100": round(pnl or 0.0, 2),
                    "mae_pct": round(mae, 4) if mae is not None else None,
                    "mfe_pct": round(mfe, 4) if mfe is not None else None,
                    "intraday_range_pct": intraday.get("intraday_range_pct"),
                    "atr_proxy_pct": intraday.get("atr_proxy_pct"),
                    "recent_volatility_pct": round(recent_vol, 4),
                    "stop_distance_pct": stop_pct,
                    "risk_per_100_shares_yen": risk100,
                    "position_value_100": round(ep * MIN_LOT, 2),
                    "volatility_scale": vol_scale,
                }
            )
    return entries


def _shares_from_budget(budget: float, entry_price: float) -> int:
    if budget <= 0 or entry_price <= 0:
        return 0
    return int(math.floor(budget / entry_price / MIN_LOT) * MIN_LOT)


def compute_shares_for_policy(
    base: Mapping[str, Any],
    *,
    equity_yen: int,
    policy: str,
) -> tuple[int, bool, bool, float]:
    entry_price = _float(base.get("entry_price")) or 0.0
    risk100 = _float(base.get("risk_per_100_shares_yen")) or 0.0
    vol_scale = _float(base.get("volatility_scale")) or 1.0

    if policy == "fixed_100_shares":
        risk_used = risk100 if risk100 > 0 else entry_price * MIN_LOT * HARD_STOP_ABS
        return MIN_LOT, False, False, risk_used

    equity_budget = equity_yen * 0.30
    shares_equity = _shares_from_budget(equity_budget, entry_price)

    shares_risk_1 = 0
    shares_risk_2 = 0
    if risk100 > 0:
        shares_risk_1 = int(math.floor(equity_yen * 0.01 / risk100) * MIN_LOT)
        shares_risk_2 = int(math.floor(equity_yen * 0.02 / risk100) * MIN_LOT)

    skipped_risk = False
    if policy == "equity_30pct_cap":
        shares = shares_equity
    elif policy == "risk_1pct_equity":
        shares = shares_risk_1
        skipped_risk = shares < MIN_LOT
    elif policy == "risk_2pct_equity":
        shares = shares_risk_2
        skipped_risk = shares < MIN_LOT
    elif policy == "volatility_adjusted":
        shares = _shares_from_budget(equity_budget * vol_scale, entry_price)
        skipped_risk = vol_scale < 1.0 and shares < shares_equity
    elif policy == "hybrid_equity30_risk1":
        if shares_equity <= 0 or shares_risk_1 <= 0:
            shares = 0
            skipped_risk = True
        else:
            shares = min(shares_equity, shares_risk_1)
            skipped_risk = shares_risk_1 < shares_equity
    else:
        shares = shares_equity

    skipped_min = shares < MIN_LOT
    if skipped_min:
        shares = 0
    risk_used = (shares / MIN_LOT) * risk100 if shares > 0 and risk100 > 0 else 0.0
    return shares, skipped_risk and skipped_min, skipped_min, risk_used


def scale_policy_row(
    base: Mapping[str, Any],
    *,
    equity_yen: int,
    policy: str,
) -> dict[str, Any]:
    entry_price = _float(base.get("entry_price")) or 0.0
    pnl100 = _float(base.get("pnl_yen_100")) or 0.0
    shares, skipped_risk, skipped_min, risk_used = compute_shares_for_policy(
        base,
        equity_yen=equity_yen,
        policy=policy,
    )
    position_value = round(entry_price * shares, 2) if shares > 0 else 0.0
    ratio = round(position_value / float(equity_yen), 6) if equity_yen > 0 and shares > 0 else 0.0
    pnl_scaled = round(pnl100 * shares / MIN_LOT, 2) if shares > 0 else 0.0
    risk_budget_used = round(risk_used / float(equity_yen), 6) if equity_yen > 0 and risk_used > 0 else 0.0

    return {
        "day": base.get("day"),
        "symbol": base.get("symbol"),
        "equity_yen": equity_yen,
        "sizing_policy": policy,
        "entry_price": entry_price,
        "intraday_range_pct": base.get("intraday_range_pct"),
        "atr_proxy_pct": base.get("atr_proxy_pct"),
        "recent_volatility_pct": base.get("recent_volatility_pct"),
        "mae_pct": base.get("mae_pct"),
        "mfe_pct": base.get("mfe_pct"),
        "stop_distance_pct": base.get("stop_distance_pct"),
        "risk_per_100_shares_yen": base.get("risk_per_100_shares_yen"),
        "position_value_100": base.get("position_value_100"),
        "shares_shadow": shares,
        "position_value": position_value,
        "position_ratio": ratio,
        "risk_budget_used": risk_budget_used,
        "pnl_yen_100": pnl100,
        "pnl_yen_scaled": pnl_scaled,
        "skipped_due_to_risk": skipped_risk,
        "skipped_due_to_min_lot": skipped_min,
    }


def build_forward_entry_rows(
    base_entries: Sequence[Mapping[str, Any]],
    *,
    logged_at: Optional[str] = None,
) -> list[dict[str, Any]]:
    logged_at = logged_at or _now_iso()
    rows: list[dict[str, Any]] = []
    for base in base_entries:
        for equity in EQUITY_LEVELS:
            for policy in FORWARD_SIZING_POLICIES:
                scaled = scale_policy_row(base, equity_yen=equity, policy=policy)
                rows.append(
                    {
                        "logged_at": logged_at,
                        "day": scaled.get("day"),
                        "symbol": scaled.get("symbol"),
                        "entry_price": scaled.get("entry_price"),
                        "actual_pnl_yen_100": scaled.get("pnl_yen_100"),
                        "sizing_policy": policy,
                        "equity_yen": equity,
                        "shares_shadow": scaled.get("shares_shadow"),
                        "skipped_due_to_min_lot": scaled.get("skipped_due_to_min_lot"),
                        "skipped_due_to_risk": scaled.get("skipped_due_to_risk"),
                        "position_value": scaled.get("position_value"),
                        "position_ratio": scaled.get("position_ratio"),
                        "risk_per_100_shares_yen": scaled.get("risk_per_100_shares_yen"),
                        "pnl_yen_scaled": scaled.get("pnl_yen_scaled"),
                    }
                )
    return rows


def aggregate_forward_summary_rows(
    entry_rows: Sequence[Mapping[str, Any]],
    *,
    day: Optional[str] = None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    filtered = [
        r
        for r in entry_rows
        if day is None or str(r.get("day") or "") == day
    ]
    scope_days = sorted({str(r.get("day") or "") for r in filtered if r.get("day")})
    day_list = [day] if day else scope_days

    for scope_day in day_list:
        for equity in EQUITY_LEVELS:
            fixed_pnl = 0.0
            for policy in FORWARD_SIZING_POLICIES:
                subset = [
                    r
                    for r in filtered
                    if str(r.get("day") or "") == scope_day
                    and int(r.get("equity_yen") or 0) == equity
                    and str(r.get("sizing_policy") or "") == policy
                ]
                executed = [r for r in subset if not _bool(r.get("skipped_due_to_min_lot"))]
                skipped = [r for r in subset if _bool(r.get("skipped_due_to_min_lot"))]
                yens = [_float(r.get("pnl_yen_scaled")) or 0.0 for r in executed]
                ratios = [_float(r.get("position_ratio")) or 0.0 for r in executed]
                total_pnl = round(sum(yens), 2)

                def _band_pnl(lo: float, hi: Optional[float]) -> float:
                    total = 0.0
                    for r in executed:
                        px = _float(r.get("entry_price")) or 0.0
                        if hi is None and px >= lo:
                            total += _float(r.get("pnl_yen_scaled")) or 0.0
                        elif hi is not None and lo <= px < hi:
                            total += _float(r.get("pnl_yen_scaled")) or 0.0
                    return round(total, 2)

                if policy == "fixed_100_shares":
                    fixed_pnl = total_pnl

                rows.append(
                    {
                        "day": scope_day,
                        "equity_yen": equity,
                        "sizing_policy": policy,
                        "entry_count": len(executed),
                        "skipped_count": len(skipped),
                        "total_pnl_yen_scaled": total_pnl,
                        "profit_factor": _pf(yens),
                        "win_rate": _win_rate(yens),
                        "max_loss_yen_scaled": round(min(yens), 2) if yens else None,
                        "pnl_stddev": _stddev(yens),
                        "avg_position_ratio": round(sum(ratios) / len(ratios), 6) if ratios else None,
                        "p95_position_ratio": _percentile(ratios, 95),
                        "high_price_pnl_scaled": _band_pnl(HIGH_PRICE_THRESHOLD, None),
                        "low_price_pnl_scaled": _band_pnl(0.0, LOW_PRICE_THRESHOLD),
                        "delta_vs_fixed_100": round(total_pnl - fixed_pnl, 2),
                    }
                )
    return rows


def aggregate_forward_cumulative_rows(
    entry_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    synthetic: list[dict[str, Any]] = []
    for equity in EQUITY_LEVELS:
        for policy in FORWARD_SIZING_POLICIES:
            subset = [
                r
                for r in entry_rows
                if int(r.get("equity_yen") or 0) == equity
                and str(r.get("sizing_policy") or "") == policy
            ]
            executed = [r for r in subset if not _bool(r.get("skipped_due_to_min_lot"))]
            skipped = [r for r in subset if _bool(r.get("skipped_due_to_min_lot"))]
            yens = [_float(r.get("pnl_yen_scaled")) or 0.0 for r in executed]
            ratios = [_float(r.get("position_ratio")) or 0.0 for r in executed]
            synthetic.append(
                {
                    "day": "cumulative",
                    "equity_yen": equity,
                    "sizing_policy": policy,
                    "entry_count": len(executed),
                    "skipped_count": len(skipped),
                    "total_pnl_yen_scaled": round(sum(yens), 2),
                    "profit_factor": _pf(yens),
                    "win_rate": _win_rate(yens),
                    "max_loss_yen_scaled": round(min(yens), 2) if yens else None,
                    "pnl_stddev": _stddev(yens),
                    "avg_position_ratio": round(sum(ratios) / len(ratios), 6) if ratios else None,
                    "p95_position_ratio": _percentile(ratios, 95),
                    "high_price_pnl_scaled": round(
                        sum(
                            _float(r.get("pnl_yen_scaled")) or 0.0
                            for r in executed
                            if (_float(r.get("entry_price")) or 0.0) >= HIGH_PRICE_THRESHOLD
                        ),
                        2,
                    ),
                    "low_price_pnl_scaled": round(
                        sum(
                            _float(r.get("pnl_yen_scaled")) or 0.0
                            for r in executed
                            if (_float(r.get("entry_price")) or 0.0) < LOW_PRICE_THRESHOLD
                        ),
                        2,
                    ),
                    "delta_vs_fixed_100": 0.0,
                }
            )
    fixed_by_equity = {
        int(r["equity_yen"]): _float(r["total_pnl_yen_scaled"]) or 0.0
        for r in synthetic
        if str(r.get("sizing_policy")) == "fixed_100_shares"
    }
    for row in synthetic:
        eq = int(row.get("equity_yen") or 0)
        row["delta_vs_fixed_100"] = round(
            (_float(row.get("total_pnl_yen_scaled")) or 0.0) - fixed_by_equity.get(eq, 0.0),
            2,
        )
    return synthetic


def compute_median_volatility(
    trades_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    repo_root: Path,
) -> float:
    vol_samples: list[float] = []
    for day, rows in trades_by_day.items():
        for row in rows:
            sym = _norm_symbol(str(row.get("symbol") or ""))
            path = resolve_intraday_path(repo_root, day=day, symbol=sym)
            if not path:
                continue
            bars = load_intraday_bars(path)
            metrics = compute_intraday_metrics(
                bars,
                entry_ts=_parse_ts(str(row.get("entry_time") or "")),
                entry_price=_float(row.get("entry_price")) or 0.0,
            )
            vol = _float(metrics.get("recent_volatility_pct"))
            if vol is not None and vol > 0:
                vol_samples.append(vol)
    return statistics.median(vol_samples) if vol_samples else 0.15


def build_entry_level_rows(base_entries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for base in base_entries:
        for equity in EQUITY_LEVELS:
            for policy in SIZING_POLICIES:
                rows.append(scale_policy_row(base, equity_yen=equity, policy=policy))
    return rows


def _vol_bucket(recent_vol: float, p33: float, p66: float) -> str:
    if recent_vol <= p33:
        return "low_volatility"
    if recent_vol <= p66:
        return "mid_volatility"
    return "high_volatility"


def aggregate_policy_rows(entry_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for equity in EQUITY_LEVELS:
        for policy in SIZING_POLICIES:
            subset = [
                r
                for r in entry_rows
                if int(r.get("equity_yen") or 0) == equity and str(r.get("sizing_policy") or "") == policy
            ]
            executed = [r for r in subset if not _bool(r.get("skipped_due_to_min_lot"))]
            skipped = [r for r in subset if _bool(r.get("skipped_due_to_min_lot"))]
            yens = [_float(r.get("pnl_yen_scaled")) or 0.0 for r in executed]
            ratios = [_float(r.get("position_ratio")) or 0.0 for r in executed]
            risk_used = [_float(r.get("risk_budget_used")) or 0.0 for r in executed]

            def _band_pnl(lo: float, hi: Optional[float]) -> float:
                total = 0.0
                for r in executed:
                    px = _float(r.get("entry_price")) or 0.0
                    if hi is None and px >= lo:
                        total += _float(r.get("pnl_yen_scaled")) or 0.0
                    elif hi is not None and lo <= px < hi:
                        total += _float(r.get("pnl_yen_scaled")) or 0.0
                return round(total, 2)

            vols = [_float(r.get("recent_volatility_pct")) or 0.0 for r in executed]
            p33 = _percentile(vols, 33) or 0.0
            p66 = _percentile(vols, 66) or 0.0
            high_vol_pnl = round(
                sum(
                    _float(r.get("pnl_yen_scaled")) or 0.0
                    for r in executed
                    if _vol_bucket(_float(r.get("recent_volatility_pct")) or 0.0, p33, p66) == "high_volatility"
                ),
                2,
            )
            low_vol_pnl = round(
                sum(
                    _float(r.get("pnl_yen_scaled")) or 0.0
                    for r in executed
                    if _vol_bucket(_float(r.get("recent_volatility_pct")) or 0.0, p33, p66) == "low_volatility"
                ),
                2,
            )

            rows.append(
                {
                    "equity_yen": equity,
                    "sizing_policy": policy,
                    "entry_count": len(executed),
                    "skipped_count": len(skipped),
                    "total_pnl_yen_scaled": round(sum(yens), 2),
                    "profit_factor": _pf(yens),
                    "win_rate": _win_rate(yens),
                    "max_loss_yen_scaled": round(min(yens), 2) if yens else None,
                    "pnl_stddev": _stddev(yens),
                    "avg_position_ratio": round(sum(ratios) / len(ratios), 6) if ratios else None,
                    "p95_position_ratio": _percentile(ratios, 95),
                    "avg_risk_budget_used": round(sum(risk_used) / len(risk_used), 6) if risk_used else None,
                    "high_price_pnl_scaled": _band_pnl(HIGH_PRICE_THRESHOLD, None),
                    "low_price_pnl_scaled": _band_pnl(0.0, LOW_PRICE_THRESHOLD),
                    "high_volatility_pnl_scaled": high_vol_pnl,
                    "low_volatility_pnl_scaled": low_vol_pnl,
                }
            )
    return rows


def build_volatility_bucket_rows(entry_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for equity in EQUITY_LEVELS:
        for policy in SIZING_POLICIES:
            subset = [
                r
                for r in entry_rows
                if int(r.get("equity_yen") or 0) == equity
                and str(r.get("sizing_policy") or "") == policy
                and not _bool(r.get("skipped_due_to_min_lot"))
            ]
            vols = [_float(r.get("recent_volatility_pct")) or 0.0 for r in subset]
            if not vols:
                continue
            p33 = _percentile(vols, 33) or 0.0
            p66 = _percentile(vols, 66) or 0.0
            for bucket in ("low_volatility", "mid_volatility", "high_volatility"):
                bucket_rows = [
                    r
                    for r in subset
                    if _vol_bucket(_float(r.get("recent_volatility_pct")) or 0.0, p33, p66) == bucket
                ]
                yens = [_float(r.get("pnl_yen_scaled")) or 0.0 for r in bucket_rows]
                shares = [_float(r.get("shares_shadow")) or 0.0 for r in bucket_rows]
                rows.append(
                    {
                        "equity_yen": equity,
                        "sizing_policy": policy,
                        "volatility_bucket": bucket,
                        "entry_count": len(bucket_rows),
                        "skipped_count": 0,
                        "total_pnl_yen_scaled": round(sum(yens), 2),
                        "profit_factor": _pf(yens),
                        "win_rate": _win_rate(yens),
                        "avg_shares_shadow": round(sum(shares) / len(shares), 2) if shares else None,
                    }
                )
    return rows


def _policy_row(policy_rows: Sequence[Mapping[str, Any]], *, equity: int, policy: str) -> dict[str, Any]:
    for row in policy_rows:
        if int(row.get("equity_yen") or 0) == equity and str(row.get("sizing_policy") or "") == policy:
            return dict(row)
    return {}


def build_verdict(
    *,
    policy_rows: Sequence[Mapping[str, Any]],
    phase260b: Mapping[str, Any],
) -> dict[str, Any]:
    fixed_5m = _policy_row(policy_rows, equity=5_000_000, policy="fixed_100_shares")
    equity30_5m = _policy_row(policy_rows, equity=5_000_000, policy="equity_30pct_cap")
    hybrid_5m = _policy_row(policy_rows, equity=5_000_000, policy="hybrid_equity30_risk1")
    risk1_5m = _policy_row(policy_rows, equity=5_000_000, policy="risk_1pct_equity")
    hybrid_1m = _policy_row(policy_rows, equity=1_000_000, policy="hybrid_equity30_risk1")

    phase260b_equity30_5m = None
    for row in phase260b.get("policy_by_equity") or []:
        if int(row.get("equity_yen") or 0) == 5_000_000 and str(row.get("sizing_policy") or "") == "max_position_30pct":
            phase260b_equity30_5m = dict(row)
            break

    fixed_total_5m = _float(fixed_5m.get("total_pnl_yen_scaled")) or 0.0
    equity30_total_5m = _float(equity30_5m.get("total_pnl_yen_scaled")) or 0.0
    hybrid_total_5m = _float(hybrid_5m.get("total_pnl_yen_scaled")) or 0.0
    low_price_equity30 = _float(equity30_5m.get("low_price_pnl_scaled")) or 0.0
    low_price_hybrid = _float(hybrid_5m.get("low_price_pnl_scaled")) or 0.0

    equity_only_sizing_overexpands_low_price = (
        low_price_equity30 < low_price_hybrid
        or abs(equity30_total_5m) > abs(hybrid_total_5m) + 1000.0
    )

    hybrid_policy_candidate = (
        hybrid_total_5m >= equity30_total_5m
        and (_float(hybrid_5m.get("high_price_pnl_scaled")) or 0.0)
        >= (_float(fixed_5m.get("high_price_pnl_scaled")) or 0.0) * 0.50
    )

    equity_1m_feasible = int(hybrid_1m.get("entry_count") or 0) >= 100

    equity_5m_feasible = (
        (_float(hybrid_5m.get("high_price_pnl_scaled")) or 0.0) > 0
        and int(hybrid_5m.get("entry_count") or 0) >= 200
    )

    phase260b_sizing_preferred = bool((phase260b.get("verdict") or {}).get("sizing_preferred_over_price_cap"))
    risk_sizing_preferred_over_price_cap = (
        hybrid_policy_candidate
        and equity_only_sizing_overexpands_low_price
        and phase260b_sizing_preferred
    )

    recommendation_parts: list[str] = []
    if equity_only_sizing_overexpands_low_price:
        recommendation_parts.append(
            "Equity-only 30% caps still amplify low/mid-price losses; risk budgets reduce share inflation."
        )
    if hybrid_policy_candidate:
        recommendation_parts.append(
            "hybrid_equity30_risk1 balances high-price access with per-trade risk limits on this sample."
        )
    if risk_sizing_preferred_over_price_cap:
        recommendation_parts.append(
            "Risk-aware sizing is preferred over price-cap removal for exposure control."
        )
    if not recommendation_parts:
        recommendation_parts.append("Treat risk-sizing shadows as indicative until overlap sample grows.")

    return {
        "risk_sizing_preferred_over_price_cap": risk_sizing_preferred_over_price_cap,
        "equity_only_sizing_overexpands_low_price": equity_only_sizing_overexpands_low_price,
        "hybrid_policy_candidate": hybrid_policy_candidate,
        "equity_1m_feasible": equity_1m_feasible,
        "equity_5m_feasible": equity_5m_feasible,
        "adoption_forbidden": True,
        "phase260b_equity30_total_pnl_5m": _float((phase260b_equity30_5m or {}).get("total_pnl_yen_scaled")),
        "recommendation": " ".join(recommendation_parts),
    }


def build_report_markdown(result: Mapping[str, Any]) -> str:
    verdict = result.get("verdict") or {}
    summary = result.get("summary") or {}
    lines = [
        "# Phase261 Risk-Aware Position Sizing Audit",
        "",
        "Shadow-only risk-based sizing vs Phase260B equity-only caps.",
        "",
        f"- overlap days: {', '.join(summary.get('trade_overlap_days') or [])}",
        f"- base entries: {summary.get('base_entry_count')}",
        f"- median recent_volatility_pct: {summary.get('median_recent_volatility_pct')}",
        "",
        "## Verdict",
        "",
        f"- risk_sizing_preferred_over_price_cap: {verdict.get('risk_sizing_preferred_over_price_cap')}",
        f"- equity_only_sizing_overexpands_low_price: {verdict.get('equity_only_sizing_overexpands_low_price')}",
        f"- hybrid_policy_candidate: {verdict.get('hybrid_policy_candidate')}",
        f"- equity_1m_feasible: {verdict.get('equity_1m_feasible')}",
        f"- equity_5m_feasible: {verdict.get('equity_5m_feasible')}",
        f"- adoption_forbidden: {verdict.get('adoption_forbidden')}",
        "",
        "## Policy totals at 5,000,000 yen",
        "",
    ]
    for row in result.get("policy_by_equity") or []:
        if int(row.get("equity_yen") or 0) != 5_000_000:
            continue
        lines.append(
            f"- `{row.get('sizing_policy')}`: pnl={row.get('total_pnl_yen_scaled')} "
            f"PF={row.get('profit_factor')} low_price_pnl={row.get('low_price_pnl_scaled')} "
            f"high_price_pnl={row.get('high_price_pnl_scaled')}"
        )
    lines.extend(["", str(verdict.get("recommendation") or ""), ""])
    return "\n".join(lines)


def run_risk_aware_sizing_audit(
    *,
    repo_root: Path,
    reports_dir: Path,
) -> dict[str, Any]:
    phase260b = _load_json(reports_dir / "phase260b_equity_position_sizing_summary.json")
    overlap_days = resolve_overlap_days(reports_dir)

    trades_by_day_raw = load_trades_by_day(repo_root)
    trades_by_day: dict[str, list[dict[str, Any]]] = {}
    for day, rows in trades_by_day_raw.items():
        norm_rows = []
        for row in rows:
            trade = dict(row)
            trade["symbol"] = _norm_symbol(str(trade.get("symbol") or ""))
            if trade.get("pnl_yen_100") is None:
                trade["pnl_yen_100"] = resolve_pnl_yen_100(trade)
            norm_rows.append(trade)
        trades_by_day[day] = norm_rows

    # Pre-pass for median volatility fallback
    vol_samples: list[float] = []
    for day in overlap_days:
        for row in trades_by_day.get(day) or []:
            sym = _norm_symbol(str(row.get("symbol") or ""))
            path = resolve_intraday_path(repo_root, day=day, symbol=sym)
            if not path:
                continue
            bars = load_intraday_bars(path)
            metrics = compute_intraday_metrics(
                bars,
                entry_ts=_parse_ts(str(row.get("entry_time") or "")),
                entry_price=_float(row.get("entry_price")) or 0.0,
            )
            vol = _float(metrics.get("recent_volatility_pct"))
            if vol is not None and vol > 0:
                vol_samples.append(vol)
    median_vol = statistics.median(vol_samples) if vol_samples else 0.15

    base_entries = enrich_base_entries(
        trades_by_day,
        overlap_days=overlap_days,
        repo_root=repo_root,
        median_volatility=median_vol,
    )
    entry_rows = build_entry_level_rows(base_entries)
    policy_rows = aggregate_policy_rows(entry_rows)
    volatility_bucket_rows = build_volatility_bucket_rows(entry_rows)
    verdict = build_verdict(policy_rows=policy_rows, phase260b=phase260b)

    return {
        "phase": "261-Risk-Aware-Position-Sizing-Audit",
        "title": "Risk-aware position sizing shadow audit",
        "generated_at": _now_iso(),
        "purpose": "Evaluate risk-based sizing vs equity-only caps after Phase260B loss amplification",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
            "adoption_forbidden": True,
        },
        "inputs": {
            "structural_trades": str(repo_root / "kabu_native" / "results" / "small_paper"),
            "intraday_1m": [str(p) for p in intraday_roots(repo_root)],
            "phase260b_summary": str(reports_dir / "phase260b_equity_position_sizing_summary.json"),
        },
        "equity_levels_yen": list(EQUITY_LEVELS),
        "sizing_policies": list(SIZING_POLICIES),
        "risk_model": {
            "default_stop_distance_pct": DEFAULT_STOP_DISTANCE_PCT,
            "risk_per_100_formula": "entry_price * 100 * stop_distance_pct / 100",
            "stop_distance_rule": "max(1.2%, abs(mae_pct), atr_proxy_pct)",
        },
        "summary": {
            "trade_overlap_days": overlap_days,
            "base_entry_count": len(base_entries),
            "median_recent_volatility_pct": round(median_vol, 4),
            "intraday_coverage_entry_count": sum(
                1 for e in base_entries if e.get("intraday_range_pct") is not None
            ),
        },
        "verdict": verdict,
        "policy_by_equity": policy_rows,
        "volatility_bucket_analysis": volatility_bucket_rows,
        "_entry_rows": entry_rows,
    }


@dataclass
class RiskAwareSizingAudit:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase261_risk_aware_sizing_summary.json",
            "entry_level": self.reports_dir / "phase261_entry_level_risk_sizing.csv",
            "policy_by_equity": self.reports_dir / "phase261_policy_by_equity.csv",
            "volatility_bucket": self.reports_dir / "phase261_volatility_bucket_analysis.csv",
            "report": self.reports_dir / "phase261_report.md",
        }

    def run(self) -> dict[str, Any]:
        return run_risk_aware_sizing_audit(repo_root=self.repo_root, reports_dir=self.reports_dir)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["entry_level"], ENTRY_RISK_SIZING_FIELDS, result.get("_entry_rows") or [])
        _write_csv(paths["policy_by_equity"], POLICY_BY_EQUITY_FIELDS, result.get("policy_by_equity") or [])
        _write_csv(paths["volatility_bucket"], VOLATILITY_BUCKET_FIELDS, result.get("volatility_bucket_analysis") or [])
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["report"].write_text(build_report_markdown(result), encoding="utf-8")
        return paths
