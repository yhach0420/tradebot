"""
Phase522 — stop_low_mfe / re-entry failure audit + overlay edge attribution.

Research only. No Runtime / Entry / Exit / adoption changes.
Parallel: strategy × day (max 4 workers).
"""

from __future__ import annotations

import heapq
import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key, _trade_pnl_yen
from research.phase400_holding_time_audit import normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase443_full_runtime_combined_capital_sim import CAP, CapacityReplayState, LEVERAGE, STOP_POLICY
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase463_trend_pullback_population_tournament import _momentum_score
from research.phase464_pre_gate_archetype_audit import _vwap_above_ratio, _vwap_dev
from research.phase465b_trend_gate_redesign import _day_high_distance
from research.phase480_pbv2_loss_cluster_audit import _mfe_mae_to_exit
from research.phase481_stop_low_mfe_reduction_tournament import _build_trade_rows
from research.phase493_global_entry_failure_audit import PERIOD_END, PERIOD_START
from research.phase507_classic_strategy_battle import (
    BASELINE_STRATEGY_ID,
    HARD_STOP_PCT,
    _run_baseline_runtime,
    _simulate_precomputed_cap,
    _universe_symbols,
)
from research.phase509_t15_t13_signal_audit import _bar_at_entry, _build_bar_cache
from research.phase510_classic_system_battle import _strategy_metrics_safe
from research.phase515b_day_high_breakout_dependency_audit import SYMBOL_6976
from research.phase516_pbv2_best_classical_overlay import (
    OVERLAY_DEFS,
    _merge_or_candidates,
    _pbv2_precomputed_candidates,
    _prepare_runtime_env,
    _scan_overlay_day,
    _trade_rows_from_state,
)
from research.phase518_day_high_winner_loser_separation import (
    _build_micro_lookup,
    _extract_entry_features,
)
from research.phase520_g3_g4_forward_shadow import SPREAD_MEDIAN_PHASE519, _passes_g3_g4
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from research.phase271_leverage_attribution_and_robustness import build_spec
from small_paper.discord_message_builder import STOP_LOW_MFE_THRESHOLD_PCT
from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

PHASE522_VERDICT = "phase522_stop_low_mfe_reentry_overlay_edge_audit_done"
MAX_WORKERS = 4
SYMBOL_5074 = "5074"
OVERLAY_STRATEGIES = ("O_R003_OR", "G3_G4")
GUARD_VARIANTS = ("A_baseline", "B_score_plus1", "C_break_stop_price", "D_cooldown_5m", "E_low_fail_bounce")

STOP_SUMMARY_FIELDS = [
    "metric",
    "value",
    "pct_of_total_loss",
    "notes",
]

REENTRY_AUDIT_FIELDS = [
    "cohort",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "avg_mfe_pct",
    "avg_mae_pct",
    "avg_pnl_yen_100",
]

CONSEC_STOP_FIELDS = [
    "symbol",
    "day",
    "chain_count",
    "total_loss_yen_100",
    "pattern",
    "first_entry_time",
    "last_exit_time",
]

REENTRY_SF_FIELDS = [
    "feature",
    "success_mean",
    "failure_mean",
    "success_median",
    "failure_median",
    "delta_mean",
    "success_n",
    "failure_n",
]

GUARD_SHADOW_FIELDS = [
    "guard_id",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "trade_count",
    "stop_hit_count",
    "stop_low_mfe_count",
    "loss_reduction_yen_100",
    "lost_profit_yen_100",
    "net_improvement_yen_100",
]

OVERLAP_FIELDS = [
    "bucket",
    "strategy_id",
    "trades",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "avg_pnl_yen_100",
    "pct_of_strategy_pnl",
]

RISING_CAPTURE_FIELDS = [
    "day",
    "universe",
    "top_n",
    "baseline_only",
    "overlay_only",
    "both",
    "neither",
    "baseline_capture_rate",
    "overlay_capture_rate",
    "both_capture_rate",
]

TOP_EXCLUSION_FIELDS = [
    "strategy_id",
    "exclusion_type",
    "remaining_pnl_yen_100",
    "remaining_pf",
    "remaining_max_dd_yen_100",
    "remaining_trades",
    "beats_baseline_pnl",
]

SYMBOL_DEP_FIELDS = [
    "strategy_id",
    "exclusion_type",
    "remaining_pnl_yen_100",
    "remaining_pf",
    "remaining_max_dd_yen_100",
    "remaining_trades",
    "beats_baseline_pnl",
]

EDGE_CLASS_FIELDS = [
    "strategy_id",
    "classification",
    "overlay_only_pnl_share_pct",
    "overlap_pnl_share_pct",
    "top10_exclusion_survives",
    "rising_capture_lead",
    "notes",
]


def _is_stop_hit(row: Mapping[str, Any]) -> bool:
    return normalize_exit_reason(str(row.get("exit_reason") or "")) == "stop_hit"


def _is_stop_low_mfe(row: Mapping[str, Any]) -> bool:
    return _is_stop_hit(row) and _float(row.get("mfe_pct")) < STOP_LOW_MFE_THRESHOLD_PCT


def _metrics_from_trades(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_float(t.get("pnl_yen_100")) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    mfes = [_float(t.get("mfe_pct")) for t in trades if t.get("mfe_pct") is not None]
    maes = [_float(t.get("mae_pct")) for t in trades if t.get("mae_pct") is not None]
    return {
        "trade_count": len(pnls),
        "total_pnl_yen_100": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
        "avg_pnl_yen_100": round(statistics.mean(pnls), 2) if pnls else 0.0,
        "avg_mfe_pct": round(statistics.mean(mfes), 4) if mfes else 0.0,
        "avg_mae_pct": round(statistics.mean(maes), 4) if maes else 0.0,
    }


def _chron_pnls(trades: Sequence[Mapping[str, Any]]) -> list[float]:
    ordered = sorted(
        trades,
        key=lambda t: _parse_ts(str(t.get("exit_time") or t.get("entry_time") or ""))
        or datetime.min.replace(tzinfo=JST),
    )
    return [_float(t.get("pnl_yen_100")) for t in ordered]


def _max_dd(trades: Sequence[Mapping[str, Any]]) -> float:
    return round(_max_drawdown_yen(_chron_pnls(trades)) if trades else 0.0, 2)


def _total_loss(trades: Sequence[Mapping[str, Any]]) -> float:
    return round(sum(p for p in (_float(t.get("pnl_yen_100")) for t in trades) if p < 0), 2)


def _baseline_trade_rows(state: CapacityReplayState, trade_by_key: Mapping[str, Mapping[str, Any]], price_idx: Mapping) -> list[dict[str, Any]]:
    raw = _build_trade_rows(state, trade_by_key=trade_by_key, price_idx=price_idx)
    rows: list[dict[str, Any]] = []
    for r in raw:
        rows.append(
            {
                "strategy_id": BASELINE_STRATEGY_ID,
                "symbol": r["symbol"],
                "day": r["day"],
                "entry_time": r["entry_time"],
                "exit_time": r.get("exit_time"),
                "pnl_yen_100": _float(r.get("pnl_yen")),
                "exit_reason": r.get("exit_reason"),
                "mfe_pct": r.get("mfe_pct"),
                "mae_pct": r.get("mae_pct"),
                "hold_sec": r.get("hold_sec"),
                "position_key": r.get("position_key"),
                "trade": r.get("trade") or r,
            }
        )
    return rows


def _audit_stop_low_mfe(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    stop_hits = [t for t in trades if _is_stop_hit(t)]
    total_loss = abs(_total_loss(trades))
    slm = [t for t in stop_hits if _is_stop_low_mfe(t)]
    slm_loss = abs(round(sum(_float(t.get("pnl_yen_100")) for t in slm), 2))

    def _mfe_bucket(threshold: float) -> dict[str, Any]:
        subset = [t for t in stop_hits if _float(t.get("mfe_pct")) <= threshold]
        loss = abs(round(sum(_float(t.get("pnl_yen_100")) for t in subset), 2))
        return {
            "metric": f"stop_mfe_le_{threshold}",
            "value": len(subset),
            "pct_of_total_loss": round(loss / total_loss * 100.0, 2) if total_loss else 0.0,
            "notes": f"loss_yen_100={loss}",
        }

    rows = [
        {"metric": "stop_hit_count", "value": len(stop_hits), "pct_of_total_loss": 0.0, "notes": ""},
        {"metric": "stop_low_mfe_count", "value": len(slm), "pct_of_total_loss": round(slm_loss / total_loss * 100.0, 2) if total_loss else 0.0, "notes": f"threshold={STOP_LOW_MFE_THRESHOLD_PCT}%"},
        {"metric": "stop_low_mfe_loss_yen_100", "value": slm_loss, "pct_of_total_loss": round(slm_loss / total_loss * 100.0, 2) if total_loss else 0.0, "notes": ""},
        {"metric": "total_loss_yen_100", "value": total_loss, "pct_of_total_loss": 100.0, "notes": ""},
        _mfe_bucket(0.0),
        _mfe_bucket(0.2),
        _mfe_bucket(0.5),
        _mfe_bucket(1.0),
    ]
    return rows


def _sorted_symbol_day_trades(trades: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        sym = str(t.get("symbol") or "").replace(".T", "")
        day = str(t.get("day") or "")[:8]
        by[(sym, day)].append(dict(t))
    for key in by:
        by[key].sort(key=lambda r: _parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST))
    return by


def _audit_stop_reentry(trades: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by = _sorted_symbol_day_trades(trades)
    reentry_after_stop: list[dict[str, Any]] = []
    no_reentry_stops: list[dict[str, Any]] = []
    for (_sym, _day), seq in by.items():
        for i, cur in enumerate(seq):
            if i == 0:
                continue
            prev = seq[i - 1]
            if not _is_stop_hit(prev):
                continue
            reentry_after_stop.append({**cur, "prev_stop_exit_time": prev.get("exit_time"), "prev_stop_pnl": prev.get("pnl_yen_100")})
        for i, cur in enumerate(seq):
            if not _is_stop_hit(cur):
                continue
            if i + 1 < len(seq):
                continue
            no_reentry_stops.append(cur)

    re_met = _metrics_from_trades(reentry_after_stop)
    no_met = _metrics_from_trades(no_reentry_stops)
    rows = [
        {"cohort": "stop_then_reentry", **re_met},
        {"cohort": "stop_no_reentry", **no_met},
    ]
    detail = [
        {
            "symbol": t.get("symbol"),
            "day": t.get("day"),
            "entry_time": t.get("entry_time"),
            "pnl_yen_100": t.get("pnl_yen_100"),
            "prev_stop_exit_time": t.get("prev_stop_exit_time"),
            "prev_stop_pnl": t.get("prev_stop_pnl"),
            "mfe_pct": t.get("mfe_pct"),
            "mae_pct": t.get("mae_pct"),
        }
        for t in reentry_after_stop
    ]
    return rows, detail


def _audit_consecutive_stops(trades: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by = _sorted_symbol_day_trades(trades)
    chains: list[dict[str, Any]] = []
    pattern_5074 = 0
    total_loss = abs(_total_loss(trades))

    for (sym, day), seq in by.items():
        i = 0
        while i < len(seq):
            if not _is_stop_hit(seq[i]):
                i += 1
                continue
            if i + 2 < len(seq) and _is_stop_hit(seq[i + 2]):
                loss = round(
                    _float(seq[i].get("pnl_yen_100")) + _float(seq[i + 2].get("pnl_yen_100")),
                    2,
                )
                chains.append(
                    {
                        "symbol": sym,
                        "day": day,
                        "chain_count": 2,
                        "total_loss_yen_100": loss,
                        "pattern": "stop_reentry_stop",
                        "first_entry_time": seq[i].get("entry_time"),
                        "last_exit_time": seq[i + 2].get("exit_time"),
                    }
                )
                if sym == SYMBOL_5074:
                    pattern_5074 += 1
                i += 3
                continue
            i += 1

        stop_streak = 0
        for t in seq:
            if _is_stop_hit(t):
                stop_streak += 1
                if stop_streak >= 3:
                    if sym == SYMBOL_5074:
                        pattern_5074 += 1
            else:
                stop_streak = 0

    sym_rank: dict[str, float] = defaultdict(float)
    for c in chains:
        sym_rank[str(c["symbol"])] += _float(c["total_loss_yen_100"])
    ranked = sorted(sym_rank.items(), key=lambda x: x[1])
    chain_loss = abs(round(sum(_float(c["total_loss_yen_100"]) for c in chains), 2))
    summary = {
        "chain_count": len(chains),
        "chain_total_loss_yen_100": chain_loss,
        "pct_of_total_loss": round(chain_loss / total_loss * 100.0, 2) if total_loss else 0.0,
        "symbol_5074_stop_stop_stop_count": pattern_5074,
        "top_symbols": ranked[:10],
    }
    return chains, summary


def _rx(trade: Mapping[str, Any], key: str) -> Optional[float]:
    if key in ("r5", "r10", "r15"):
        return _float(trade.get(key)) if trade.get(key) is not None else None
    if key == "board_imbalance":
        return _float(trade.get("board_imbalance"))
    if key == "momentum_score":
        return _momentum_score(trade)
    if key == "day_high_distance":
        return _day_high_distance(trade)
    if key == "vwap_dev":
        return _vwap_dev(trade)
    if key == "vwap_above_ratio":
        return _vwap_above_ratio(trade)
    if key == "entry_score_v2":
        fields = compute_entry_expectancy_score_fields(trade=trade)
        v = fields.get("entry_expectancy_score_v2")
        return float(v) if v is not None else None
    return _float(trade.get(key)) if trade.get(key) is not None else None


def _min_price_between(
    sym: str,
    day: str,
    start: datetime,
    end: datetime,
    price_idx: Mapping[tuple[str, str], list],
) -> Optional[float]:
    sym_t = sym if sym.endswith(".T") else f"{sym}.T"
    series = price_idx.get((sym_t, day), [])
    lows: list[float] = []
    for ts, px in series:
        if isinstance(ts, datetime):
            dt = ts
        else:
            dt = _parse_ts(str(ts))
        if dt is None or dt < start or dt > end:
            continue
        lows.append(float(px))
    return min(lows) if lows else None


def _reentry_success_failure(
    trades: Sequence[Mapping[str, Any]],
    *,
    bar_cache: Mapping,
    price_idx: Mapping,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by = _sorted_symbol_day_trades(trades)
    pairs: list[dict[str, Any]] = []
    for (_sym, _day), seq in by.items():
        for i in range(1, len(seq)):
            prev, cur = seq[i - 1], seq[i]
            if not _is_stop_hit(prev):
                continue
            src = cur.get("trade") or cur
            prev_src = prev.get("trade") or prev
            ent = _parse_ts(str(cur.get("entry_time") or ""))
            prev_ex = _parse_ts(str(prev.get("exit_time") or ""))
            entry_px = _float(src.get("entry_price") or cur.get("entry_price"))
            prev_entry = _float(prev_src.get("entry_price") or prev.get("entry_price"))
            prev_exit = _float(prev_src.get("exit_price") or prev.get("exit_price"))
            stop_px = round(prev_entry * (1.0 - HARD_STOP_PCT / 100.0), 4) if prev_entry > 0 else 0.0
            min_after = _min_price_between(str(cur.get("symbol") or ""), str(cur.get("day") or "")[:8], prev_ex, ent, price_idx) if prev_ex and ent else None
            sym_t = f"{str(cur.get('symbol') or '').replace('.T', '')}.T"
            day = str(cur.get("day") or "")[:8]
            ind_vals: dict[str, Any] = {}
            cached = bar_cache.get((sym_t, day))
            if cached and ent:
                bars, ind_rows = cached
                bi = _bar_at_entry(bars, ind_rows, ent)
                if bi is not None:
                    ind_vals = dict(ind_rows[bi].values)
            row = {
                **dict(cur),
                "success": _float(cur.get("pnl_yen_100")) > 0,
                "broke_prev_stop_price": entry_px > stop_px if stop_px > 0 else None,
                "broke_prev_entry_price": entry_px > prev_entry if prev_entry > 0 else None,
                "new_low_after_stop": min_after < prev_exit if min_after is not None and prev_exit > 0 else None,
                "low_fail_bounce": (
                    min_after is not None
                    and prev_exit > 0
                    and min_after >= prev_exit * 0.998
                    and entry_px > stop_px
                ),
                "volume_increase_ratio": None,
                "RSI14": ind_vals.get("RSI14"),
                "EMA20": ind_vals.get("EMA20"),
                "VWAP": ind_vals.get("VWAP"),
                "ADX": ind_vals.get("ADX"),
                "board_imbalance": _rx(src, "board_imbalance"),
                "momentum_score": _rx(src, "momentum_score"),
                "day_high_distance": _rx(src, "day_high_distance"),
                "r5": _rx(src, "r5"),
                "r10": _rx(src, "r10"),
                "r15": _rx(src, "r15"),
            }
            pairs.append(row)

    features = [
        "broke_prev_stop_price",
        "broke_prev_entry_price",
        "new_low_after_stop",
        "low_fail_bounce",
        "RSI14",
        "ADX",
        "board_imbalance",
        "momentum_score",
        "day_high_distance",
        "r5",
        "r10",
        "r15",
        "entry_score_v2",
    ]
    success = [p for p in pairs if p.get("success")]
    failure = [p for p in pairs if not p.get("success")]
    cmp_rows: list[dict[str, Any]] = []
    for feat in features:
        if feat == "entry_score_v2":
            s_vals = [_rx(p.get("trade") or p, "entry_score_v2") for p in success]
            f_vals = [_rx(p.get("trade") or p, "entry_score_v2") for p in failure]
        elif feat in ("broke_prev_stop_price", "broke_prev_entry_price", "new_low_after_stop", "low_fail_bounce"):
            s_vals = [1.0 if p.get(feat) else 0.0 for p in success if p.get(feat) is not None]
            f_vals = [1.0 if p.get(feat) else 0.0 for p in failure if p.get(feat) is not None]
        else:
            s_vals = [_float(p.get(feat)) for p in success if p.get(feat) is not None]
            f_vals = [_float(p.get(feat)) for p in failure if p.get(feat) is not None]
        s_vals = [v for v in s_vals if v is not None]
        f_vals = [v for v in f_vals if v is not None]
        if not s_vals and not f_vals:
            continue
        cmp_rows.append(
            {
                "feature": feat,
                "success_mean": round(statistics.mean(s_vals), 4) if s_vals else 0.0,
                "failure_mean": round(statistics.mean(f_vals), 4) if f_vals else 0.0,
                "success_median": round(statistics.median(s_vals), 4) if s_vals else 0.0,
                "failure_median": round(statistics.median(f_vals), 4) if f_vals else 0.0,
                "delta_mean": round((statistics.mean(s_vals) if s_vals else 0) - (statistics.mean(f_vals) if f_vals else 0), 4),
                "success_n": len(s_vals),
                "failure_n": len(f_vals),
            }
        )
    return cmp_rows, pairs


@dataclass
class _ReentryGuardTracker:
    guard_id: str
    price_idx: Mapping
    last_stop: dict[str, dict[str, Any]] = field(default_factory=dict)

    def on_stop_close(self, trade: Mapping[str, Any], log_row: Mapping[str, Any]) -> None:
        if not _is_stop_hit(log_row):
            return
        sym = str(trade.get("symbol") or "").replace(".T", "")
        src = trade
        entry_px = _float(src.get("entry_price"))
        score_fields = compute_entry_expectancy_score_fields(trade=src)
        self.last_stop[sym] = {
            "day": str(trade.get("day") or "")[:8],
            "exit_time": log_row.get("exit_time"),
            "exit_dt": _parse_ts(str(log_row.get("exit_time") or "")),
            "entry_price": entry_px,
            "exit_price": _float(src.get("exit_price") or log_row.get("exit_price")),
            "stop_price": round(entry_px * (1.0 - HARD_STOP_PCT / 100.0), 4) if entry_px > 0 else 0.0,
            "entry_score": int(score_fields.get("entry_expectancy_score_v2") or 0),
        }

    def block_entry(self, trade: Mapping[str, Any]) -> bool:
        if self.guard_id == "A_baseline":
            return False
        sym = str(trade.get("symbol") or "").replace(".T", "")
        rec = self.last_stop.get(sym)
        if not rec:
            return False
        if str(trade.get("day") or "")[:8] != str(rec.get("day") or ""):
            return False
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if ent is None or rec.get("exit_dt") is None:
            return False
        entry_px = _float(trade.get("entry_price"))
        if self.guard_id == "B_score_plus1":
            score = int(compute_entry_expectancy_score_fields(trade=trade).get("entry_expectancy_score_v2") or 0)
            return score < int(rec.get("entry_score") or 0) + 1
        if self.guard_id == "C_break_stop_price":
            return entry_px <= _float(rec.get("stop_price"))
        if self.guard_id == "D_cooldown_5m":
            return (ent - rec["exit_dt"]).total_seconds() < 300
        if self.guard_id == "E_low_fail_bounce":
            stop_px = _float(rec.get("stop_price"))
            exit_px = _float(rec.get("exit_price"))
            if entry_px <= stop_px:
                return True
            min_after = _min_price_between(sym, str(trade.get("day") or "")[:8], rec["exit_dt"], ent, self.price_idx)
            if min_after is None or exit_px <= 0:
                return True
            low_fail = min_after >= exit_px * 0.998
            return not low_fail
        return False


def _simulate_guard_cap(
    candidates: Sequence[Mapping[str, Any]],
    *,
    guard_id: str,
    price_idx: Mapping,
) -> CapacityReplayState:
    tracker = _ReentryGuardTracker(guard_id=guard_id, price_idx=price_idx)
    spec = build_spec(leverage=LEVERAGE, cap=CAP, stop_policy=STOP_POLICY)
    state = CapacityReplayState(
        scenario_id=guard_id,
        max_concurrent_positions=CAP,
        spec=spec,
        initial_equity=1_500_000.0,
        equity_floor=1_500_000.0 * 0.5,
        pnl_resolver=lambda *a, **k: 0.0,
        exit_mode=f"{guard_id}_guard",
        shadow_by_key={},
        entry_block_fn=tracker.block_entry,
        baseline_accepted_keys=set(),
    )
    entry_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    for i, trade in enumerate(candidates):
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if ent is None:
            continue
        heapq.heappush(entry_heap, (ent, 0, f"e{i:05d}", dict(trade)))
    exit_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    while entry_heap or exit_heap:
        next_entry = entry_heap[0] if entry_heap else None
        next_exit = exit_heap[0] if exit_heap else None
        if next_exit is not None and (next_entry is None or next_exit[0] <= next_entry[0]):
            ex_dt, _, key, trade = heapq.heappop(exit_heap)
            ts = ex_dt.isoformat()
            day = str(trade.get("day") or "")[:8]
            pnl = float(_trade_pnl_yen(trade, shares=100) or trade.get("pnl_yen") or 0)
            reason = str(trade.get("exit_reason") or "")
            log = {"exit_time": ts, "exit_reason": reason, "pnl_yen": pnl}
            tracker.on_stop_close(trade, log)
            state.close_position_at(trade, ts=ts, day=day, exit_reason=reason, pnl_yen=pnl)
            continue
        ent_dt, _, _, trade = heapq.heappop(entry_heap)
        ts = ent_dt.isoformat()
        day = str(trade.get("day") or "")[:8]
        if state.try_entry(trade, ts, day):
            ex_dt = _parse_ts(str(trade.get("exit_time") or "")) or ent_dt + timedelta(minutes=5)
            key = _position_key(trade)
            heapq.heappush(exit_heap, (ex_dt, 1, key, trade))
    if state.open_positions:
        state._force_close_all(datetime.now(JST).isoformat(), str(candidates[-1].get("day") or "")[:8] if candidates else "", reason="end_of_period")
    return state


def _guard_shadow_rows(
    baseline_trades: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    price_idx: Mapping,
) -> list[dict[str, Any]]:
    base_pnl = round(sum(_float(t.get("pnl_yen_100")) for t in baseline_trades), 2)
    base_losses = [t for t in baseline_trades if _float(t.get("pnl_yen_100")) < 0]
    base_loss_sum = abs(round(sum(_float(t.get("pnl_yen_100")) for t in base_losses), 2))
    rows: list[dict[str, Any]] = []
    for gid in GUARD_VARIANTS:
        if gid == "A_baseline":
            trades = list(baseline_trades)
        else:
            st = _simulate_guard_cap(candidates, guard_id=gid, price_idx=price_idx)
            trades = _baseline_trade_rows(st, {_position_key(t.get("trade") or t): t.get("trade") or t for t in candidates}, price_idx)
        pnls = [_float(t.get("pnl_yen_100")) for t in trades]
        stop_hits = [t for t in trades if _is_stop_hit(t)]
        slm = [t for t in stop_hits if _is_stop_low_mfe(t)]
        pnl = round(sum(pnls), 2)
        losses = [p for p in pnls if p < 0]
        loss_sum = abs(round(sum(losses), 2))
        loss_reduction = round(base_loss_sum - loss_sum, 2)
        lost_profit = round(max(0.0, pnl - base_pnl) if pnl < base_pnl else 0.0, 2)
        if pnl > base_pnl:
            lost_profit = 0.0
        else:
            lost_profit = round(base_pnl - pnl, 2)
        rows.append(
            {
                "guard_id": gid,
                "total_pnl_yen_100": pnl,
                "profit_factor": _pf(pnls),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
                "trade_count": len(pnls),
                "stop_hit_count": len(stop_hits),
                "stop_low_mfe_count": len(slm),
                "loss_reduction_yen_100": loss_reduction,
                "lost_profit_yen_100": lost_profit,
                "net_improvement_yen_100": round(pnl - base_pnl, 2),
            }
        )
    return rows


def _entry_dt(trade: Mapping[str, Any]) -> Optional[datetime]:
    return _parse_ts(str(trade.get("entry_time") or ""))


def _overlap_buckets(
    baseline: Sequence[Mapping[str, Any]],
    overlay: Sequence[Mapping[str, Any]],
    strategy_id: str,
    window_sec: float,
) -> list[dict[str, Any]]:
    used: set[int] = set()
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    overlay_total = sum(_float(t.get("pnl_yen_100")) for t in overlay)

    for bt in baseline:
        bdt = _entry_dt(bt)
        if bdt is None:
            continue
        matched = False
        for j, ot in enumerate(overlay):
            if j in used:
                continue
            if str(bt.get("symbol")) != str(ot.get("symbol")) or str(bt.get("day"))[:8] != str(ot.get("day"))[:8]:
                continue
            odt = _entry_dt(ot)
            if odt is None:
                continue
            if abs((odt - bdt).total_seconds()) <= window_sec:
                buckets[f"both_pm{int(window_sec)}s"].append(ot)
                used.add(j)
                matched = True
                break
        if not matched:
            buckets["pbv2_only"].append(bt)

    for j, ot in enumerate(overlay):
        if j not in used:
            buckets["overlay_only"].append(ot)

    rows: list[dict[str, Any]] = []
    for bucket, items in buckets.items():
        met = _metrics_from_trades(items)
        total_pnl = met["total_pnl_yen_100"]
        rows.append(
            {
                "bucket": bucket,
                "strategy_id": strategy_id,
                "trades": met["trade_count"],
                "total_pnl_yen_100": total_pnl,
                "profit_factor": met["profit_factor"],
                "win_rate": met["win_rate"],
                "avg_pnl_yen_100": met["avg_pnl_yen_100"],
                "pct_of_strategy_pnl": round(total_pnl / overlay_total * 100.0, 2) if overlay_total else 0.0,
            }
        )
    return rows


def _day_return_rank(price_idx: Mapping, universe: Sequence[str], day: str) -> list[tuple[str, float]]:
    rets: list[tuple[str, float]] = []
    for sym in universe:
        sym_t = sym if sym.endswith(".T") else f"{sym}.T"
        series = price_idx.get((sym_t, day), [])
        if len(series) < 2:
            continue
        o = float(series[0][1])
        c = float(series[-1][1])
        if o <= 0:
            continue
        rets.append((sym_t.replace(".T", ""), round((c - o) / o * 100.0, 4)))
    return sorted(rets, key=lambda x: x[1], reverse=True)


def _rising_capture_rows(
    *,
    baseline: Sequence[Mapping[str, Any]],
    overlay_trades: Mapping[str, Sequence[Mapping[str, Any]]],
    price_idx: Mapping,
    universe: Sequence[str],
    days: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in days:
        ranked = _day_return_rank(price_idx, universe, day)
        for top_n in (10, 20):
            top_syms = {s for s, _ in ranked[:top_n]}
            for sid, trades in overlay_trades.items():
                day_trades = [t for t in trades if str(t.get("day") or "")[:8] == day]
                entered = {str(t.get("symbol") or "").replace(".T", "") for t in day_trades}
                base_day = {str(t.get("symbol") or "").replace(".T", "") for t in baseline if str(t.get("day") or "")[:8] == day}
                b_only = len(top_syms & base_day - entered)
                o_only = len(top_syms & entered - base_day)
                both = len(top_syms & entered & base_day)
                neither = len(top_syms - entered - base_day)
                rows.append(
                    {
                        "day": day,
                        "universe": sid,
                        "top_n": top_n,
                        "baseline_only": b_only,
                        "overlay_only": o_only,
                        "both": both,
                        "neither": neither,
                        "baseline_capture_rate": round(len(top_syms & base_day) / len(top_syms), 4) if top_syms else 0.0,
                        "overlay_capture_rate": round(len(top_syms & entered) / len(top_syms), 4) if top_syms else 0.0,
                        "both_capture_rate": round(both / len(top_syms), 4) if top_syms else 0.0,
                    }
                )
    return rows


def _top_trade_exclusion(
    trades_by_strategy: Mapping[str, Sequence[Mapping[str, Any]]],
    baseline_pnl: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sid, trades in trades_by_strategy.items():
        ranked = sorted(trades, key=lambda t: _float(t.get("pnl_yen_100")), reverse=True)
        for label, n in (("top1_trade", 1), ("top5_trades", 5), ("top10_trades", 10)):
            ex_keys = {_position_key(t) for t in ranked[:n]}
            rem = [t for t in trades if _position_key(t) not in ex_keys]
            pnl = round(sum(_float(t.get("pnl_yen_100")) for t in rem), 2)
            rows.append(
                {
                    "strategy_id": sid,
                    "exclusion_type": label,
                    "remaining_pnl_yen_100": pnl,
                    "remaining_pf": _pf([_float(t.get("pnl_yen_100")) for t in rem]),
                    "remaining_max_dd_yen_100": _max_dd(rem),
                    "remaining_trades": len(rem),
                    "beats_baseline_pnl": pnl > baseline_pnl,
                }
            )
    return rows


def _symbol_dependency_exclusion(
    trades_by_strategy: Mapping[str, Sequence[Mapping[str, Any]]],
    baseline_pnl: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sid, trades in trades_by_strategy.items():
        sym_pnl: dict[str, float] = defaultdict(float)
        for t in trades:
            sym_pnl[str(t.get("symbol") or "").replace(".T", "")] += _float(t.get("pnl_yen_100"))
        sym_rank = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)
        specs = [
            ("top1_symbol", {sym_rank[0][0]} if sym_rank else set()),
            ("top3_symbols", {s for s, _ in sym_rank[:3]}),
            (f"symbol_{SYMBOL_6976}", {SYMBOL_6976}),
        ]
        for label, ex_syms in specs:
            rem = [t for t in trades if str(t.get("symbol") or "").replace(".T", "") not in ex_syms]
            pnl = round(sum(_float(t.get("pnl_yen_100")) for t in rem), 2)
            rows.append(
                {
                    "strategy_id": sid,
                    "exclusion_type": label,
                    "remaining_pnl_yen_100": pnl,
                    "remaining_pf": _pf([_float(t.get("pnl_yen_100")) for t in rem]),
                    "remaining_max_dd_yen_100": _max_dd(rem),
                    "remaining_trades": len(rem),
                    "beats_baseline_pnl": pnl > baseline_pnl,
                }
            )
    return rows


def _classify_edge(
    strategy_id: str,
    overlap_rows: Sequence[Mapping[str, Any]],
    top_excl: Sequence[Mapping[str, Any]],
    rising_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    oo = next((r for r in overlap_rows if r.get("bucket") == "overlay_only" and r.get("strategy_id") == strategy_id), {})
    both = next((r for r in overlap_rows if str(r.get("bucket", "")).startswith("both") and r.get("strategy_id") == strategy_id), {})
    oo_pnl_share = _float(oo.get("pct_of_strategy_pnl"))
    top10_row = next((r for r in top_excl if r.get("strategy_id") == strategy_id and r.get("exclusion_type") == "top10_trades"), {})
    survives = _float(top10_row.get("remaining_pnl_yen_100")) > 0
    rising = [r for r in rising_rows if r.get("universe") == strategy_id and r.get("top_n") == 10]
    o_cap = statistics.mean([_float(r.get("overlay_capture_rate")) for r in rising]) if rising else 0.0
    b_cap = statistics.mean([_float(r.get("baseline_capture_rate")) for r in rising]) if rising else 0.0
    lead = o_cap > b_cap

    if oo_pnl_share >= 60 and survives:
        cls = "C_PBv2_complement_edge"
    elif oo_pnl_share >= 40 and lead:
        cls = "D_PBv2_complement_edge"
    elif _float(both.get("pct_of_strategy_pnl")) >= 50:
        cls = "B_same_edge_different_timing"
    elif not survives:
        cls = "A_concentrated_profit"
    else:
        cls = "C_PBv2_complement_edge" if oo_pnl_share >= 30 else "B_same_edge_different_timing"

    return {
        "strategy_id": strategy_id,
        "classification": cls,
        "overlay_only_pnl_share_pct": oo_pnl_share,
        "overlap_pnl_share_pct": _float(both.get("pct_of_strategy_pnl")),
        "top10_exclusion_survives": survives,
        "rising_capture_lead": lead,
        "notes": f"overlay_capture={round(o_cap,4)} baseline_capture={round(b_cap,4)}",
    }


def _mandatory_answers(
    *,
    stop_rows: Sequence[Mapping[str, Any]],
    reentry_rows: Sequence[Mapping[str, Any]],
    chain_summary: Mapping[str, Any],
    sf_rows: Sequence[Mapping[str, Any]],
    guard_rows: Sequence[Mapping[str, Any]],
    overlap_all: Sequence[Mapping[str, Any]],
    top_excl: Sequence[Mapping[str, Any]],
    edge_rows: Sequence[Mapping[str, Any]],
    baseline_pnl: float,
) -> dict[str, Any]:
    slm_pct = next((r for r in stop_rows if r.get("metric") == "stop_low_mfe_loss_yen_100"), {})
    slm_share = _float(slm_pct.get("pct_of_total_loss"))
    re_pnls = [r for r in reentry_rows if r.get("cohort") == "stop_then_reentry"]
    re_pnl = _float(re_pnls[0].get("total_pnl_yen_100")) if re_pnls else 0.0
    best_guard = max(guard_rows, key=lambda r: _float(r.get("net_improvement_yen_100")), default={})
    shadow_cands = [g for g in guard_rows if g.get("guard_id") != "A_baseline" and _float(g.get("net_improvement_yen_100")) > 0]

    def _top10_beats(sid: str) -> bool:
        row = next((r for r in top_excl if r.get("strategy_id") == sid and r.get("exclusion_type") == "top10_trades"), {})
        return _float(row.get("remaining_pnl_yen_100")) > baseline_pnl

    g3_edge = next((e for e in edge_rows if e.get("strategy_id") == "G3_G4"), {})
    or_edge = next((e for e in edge_rows if e.get("strategy_id") == "O_R003_OR"), {})

    low_fail = next((r for r in sf_rows if r.get("feature") == "low_fail_bounce"), {})
    downtrend_trap = _float(low_fail.get("failure_mean", 0)) > _float(low_fail.get("success_mean", 0))

    return {
        "1_stop_low_mfe_primary_loss_driver": slm_share >= 25.0,
        "1_stop_low_mfe_loss_share_pct": slm_share,
        "2_stop_reentry_profitable": re_pnl > 0,
        "2_stop_reentry_pnl": re_pnl,
        "3_stop_chain_loss_share_pct": chain_summary.get("pct_of_total_loss"),
        "3_stop_chain_count": chain_summary.get("chain_count"),
        "4_symbol_5074_pattern_frequent": int(chain_summary.get("symbol_5074_stop_stop_stop_count") or 0) >= 3,
        "4_symbol_5074_count": chain_summary.get("symbol_5074_stop_stop_stop_count"),
        "5_reentry_success_conditions": [r["feature"] for r in sf_rows if _float(r.get("delta_mean")) > 0][:5],
        "6_reentry_failure_conditions": [r["feature"] for r in sf_rows if _float(r.get("delta_mean")) < 0][:5],
        "7_downtrend_bounce_misread": downtrend_trap,
        "8_reentry_guard_improves": bool(shadow_cands),
        "9_best_guard": best_guard.get("guard_id"),
        "9_best_guard_net_improvement": best_guard.get("net_improvement_yen_100"),
        "10_next_shadow_candidate": best_guard.get("guard_id") if shadow_cands else None,
        "11_g3_g4_separate_edge": str(g3_edge.get("classification", "")).startswith("C") or str(g3_edge.get("classification", "")).startswith("D"),
        "12_o_r003_or_separate_edge": str(or_edge.get("classification", "")).startswith("C") or str(or_edge.get("classification", "")).startswith("D"),
        "13_g3_g4_top10_beats_baseline": _top10_beats("G3_G4"),
        "13_o_r003_or_top10_beats_baseline": _top10_beats("O_R003_OR"),
        "14_g3_g4_rising_capture_lead": g3_edge.get("rising_capture_lead"),
        "14_o_r003_or_rising_capture_lead": or_edge.get("rising_capture_lead"),
        "15_pbv2_g3_g4_coexistence_value": _top10_beats("G3_G4") or _float(g3_edge.get("overlay_only_pnl_share_pct")) >= 30,
        "adopt_not_allowed": True,
    }


@dataclass
class Phase522Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        workers = min(max(1, self.max_workers), MAX_WORKERS)
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(self.repo_root)
        bar_cache, days = _build_bar_cache(self.repo_root)
        replay_pool, runtime_shadows, guard_c_block = _prepare_runtime_env(self.repo_root)
        price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
        universe = _universe_symbols(replay_pool)
        micro_lookup = _build_micro_lookup(replay_pool)

        baseline_state, baseline_met = _run_baseline_runtime(self.repo_root)
        trade_by_key = {_position_key(t): t for t in replay_pool}
        baseline_trades = _baseline_trade_rows(baseline_state, trade_by_key, price_idx)
        baseline_pnl = _float(baseline_met.get("total_pnl_yen_100"))

        stop_summary = _audit_stop_low_mfe(baseline_trades)
        reentry_summary, reentry_detail = _audit_stop_reentry(baseline_trades)
        chain_rows, chain_summary = _audit_consecutive_stops(baseline_trades)
        sf_rows, _sf_pairs = _reentry_success_failure(baseline_trades, bar_cache=bar_cache, price_idx=price_idx)

        pbv2_candidates = _pbv2_precomputed_candidates(replay_pool, runtime_shadows, guard_c_block)
        guard_rows = _guard_shadow_rows(baseline_trades, pbv2_candidates, price_idx)

        overlay_by_strategy: dict[str, list[dict[str, Any]]] = {s: [] for s in OVERLAY_STRATEGIES}
        scan_jobs = [(sid, day) for sid in OVERLAY_STRATEGIES for day in days]

        def _scan_strategy_day(sid: str, day: str) -> tuple[str, list[dict[str, Any]]]:
            overlay = OVERLAY_DEFS["O_R003"]
            raw = _scan_overlay_day(
                overlay,
                day=day,
                universe=universe,
                bar_cache=bar_cache,
                price_idx=price_idx,
            )
            if sid == "G3_G4":
                kept: list[dict[str, Any]] = []
                for trade in raw:
                    feats = _extract_entry_features(trade, bar_cache=bar_cache, micro_lookup=micro_lookup)
                    if _passes_g3_g4(feats):
                        kept.append(trade)
                raw = kept
            return sid, raw

        if self.parallel and scan_jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_scan_strategy_day, sid, day): (sid, day) for sid, day in scan_jobs}
                for fut in as_completed(futs):
                    sid, chunk = fut.result()
                    overlay_by_strategy[sid].extend(chunk)
        else:
            for sid, day in scan_jobs:
                s, chunk = _scan_strategy_day(sid, day)
                overlay_by_strategy[s].extend(chunk)

        trades_by_strategy: dict[str, list[dict[str, Any]]] = {"BASELINE": baseline_trades}
        or_overlay = OVERLAY_DEFS["O_R003"]
        for sid in OVERLAY_STRATEGIES:
            if sid == "O_R003_OR":
                merged = _merge_or_candidates(
                    pbv2_candidates,
                    overlay_by_strategy[sid],
                    bar_cache=bar_cache,
                    overlay=or_overlay,
                    guard_c_block=guard_c_block,
                )
            else:
                merged = _merge_or_candidates(
                    pbv2_candidates,
                    overlay_by_strategy[sid],
                    bar_cache=bar_cache,
                    overlay=or_overlay,
                    guard_c_block=guard_c_block,
                )
            state = _simulate_precomputed_cap(merged, mode=f"phase522_{sid.lower()}")
            rows = _trade_rows_from_state(state, sid)
            enriched: list[dict[str, Any]] = []
            for r in rows:
                pk = str(r.get("position_key") or "")
                src = trade_by_key.get(pk, {})
                exit_ts = str(r.get("exit_time") or "")
                mfe, mae = _mfe_mae_to_exit(src or r, price_idx=price_idx, exit_ts_iso=exit_ts)
                enriched.append({**dict(r), "mfe_pct": mfe, "mae_pct": mae, "trade": src})
            trades_by_strategy[sid] = enriched

        overlap_all: list[dict[str, Any]] = []
        for sid in OVERLAY_STRATEGIES:
            overlap_all.extend(_overlap_buckets(baseline_trades, trades_by_strategy[sid], sid, 60))
            overlap_all.extend(_overlap_buckets(baseline_trades, trades_by_strategy[sid], sid, 300))

        rising_rows = _rising_capture_rows(
            baseline=baseline_trades,
            overlay_trades={k: v for k, v in trades_by_strategy.items() if k != "BASELINE"},
            price_idx=price_idx,
            universe=universe,
            days=days,
        )
        top_excl = _top_trade_exclusion(trades_by_strategy, baseline_pnl)
        sym_dep = _symbol_dependency_exclusion(trades_by_strategy, baseline_pnl)
        edge_rows = [
            _classify_edge(sid, overlap_all, top_excl, rising_rows)
            for sid in OVERLAY_STRATEGIES
        ]

        mandatory = _mandatory_answers(
            stop_rows=stop_summary,
            reentry_rows=reentry_summary,
            chain_summary=chain_summary,
            sf_rows=sf_rows,
            guard_rows=guard_rows,
            overlap_all=overlap_all,
            top_excl=top_excl,
            edge_rows=edge_rows,
            baseline_pnl=baseline_pnl,
        )

        return {
            "verdict": PHASE522_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "parallel_workers": workers,
            "spread_median_frozen_g3_g4": SPREAD_MEDIAN_PHASE519,
            "baseline_metrics": baseline_met,
            "stop_low_mfe_summary": stop_summary,
            "reentry_audit": reentry_summary,
            "reentry_detail": reentry_detail,
            "consecutive_stop": chain_rows,
            "consecutive_stop_summary": chain_summary,
            "reentry_success_failure": sf_rows,
            "reentry_guard_shadow": guard_rows,
            "overlay_overlap": overlap_all,
            "overlay_rising_capture": rising_rows,
            "overlay_top_trade_exclusion": top_excl,
            "overlay_symbol_dependency": sym_dep,
            "overlay_edge_classification": edge_rows,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "stop_summary": reports / "phase522_stop_low_mfe_summary.csv",
            "reentry_audit": reports / "phase522_reentry_audit.csv",
            "consecutive_stop": reports / "phase522_consecutive_stop.csv",
            "reentry_sf": reports / "phase522_reentry_success_failure.csv",
            "guard_shadow": reports / "phase522_reentry_guard_shadow.csv",
            "overlap": reports / "phase522_overlay_overlap.csv",
            "rising_capture": reports / "phase522_overlay_rising_capture.csv",
            "top_exclusion": reports / "phase522_overlay_top_trade_exclusion.csv",
            "symbol_dep": reports / "phase522_overlay_symbol_dependency.csv",
            "edge_class": reports / "phase522_overlay_edge_classification.csv",
            "report": reports / "phase522_report.json",
            "docs": kabu / "docs" / "operations" / "phase522_stop_low_mfe_reentry_overlay_edge_audit.md",
        }
        _write_csv(paths["stop_summary"], STOP_SUMMARY_FIELDS, list(result.get("stop_low_mfe_summary") or []))
        _write_csv(paths["reentry_audit"], REENTRY_AUDIT_FIELDS, list(result.get("reentry_audit") or []))
        _write_csv(paths["consecutive_stop"], CONSEC_STOP_FIELDS, list(result.get("consecutive_stop") or []))
        _write_csv(paths["reentry_sf"], REENTRY_SF_FIELDS, list(result.get("reentry_success_failure") or []))
        _write_csv(paths["guard_shadow"], GUARD_SHADOW_FIELDS, list(result.get("reentry_guard_shadow") or []))
        _write_csv(paths["overlap"], OVERLAP_FIELDS, list(result.get("overlay_overlap") or []))
        _write_csv(paths["rising_capture"], RISING_CAPTURE_FIELDS, list(result.get("overlay_rising_capture") or []))
        _write_csv(paths["top_exclusion"], TOP_EXCLUSION_FIELDS, list(result.get("overlay_top_trade_exclusion") or []))
        _write_csv(paths["symbol_dep"], SYMBOL_DEP_FIELDS, list(result.get("overlay_symbol_dependency") or []))
        _write_csv(paths["edge_class"], EDGE_CLASS_FIELDS, list(result.get("overlay_edge_classification") or []))
        slim = {k: v for k, v in result.items() if k not in ("reentry_detail",)}
        paths["report"].write_text(json.dumps(slim, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    bm = result.get("baseline_metrics") or {}
    lines = [
        "# Phase522 — stop_low_mfe / Re-Entry / Overlay Edge Audit",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        f"**Baseline PnL:** {bm.get('total_pnl_yen_100')}",
        "",
        "## Mandatory answers",
        "",
    ]
    for i in range(1, 16):
        keys = [k for k in ma if k.startswith(f"{i}_")]
        for k in sorted(keys):
            lines.append(f"{i}. {k}: **{ma.get(k)}**")
    lines.append("")
    lines.append("Research only — no Runtime adoption.")
    return "\n".join(lines)
