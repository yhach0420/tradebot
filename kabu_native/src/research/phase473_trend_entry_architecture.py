"""
Phase473 — Trend Entry Architecture Design Audit (research only).

Design-only audit for Trend ENTRY separate from Pullback v2. No runtime changes.
"""

from __future__ import annotations

import json
import pickle
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase365_production_stack_validation import phase364_blocked_only
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase440_boundary_capacity_audit import ShadowExitInfo
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import (
    DAY_618,
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _chronological_pnls_from_log,
    _now_iso,
    _optional_float,
    _symbol_pnl_from_log,
)
from research.phase451b_entry_shape_tournament_mid_high import _board_token
from research.phase459_winner_pattern_audit import _stop_rate_from_log
from research.phase463_trend_pullback_population_tournament import (
    _fill_close_proxy_shadows,
    _filter_replay_pool,
    _weak_shape_block,
)
from research.phase464_pre_gate_archetype_audit import (
    _annotate_candidates,
    _load_population_cache,
    _passes_board_gate,
    _vwap_above_ratio,
)
from research.phase465b_trend_gate_redesign import _concentration, _day_high_distance, _high_update_age
from research.phase467_trend_exit_audit import (
    _fill_counterfactual_gaps,
    _precompute_exit_shadows,
    _simulate_exit_variant,
    _prepare_forward_context_price_idx,
)
from research.phase470_momentum_necessity_tournament import late_chase_block
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_expectancy_score_shadow import (
    MOMENTUM_SCORE_CUTOFF_P33,
    TERTILE_CUTOFFS,
    momentum_score_cutoff_pass,
)

REPLAY_MODE = "phase456_runtime_np"
MOM_P66 = TERTILE_CUTOFFS["Momentum"]["p66"]
SYMBOL_FOCUS = ("6976", "4062", "6920", "3441", "6492", "7256", "7600")
CAPTURE = ("3441", "6492", "7256", "7600")
DAY_FOCUS = (DAY_618, DAY_619)

ARCHITECTURE_FIELDS = [
    "candidate_id",
    "label",
    "signal_conditions",
    "condition_count",
    "exit_mode",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_rate",
    "avg_hold_sec",
    "daily_pnl_618",
    "daily_pnl_619",
    "symbol_pnl_6976",
    "symbol_pnl_4062",
    "captured_3441",
    "captured_6492",
    "captured_7256",
    "captured_7600",
    "top_day_share",
    "top_symbol_share",
    "rank_by_pnl",
]

EXIT_COMPARE_FIELDS = [
    "candidate_id",
    "exit_mode",
    "exit_label",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_rate",
    "avg_hold_sec",
    "delta_pnl_vs_runtime",
]

INTERACTION_FIELDS = [
    "variant",
    "label",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_rate",
    "delta_pnl_vs_pbv2",
    "delta_accepted_vs_pbv2",
    "cap_overlap_with_pbv2",
    "pbv2_only_count",
    "trend_only_count",
    "both_count",
    "symbol_pnl_6976",
    "symbol_pnl_4062",
    "captured_3441",
    "captured_6492",
    "captured_7256",
    "captured_7600",
    "top_day_share",
    "top_symbol_share",
]

SYMBOL_DAY_FIELDS = [
    "variant",
    "candidate_id",
    "symbol",
    "day",
    "pnl_yen",
    "accepted_count",
    "stop_rate",
]

EXIT_DECOMP_FIELDS = [
    "bucket",
    "loser_count",
    "runtime_pnl_yen",
    "vwap_break_pnl_yen",
    "hard_stop_pnl_yen",
    "vwap_improves_count",
    "hard_stop_hurt_count",
]


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _rise(trade: Mapping[str, Any], minutes: int) -> Optional[float]:
    return _float(trade.get(f"entry_rise_{minutes}min_pct"))


def pass_pbv2(trade: Mapping[str, Any]) -> bool:
    if not momentum_score_cutoff_pass(trade, cutoff=MOMENTUM_SCORE_CUTOFF_P33):
        return False
    if not _passes_board_gate(trade):
        return False
    if guard_high_drift(trade):
        return False
    if _weak_shape_block(trade):
        return False
    if phase364_blocked_only(trade):
        return False
    if late_chase_block(trade):
        return False
    return True


def _vwap_dev(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("entry_vwap_dev_pct")) or _float(trade.get("vwap_dev_pct"))


def _gate_ta(t: Mapping[str, Any]) -> bool:
    return (_float(t.get("high_update_count_30m")) or 0) >= 2 and (_vwap_above_ratio(t) or 0) >= 0.7


def _gate_tb(t: Mapping[str, Any]) -> bool:
    vd = _vwap_dev(t)
    return (_float(t.get("consecutive_above_ticks")) or 0) >= 20 and vd is not None and vd > 0


def _gate_tc(t: Mapping[str, Any]) -> bool:
    return _day_high_distance(t) <= 2.0 and (_float(t.get("high_update_count_30m")) or 0) >= 2


def _gate_td(t: Mapping[str, Any]) -> bool:
    mom = _float(t.get("momentum_continuation_score"))
    return mom is not None and mom >= MOM_P66 and (_vwap_above_ratio(t) or 0) >= 0.7


def _gate_te1(t: Mapping[str, Any]) -> bool:
    return _gate_ta(t)


def _gate_te2(t: Mapping[str, Any]) -> bool:
    return (_float(t.get("high_update_count_30m")) or 0) >= 2 and (_float(t.get("consecutive_above_ticks")) or 0) >= 20


def _gate_te3(t: Mapping[str, Any]) -> bool:
    return (_float(t.get("high_update_count_30m")) or 0) >= 2 and _day_high_distance(t) <= 2.0


def _gate_te4(t: Mapping[str, Any]) -> bool:
    return (_vwap_above_ratio(t) or 0) >= 0.7 and (_float(t.get("consecutive_above_ticks")) or 0) >= 20


def _gate_te5(t: Mapping[str, Any]) -> bool:
    return (_vwap_above_ratio(t) or 0) >= 0.7 and _day_high_distance(t) <= 2.0


REF_GATE_SPECS: dict[str, tuple[str, int, Callable[[Mapping[str, Any]], bool]]] = {
    "T-ref-HU30": ("high_update_count_30m >= 2", 1, lambda t: (_float(t.get("high_update_count_30m")) or 0) >= 2),
    "T-ref-VWAP": ("vwap_above_ratio >= 0.7", 1, lambda t: (_vwap_above_ratio(t) or 0) >= 0.7),
    "T-ref-CAT": ("consecutive_above_ticks >= 20", 1, lambda t: (_float(t.get("consecutive_above_ticks")) or 0) >= 20),
    "T-ref-DH": ("day_high_distance <= 2.0", 1, lambda t: _day_high_distance(t) <= 2.0),
    "T-ref-MOM": (f"momentum_continuation_score >= {MOM_P66}", 1, lambda t: (_float(t.get("momentum_continuation_score")) or 0) >= MOM_P66),
    "T-ref-3way": ("HU+VWAP+CAT (diag only)", 3, lambda t: _gate_ta(t) and (_float(t.get("consecutive_above_ticks")) or 0) >= 20),
}

TREND_GATE_SPECS: dict[str, tuple[str, int, Callable[[Mapping[str, Any]], bool]]] = {
    "T-A": ("high_update_count_30m>=2 AND vwap_above_ratio>=0.7 + Board:mid/high", 2, _gate_ta),
    "T-B": ("consecutive_above_ticks>=20 AND vwap_dev_pct>0 + Board:mid/high", 2, _gate_tb),
    "T-C": ("day_high_distance<=2.0 AND high_update_count_30m>=2 + Board:mid/high", 2, _gate_tc),
    "T-D": (f"momentum_continuation_score>={MOM_P66} AND vwap_above_ratio>=0.7 + Board:mid/high", 2, _gate_td),
    "T-E1": ("HU30 + VWAP (same as T-A)", 2, _gate_te1),
    "T-E2": ("HU30 + consecutive_above_ticks", 2, _gate_te2),
    "T-E3": ("HU30 + day_high_distance", 2, _gate_te3),
    "T-E4": ("VWAP + consecutive_above_ticks", 2, _gate_te4),
    "T-E5": ("VWAP + day_high_distance", 2, _gate_te5),
    **REF_GATE_SPECS,
}


def _make_trend_entry(signal_fn: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
    def fn(t: Mapping[str, Any]) -> bool:
        if not _passes_board_gate(t):
            return False
        if not signal_fn(t):
            return False
        if guard_high_drift(t):
            return False
        if _weak_shape_block(t):
            return False
        if phase364_blocked_only(t):
            return False
        return True

    return fn


def _entry_block(pass_fn: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
    return lambda t: not pass_fn(t)


def _avg_hold(trade_log: Sequence[Mapping[str, Any]]) -> float:
    holds = [_float(r.get("hold_sec")) or 0.0 for r in trade_log]
    return round(statistics.mean(holds), 2) if holds else 0.0


def _metrics_from_state(
    state: Any,
    *,
    candidate_id: str,
    exit_mode: str,
    baseline: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym_pnl = _symbol_pnl_from_log(state.trade_log)
    accepted_syms = {str(r.get("symbol") or "").replace(".T", "") for r in state.trade_log}
    top_day, top_sym = _concentration(state.trade_log)
    label, cond_n, _ = TREND_GATE_SPECS.get(candidate_id, (candidate_id, 0, lambda t: False))
    row = {
        "candidate_id": candidate_id,
        "label": label,
        "signal_conditions": label,
        "condition_count": cond_n,
        "exit_mode": exit_mode,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "accepted_count": state.accepted_trade_count,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "avg_hold_sec": _avg_hold(state.trade_log),
        "daily_pnl_618": round(float(state.daily_pnls.get(DAY_618, 0.0)), 2),
        "daily_pnl_619": round(float(state.daily_pnls.get(DAY_619, 0.0)), 2),
        "symbol_pnl_6976": sym_pnl.get("6976", 0.0),
        "symbol_pnl_4062": sym_pnl.get("4062", 0.0),
        "top_day_share": top_day,
        "top_symbol_share": top_sym,
        **{f"captured_{c}": c in accepted_syms for c in CAPTURE},
    }
    if baseline:
        row["delta_pnl_vs_runtime"] = round(float(row["total_pnl_yen"]) - float(baseline["total_pnl_yen"]), 2)
    return row


def _interaction_metrics(
    state: Any,
    *,
    variant: str,
    label: str,
    pbv2_keys: set[str],
    trend_keys: set[str],
    baseline: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym_pnl = _symbol_pnl_from_log(state.trade_log)
    accepted_syms = {str(r.get("symbol") or "").replace(".T", "") for r in state.trade_log}
    top_day, top_sym = _concentration(state.trade_log)
    accepted = {str((r.get("trade") or {}).get("symbol") or r.get("symbol") or "") + "|" + str((r.get("trade") or {}).get("entry_time") or r.get("entry_time") or "") for r in state.trade_log}
    both = len(accepted & pbv2_keys & trend_keys)
    pb_only = len((accepted & pbv2_keys) - trend_keys)
    tr_only = len((accepted & trend_keys) - pbv2_keys)
    row = {
        "variant": variant,
        "label": label,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "accepted_count": state.accepted_trade_count,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "cap_overlap_with_pbv2": both,
        "pbv2_only_count": pb_only,
        "trend_only_count": tr_only,
        "both_count": both,
        "symbol_pnl_6976": sym_pnl.get("6976", 0.0),
        "symbol_pnl_4062": sym_pnl.get("4062", 0.0),
        "top_day_share": top_day,
        "top_symbol_share": top_sym,
        **{f"captured_{c}": c in accepted_syms for c in CAPTURE},
    }
    if baseline:
        row["delta_pnl_vs_pbv2"] = round(float(row["total_pnl_yen"]) - float(baseline["total_pnl_yen"]), 2)
        row["delta_accepted_vs_pbv2"] = int(row["accepted_count"]) - int(baseline["accepted_count"])
    else:
        row["delta_pnl_vs_pbv2"] = 0.0
        row["delta_accepted_vs_pbv2"] = 0
    return row


def _trade_key(trade: Mapping[str, Any]) -> str:
    return f"{trade.get('symbol')}|{trade.get('entry_time')}"


def _symbol_day_rows(variant: str, candidate_id: str, state: Any) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], list[float]] = {}
    stops: Counter[tuple[str, str]] = Counter()
    for r in state.trade_log:
        tr = r.get("trade") or {}
        sym = str(tr.get("symbol") or r.get("symbol") or "").replace(".T", "")
        day = str(tr.get("day") or "")[:8]
        if sym not in SYMBOL_FOCUS and day not in DAY_FOCUS:
            continue
        key = (sym, day)
        by_key.setdefault(key, []).append(float(r.get("pnl_yen") or 0))
        reason = str(r.get("exit_reason") or "").lower()
        if "stop" in reason:
            stops[key] += 1
    rows: list[dict[str, Any]] = []
    for sym in SYMBOL_FOCUS:
        for day in DAY_FOCUS:
            key = (sym, day)
            pnls = by_key.get(key, [])
            rows.append(
                {
                    "variant": variant,
                    "candidate_id": candidate_id,
                    "symbol": sym,
                    "day": day,
                    "pnl_yen": round(sum(pnls), 2),
                    "accepted_count": len(pnls),
                    "stop_rate": round(stops[key] / len(pnls), 4) if pnls else 0.0,
                }
            )
    return rows


def _run_replay(
    pass_fn: Callable[[Mapping[str, Any]], bool],
    *,
    replay_pool: Sequence[Mapping[str, Any]],
    shadows: Mapping[str, Any],
    mode_suffix: str,
) -> Any:
    return simulate_capacity_replay(
        replay_pool,
        shadows,
        mode=f"{REPLAY_MODE}_p473_{mode_suffix}",
        entry_block_fn=_entry_block(pass_fn),
        baseline_accepted_keys=set(),
    )


def _exit_decomposition(
    trade_log: Sequence[Mapping[str, Any]],
    *,
    vwap_shadows: Mapping[str, ShadowExitInfo],
    hard_shadows: Mapping[str, ShadowExitInfo],
    price_idx: Mapping[tuple[str, str], list],
) -> list[dict[str, Any]]:
    losers: list[dict[str, Any]] = []
    for r in trade_log:
        pnl = float(r.get("pnl_yen") or 0)
        if pnl >= 0:
            continue
        tr = dict(r.get("trade") or r)
        key = _trade_key(tr)
        runtime = pnl
        vwap = float(getattr(vwap_shadows.get(key), "shadow_pnl_yen", None) or runtime)
        hard = float(getattr(hard_shadows.get(key), "shadow_pnl_yen", None) or runtime)
        reason = str(r.get("exit_reason") or "")
        rlow = reason.lower()
        if "stop" in rlow and "no_progress" not in rlow:
            bucket = "hard_stop_dominated"
        elif "no_progress" in rlow:
            bucket = "no_progress_dominated"
        elif "trail" in rlow:
            bucket = "trailing_dominated"
        else:
            bucket = "other_runtime"
        losers.append(
            {
                "bucket": bucket,
                "runtime_pnl": runtime,
                "vwap_pnl": vwap,
                "hard_pnl": hard,
                "vwap_improves": vwap > runtime,
                "hard_hurt": hard < runtime,
            }
        )

    rows: list[dict[str, Any]] = []
    for bucket in ("hard_stop_dominated", "no_progress_dominated", "trailing_dominated", "other_runtime", "all_losers"):
        sub = losers if bucket == "all_losers" else [x for x in losers if x["bucket"] == bucket]
        if not sub:
            continue
        rows.append(
            {
                "bucket": bucket,
                "loser_count": len(sub),
                "runtime_pnl_yen": round(sum(x["runtime_pnl"] for x in sub), 2),
                "vwap_break_pnl_yen": round(sum(x["vwap_pnl"] for x in sub), 2),
                "hard_stop_pnl_yen": round(sum(x["hard_pnl"] for x in sub), 2),
                "vwap_improves_count": sum(1 for x in sub if x["vwap_improves"]),
                "hard_stop_hurt_count": sum(1 for x in sub if x["hard_hurt"]),
            }
        )
    return rows


def _feature_audit_row(trade: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "symbol": trade.get("symbol"),
        "day": str(trade.get("day") or "")[:8],
        "entry_time": trade.get("entry_time"),
        "outcome": trade.get("outcome"),
        "high_update_count_30m": _float(trade.get("high_update_count_30m")),
        "high_update_count_session": _float(trade.get("high_update_count_session")),
        "high_update_age": _high_update_age(trade),
        "vwap_above_ratio": _vwap_above_ratio(trade),
        "consecutive_above_ticks": _float(trade.get("consecutive_above_ticks")),
        "vwap_dev_pct": _vwap_dev(trade),
        "day_high_distance": _day_high_distance(trade),
        "board_bucket": (_board_token(trade) or "unknown").split(":", 1)[-1],
        "board_imbalance": _float(trade.get("entry_order_book_imbalance")),
        "momentum_continuation_score": _float(trade.get("momentum_continuation_score")),
        "trading_value": _float(trade.get("trading_value")),
        "pbv2_pass": pass_pbv2(trade),
        "primary_label": trade.get("primary_label"),
    }


def _verdict(
    *,
    pbv2_row: Mapping[str, Any],
    trend_row: Mapping[str, Any],
    dual_row: Mapping[str, Any],
    best_trend: Mapping[str, Any],
    exit_compare: Sequence[Mapping[str, Any]],
    overfit: bool,
) -> str:
    trend_pnl = float(trend_row.get("total_pnl_yen") or 0)
    trend_pf = float(trend_row.get("profit_factor") or 0)
    dual_delta = float(dual_row.get("delta_pnl_vs_pbv2") or 0)
    pbv2_pnl = float(pbv2_row.get("total_pnl_yen") or 0)

    runtime_row = next((r for r in exit_compare if r.get("exit_mode") == "runtime"), {})
    vwap_row = next((r for r in exit_compare if r.get("exit_mode") == "vwap_break"), {})
    vwap_delta = float(vwap_row.get("delta_pnl_vs_runtime") or 0)

    if dual_delta < -50000:
        return "trend_reject"
    if vwap_delta > 50000 and float(vwap_row.get("total_pnl_yen") or 0) > trend_pnl + 10000:
        return "trend_exit_needed"
    if trend_pnl <= 0 and trend_pf < 1.0:
        return "trend_reject"
    if trend_pnl > 0 and trend_pf >= 1.0 and dual_delta >= -5000 and not overfit:
        return "trend_entry_candidate"
    if trend_pnl > 0 or dual_delta > 0:
        return "trend_not_ready"
    return "trend_reject"


def _load_replay_pool(reports: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = reports / ".phase463_cache" / "population.pkl"
    if not path.is_file():
        raise FileNotFoundError("phase463 cache required")
    with path.open("rb") as fh:
        payload = pickle.load(fh)
    return list(payload["replay_pool"]), dict(payload.get("np_shadows") or {})


def _merge_shadows(
    base: Mapping[str, ShadowExitInfo],
    overlay: Mapping[str, ShadowExitInfo],
) -> dict[str, ShadowExitInfo]:
    out = dict(base)
    out.update(overlay)
    return out


def _precompute_exit_shadows_subset(
    trades: Sequence[Mapping[str, Any]],
    *,
    kabu: Path,
    variant: str,
    price_idx: Mapping[tuple[str, str], list],
) -> dict[str, ShadowExitInfo]:
    if not trades:
        return {}
    return _precompute_exit_shadows(trades, kabu=kabu, variant=variant, price_idx=price_idx)


def _lazy_shadows_for_log(
    trade_log: Sequence[Mapping[str, Any]],
    *,
    kabu: Path,
    variant: str,
    price_idx: Mapping[tuple[str, str], list],
) -> dict[str, ShadowExitInfo]:
    trades = [dict(r.get("trade") or r) for r in trade_log]
    return _precompute_exit_shadows_subset(trades, kabu=kabu, variant=variant, price_idx=price_idx)


def run_phase473(
    *,
    repo_root: Path,
    parallel: bool = False,
    max_workers: int = 4,
) -> dict[str, Any]:
    del parallel, max_workers
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool(replay_pool, runtime_shadows)
    print(f"phase473 replay pool: {len(replay_pool)}", flush=True)

    cached = _load_population_cache(reports)
    if cached:
        candidates, _, _ = cached
    else:
        candidates = list(replay_pool)
    annotated = _annotate_candidates(candidates, price_idx=price_idx)
    feature_audit = [_feature_audit_row(t) for t in annotated if not t.get("data_stale")]

    production_ids = [k for k in TREND_GATE_SPECS if not k.startswith("T-ref")]
    runtime_rows: dict[str, dict[str, Any]] = {}
    states_rt: dict[str, Any] = {}

    cache_path = reports / ".phase473_cache" / "replay.pkl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    architecture_rows: list[dict[str, Any]] = []
    for cid in production_ids:
        _, _, signal_fn = TREND_GATE_SPECS[cid]
        trend_fn = _make_trend_entry(signal_fn)
        st_rt = _run_replay(trend_fn, replay_pool=replay_pool, shadows=runtime_shadows, mode_suffix=f"{cid}_rt")
        states_rt[cid] = st_rt
        row_rt = _metrics_from_state(st_rt, candidate_id=cid, exit_mode="runtime")
        runtime_rows[cid] = row_rt
        architecture_rows.append(row_rt)

    best_id = max(
        production_ids,
        key=lambda cid: float(runtime_rows.get(cid, {}).get("total_pnl_yen") or -1e18),
    )
    _, _, best_signal = TREND_GATE_SPECS[best_id]
    best_trend_fn = _make_trend_entry(best_signal)

    trend_subset = [t for t in replay_pool if best_trend_fn(t)]
    print(f"phase473 vwap shadow subset (best {best_id}): {len(trend_subset)}", flush=True)
    vwap_subset = _precompute_exit_shadows_subset(
        trend_subset, kabu=kabu, variant="B", price_idx=price_idx
    )
    vwap_subset = _fill_counterfactual_gaps(
        trend_subset, vwap_subset, price_idx=price_idx, entry_fn=lambda t: True
    )
    vwap_shadows = _merge_shadows(runtime_shadows, vwap_subset)

    st_vw = _run_replay(
        best_trend_fn, replay_pool=replay_pool, shadows=vwap_shadows, mode_suffix=f"{best_id}_vw"
    )
    row_vw = _metrics_from_state(st_vw, candidate_id=best_id, exit_mode="vwap_break", baseline=runtime_rows[best_id])
    architecture_rows.append(row_vw)

    for cid in production_ids:
        if cid == best_id:
            continue
        _, _, signal_fn = TREND_GATE_SPECS[cid]
        trend_fn = _make_trend_entry(signal_fn)
        subset = [t for t in replay_pool if trend_fn(t)]
        if not subset:
            continue
        vw = _precompute_exit_shadows_subset(subset, kabu=kabu, variant="B", price_idx=price_idx)
        vw = _fill_counterfactual_gaps(subset, vw, price_idx=price_idx, entry_fn=lambda t: True)
        st = _run_replay(trend_fn, replay_pool=replay_pool, shadows=_merge_shadows(runtime_shadows, vw), mode_suffix=f"{cid}_vw")
        architecture_rows.append(
            _metrics_from_state(st, candidate_id=cid, exit_mode="vwap_break", baseline=runtime_rows[cid])
        )

    with cache_path.open("wb") as fh:
        pickle.dump(
            {
                "replay_pool": replay_pool,
                "runtime_shadows": runtime_shadows,
                "best_id": best_id,
            },
            fh,
            protocol=pickle.HIGHEST_PROTOCOL,
        )

    pbv2_state = _run_replay(pass_pbv2, replay_pool=replay_pool, shadows=runtime_shadows, mode_suffix="pbv2")
    trend_state = states_rt[best_id]
    dual_fn = lambda t: pass_pbv2(t) or best_trend_fn(t)
    dual_state = _run_replay(dual_fn, replay_pool=replay_pool, shadows=runtime_shadows, mode_suffix="dual")

    pbv2_keys = {_trade_key(r.get("trade") or r) for r in pbv2_state.trade_log}
    trend_keys = {_trade_key(r.get("trade") or r) for r in trend_state.trade_log}
    pbv2_metrics = _interaction_metrics(
        pbv2_state, variant="A", label="Pullback v2 only", pbv2_keys=pbv2_keys, trend_keys=trend_keys
    )
    trend_metrics = _interaction_metrics(
        trend_state,
        variant="B",
        label=f"Trend only ({best_id})",
        pbv2_keys=pbv2_keys,
        trend_keys=trend_keys,
        baseline=pbv2_metrics,
    )
    dual_metrics = _interaction_metrics(
        dual_state,
        variant="C",
        label=f"Pullback v2 OR Trend ({best_id})",
        pbv2_keys=pbv2_keys,
        trend_keys=trend_keys,
        baseline=pbv2_metrics,
    )
    interaction_rows = [pbv2_metrics, trend_metrics, dual_metrics]

    best_rt = runtime_rows[best_id]
    exit_compare_rows: list[dict[str, Any]] = [
        {
            "candidate_id": best_id,
            "exit_mode": "runtime",
            "exit_label": "Hard Stop → No Progress → Board Dynamic Trailing",
            "total_pnl_yen": best_rt["total_pnl_yen"],
            "profit_factor": best_rt["profit_factor"],
            "max_drawdown_yen": best_rt["max_drawdown_yen"],
            "accepted_count": best_rt["accepted_count"],
            "stop_rate": best_rt["stop_rate"],
            "avg_hold_sec": best_rt["avg_hold_sec"],
            "delta_pnl_vs_runtime": 0.0,
        },
        {
            "candidate_id": best_id,
            "exit_mode": "vwap_break",
            "exit_label": "Hard Stop + VWAP Break",
            "total_pnl_yen": row_vw["total_pnl_yen"],
            "profit_factor": row_vw["profit_factor"],
            "max_drawdown_yen": row_vw["max_drawdown_yen"],
            "accepted_count": row_vw["accepted_count"],
            "stop_rate": row_vw["stop_rate"],
            "avg_hold_sec": row_vw["avg_hold_sec"],
            "delta_pnl_vs_runtime": row_vw.get("delta_pnl_vs_runtime", 0.0),
        },
    ]

    symbol_day: list[dict[str, Any]] = []
    symbol_day.extend(_symbol_day_rows("A", "PBv2", pbv2_state))
    symbol_day.extend(_symbol_day_rows("B", best_id, trend_state))
    symbol_day.extend(_symbol_day_rows("C", best_id, dual_state))

    exit_decomp = _exit_decomposition(
        trend_state.trade_log,
        vwap_shadows=_lazy_shadows_for_log(
            [r for r in trend_state.trade_log if float(r.get("pnl_yen") or 0) < 0],
            kabu=kabu,
            variant="B",
            price_idx=price_idx,
        ),
        hard_shadows=_lazy_shadows_for_log(
            [r for r in trend_state.trade_log if float(r.get("pnl_yen") or 0) < 0],
            kabu=kabu,
            variant="F",
            price_idx=price_idx,
        ),
        price_idx=price_idx,
    )

    architecture_rows.sort(key=lambda r: float(r.get("total_pnl_yen") or 0), reverse=True)
    for i, r in enumerate(architecture_rows, start=1):
        r["rank_by_pnl"] = i

    loo_pnls: list[float] = []
    full_trend_pnl = float(trend_metrics["total_pnl_yen"])
    days = sorted({str(t.get("day") or "")[:8] for t in replay_pool if t.get("day")})
    for day in days:
        pool = [t for t in replay_pool if str(t.get("day") or "")[:8] != day]
        st = _run_replay(best_trend_fn, replay_pool=pool, shadows=runtime_shadows, mode_suffix=f"loo_{day}")
        loo_pnls.append(sum(_chronological_pnls_from_log(st.trade_log)))
    overfit = (
        float(trend_metrics.get("top_day_share") or 0) > 0.45
        or float(trend_metrics.get("top_symbol_share") or 0) > 0.45
        or (loo_pnls and full_trend_pnl > 0 and min(loo_pnls) < 0)
    )

    verdict = _verdict(
        pbv2_row=pbv2_metrics,
        trend_row=trend_metrics,
        dual_row=dual_metrics,
        best_trend=runtime_rows[best_id],
        exit_compare=exit_compare_rows,
        overfit=overfit,
    )

    vwap_improves = float(exit_compare_rows[1].get("delta_pnl_vs_runtime") or 0) > 5000

    mandatory = {
        "1_best_trend_entry": f"{best_id} ({TREND_GATE_SPECS[best_id][0]})",
        "2_trend_only_pnl": trend_metrics["total_pnl_yen"],
        "3_trend_only_pf": trend_metrics["profit_factor"],
        "4_trend_exit_improves": vwap_improves,
        "5_pbv2_only_pnl": pbv2_metrics["total_pnl_yen"],
        "6_pbv2_plus_trend_pnl": dual_metrics["total_pnl_yen"],
        "7_breaks_pbv2": float(dual_metrics["delta_pnl_vs_pbv2"] or 0) < -10000,
        "8_6976_impact": {"pbv2": pbv2_metrics["symbol_pnl_6976"], "dual": dual_metrics["symbol_pnl_6976"]},
        "9_4062_impact": {"pbv2": pbv2_metrics["symbol_pnl_4062"], "dual": dual_metrics["symbol_pnl_4062"]},
        "10_3441_capture": dual_metrics["captured_3441"],
        "11_6492_capture": dual_metrics["captured_6492"],
        "12_7256_capture": dual_metrics["captured_7256"],
        "13_7600_capture": dual_metrics["captured_7600"],
        "14_overfit_risk": overfit,
        "15_runtime_candidate": verdict == "trend_entry_candidate" and not overfit,
        "16_shadow_candidate": best_id if verdict in ("trend_entry_candidate", "trend_exit_needed") else None,
        "17_next_actions": [
            f"Verdict: {verdict}",
            f"Best trend gate: {best_id}",
            "Keep Pullback v2 as primary; trend separate path only",
            "Shadow trend exit (VWAP break) if trend_exit_needed",
        ],
        "best_trend_id": best_id,
        "pbv2_metrics": pbv2_metrics,
        "trend_metrics": trend_metrics,
        "dual_metrics": dual_metrics,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_architecture_rows": architecture_rows,
        "_exit_compare_rows": exit_compare_rows,
        "_interaction_rows": interaction_rows,
        "_symbol_day_rows": symbol_day,
        "_exit_decomp_rows": exit_decomp,
        "_feature_audit_count": len(feature_audit),
        "_best_trend_id": best_id,
    }


@dataclass
class Phase473Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase473(
            repo_root=self.repo_root,
            parallel=self.parallel,
            max_workers=self.max_workers,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "architecture": reports / "phase473_trend_entry_architecture.csv",
            "exit_compare": reports / "phase473_trend_entry_exit_compare.csv",
            "interaction": reports / "phase473_trend_pullback_interaction.csv",
            "symbol_day": reports / "phase473_trend_symbol_day_attribution.csv",
            "summary": reports / "phase473_summary.json",
        }
        _write_csv(paths["architecture"], ARCHITECTURE_FIELDS, list(result.get("_architecture_rows") or []))
        _write_csv(paths["exit_compare"], EXIT_COMPARE_FIELDS, list(result.get("_exit_compare_rows") or []))
        _write_csv(paths["interaction"], INTERACTION_FIELDS, list(result.get("_interaction_rows") or []))
        _write_csv(paths["symbol_day"], SYMBOL_DAY_FIELDS, list(result.get("_symbol_day_rows") or []))
        _write_csv(reports / "phase473_trend_exit_decomposition.csv", EXIT_DECOMP_FIELDS, list(result.get("_exit_decomp_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase473_trend_entry_architecture.md"
        m = result.get("mandatory_answers") or {}
        arch = list(result.get("_architecture_rows") or [])
        lines = [
            "# Phase473 — Trend Entry Architecture Design Audit",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')}–{result.get('period_end')}",
            "",
            "## 必須回答",
            "",
            "| # | 項目 | 結果 |",
            "|---|------|------|",
            f"| 1 | 最良Trend Entry | {m.get('1_best_trend_entry')} |",
            f"| 2 | Trend only PnL | {m.get('2_trend_only_pnl')} |",
            f"| 3 | Trend only PF | {m.get('3_trend_only_pf')} |",
            f"| 4 | Trend Exit改善 | {m.get('4_trend_exit_improves')} |",
            f"| 5 | PBv2 only PnL | {m.get('5_pbv2_only_pnl')} |",
            f"| 6 | PBv2+Trend PnL | {m.get('6_pbv2_plus_trend_pnl')} |",
            f"| 7 | PBv2破壊 | {m.get('7_breaks_pbv2')} |",
            f"| 8 | 6976影響 | {m.get('8_6976_impact')} |",
            f"| 9 | 4062影響 | {m.get('9_4062_impact')} |",
            f"| 10–13 | 3441/6492/7256/7600 | {m.get('10_3441_capture')}/{m.get('11_6492_capture')}/{m.get('12_7256_capture')}/{m.get('13_7600_capture')} |",
            f"| 14 | 過学習 | {m.get('14_overfit_risk')} |",
            f"| 15 | Runtime候補 | {m.get('15_runtime_candidate')} |",
            f"| 16 | Shadow候補 | {m.get('16_shadow_candidate')} |",
            "",
            "## Trend Entry Tournament (runtime exit)",
            "",
            "| rank | id | PnL | PF | acc | stop |",
            "|---:|---|---:|---:|---:|---:|",
        ]
        rt_rows = sorted(
            [r for r in arch if r.get("exit_mode") == "runtime" and not str(r.get("candidate_id", "")).startswith("T-ref")],
            key=lambda x: x.get("rank_by_pnl", 99),
        )
        for r in rt_rows[:12]:
            lines.append(
                f"| {r.get('rank_by_pnl')} | {r.get('candidate_id')} | {r.get('total_pnl_yen')} "
                f"| {r.get('profit_factor')} | {r.get('accepted_count')} | {r.get('stop_rate')} |"
            )
        lines.extend(["", f"Next: {m.get('17_next_actions')}", ""])
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
        paths["report"] = report
        return paths
