"""
Phase532 — Dual-Path Entry Study (research only).

Separates PBv2 Path (G9) from OR Path (F6 / OR_only) and tests coexistence value.
No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase480_pbv2_loss_cluster_audit import _mfe_mae_to_exit
from research.phase493_global_entry_failure_audit import PERIOD_START
from research.phase507_classic_strategy_battle import (
    _run_baseline_runtime,
    _universe_symbols,
)
from research.phase509_t15_t13_signal_audit import _build_bar_cache
from research.phase516_pbv2_best_classical_overlay import (
    OVERLAY_DEFS,
    _merge_or_candidates,
    _pbv2_precomputed_candidates,
    _prepare_runtime_env,
    _scan_overlay_day,
)
from research.phase517_o_r003_or_robustness_audit import (
    OrSimResult,
    _executed_trade_rows,
    _metrics_from_trades,
    _simulate_or_audited,
)
from research.phase518_day_high_winner_loser_separation import _build_micro_lookup, _extract_entry_features
from research.phase522_stop_low_mfe_reentry_overlay_edge_audit import _baseline_trade_rows
from research.phase524_live_reentry_guard_and_stop_low_mfe import _is_stop_low_mfe, _latest_live_day
from research.phase527_entry_quality_guard import _breakout_class, _guard_allows_entry, _is_mfe0
from research.phase530_winner_capture_research import (
    _avg_capture,
    _run_capture_day_job,
    _sym_key,
    _winner_capture_score,
)
from research.phase488_current_runtime_replay import _filter_period
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE532_VERDICT = "phase532_dual_path_entry_study_done"
MAX_WORKERS = 4

S0 = "S0_BASELINE"
S1 = "S1_PBV2_G9"
S2 = "S2_OR"
S3 = "S3_OR_F6"
S4 = "S4_DUAL_G9_F6"
S5 = "S5_DUAL_G9_OR_ONLY"

STRATEGIES = (S0, S1, S2, S3, S4, S5)

SUMMARY_FIELDS = [
    "strategy_id",
    "description",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trade_count",
    "win_rate",
    "avg_pnl_yen_100",
    "stop_low_mfe_count",
    "mfe0_count",
    "late_breakout_count",
    "high_chase_count",
    "winner_capture_score",
    "success_pass",
]

CAPTURE_FIELDS = [
    "strategy_id",
    "universe_type",
    "top_n",
    "capture_rate",
    "effective_capture_rate",
    "strong_capture_rate",
    "mfe_gt5_capture_rate",
    "winner_capture_score",
]

ATTRIBUTION_FIELDS = [
    "strategy_id",
    "attribution_class",
    "trade_count",
    "total_pnl_yen_100",
    "avg_mfe_pct",
    "avg_mae_pct",
    "symbols_sample",
    "days_sample",
]

DEPENDENCY_FIELDS = [
    "strategy_id",
    "exclusion_type",
    "remaining_pnl_yen_100",
    "remaining_pf",
    "remaining_max_dd_yen_100",
    "remaining_trades",
    "remains_positive",
]

CAP_COLLISION_FIELDS = [
    "strategy_id",
    "cap_block_count",
    "pbv2_replacement_count",
    "or_replacement_count",
    "replacement_pnl_yen_100",
    "net_substitution_pnl",
    "pbv2_trade_lost_count",
    "or_trade_added_count",
]


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _passes_g9(feats: Mapping[str, Any]) -> bool:
    return _guard_allows_entry(
        "G9_spread50_update5",
        {
            "spread": feats.get("spread"),
            "update_count_before_entry": feats.get("update_count_before_entry"),
        },
    )


def _passes_f6(feats: Mapping[str, Any]) -> bool:
    mins = feats.get("minutes_from_open")
    return mins is not None and float(mins) <= 150.0


def _filter_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    bar_cache: Mapping,
    micro_lookup: Mapping,
    predicate,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in candidates:
        feats = _extract_entry_features(c, bar_cache=bar_cache, micro_lookup=micro_lookup)
        if predicate(feats):
            out.append(dict(c))
    return out


def _merge_dual_or_only(
    pbv2_candidates: Sequence[Mapping[str, Any]],
    overlay_trades: Sequence[Mapping[str, Any]],
    *,
    bar_cache: Mapping,
    overlay_def,
    guard_c_block,
) -> list[dict[str, Any]]:
    """PBv2_G9 base + overlay-only entries (no OR upgrade on existing PBv2 keys)."""
    from datetime import datetime

    merged: dict[str, dict[str, Any]] = {}
    pbv2_keys: set[str] = set()
    for trade in pbv2_candidates:
        key = _position_key(trade)
        pbv2_keys.add(key)
        merged[key] = {
            **dict(trade),
            "_pbv2": True,
            "_overlay": False,
        }

    for trade in overlay_trades:
        key = _position_key(trade)
        if key in pbv2_keys or key in merged:
            continue
        merged[key] = {**dict(trade), "_pbv2": False, "_overlay": True}

    out: list[dict[str, Any]] = []
    for key in sorted(
        merged,
        key=lambda k: _parse_ts(str(merged[k].get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
    ):
        t = merged[key]
        if t.get("_pbv2") and guard_c_block(t):
            continue
        out.append(t)
    return out


def _build_strategy_candidates(
    strategy_id: str,
    *,
    pbv2_candidates: Sequence[Mapping[str, Any]],
    overlay_all: Sequence[Mapping[str, Any]],
    bar_cache: Mapping,
    micro_lookup: Mapping,
    overlay_def,
    guard_c_block,
) -> list[dict[str, Any]]:
    pbv2_g9 = _filter_candidates(pbv2_candidates, bar_cache=bar_cache, micro_lookup=micro_lookup, predicate=_passes_g9)
    overlay_f6 = _filter_candidates(overlay_all, bar_cache=bar_cache, micro_lookup=micro_lookup, predicate=_passes_f6)

    if strategy_id == S0:
        return [dict(c) for c in pbv2_candidates]
    if strategy_id == S1:
        return pbv2_g9
    if strategy_id == S2:
        return _merge_or_candidates(
            pbv2_candidates, overlay_all, bar_cache=bar_cache, overlay=overlay_def, guard_c_block=guard_c_block
        )
    if strategy_id == S3:
        return _merge_or_candidates(
            pbv2_candidates, overlay_f6, bar_cache=bar_cache, overlay=overlay_def, guard_c_block=guard_c_block
        )
    if strategy_id == S4:
        return _merge_or_candidates(
            pbv2_g9, overlay_f6, bar_cache=bar_cache, overlay=overlay_def, guard_c_block=guard_c_block
        )
    if strategy_id == S5:
        return _merge_dual_or_only(
            pbv2_g9, overlay_all, bar_cache=bar_cache, overlay_def=overlay_def, guard_c_block=guard_c_block
        )
    return []


def _enrich_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    strategy_id: str,
    price_idx: Mapping,
    bar_cache: Mapping,
    trade_by_key: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for r in trades:
        pk = str(r.get("position_key") or "")
        src = trade_by_key.get(pk, {})
        mfe = r.get("mfe_pct")
        mae = r.get("mae_pct")
        if mfe is None or mfe == "":
            mfe, mae = _mfe_mae_to_exit(src or r, price_idx=price_idx, exit_ts_iso=str(r.get("exit_time") or ""))
        row = {
            **dict(r),
            "strategy_id": strategy_id,
            "mfe_pct": mfe,
            "mae_pct": mae,
            "breakout_class": _breakout_class({**dict(r), "mfe_pct": mfe, "mae_pct": mae}, bar_cache),
        }
        rows.append(row)
    return rows


def _performance_summary(
    strategy_id: str,
    trades: Sequence[Mapping[str, Any]],
    *,
    capture_detail: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
    dependency_rows: Sequence[Mapping[str, Any]],
    cap_row: Mapping[str, Any],
) -> dict[str, Any]:
    met = _metrics_from_trades(trades, scenario_id=strategy_id)
    wcs = _winner_capture_score(capture_detail, strategy_id)
    desc = {
        S0: "BASELINE_RUNTIME",
        S1: "PBv2 + Phase528 G9",
        S2: "O_R003_OR",
        S3: "O_R003_OR + F6 (mins<=150)",
        S4: "Dual: PBv2_G9 + OR_F6",
        S5: "Dual: PBv2_G9 + OR_only",
    }.get(strategy_id, strategy_id)

    top10_ex = next((r for r in dependency_rows if r.get("exclusion_type") == "top10_trades"), {})
    top3_sym = next((r for r in dependency_rows if r.get("exclusion_type") == "top3_symbols"), {})

    success = (
        _num(met.get("total_pnl_yen_100")) >= _num(baseline.get("total_pnl_yen_100"))
        and _float(met.get("profit_factor")) >= _float(baseline.get("profit_factor"))
        and _float(met.get("max_drawdown_yen_100")) <= _float(baseline.get("max_drawdown_yen_100"))
        and wcs > _float(baseline.get("winner_capture_score"))
        and _num(top10_ex.get("remaining_pnl_yen_100")) > 0
        and _num(top3_sym.get("remaining_pnl_yen_100")) > 0
        and _float(cap_row.get("net_substitution_pnl")) > 0
    )

    return {
        "strategy_id": strategy_id,
        "description": desc,
        "total_pnl_yen_100": met.get("total_pnl_yen_100"),
        "profit_factor": met.get("profit_factor"),
        "max_drawdown_yen_100": met.get("max_drawdown_yen_100"),
        "trade_count": met.get("trades"),
        "win_rate": met.get("win_rate"),
        "avg_pnl_yen_100": met.get("avg_pnl_yen_100"),
        "stop_low_mfe_count": sum(1 for t in trades if _is_stop_low_mfe(t)),
        "mfe0_count": sum(1 for t in trades if _is_mfe0(t)),
        "late_breakout_count": sum(1 for t in trades if t.get("breakout_class") == "late_breakout"),
        "high_chase_count": sum(1 for t in trades if t.get("breakout_class") == "high_chase"),
        "winner_capture_score": wcs,
        "success_pass": success,
    }


def _capture_rows_for_strategy(
    strategy_id: str,
    trades: Sequence[Mapping[str, Any]],
    capture_detail: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    wcs = _winner_capture_score(capture_detail, strategy_id)
    mfes = [_num(t.get("mfe_pct")) for t in trades if t.get("mfe_pct") is not None]
    n = len(trades)
    mfe_gt1 = sum(1 for m in mfes if m > 1.0) / n if n else 0.0
    mfe_gt3 = sum(1 for m in mfes if m > 3.0) / n if n else 0.0
    mfe_gt5 = sum(1 for m in mfes if m > 5.0) / n if n else 0.0

    rows: list[dict[str, Any]] = []
    for universe_type, top_n in (("day_return", 10), ("day_return", 20)):
        rows.append(
            {
                "strategy_id": strategy_id,
                "universe_type": universe_type,
                "top_n": top_n,
                "capture_rate": _avg_capture(
                    capture_detail, strategy_id=strategy_id, universe_type=universe_type, top_n=top_n, field="capture_rate"
                ),
                "effective_capture_rate": _avg_capture(
                    capture_detail,
                    strategy_id=strategy_id,
                    universe_type=universe_type,
                    top_n=top_n,
                    field="effective_capture_rate",
                ),
                "strong_capture_rate": _avg_capture(
                    capture_detail,
                    strategy_id=strategy_id,
                    universe_type=universe_type,
                    top_n=top_n,
                    field="strong_capture_rate",
                ),
                "mfe_gt5_capture_rate": round(mfe_gt5, 4),
                "winner_capture_score": wcs,
            }
        )
    rows.append(
        {
            "strategy_id": strategy_id,
            "universe_type": "trade_mfe",
            "top_n": 0,
            "capture_rate": round(mfe_gt1, 4),
            "effective_capture_rate": round(mfe_gt1, 4),
            "strong_capture_rate": round(mfe_gt3, 4),
            "mfe_gt5_capture_rate": round(mfe_gt5, 4),
            "winner_capture_score": wcs,
        }
    )
    return rows


def _attribution_rows(
    strategy_id: str,
    trades: Sequence[Mapping[str, Any]],
    *,
    baseline_trades: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    base_keys = {_position_key(t) for t in baseline_trades}
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for t in trades:
        pbv2 = bool(t.get("accepted_by_pbv2"))
        overlay = bool(t.get("accepted_by_overlay"))
        pk = _position_key(t)
        if pbv2 and overlay:
            cls = "Both"
        elif pbv2:
            cls = "PBv2_only"
        elif overlay:
            cls = "OR_only"
        else:
            cls = "PBv2_only"
        if strategy_id in (S4, S5) and pk not in base_keys and overlay and not pbv2:
            buckets["Dual_only"].append(dict(t))
        buckets[cls].append(dict(t))

    rows: list[dict[str, Any]] = []
    for cls in ("PBv2_only", "OR_only", "Both", "Dual_only"):
        items = buckets.get(cls, [])
        if not items:
            continue
        mfes = [_num(t.get("mfe_pct")) for t in items if t.get("mfe_pct") is not None]
        maes = [_num(t.get("mae_pct")) for t in items if t.get("mae_pct") is not None]
        rows.append(
            {
                "strategy_id": strategy_id,
                "attribution_class": cls,
                "trade_count": len(items),
                "total_pnl_yen_100": round(sum(_num(t.get("pnl_yen_100")) for t in items), 2),
                "avg_mfe_pct": round(statistics.mean(mfes), 4) if mfes else None,
                "avg_mae_pct": round(statistics.mean(maes), 4) if maes else None,
                "symbols_sample": ",".join(sorted({_sym_key(t.get("symbol")) for t in items})[:5]),
                "days_sample": ",".join(sorted({str(t.get("day") or "")[:8] for t in items})[:5]),
            }
        )
    return rows


def _dependency_rows(
    strategy_id: str,
    trades: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    sym_pnl: dict[str, float] = defaultdict(float)
    day_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        sym_pnl[_sym_key(t.get("symbol"))] += _num(t.get("pnl_yen_100"))
        day_pnl[str(t.get("day") or "")[:8]] += _num(t.get("pnl_yen_100"))

    sym_rank = sorted(sym_pnl.items(), key=lambda x: x[1], reverse=True)
    day_rank = sorted(day_pnl.items(), key=lambda x: x[1], reverse=True)
    top10_keys = {
        _position_key(t)
        for t in sorted(trades, key=lambda x: _num(x.get("pnl_yen_100")), reverse=True)[:10]
    }

    def _remaining(ex_sym: set[str], ex_day: set[str], ex_keys: set[str]) -> dict[str, Any]:
        rem = [
            t
            for t in trades
            if _sym_key(t.get("symbol")) not in ex_sym
            and str(t.get("day") or "")[:8] not in ex_day
            and _position_key(t) not in ex_keys
        ]
        met = _metrics_from_trades(rem, scenario_id=strategy_id)
        return {
            "strategy_id": strategy_id,
            "exclusion_type": "",
            "remaining_pnl_yen_100": met.get("total_pnl_yen_100"),
            "remaining_pf": met.get("profit_factor"),
            "remaining_max_dd_yen_100": met.get("max_drawdown_yen_100"),
            "remaining_trades": met.get("trades"),
            "remains_positive": _num(met.get("total_pnl_yen_100")) > 0,
        }

    top3_trade_keys = {
        _position_key(t)
        for t in sorted(trades, key=lambda x: _num(x.get("pnl_yen_100")), reverse=True)[:3]
    }
    top1_trade_keys = top3_trade_keys and {_position_key(sorted(trades, key=lambda x: _num(x.get("pnl_yen_100")), reverse=True)[0])} or set()

    specs = [
        ("top1_trade", set(), set(), top1_trade_keys),
        ("top3_trades", set(), set(), top3_trade_keys),
        ("top10_trades", set(), set(), top10_keys),
        ("top1_symbol", {sym_rank[0][0]} if sym_rank else set(), set(), set()),
        ("top3_symbols", {s for s, _ in sym_rank[:3]}, set(), set()),
        ("top1_day", set(), {day_rank[0][0]} if day_rank else set(), set()),
        ("top3_days", set(), {d for d, _ in day_rank[:3]}, set()),
    ]
    rows: list[dict[str, Any]] = []
    for ex_type, ex_sym, ex_day, ex_keys in specs:
        if ex_type == "top1_trade" and not trades:
            continue
        row = _remaining(ex_sym, ex_day, ex_keys)
        row["exclusion_type"] = ex_type
        rows.append(row)
    return rows


def _cap_collision_summary(
    strategy_id: str,
    *,
    baseline_trades: Sequence[Mapping[str, Any]],
    scenario_trades: Sequence[Mapping[str, Any]],
    or_result: Optional[OrSimResult],
) -> dict[str, Any]:
    if strategy_id == S0:
        return {
            "strategy_id": S0,
            "cap_block_count": 0,
            "pbv2_replacement_count": 0,
            "or_replacement_count": 0,
            "replacement_pnl_yen_100": 0.0,
            "net_substitution_pnl": 0.0,
            "pbv2_trade_lost_count": 0,
            "or_trade_added_count": 0,
        }

    base_pnl = {_position_key(t): _num(t.get("pnl_yen_100")) for t in baseline_trades}
    scen_pnl = {_position_key(t): _num(t.get("pnl_yen_100")) for t in scenario_trades}
    lost = set(base_pnl) - set(scen_pnl)
    added = set(scen_pnl) - set(base_pnl)

    pbv2_lost = [k for k in lost if base_pnl.get(k, 0) != 0]
    or_added = [k for k in added if scen_pnl.get(k, 0) != 0]
    lost_pnl = round(sum(base_pnl.get(k, 0) for k in lost), 2)
    added_pnl = round(sum(scen_pnl.get(k, 0) for k in added), 2)

    cap_blocks = 0
    if or_result:
        cap_blocks = sum(1 for r in or_result.entry_audit if not r.accepted and r.reject_reason == "cap_full")

    pbv2_rep = sum(1 for k in lost if k in base_pnl)
    or_rep = len(added)

    return {
        "strategy_id": strategy_id,
        "cap_block_count": cap_blocks,
        "pbv2_replacement_count": pbv2_rep,
        "or_replacement_count": or_rep,
        "replacement_pnl_yen_100": round(lost_pnl + added_pnl, 2),
        "net_substitution_pnl": round(added_pnl + lost_pnl, 2),
        "pbv2_trade_lost_count": len(lost),
        "or_trade_added_count": len(added),
    }


def _mandatory_answers(
    *,
    summaries: Sequence[Mapping[str, Any]],
    baseline: Mapping[str, Any],
    capture_rows: Sequence[Mapping[str, Any]],
    attribution_rows: Sequence[Mapping[str, Any]],
    dependency_rows: Sequence[Mapping[str, Any]],
    cap_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    dual_rows = [r for r in summaries if r.get("strategy_id") in (S4, S5)]
    success_rows = [r for r in dual_rows if r.get("success_pass")]

    b_pnl = _num(baseline.get("total_pnl_yen_100"))
    b_pf = _float(baseline.get("profit_factor"))
    b_dd = _float(baseline.get("max_drawdown_yen_100"))
    b_wcs = _float(baseline.get("winner_capture_score"))

    def _beats_base(sid: str) -> bool:
        row = next((r for r in summaries if r.get("strategy_id") == sid), {})
        return (
            _num(row.get("total_pnl_yen_100")) >= b_pnl
            and _float(row.get("profit_factor")) >= b_pf
            and _float(row.get("max_drawdown_yen_100")) <= b_dd
            and _float(row.get("winner_capture_score")) > b_wcs
        )

    best_dual = max(dual_rows, key=lambda r: _float(r.get("winner_capture_score")), default={})
    best_dual_id = best_dual.get("strategy_id") or ""

    or_row = next((r for r in summaries if r.get("strategy_id") == S2), {})
    s4_row = next((r for r in summaries if r.get("strategy_id") == S4), {})
    s5_row = next((r for r in summaries if r.get("strategy_id") == S5), {})

    wcs_improved = any(
        _float(r.get("winner_capture_score")) > b_wcs for r in summaries if r.get("strategy_id") != S0
    )
    pnl_improved = any(_num(r.get("total_pnl_yen_100")) > b_pnl for r in summaries if r.get("strategy_id") != S0)
    pf_improved = any(_float(r.get("profit_factor")) > b_pf for r in summaries if r.get("strategy_id") != S0)
    dd_improved = any(
        _float(r.get("max_drawdown_yen_100")) < b_dd for r in summaries if r.get("strategy_id") != S0
    )

    def _top10_resolved(sid: str) -> bool:
        row = next(
            (r for r in dependency_rows if r.get("strategy_id") == sid and r.get("exclusion_type") == "top10_trades"),
            {},
        )
        base_row = next(
            (r for r in dependency_rows if r.get("strategy_id") == S0 and r.get("exclusion_type") == "top10_trades"),
            {},
        )
        return _num(row.get("remaining_pnl_yen_100")) > 0 and _num(row.get("remaining_pnl_yen_100")) >= _num(
            base_row.get("remaining_pnl_yen_100")
        )

    def _top3_sym_resolved(sid: str) -> bool:
        row = next(
            (r for r in dependency_rows if r.get("strategy_id") == sid and r.get("exclusion_type") == "top3_symbols"),
            {},
        )
        base_row = next(
            (r for r in dependency_rows if r.get("strategy_id") == S0 and r.get("exclusion_type") == "top3_symbols"),
            {},
        )
        return _num(row.get("remaining_pnl_yen_100")) > 0 and _num(row.get("remaining_pnl_yen_100")) >= _num(
            base_row.get("remaining_pnl_yen_100")
        )

    cap_ok = any(_float(r.get("net_substitution_pnl")) > 0 for r in cap_rows if r.get("strategy_id") in (S4, S5))

    dual_only = [r for r in attribution_rows if r.get("attribution_class") == "Dual_only"]
    separate_edge = any(int(r.get("trade_count") or 0) > 0 and _num(r.get("total_pnl_yen_100")) > 0 for r in dual_only)

    shadow_ok = bool(success_rows) or (_beats_base(S4) or _beats_base(S5))
    prod_ok = bool(success_rows)

    return {
        "1_dual_beats_baseline_candidate_exists": bool(success_rows) or _beats_base(S4) or _beats_base(S5),
        "2_best_dual_config": best_dual_id,
        "3_winner_capture_improves": wcs_improved,
        "4_pnl_improves": pnl_improved,
        "5_pf_improves": pf_improved,
        "6_dd_improves": dd_improved,
        "7_top10_dependency_resolved": _top10_resolved(S4) or _top10_resolved(S5),
        "8_top3_symbol_dependency_resolved": _top3_sym_resolved(S4) or _top3_sym_resolved(S5),
        "9_cap_collision_acceptable": cap_ok,
        "10_dual_is_separate_edge": separate_edge,
        "11_proceed_to_shadow_candidate": shadow_ok,
        "12_production_adoption_candidate": prod_ok,
        "success_strategies": [r.get("strategy_id") for r in success_rows],
        "s4_success_pass": bool(s4_row.get("success_pass")),
        "s5_success_pass": bool(s5_row.get("success_pass")),
        "or_winner_capture_score": or_row.get("winner_capture_score"),
        "baseline_winner_capture_score": b_wcs,
    }


def _render_doc(result: Mapping[str, Any]) -> str:
    ans = result.get("mandatory_answers") or {}
    lines = [
        "# Phase532 — Dual-Path Entry Study",
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
            "## Strategies",
            "",
            "- S0: BASELINE_RUNTIME",
            "- S1: PBv2 + G9",
            "- S2: O_R003_OR",
            "- S3: O_R003_OR + F6",
            "- S4: Dual PBv2_G9 + OR_F6",
            "- S5: Dual PBv2_G9 + OR_only",
            "",
            "Research only — no Runtime adoption.",
        ]
    )
    return "\n".join(lines) + "\n"


@dataclass
class Phase532Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        workers = min(max(1, self.max_workers), MAX_WORKERS)
        kabu = resolve_kabu_root(self.repo_root)
        period_end = _latest_live_day(self.repo_root)
        price_idx = _build_price_index_to(kabu, period_end=period_end)
        bar_cache, days = _build_bar_cache(self.repo_root)
        days = [d for d in days if d >= PERIOD_START and d <= period_end]
        replay_pool, runtime_shadows, guard_c_block = _prepare_runtime_env(self.repo_root)
        universe = _universe_symbols(_filter_period(replay_pool, start=PERIOD_START, end=period_end))
        micro_lookup = _build_micro_lookup(replay_pool)
        trade_by_key = {_position_key(t): t for t in replay_pool}

        pbv2_candidates = _pbv2_precomputed_candidates(replay_pool, runtime_shadows, guard_c_block)
        overlay_def = OVERLAY_DEFS["O_R003"]

        def _scan_day(day: str) -> list[dict[str, Any]]:
            return _scan_overlay_day(
                overlay_def,
                day=day,
                universe=universe,
                bar_cache=bar_cache,
                price_idx=price_idx,
            )

        scan_jobs = list(days)
        overlay_by_day: dict[str, list[dict[str, Any]]] = {}

        if self.parallel:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_scan_day, day): day for day in scan_jobs}
                for fut in as_completed(futs):
                    day = futs[fut]
                    overlay_by_day[day] = fut.result()
        else:
            for day in days:
                overlay_by_day[day] = _scan_day(day)

        overlay_all = [t for chunk in overlay_by_day.values() for t in chunk]

        baseline_state, _ = _run_baseline_runtime(self.repo_root)
        baseline_raw = _baseline_trade_rows(baseline_state, trade_by_key, price_idx)
        baseline_trades = _enrich_trades(
            [
                {
                    **dict(r),
                    "accepted_by_pbv2": True,
                    "accepted_by_overlay": False,
                }
                for r in baseline_raw
            ],
            strategy_id=S0,
            price_idx=price_idx,
            bar_cache=bar_cache,
            trade_by_key=trade_by_key,
        )

        trades_by_strategy: dict[str, list[dict[str, Any]]] = {S0: baseline_trades}
        sim_results: dict[str, OrSimResult] = {}

        sim_jobs = [sid for sid in STRATEGIES if sid != S0]

        def _sim_strategy(sid: str) -> tuple[str, list[dict[str, Any]], OrSimResult]:
            candidates = _build_strategy_candidates(
                sid,
                pbv2_candidates=pbv2_candidates,
                overlay_all=overlay_all,
                bar_cache=bar_cache,
                micro_lookup=micro_lookup,
                overlay_def=overlay_def,
                guard_c_block=guard_c_block,
            )
            result = _simulate_or_audited(candidates, mode=f"phase532_{sid.lower()}")
            raw = _executed_trade_rows(result.state, sid)
            enriched = _enrich_trades(raw, strategy_id=sid, price_idx=price_idx, bar_cache=bar_cache, trade_by_key=trade_by_key)
            return sid, enriched, result

        if self.parallel and sim_jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_sim_strategy, sid): sid for sid in sim_jobs}
                for fut in as_completed(futs):
                    sid, enriched, result = fut.result()
                    trades_by_strategy[sid] = enriched
                    sim_results[sid] = result
        else:
            for sid in sim_jobs:
                sid, enriched, result = _sim_strategy(sid)
                trades_by_strategy[sid] = enriched
                sim_results[sid] = result

        capture_jobs = [(day, sid) for day in days for sid in STRATEGIES]
        capture_detail: list[dict[str, Any]] = []

        def _cap_job(day: str, sid: str) -> list[dict[str, Any]]:
            return _run_capture_day_job(
                day,
                sid,
                trades_by_strategy.get(sid, []),
                price_idx=price_idx,
                bar_cache=bar_cache,
                universe=universe,
            )

        if self.parallel and capture_jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_cap_job, day, sid): (day, sid) for day, sid in capture_jobs}
                for fut in as_completed(futs):
                    capture_detail.extend(fut.result())
        else:
            for day, sid in capture_jobs:
                capture_detail.extend(_cap_job(day, sid))

        dependency_all: list[dict[str, Any]] = []
        cap_all: list[dict[str, Any]] = []
        attribution_all: list[dict[str, Any]] = []
        capture_summary: list[dict[str, Any]] = []

        for sid in STRATEGIES:
            trades = trades_by_strategy.get(sid, [])
            dependency_all.extend(_dependency_rows(sid, trades))
            cap_all.append(
                _cap_collision_summary(
                    sid,
                    baseline_trades=baseline_trades,
                    scenario_trades=trades,
                    or_result=sim_results.get(sid),
                )
            )
            attribution_all.extend(_attribution_rows(sid, trades, baseline_trades=baseline_trades))
            capture_summary.extend(_capture_rows_for_strategy(sid, trades, capture_detail))

        baseline_summary = _performance_summary(
            S0,
            baseline_trades,
            capture_detail=capture_detail,
            baseline={
                "total_pnl_yen_100": _metrics_from_trades(baseline_trades).get("total_pnl_yen_100"),
                "profit_factor": _metrics_from_trades(baseline_trades).get("profit_factor"),
                "max_drawdown_yen_100": _metrics_from_trades(baseline_trades).get("max_drawdown_yen_100"),
                "winner_capture_score": _winner_capture_score(capture_detail, S0),
            },
            dependency_rows=[r for r in dependency_all if r.get("strategy_id") == S0],
            cap_row=next((r for r in cap_all if r.get("strategy_id") == S0), {}),
        )

        summaries: list[dict[str, Any]] = [baseline_summary]
        for sid in STRATEGIES:
            if sid == S0:
                continue
            trades = trades_by_strategy.get(sid, [])
            cap_row = next((r for r in cap_all if r.get("strategy_id") == sid), {})
            dep_rows = [r for r in dependency_all if r.get("strategy_id") == sid]
            summaries.append(
                _performance_summary(
                    sid,
                    trades,
                    capture_detail=capture_detail,
                    baseline=baseline_summary,
                    dependency_rows=dep_rows,
                    cap_row=cap_row,
                )
            )

        mandatory = _mandatory_answers(
            summaries=summaries,
            baseline=baseline_summary,
            capture_rows=capture_summary,
            attribution_rows=attribution_all,
            dependency_rows=dependency_all,
            cap_rows=cap_all,
        )

        return {
            "verdict": PHASE532_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": period_end,
            "includes_20260624": "20260624" in days,
            "parallel_workers": workers,
            "days_count": len(days),
            "summary": summaries,
            "capture": capture_summary,
            "attribution": attribution_all,
            "dependency": dependency_all,
            "cap_collision": cap_all,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(kabu)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "summary": reports / "phase532_dual_path_summary.csv",
            "capture": reports / "phase532_dual_path_capture.csv",
            "attribution": reports / "phase532_dual_path_attribution.csv",
            "dependency": reports / "phase532_dual_path_dependency.csv",
            "cap": reports / "phase532_dual_path_cap_collision.csv",
            "report": reports / "phase532_report.json",
            "docs": kabu / "docs" / "operations" / "phase532_dual_path_entry_study.md",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary") or []))
        _write_csv(paths["capture"], CAPTURE_FIELDS, list(result.get("capture") or []))
        _write_csv(paths["attribution"], ATTRIBUTION_FIELDS, list(result.get("attribution") or []))
        _write_csv(paths["dependency"], DEPENDENCY_FIELDS, list(result.get("dependency") or []))
        _write_csv(paths["cap"], CAP_COLLISION_FIELDS, list(result.get("cap_collision") or []))
        report_body = {k: v for k, v in result.items()}
        paths["report"].write_text(json.dumps(report_body, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        paths["docs"].parent.mkdir(parents=True, exist_ok=True)
        paths["docs"].write_text(_render_doc(result), encoding="utf-8")
        return paths
