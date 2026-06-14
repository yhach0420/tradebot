"""
Phase386: Third position quality review (CAP2 vs CAP3 delta trades).

Identifies trades accepted at CAP=3 but rejected at CAP=2 under Phase385 conditions
and compares quality vs the CAP=2 accepted cohort.
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

from research.phase377_daily_regime_breakdown import PRIMARY_STACK
from research.phase379_380_period_b_eval import is_low_mfe_stop, is_stop_hit
from research.phase382_capital_constrained_backtest import (
    _day_from_ts,
    _float,
    _parse_ts,
    _pf,
    _position_key,
    _write_csv,
    dedupe_trades,
)
from research.phase383_realistic_credit_sizing_backtest import build_event_timeline
from research.phase385_cap_sensitivity_study import (
    DEFAULT_EQUITY_FLOOR,
    DEFAULT_INITIAL_EQUITY,
    FIXED_SPEC,
    CapScenarioState,
)

JST = ZoneInfo("Asia/Tokyo")
DEFAULT_MIN_DAY = "20260529"
DEFAULT_MAX_DAY = "20260612"
CAP2 = 2
CAP3 = 3

TRADE_CSV_FIELDS = [
    "cohort",
    "symbol",
    "dynamic40_rank",
    "dynamic40_rank_bucket",
    "entry_score",
    "entry_time",
    "session_kind",
    "time_bucket",
    "board_tier",
    "momentum",
    "vwap_dev",
    "rise_5min",
    "exit_reason",
    "pnl_yen_100",
    "peak_mfe",
    "peak_mae",
    "hold_sec",
    "cap2_reject_reason",
]


def _board_tier(trade: Mapping[str, Any]) -> str:
    return str(
        trade.get("board_dynamic_tier")
        or trade.get("board_dynamic_trailing_tier")
        or trade.get("board_tier")
        or "unknown"
    )


def _entry_score(trade: Mapping[str, Any]) -> Optional[float]:
    for key in (
        "entry_score_v2",
        "entry_expectancy_score_v2",
        "entry_score",
        "entry_momentum_score",
    ):
        val = _float(trade.get(key))
        if val is not None:
            return val
    return None


def _momentum(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("entry_momentum_score") or trade.get("momentum"))


def _time_bucket(trade: Mapping[str, Any]) -> str:
    tb = str(trade.get("time_bucket") or trade.get("entry_time_bucket") or "")
    if tb:
        return tb
    entry = str(trade.get("entry_time") or "")
    if "T" in entry:
        hh = entry.split("T", 1)[1][:2]
        if hh.isdigit():
            h = int(hh)
            if h < 10:
                return "open_09"
            if h < 12:
                return "mid_am"
            if h < 14:
                return "lunch"
            return "pm"
    return str(trade.get("session_kind") or "unknown")


def _dynamic40_rank(trade: Mapping[str, Any]) -> str:
    rank = trade.get("dynamic_rank") or trade.get("dynamic40_rank")
    if rank not in (None, ""):
        return str(rank)
    return str(trade.get("dynamic40_rank_bucket") or "")


@dataclass
class TrackedCapState(CapScenarioState):
    rejected_entries: dict[str, str] = field(default_factory=dict)

    def _reject_entry(self, trade: Mapping[str, Any], reason: str) -> None:
        super()._reject_entry(trade, reason)
        self.rejected_entries[_position_key(trade)] = reason


def simulate_cap_acceptance(
    trades: Sequence[Mapping[str, Any]],
    *,
    cap: int,
    initial_equity: float = DEFAULT_INITIAL_EQUITY,
    equity_floor: float = DEFAULT_EQUITY_FLOOR,
) -> dict[str, Any]:
    state = TrackedCapState(
        scenario_id=f"CAP_{cap}",
        max_concurrent_positions=cap,
        spec=dict(FIXED_SPEC),
        initial_equity=initial_equity,
        equity_floor=equity_floor,
    )
    events = build_event_timeline(trades)
    for dt, _, kind, trade in events:
        ts = dt.isoformat()
        day = _day_from_ts(ts)
        if kind == "entry":
            state.try_entry(trade, ts, day)
        else:
            state.process_exit(trade, ts, day)
    if state.open_positions and events:
        last_ts = events[-1][0].isoformat()
        last_day = _day_from_ts(last_ts)
        state._force_close_all(last_ts, last_day, reason="end_of_period")
    return {
        "cap": cap,
        "accepted_keys": set(state.accepted_keys),
        "rejected_entries": dict(state.rejected_entries),
        "accepted_trade_count": state.accepted_trade_count,
        "rejected_trade_count": state.rejected_trade_count,
    }


def trade_lookup(trades: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_position_key(t): dict(t) for t in trades}


def enrich_trade_row(trade: Mapping[str, Any], *, cohort: str, cap2_reject_reason: str = "") -> dict[str, Any]:
    return {
        "cohort": cohort,
        "symbol": trade.get("symbol"),
        "dynamic40_rank": _dynamic40_rank(trade),
        "dynamic40_rank_bucket": trade.get("dynamic40_rank_bucket") or "",
        "entry_score": _entry_score(trade),
        "entry_time": trade.get("entry_time"),
        "session_kind": trade.get("session_kind") or "",
        "time_bucket": _time_bucket(trade),
        "board_tier": _board_tier(trade),
        "momentum": _momentum(trade),
        "vwap_dev": _float(trade.get("entry_vwap_dev_pct")),
        "rise_5min": _float(trade.get("entry_rise_5min_pct")),
        "exit_reason": str(trade.get("exit_reason_canonical") or trade.get("exit_reason") or ""),
        "pnl_yen_100": _float(trade.get("pnl_yen_100")),
        "peak_mfe": _float(trade.get("peak_mfe_pct")),
        "peak_mae": _float(trade.get("peak_mae_pct")),
        "hold_sec": _float(trade.get("hold_sec")) or _float(trade.get("hold_duration_sec")),
        "cap2_reject_reason": cap2_reject_reason,
        "_trade": dict(trade),
    }


def cohort_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in trades]
    mfes = [_float(t.get("peak_mfe_pct")) for t in trades]
    mfe_valid = [float(v) for v in mfes if v is not None]
    n = len(trades)
    wins = sum(1 for p in pnls if p > 0)
    stops = sum(1 for t in trades if is_stop_hit(t))
    low_mfe = sum(1 for t in trades if is_low_mfe_stop(t))
    return {
        "trade_count": n,
        "total_pnl_yen_100": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "win_rate": round(wins / n, 4) if n else 0.0,
        "avg_pnl_yen_100": round(statistics.mean(pnls), 2) if pnls else 0.0,
        "avg_mfe_pct": round(statistics.mean(mfe_valid), 4) if mfe_valid else None,
        "stop_hit_count": stops,
        "stop_hit_rate": round(stops / n, 4) if n else 0.0,
        "low_mfe_stop_count": low_mfe,
        "low_mfe_stop_rate": round(low_mfe / n, 4) if n else 0.0,
        "trailing_mfe_exit_count": sum(
            1 for t in trades if str(t.get("exit_reason_canonical") or "") == "trailing_mfe_exit"
        ),
        "overlap_replaced_count": sum(
            1 for t in trades if str(t.get("exit_reason_canonical") or "") == "overlap_replaced"
        ),
    }


def _feature_means(trades: Sequence[Mapping[str, Any]]) -> dict[str, Optional[float]]:
    def mean_field(key: str, getter=None) -> Optional[float]:
        vals = []
        for t in trades:
            v = getter(t) if getter else _float(t.get(key))
            if v is not None:
                vals.append(float(v))
        return round(statistics.mean(vals), 4) if vals else None

    return {
        "entry_score": mean_field("entry_score", _entry_score),
        "momentum": mean_field("momentum", _momentum),
        "vwap_dev": mean_field("entry_vwap_dev_pct"),
        "rise_5min": mean_field("entry_rise_5min_pct"),
        "peak_mfe": mean_field("peak_mfe_pct"),
        "peak_mae": mean_field("peak_mae_pct"),
        "hold_sec": mean_field("hold_sec", lambda t: _float(t.get("hold_sec")) or _float(t.get("hold_duration_sec"))),
    }


def distribution_analysis(
    trades: Sequence[Mapping[str, Any]],
    *,
    label: str,
) -> dict[str, Any]:
    pnls = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in trades]
    sym_pnl: dict[str, float] = defaultdict(float)
    sym_cnt: Counter[str] = Counter()
    time_pnl: dict[str, float] = defaultdict(float)
    time_cnt: Counter[str] = Counter()
    rank_pnl: dict[str, float] = defaultdict(float)
    rank_cnt: Counter[str] = Counter()
    for t in trades:
        sym = str(t.get("symbol") or "")
        pnl = float(_float(t.get("pnl_yen_100")) or 0.0)
        sym_pnl[sym] += pnl
        sym_cnt[sym] += 1
        tb = _time_bucket(t)
        time_pnl[tb] += pnl
        time_cnt[tb] += 1
        rb = str(t.get("dynamic40_rank_bucket") or _dynamic40_rank(t) or "unknown")
        rank_pnl[rb] += pnl
        rank_cnt[rb] += 1

    total_pnl = sum(pnls) or 0.0
    top_sym = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)[:3] if sym_pnl else []
    top_sym_share = round(top_sym[0][1] / total_pnl, 4) if top_sym and total_pnl else None
    if top_sym_share is not None and total_pnl < 0 and top_sym[0][1] < 0:
        top_sym_share = round(abs(top_sym[0][1]) / abs(total_pnl), 4)

    return {
        "label": label,
        "trade_count": len(trades),
        "top_symbols_by_pnl": [{"symbol": s, "pnl_yen_100": round(p, 2), "count": sym_cnt[s]} for s, p in top_sym],
        "top_symbol_pnl_share": top_sym_share,
        "unique_symbol_count": len(sym_cnt),
        "time_bucket_counts": dict(time_cnt),
        "time_bucket_pnl": {k: round(v, 2) for k, v in time_pnl.items()},
        "rank_bucket_counts": dict(rank_cnt),
        "rank_bucket_pnl": {k: round(v, 2) for k, v in rank_pnl.items()},
        "board_tier_counts": dict(Counter(_board_tier(t) for t in trades)),
    }


def build_daily_robustness(
    cap2_trades: Sequence[Mapping[str, Any]],
    cap3_additional: Sequence[Mapping[str, Any]],
    cap3_all: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    def daily_map(ts: Sequence[Mapping[str, Any]]) -> dict[str, float]:
        out: dict[str, float] = defaultdict(float)
        for t in ts:
            day = str(t.get("day_key") or _day_from_ts(str(t.get("entry_time") or "")) or "")
            if day:
                out[day] += float(_float(t.get("pnl_yen_100")) or 0.0)
        return dict(out)

    d2 = daily_map(cap2_trades)
    d3 = daily_map(cap3_all)
    d_add = daily_map(cap3_additional)
    days = sorted(set(d2) | set(d3))
    improved = worsened = unchanged = 0
    daily_rows = []
    for day in days:
        p2 = d2.get(day, 0.0)
        p3 = d3.get(day, 0.0)
        padd = d_add.get(day, 0.0)
        if p3 > p2:
            improved += 1
        elif p3 < p2:
            worsened += 1
        else:
            unchanged += 1
        daily_rows.append(
            {
                "day": day,
                "cap2_pnl_yen_100": round(p2, 2),
                "cap3_pnl_yen_100": round(p3, 2),
                "cap3_additional_pnl_yen_100": round(padd, 2),
                "delta_cap3_vs_cap2": round(p3 - p2, 2),
                "cap2_better": p2 > p3,
            }
        )
    return {
        "improved_days_cap3_vs_cap2": improved,
        "worsened_days_cap3_vs_cap2": worsened,
        "unchanged_days": unchanged,
        "cap2_better_days": sum(1 for r in daily_rows if r.get("cap2_better")),
        "additional_cohort_negative_days": sum(1 for d, v in d_add.items() if v < 0),
        "daily_rows": daily_rows,
    }


def build_conclusions(
    *,
    cap2_metrics: Mapping[str, Any],
    cap3_add_metrics: Mapping[str, Any],
    cap2_dist: Mapping[str, Any],
    cap3_add_dist: Mapping[str, Any],
    robustness: Mapping[str, Any],
    delta_count: int,
) -> dict[str, Any]:
    add_pnl = float(cap3_add_metrics.get("total_pnl_yen_100") or 0.0)
    add_pf = cap3_add_metrics.get("profit_factor")
    cap2_pf = cap2_metrics.get("profit_factor")
    add_avg = float(cap3_add_metrics.get("avg_pnl_yen_100") or 0.0)
    cap2_avg = float(cap2_metrics.get("avg_pnl_yen_100") or 0.0)

    third_low_quality = add_pnl < 0 or (add_avg < cap2_avg * 0.5)
    degraded_features = []
    f2 = cap2_metrics.get("feature_means") or {}
    f3 = cap3_add_metrics.get("feature_means") or {}
    for feat in ("entry_score", "momentum", "vwap_dev", "rise_5min", "peak_mfe"):
        v2 = f2.get(feat)
        v3 = f3.get(feat)
        if v2 is not None and v3 is not None and v3 < v2:
            degraded_features.append(feat)

    if float(cap3_add_metrics.get("stop_hit_rate") or 0) > float(cap2_metrics.get("stop_hit_rate") or 0):
        degraded_features.append("stop_hit_rate")
    if float(cap3_add_metrics.get("low_mfe_stop_rate") or 0) > float(cap2_metrics.get("low_mfe_stop_rate") or 0):
        degraded_features.append("low_mfe_stop_rate")

    sym_share = cap3_add_dist.get("top_symbol_pnl_share")
    symbol_dependent = sym_share is not None and sym_share >= 0.35 and cap3_add_dist.get("unique_symbol_count", 0) <= 8

    time_counts = cap3_add_dist.get("time_bucket_counts") or {}
    time_pnl = cap3_add_dist.get("time_bucket_pnl") or {}
    worst_time = min(time_pnl, key=time_pnl.get, default="") if time_pnl else ""
    time_dependent = bool(worst_time) and abs(time_pnl.get(worst_time, 0.0)) > abs(add_pnl) * 0.4

    rank_pnl = cap3_add_dist.get("rank_bucket_pnl") or {}
    rank_dependent = any(
        str(k).startswith("rank_31") or str(k).startswith("rank_21")
        for k, v in rank_pnl.items()
        if v < 0 and abs(v) > abs(add_pnl) * 0.25
    )

    cap2_robust = (
        int(robustness.get("cap2_better_days") or 0) >= int(robustness.get("worsened_days_cap3_vs_cap2") or 0)
        and int(robustness.get("improved_days_cap3_vs_cap2") or 0) <= int(robustness.get("cap2_better_days") or 0)
    )

    return {
        "third_position_is_low_quality": third_low_quality,
        "delta_trade_count": delta_count,
        "delta_total_pnl_yen_100": add_pnl,
        "degraded_features": degraded_features,
        "symbol_dependent": symbol_dependent,
        "time_dependent": time_dependent,
        "dynamic40_rank_dependent": rank_dependent,
        "cap2_superiority_robust": cap2_robust,
        "cap3_is_optimal": not third_low_quality and add_pnl > 0,
        "recommended_cap": CAP2 if third_low_quality or add_pnl < 0 else CAP3,
        "pf_comparison": {
            "cap2_accepted": cap2_pf,
            "cap3_additional": add_pf,
            "pf_maintained_on_additions": (
                add_pf is not None and cap2_pf is not None and float(add_pf) >= float(cap2_pf) * 0.9
            ),
        },
    }


def build_report(summary: Mapping[str, Any]) -> str:
    conc = summary.get("conclusions") or {}
    cmp_rows = summary.get("cohort_comparison") or {}
    cap2 = cmp_rows.get("cap2_accepted") or {}
    add = cmp_rows.get("cap3_additional") or {}
    lines = [
        "# Phase386 Third Position Quality Review",
        "",
        f"**期間:** {summary.get('population', {}).get('min_day')}–{summary.get('population', {}).get('max_day')}",
        f"**条件:** 200万円 / 信用2倍 / 100株固定（Phase385準拠）",
        f"**差分:** CAP3採用 & CAP2 reject = **{conc.get('delta_trade_count')}件**",
        "",
        "## 必須回答",
        "",
        f"- **3つ目ポジションは低品質か:** {'はい' if conc.get('third_position_is_low_quality') else 'いいえ'}",
        f"- **悪化特徴:** {', '.join(conc.get('degraded_features') or []) or 'なし'}",
        f"- **特定銘柄依存:** {'はい' if conc.get('symbol_dependent') else 'いいえ'}",
        f"- **特定時間帯依存:** {'はい' if conc.get('time_dependent') else 'いいえ'}",
        f"- **Dynamic40 rank依存:** {'はい' if conc.get('dynamic40_rank_dependent') else 'いいえ'}",
        f"- **CAP2優位はロバストか:** {'はい' if conc.get('cap2_superiority_robust') else 'いいえ'}",
        f"- **推奨CAP:** {conc.get('recommended_cap')}",
        "",
        "## コホート比較",
        "",
        "| 指標 | CAP2採用 | CAP3追加 |",
        "|---|---:|---:|",
        f"| trade_count | {cap2.get('trade_count')} | {add.get('trade_count')} |",
        f"| total_pnl | {cap2.get('total_pnl_yen_100')} | {add.get('total_pnl_yen_100')} |",
        f"| PF | {cap2.get('profit_factor')} | {add.get('profit_factor')} |",
        f"| win_rate | {cap2.get('win_rate')} | {add.get('win_rate')} |",
        f"| avg_pnl | {cap2.get('avg_pnl_yen_100')} | {add.get('avg_pnl_yen_100')} |",
        f"| avg_mfe | {cap2.get('avg_mfe_pct')} | {add.get('avg_mfe_pct')} |",
        f"| stop_hit率 | {cap2.get('stop_hit_rate')} | {add.get('stop_hit_rate')} |",
        f"| low_mfe_stop率 | {cap2.get('low_mfe_stop_rate')} | {add.get('low_mfe_stop_rate')} |",
        "",
        "## CAP=3比 増分",
        "",
        f"- 追加約定: {conc.get('delta_trade_count')}件",
        f"- 追加PnL: {conc.get('delta_total_pnl_yen_100')}円",
        "",
        "## 日別ロバスト性",
        "",
        f"- CAP2優位日: {summary.get('robustness', {}).get('cap2_better_days')}",
        f"- CAP3優位日: {summary.get('robustness', {}).get('improved_days_cap3_vs_cap2')}",
        f"- CAP3劣位日: {summary.get('robustness', {}).get('worsened_days_cap3_vs_cap2')}",
        "",
    ]
    return "\n".join(lines) + "\n"


@dataclass
class Phase386ThirdPositionQualityReview:
    reports_dir: Path
    min_day: str = DEFAULT_MIN_DAY
    max_day: Optional[str] = DEFAULT_MAX_DAY
    initial_equity: float = DEFAULT_INITIAL_EQUITY
    equity_floor: float = DEFAULT_EQUITY_FLOOR
    all_trades: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase386_third_position_quality_summary.json",
            "trades": self.reports_dir / "phase386_third_position_quality_trades.csv",
            "report": self.reports_dir / "phase386_third_position_quality_report.md",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.all_trades.extend(result.get("all_trades") or result.get("valid_trades") or [])

    def run(
        self,
        *,
        wall_runtime_sec: float = 0.0,
        sessions_discovered: int = 0,
        sessions_evaluated: int = 0,
    ) -> dict[str, Any]:
        trades, duplicate_removed = dedupe_trades(self.all_trades)
        trades = sorted(
            trades,
            key=lambda t: (_parse_ts(t.get("entry_time")) or datetime.min.replace(tzinfo=JST), str(t.get("symbol") or "")),
        )
        lookup = trade_lookup(trades)

        sim2 = simulate_cap_acceptance(
            trades, cap=CAP2, initial_equity=self.initial_equity, equity_floor=self.equity_floor
        )
        sim3 = simulate_cap_acceptance(
            trades, cap=CAP3, initial_equity=self.initial_equity, equity_floor=self.equity_floor
        )
        cap2_keys = sim2["accepted_keys"]
        cap3_keys = sim3["accepted_keys"]
        additional_keys = cap3_keys - cap2_keys

        cap2_trades = [lookup[k] for k in sorted(cap2_keys) if k in lookup]
        cap3_additional_trades = [lookup[k] for k in sorted(additional_keys) if k in lookup]
        cap3_all_trades = [lookup[k] for k in sorted(cap3_keys) if k in lookup]

        cap2_reject = sim2["rejected_entries"]
        trade_rows = []
        for k in sorted(cap2_keys):
            if k in lookup:
                trade_rows.append(enrich_trade_row(lookup[k], cohort="cap2_accepted"))
        for k in sorted(additional_keys):
            if k in lookup:
                trade_rows.append(
                    enrich_trade_row(
                        lookup[k],
                        cohort="cap3_additional",
                        cap2_reject_reason=cap2_reject.get(k, "max_concurrent_positions"),
                    )
                )

        cap2_metrics = cohort_metrics(cap2_trades)
        cap3_add_metrics = cohort_metrics(cap3_additional_trades)
        cap2_metrics["feature_means"] = _feature_means(cap2_trades)
        cap3_add_metrics["feature_means"] = _feature_means(cap3_additional_trades)

        cap2_dist = distribution_analysis(cap2_trades, label="cap2_accepted")
        cap3_add_dist = distribution_analysis(cap3_additional_trades, label="cap3_additional")
        robustness = build_daily_robustness(cap2_trades, cap3_additional_trades, cap3_all_trades)

        conclusions = build_conclusions(
            cap2_metrics=cap2_metrics,
            cap3_add_metrics=cap3_add_metrics,
            cap2_dist=cap2_dist,
            cap3_add_dist=cap3_add_dist,
            robustness=robustness,
            delta_count=len(additional_keys),
        )

        cap3_base = cohort_metrics(cap3_all_trades)
        delta_vs_cap3 = {
            "additional_accepted_count": len(additional_keys),
            "additional_pnl_yen_100": cap3_add_metrics["total_pnl_yen_100"],
            "additional_loss_pnl_yen_100": round(
                sum(min(0.0, float(_float(t.get("pnl_yen_100")) or 0.0)) for t in cap3_additional_trades), 2
            ),
            "additional_gain_pnl_yen_100": round(
                sum(max(0.0, float(_float(t.get("pnl_yen_100")) or 0.0)) for t in cap3_additional_trades), 2
            ),
            "cap2_only_pnl_yen_100": cap2_metrics["total_pnl_yen_100"],
            "cap3_all_pnl_yen_100": cap3_base["total_pnl_yen_100"],
            "pnl_delta_cap3_minus_cap2": round(
                float(cap3_base["total_pnl_yen_100"]) - float(cap2_metrics["total_pnl_yen_100"]), 2
            ),
            "cap2_reject_reasons_for_additional": dict(
                Counter(cap2_reject.get(k, "unknown") for k in additional_keys)
            ),
        }

        public_trade_rows = [{k: v for k, v in r.items() if not str(k).startswith("_")} for r in trade_rows]

        return {
            "phase": 386,
            "title": "Third position quality review",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "stack_id": PRIMARY_STACK,
            "initial_equity": self.initial_equity,
            "leverage_limit": 2.0,
            "cap2": CAP2,
            "cap3": CAP3,
            "population": {
                "min_day": self.min_day,
                "max_day": self.max_day,
                "sessions_discovered": sessions_discovered,
                "sessions_evaluated": sessions_evaluated,
                "input_trade_count_raw": len(self.all_trades),
                "duplicate_session_trades_removed": duplicate_removed,
                "input_trade_count": len(trades),
            },
            "simulation_acceptance": {
                "cap2_accepted": sim2["accepted_trade_count"],
                "cap2_rejected": sim2["rejected_trade_count"],
                "cap3_accepted": sim3["accepted_trade_count"],
                "cap3_rejected": sim3["rejected_trade_count"],
                "cap3_additional_count": len(additional_keys),
            },
            "cohort_comparison": {
                "cap2_accepted": cap2_metrics,
                "cap3_additional": cap3_add_metrics,
            },
            "delta_vs_cap3": delta_vs_cap3,
            "distribution": {
                "cap2_accepted": cap2_dist,
                "cap3_additional": cap3_add_dist,
            },
            "robustness": {k: v for k, v in robustness.items() if k != "daily_rows"},
            "conclusions": conclusions,
            "trade_rows": public_trade_rows,
            "wall_runtime_sec": round(wall_runtime_sec, 2),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        payload = {k: v for k, v in result.items() if k not in ("trade_rows",)}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_csv(paths["trades"], list(result.get("trade_rows") or []), TRADE_CSV_FIELDS)
        paths["report"].write_text(build_report(payload), encoding="utf-8")
        return paths


__all__ = ["Phase386ThirdPositionQualityReview", "simulate_cap_acceptance", "cohort_metrics"]
