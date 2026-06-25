"""
Phase511 — Entry / Exit cross battle (research only).

Decomposes PBv2 advantage into Entry vs Exit contributions.
No PBv2 modification. No adoption.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import (
    POLICY_BASELINE,
    PolicySpec,
    simulate_time_decay_exit,
)
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase463_trend_pullback_population_tournament import _fill_close_proxy_shadows
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase488_current_runtime_replay import (
    _filter_period,
    _filter_replay_pool_safe,
    _trade_summary_rows,
)
from research.phase493_global_entry_failure_audit import (
    PERIOD_END,
    PERIOD_START,
    _enrich_trade_row,
    _is_loser,
    _medians_from_losers,
    _replay_with_extra_block,
)
from research.phase502_classic_indicator_guard_replay import _build_feature_environment
from research.phase507_classic_indicators import (
    Bar1m,
    BarIndicatorRow,
    _in_trading_window,
    compute_bar_indicators,
    ticks_to_1m_bars,
)
from research.phase507_classic_strategy_battle import (
    BASELINE_STRATEGY_ID,
    ENTRY_COOLDOWN_SEC,
    MIN_BARS_WARMUP,
    _day_rows,
    _rank_summaries,
    _run_baseline_runtime,
    _simulate_precomputed_cap,
    _universe_symbols,
    state_trade_logs,
)
from research.phase510_classic_system_battle import (
    _find_exit_system,
    _strategy_metrics_safe,
    scan_system_symbol_day,
)
from research.phase443_full_runtime_combined_capital_sim import _chronological_pnls_from_log
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE511_VERDICT = "phase511_entry_exit_cross_battle_done"
MAX_WORKERS_CAP = 4

E_PB = "E_PB"
E_TREND = "E_TREND"
E_MOMENTUM = "E_MOMENTUM"
X_PB = "X_PB"
X_TREND = "X_TREND"
X_MOMENTUM = "X_MOMENTUM"

X_TREND_ID = "ATR_OR_EMA5_OR_STOP"
X_MOMENTUM_ID = "ATR_OR_RSI_OR_STOP"
E_TREND_ID = "SYS_A"
E_MOMENTUM_ID = "SYS_B"

CROSS_COMBOS: list[dict[str, str]] = [
    {
        "combo_id": "CROSS_BASELINE",
        "label": "PBv2 Entry + PBv2 Exit",
        "entry_id": E_PB,
        "exit_id": X_PB,
    },
    {
        "combo_id": "CROSS_PB_TREND_EXIT",
        "label": "PBv2 Entry + Trend Exit",
        "entry_id": E_PB,
        "exit_id": X_TREND,
    },
    {
        "combo_id": "CROSS_PB_MOMENTUM_EXIT",
        "label": "PBv2 Entry + Momentum Exit",
        "entry_id": E_PB,
        "exit_id": X_MOMENTUM,
    },
    {
        "combo_id": "CROSS_TREND_PB_EXIT",
        "label": "Trend Entry + PBv2 Exit",
        "entry_id": E_TREND,
        "exit_id": X_PB,
    },
    {
        "combo_id": "CROSS_MOMENTUM_PB_EXIT",
        "label": "Momentum Entry + PBv2 Exit",
        "entry_id": E_MOMENTUM,
        "exit_id": X_PB,
    },
    {
        "combo_id": "CROSS_TREND_TREND",
        "label": "Trend Entry + Trend Exit",
        "entry_id": E_TREND,
        "exit_id": X_TREND,
    },
    {
        "combo_id": "CROSS_MOMENTUM_MOMENTUM",
        "label": "Momentum Entry + Momentum Exit",
        "entry_id": E_MOMENTUM,
        "exit_id": X_MOMENTUM,
    },
]

SUMMARY_FIELDS = [
    "combo_id",
    "label",
    "entry_id",
    "exit_id",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trades",
    "win_rate",
    "avg_pnl_yen_100",
    "positive_day_count",
    "negative_day_count",
    "best_day_pnl",
    "worst_day_pnl",
    "daily_stability_score",
    "hard_stop_rate",
    "session_end_rate",
    "baseline_diff_pnl",
    "baseline_diff_pf",
    "rank_pnl",
    "rank_pf",
    "rank_stability",
]

DAILY_FIELDS = [
    "combo_id",
    "day",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
]

TRADE_FIELDS = [
    "combo_id",
    "entry_id",
    "exit_id",
    "symbol",
    "day",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "pnl_yen_100",
    "exit_reason",
]

EXIT_BREAKDOWN_FIELDS = [
    "combo_id",
    "exit_reason",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "share_of_trades_pct",
]

_PB_RUNTIME_POLICY = PolicySpec(POLICY_BASELINE, None, None, None, False, False)


def _session_end_epoch(day: str) -> float:
    dt = datetime.strptime(f"{day[:8]} 15:30:00", "%Y%m%d %H:%M:%S").replace(tzinfo=JST)
    return dt.timestamp()


def _epoch_series(
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    sym: str,
    day: str,
) -> list[tuple[float, float]]:
    key = (sym if sym.endswith(".T") else f"{sym}.T", day[:8])
    return [(ts.timestamp(), float(px)) for ts, px in price_idx.get(key, []) if px > 0]


def _bar_at_entry(bars: Sequence[Bar1m], entry_time: datetime) -> Optional[int]:
    best_i: Optional[int] = None
    best_delta = timedelta(days=999)
    for i, bar in enumerate(bars):
        delta = abs(bar.ts - entry_time)
        if delta < best_delta:
            best_delta = delta
            best_i = i
    if best_i is None or best_delta > timedelta(minutes=2):
        return None
    return best_i


def _float(v: Any) -> float:
    try:
        if v is None or v == "":
            return 0.0
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _exit_id_to_internal(exit_id: str) -> str:
    if exit_id == X_TREND:
        return X_TREND_ID
    if exit_id == X_MOMENTUM:
        return X_MOMENTUM_ID
    raise ValueError(exit_id)


def _entry_id_to_internal(entry_id: str) -> str:
    if entry_id == E_TREND:
        return E_TREND_ID
    if entry_id == E_MOMENTUM:
        return E_MOMENTUM_ID
    raise ValueError(entry_id)


def _build_pbv2_entry_pool(repo_root: Path) -> list[dict[str, Any]]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    replay_pool, _ = _load_replay_pool(reports)
    replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END)
    _ensure_enriched(replay_pool, price_idx=price_idx)

    from research.phase473_trend_entry_architecture import pass_pbv2

    pre_rows = [
        _enrich_trade_row({"trade": t, "day": str(t.get("day") or "")[:8], "pnl_yen": 0, "exit_reason": ""})
        for t in replay_pool
        if pass_pbv2(t)
    ]
    medians = _medians_from_losers([r for r in pre_rows if _is_loser(r)])
    feature_row, _ = _build_feature_environment(replay_pool, price_idx=price_idx, medians=medians)

    def guard_c_block(trade: Mapping[str, Any]) -> bool:
        row = feature_row(trade)
        rsi = row.get("rsi_over80")
        return bool(row.get("late_chase_cluster")) and (
            rsi == 1.0 or rsi is True or (isinstance(rsi, (int, float)) and float(rsi) >= 1.0)
        )

    pool: list[dict[str, Any]] = []
    for trade in replay_pool:
        if not pass_pbv2(trade):
            continue
        if guard_c_block(trade):
            continue
        pool.append(dict(trade))
    return pool


def _apply_classical_exit_pb_entry(
    trade: Mapping[str, Any],
    *,
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]],
    exit_id: str,
) -> Optional[dict[str, Any]]:
    sym = str(trade.get("symbol") or "")
    if not sym.endswith(".T"):
        sym = f"{sym}.T"
    day = str(trade.get("day") or "")[:8]
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    if ent is None:
        return None
    cached = bar_cache.get((sym, day))
    if not cached:
        return None
    bars, ind_rows = cached
    i = _bar_at_entry(bars, ent)
    if i is None or i < MIN_BARS_WARMUP:
        return None
    entry_px = _float(trade.get("entry_price")) or bars[i].close
    exit_i, exit_reason = _find_exit_system(
        bars, ind_rows, i, exit_rule_id=_exit_id_to_internal(exit_id), entry_px=entry_px
    )
    exit_bar = bars[exit_i]
    return {
        "symbol": sym,
        "day": day,
        "entry_time": ent.isoformat(),
        "exit_time": exit_bar.ts.isoformat(),
        "entry_price": entry_px,
        "exit_price": exit_bar.close,
        "pnl_yen": round((exit_bar.close - entry_px) * 100.0, 2),
        "exit_reason": exit_reason,
    }


def _apply_pb_exit_classical_entry(
    candidate: Mapping[str, Any],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    imb_pct: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    sym = str(candidate.get("symbol") or "")
    day = str(candidate.get("day") or "")[:8]
    ent = _parse_ts(str(candidate.get("entry_time") or ""))
    if ent is None:
        return None
    entry_px = _float(candidate.get("entry_price"))
    if entry_px <= 0:
        return None
    series = _epoch_series(price_idx, sym, day)
    if not series:
        return None
    sim = simulate_time_decay_exit(
        series,
        entry_ts=ent.timestamp(),
        entry_price=entry_px,
        session_end_ts=_session_end_epoch(day),
        imb_pct=imb_pct,
        policy=_PB_RUNTIME_POLICY,
    )
    ex_ts = float(sim.get("shadow_exit_ts") or ent.timestamp())
    exit_dt = datetime.fromtimestamp(ex_ts, tz=JST)
    return {
        **dict(candidate),
        "exit_time": exit_dt.isoformat(),
        "exit_price": sim.get("shadow_exit_price"),
        "pnl_yen": sim.get("shadow_pnl_yen_100"),
        "exit_reason": str(sim.get("shadow_exit_reason") or ""),
    }


def _scan_classical_day(
    *,
    symbol: str,
    day: str,
    bars: Sequence[Bar1m],
    ind_rows: Sequence[BarIndicatorRow],
    entry_id: str,
    exit_id: str,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> list[dict[str, Any]]:
    internal_entry = _entry_id_to_internal(entry_id)
    if exit_id == X_PB:
        raw = scan_system_symbol_day(
            symbol=symbol,
            day=day,
            bars=bars,
            ind_rows=ind_rows,
            entry_rule_id=internal_entry,
            exit_rule_id=X_TREND_ID,
        )
        out: list[dict[str, Any]] = []
        for c in raw:
            ent_only = {k: c[k] for k in ("symbol", "day", "entry_time", "entry_price") if k in c}
            applied = _apply_pb_exit_classical_entry(ent_only, price_idx=price_idx)
            if applied:
                out.append(applied)
        return out
    internal_exit = _exit_id_to_internal(exit_id)
    return scan_system_symbol_day(
        symbol=symbol,
        day=day,
        bars=bars,
        ind_rows=ind_rows,
        entry_rule_id=internal_entry,
        exit_rule_id=internal_exit,
    )


def _combo_metrics(
    state: Any,
    *,
    combo: Mapping[str, str],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    base = _strategy_metrics_safe(
        state,
        strategy_id=combo["combo_id"],
        entry_rule_id=combo["entry_id"],
        exit_rule_id=combo["exit_id"],
        baseline=baseline,
    )
    reasons = [str(log.get("exit_reason") or "").lower() for log in state.trade_log]
    n = len(reasons)
    hard = sum(1 for r in reasons if r in ("hard_stop", "stop_hit"))
    sess = sum(1 for r in reasons if r in ("session_end", "session_close"))
    row = {
        "combo_id": combo["combo_id"],
        "label": combo["label"],
        "entry_id": combo["entry_id"],
        "exit_id": combo["exit_id"],
        **base,
        "hard_stop_rate": round(hard / n, 4) if n else 0.0,
        "session_end_rate": round(sess / n, 4) if n else 0.0,
    }
    return row


def _exit_breakdown(state: Any, combo_id: str) -> list[dict[str, Any]]:
    buckets: dict[str, list[float]] = defaultdict(list)
    for log in state.trade_log:
        reason = str(log.get("exit_reason") or "unknown")
        buckets[reason].append(_float(log.get("pnl_yen")))
    total_n = sum(len(v) for v in buckets.values())
    rows: list[dict[str, Any]] = []
    for reason, pnls in sorted(buckets.items()):
        wins = sum(1 for p in pnls if p > 0)
        rows.append(
            {
                "combo_id": combo_id,
                "exit_reason": reason,
                "trade_count": len(pnls),
                "total_pnl_yen_100": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
                "share_of_trades_pct": round(len(pnls) / total_n * 100.0, 2) if total_n else 0.0,
            }
        )
    return rows


def _mandatory_answers(
    summary_rows: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
) -> dict[str, Any]:
    b_pnl = float(baseline["total_pnl_yen_100"])
    b_pf = float(baseline["profit_factor"] or 0)
    by_combo = {r["combo_id"]: r for r in summary_rows}
    base_row = by_combo.get("CROSS_BASELINE", baseline)

    pb_trend_x = by_combo.get("CROSS_PB_TREND_EXIT", {})
    pb_mom_x = by_combo.get("CROSS_PB_MOMENTUM_EXIT", {})
    trend_pb_x = by_combo.get("CROSS_TREND_PB_EXIT", {})
    mom_pb_x = by_combo.get("CROSS_MOMENTUM_PB_EXIT", {})

    beat_baseline = [r["combo_id"] for r in summary_rows if float(r.get("total_pnl_yen_100") or 0) > b_pnl]
    classical_entries = [r for r in summary_rows if r.get("entry_id") in (E_TREND, E_MOMENTUM)]
    classical_exits_only = [r for r in summary_rows if r.get("exit_id") in (X_TREND, X_MOMENTUM) and r.get("entry_id") == E_PB]
    classical_exits_all = [r for r in summary_rows if r.get("exit_id") in (X_TREND, X_MOMENTUM)]

    entry_only_scores = {
        "pb_entry_baseline": float(base_row.get("total_pnl_yen_100") or 0),
        "pb_entry_trend_exit": float(pb_trend_x.get("total_pnl_yen_100") or 0),
        "pb_entry_mom_exit": float(pb_mom_x.get("total_pnl_yen_100") or 0),
    }
    exit_only_scores = {
        "trend_entry_pb_exit": float(trend_pb_x.get("total_pnl_yen_100") or 0),
        "mom_entry_pb_exit": float(mom_pb_x.get("total_pnl_yen_100") or 0),
    }

    entry_strong = entry_only_scores["pb_entry_baseline"] > max(
        float(trend_pb_x.get("total_pnl_yen_100") or 0),
        float(mom_pb_x.get("total_pnl_yen_100") or 0),
    )
    exit_strong = float(base_row.get("total_pnl_yen_100") or 0) > max(
        float(pb_trend_x.get("total_pnl_yen_100") or 0),
        float(pb_mom_x.get("total_pnl_yen_100") or 0),
    )

    if entry_strong and exit_strong:
        pb_edge = "both"
    elif entry_strong:
        pb_edge = "entry"
    elif exit_strong:
        pb_edge = "exit"
    else:
        pb_edge = "neither_dominant"

    mom_pb_viable = float(mom_pb_x.get("total_pnl_yen_100") or 0) > 0 and float(mom_pb_x.get("profit_factor") or 0) > 1.0
    trend_pb_viable = float(trend_pb_x.get("total_pnl_yen_100") or 0) > 0 and float(trend_pb_x.get("profit_factor") or 0) > 1.0

    next_deep = max(
        summary_rows,
        key=lambda r: float(r.get("total_pnl_yen_100") or 0) if r.get("combo_id") != "CROSS_BASELINE" else -1e18,
    )

    return {
        "1_pbv2_entry_excellent": entry_strong,
        "2_pbv2_exit_excellent": exit_strong,
        "3_momentum_entry_pb_exit_viable": mom_pb_viable,
        "4_trend_entry_pb_exit_viable": trend_pb_viable,
        "5_pbv2_edge_source": pb_edge,
        "6_classical_entry_beats_pbv2": [
            r["combo_id"] for r in classical_entries if float(r.get("total_pnl_yen_100") or 0) > b_pnl
        ],
        "7_classical_exit_beats_pbv2": [
            r["combo_id"] for r in classical_exits_all if float(r.get("total_pnl_yen_100") or 0) > b_pnl
        ],
        "8_next_deep_dive": next_deep.get("combo_id"),
        "beat_baseline_pnl": beat_baseline,
        "entry_decomposition": entry_only_scores,
        "exit_decomposition": exit_only_scores,
        "momentum_pb_exit_metrics": mom_pb_x,
        "trend_pb_exit_metrics": trend_pb_x,
    }


@dataclass
class Phase511Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(self.repo_root)
        max_workers = min(max(1, self.max_workers), MAX_WORKERS_CAP)

        baseline_state, baseline_met_raw = _run_baseline_runtime(self.repo_root)
        baseline_met = _combo_metrics(
            baseline_state,
            combo=CROSS_COMBOS[0],
            baseline=baseline_met_raw,
        )

        price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
        replay_pool, _ = _load_replay_pool(reports)
        replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END)
        universe = _universe_symbols(replay_pool)
        days = sorted({str(t.get("day") or "")[:8] for t in replay_pool if t.get("day")})

        bar_cache: dict[tuple[str, str], tuple[list[Bar1m], list[BarIndicatorRow]]] = {}
        for sym in universe:
            for day in days:
                series = price_idx.get((sym, day), [])
                if not series:
                    continue
                bars = ticks_to_1m_bars(series)
                if len(bars) < MIN_BARS_WARMUP + 5:
                    continue
                bar_cache[(sym, day)] = (bars, compute_bar_indicators(bars))

        pb_pool = _build_pbv2_entry_pool(self.repo_root)
        pb_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in pb_pool:
            pb_by_day[str(t.get("day") or "")[:8]].append(t)

        jobs: list[tuple[str, str, str, str]] = []
        for combo in CROSS_COMBOS[1:]:
            for day in days:
                jobs.append((combo["combo_id"], combo["entry_id"], combo["exit_id"], day))

        combo_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)

        def _job(job: tuple[str, str, str, str]) -> tuple[str, list[dict[str, Any]]]:
            cid, eid, xid, day = job
            local: list[dict[str, Any]] = []
            if eid == E_PB:
                for trade in pb_by_day.get(day, []):
                    if xid == X_PB:
                        continue
                    tr = _apply_classical_exit_pb_entry(trade, bar_cache=bar_cache, exit_id=xid)
                    if tr:
                        tr["combo_id"] = cid
                        tr["entry_id"] = eid
                        tr["exit_id"] = xid
                        local.append(tr)
            else:
                for sym in universe:
                    cached = bar_cache.get((sym, day))
                    if not cached:
                        continue
                    bars, ind_rows = cached
                    for tr in _scan_classical_day(
                        symbol=sym,
                        day=day,
                        bars=bars,
                        ind_rows=ind_rows,
                        entry_id=eid,
                        exit_id=xid,
                        price_idx=price_idx,
                    ):
                        tr["combo_id"] = cid
                        tr["entry_id"] = eid
                        tr["exit_id"] = xid
                        local.append(tr)
            return cid, local

        if self.parallel and jobs:
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                for fut in as_completed(ex.submit(_job, j) for j in jobs):
                    cid, cands = fut.result()
                    combo_candidates[cid].extend(cands)
        else:
            for j in jobs:
                cid, cands = _job(j)
                combo_candidates[cid].extend(cands)

        summary_rows: list[dict[str, Any]] = [baseline_met]
        daily_rows: list[dict[str, Any]] = _day_rows(baseline_state, "CROSS_BASELINE")
        for r in daily_rows:
            r["combo_id"] = "CROSS_BASELINE"
        trade_rows: list[dict[str, Any]] = []
        for log in _trade_summary_rows(baseline_state):
            trade_rows.append(
                {
                    "combo_id": "CROSS_BASELINE",
                    "entry_id": E_PB,
                    "exit_id": X_PB,
                    "symbol": str(log.get("symbol") or "").replace(".T", ""),
                    "day": str(log.get("day") or "")[:8],
                    "entry_time": log.get("entry_time"),
                    "exit_time": log.get("exit_time"),
                    "entry_price": "",
                    "exit_price": "",
                    "pnl_yen_100": log.get("pnl_yen"),
                    "exit_reason": log.get("exit_reason"),
                }
            )

        exit_breakdown_rows: list[dict[str, Any]] = _exit_breakdown(baseline_state, "CROSS_BASELINE")
        combo_states: dict[str, Any] = {"CROSS_BASELINE": baseline_state}

        combo_by_id = {c["combo_id"]: c for c in CROSS_COMBOS}
        for cid, cands in combo_candidates.items():
            combo = combo_by_id[cid]
            st = _simulate_precomputed_cap(cands, mode=f"phase511_{cid}")
            combo_states[cid] = st
            met = _combo_metrics(st, combo=combo, baseline=baseline_met)
            summary_rows.append(met)
            for dr in _day_rows(st, cid):
                dr["combo_id"] = cid
                daily_rows.append(dr)
            spec = {"strategy_id": cid, "entry_rule_id": combo["entry_id"], "exit_rule_id": combo["exit_id"]}
            for log in state_trade_logs(st, spec):
                trade_rows.append(
                    {
                        "combo_id": cid,
                        "entry_id": combo["entry_id"],
                        "exit_id": combo["exit_id"],
                        "symbol": log.get("symbol"),
                        "day": log.get("day"),
                        "entry_time": log.get("entry_time"),
                        "exit_time": log.get("exit_time"),
                        "entry_price": log.get("entry_price"),
                        "exit_price": log.get("exit_price"),
                        "pnl_yen_100": log.get("pnl_yen_100"),
                        "exit_reason": log.get("exit_reason"),
                    }
                )
            exit_breakdown_rows.extend(_exit_breakdown(st, cid))

        _rank_summaries(summary_rows)
        mandatory = _mandatory_answers(summary_rows, baseline_met)
        return {
            "verdict": PHASE511_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "summary_rows": summary_rows,
            "daily_rows": daily_rows,
            "trade_rows": trade_rows,
            "exit_breakdown": exit_breakdown_rows,
            "mandatory_answers": mandatory,
            "combos": CROSS_COMBOS,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase511_cross_battle.csv",
            "daily": reports / "phase511_cross_battle_daily.csv",
            "trades": reports / "phase511_cross_battle_trades.csv",
            "report": reports / "phase511_cross_battle_report.json",
            "docs": kabu / "docs" / "operations" / "phase511_entry_exit_cross_battle.md",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary_rows") or []))
        _write_csv(paths["daily"], DAILY_FIELDS, list(result.get("daily_rows") or []))
        _write_csv(paths["trades"], TRADE_FIELDS, list(result.get("trade_rows") or []))
        paths["report"].write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    lines = [
        "# Phase511 — Entry / Exit Cross Battle",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        "",
        "## Mandatory answers",
        "",
        f"1. PBv2 Entry excellent: **{ma.get('1_pbv2_entry_excellent')}**",
        f"2. PBv2 Exit excellent: **{ma.get('2_pbv2_exit_excellent')}**",
        f"3. Momentum Entry + PB Exit viable: **{ma.get('3_momentum_entry_pb_exit_viable')}**",
        f"4. Trend Entry + PB Exit viable: **{ma.get('4_trend_entry_pb_exit_viable')}**",
        f"5. PBv2 edge source: **{ma.get('5_pbv2_edge_source')}**",
        f"6. Classical entry beats PBv2: {ma.get('6_classical_entry_beats_pbv2')}",
        f"7. Classical exit beats PBv2: {ma.get('7_classical_exit_beats_pbv2')}",
        f"8. Next deep dive: **{ma.get('8_next_deep_dive')}**",
        "",
        "## Summary",
        "",
        "| Combo | PnL | PF | maxDD | Trades | hard_stop% | session_end% |",
        "|-------|-----|-----|-------|--------|------------|----------------|",
    ]
    for r in result.get("summary_rows") or []:
        lines.append(
            f"| {r.get('combo_id')} | {r.get('total_pnl_yen_100')} | {r.get('profit_factor')} | "
            f"{r.get('max_drawdown_yen_100')} | {r.get('trades')} | {r.get('hard_stop_rate')} | "
            f"{r.get('session_end_rate')} |"
        )
    return "\n".join(lines) + "\n"
