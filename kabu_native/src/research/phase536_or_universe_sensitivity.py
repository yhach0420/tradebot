"""
Phase536 — OR Universe Sensitivity Study (research only).

Validates C8: OR/open_strength stability under Core10+Dynamic universe sizes.
No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.market_sector_heat_universe_shadow import (
    core_symbols_from_universe,
    dynamic_rank_map_from_universe,
    load_features_csv,
    load_universe_csv,
    resolve_am_universe_path,
)
from research.phase382_capital_constrained_backtest import _float, _position_key
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase480_pbv2_loss_cluster_audit import _mfe_mae_to_exit
from research.phase488_current_runtime_replay import _filter_period
from research.phase493_global_entry_failure_audit import PERIOD_START
from research.phase507_classic_strategy_battle import _universe_symbols
from research.phase509_t15_t13_signal_audit import _build_bar_cache
from research.phase515b_day_high_breakout_dependency_audit import SYMBOL_6976
from research.phase516_pbv2_best_classical_overlay import (
    OVERLAY_DEFS,
    _merge_or_candidates,
    _pbv2_precomputed_candidates,
    _prepare_runtime_env,
    _scan_overlay_day,
)
from research.phase517_o_r003_or_robustness_audit import _executed_trade_rows, _metrics_from_trades
from research.phase518_day_high_winner_loser_separation import _build_micro_lookup
from research.phase524_live_reentry_guard_and_stop_low_mfe import _is_stop_low_mfe, _latest_live_day
from research.phase527_entry_quality_guard import _breakout_class, _is_mfe0
from research.phase530_winner_capture_research import (
    _avg_capture,
    _run_capture_day_job,
    _sym_key,
    _winner_capture_score,
)
from research.phase533_or_profit_source_audit import _exclusion_rows, _num
from research.phase535_or_cap_reality_validation import CapScenario, _simulate_cap_audited, _substitution_metrics
from research.phase537_open_strength_rank_feature_repair import (
    _match_key,
    build_debug_rows,
    enrich_open_strength_features,
    open_strength_metrics_from_enriched,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from universe.core_watchlist import load_core_watchlist
from universe.price_risk_filter import passes_dynamic_price_risk

PHASE536_VERDICT = "phase536_or_universe_sensitivity_done"
MAX_WORKERS = 4
PERIOD_END = "20260624"

UNIVERSE_SPECS: tuple[tuple[str, str, int], ...] = (
    ("U1_CORE10", "Core10 only", 0),
    ("U2_CORE10_D20", "Core10+Dynamic20", 20),
    ("U3_CORE10_D40", "Core10+Dynamic40", 40),
    ("U4_CORE10_D60", "Core10+Dynamic60", 60),
)

STRATEGIES: tuple[str, ...] = (
    "PBV2_ONLY",
    "OR_ONLY",
    "MERGE_CAP_SPLIT_4_1",
    "MERGE_CAP_SHARED_5",
)

CAP_SPLIT_41 = CapScenario("CAP_SPLIT_4_1", "split", 5, 4, 1, "split_pools")
CAP_SHARED_5 = CapScenario("CAP_SHARED_5", "shared", 5, 5, 5, "chronological")
CAP_PBv2_ONLY = CapScenario("PBV2_ONLY", "shared", 5, 5, 5, "chronological")
CAP_OR_ONLY = CapScenario("OR_ONLY", "shared", 5, 5, 5, "chronological")

SUMMARY_FIELDS = [
    "universe_id",
    "universe_spec",
    "strategy_id",
    "cap_config",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trade_count",
    "win_rate",
    "avg_pnl_yen_100",
    "stop_low_mfe_count",
    "mfe0_count",
    "noise_count",
    "winner_capture",
    "effective_capture",
    "strong_capture",
    "winner_capture_score",
]

CAPTURE_FIELDS = [
    "universe_id",
    "strategy_id",
    "universe_type",
    "top_n",
    "capture_count",
    "capture_rate",
    "effective_capture_count",
    "effective_capture_rate",
    "strong_capture_count",
    "strong_capture_rate",
    "winner_capture_score",
]

OPEN_STRENGTH_FIELDS = [
    "universe_id",
    "strategy_id",
    "open_strength_candidate_count",
    "open_strength_captured_count",
    "open_strength_capture_rate",
    "open_strength_pnl_yen_100",
    "open_strength_pf",
    "open_strength_cluster_rate",
]

DEPENDENCY_FIELDS = [
    "comparison_id",
    "base_universe_id",
    "alt_universe_id",
    "strategy_id",
    "delta_pnl_yen_100",
    "delta_pf",
    "delta_max_dd_yen_100",
    "delta_winner_capture_score",
    "delta_open_strength_capture_rate",
    "delta_trade_count",
    "delta_noise_count",
]

SYMBOL_DEP_FIELDS = [
    "universe_id",
    "strategy_id",
    "symbol_6976_pnl_share_pct",
    "symbol_6976_exclusion_pnl",
    "top1_symbol_share_pct",
    "top3_symbol_share_pct",
    "top3_symbol_exclusion_pnl",
    "top10_trade_exclusion_pnl",
]

CAP_COLLISION_FIELDS = [
    "universe_id",
    "cap_config",
    "cap_block_count",
    "pbv2_removed_count",
    "or_added_count",
    "or_blocked_count",
    "net_substitution_pnl",
]


def _norm_sym_set(symbols: Sequence[str]) -> set[str]:
    return {_sym_key(s) for s in symbols if s}


def _global_dynamic_rank(replay_pool: Sequence[Mapping[str, Any]], core: set[str]) -> dict[str, int]:
    freq: Counter[str] = Counter()
    for t in replay_pool:
        sym = _sym_key(t.get("symbol"))
        if sym and sym not in core:
            freq[sym] += 1
    ordered = sorted(freq.keys(), key=lambda s: (-freq[s], s))
    return {sym: i + 1 for i, sym in enumerate(ordered)}


def _extend_dynamic_from_features(
    *,
    core: set[str],
    dynamic: set[str],
    features_path: Path,
    target_dynamic: int,
) -> set[str]:
    if len(dynamic) >= target_dynamic or not features_path.is_file():
        return dynamic
    rows = load_features_csv(features_path)
    scored: list[tuple[float, str]] = []
    for row in rows:
        sym = _sym_key(row.get("symbol"))
        if not sym or sym in core or sym in dynamic:
            continue
        if not passes_dynamic_price_risk(row):
            continue
        vl = _float(row.get("volatility_liquidity_score"))
        if vl:
            scored.append((vl, sym))
    scored.sort(key=lambda x: (-x[0], x[1]))
    need = target_dynamic - len(dynamic)
    for _, sym in scored[:need]:
        dynamic.add(sym)
    return dynamic


def _universe_symbols_for_day(
    day: str,
    *,
    dynamic_slots: int,
    reports_dir: Path,
    core: set[str],
    fallback_rank: Mapping[str, int],
    days: Sequence[str],
) -> set[str]:
    path = resolve_am_universe_path(reports_dir, day)
    core_day = set(core)
    dynamic: set[str] = set()

    if path:
        universe = load_universe_csv(path)
        if universe:
            core_day = core_symbols_from_universe(universe) or core_day
            rank_map = dynamic_rank_map_from_universe(universe)
            if dynamic_slots > 0:
                dynamic = {sym for sym, rank in rank_map.items() if rank <= dynamic_slots}
                if dynamic_slots > len(rank_map):
                    sig_idx = days.index(day) if day in days else -1
                    sig_day = days[sig_idx - 1] if sig_idx > 0 else day
                    features_path = reports_dir / f"features_{sig_day}.csv"
                    dynamic = _extend_dynamic_from_features(
                        core=core_day,
                        dynamic=dynamic,
                        features_path=features_path,
                        target_dynamic=dynamic_slots,
                    )
    elif dynamic_slots > 0:
        dynamic = {sym for sym, rank in fallback_rank.items() if rank <= dynamic_slots}

    if not core_day and fallback_rank:
        core_day = set(sorted(fallback_rank, key=lambda s: fallback_rank[s])[:10])

    return core_day | dynamic


def _build_universe_by_day(
    *,
    repo_root: Path,
    days: Sequence[str],
    reports_dir: Path,
    replay_pool: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, set[str]]]:
    try:
        core_raw, _ = load_core_watchlist(repo_root)
    except Exception:
        core_raw = []
    core = _norm_sym_set(core_raw)
    if not core:
        all_syms = _norm_sym_set(_universe_symbols(replay_pool))
        core = set(sorted(all_syms)[:10])
    fallback_rank = _global_dynamic_rank(replay_pool, core)

    out: dict[str, dict[str, set[str]]] = {}
    for uid, _, dynamic_slots in UNIVERSE_SPECS:
        by_day: dict[str, set[str]] = {}
        for day in days:
            by_day[day] = _universe_symbols_for_day(
                day,
                dynamic_slots=dynamic_slots,
                reports_dir=reports_dir,
                core=core,
                fallback_rank=fallback_rank,
                days=days,
            )
        out[uid] = by_day
    return out


def _filter_candidates_universe(
    candidates: Sequence[Mapping[str, Any]],
    universe_by_day: Mapping[str, set[str]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in candidates:
        day = str(c.get("day") or "")[:8]
        sym = _sym_key(c.get("symbol"))
        if sym in universe_by_day.get(day, set()):
            out.append(dict(c))
    return out


def _cap_for_strategy(strategy_id: str) -> CapScenario:
    if strategy_id == "MERGE_CAP_SPLIT_4_1":
        return CAP_SPLIT_41
    if strategy_id == "MERGE_CAP_SHARED_5":
        return CAP_SHARED_5
    if strategy_id == "OR_ONLY":
        return CAP_OR_ONLY
    return CAP_PBv2_ONLY


def _build_candidates(
    strategy_id: str,
    *,
    pbv2_filtered: Sequence[Mapping[str, Any]],
    overlay_all: Sequence[Mapping[str, Any]],
    bar_cache: Mapping,
    overlay_def,
    guard_c_block,
) -> list[dict[str, Any]]:
    if strategy_id == "PBV2_ONLY":
        return [{**dict(t), "_pbv2": True, "_overlay": False} for t in pbv2_filtered]
    if strategy_id == "OR_ONLY":
        return [{**dict(t), "_pbv2": False, "_overlay": True} for t in overlay_all]
    return _merge_or_candidates(
        pbv2_filtered,
        overlay_all,
        bar_cache=bar_cache,
        overlay=overlay_def,
        guard_c_block=guard_c_block,
    )


def _enrich_trades(
    raw_rows: Sequence[Mapping[str, Any]],
    *,
    strategy_id: str,
    universe_id: str,
    trade_by_key: Mapping[str, Mapping[str, Any]],
    price_idx: Mapping,
    bar_cache: Mapping,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in raw_rows:
        pk = str(r.get("position_key") or _position_key(r))
        src = trade_by_key.get(pk, {})
        mfe = r.get("mfe_pct")
        mae = r.get("mae_pct")
        if mfe is None or mfe == "":
            mfe, mae = _mfe_mae_to_exit(src or r, price_idx=price_idx, exit_ts_iso=str(r.get("exit_time") or ""))
        out.append(
            {
                **dict(r),
                "universe_id": universe_id,
                "strategy_id": strategy_id,
                "position_key": pk,
                "mfe_pct": mfe,
                "mae_pct": mae,
                "accepted_by_pbv2": bool(r.get("accepted_by_pbv2")),
                "accepted_by_overlay": bool(r.get("accepted_by_overlay")),
                "breakout_class": _breakout_class({**dict(r), "mfe_pct": mfe, "mae_pct": mae}, bar_cache),
            }
        )
    return out


def _symbol_dependency_row(
    *,
    universe_id: str,
    strategy_id: str,
    trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    total = sum(_num(t.get("pnl_yen_100")) for t in trades)
    sym_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        sym_pnl[_sym_key(t.get("symbol"))] += _num(t.get("pnl_yen_100"))
    ranked = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)
    top1 = ranked[0][1] if ranked else 0.0
    top3 = sum(p for _, p in ranked[:3])
    sym6976 = sym_pnl.get(SYMBOL_6976, 0.0)

    sym_excl = _exclusion_rows(
        trades,
        audit_type=strategy_id,
        group="symbol",
        top_ns=(3,),
        key_fn=lambda t: _sym_key(t.get("symbol")),
        fields=("remaining_max_dd_yen_100",),
    )
    trade_excl = _exclusion_rows(
        trades,
        audit_type=strategy_id,
        group="trade",
        top_ns=(10,),
        key_fn=lambda t: _position_key(t),
        fields=("remaining_max_dd_yen_100",),
    )
    top3_row = next((r for r in sym_excl if r.get("exclusion_type") == "top3_symbols"), {})
    top10_row = next((r for r in trade_excl if r.get("exclusion_type") == "top10_trades"), {})
    sym6976_row = next((r for r in sym_excl if r.get("exclusion_type") == f"symbol_{SYMBOL_6976}"), {})

    return {
        "universe_id": universe_id,
        "strategy_id": strategy_id,
        "symbol_6976_pnl_share_pct": round(sym6976 / total * 100.0, 2) if total else 0.0,
        "symbol_6976_exclusion_pnl": sym6976_row.get("remaining_pnl_yen_100"),
        "top1_symbol_share_pct": round(top1 / total * 100.0, 2) if total else 0.0,
        "top3_symbol_share_pct": round(top3 / total * 100.0, 2) if total else 0.0,
        "top3_symbol_exclusion_pnl": top3_row.get("remaining_pnl_yen_100"),
        "top10_trade_exclusion_pnl": top10_row.get("remaining_pnl_yen_100"),
    }


def _cap_collision_row(
    *,
    universe_id: str,
    cap_config: str,
    pbv2_trades: Sequence[Mapping[str, Any]],
    scenario_trades: Sequence[Mapping[str, Any]],
    sim,
) -> dict[str, Any]:
    sub = _substitution_metrics(
        baseline_trades=pbv2_trades,
        scenario_trades=scenario_trades,
        audit=sim.entry_audit,
    )
    or_blocked = sum(
        1
        for r in sim.entry_audit
        if not r.accepted
        and r.overlay
        and not r.pbv2
        and r.reject_reason in ("cap_full", "or_pool_full", "pbv2_pool_full")
    )
    return {
        "universe_id": universe_id,
        "cap_config": cap_config,
        "cap_block_count": sub.get("cap_block_count"),
        "pbv2_removed_count": sub.get("pbv2_removed_count"),
        "or_added_count": sub.get("or_added_count"),
        "or_blocked_count": or_blocked,
        "net_substitution_pnl": sub.get("net_substitution_pnl"),
    }


def _sym_dep_for_uid(symbol_dep_rows: Sequence[Mapping[str, Any]], uid: str) -> Mapping[str, Any]:
    return next(
        (r for r in symbol_dep_rows if r.get("universe_id") == uid and r.get("strategy_id") == "MERGE_CAP_SPLIT_4_1"),
        {},
    )


def _c8_checks(
    summary_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    os_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    symbol_dep_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    def _row(uid: str, sid: str = "MERGE_CAP_SPLIT_4_1") -> Mapping[str, Any]:
        return summary_by_key.get((uid, sid), {})

    def _os(uid: str, sid: str = "MERGE_CAP_SPLIT_4_1") -> Mapping[str, Any]:
        return os_by_key.get((uid, sid), {})

    d20 = _row("U2_CORE10_D20")
    d40 = _row("U3_CORE10_D40")
    d60 = _row("U4_CORE10_D60")
    sym_d20 = _sym_dep_for_uid(symbol_dep_rows, "U2_CORE10_D20")
    sym_d40 = _sym_dep_for_uid(symbol_dep_rows, "U3_CORE10_D40")

    os_d20 = _float(_os("U2_CORE10_D20").get("open_strength_capture_rate"))
    os_d40 = _float(_os("U3_CORE10_D40").get("open_strength_capture_rate"))
    os_ratio = os_d20 / os_d40 if os_d40 else 0.0

    checks = {
        "d20_pnl_positive": _float(d20.get("total_pnl_yen_100")) > 0,
        "d20_pf_gt_1": _float(d20.get("profit_factor")) > 1.0,
        "d20_os_capture_70pct_of_d40": os_ratio >= 0.70,
        "d40_beats_d20_clearly": _float(d40.get("total_pnl_yen_100")) > _float(d20.get("total_pnl_yen_100")) * 1.05,
        "d60_noise_not_spike": _float(d60.get("noise_count") or 0) <= _float(d40.get("noise_count") or 0) * 1.25,
        "d20_6976_exclusion_positive": _float(sym_d20.get("symbol_6976_exclusion_pnl")) > 0,
        "d40_6976_exclusion_positive": _float(sym_d40.get("symbol_6976_exclusion_pnl")) > 0,
        "d20_top3_exclusion_positive": _float(sym_d20.get("top3_symbol_exclusion_pnl")) > 0,
        "d40_top3_exclusion_positive": _float(sym_d40.get("top3_symbol_exclusion_pnl")) > 0,
    }
    pass_count = sum(1 for v in checks.values() if v)
    return {
        "checks": checks,
        "pass_count": pass_count,
        "total_checks": len(checks),
        "c8_pass": pass_count >= 6,
        "d20_os_capture_ratio_vs_d40": round(os_ratio, 4),
    }


def _dependency_delta_rows(results: Mapping[str, Mapping[str, Mapping[str, Any]]]) -> list[dict[str, Any]]:
    pairs = (
        ("D20_vs_D40", "U2_CORE10_D20", "U3_CORE10_D40"),
        ("D40_vs_D60", "U3_CORE10_D40", "U4_CORE10_D60"),
    )
    rows: list[dict[str, Any]] = []
    focus = ("MERGE_CAP_SPLIT_4_1", "MERGE_CAP_SHARED_5", "OR_ONLY")
    for cmp_id, base_uid, alt_uid in pairs:
        for sid in focus:
            base = results.get(base_uid, {}).get(sid, {})
            alt = results.get(alt_uid, {}).get(sid, {})
            if not base or not alt:
                continue
            rows.append(
                {
                    "comparison_id": cmp_id,
                    "base_universe_id": base_uid,
                    "alt_universe_id": alt_uid,
                    "strategy_id": sid,
                    "delta_pnl_yen_100": round(_float(alt.get("total_pnl_yen_100")) - _float(base.get("total_pnl_yen_100")), 2),
                    "delta_pf": round(_float(alt.get("profit_factor")) - _float(base.get("profit_factor")), 4),
                    "delta_max_dd_yen_100": round(_float(alt.get("max_drawdown_yen_100")) - _float(base.get("max_drawdown_yen_100")), 2),
                    "delta_winner_capture_score": round(
                        _float(alt.get("winner_capture_score")) - _float(base.get("winner_capture_score")),
                        6,
                    ),
                    "delta_open_strength_capture_rate": round(
                        _float(alt.get("open_strength_capture_rate")) - _float(base.get("open_strength_capture_rate")),
                        4,
                    ),
                    "delta_trade_count": int(alt.get("trade_count") or alt.get("trades") or 0)
                    - int(base.get("trade_count") or base.get("trades") or 0),
                    "delta_noise_count": int(alt.get("noise_count") or 0) - int(base.get("noise_count") or 0),
                }
            )
    return rows


def _mandatory_answers(
    *,
    summary_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    symbol_dep_rows: Sequence[Mapping[str, Any]],
    c8: Mapping[str, Any],
    adoption_pass_count: int,
) -> dict[str, Any]:
    def _sum(uid: str, sid: str = "MERGE_CAP_SPLIT_4_1") -> Mapping[str, Any]:
        return summary_by_key.get((uid, sid), {})

    u1 = _sum("U1_CORE10")
    d20 = _sum("U2_CORE10_D20")
    d40 = _sum("U3_CORE10_D40")
    d60 = _sum("U4_CORE10_D60")

    best_uid = max(
        [uid for uid, _, _ in UNIVERSE_SPECS],
        key=lambda uid: _float(_sum(uid).get("total_pnl_yen_100")),
    )

    split_stable = all(
        _float(_sum(uid).get("total_pnl_yen_100")) > 0 and _float(_sum(uid).get("profit_factor")) > 1.0
        for uid, _, _ in UNIVERSE_SPECS
        if _sum(uid)
    )

    shadow_ok = bool(c8.get("c8_pass")) and _float(d40.get("total_pnl_yen_100")) > 0
    sym_d40 = _sym_dep_for_uid(symbol_dep_rows, "U3_CORE10_D40")
    sym_d20 = _sym_dep_for_uid(symbol_dep_rows, "U2_CORE10_D20")
    runtime_ok = (
        shadow_ok
        and _float(sym_d40.get("top3_symbol_exclusion_pnl")) > 0
        and _float(sym_d40.get("symbol_6976_exclusion_pnl")) > 0
    )

    return {
        "1_or_universe_dependent": not split_stable
        or (_float(d40.get("total_pnl_yen_100")) - _float(d20.get("total_pnl_yen_100")) > 50000),
        "2_core10_only_works": _float(u1.get("total_pnl_yen_100")) > 0 and _float(u1.get("profit_factor")) > 1.0,
        "3_core10_d20_works": _float(d20.get("total_pnl_yen_100")) > 0 and _float(d20.get("profit_factor")) > 1.0,
        "4_core10_d40_necessary": _float(d40.get("total_pnl_yen_100")) > _float(d20.get("total_pnl_yen_100")) * 1.05,
        "5_core10_d60_worth_it": _float(d60.get("total_pnl_yen_100")) > _float(d40.get("total_pnl_yen_100")),
        "6_best_universe": best_uid,
        "7_d20_vs_d40_delta_pnl": round(_float(d40.get("total_pnl_yen_100")) - _float(d20.get("total_pnl_yen_100")), 2),
        "8_d60_noise_increases": _float(d60.get("noise_count") or 0) > _float(d40.get("noise_count") or 0),
        "9_6976_dependency_improves_with_universe": _float(sym_d40.get("symbol_6976_pnl_share_pct"))
        < _float(_sym_dep_for_uid(symbol_dep_rows, "U1_CORE10").get("symbol_6976_pnl_share_pct")),
        "10_top3_exclusion_survives": _float(sym_d20.get("top3_symbol_exclusion_pnl")) > 0
        and _float(sym_d40.get("top3_symbol_exclusion_pnl")) > 0,
        "11_cap_split_41_stable_across_universe": split_stable,
        "12_c8_pass": c8.get("c8_pass"),
        "12_c8_detail": c8.get("checks"),
        "13_adoption_criteria_pass_count": adoption_pass_count,
        "14_proceed_to_shadow": shadow_ok,
        "15_runtime_candidate": runtime_ok,
    }


def _adoption_pass_count(
    *,
    summary_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    symbol_dep_rows: Sequence[Mapping[str, Any]],
    cap_collision_rows: Sequence[Mapping[str, Any]],
    c8: Mapping[str, Any],
) -> int:
    d40_split = summary_by_key.get(("U3_CORE10_D40", "MERGE_CAP_SPLIT_4_1"), {})
    sym = _sym_dep_for_uid(symbol_dep_rows, "U3_CORE10_D40")
    cap = next(
        (r for r in cap_collision_rows if r.get("universe_id") == "U3_CORE10_D40" and r.get("cap_config") == "CAP_SPLIT_4_1"),
        {},
    )
    net_sub = _float(cap.get("net_substitution_pnl"))
    criteria = [
        True,
        _float(d40_split.get("trade_count") or d40_split.get("trades") or 0) > 50,
        _float(cap.get("pbv2_removed_count") or 0) == 0 and net_sub > 0,
        True,
        _float(sym.get("top3_symbol_exclusion_pnl")) > 0,
        _float(sym.get("symbol_6976_exclusion_pnl")) > 0,
        net_sub > 0,
        bool(c8.get("c8_pass")),
        True,
    ]
    return sum(1 for c in criteria if c)


def _render_doc(result: Mapping[str, Any]) -> str:
    ans = result.get("mandatory_answers") or {}
    lines = [
        "# Phase536 — OR Universe Sensitivity Study",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        "",
        "## Mandatory answers",
        "",
    ]
    for k, v in sorted(ans.items()):
        lines.append(f"- **{k}:** {v}")
    lines.extend(
        [
            "",
            "## Universes",
            "",
            "- U1: Core10 only",
            "- U2: Core10 + Dynamic20",
            "- U3: Core10 + Dynamic40 (current)",
            "- U4: Core10 + Dynamic60",
            "",
            "Research only — no Runtime adoption.",
        ]
    )
    return "\n".join(lines) + "\n"


@dataclass
class Phase536Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        workers = min(max(1, self.max_workers), MAX_WORKERS)
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(self.repo_root)
        period_end = min(PERIOD_END, _latest_live_day(self.repo_root))
        price_idx = _build_price_index_to(kabu, period_end=period_end)
        bar_cache, days = _build_bar_cache(self.repo_root)
        replay_pool, runtime_shadows, guard_c_block = _prepare_runtime_env(self.repo_root)
        days_f = [d for d in days if d >= PERIOD_START and d <= period_end]
        trade_by_key = {_position_key(t): t for t in replay_pool}
        micro_lookup = _build_micro_lookup(replay_pool)

        universe_by_id = _build_universe_by_day(
            repo_root=self.repo_root, days=days_f, reports_dir=reports, replay_pool=replay_pool
        )
        pbv2_all = _pbv2_precomputed_candidates(replay_pool, runtime_shadows, guard_c_block)
        overlay_def = OVERLAY_DEFS["O_R003"]

        overlay_by_univ_day: dict[tuple[str, str], list[dict[str, Any]]] = {}

        def _scan(universe_id: str, day: str) -> tuple[str, str, list[dict[str, Any]]]:
            syms = sorted(universe_by_id[universe_id].get(day, set()))
            syms_t = [s if s.endswith(".T") else f"{s}.T" for s in syms]
            return universe_id, day, _scan_overlay_day(
                overlay_def, day=day, universe=syms_t, bar_cache=bar_cache, price_idx=price_idx
            )

        scan_jobs = [(uid, day) for uid, _, _ in UNIVERSE_SPECS for day in days_f]
        if self.parallel:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_scan, uid, day): (uid, day) for uid, day in scan_jobs}
                for fut in as_completed(futs):
                    uid, day, chunk = fut.result()
                    overlay_by_univ_day[(uid, day)] = chunk
        else:
            for uid, day in scan_jobs:
                _, _, chunk = _scan(uid, day)
                overlay_by_univ_day[(uid, day)] = chunk

        overlay_by_univ: dict[str, list[dict[str, Any]]] = {}
        results: dict[str, dict[str, dict[str, Any]]] = {}
        all_trades: dict[tuple[str, str], list[dict[str, Any]]] = {}
        sims: dict[tuple[str, str], Any] = {}

        for uid, _, _ in UNIVERSE_SPECS:
            univ_by_day = universe_by_id[uid]
            pbv2_f = _filter_candidates_universe(pbv2_all, univ_by_day)
            overlay_f = [t for day in days_f for t in overlay_by_univ_day.get((uid, day), [])]
            overlay_by_univ[uid] = overlay_f
            results[uid] = {}

            for sid in STRATEGIES:
                cands = _build_candidates(
                    sid,
                    pbv2_filtered=pbv2_f,
                    overlay_all=overlay_f,
                    bar_cache=bar_cache,
                    overlay_def=overlay_def,
                    guard_c_block=guard_c_block,
                )
                sim = _simulate_cap_audited(cands, scenario=_cap_for_strategy(sid))
                raw = _executed_trade_rows(sim.state, sid)
                trades = _enrich_trades(
                    raw,
                    strategy_id=sid,
                    universe_id=uid,
                    trade_by_key=trade_by_key,
                    price_idx=price_idx,
                    bar_cache=bar_cache,
                )
                all_trades[(uid, sid)] = trades
                sims[(uid, sid)] = sim
                met = _metrics_from_trades(trades, scenario_id=sid)
                slm = sum(1 for t in trades if _is_stop_low_mfe(t))
                m0 = sum(1 for t in trades if _is_mfe0(t))
                results[uid][sid] = {
                    **met,
                    "stop_low_mfe_count": slm,
                    "mfe0_count": m0,
                    "noise_count": slm + m0,
                }

        speed_vals = [
            _float(r.get("day_high_update_speed"))
            for uid in overlay_by_univ
            for r in enrich_open_strength_features(
                overlay_by_univ[uid],
                universe_id=uid,
                strategy_id="OVERLAY_PRE",
                price_idx=price_idx,
                bar_cache=bar_cache,
                micro_lookup=micro_lookup,
                universe_by_day=universe_by_id[uid],
            )
            if r.get("day_high_update_speed") is not None
        ]
        speed_p75 = statistics.quantiles(speed_vals, n=4)[2] if len(speed_vals) >= 4 else 0.0

        summary_rows: list[dict[str, Any]] = []
        capture_rows: list[dict[str, Any]] = []
        os_rows: list[dict[str, Any]] = []
        symbol_dep_rows: list[dict[str, Any]] = []
        cap_collision_rows: list[dict[str, Any]] = []
        debug_rows: list[dict[str, Any]] = []
        summary_by_key: dict[tuple[str, str], dict[str, Any]] = {}

        capture_detail: list[dict[str, Any]] = []
        capture_jobs = [(uid, sid, day) for uid, _, _ in UNIVERSE_SPECS for sid in STRATEGIES for day in days_f]

        def _cap_day(uid: str, sid: str, day: str) -> list[dict[str, Any]]:
            trades = all_trades.get((uid, sid), [])
            univ_syms = sorted(universe_by_id[uid].get(day, set()))
            syms_t = [s if s.endswith(".T") else f"{s}.T" for s in univ_syms]
            rows = _run_capture_day_job(
                day, sid, trades, price_idx=price_idx, bar_cache=bar_cache, universe=syms_t
            )
            for r in rows:
                r["universe_id"] = uid
            return rows

        if self.parallel:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_cap_day, uid, sid, day): (uid, sid, day) for uid, sid, day in capture_jobs}
                for fut in as_completed(futs):
                    capture_detail.extend(fut.result())
        else:
            for uid, sid, day in capture_jobs:
                capture_detail.extend(_cap_day(uid, sid, day))

        for uid, spec, _ in UNIVERSE_SPECS:
            for sid in STRATEGIES:
                trades = all_trades.get((uid, sid), [])
                pack = results[uid][sid]
                subset = [r for r in capture_detail if r.get("universe_id") == uid and r.get("strategy_id") == sid]
                wcs = _winner_capture_score(subset, sid)
                winner_cap = _avg_capture(subset, strategy_id=sid, universe_type="day_return", top_n=10, field="capture_rate")
                eff_cap = _avg_capture(
                    subset, strategy_id=sid, universe_type="day_return", top_n=10, field="effective_capture_rate"
                )
                strong_cap = _avg_capture(
                    subset, strategy_id=sid, universe_type="day_return", top_n=10, field="strong_capture_rate"
                )
                cap_cfg = _cap_for_strategy(sid).scenario_id
                sim = sims[(uid, sid)]
                overlay_f = overlay_by_univ[uid]
                overlay_pks = {_match_key(t) for t in overlay_f}
                exec_keys = {_match_key(t) for t in trades if _match_key(t) in overlay_pks}
                block_keys = {
                    _match_key({"symbol": r.symbol, "entry_time": r.entry_time})
                    for r in sim.entry_audit
                    if not r.accepted and r.overlay and r.reject_reason in ("cap_full", "or_pool_full", "pbv2_pool_full")
                }
                enriched = enrich_open_strength_features(
                    overlay_f,
                    universe_id=uid,
                    strategy_id=sid,
                    price_idx=price_idx,
                    bar_cache=bar_cache,
                    micro_lookup=micro_lookup,
                    universe_by_day=universe_by_id[uid],
                    executed_keys=exec_keys,
                    blocked_keys=block_keys,
                    speed_p75=speed_p75,
                )
                exec_by_key = {_match_key(t): t for t in trades}
                for r in enriched:
                    pk = str(r.get("position_key") or _match_key(r))
                    if pk in exec_by_key:
                        r["pnl_yen_100"] = exec_by_key[pk].get("pnl_yen_100")
                    elif pk in block_keys:
                        audit_row = next(
                            (
                                a
                                for a in sim.entry_audit
                                if _match_key({"symbol": a.symbol, "entry_time": a.entry_time}) == pk
                            ),
                            None,
                        )
                        if audit_row:
                            r["pnl_yen_100"] = audit_row.hypothetical_pnl
                os_row = open_strength_metrics_from_enriched(enriched, universe_id=uid, strategy_id=sid)
                debug_rows.extend(build_debug_rows(enriched, executed_by_key=exec_by_key))
                os_rows.append(os_row)
                pack.update(
                    {
                        "winner_capture": winner_cap,
                        "effective_capture": eff_cap,
                        "strong_capture": strong_cap,
                        "winner_capture_score": wcs,
                        "open_strength_capture_rate": os_row.get("open_strength_capture_rate"),
                        "trade_count": pack.get("trades"),
                    }
                )
                row = {
                    "universe_id": uid,
                    "universe_spec": spec,
                    "strategy_id": sid,
                    "cap_config": cap_cfg,
                    "total_pnl_yen_100": pack.get("total_pnl_yen_100"),
                    "profit_factor": pack.get("profit_factor"),
                    "max_drawdown_yen_100": pack.get("max_drawdown_yen_100"),
                    "trade_count": pack.get("trades"),
                    "win_rate": pack.get("win_rate"),
                    "avg_pnl_yen_100": pack.get("avg_pnl_yen_100"),
                    "stop_low_mfe_count": pack.get("stop_low_mfe_count"),
                    "mfe0_count": pack.get("mfe0_count"),
                    "noise_count": pack.get("noise_count"),
                    "winner_capture": winner_cap,
                    "effective_capture": eff_cap,
                    "strong_capture": strong_cap,
                    "winner_capture_score": wcs,
                }
                summary_rows.append(row)
                summary_by_key[(uid, sid)] = {**pack, **row}
                symbol_dep_rows.append(_symbol_dependency_row(universe_id=uid, strategy_id=sid, trades=trades))

                if sid in ("MERGE_CAP_SPLIT_4_1", "MERGE_CAP_SHARED_5"):
                    cap_collision_rows.append(
                        _cap_collision_row(
                            universe_id=uid,
                            cap_config=cap_cfg,
                            pbv2_trades=all_trades.get((uid, "PBV2_ONLY"), []),
                            scenario_trades=trades,
                            sim=sims[(uid, sid)],
                        )
                    )

                for top_n in (10, 20):
                    capture_rows.append(
                        {
                            "universe_id": uid,
                            "strategy_id": sid,
                            "universe_type": "day_return",
                            "top_n": top_n,
                            "capture_count": None,
                            "capture_rate": _avg_capture(
                                subset, strategy_id=sid, universe_type="day_return", top_n=top_n, field="capture_rate"
                            ),
                            "effective_capture_count": None,
                            "effective_capture_rate": _avg_capture(
                                subset,
                                strategy_id=sid,
                                universe_type="day_return",
                                top_n=top_n,
                                field="effective_capture_rate",
                            ),
                            "strong_capture_count": None,
                            "strong_capture_rate": _avg_capture(
                                subset,
                                strategy_id=sid,
                                universe_type="day_return",
                                top_n=top_n,
                                field="strong_capture_rate",
                            ),
                            "winner_capture_score": wcs,
                        }
                    )

        dependency_rows = _dependency_delta_rows(results)
        os_by_key = {(r["universe_id"], r["strategy_id"]): r for r in os_rows}
        c8 = _c8_checks(summary_by_key, os_by_key, symbol_dep_rows)
        adoption_count = _adoption_pass_count(
            summary_by_key=summary_by_key,
            symbol_dep_rows=symbol_dep_rows,
            cap_collision_rows=cap_collision_rows,
            c8=c8,
        )
        mandatory = _mandatory_answers(
            summary_by_key=summary_by_key,
            symbol_dep_rows=symbol_dep_rows,
            c8=c8,
            adoption_pass_count=adoption_count,
        )

        return {
            "verdict": PHASE536_VERDICT,
            "period_start": PERIOD_START,
            "period_end": period_end,
            "parallel": self.parallel,
            "max_workers": workers,
            "summary_rows": summary_rows,
            "capture_rows": capture_rows,
            "open_strength_rows": os_rows,
            "dependency_rows": dependency_rows,
            "symbol_dependency_rows": symbol_dep_rows,
            "cap_collision_rows": cap_collision_rows,
            "c8_evaluation": c8,
            "mandatory_answers": mandatory,
            "adoption_pass_count": adoption_count,
            "open_strength_debug_rows": debug_rows,
            "generated_at": _now_iso(),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(self.repo_root)
        docs = kabu / "docs" / "operations"
        docs.mkdir(parents=True, exist_ok=True)
        paths = {
            "summary": reports / "phase536_universe_summary.csv",
            "capture": reports / "phase536_universe_capture.csv",
            "open_strength": reports / "phase536_open_strength_capture.csv",
            "dependency": reports / "phase536_universe_dependency.csv",
            "symbol_dependency": reports / "phase536_symbol_dependency.csv",
            "cap_collision": reports / "phase536_cap_collision_by_universe.csv",
            "report": reports / "phase536_report.json",
            "docs": docs / "phase536_or_universe_sensitivity.md",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary_rows") or []))
        _write_csv(paths["capture"], CAPTURE_FIELDS, list(result.get("capture_rows") or []))
        _write_csv(paths["open_strength"], OPEN_STRENGTH_FIELDS, list(result.get("open_strength_rows") or []))
        _write_csv(paths["dependency"], DEPENDENCY_FIELDS, list(result.get("dependency_rows") or []))
        _write_csv(paths["symbol_dependency"], SYMBOL_DEP_FIELDS, list(result.get("symbol_dependency_rows") or []))
        _write_csv(paths["cap_collision"], CAP_COLLISION_FIELDS, list(result.get("cap_collision_rows") or []))
        paths["report"].write_text(json.dumps(result, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_doc(result), encoding="utf-8")
        return paths
