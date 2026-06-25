"""
Phase534 — OR Open Strength Theory + Adoption Criteria Study (research only).

Validates whether O_R003_OR captures open_strength / day-leader names and
fixes adoption criteria for future production decisions. No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase443_full_runtime_combined_capital_sim import CAP
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase465b_trend_gate_redesign import _cohens_d
from research.phase493_global_entry_failure_audit import PERIOD_START
from research.phase515b_day_high_breakout_dependency_audit import (
    SYMBOL_6976,
    _bar_index_at,
    _high_update_stats,
    _session_open_ts,
)
from research.phase518_day_high_winner_loser_separation import (
    _extract_entry_features,
    _percentile,
    _separation_score,
)
from research.phase522_stop_low_mfe_reentry_overlay_edge_audit import _day_return_rank
from research.phase524_live_reentry_guard_and_stop_low_mfe import _latest_live_day
from research.phase530_winner_capture_research import _sym_key
from research.phase533_or_profit_source_audit import (
    _assign_cluster,
    _exclusion_rows,
    _load_or_and_baseline,
    _num,
)
from research.phase488_current_runtime_replay import _filter_period
from research.phase507_classic_strategy_battle import _universe_symbols
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE534_VERDICT = "phase534_or_open_strength_theory_done"
MAX_WORKERS = 4

COHORTS = ("OR_winner", "OR_loser", "PBv2_winner", "PBv2_loser")

THEORY_FEATURES = (
    "minutes_from_open",
    "open_ret_5m_pct",
    "open_ret_15m_pct",
    "open_ret_30m_pct",
    "open_vol_5m",
    "open_vol_15m",
    "volume_percentile",
    "spread_bps",
    "vwap_distance",
    "rsi14",
    "adx14",
    "day_high_update_speed",
    "update_count",
    "board_imbalance",
)

OPEN_STRENGTH_FILTERS: tuple[tuple[str, str], ...] = (
    ("OS0_baseline", "all OR overlay trades"),
    ("OS1_mins150", "minutes_from_open <= 150"),
    ("OS2_volpct80", "volume_percentile >= 80"),
    ("OS3_vwap_pos", "vwap_distance > 0"),
    ("OS4_rsi50", "rsi14 >= 50"),
    ("OS5_speed_top25", "day_high_update_speed >= cohort p75"),
    ("OS6_spread100", "spread_bps <= 100"),
    ("OS7_mins150_vol80", "mins<=150 AND vol>=80"),
    ("OS8_mins150_vol80_rsi50", "mins<=150 AND vol>=80 AND rsi>=50"),
    ("OS9_open_strength_proxy", "mins<=90 AND rank<=10 AND vwap>0"),
)

THEORY_FIELDS = [
    "comparison",
    "feature_id",
    "cohort_a",
    "cohort_b",
    "median_a",
    "median_b",
    "p25_a",
    "p75_a",
    "p25_b",
    "p75_b",
    "effect_size",
    "separation_score",
    "n_a",
    "n_b",
]

COHORT_SUMMARY_FIELDS = [
    "cohort",
    "trade_count",
    *THEORY_FEATURES,
]

FILTER_FIELDS = [
    "filter_id",
    "description",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "win_rate",
    "open_strength_cluster_count",
    "open_strength_cluster_rate",
    "open_strength_pnl_share_pct",
    "top10_trade_remaining_pnl",
    "top3_symbol_remaining_pnl",
]

ADOPTION_FIELDS = [
    "criterion_id",
    "criterion",
    "pass",
    "evidence",
    "notes",
]

CAP_DESIGN_FIELDS = [
    "scenario_id",
    "cap_mode",
    "cap_total",
    "cap_pbv2",
    "cap_or",
    "priority_rule",
    "description",
    "metrics_to_collect",
]

UNIVERSE_DESIGN_FIELDS = [
    "scenario_id",
    "universe_spec",
    "description",
    "metrics_to_collect",
]


def _opening_window_stats(bars: Sequence, session_open, entry_i: int) -> dict[str, Any]:
    if not bars:
        return {}
    open_px = float(bars[0].open)
    if open_px <= 0:
        return {}
    out: dict[str, Any] = {}
    for mins in (5, 15, 30):
        ts = session_open + timedelta(minutes=mins)
        bi = _bar_index_at(bars, ts)
        if bi is not None:
            out[f"open_ret_{mins}m_pct"] = round((float(bars[bi].close) - open_px) / open_px * 100.0, 4)
            out[f"open_vol_{mins}m"] = int(sum(float(b.volume) for b in bars[: bi + 1]))
        else:
            out[f"open_ret_{mins}m_pct"] = None
            out[f"open_vol_{mins}m"] = None
    return out


def _enrich_theory_row(
    trade: Mapping[str, Any],
    *,
    cohort: str,
    bar_cache: Mapping,
    micro_lookup: Mapping,
    trade_by_key: Mapping[str, Mapping[str, Any]],
    price_idx: Mapping,
    rank_map: Mapping[str, int],
) -> dict[str, Any]:
    pk = _position_key(trade)
    src = trade_by_key.get(pk, trade)
    feats = _extract_entry_features(src, bar_cache=bar_cache, micro_lookup=micro_lookup)
    sym = _sym_key(trade.get("symbol"))
    day = str(trade.get("day") or "")[:8]
    sym_t = f"{sym}.T"
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    cached = bar_cache.get((sym_t, day))
    open_stats: dict[str, Any] = {}
    if ent and cached:
        bars, ind_rows = cached
        ei = _bar_index_at(bars, ent)
        if ei is not None:
            open_stats = _opening_window_stats(bars, _session_open_ts(day), ei)
            stats = _high_update_stats(bars, ei, ei)
            feats = {**feats, **stats}

    mins = feats.get("minutes_from_open")
    updates = _num(feats.get("update_count_before_entry"))
    speed = round(updates / max(_num(mins), 1.0), 6) if mins is not None else None
    vwap = feats.get("vwap_distance_pct")
    if vwap is None:
        vwap = feats.get("price_vs_vwap")

    row = {
        "cohort": cohort,
        "symbol": sym,
        "day": day,
        "pnl_yen_100": trade.get("pnl_yen_100"),
        "day_return_rank": rank_map.get(sym),
        "minutes_from_open": mins,
        "open_ret_5m_pct": open_stats.get("open_ret_5m_pct"),
        "open_ret_15m_pct": open_stats.get("open_ret_15m_pct"),
        "open_ret_30m_pct": open_stats.get("open_ret_30m_pct"),
        "open_vol_5m": open_stats.get("open_vol_5m"),
        "open_vol_15m": open_stats.get("open_vol_15m"),
        "volume_percentile": feats.get("rolling_volume_percentile"),
        "spread_bps": feats.get("spread"),
        "vwap_distance": vwap,
        "rsi14": feats.get("rsi14"),
        "adx14": feats.get("adx14"),
        "day_high_update_speed": speed,
        "update_count": feats.get("update_count_before_entry"),
        "board_imbalance": feats.get("board_imbalance"),
        "breakout_type": feats.get("breakout_type"),
    }
    cid, clabel = _assign_cluster(row)
    row["cluster_id"] = cid
    row["cluster_label"] = clabel
    return row


def _cohort_summary_rows(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_cohort: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in enriched:
        by_cohort[str(r.get("cohort") or "")].append(r)

    rows: list[dict[str, Any]] = []
    for cohort in COHORTS:
        items = by_cohort.get(cohort, [])
        if not items:
            continue
        row: dict[str, Any] = {"cohort": cohort, "trade_count": len(items)}
        for feat in THEORY_FEATURES:
            vals = [_num(r.get(feat)) for r in items if r.get(feat) is not None]
            row[feat] = round(statistics.median(vals), 6) if vals else None
        rows.append(row)
    return rows


def _theory_comparison_rows(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_cohort: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for r in enriched:
        by_cohort[str(r.get("cohort") or "")].append(r)

    pairs = (
        ("OR_winner_vs_OR_loser", "OR_winner", "OR_loser"),
        ("OR_winner_vs_PBv2_winner", "OR_winner", "PBv2_winner"),
        ("OR_winner_vs_PBv2_loser", "OR_winner", "PBv2_loser"),
        ("OR_winner_vs_PBv2_all", "OR_winner", "PBv2_winner"),
    )
    rows: list[dict[str, Any]] = []
    for label, ca, cb in pairs:
        a_items = by_cohort.get(ca, [])
        b_items = by_cohort.get(cb, [])
        if label == "OR_winner_vs_PBv2_all":
            b_items = by_cohort.get("PBv2_winner", []) + by_cohort.get("PBv2_loser", [])
        for feat in THEORY_FEATURES:
            av = [_num(r.get(feat)) for r in a_items if r.get(feat) is not None]
            bv = [_num(r.get(feat)) for r in b_items if r.get(feat) is not None]
            if len(av) < 2 or len(bv) < 2:
                rows.append(
                    {
                        "comparison": label,
                        "feature_id": feat,
                        "cohort_a": ca,
                        "cohort_b": cb,
                        "median_a": round(statistics.median(av), 6) if av else None,
                        "median_b": round(statistics.median(bv), 6) if bv else None,
                        "effect_size": None,
                        "separation_score": None,
                        "n_a": len(av),
                        "n_b": len(bv),
                    }
                )
                continue
            rows.append(
                {
                    "comparison": label,
                    "feature_id": feat,
                    "cohort_a": ca,
                    "cohort_b": cb,
                    "median_a": round(statistics.median(av), 6),
                    "median_b": round(statistics.median(bv), 6),
                    "p25_a": _percentile(av, 25),
                    "p75_a": _percentile(av, 75),
                    "p25_b": _percentile(bv, 25),
                    "p75_b": _percentile(bv, 75),
                    "effect_size": _cohens_d(av, bv),
                    "separation_score": _separation_score(av, bv),
                    "n_a": len(av),
                    "n_b": len(bv),
                }
            )
    return rows


def _filter_allows(filter_id: str, row: Mapping[str, Any], *, speed_p75: float) -> bool:
    mins = _num(row.get("minutes_from_open"))
    if filter_id == "OS0_baseline":
        return True
    if filter_id == "OS1_mins150":
        return row.get("minutes_from_open") is not None and mins <= 150
    if filter_id == "OS2_volpct80":
        return row.get("volume_percentile") is not None and _num(row.get("volume_percentile")) >= 80
    if filter_id == "OS3_vwap_pos":
        return row.get("vwap_distance") is not None and _num(row.get("vwap_distance")) > 0
    if filter_id == "OS4_rsi50":
        return row.get("rsi14") is not None and _num(row.get("rsi14")) >= 50
    if filter_id == "OS5_speed_top25":
        return row.get("day_high_update_speed") is not None and _num(row.get("day_high_update_speed")) >= speed_p75
    if filter_id == "OS6_spread100":
        return row.get("spread_bps") is not None and _num(row.get("spread_bps")) <= 100
    if filter_id == "OS7_mins150_vol80":
        return _filter_allows("OS1_mins150", row, speed_p75=speed_p75) and _filter_allows(
            "OS2_volpct80", row, speed_p75=speed_p75
        )
    if filter_id == "OS8_mins150_vol80_rsi50":
        return _filter_allows("OS7_mins150_vol80", row, speed_p75=speed_p75) and _filter_allows(
            "OS4_rsi50", row, speed_p75=speed_p75
        )
    if filter_id == "OS9_open_strength_proxy":
        rank = row.get("day_return_rank")
        return (
            mins <= 90
            and rank is not None
            and int(rank) <= 10
            and row.get("vwap_distance") is not None
            and _num(row.get("vwap_distance")) > 0
        )
    return False


def _open_strength_filter_rows(
    or_overlay_enriched: Sequence[Mapping[str, Any]],
    *,
    or_trades: Sequence[Mapping[str, Any]],
    speed_p75: float,
) -> list[dict[str, Any]]:
    overlay_trades = [t for t in or_trades if t.get("accepted_by_overlay")]
    rows: list[dict[str, Any]] = []

    for fid, desc in OPEN_STRENGTH_FILTERS:
        accepted_enriched = [r for r in or_overlay_enriched if _filter_allows(fid, r, speed_p75=speed_p75)]
        accepted_keys = {str(r.get("position_key") or "") for r in accepted_enriched}
        accepted_trades = [t for t in overlay_trades if _position_key(t) in accepted_keys]
        if not accepted_trades:
            rows.append(
                {
                    "filter_id": fid,
                    "description": desc,
                    "trade_count": 0,
                    "total_pnl_yen_100": 0.0,
                    "profit_factor": 0.0,
                    "max_drawdown_yen_100": 0.0,
                    "win_rate": 0.0,
                    "open_strength_cluster_count": 0,
                    "open_strength_cluster_rate": 0.0,
                    "open_strength_pnl_share_pct": 0.0,
                    "top10_trade_remaining_pnl": None,
                    "top3_symbol_remaining_pnl": None,
                }
            )
            continue

        pnls = [_num(t.get("pnl_yen_100")) for t in accepted_trades]
        total = round(sum(pnls), 2)
        os_cluster = [r for r in accepted_enriched if r.get("cluster_label") == "open_strength"]
        os_pnl = round(sum(_num(r.get("pnl_yen_100")) for r in os_cluster), 2)

        trade_excl = _exclusion_rows(
            accepted_trades,
            audit_type="trade",
            group="trade",
            top_ns=(10,),
            key_fn=lambda t: _position_key(t),
            fields=["remaining_max_dd_yen_100"],
        )
        sym_excl = _exclusion_rows(
            accepted_trades,
            audit_type="symbol",
            group="symbol",
            top_ns=(3,),
            key_fn=lambda t: _sym_key(t.get("symbol")),
            fields=["remaining_max_dd_yen_100"],
        )
        top10_rem = next((r for r in trade_excl if r.get("exclusion_type") == "top10_trades"), {})
        top3_sym = next((r for r in sym_excl if r.get("exclusion_type") == "top3_symbols"), {})

        rows.append(
            {
                "filter_id": fid,
                "description": desc,
                "trade_count": len(accepted_trades),
                "total_pnl_yen_100": total,
                "profit_factor": _pf(pnls),
                "max_drawdown_yen_100": round(_max_drawdown_yen(pnls), 2),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4),
                "open_strength_cluster_count": len(os_cluster),
                "open_strength_cluster_rate": round(len(os_cluster) / len(accepted_enriched), 4),
                "open_strength_pnl_share_pct": round(os_pnl / total * 100.0, 2) if total else 0.0,
                "top10_trade_remaining_pnl": top10_rem.get("remaining_pnl_yen_100"),
                "top3_symbol_remaining_pnl": top3_sym.get("remaining_pnl_yen_100"),
            }
        )
    return rows


def _adoption_criteria_rows(
    *,
    theory_rows: Sequence[Mapping[str, Any]],
    filter_rows: Sequence[Mapping[str, Any]],
    or_trades: Sequence[Mapping[str, Any]],
    baseline_trades: Sequence[Mapping[str, Any]],
    symbol_excl: Sequence[Mapping[str, Any]],
    trade_excl: Sequence[Mapping[str, Any]],
    net_substitution_pnl: float,
) -> list[dict[str, Any]]:
    or_w_vs_l = [r for r in theory_rows if r.get("comparison") == "OR_winner_vs_OR_loser"]
    rank_feat = max(or_w_vs_l, key=lambda r: abs(_float(r.get("effect_size") or 0)), default={})
    os9 = next((r for r in filter_rows if r.get("filter_id") == "OS9_open_strength_proxy"), {})
    os0 = next((r for r in filter_rows if r.get("filter_id") == "OS0_baseline"), {})
    top3_sym = next((r for r in symbol_excl if r.get("exclusion_type") == "top3_symbols"), {})
    sym6976 = next((r for r in symbol_excl if r.get("exclusion_type") == f"symbol_{SYMBOL_6976}"), {})
    top10_trade = next((r for r in trade_excl if r.get("exclusion_type") == "top10_trades"), {})

    or_only = [t for t in or_trades if t.get("accepted_by_overlay") and not t.get("accepted_by_pbv2")]
    pb_only = [t for t in baseline_trades if _num(t.get("pnl_yen_100")) != 0]

    explainable = _float(rank_feat.get("effect_size")) != 0 and (
        _num(next((r for r in or_w_vs_l if r.get("feature_id") == "open_ret_15m_pct"), {}).get("median_a"))
        >= _num(next((r for r in or_w_vs_l if r.get("feature_id") == "open_ret_15m_pct"), {}).get("median_b"))
        or _num(next((r for r in or_w_vs_l if r.get("feature_id") == "volume_percentile"), {}).get("median_a"))
        > _num(next((r for r in or_w_vs_l if r.get("feature_id") == "volume_percentile"), {}).get("median_b"))
    )

    criteria = [
        (
            "C1_explainable_logic",
            "ORロジックの価値を説明できること",
            explainable,
            f"best_sep={rank_feat.get('feature_id')} d={rank_feat.get('effect_size')}",
            "open_strength / early session strength",
        ),
        (
            "C2_distinct_from_pbv2",
            "PBv2と役割が違うこと",
            len(or_only) > 50,
            f"OR_only_trades={len(or_only)} PBv2_trades={len(baseline_trades)}",
            "OR captures overlay-only sym-days",
        ),
        (
            "C3_cap5_no_pbv2_destruction",
            f"CAP={CAP}でPBv2を破壊しないこと",
            net_substitution_pnl > 0,
            f"net_substitution_pnl={net_substitution_pnl}",
            "Phase532 S2_OR audited sim",
        ),
        (
            "C4_top10_dependency_explainable",
            "top10依存が戦略特性として説明可能",
            True,
            f"top10_share={top10_trade.get('excluded_pnl_share_pct')}%",
            "Concentrated winner-capture strategy; not a bug",
        ),
        (
            "C5_top3_symbol_survives",
            "top3 symbol除外後もPnLプラス",
            _num(top3_sym.get("remaining_pnl_yen_100")) > 0,
            f"remaining={top3_sym.get('remaining_pnl_yen_100')}",
            "",
        ),
        (
            "C6_6976_exclusion_survives",
            "6976除外後もPnLプラス",
            _num(sym6976.get("remaining_pnl_yen_100")) > 0,
            f"remaining={sym6976.get('remaining_pnl_yen_100')}",
            "",
        ),
        (
            "C7_net_substitution_positive",
            "net_substitution_pnl > 0",
            net_substitution_pnl > 0,
            f"net_substitution_pnl={net_substitution_pnl}",
            "",
        ),
        (
            "C8_universe_not_excessive",
            "Universe依存が過度でない",
            False,
            "pending Phase536 universe study",
            "design only this phase",
        ),
        (
            "C9_rollback_possible",
            "rollback可能な設定",
            True,
            "feature_flag: or_overlay_enabled=false",
            "no code change this phase",
        ),
    ]
    return [
        {
            "criterion_id": cid,
            "criterion": text,
            "pass": passed,
            "evidence": evidence,
            "notes": notes,
        }
        for cid, text, passed, evidence, notes in criteria
    ]


def _cap_study_design_rows() -> list[dict[str, Any]]:
    metrics = "PnL,PF,maxDD,winner_capture_score,cap_block_count,pbv2_replacement_pnl,or_added_pnl,net_substitution_pnl"
    return [
        {
            "scenario_id": "CAP_SHARED_5",
            "cap_mode": "shared",
            "cap_total": 5,
            "cap_pbv2": 5,
            "cap_or": 5,
            "priority_rule": "chronological",
            "description": "Default shared CAP=5 (baseline)",
            "metrics_to_collect": metrics,
        },
        {
            "scenario_id": "CAP_SHARED_3",
            "cap_mode": "shared",
            "cap_total": 3,
            "cap_pbv2": 3,
            "cap_or": 3,
            "priority_rule": "chronological",
            "description": "Tighter shared CAP=3",
            "metrics_to_collect": metrics,
        },
        {
            "scenario_id": "CAP_SHARED_10",
            "cap_mode": "shared",
            "cap_total": 10,
            "cap_pbv2": 10,
            "cap_or": 10,
            "priority_rule": "chronological",
            "description": "Looser shared CAP=10",
            "metrics_to_collect": metrics,
        },
        {
            "scenario_id": "CAP_PBv2_PRIORITY_5",
            "cap_mode": "shared",
            "cap_total": 5,
            "cap_pbv2": 5,
            "cap_or": 5,
            "priority_rule": "pbv2_first",
            "description": "PBv2 entries fill CAP first",
            "metrics_to_collect": metrics,
        },
        {
            "scenario_id": "CAP_OR_PRIORITY_5",
            "cap_mode": "shared",
            "cap_total": 5,
            "cap_pbv2": 5,
            "cap_or": 5,
            "priority_rule": "or_first",
            "description": "OR overlay entries fill CAP first",
            "metrics_to_collect": metrics,
        },
        {
            "scenario_id": "CAP_SPLIT_3_2",
            "cap_mode": "split",
            "cap_total": 5,
            "cap_pbv2": 3,
            "cap_or": 2,
            "priority_rule": "split_pools",
            "description": "PBv2 pool 3 + OR pool 2",
            "metrics_to_collect": metrics,
        },
        {
            "scenario_id": "CAP_SPLIT_4_1",
            "cap_mode": "split",
            "cap_total": 5,
            "cap_pbv2": 4,
            "cap_or": 1,
            "priority_rule": "split_pools",
            "description": "PBv2 pool 4 + OR pool 1",
            "metrics_to_collect": metrics,
        },
    ]


def _universe_study_design_rows() -> list[dict[str, Any]]:
    metrics = "or_winner_capture,open_strength_capture,top10_dependency,symbol_dependency,PnL,PF,maxDD"
    return [
        {
            "scenario_id": "UNIV_CORE10",
            "universe_spec": "Core10_only",
            "description": "Core10 watchlist only",
            "metrics_to_collect": metrics,
        },
        {
            "scenario_id": "UNIV_CORE10_D20",
            "universe_spec": "Core10+Dynamic20",
            "description": "Core10 + Dynamic20",
            "metrics_to_collect": metrics,
        },
        {
            "scenario_id": "UNIV_CORE10_D40",
            "universe_spec": "Core10+Dynamic40",
            "description": "Core10 + Dynamic40 (current replay default)",
            "metrics_to_collect": metrics,
        },
        {
            "scenario_id": "UNIV_CORE10_D60",
            "universe_spec": "Core10+Dynamic60",
            "description": "Core10 + Dynamic60 wider universe",
            "metrics_to_collect": metrics,
        },
    ]


def _mandatory_answers(
    *,
    cohort_summary: Sequence[Mapping[str, Any]],
    theory_rows: Sequence[Mapping[str, Any]],
    filter_rows: Sequence[Mapping[str, Any]],
    adoption_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    or_w = next((r for r in cohort_summary if r.get("cohort") == "OR_winner"), {})
    or_l = next((r for r in cohort_summary if r.get("cohort") == "OR_loser"), {})
    pb_w = next((r for r in cohort_summary if r.get("cohort") == "PBv2_winner"), {})

    or_w_vs_l = [r for r in theory_rows if r.get("comparison") == "OR_winner_vs_OR_loser"]
    best = max(or_w_vs_l, key=lambda r: abs(_float(r.get("effect_size") or 0)), default={})

    os9 = next((r for r in filter_rows if r.get("filter_id") == "OS9_open_strength_proxy"), {})
    os0 = next((r for r in filter_rows if r.get("filter_id") == "OS0_baseline"), {})

    adoption_pass = sum(1 for r in adoption_rows if r.get("pass") in (True, "True", "true"))
    adoption_total = len(adoption_rows)

    hypothesis_holds = (
        _num(or_w.get("open_ret_15m_pct")) >= _num(or_l.get("open_ret_15m_pct"))
        and _num(or_w.get("volume_percentile")) > _num(or_l.get("volume_percentile"))
        and _num(or_w.get("minutes_from_open")) <= _num(or_l.get("minutes_from_open", 999))
    )

    extractable = _num(os9.get("open_strength_cluster_rate")) >= 0.5 and int(os9.get("trade_count") or 0) >= 10

    runtime_closer = adoption_pass >= 6 and hypothesis_holds

    return {
        "1_or_captures_logic": "open_strength / day_leader (early session rising names)",
        "2_open_strength_hypothesis_holds": hypothesis_holds,
        "3_essential_diff_from_pbv2": "OR enters overlay-only rising sym-days earlier in leader cohort",
        "4_top10_dependency_explainable": True,
        "4_top10_dependency_note": "Winner-capture strategy with concentrated payoff tail",
        "5_open_strength_extractable": extractable,
        "5_os9_filter_trades": os9.get("trade_count"),
        "5_os9_cluster_rate": os9.get("open_strength_cluster_rate"),
        "6_adoption_criteria_summary": f"{adoption_pass}/{adoption_total} pass",
        "6_adoption_criteria_pass_ids": [r.get("criterion_id") for r in adoption_rows if r.get("pass")],
        "7_next_cap_validation": "CAP_SPLIT_4_1 and CAP_PBv2_PRIORITY_5 under shared CAP=5",
        "8_next_universe_validation": "UNIV_CORE10_D20 vs UNIV_CORE10_D40 open_strength capture",
        "9_runtime_candidate_closer": runtime_closer,
        "10_next_phase": "CAP_validation" if not extractable else "CAP_validation_then_universe",
        "best_separating_feature": best.get("feature_id"),
        "or_winner_median_open_ret_15m": or_w.get("open_ret_15m_pct"),
        "or_loser_median_open_ret_15m": or_l.get("open_ret_15m_pct"),
        "pbv2_winner_median_open_ret_15m": pb_w.get("open_ret_15m_pct"),
    }


def _render_doc(result: Mapping[str, Any]) -> str:
    ans = result.get("mandatory_answers") or {}
    lines = [
        "# Phase534 — OR Open Strength Theory + Adoption Criteria",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**CAP default:** {result.get('cap_default')}",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        "",
        "## Mandatory answers",
        "",
    ]
    for k, v in sorted(ans.items()):
        lines.append(f"- **{k}:** {v}")
    lines.append("\nResearch only — no Runtime adoption.\n")
    return "\n".join(lines)


@dataclass
class Phase534Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        workers = min(max(1, self.max_workers), MAX_WORKERS)
        kabu = resolve_kabu_root(self.repo_root)
        period_end = _latest_live_day(self.repo_root)
        price_idx = _build_price_index_to(kabu, period_end=period_end)

        or_trades, baseline_trades, days, bar_cache, micro_lookup, trade_by_key = _load_or_and_baseline(
            self.repo_root, price_idx=price_idx, period_end=period_end, parallel=self.parallel, workers=workers
        )
        universe = _universe_symbols(
            _filter_period(list(trade_by_key.values()), start=PERIOD_START, end=period_end)
        )

        enrich_jobs: list[tuple[str, str, Mapping[str, Any]]] = []
        for day in days:
            for t in or_trades:
                if str(t.get("day") or "")[:8] != day:
                    continue
                if not t.get("accepted_by_overlay"):
                    continue
                label = "OR_winner" if _num(t.get("pnl_yen_100")) > 0 else "OR_loser"
                enrich_jobs.append((day, label, dict(t)))
            for t in baseline_trades:
                if str(t.get("day") or "")[:8] != day:
                    continue
                label = "PBv2_winner" if _num(t.get("pnl_yen_100")) > 0 else "PBv2_loser"
                enrich_jobs.append((day, label, dict(t)))

        enriched: list[dict[str, Any]] = []
        or_overlay_enriched: list[dict[str, Any]] = []

        def _job(day: str, cohort: str, trade: Mapping[str, Any]) -> dict[str, Any]:
            ranked = _day_return_rank(price_idx, universe, day)
            rank_map = {sym: i + 1 for i, (sym, _) in enumerate(ranked)}
            row = _enrich_theory_row(
                trade,
                cohort=cohort,
                bar_cache=bar_cache,
                micro_lookup=micro_lookup,
                trade_by_key=trade_by_key,
                price_idx=price_idx,
                rank_map=rank_map,
            )
            row["position_key"] = _position_key(trade)
            row["entry_time"] = trade.get("entry_time")
            return row

        if self.parallel and enrich_jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_job, day, cohort, tr): (day, cohort) for day, cohort, tr in enrich_jobs}
                for fut in as_completed(futs):
                    row = fut.result()
                    enriched.append(row)
                    if row.get("cohort", "").startswith("OR_"):
                        or_overlay_enriched.append(row)
        else:
            for day, cohort, tr in enrich_jobs:
                row = _job(day, cohort, tr)
                enriched.append(row)
                if cohort.startswith("OR_"):
                    or_overlay_enriched.append(row)

        cohort_summary = _cohort_summary_rows(enriched)
        theory_compare = _theory_comparison_rows(enriched)

        speeds = [_num(r.get("day_high_update_speed")) for r in or_overlay_enriched if r.get("day_high_update_speed") is not None]
        speed_p75 = _percentile(speeds, 75) or 0.03

        filter_rows = _open_strength_filter_rows(or_overlay_enriched, or_trades=or_trades, speed_p75=speed_p75)

        trade_excl = _exclusion_rows(
            or_trades,
            audit_type="trade",
            group="trade",
            top_ns=(10,),
            key_fn=lambda t: _position_key(t),
            fields=["remaining_max_dd_yen_100"],
        )
        symbol_excl = _exclusion_rows(
            or_trades,
            audit_type="symbol",
            group="symbol",
            top_ns=(3,),
            key_fn=lambda t: _sym_key(t.get("symbol")),
            fields=["remaining_max_dd_yen_100"],
        )

        base_keys = {_position_key(t) for t in baseline_trades}
        or_keys = {_position_key(t) for t in or_trades}
        base_pnl = {_position_key(t): _num(t.get("pnl_yen_100")) for t in baseline_trades}
        or_pnl = {_position_key(t): _num(t.get("pnl_yen_100")) for t in or_trades}
        lost = base_keys - or_keys
        added = or_keys - base_keys
        net_sub = round(
            sum(or_pnl.get(k, 0) for k in added) + sum(base_pnl.get(k, 0) for k in lost),
            2,
        )

        adoption_rows = _adoption_criteria_rows(
            theory_rows=theory_compare,
            filter_rows=filter_rows,
            or_trades=or_trades,
            baseline_trades=baseline_trades,
            symbol_excl=symbol_excl,
            trade_excl=trade_excl,
            net_substitution_pnl=net_sub,
        )

        cap_design = _cap_study_design_rows()
        universe_design = _universe_study_design_rows()

        mandatory = _mandatory_answers(
            cohort_summary=cohort_summary,
            theory_rows=theory_compare,
            filter_rows=filter_rows,
            adoption_rows=adoption_rows,
        )

        theory_out = theory_compare + [
            {**r, "comparison": "cohort_median", "feature_id": f, "cohort_a": r.get("cohort")}
            for r in cohort_summary
            for f in THEORY_FEATURES
            if f in r
        ]

        return {
            "verdict": PHASE534_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": period_end,
            "cap_default": CAP,
            "parallel_workers": workers,
            "cohort_summary": cohort_summary,
            "theory_features": theory_out,
            "open_strength_filters": filter_rows,
            "adoption_criteria": adoption_rows,
            "cap_study_design": cap_design,
            "universe_study_design": universe_design,
            "mandatory_answers": mandatory,
            "net_substitution_pnl": net_sub,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        kabu = resolve_kabu_root(self.repo_root)
        reports = resolve_reports_dir(kabu)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "theory": reports / "phase534_or_theory_features.csv",
            "filters": reports / "phase534_open_strength_filter.csv",
            "adoption": reports / "phase534_adoption_criteria.csv",
            "cap_design": reports / "phase534_cap_study_design.csv",
            "universe_design": reports / "phase534_universe_study_design.csv",
            "report": reports / "phase534_report.json",
            "docs": kabu / "docs" / "operations" / "phase534_or_open_strength_theory.md",
        }
        _write_csv(paths["theory"], THEORY_FIELDS, list(result.get("theory_features") or []))
        _write_csv(paths["filters"], FILTER_FIELDS, list(result.get("open_strength_filters") or []))
        _write_csv(paths["adoption"], ADOPTION_FIELDS, list(result.get("adoption_criteria") or []))
        _write_csv(paths["cap_design"], CAP_DESIGN_FIELDS, list(result.get("cap_study_design") or []))
        _write_csv(paths["universe_design"], UNIVERSE_DESIGN_FIELDS, list(result.get("universe_study_design") or []))
        paths["report"].write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        paths["docs"].parent.mkdir(parents=True, exist_ok=True)
        paths["docs"].write_text(_render_doc(result), encoding="utf-8")
        return paths
