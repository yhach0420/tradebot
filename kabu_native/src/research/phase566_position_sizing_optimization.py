"""
Phase566 — Position sizing optimization study (research only).

Compares sizing policies on Phase558 latest Runtime accepted trades (20260529–20260625).
No Runtime changes.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import JST, _now_iso
from research.phase524_live_reentry_guard_and_stop_low_mfe import _latest_live_day
from research.phase561_trailing_shadow_validation import (
    FULL_END,
    FULL_START,
    LIVE_START,
    _load_full_period_accepted,
)
from research.position_exposure_audit import price_band_label
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE566_VERDICT = "phase566_position_sizing_optimization_done"

CAPITAL_LEVELS: tuple[int, ...] = (1_000_000, 3_000_000, 5_000_000)
MIN_LOT = 100
HIGH_PRICE_THRESHOLD = 3000.0
LOW_PRICE_THRESHOLD = 500.0
RISK_PER_TRADE_PCT = 0.01
STOP_RISK_PCT = 0.012

SIZING_POLICIES: tuple[tuple[str, str], ...] = (
    ("fixed_100", "Fixed 100 shares"),
    ("max_position_20pct", "Max position 20% of equity"),
    ("max_position_30pct", "Max position 30% of equity"),
    ("max_position_40pct", "Max position 40% of equity"),
    ("risk_per_trade_1pct", "Risk 1% per trade (1.2% stop)"),
    ("price_band_sizing", "Price-band position caps"),
    ("high_price_cap", "High-price cap (>=3000 yen → max 100 shares)"),
    ("low_price_suppress", "Low-price suppress (<=500 yen → 15% cap)"),
)

PRICE_BAND_MAX_PCT: dict[str, float] = {
    "<300": 0.15,
    "300-1000": 0.25,
    "1000-3000": 0.30,
    "3000-5000": 0.20,
    "5000-10000": 0.15,
    "10000+": 0.10,
    "unknown": 0.20,
}

SUMMARY_FIELDS = [
    "sizing_policy",
    "sizing_label",
    "initial_equity_yen",
    "executed_trades",
    "capital_skip_count",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "win_rate",
    "avg_position_ratio",
    "p95_position_ratio",
    "high_price_trades",
    "low_price_trades",
    "worst_loss_yen",
    "best_win_yen",
    "final_equity_yen",
    "total_return_pct",
    "delta_pnl_vs_fixed_100",
    "delta_pf_vs_fixed_100",
    "delta_maxdd_vs_fixed_100",
]

EQUITY_CURVE_FIELDS = [
    "day",
    "entry_time",
    "sizing_policy",
    "initial_equity_yen",
    "equity_yen",
    "daily_pnl_yen",
    "drawdown_yen",
    "executed_trades_cum",
    "capital_skip_cum",
]

PRICE_BAND_FIELDS = [
    "price_band",
    "sizing_policy",
    "initial_equity_yen",
    "trade_count",
    "capital_skip_count",
    "total_pnl_yen",
    "profit_factor",
    "win_rate",
    "avg_position_ratio",
    "pnl_share_pct",
]

CAPITAL_SKIP_FIELDS = [
    "sizing_policy",
    "initial_equity_yen",
    "skip_reason",
    "skip_count",
    "skipped_pnl_yen_100",
    "avg_entry_price",
    "high_price_skip_count",
    "low_price_skip_count",
]


def _num(v: Any) -> float:
    return _float(v) or 0.0


def _max_position_pct(policy: str, entry_price: float) -> Optional[float]:
    if policy == "max_position_20pct":
        return 0.20
    if policy == "max_position_30pct":
        return 0.30
    if policy == "max_position_40pct":
        return 0.40
    if policy == "price_band_sizing":
        return PRICE_BAND_MAX_PCT.get(price_band_label(entry_price), 0.20)
    if policy == "low_price_suppress":
        return 0.15 if entry_price <= LOW_PRICE_THRESHOLD else 0.30
    return None


def compute_shares(
    *,
    equity: float,
    entry_price: float,
    policy: str,
) -> tuple[int, Optional[str]]:
    if entry_price <= 0 or equity <= 0:
        return 0, "invalid_price"

    if policy == "fixed_100":
        pos_val = entry_price * MIN_LOT
        if pos_val > equity:
            return 0, "insufficient_equity"
        return MIN_LOT, None

    if policy == "risk_per_trade_1pct":
        risk_yen = equity * RISK_PER_TRADE_PCT
        stop_yen_per_share = entry_price * STOP_RISK_PCT
        if stop_yen_per_share <= 0:
            return 0, "invalid_stop"
        lots = math.floor(risk_yen / stop_yen_per_share / MIN_LOT)
        shares = int(lots * MIN_LOT)
        cap_budget = equity * 0.40
        max_shares = int(math.floor(cap_budget / entry_price / MIN_LOT) * MIN_LOT)
        if max_shares >= MIN_LOT:
            shares = min(shares, max_shares)
        if shares < MIN_LOT:
            return 0, "risk_size_below_min_lot"
        return shares, None

    if policy == "high_price_cap":
        pct = 0.30
        budget = equity * pct
        lots = math.floor(budget / entry_price / MIN_LOT)
        shares = int(lots * MIN_LOT)
        if entry_price >= HIGH_PRICE_THRESHOLD:
            shares = min(shares, MIN_LOT)
        if shares < MIN_LOT:
            return 0, "below_min_lot"
        return shares, None

    pct = _max_position_pct(policy, entry_price)
    if pct is None:
        return MIN_LOT, None
    budget = equity * pct
    lots = math.floor(budget / entry_price / MIN_LOT)
    shares = int(lots * MIN_LOT)
    if shares < MIN_LOT:
        return 0, "below_min_lot"
    return shares, None


def _prepare_trades(accepted: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for t in accepted:
        ep = _float(t.get("entry_price"))
        if ep is None or ep <= 0:
            continue
        pnl100 = _num(t.get("pnl_yen_100"))
        rows.append(
            {
                "day": str(t.get("day") or "")[:8],
                "symbol": str(t.get("symbol") or ""),
                "entry_time": str(t.get("entry_time") or ""),
                "exit_time": str(t.get("exit_time") or ""),
                "entry_price": round(ep, 4),
                "price_band": price_band_label(ep),
                "pnl_yen_100": round(pnl100, 2),
                "exit_reason": str(t.get("exit_reason") or ""),
                "entry_type": str(t.get("entry_type") or ""),
                "_segment": str(t.get("_segment") or ""),
            }
        )
    rows.sort(key=lambda r: _parse_ts(r.get("entry_time")) or datetime.min.replace(tzinfo=JST))
    return rows


def simulate_sizing_policy(
    trades: Sequence[Mapping[str, Any]],
    *,
    initial_equity: int,
    policy: str,
) -> dict[str, Any]:
    equity = float(initial_equity)
    peak = equity
    max_dd = 0.0
    executed = 0
    skips = 0
    skip_reasons: dict[str, int] = {}
    skip_rows: list[dict[str, Any]] = []
    pnls: list[float] = []
    ratios: list[float] = []
    high_price = 0
    low_price = 0
    equity_curve: list[dict[str, Any]] = []
    daily_pnl: dict[str, float] = {}

    for t in trades:
        ep = _num(t.get("entry_price"))
        shares, skip_reason = compute_shares(equity=equity, entry_price=ep, policy=policy)
        pnl100 = _num(t.get("pnl_yen_100"))
        day = str(t.get("day") or "")

        if shares < MIN_LOT:
            skips += 1
            reason = skip_reason or "skipped"
            skip_reasons[reason] = skip_reasons.get(reason, 0) + 1
            skip_rows.append(
                {
                    "day": day,
                    "symbol": t.get("symbol"),
                    "entry_price": ep,
                    "skip_reason": reason,
                    "pnl_yen_100": pnl100,
                    "price_band": t.get("price_band"),
                }
            )
            continue

        pos_val = ep * shares
        ratio = pos_val / equity if equity > 0 else 0.0
        pnl = round(pnl100 * shares / MIN_LOT, 2)
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        executed += 1
        pnls.append(pnl)
        ratios.append(ratio)
        if ep >= HIGH_PRICE_THRESHOLD:
            high_price += 1
        if ep <= LOW_PRICE_THRESHOLD:
            low_price += 1
        daily_pnl[day] = daily_pnl.get(day, 0.0) + pnl
        equity_curve.append(
            {
                "day": day,
                "entry_time": t.get("entry_time"),
                "equity_yen": round(equity, 2),
                "trade_pnl_yen": pnl,
                "shares": shares,
                "position_ratio": round(ratio, 6),
            }
        )

    cum_eq = float(initial_equity)
    cum_peak = cum_eq
    curve_out: list[dict[str, Any]] = []
    skip_cum = 0
    exec_cum = 0
    for day in sorted(daily_pnl.keys()):
        dpnl = daily_pnl[day]
        cum_eq += dpnl
        cum_peak = max(cum_peak, cum_eq)
        curve_out.append(
            {
                "day": day,
                "entry_time": day,
                "sizing_policy": policy,
                "initial_equity_yen": initial_equity,
                "equity_yen": round(cum_eq, 2),
                "daily_pnl_yen": round(dpnl, 2),
                "drawdown_yen": round(cum_peak - cum_eq, 2),
                "executed_trades_cum": exec_cum,
                "capital_skip_cum": skip_cum,
            }
        )

    return {
        "sizing_policy": policy,
        "initial_equity_yen": initial_equity,
        "executed_trades": executed,
        "capital_skip_count": skips,
        "total_pnl_yen": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "max_drawdown_yen": round(max_dd, 2),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
        "avg_position_ratio": round(statistics.mean(ratios), 6) if ratios else 0.0,
        "p95_position_ratio": round(sorted(ratios)[int(len(ratios) * 0.95)] if ratios else 0.0, 6),
        "high_price_trades": high_price,
        "low_price_trades": low_price,
        "worst_loss_yen": round(min(pnls), 2) if pnls else 0.0,
        "best_win_yen": round(max(pnls), 2) if pnls else 0.0,
        "final_equity_yen": round(equity, 2),
        "total_return_pct": round((equity - initial_equity) / initial_equity * 100.0, 4),
        "_pnls": pnls,
        "_skip_rows": skip_rows,
        "_skip_reasons": skip_reasons,
        "_equity_curve_daily": curve_out,
        "_trade_curve": equity_curve,
    }


def _fixed_100_distortion(trades: Sequence[Mapping[str, Any]], *, equity: int) -> dict[str, Any]:
    ratios = []
    band_pnl: dict[str, float] = {}
    band_count: dict[str, int] = {}
    for t in trades:
        ep = _num(t.get("entry_price"))
        if ep <= 0:
            continue
        ratio = (ep * MIN_LOT) / float(equity)
        ratios.append(ratio)
        band = price_band_label(ep)
        band_pnl[band] = band_pnl.get(band, 0.0) + _num(t.get("pnl_yen_100"))
        band_count[band] = band_count.get(band, 0) + 1
    total_abs = sum(abs(v) for v in band_pnl.values()) or 1.0
    top_band = max(band_pnl.items(), key=lambda kv: abs(kv[1])) if band_pnl else ("", 0.0)
    return {
        "equity_yen": equity,
        "trade_count": len(ratios),
        "position_ratio_mean": round(statistics.mean(ratios), 6) if ratios else 0.0,
        "position_ratio_stdev": round(statistics.pstdev(ratios), 6) if len(ratios) > 1 else 0.0,
        "position_ratio_max": round(max(ratios), 6) if ratios else 0.0,
        "trades_over_50pct_equity": sum(1 for r in ratios if r > 0.50),
        "trades_over_100pct_equity": sum(1 for r in ratios if r > 1.0),
        "top_pnl_band": top_band[0],
        "top_pnl_band_share_pct": round(abs(top_band[1]) / total_abs * 100.0, 2),
        "high_price_pnl_yen_100": round(
            sum(_num(t.get("pnl_yen_100")) for t in trades if _num(t.get("entry_price")) >= HIGH_PRICE_THRESHOLD),
            2,
        ),
        "low_price_pnl_yen_100": round(
            sum(_num(t.get("pnl_yen_100")) for t in trades if _num(t.get("entry_price")) <= LOW_PRICE_THRESHOLD),
            2,
        ),
    }


def _price_band_rows(
    trades: Sequence[Mapping[str, Any]],
    sim: Mapping[str, Any],
    *,
    initial_equity: int,
    policy: str,
) -> list[dict[str, Any]]:
    executed_by_band: dict[str, list[float]] = {}
    skip_by_band: dict[str, int] = {}
    ratio_by_band: dict[str, list[float]] = {}

    skip_set = {
        (str(r.get("day")), str(r.get("symbol")), _num(r.get("entry_price")))
        for r in sim.get("_skip_rows") or []
    }
    for t in trades:
        band = str(t.get("price_band") or price_band_label(_num(t.get("entry_price"))))
        key = (str(t.get("day")), str(t.get("symbol")), _num(t.get("entry_price")))
        if key in skip_set:
            skip_by_band[band] = skip_by_band.get(band, 0) + 1
            continue
        ep = _num(t.get("entry_price"))
        shares, _ = compute_shares(equity=float(initial_equity), entry_price=ep, policy=policy)
        if shares < MIN_LOT:
            skip_by_band[band] = skip_by_band.get(band, 0) + 1
            continue
        pnl = _num(t.get("pnl_yen_100")) * shares / MIN_LOT
        executed_by_band.setdefault(band, []).append(pnl)
        ratio_by_band.setdefault(band, []).append(ep * shares / float(initial_equity))

    total_pnl = sum(sum(v) for v in executed_by_band.values()) or 0.0
    rows: list[dict[str, Any]] = []
    bands = sorted(set(list(executed_by_band.keys()) + list(skip_by_band.keys())))
    for band in bands:
        pnls = executed_by_band.get(band) or []
        pnl_sum = sum(pnls)
        rows.append(
            {
                "price_band": band,
                "sizing_policy": policy,
                "initial_equity_yen": initial_equity,
                "trade_count": len(pnls),
                "capital_skip_count": skip_by_band.get(band, 0),
                "total_pnl_yen": round(pnl_sum, 2),
                "profit_factor": _pf(pnls),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
                "avg_position_ratio": round(statistics.mean(ratio_by_band.get(band) or [0.0]), 6),
                "pnl_share_pct": round(pnl_sum / total_pnl * 100.0, 2) if total_pnl else 0.0,
            }
        )
    return rows


def _capital_skip_rows(sim: Mapping[str, Any]) -> list[dict[str, Any]]:
    policy = str(sim.get("sizing_policy") or "")
    equity = int(sim.get("initial_equity_yen") or 0)
    by_reason: dict[str, list[dict[str, Any]]] = {}
    for row in sim.get("_skip_rows") or []:
        reason = str(row.get("skip_reason") or "skipped")
        by_reason.setdefault(reason, []).append(dict(row))

    rows: list[dict[str, Any]] = []
    for reason, items in sorted(by_reason.items()):
        prices = [_num(r.get("entry_price")) for r in items]
        rows.append(
            {
                "sizing_policy": policy,
                "initial_equity_yen": equity,
                "skip_reason": reason,
                "skip_count": len(items),
                "skipped_pnl_yen_100": round(sum(_num(r.get("pnl_yen_100")) for r in items), 2),
                "avg_entry_price": round(statistics.mean(prices), 2) if prices else 0.0,
                "high_price_skip_count": sum(
                    1 for r in items if _num(r.get("entry_price")) >= HIGH_PRICE_THRESHOLD
                ),
                "low_price_skip_count": sum(
                    1 for r in items if _num(r.get("entry_price")) <= LOW_PRICE_THRESHOLD
                ),
            }
        )
    return rows


def _best_policy(
    summary_rows: Sequence[Mapping[str, Any]],
    *,
    equity: int,
    exclude: Optional[set[str]] = None,
) -> dict[str, Any]:
    ex = exclude or set()
    candidates = [
        r
        for r in summary_rows
        if int(r.get("initial_equity_yen") or 0) == equity
        and str(r.get("sizing_policy") or "") not in ex
    ]
    if not candidates:
        return {}

    def score(r: Mapping[str, Any]) -> float:
        pnl = _num(r.get("total_pnl_yen"))
        pf = _num(r.get("profit_factor"))
        dd = _num(r.get("max_drawdown_yen"))
        skips = int(r.get("capital_skip_count") or 0)
        return pnl + pf * 50000.0 - dd * 0.5 - skips * 2000.0

    return max(candidates, key=score)


def _mandatory_answers(
    *,
    trades: Sequence[Mapping[str, Any]],
    summary_rows: Sequence[Mapping[str, Any]],
    distortion_1m: Mapping[str, Any],
) -> dict[str, Any]:
    fixed = {
        (int(r["initial_equity_yen"]), r["sizing_policy"]): r
        for r in summary_rows
        if r.get("sizing_policy") == "fixed_100"
    }

    def _row(equity: int, policy: str) -> dict[str, Any]:
        return dict(next((r for r in summary_rows if int(r.get("initial_equity_yen") or 0) == equity and r.get("sizing_policy") == policy), {}))

    best_1m = _best_policy(summary_rows, equity=1_000_000)
    best_3m = _best_policy(summary_rows, equity=3_000_000)
    best_5m = _best_policy(summary_rows, equity=5_000_000)

    f1 = fixed.get((1_000_000, "fixed_100"), {})
    m20_1 = _row(1_000_000, "max_position_20pct")
    m30_3 = _row(3_000_000, "max_position_30pct")
    m40_5 = _row(5_000_000, "max_position_40pct")

    distortion_large = (
        int(distortion_1m.get("trades_over_100pct_equity") or 0) >= 5
        or float(distortion_1m.get("position_ratio_stdev") or 0) >= 0.15
    )

    hp_pnl = _num(distortion_1m.get("high_price_pnl_yen_100"))
    lp_pnl = _num(distortion_1m.get("low_price_pnl_yen_100"))
    hp_cap = _row(1_000_000, "high_price_cap")
    lp_sup = _row(1_000_000, "low_price_suppress")

    pf_improved = any(
        _num(r.get("profit_factor")) > _num(fixed.get((int(r["initial_equity_yen"]), "fixed_100"), {}).get("profit_factor"))
        for r in summary_rows
        if r.get("sizing_policy") != "fixed_100"
    )
    dd_improved = any(
        _num(r.get("max_drawdown_yen")) < _num(fixed.get((int(r["initial_equity_yen"]), "fixed_100"), {}).get("max_drawdown_yen"))
        for r in summary_rows
        if r.get("sizing_policy") != "fixed_100"
    )

    max_pct_scores = []
    for pct in (20, 30, 40):
        rows = [r for r in summary_rows if r.get("sizing_policy") == f"max_position_{pct}pct"]
        avg_score = statistics.mean(
            [_num(r.get("total_pnl_yen")) - _num(r.get("max_drawdown_yen")) * 0.3 for r in rows]
        ) if rows else 0.0
        max_pct_scores.append((pct, avg_score))
    best_max_pct = max(max_pct_scores, key=lambda x: x[1])[0] if max_pct_scores else 30

    runtime_candidate = False
    for eq in CAPITAL_LEVELS:
        b = _best_policy(summary_rows, equity=eq)
        f = fixed.get((eq, "fixed_100"), {})
        if (
            b.get("sizing_policy") != "fixed_100"
            and _num(b.get("total_pnl_yen")) > _num(f.get("total_pnl_yen"))
            and _num(b.get("profit_factor")) >= _num(f.get("profit_factor"))
            and _num(b.get("max_drawdown_yen")) <= _num(f.get("max_drawdown_yen")) + 10000
            and int(b.get("capital_skip_count") or 0) <= int(f.get("capital_skip_count") or 0) + 20
        ):
            runtime_candidate = True

    return {
        "1_fixed_100_distortion_large": distortion_large,
        "1_distortion_detail": distortion_1m,
        "2_best_sizing_1M": best_1m.get("sizing_policy"),
        "2_best_sizing_1M_pnl": best_1m.get("total_pnl_yen"),
        "2_best_sizing_1M_skips": best_1m.get("capital_skip_count"),
        "3_best_sizing_3M": best_3m.get("sizing_policy"),
        "3_best_sizing_3M_pnl": best_3m.get("total_pnl_yen"),
        "4_best_sizing_5M": best_5m.get("sizing_policy"),
        "4_best_sizing_5M_pnl": best_5m.get("total_pnl_yen"),
        "5_keep_high_price_trades": hp_pnl > 0 and _num(hp_cap.get("total_pnl_yen")) < _num(f1.get("total_pnl_yen")) * 0.85,
        "5_high_price_pnl_yen_100": hp_pnl,
        "6_suppress_low_price": lp_pnl < 0 or _num(lp_sup.get("total_pnl_yen")) > _num(f1.get("total_pnl_yen")),
        "6_low_price_pnl_yen_100": lp_pnl,
        "7_recommended_max_position_pct": best_max_pct,
        "8_pf_improves_vs_fixed_100": pf_improved,
        "9_maxdd_improves_vs_fixed_100": dd_improved,
        "10_runtime_candidate": runtime_candidate,
        "11_next_phase": (
            "phase567_position_sizing_shadow_daily_monitor"
            if runtime_candidate
            else "phase567_position_sizing_observation_hold"
        ),
        "reference_fixed_100": {
            "1M": {k: f1.get(k) for k in ("total_pnl_yen", "profit_factor", "max_drawdown_yen", "capital_skip_count")},
            "3M": {k: fixed.get((3_000_000, "fixed_100"), {}).get(k) for k in ("total_pnl_yen", "profit_factor", "max_drawdown_yen")},
            "5M": {k: fixed.get((5_000_000, "fixed_100"), {}).get(k) for k in ("total_pnl_yen", "profit_factor", "max_drawdown_yen")},
        },
        "max_position_compare_1M": {
            "20pct": {k: m20_1.get(k) for k in ("total_pnl_yen", "profit_factor", "max_drawdown_yen", "capital_skip_count")},
            "30pct": {k: _row(1_000_000, "max_position_30pct").get(k) for k in ("total_pnl_yen", "profit_factor", "max_drawdown_yen", "capital_skip_count")},
        },
    }


@dataclass
class Phase566Job:
    repo_root: Path
    period_start: str = FULL_START
    live_start: str = LIVE_START
    period_end: str = FULL_END

    def run(self) -> dict[str, Any]:
        repo = self.repo_root.resolve()
        end = min(self.period_end, _latest_live_day(repo))
        trades = _prepare_trades(
            _load_full_period_accepted(
                repo, full_start=self.period_start, live_start=self.live_start, end=end
            )
        )
        if not trades:
            raise RuntimeError("No Phase558 accepted trades for Phase566")

        sims: list[dict[str, Any]] = []
        summary_rows: list[dict[str, Any]] = []
        equity_curve_rows: list[dict[str, Any]] = []
        price_band_rows: list[dict[str, Any]] = []
        capital_skip_rows: list[dict[str, Any]] = []

        for initial in CAPITAL_LEVELS:
            for policy_id, label in SIZING_POLICIES:
                sim = simulate_sizing_policy(trades, initial_equity=initial, policy=policy_id)
                sims.append(sim)
                row = {k: sim.get(k) for k in SUMMARY_FIELDS if not k.startswith("delta_")}
                row["sizing_label"] = label
                summary_rows.append(row)
                equity_curve_rows.extend(sim.get("_equity_curve_daily") or [])
                price_band_rows.extend(
                    _price_band_rows(trades, sim, initial_equity=initial, policy=policy_id)
                )
                capital_skip_rows.extend(_capital_skip_rows(sim))

        fixed_by_eq = {
            int(r["initial_equity_yen"]): r
            for r in summary_rows
            if r.get("sizing_policy") == "fixed_100"
        }
        for row in summary_rows:
            f = fixed_by_eq.get(int(row.get("initial_equity_yen") or 0), {})
            row["delta_pnl_vs_fixed_100"] = round(_num(row.get("total_pnl_yen")) - _num(f.get("total_pnl_yen")), 2)
            row["delta_pf_vs_fixed_100"] = round(_num(row.get("profit_factor")) - _num(f.get("profit_factor")), 4)
            row["delta_maxdd_vs_fixed_100"] = round(
                _num(row.get("max_drawdown_yen")) - _num(f.get("max_drawdown_yen")), 2
            )

        distortion_1m = _fixed_100_distortion(trades, equity=1_000_000)
        mandatory = _mandatory_answers(
            trades=trades, summary_rows=summary_rows, distortion_1m=distortion_1m
        )

        return {
            "verdict": PHASE566_VERDICT,
            "generated_at": _now_iso(),
            "period": f"{self.period_start}-{end}",
            "trade_count": len(trades),
            "capital_levels": list(CAPITAL_LEVELS),
            "sizing_policies": [p[0] for p in SIZING_POLICIES],
            "summary": summary_rows,
            "equity_curve": equity_curve_rows,
            "price_band_analysis": price_band_rows,
            "capital_skip_analysis": capital_skip_rows,
            "distortion_fixed_100_1M": distortion_1m,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root.resolve())
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "summary": reports / "phase566_position_sizing_summary.csv",
            "equity_curve": reports / "phase566_equity_curve.csv",
            "price_band": reports / "phase566_price_band_analysis.csv",
            "capital_skip": reports / "phase566_capital_skip_analysis.csv",
            "report": reports / "phase566_report.json",
            "doc": resolve_kabu_root(self.repo_root) / "docs" / "operations" / "phase566_position_sizing_optimization.md",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary") or []))
        _write_csv(paths["equity_curve"], EQUITY_CURVE_FIELDS, list(result.get("equity_curve") or []))
        _write_csv(paths["price_band"], PRICE_BAND_FIELDS, list(result.get("price_band_analysis") or []))
        _write_csv(paths["capital_skip"], CAPITAL_SKIP_FIELDS, list(result.get("capital_skip_analysis") or []))
        paths["report"].write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
        ma = result.get("mandatory_answers") or {}
        paths["doc"].parent.mkdir(parents=True, exist_ok=True)
        paths["doc"].write_text(
            "\n".join(
                [
                    "# Phase566 — Position Sizing Optimization Study",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**Period:** {result.get('period')}",
                    f"**Trades:** {result.get('trade_count')}",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. fixed 100-share distortion large: {ma.get('1_fixed_100_distortion_large')}",
                    f"2. best sizing 1M: {ma.get('2_best_sizing_1M')} (pnl={ma.get('2_best_sizing_1M_pnl')}, skips={ma.get('2_best_sizing_1M_skips')})",
                    f"3. best sizing 3M: {ma.get('3_best_sizing_3M')} (pnl={ma.get('3_best_sizing_3M_pnl')})",
                    f"4. best sizing 5M: {ma.get('4_best_sizing_5M')} (pnl={ma.get('4_best_sizing_5M_pnl')})",
                    f"5. keep high-price trades: {ma.get('5_keep_high_price_trades')}",
                    f"6. suppress low-price trades: {ma.get('6_suppress_low_price')}",
                    f"7. recommended max_position %: {ma.get('7_recommended_max_position_pct')}",
                    f"8. PF improves vs fixed 100: {ma.get('8_pf_improves_vs_fixed_100')}",
                    f"9. maxDD improves vs fixed 100: {ma.get('9_maxdd_improves_vs_fixed_100')}",
                    f"10. runtime candidate: {ma.get('10_runtime_candidate')}",
                    f"11. next phase: {ma.get('11_next_phase')}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        return paths
