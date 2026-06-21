"""
Phase481 — PBv2 Stop Low MFE Reduction Tournament (research only).

Part A: stop_low_mfe vs winner feature separation
Part B–C: entry guard tournament + CAP5 replay
Part D: symbol/day attribution
Part E: robustness
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence

from pathlib import Path

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key
from research.phase400_holding_time_audit import normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase443_full_runtime_combined_capital_sim import (
    CapacityReplayState,
    simulate_capacity_replay,
    _stop_rate_from_log,
)
from research.phase446_momentum_score_audit import _decompose_momentum_score
from research.phase451_entry_shape_tournament import (
    DAY_618,
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _chronological_pnls_from_log as _chron_pnls,
    _now_iso,
    _optional_float,
)
from research.phase451b_entry_shape_tournament_mid_high import _board_token
from research.phase463_trend_pullback_population_tournament import (
    _board_bucket,
    _fill_close_proxy_shadows,
    _filter_replay_pool,
    _momentum_score,
)
from research.phase464_pre_gate_archetype_audit import _vwap_above_ratio, _vwap_dev
from research.phase465b_trend_gate_redesign import (
    _cohens_d,
    _concentration,
    _day_high_distance,
    _high_update_age,
    _mi_median_split,
)
from research.phase467_trend_exit_audit import _prepare_forward_context_price_idx
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase480_pbv2_loss_cluster_audit import _assign_cluster, _mfe_mae_to_exit
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

FOCUS_SYMBOLS = ("6976", "4062", "6920", "3441", "6492", "7256", "7600")
MFE_STOP_LOW = 0.5
PERCENTILE_CANDIDATES = (0.25, 0.33, 0.40)

FEATURE_AUDIT_FIELDS = [
    "feature",
    "cohort",
    "n",
    "mean",
    "median",
    "missing_rate",
    "cohens_d_vs_winner",
    "cohens_d_vs_non_stop_loser",
    "mutual_information_vs_winner",
    "rank_by_abs_cohens_d",
]

GUARD_TOURNAMENT_FIELDS = [
    "guard_id",
    "label",
    "conditions",
    "threshold_summary",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_rate",
    "stop_low_mfe_count",
    "stop_low_mfe_pnl_yen",
    "delta_pnl_vs_baseline",
    "delta_pf_vs_baseline",
    "delta_maxdd_vs_baseline",
    "delta_stop_low_mfe_count",
    "delta_stop_low_mfe_pnl",
    "blocked_winners",
    "blocked_losers",
    "blocked_stop_low_mfe",
    "blocked_pnl_yen",
    "rank_by_pnl",
]

REPLAY_ATTR_FIELDS = [
    "guard_id",
    "symbol",
    "day",
    "accepted_count",
    "total_pnl_yen",
    "stop_low_mfe_count",
    "stop_low_mfe_pnl_yen",
    "delta_pnl_vs_baseline",
]

ROBUSTNESS_FIELDS = [
    "test",
    "guard_id",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_low_mfe_count",
    "delta_pnl_vs_full",
    "top_day_share",
    "top_symbol_share",
]


def _rx(trade: Mapping[str, Any], key: str) -> Optional[float]:
    if key == "momentum_continuation_score":
        return _float(trade.get("momentum_continuation_score"))
    if key == "price_mom":
        return _decompose_momentum_score(trade).get("price_mom_component")
    if key == "vwap_part":
        return _decompose_momentum_score(trade).get("vwap_part_component")
    if key == "mfe_proxy":
        return _decompose_momentum_score(trade).get("mfe_proxy_component")
    if key == "board_imbalance":
        return _float(trade.get("entry_order_book_imbalance"))
    if key == "board_bucket":
        return None
    if key == "r5":
        return _optional_float(trade.get("return_5min_pct")) or _optional_float(trade.get("entry_rise_5min_pct"))
    if key == "r10":
        return _optional_float(trade.get("return_10min_pct")) or _optional_float(trade.get("entry_rise_10min_pct"))
    if key == "r15":
        return _optional_float(trade.get("return_15min_pct")) or _optional_float(trade.get("entry_rise_15min_pct"))
    if key == "r30":
        return _optional_float(trade.get("return_30min_pct")) or _optional_float(trade.get("entry_rise_30min_pct"))
    if key == "vwap_dev_pct":
        return _vwap_dev(trade)
    if key == "vwap_above_ratio":
        return _vwap_above_ratio(trade)
    if key == "consecutive_above_ticks":
        return _float(trade.get("consecutive_above_ticks"))
    if key == "day_high_distance":
        return _day_high_distance(trade)
    if key == "high_update_age":
        return _high_update_age(trade)
    if key == "vwap_structure_score":
        return _float(trade.get("vwap_structure_score"))
    if key == "trading_value_rate":
        return _float(trade.get("trading_value_rate"))
    if key == "volume_rate":
        return _float(trade.get("tick_rate_60s")) or _float(trade.get("volume_rate"))
    if key == "hold_sec":
        return _float(trade.get("hold_sec"))
    if key == "spread_bps":
        return _float(trade.get("spread_bps"))
    return _float(trade.get(key))


NUMERIC_FEATURES = (
    "momentum_continuation_score",
    "price_mom",
    "vwap_part",
    "mfe_proxy",
    "board_imbalance",
    "r5",
    "r10",
    "r15",
    "r30",
    "vwap_dev_pct",
    "vwap_above_ratio",
    "consecutive_above_ticks",
    "day_high_distance",
    "high_update_age",
    "vwap_structure_score",
    "trading_value_rate",
    "volume_rate",
    "hold_sec",
    "mfe_pct",
    "mae_pct",
)

ENTRY_GUARD_FEATURES = (
    "momentum_continuation_score",
    "vwap_part",
    "board_imbalance",
    "r5",
    "r10",
    "r15",
    "r30",
    "vwap_dev_pct",
    "vwap_above_ratio",
    "consecutive_above_ticks",
    "day_high_distance",
    "high_update_age",
    "vwap_structure_score",
)


def _is_stop_low_mfe(row: Mapping[str, Any]) -> bool:
    cid, _ = _assign_cluster(row)
    return cid == "A"


def _build_trade_rows(
    state: CapacityReplayState,
    *,
    trade_by_key: Mapping[str, Mapping[str, Any]],
    price_idx: Mapping[tuple[str, str], list],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for log_row in state.trade_log:
        tr = log_row.get("trade") or log_row
        key = _position_key(tr)
        src = trade_by_key.get(key) or tr
        exit_ts = str(log_row.get("exit_time") or "")
        mfe, mae = _mfe_mae_to_exit(src, price_idx=price_idx, exit_ts_iso=exit_ts)
        row = {
            "position_key": key,
            "symbol": str(tr.get("symbol") or "").replace(".T", ""),
            "day": str(tr.get("day") or "")[:8],
            "entry_time": tr.get("entry_time"),
            "pnl_yen": float(log_row.get("pnl_yen") or 0),
            "exit_reason": normalize_exit_reason(str(log_row.get("exit_reason") or "")),
            "hold_sec": float(log_row.get("hold_sec") or 0),
            "mfe_pct": mfe,
            "mae_pct": mae,
            "board_bucket": _board_bucket(src),
            "trade": src,
        }
        row["is_stop_low_mfe"] = _is_stop_low_mfe(row)
        rows.append(row)
    return rows


def _cohort_label(row: Mapping[str, Any]) -> str:
    if row.get("is_stop_low_mfe"):
        return "stop_low_mfe"
    if float(row.get("pnl_yen") or 0) > 0:
        return "winner"
    return "non_stop_loser"


def _feature_audit(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    slm = [r for r in rows if _cohort_label(r) == "stop_low_mfe"]
    win = [r for r in rows if _cohort_label(r) == "winner"]
    nsl = [r for r in rows if _cohort_label(r) == "non_stop_loser"]
    out: list[dict[str, Any]] = []
    ranking: list[dict[str, Any]] = []

    for feat in NUMERIC_FEATURES:
        for cohort_name, bucket in (
            ("stop_low_mfe", slm),
            ("winner", win),
            ("non_stop_loser", nsl),
        ):
            vals: list[float] = []
            miss = 0
            for r in bucket:
                tr = r.get("trade") or r
                if feat in ("mfe_pct", "mae_pct", "hold_sec"):
                    v = r.get(feat) if feat != "hold_sec" else r.get("hold_sec")
                else:
                    v = _rx(tr, feat)
                if v is None:
                    miss += 1
                else:
                    vals.append(float(v))
            out.append(
                {
                    "feature": feat,
                    "cohort": cohort_name,
                    "n": len(bucket),
                    "mean": round(statistics.mean(vals), 6) if vals else None,
                    "median": round(statistics.median(vals), 6) if vals else None,
                    "missing_rate": round(miss / len(bucket), 4) if bucket else 0.0,
                    "cohens_d_vs_winner": None,
                    "cohens_d_vs_non_stop_loser": None,
                    "mutual_information_vs_winner": None,
                    "rank_by_abs_cohens_d": None,
                }
            )

        slm_vals = []
        win_vals = []
        nsl_vals = []
        for r in slm:
            tr = r.get("trade") or r
            v = r.get(feat) if feat in ("mfe_pct", "mae_pct", "hold_sec") else _rx(tr, feat)
            if v is not None:
                slm_vals.append(float(v))
        for r in win:
            tr = r.get("trade") or r
            v = r.get(feat) if feat in ("mfe_pct", "mae_pct", "hold_sec") else _rx(tr, feat)
            if v is not None:
                win_vals.append(float(v))
        for r in nsl:
            tr = r.get("trade") or r
            v = r.get(feat) if feat in ("mfe_pct", "mae_pct", "hold_sec") else _rx(tr, feat)
            if v is not None:
                nsl_vals.append(float(v))

        d_win = _cohens_d(slm_vals, win_vals)
        d_nsl = _cohens_d(slm_vals, nsl_vals)
        mi = _mi_median_split(win_vals, slm_vals) if win_vals and slm_vals else None
        for row in out:
            if row["feature"] == feat and row["cohort"] == "stop_low_mfe":
                row["cohens_d_vs_winner"] = d_win
                row["cohens_d_vs_non_stop_loser"] = d_nsl
                row["mutual_information_vs_winner"] = mi
        if d_win is not None:
            ranking.append({"feature": feat, "cohens_d": d_win, "mi": mi or 0.0})

    ranking.sort(key=lambda r: abs(float(r.get("cohens_d") or 0)), reverse=True)
    rank_map = {r["feature"]: i + 1 for i, r in enumerate(ranking)}
    for row in out:
        if row["cohort"] == "stop_low_mfe":
            row["rank_by_abs_cohens_d"] = rank_map.get(row["feature"])
    return out, ranking


def _percentile(vals: Sequence[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[idx]


def _pool_values(pool: Sequence[Mapping[str, Any]], key: str) -> list[float]:
    out: list[float] = []
    for t in pool:
        v = _rx(t, key)
        if v is not None:
            out.append(float(v))
    return out


def _best_threshold(
    key: str, pool: Sequence[Mapping[str, Any]], slm_keys: set[str]
) -> tuple[float, str, str]:
    vals = _pool_values(pool, key)
    if not vals:
        return 0.0, f"{key}<=0", "lt"
    key_map = {_position_key(t): t for t in pool}
    slm_vals = [
        float(v)
        for k in slm_keys
        if (t := key_map.get(k)) is not None
        for v in [_rx(t, key)]
        if v is not None
    ]
    win_vals = [
        float(v)
        for t in pool
        if _position_key(t) not in slm_keys
        for v in [_rx(t, key)]
        if v is not None
    ]
    direction = "lt" if (statistics.mean(slm_vals) if slm_vals else 0.0) < (statistics.mean(win_vals) if win_vals else 0.0) else "gt"
    best_thr = _percentile(vals, PERCENTILE_CANDIDATES[0])
    best_p = PERCENTILE_CANDIDATES[0]
    best_score = -1e18

    def _rejects(v: float, thr: float) -> bool:
        return v < thr if direction == "lt" else v > thr

    for p in PERCENTILE_CANDIDATES:
        thr = _percentile(vals, p)
        blocked_slm = 0
        blocked_win = 0
        for k in slm_keys:
            t = key_map.get(k)
            if t is None:
                continue
            v = _rx(t, key)
            if v is not None and _rejects(float(v), thr):
                blocked_slm += 1
        for t in pool:
            if _position_key(t) in slm_keys:
                continue
            v = _rx(t, key)
            if v is not None and _rejects(float(v), thr):
                blocked_win += 1
        score = blocked_slm - 0.5 * blocked_win
        if score > best_score:
            best_score = score
            best_p = p
            best_thr = thr
    op = "<" if direction == "lt" else ">"
    return best_thr, f"{key}{op}{best_thr:.4f}@p{int(best_p * 100)}", direction


@dataclass(frozen=True)
class GuardSpec:
    guard_id: str
    label: str
    conditions: str
    reject_fn: Callable[[Mapping[str, Any]], bool]
    threshold_summary: str = ""


def _reject_fn(key: str, thr: float, direction: str) -> Callable[[Mapping[str, Any]], bool]:
    def fn(t: Mapping[str, Any]) -> bool:
        v = _rx(t, key)
        if v is None:
            return False
        return float(v) < thr if direction == "lt" else float(v) > thr

    return fn


def _build_guards(
    replay_pool: Sequence[Mapping[str, Any]],
    slm_keys: set[str],
    ranking: Sequence[Mapping[str, Any]],
) -> list[GuardSpec]:
    pbv2_pool = [t for t in replay_pool if pass_pbv2(t)]
    thr_mom, sum_mom, dir_mom = _best_threshold("momentum_continuation_score", pbv2_pool, slm_keys)
    thr_vwap, sum_vwap, dir_vwap = _best_threshold("vwap_dev_pct", pbv2_pool, slm_keys)
    thr_board, sum_board, dir_board = _best_threshold("board_imbalance", pbv2_pool, slm_keys)
    thr_r10, sum_r10, dir_r10 = _best_threshold("r10", pbv2_pool, slm_keys)
    thr_vstruct, sum_vstruct, dir_vstruct = _best_threshold("vwap_structure_score", pbv2_pool, slm_keys)

    guards = [
        GuardSpec(
            "G1",
            "low initial momentum reject",
            sum_mom,
            _reject_fn("momentum_continuation_score", thr_mom, dir_mom),
            sum_mom,
        ),
        GuardSpec("G2", "weak vwap reject", sum_vwap, _reject_fn("vwap_dev_pct", thr_vwap, dir_vwap), sum_vwap),
        GuardSpec("G3", "weak board reject", sum_board, _reject_fn("board_imbalance", thr_board, dir_board), sum_board),
        GuardSpec("G4", "poor r10 reject", sum_r10, _reject_fn("r10", thr_r10, dir_r10), sum_r10),
        GuardSpec(
            "G5",
            "low vwap structure reject",
            sum_vstruct,
            _reject_fn("vwap_structure_score", thr_vstruct, dir_vstruct),
            sum_vstruct,
        ),
    ]

    g6 = lambda t: _reject_fn("vwap_dev_pct", thr_vwap, dir_vwap)(t) and _reject_fn("board_imbalance", thr_board, dir_board)(t)
    g7 = lambda t: _reject_fn("r10", thr_r10, dir_r10)(t) and _reject_fn("vwap_dev_pct", thr_vwap, dir_vwap)(t)
    g8 = lambda t: _reject_fn("momentum_continuation_score", thr_mom, dir_mom)(t) and _reject_fn("board_imbalance", thr_board, dir_board)(t)
    g9 = lambda t: _reject_fn("vwap_structure_score", thr_vstruct, dir_vstruct)(t) and _reject_fn("r10", thr_r10, dir_r10)(t)
    guards.extend(
        [
            GuardSpec("G6", "weak vwap AND weak board", "vwap_dev & board_imb", g6, f"{sum_vwap};{sum_board}"),
            GuardSpec("G7", "poor r10 AND weak vwap", "r10 & vwap_dev", g7, f"{sum_r10};{sum_vwap}"),
            GuardSpec("G8", "low momentum AND weak board", "mom & board_imb", g8, f"{sum_mom};{sum_board}"),
            GuardSpec("G9", "low vwap structure AND poor r10", "vwap_struct & r10", g9, f"{sum_vstruct};{sum_r10}"),
        ]
    )

    entry_ranking = [r for r in ranking if r.get("feature") in ENTRY_GUARD_FEATURES]
    top_feats = [r["feature"] for r in entry_ranking[:2]] if len(entry_ranking) >= 2 else ["high_update_age", "r30"]
    medians: dict[str, float] = {}
    directions: dict[str, str] = {}
    slm_rows = [t for t in pbv2_pool if _position_key(t) in slm_keys]
    win_rows = [t for t in pbv2_pool if _position_key(t) not in slm_keys]
    for feat in top_feats:
        sv = _pool_values(slm_rows, feat)
        wv = _pool_values(win_rows, feat)
        allv = _pool_values(pbv2_pool, feat)
        medians[feat] = statistics.median(allv) if allv else 0.0
        directions[feat] = "lt" if (statistics.mean(sv) if sv else 0) < (statistics.mean(wv) if wv else 0) else "gt"

    def g10(t: Mapping[str, Any]) -> bool:
        hits = 0
        for feat in top_feats:
            v = _rx(t, feat)
            if v is None:
                continue
            med = medians[feat]
            if directions[feat] == "lt" and float(v) < med:
                hits += 1
            elif directions[feat] == "gt" and float(v) > med:
                hits += 1
        return hits >= 2

    guards.append(
        GuardSpec(
            "G10",
            "conservative best 2-condition guard",
            f"{' & '.join(top_feats)} medians",
            g10,
            ";".join(f"{f}:{directions.get(f, 'lt')}@{medians.get(f, 0):.4f}" for f in top_feats),
        )
    )
    return guards


def _pass_with_guard(guard: GuardSpec) -> Callable[[Mapping[str, Any]], bool]:
    def fn(t: Mapping[str, Any]) -> bool:
        if not pass_pbv2(t):
            return False
        return not guard.reject_fn(t)

    return fn


def _replay_metrics(
    state: CapacityReplayState,
    *,
    trade_by_key: Mapping[str, Mapping[str, Any]],
    price_idx: Mapping[tuple[str, str], list],
) -> dict[str, Any]:
    rows = _build_trade_rows(state, trade_by_key=trade_by_key, price_idx=price_idx)
    chron = _chron_pnls(state.trade_log)
    slm = [r for r in rows if r.get("is_stop_low_mfe")]
    return {
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron),
        "accepted_count": state.accepted_trade_count,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "stop_low_mfe_count": len(slm),
        "stop_low_mfe_pnl_yen": round(sum(float(r.get("pnl_yen") or 0) for r in slm), 2),
        "_rows": rows,
        "_keys": {_position_key(r.get("trade") or r) for r in rows},
    }


def _blocked_stats(
    baseline_rows: Sequence[Mapping[str, Any]],
    blocked_keys: set[str],
) -> dict[str, Any]:
    blocked = [r for r in baseline_rows if r.get("position_key") in blocked_keys]
    winners = [r for r in blocked if float(r.get("pnl_yen") or 0) > 0]
    losers = [r for r in blocked if float(r.get("pnl_yen") or 0) < 0]
    slm = [r for r in blocked if r.get("is_stop_low_mfe")]
    return {
        "blocked_winners": len(winners),
        "blocked_losers": len(losers),
        "blocked_stop_low_mfe": len(slm),
        "blocked_pnl_yen": round(sum(float(r.get("pnl_yen") or 0) for r in blocked), 2),
    }


def _symbol_day_attr(
    guard_id: str,
    rows: Sequence[Mapping[str, Any]],
    baseline_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    base_by: dict[tuple[str, str], float] = defaultdict(float)
    for r in baseline_rows:
        base_by[(str(r.get("symbol") or ""), str(r.get("day") or ""))] += float(r.get("pnl_yen") or 0)

    out: list[dict[str, Any]] = []
    keys_seen: set[tuple[str, str]] = set()
    grouped: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        k = (str(r.get("symbol") or ""), str(r.get("day") or ""))
        grouped[k].append(r)
        keys_seen.add(k)

    for sym in FOCUS_SYMBOLS:
        for day in (DAY_618, DAY_619):
            k = (sym, day)
            bucket = grouped.get(k, [])
            slm = [x for x in bucket if x.get("is_stop_low_mfe")]
            pnl = sum(float(x.get("pnl_yen") or 0) for x in bucket)
            out.append(
                {
                    "guard_id": guard_id,
                    "symbol": sym,
                    "day": day,
                    "accepted_count": len(bucket),
                    "total_pnl_yen": round(pnl, 2),
                    "stop_low_mfe_count": len(slm),
                    "stop_low_mfe_pnl_yen": round(sum(float(x.get("pnl_yen") or 0) for x in slm), 2),
                    "delta_pnl_vs_baseline": round(pnl - base_by.get(k, 0.0), 2),
                }
            )

    for sym in FOCUS_SYMBOLS:
        sym_rows = [r for r in rows if r.get("symbol") == sym]
        slm = [x for x in sym_rows if x.get("is_stop_low_mfe")]
        pnl = sum(float(x.get("pnl_yen") or 0) for x in sym_rows)
        base_pnl = sum(float(r.get("pnl_yen") or 0) for r in baseline_rows if r.get("symbol") == sym)
        out.append(
            {
                "guard_id": guard_id,
                "symbol": sym,
                "day": "ALL",
                "accepted_count": len(sym_rows),
                "total_pnl_yen": round(pnl, 2),
                "stop_low_mfe_count": len(slm),
                "stop_low_mfe_pnl_yen": round(sum(float(x.get("pnl_yen") or 0) for x in slm), 2),
                "delta_pnl_vs_baseline": round(pnl - base_pnl, 2),
            }
        )
    return out


def _top_shares(state: CapacityReplayState) -> tuple[float, float]:
    return _concentration(state.trade_log)


def _verdict(
    *,
    ranking: Sequence[Mapping[str, Any]],
    entry_ranking: Sequence[Mapping[str, Any]],
    best: Mapping[str, Any],
    baseline: Mapping[str, Any],
    robust_rows: Sequence[Mapping[str, Any]],
) -> str:
    entry_top_d = abs(float(entry_ranking[0].get("cohens_d") or 0)) if entry_ranking else 0.0
    delta_pnl = float(best.get("delta_pnl_vs_baseline") or 0)
    delta_slm = int(best.get("delta_stop_low_mfe_count") or 0)
    blocked_win = int(best.get("blocked_winners") or 0)
    best_id = str(best.get("guard_id") or "baseline")

    if best_id == "baseline" or delta_pnl <= 0:
        if entry_top_d >= 0.25:
            return "exit_fix_needed"
        return "no_entry_signal"

    loo = [r for r in robust_rows if str(r.get("test", "")).startswith("LOO_")]
    loo_deltas = [float(r.get("delta_pnl_vs_full") or 0) for r in loo]
    loo_unstable = loo_deltas and min(loo_deltas) < -50000

    top_day = float(best.get("top_day_share") or 0)
    if loo_unstable or top_day > 0.45:
        return "overfit_candidate"
    if delta_pnl >= 10000 and delta_slm <= -3 and blocked_win <= 8:
        return "stop_low_mfe_guard_candidate"
    if delta_pnl >= 0 and delta_slm < 0:
        return "stop_low_mfe_guard_candidate"
    return "no_entry_signal"


def run_phase481(*, repo_root: Path, parallel: bool = False, max_workers: int = 4) -> dict[str, Any]:
    del parallel, max_workers
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)
    trade_by_key = {_position_key(t): t for t in replay_pool}

    st_base = simulate_capacity_replay(
        replay_pool,
        runtime_shadows,
        mode="phase481_pbv2",
        entry_block_fn=_entry_block(pass_pbv2),
        baseline_accepted_keys=set(),
    )
    baseline_rows = _build_trade_rows(st_base, trade_by_key=trade_by_key, price_idx=price_idx)
    baseline = _replay_metrics(st_base, trade_by_key=trade_by_key, price_idx=price_idx)
    slm_keys = {r["position_key"] for r in baseline_rows if r.get("is_stop_low_mfe")}

    audit_rows, ranking = _feature_audit(baseline_rows)
    guards = _build_guards(replay_pool, slm_keys, ranking)

    tournament_rows: list[dict[str, Any]] = []
    replay_attr: list[dict[str, Any]] = []
    states: dict[str, CapacityReplayState] = {"baseline": st_base}

    tournament_rows.append(
        {
            "guard_id": "baseline",
            "label": "PBv2 runtime (no extra guard)",
            "conditions": "pass_pbv2",
            "threshold_summary": "",
            **{k: baseline[k] for k in baseline if not k.startswith("_")},
            "delta_pnl_vs_baseline": 0.0,
            "delta_pf_vs_baseline": 0.0,
            "delta_maxdd_vs_baseline": 0.0,
            "delta_stop_low_mfe_count": 0,
            "delta_stop_low_mfe_pnl": 0.0,
            "blocked_winners": 0,
            "blocked_losers": 0,
            "blocked_stop_low_mfe": 0,
            "blocked_pnl_yen": 0.0,
        }
    )
    replay_attr.extend(_symbol_day_attr("baseline", baseline_rows, baseline_rows))

    for g in guards:
        st = simulate_capacity_replay(
            replay_pool,
            runtime_shadows,
            mode=f"phase481_{g.guard_id}",
            entry_block_fn=_entry_block(_pass_with_guard(g)),
            baseline_accepted_keys=set(),
        )
        states[g.guard_id] = st
        met = _replay_metrics(st, trade_by_key=trade_by_key, price_idx=price_idx)
        blocked_keys = baseline["_keys"] - met["_keys"]
        blk = _blocked_stats(baseline_rows, blocked_keys)
        top_day, top_sym = _top_shares(st)
        row = {
            "guard_id": g.guard_id,
            "label": g.label,
            "conditions": g.conditions,
            "threshold_summary": g.threshold_summary,
            **{k: met[k] for k in met if not k.startswith("_")},
            "delta_pnl_vs_baseline": round(float(met["total_pnl_yen"]) - float(baseline["total_pnl_yen"]), 2),
            "delta_pf_vs_baseline": round((met["profit_factor"] or 0) - (baseline["profit_factor"] or 0), 4),
            "delta_maxdd_vs_baseline": round(float(met["max_drawdown_yen"]) - float(baseline["max_drawdown_yen"]), 2),
            "delta_stop_low_mfe_count": int(met["stop_low_mfe_count"]) - int(baseline["stop_low_mfe_count"]),
            "delta_stop_low_mfe_pnl": round(float(met["stop_low_mfe_pnl_yen"]) - float(baseline["stop_low_mfe_pnl_yen"]), 2),
            "top_day_share": top_day,
            "top_symbol_share": top_sym,
            **blk,
        }
        tournament_rows.append(row)
        replay_attr.extend(_symbol_day_attr(g.guard_id, met["_rows"], baseline_rows))

    tournament_rows.sort(key=lambda r: float(r.get("total_pnl_yen") or -1e18), reverse=True)
    for i, r in enumerate(tournament_rows, start=1):
        r["rank_by_pnl"] = i

    guard_only = [r for r in tournament_rows if str(r.get("guard_id")) != "baseline"]
    best_guard = max(
        guard_only,
        key=lambda r: (
            float(r.get("delta_pnl_vs_baseline") or -1e18),
            int(r.get("delta_stop_low_mfe_count") or 0),
            -int(r.get("blocked_winners") or 999),
        ),
    )
    best = best_guard if float(best_guard.get("delta_pnl_vs_baseline") or 0) > 0 else next(
        r for r in tournament_rows if str(r.get("guard_id")) == "baseline"
    )
    best_id = str(best.get("guard_id") or "baseline")
    entry_ranking = [r for r in ranking if r.get("feature") in ENTRY_GUARD_FEATURES]
    full_pnl = float(best.get("total_pnl_yen") or 0)
    robust_rows: list[dict[str, Any]] = []

    def _run_guard_pool(pool: Sequence[Mapping[str, Any]], test: str) -> None:
        if best_id == "baseline":
            st = simulate_capacity_replay(
                pool,
                runtime_shadows,
                mode=f"phase481_robust_{test}",
                entry_block_fn=_entry_block(pass_pbv2),
                baseline_accepted_keys=set(),
            )
        else:
            g = next(x for x in guards if x.guard_id == best_id)
            st = simulate_capacity_replay(
                pool,
                runtime_shadows,
                mode=f"phase481_robust_{test}",
                entry_block_fn=_entry_block(_pass_with_guard(g)),
                baseline_accepted_keys=set(),
            )
        met = _replay_metrics(st, trade_by_key=trade_by_key, price_idx=price_idx)
        td, ts = _top_shares(st)
        robust_rows.append(
            {
                "test": test,
                "guard_id": best_id,
                "total_pnl_yen": met["total_pnl_yen"],
                "profit_factor": met["profit_factor"],
                "max_drawdown_yen": met["max_drawdown_yen"],
                "accepted_count": met["accepted_count"],
                "stop_low_mfe_count": met["stop_low_mfe_count"],
                "delta_pnl_vs_full": round(float(met["total_pnl_yen"]) - full_pnl, 2),
                "top_day_share": td,
                "top_symbol_share": ts,
            }
        )

    days = sorted({str(t.get("day") or "")[:8] for t in replay_pool if t.get("day")})
    for day in days:
        _run_guard_pool([t for t in replay_pool if str(t.get("day") or "")[:8] != day], f"LOO_{day}")
    _run_guard_pool(replay_pool, "full")
    for sym in ("6976.T", "4062.T"):
        _run_guard_pool([t for t in replay_pool if str(t.get("symbol") or "") != sym], f"exclude_{sym.replace('.T','')}")

    sym_pnls: Counter[str] = Counter()
    for r in states.get(best_id, st_base).trade_log:
        sym_pnls[str(r.get("symbol") or "")] += 1
    if sym_pnls:
        top_sym = sym_pnls.most_common(1)[0][0]
        _run_guard_pool([t for t in replay_pool if str(t.get("symbol") or "") != top_sym], "exclude_top_symbol")

    verdict = _verdict(
        ranking=ranking,
        entry_ranking=entry_ranking,
        best=best,
        baseline=baseline,
        robust_rows=robust_rows,
    )
    top_feat = ranking[0]["feature"] if ranking else ""
    top_entry_feat = entry_ranking[0]["feature"] if entry_ranking else ""
    sym6976 = next((r for r in replay_attr if r["guard_id"] == best_id and r["symbol"] == "6976" and r["day"] == "ALL"), {})
    sym4062 = next((r for r in replay_attr if r["guard_id"] == best_id and r["symbol"] == "4062" and r["day"] == "ALL"), {})
    day618_rows = [r for r in replay_attr if r["guard_id"] == best_id and r["day"] == DAY_618 and r["symbol"] != "ALL"]
    day619_rows = [r for r in replay_attr if r["guard_id"] == best_id and r["day"] == DAY_619 and r["symbol"] != "ALL"]
    day618 = {
        "guard_id": best_id,
        "day": DAY_618,
        "accepted_count": sum(int(r.get("accepted_count") or 0) for r in day618_rows),
        "total_pnl_yen": round(sum(float(r.get("total_pnl_yen") or 0) for r in day618_rows), 2),
        "stop_low_mfe_count": sum(int(r.get("stop_low_mfe_count") or 0) for r in day618_rows),
        "delta_pnl_vs_baseline": round(sum(float(r.get("delta_pnl_vs_baseline") or 0) for r in day618_rows), 2),
    }
    day619 = {
        "guard_id": best_id,
        "day": DAY_619,
        "accepted_count": sum(int(r.get("accepted_count") or 0) for r in day619_rows),
        "total_pnl_yen": round(sum(float(r.get("total_pnl_yen") or 0) for r in day619_rows), 2),
        "stop_low_mfe_count": sum(int(r.get("stop_low_mfe_count") or 0) for r in day619_rows),
        "delta_pnl_vs_baseline": round(sum(float(r.get("delta_pnl_vs_baseline") or 0) for r in day619_rows), 2),
    }

    loo_deltas = [float(r.get("delta_pnl_vs_full") or 0) for r in robust_rows if str(r.get("test", "")).startswith("LOO_")]
    overfit_risk = "high" if loo_deltas and min(loo_deltas) < -40000 else "moderate" if loo_deltas and statistics.pstdev(loo_deltas) > 30000 else "low"

    mandatory = {
        "1_top_separating_feature": top_feat,
        "1b_top_entry_separating_feature": top_entry_feat,
        "2_best_guard": f"{best.get('guard_id')} ({best.get('label')})",
        "3_pnl_improvement": best.get("delta_pnl_vs_baseline"),
        "4_pf_improvement": best.get("delta_pf_vs_baseline"),
        "5_maxdd_change": best.get("delta_maxdd_vs_baseline"),
        "6_stop_low_mfe_reduction_count": best.get("delta_stop_low_mfe_count"),
        "7_stop_low_mfe_reduction_pnl": best.get("delta_stop_low_mfe_pnl"),
        "8_blocked_winners": best.get("blocked_winners"),
        "9_blocked_losers": best.get("blocked_losers"),
        "10_6976_impact": sym6976,
        "11_4062_impact": sym4062,
        "12_day_618_impact": day618,
        "13_day_619_impact": day619,
        "14_overfit_risk": overfit_risk,
        "15_runtime_candidate": verdict == "stop_low_mfe_guard_candidate" and float(best.get("delta_pnl_vs_baseline") or 0) >= 15000,
        "16_shadow_candidate": best_id if verdict in ("stop_low_mfe_guard_candidate", "overfit_candidate") else None,
        "17_next_actions": _next_actions(verdict, best, top_feat),
        "verdict": verdict,
        "baseline_pnl": baseline["total_pnl_yen"],
        "baseline_stop_low_mfe_count": baseline["stop_low_mfe_count"],
        "top_cohens_d": ranking[0] if ranking else {},
        "top_entry_cohens_d": entry_ranking[0] if entry_ranking else {},
        "best_guard_by_delta": best_guard,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_feature_audit": audit_rows,
        "_guard_tournament": tournament_rows,
        "_replay_attr": replay_attr,
        "_robustness": robust_rows,
        "_ranking": ranking,
    }


def _next_actions(verdict: str, best: Mapping[str, Any], top_feat: str) -> list[str]:
    actions = [f"Verdict: {verdict}"]
    if verdict == "stop_low_mfe_guard_candidate":
        actions.append(f"Shadow guard {best.get('guard_id')}: {best.get('conditions')}")
    elif verdict == "exit_fix_needed":
        actions.append("Entry guards insufficient; investigate early-stop / no-progress for low-MFE cohort")
    elif verdict == "overfit_candidate":
        actions.append("Guard improves in-sample but LOO/concentration unstable — shadow only with caution")
    else:
        actions.append(f"No strong guard; top separator {top_feat} — continue PBv2 baseline")
    actions.append(f"Best PnL delta vs baseline: {best.get('delta_pnl_vs_baseline')}")
    return actions


@dataclass
class Phase481Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase481(repo_root=self.repo_root, parallel=self.parallel, max_workers=self.max_workers)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "feature_audit": reports / "phase481_stop_low_mfe_feature_audit.csv",
            "guard_tournament": reports / "phase481_stop_low_mfe_guard_tournament.csv",
            "replay": reports / "phase481_stop_low_mfe_replay.csv",
            "robustness": reports / "phase481_stop_low_mfe_robustness.csv",
            "summary": reports / "phase481_summary.json",
        }
        _write_csv(paths["feature_audit"], FEATURE_AUDIT_FIELDS, list(result.get("_feature_audit") or []))
        _write_csv(paths["guard_tournament"], GUARD_TOURNAMENT_FIELDS, list(result.get("_guard_tournament") or []))
        _write_csv(paths["replay"], REPLAY_ATTR_FIELDS, list(result.get("_replay_attr") or []))
        _write_csv(paths["robustness"], ROBUSTNESS_FIELDS, list(result.get("_robustness") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase481_stop_low_mfe_reduction_tournament.md"
        self._write_report(report, result)
        paths["report"] = report
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        guards = list(result.get("_guard_tournament") or [])
        lines = [
            "# Phase481 — PBv2 Stop Low MFE Reduction Tournament",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')}–{result.get('period_end')}",
            "",
            "## 必須回答",
            "",
            "| # | 項目 | 結果 |",
            "|---|------|------|",
            f"| 1 | 最大分離特徴量 | **{m.get('1_top_separating_feature')}** (entry: **{m.get('1b_top_entry_separating_feature')}**) |",
            f"| 2 | 最良guard | **{m.get('2_best_guard')}** |",
            f"| 3 | PnL改善 | **{m.get('3_pnl_improvement')}** |",
            f"| 4 | PF改善 | **{m.get('4_pf_improvement')}** |",
            f"| 5 | maxDD変化 | **{m.get('5_maxdd_change')}** |",
            f"| 6 | stop_low_mfe削減件数 | **{m.get('6_stop_low_mfe_reduction_count')}** |",
            f"| 7 | stop_low_mfe削減PnL | **{m.get('7_stop_low_mfe_reduction_pnl')}** |",
            f"| 8 | blocked winners | **{m.get('8_blocked_winners')}** |",
            f"| 9 | blocked losers | **{m.get('9_blocked_losers')}** |",
            f"| 10 | 6976影響 | {m.get('10_6976_impact')} |",
            f"| 11 | 4062影響 | {m.get('11_4062_impact')} |",
            f"| 12 | 6/18影響 | {m.get('12_day_618_impact')} |",
            f"| 13 | 6/19影響 | {m.get('13_day_619_impact')} |",
            f"| 14 | 過学習リスク | **{m.get('14_overfit_risk')}** |",
            f"| 15 | Runtime候補 | **{m.get('15_runtime_candidate')}** |",
            f"| 16 | Shadow候補 | **{m.get('16_shadow_candidate')}** |",
            f"| 17 | 次アクション | {'; '.join(m.get('17_next_actions') or [])} |",
            "",
            "## Guard tournament",
            "",
        ]
        for g in guards[:8]:
            lines.append(
                f"- **{g.get('guard_id')}**: PnL {g.get('total_pnl_yen')} Δ{g.get('delta_pnl_vs_baseline')} "
                f"slm Δ{g.get('delta_stop_low_mfe_count')} blocked_w {g.get('blocked_winners')}"
            )
        lines.extend(["", f"**判定:** `{result.get('verdict')}`"])
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
