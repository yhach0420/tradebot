"""
Phase523 — Re-entry definition audit + overlay edge reality audit.

Extends Phase522 with:
  - Live paper structural trades through 20260624
  - Resolved exit_reason (structural vs overlap_replaced)
  - Relaxed stop-chain definitions
  - Deep overlay top-trade / symbol / rising-capture reality check

Research only. No Runtime changes.
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key
from research.phase400_holding_time_audit import normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase409_boundary_forward_shadow import load_structural_trades_for_day
from research.phase443_full_runtime_combined_capital_sim import CapacityReplayState
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase480_pbv2_loss_cluster_audit import _mfe_mae_to_exit
from research.phase481_stop_low_mfe_reduction_tournament import _build_trade_rows
from research.phase488_current_runtime_replay import _filter_period
from research.phase493_global_entry_failure_audit import PERIOD_START
from research.phase507_classic_strategy_battle import (
    BASELINE_STRATEGY_ID,
    _run_baseline_runtime,
    _simulate_precomputed_cap,
    _universe_symbols,
)
from research.phase509_t15_t13_signal_audit import _bar_at_entry, _build_bar_cache
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

PHASE523_VERDICT = "phase523_reentry_definition_overlay_edge_reality_audit_done"
PERIOD_END_523 = "20260624"
MAX_WORKERS = 4
SYMBOL_5074 = "5074"
OVERLAY_STRATEGIES = ("O_R003_OR", "G3_G4")

REENTRY_TIMELINE_FIELDS = [
    "data_source",
    "day",
    "session",
    "symbol",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "exit_reason",
    "exit_reason_resolved",
    "pnl_yen_100",
    "pnl_pct",
    "mfe_pct",
    "mae_pct",
]

STOP_REENTRY_CLASS_FIELDS = [
    "follow_up_class",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "avg_mfe_pct",
    "avg_mae_pct",
]

RELAXED_CHAIN_FIELDS = [
    "definition_id",
    "definition_label",
    "data_source",
    "count",
    "notes",
]

CASE_5074_FIELDS = [
    "seq",
    "day",
    "session",
    "symbol",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "exit_reason",
    "exit_reason_resolved",
    "pnl_yen_100",
    "mfe_pct",
    "mae_pct",
    "audit_note",
]

TOP_TRADE_OVERLAP_FIELDS = [
    "comparison",
    "top_n",
    "strategy_a",
    "strategy_b",
    "symbol_overlap_count",
    "trade_overlap_count",
    "same_symbol_same_day_count",
    "jaccard_similarity",
    "overlapped_profit_share_pct_a",
    "overlapped_profit_share_pct_b",
    "unique_profit_share_pct_a",
    "unique_profit_share_pct_b",
]

TOP_SYMBOL_OVERLAP_FIELDS = [
    "comparison",
    "top_n",
    "strategy_a",
    "strategy_b",
    "common_symbol_count",
    "overlay_unique_symbol_count",
    "pbv2_unique_symbol_count",
    "jaccard_similarity",
    "overlay_unique_pnl",
    "pbv2_unique_pnl",
]

RISING_DETAIL_FIELDS = [
    "day",
    "rank_universe",
    "top_n",
    "strategy_id",
    "capture_rate",
    "capture_mfe_gt_0_5_rate",
    "capture_mfe_gt_1_0_rate",
    "pbv2_only",
    "overlay_only",
    "both",
    "neither",
    "captured_pnl",
]

TOP10_MEANING_FIELDS = [
    "strategy_id",
    "top10_pnl",
    "non_top10_pnl",
    "top10_avg_mfe",
    "non_top10_avg_mfe",
    "top10_in_rising_top10_pct",
    "non_top10_in_rising_top10_pct",
    "top10_is_trend_capture",
]

COEXISTENCE_FIELDS = [
    "strategy_id",
    "classification",
    "symbol_overlap_jaccard_top20",
    "overlay_unique_profit_share_pct",
    "rising_capture_lead",
    "top10_trend_capture",
    "notes",
]


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _resolved_exit_reason(row: Mapping[str, Any]) -> str:
    reason = str(row.get("exit_reason") or "").strip()
    structural = str(row.get("structural_exit_reason") or "").strip()
    if reason.lower() == "overlap_replaced_review" and structural:
        return normalize_exit_reason(structural)
    if structural:
        return normalize_exit_reason(structural)
    return normalize_exit_reason(reason)


def _is_stop_hit(row: Mapping[str, Any]) -> bool:
    try:
        from small_paper.canonical_summary import is_stop_exit

        if is_stop_exit(row):
            return True
    except Exception:
        pass
    return _resolved_exit_reason(row) == "stop_hit"


def _is_loss_exit(row: Mapping[str, Any]) -> bool:
    return _float(row.get("pnl_yen_100")) < 0


def _follow_up_class(row: Mapping[str, Any]) -> str:
    er = _resolved_exit_reason(row)
    if er == "stop_hit":
        return "stop_to_stop"
    if er == "trailing_mfe":
        return "stop_to_trailing"
    if "no_progress" in er:
        return "stop_to_no_progress"
    if er == "session_close":
        return "stop_to_session_close"
    return "stop_to_other"


def _iter_calendar_days(start: str, end: str) -> list[str]:
    d0 = datetime.strptime(start, "%Y%m%d").replace(tzinfo=JST)
    d1 = datetime.strptime(end, "%Y%m%d").replace(tzinfo=JST)
    out: list[str] = []
    cur = d0
    while cur <= d1:
        out.append(cur.strftime("%Y%m%d"))
        cur += timedelta(days=1)
    return out


def _load_live_trades(repo_root: Path, *, start: str, end: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in _iter_calendar_days(start, end):
        for t in load_structural_trades_for_day(repo_root, day):
            rows.append({**dict(t), "data_source": "live_paper", "strategy_id": "LIVE_PAPER"})
    return rows


def _replay_baseline_rows(repo_root: Path, price_idx: Mapping) -> list[dict[str, Any]]:
    from research.phase476_pre_breakout_gate_replay import _load_replay_pool

    reports = resolve_reports_dir(repo_root)
    replay_pool, _ = _load_replay_pool(reports)
    replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END_523)
    trade_by_key = {_position_key(t): t for t in replay_pool}
    state, _ = _run_baseline_runtime(repo_root)
    raw = _build_trade_rows(state, trade_by_key=trade_by_key, price_idx=price_idx)
    rows: list[dict[str, Any]] = []
    for r in raw:
        src = r.get("trade") or r
        ep = _float(src.get("entry_price"))
        xp = _float(src.get("exit_price") or src.get("close_price"))
        pct = _float(src.get("realized_pnl_pct") or src.get("pnl_pct"))
        if pct == 0 and ep > 0 and xp > 0:
            pct = round((xp - ep) / ep * 100.0, 4)
        rows.append(
            {
                "data_source": "replay_cap",
                "strategy_id": BASELINE_STRATEGY_ID,
                "session": "replay",
                "symbol": r["symbol"],
                "day": r["day"],
                "entry_time": r["entry_time"],
                "entry_price": ep,
                "exit_time": r.get("exit_time"),
                "exit_price": xp,
                "exit_reason": r.get("exit_reason"),
                "exit_reason_resolved": normalize_exit_reason(str(r.get("exit_reason") or "")),
                "pnl_yen_100": _float(r.get("pnl_yen")),
                "pnl_pct": pct,
                "mfe_pct": r.get("mfe_pct"),
                "mae_pct": r.get("mae_pct"),
                "position_key": r.get("position_key"),
                "trade": src,
            }
        )
    return rows


def _enrich_live_mfe(rows: Sequence[Mapping[str, Any]], price_idx: Mapping) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        row = dict(r)
        sym = str(row.get("symbol") or "").replace(".T", "")
        day = str(row.get("day") or "")[:8]
        exit_ts = str(row.get("exit_time") or "")
        mfe, mae = _mfe_mae_to_exit(row, price_idx=price_idx, exit_ts_iso=exit_ts)
        row["mfe_pct"] = mfe if mfe is not None else row.get("peak_mfe_pct") or row.get("mfe_pct")
        row["mae_pct"] = mae if mae is not None else row.get("mae_pct")
        row["exit_reason_resolved"] = _resolved_exit_reason(row)
        row["pnl_pct"] = row.get("pnl_pct") or row.get("realized_pnl_pct")
        row["entry_price"] = _float(row.get("entry_price"))
        row["exit_price"] = _float(row.get("exit_price") or row.get("close_price"))
        out.append(row)
    return out


def _timeline_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = sorted(
        [dict(t) for t in trades],
        key=lambda t: (
            str(t.get("day") or ""),
            str(t.get("symbol") or ""),
            _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
        ),
    )
    return [{k: r.get(k) for k in REENTRY_TIMELINE_FIELDS} for r in rows]


def _sorted_symbol_day(trades: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    by: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        sym = str(t.get("symbol") or "").replace(".T", "")
        day = str(t.get("day") or "")[:8]
        by[(sym, day)].append(dict(t))
    for k in by:
        by[k].sort(key=lambda r: _parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST))
    return by


def _metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_float(t.get("pnl_yen_100")) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    mfes = [_float(t.get("mfe_pct")) for t in trades if t.get("mfe_pct") is not None]
    maes = [_float(t.get("mae_pct")) for t in trades if t.get("mae_pct") is not None]
    return {
        "trade_count": len(pnls),
        "total_pnl_yen_100": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
        "avg_mfe_pct": round(statistics.mean(mfes), 4) if mfes else 0.0,
        "avg_mae_pct": round(statistics.mean(maes), 4) if maes else 0.0,
    }


def _stop_reentry_classification(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by = _sorted_symbol_day(trades)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for _k, seq in by.items():
        for i in range(1, len(seq)):
            if not _is_stop_hit(seq[i - 1]):
                continue
            buckets[_follow_up_class(seq[i])].append(seq[i])
    rows: list[dict[str, Any]] = []
    for cls, items in sorted(buckets.items()):
        rows.append({"follow_up_class": cls, **_metrics(items)})
    return rows


def _relaxed_chains(trades: Sequence[Mapping[str, Any]], *, data_source: str) -> list[dict[str, Any]]:
    by = _sorted_symbol_day(trades)
    d1 = d2 = d3_syms = d4_syms = d5 = 0
    for (_sym, _day), seq in by.items():
        loss_count = sum(1 for t in seq if _is_loss_exit(t))
        if loss_count >= 2:
            d3_syms += 1
        if loss_count >= 3:
            d4_syms += 1
        for i in range(len(seq) - 1):
            if _is_stop_hit(seq[i]) and i + 2 < len(seq) and _is_stop_hit(seq[i + 2]):
                d1 += 1
            if _is_loss_exit(seq[i]) and i + 2 < len(seq) and _is_loss_exit(seq[i + 2]):
                d2 += 1
            prev_ex = _float(seq[i].get("exit_price")) or 0.0
            cur_en = _float(seq[i + 1].get("entry_price")) or 0.0
            if prev_ex > 0 and cur_en > 0 and cur_en <= prev_ex:
                d5 += 1
    return [
        {"definition_id": "D1", "definition_label": "stop→next_entry→stop", "data_source": data_source, "count": d1, "notes": "strict adjacency"},
        {"definition_id": "D2", "definition_label": "loss→next_entry→loss", "data_source": data_source, "count": d2, "notes": "skip middle trade"},
        {"definition_id": "D3", "definition_label": "same symbol 2+ loss exits/day", "data_source": data_source, "count": d3_syms, "notes": "symbol-day count"},
        {"definition_id": "D4", "definition_label": "same symbol 3+ loss exits/day", "data_source": data_source, "count": d4_syms, "notes": "symbol-day count"},
        {"definition_id": "D5", "definition_label": "reentry at/below prev exit price", "data_source": data_source, "count": d5, "notes": "consecutive pair"},
    ]


def _case_5074(
    live_trades: Sequence[Mapping[str, Any]],
    replay_trades: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    target_day = "20260624"
    live_5074 = [
        t for t in live_trades
        if str(t.get("symbol") or "").replace(".T", "") == SYMBOL_5074 and str(t.get("day") or "")[:8] == target_day
    ]
    live_5074.sort(key=lambda r: _parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST))

    notes: list[str] = []
    if not live_5074:
        notes.append("20260624 live structural_trades not found on disk")
    else:
        am = [
            t for t in live_5074
            if (ent := _parse_ts(str(t.get("entry_time") or ""))) is not None and ent.hour < 12
        ]
        notes.append(f"live_5074_trades={len(live_5074)} am_trades={len(am)}")

    replay_5074 = [t for t in replay_trades if str(t.get("symbol") or "").replace(".T", "") == SYMBOL_5074]
    notes.append(f"replay_5074_trades={len(replay_5074)} replay_max_day<=20260619")

    phase522_zero_reasons = [
        "Phase522 PERIOD_END=20260622 excluded 20260624",
        "Phase522 used replay_cap only (no live overlap_replaced structural exits)",
        "Phase522 D1 required stop_hit on trades[i] and trades[i+2] with any trade[i+1] between",
        "CAP replay collapses same-symbol churn differently than live AM sessions",
    ]

    rows: list[dict[str, Any]] = []
    for i, t in enumerate(live_5074, start=1):
        rows.append(
            {
                "seq": i,
                "day": t.get("day"),
                "session": t.get("session"),
                "symbol": SYMBOL_5074,
                "entry_time": t.get("entry_time"),
                "entry_price": t.get("entry_price"),
                "exit_time": t.get("exit_time"),
                "exit_price": t.get("exit_price"),
                "exit_reason": t.get("exit_reason"),
                "exit_reason_resolved": t.get("exit_reason_resolved") or _resolved_exit_reason(t),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "mfe_pct": t.get("mfe_pct"),
                "mae_pct": t.get("mae_pct"),
                "audit_note": "",
            }
        )

    if not rows:
        for i, reason in enumerate(phase522_zero_reasons, start=1):
            rows.append(
                {
                    "seq": i,
                    "day": target_day,
                    "session": "N/A",
                    "symbol": SYMBOL_5074,
                    "entry_time": "",
                    "entry_price": 0,
                    "exit_time": "",
                    "exit_price": 0,
                    "exit_reason": "",
                    "exit_reason_resolved": "",
                    "pnl_yen_100": 0,
                    "mfe_pct": 0,
                    "mae_pct": 0,
                    "audit_note": reason,
                }
            )
    else:
        stops = [t for t in live_5074 if _is_stop_hit(t)]
        resolved_stops = [t for t in live_5074 if str(t.get("exit_reason_resolved") or "") == "stop_hit"]
        rows[0]["audit_note"] = (
            f"resolved_stop_count={len(resolved_stops)} raw_stop_count={len(stops)}; "
            f"overlap_replaced={sum(1 for t in live_5074 if 'overlap' in str(t.get('exit_reason') or '').lower())}"
        )

    summary = {
        "live_trade_count": len(live_5074),
        "is_stop_chain_live": len([t for t in live_5074 if _is_stop_hit(t)]) >= 3 if live_5074 else None,
        "phase522_zero_reasons": phase522_zero_reasons,
        "period_gap": "20260624 not in Phase522 replay pool",
        "data_available": bool(live_5074),
    }
    return rows, summary


def _trade_key(t: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(t.get("symbol") or "").replace(".T", ""),
        str(t.get("day") or "")[:8],
        str(t.get("entry_time") or "")[:19],
    )


def _top_trade_overlap(
    a_trades: Sequence[Mapping[str, Any]],
    b_trades: Sequence[Mapping[str, Any]],
    *,
    strategy_a: str,
    strategy_b: str,
    top_n: int,
) -> dict[str, Any]:
    a_top = sorted(a_trades, key=lambda t: _float(t.get("pnl_yen_100")), reverse=True)[:top_n]
    b_top = sorted(b_trades, key=lambda t: _float(t.get("pnl_yen_100")), reverse=True)[:top_n]
    a_keys = {_trade_key(t) for t in a_top}
    b_keys = {_trade_key(t) for t in b_top}
    inter = a_keys & b_keys
    union = a_keys | b_keys
    a_syms = {k[0] for k in a_keys}
    b_syms = {k[0] for k in b_keys}
    same_sym_day = len({(k[0], k[1]) for k in inter})
    a_profit = sum(max(0, _float(t.get("pnl_yen_100"))) for t in a_top)
    b_profit = sum(max(0, _float(t.get("pnl_yen_100"))) for t in b_top)
    overlap_a = sum(max(0, _float(t.get("pnl_yen_100"))) for t in a_top if _trade_key(t) in inter)
    overlap_b = sum(max(0, _float(t.get("pnl_yen_100"))) for t in b_top if _trade_key(t) in inter)
    return {
        "comparison": f"{strategy_a}_vs_{strategy_b}",
        "top_n": top_n,
        "strategy_a": strategy_a,
        "strategy_b": strategy_b,
        "symbol_overlap_count": len(a_syms & b_syms),
        "trade_overlap_count": len(inter),
        "same_symbol_same_day_count": same_sym_day,
        "jaccard_similarity": round(len(inter) / len(union), 4) if union else 0.0,
        "overlapped_profit_share_pct_a": round(overlap_a / a_profit * 100.0, 2) if a_profit else 0.0,
        "overlapped_profit_share_pct_b": round(overlap_b / b_profit * 100.0, 2) if b_profit else 0.0,
        "unique_profit_share_pct_a": round((a_profit - overlap_a) / a_profit * 100.0, 2) if a_profit else 0.0,
        "unique_profit_share_pct_b": round((b_profit - overlap_b) / b_profit * 100.0, 2) if b_profit else 0.0,
    }


def _symbol_pnl_rank(trades: Sequence[Mapping[str, Any]]) -> list[tuple[str, float]]:
    sym: dict[str, float] = defaultdict(float)
    for t in trades:
        sym[str(t.get("symbol") or "").replace(".T", "")] += _float(t.get("pnl_yen_100"))
    return sorted(sym.items(), key=lambda x: x[1], reverse=True)


def _top_symbol_overlap(
    a_trades: Sequence[Mapping[str, Any]],
    b_trades: Sequence[Mapping[str, Any]],
    *,
    strategy_a: str,
    strategy_b: str,
    top_n: int,
) -> dict[str, Any]:
    a_top = {s for s, _ in _symbol_pnl_rank(a_trades)[:top_n]}
    b_top = {s for s, _ in _symbol_pnl_rank(b_trades)[:top_n]}
    inter = a_top & b_top
    union = a_top | b_top
    a_unique = a_top - b_top
    b_unique = b_top - a_top
    a_uniq_pnl = sum(_float(t.get("pnl_yen_100")) for t in a_trades if str(t.get("symbol") or "").replace(".T", "") in a_unique)
    b_uniq_pnl = sum(_float(t.get("pnl_yen_100")) for t in b_trades if str(t.get("symbol") or "").replace(".T", "") in b_unique)
    overlay_unique = a_unique if strategy_a != BASELINE_STRATEGY_ID else b_unique
    pbv2_unique = b_unique if strategy_a != BASELINE_STRATEGY_ID else a_unique
    if strategy_a == BASELINE_STRATEGY_ID:
        overlay_unique, pbv2_unique = b_unique, a_unique
    return {
        "comparison": f"{strategy_a}_vs_{strategy_b}",
        "top_n": top_n,
        "strategy_a": strategy_a,
        "strategy_b": strategy_b,
        "common_symbol_count": len(inter),
        "overlay_unique_symbol_count": len(overlay_unique),
        "pbv2_unique_symbol_count": len(pbv2_unique),
        "jaccard_similarity": round(len(inter) / len(union), 4) if union else 0.0,
        "overlay_unique_pnl": round(
            sum(_float(t.get("pnl_yen_100")) for t in (a_trades if strategy_a != BASELINE_STRATEGY_ID else b_trades)
                if str(t.get("symbol") or "").replace(".T", "") in overlay_unique),
            2,
        ),
        "pbv2_unique_pnl": round(
            sum(_float(t.get("pnl_yen_100")) for t in (b_trades if strategy_a == BASELINE_STRATEGY_ID else a_trades)
                if str(t.get("symbol") or "").replace(".T", "") in pbv2_unique),
            2,
        ),
    }


def _day_risers(price_idx: Mapping, universe: Sequence[str], day: str, top_n: int) -> set[str]:
    rets: list[tuple[str, float]] = []
    for sym in universe:
        sym_t = sym if sym.endswith(".T") else f"{sym}.T"
        series = price_idx.get((sym_t, day), [])
        if len(series) < 2:
            continue
        o, c = float(series[0][1]), float(series[-1][1])
        if o <= 0:
            continue
        rets.append((sym_t.replace(".T", ""), (c - o) / o * 100.0))
    rets.sort(key=lambda x: x[1], reverse=True)
    return {s for s, _ in rets[:top_n]}


def _rising_capture_detail(
    *,
    baseline: Sequence[Mapping[str, Any]],
    strategies: Mapping[str, Sequence[Mapping[str, Any]]],
    price_idx: Mapping,
    universe: Sequence[str],
    days: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day in days:
        for top_n in (10, 20, 50):
            risers = _day_risers(price_idx, universe, day, top_n)
            if not risers:
                continue
            base_syms = {str(t.get("symbol") or "").replace(".T", "") for t in baseline if str(t.get("day") or "")[:8] == day}
            for sid, trades in strategies.items():
                day_trades = [t for t in trades if str(t.get("day") or "")[:8] == day]
                entered = {str(t.get("symbol") or "").replace(".T", "") for t in day_trades}
                cap = entered & risers
                mfe05 = sum(1 for t in day_trades if str(t.get("symbol") or "").replace(".T", "") in risers and _num(t.get("mfe_pct")) > 0.5)
                mfe10 = sum(1 for t in day_trades if str(t.get("symbol") or "").replace(".T", "") in risers and _num(t.get("mfe_pct")) > 1.0)
                pnl = round(sum(_float(t.get("pnl_yen_100")) for t in day_trades if str(t.get("symbol") or "").replace(".T", "") in risers), 2)
                b_only = len(risers & base_syms - entered)
                o_only = len(risers & entered - base_syms)
                both = len(risers & entered & base_syms)
                neither = len(risers - entered - base_syms)
                rows.append(
                    {
                        "day": day,
                        "rank_universe": "day_return",
                        "top_n": top_n,
                        "strategy_id": sid,
                        "capture_rate": round(len(cap) / len(risers), 4),
                        "capture_mfe_gt_0_5_rate": round(mfe05 / len(risers), 4) if risers else 0.0,
                        "capture_mfe_gt_1_0_rate": round(mfe10 / len(risers), 4) if risers else 0.0,
                        "pbv2_only": b_only,
                        "overlay_only": o_only,
                        "both": both,
                        "neither": neither,
                        "captured_pnl": pnl,
                    }
                )
    return rows


def _top10_meaning(
    strategy_id: str,
    trades: Sequence[Mapping[str, Any]],
    *,
    rising_by_day: Mapping[str, set[str]],
) -> dict[str, Any]:
    ranked = sorted(trades, key=lambda t: _float(t.get("pnl_yen_100")), reverse=True)
    top10 = ranked[:10]
    rest = ranked[10:]
    top_pnl = round(sum(_float(t.get("pnl_yen_100")) for t in top10), 2)
    rest_pnl = round(sum(_float(t.get("pnl_yen_100")) for t in rest), 2)
    top_mfe = [_float(t.get("mfe_pct")) for t in top10 if t.get("mfe_pct") is not None]
    rest_mfe = [_float(t.get("mfe_pct")) for t in rest if t.get("mfe_pct") is not None]

    def _in_rising(group: Sequence[Mapping[str, Any]]) -> float:
        hits = total = 0
        for t in group:
            day = str(t.get("day") or "")[:8]
            risers = rising_by_day.get(f"{day}:10", set())
            if not risers:
                continue
            total += 1
            if str(t.get("symbol") or "").replace(".T", "") in risers:
                hits += 1
        return round(hits / total, 4) if total else 0.0

    rising_top10 = _in_rising(top10)
    rising_rest = _in_rising(rest)
    return {
        "strategy_id": strategy_id,
        "top10_pnl": top_pnl,
        "non_top10_pnl": rest_pnl,
        "top10_avg_mfe": round(statistics.mean(top_mfe), 4) if top_mfe else 0.0,
        "non_top10_avg_mfe": round(statistics.mean(rest_mfe), 4) if rest_mfe else 0.0,
        "top10_in_rising_top10_pct": rising_top10,
        "non_top10_in_rising_top10_pct": rising_rest,
        "top10_is_trend_capture": rising_top10 >= 0.5 and (statistics.mean(top_mfe) if top_mfe else 0) >= 0.8,
    }


def _coexistence_class(
    strategy_id: str,
    *,
    trade_overlap: Mapping[str, Any],
    symbol_overlap: Mapping[str, Any],
    rising_rows: Sequence[Mapping[str, Any]],
    top10_row: Mapping[str, Any],
) -> dict[str, Any]:
    jaccard = _float(trade_overlap.get("jaccard_similarity"))
    unique_share = _float(trade_overlap.get("unique_profit_share_pct_b" if strategy_id != BASELINE_STRATEGY_ID else "unique_profit_share_pct_a"))
    if strategy_id != BASELINE_STRATEGY_ID:
        unique_share = _float(trade_overlap.get("unique_profit_share_pct_b"))
    rising = [r for r in rising_rows if r.get("strategy_id") == strategy_id and r.get("top_n") == 10]
    base_rows = [r for r in rising_rows if r.get("strategy_id") == BASELINE_STRATEGY_ID and r.get("top_n") == 10]
    o_cap = statistics.mean([_float(r.get("capture_rate")) for r in rising]) if rising else 0.0
    b_cap = statistics.mean([_float(r.get("capture_rate")) for r in base_rows]) if base_rows else 0.0
    trend = bool(top10_row.get("top10_is_trend_capture"))

    if jaccard >= 0.5 and unique_share < 30:
        cls = "A_same_timing_no_coexistence"
    elif jaccard >= 0.3 and o_cap > b_cap:
        cls = "B_same_symbol_earlier_entry"
    elif _float(symbol_overlap.get("overlay_unique_pnl")) > 50000:
        cls = "C_different_symbol_coexistence"
    elif o_cap > b_cap and not trend:
        cls = "D_high_capture_top10_dependent"
    elif trend and o_cap > b_cap:
        cls = "E_rising_capture_engine"
    else:
        cls = "D_high_capture_top10_dependent"

    return {
        "strategy_id": strategy_id,
        "classification": cls,
        "symbol_overlap_jaccard_top20": _float(symbol_overlap.get("jaccard_similarity")),
        "overlay_unique_profit_share_pct": unique_share,
        "rising_capture_lead": o_cap > b_cap,
        "top10_trend_capture": trend,
        "notes": f"capture overlay={round(o_cap,4)} baseline={round(b_cap,4)}",
    }


def _build_overlay_strategies(
    repo_root: Path,
    *,
    parallel: bool,
    workers: int,
) -> dict[str, list[dict[str, Any]]]:
    bar_cache, days = _build_bar_cache(repo_root)
    replay_pool, runtime_shadows, guard_c_block = _prepare_runtime_env(repo_root)
    kabu = resolve_kabu_root(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END_523)
    universe = _universe_symbols(replay_pool)
    micro_lookup = _build_micro_lookup(replay_pool)
    pbv2_candidates = _pbv2_precomputed_candidates(replay_pool, runtime_shadows, guard_c_block)
    overlay = OVERLAY_DEFS["O_R003"]
    overlay_by: dict[str, list[dict[str, Any]]] = {s: [] for s in OVERLAY_STRATEGIES}
    jobs = [(sid, day) for sid in OVERLAY_STRATEGIES for day in days]

    def _job(sid: str, day: str) -> tuple[str, list[dict[str, Any]]]:
        raw = _scan_overlay_day(overlay, day=day, universe=universe, bar_cache=bar_cache, price_idx=price_idx)
        if sid == "G3_G4":
            raw = [t for t in raw if _passes_g3_g4(_extract_entry_features(t, bar_cache=bar_cache, micro_lookup=micro_lookup))]
        return sid, raw

    if parallel and jobs:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_job, sid, day): (sid, day) for sid, day in jobs}
            for fut in as_completed(futs):
                sid, chunk = fut.result()
                overlay_by[sid].extend(chunk)
    else:
        for sid, day in jobs:
            s, chunk = _job(sid, day)
            overlay_by[s].extend(chunk)

    trade_by_key = {_position_key(t): t for t in replay_pool}
    out: dict[str, list[dict[str, Any]]] = {}
    for sid in OVERLAY_STRATEGIES:
        merged = _merge_or_candidates(
            pbv2_candidates,
            overlay_by[sid],
            bar_cache=bar_cache,
            overlay=overlay,
            guard_c_block=guard_c_block,
        )
        state = _simulate_precomputed_cap(merged, mode=f"phase523_{sid.lower()}")
        rows: list[dict[str, Any]] = []
        for r in _trade_rows_from_state(state, sid):
            pk = str(r.get("position_key") or "")
            src = trade_by_key.get(pk, {})
            mfe, mae = _mfe_mae_to_exit(src or r, price_idx=price_idx, exit_ts_iso=str(r.get("exit_time") or ""))
            rows.append({**dict(r), "mfe_pct": mfe, "mae_pct": mae})
        out[sid] = rows
    return out


def _part_a_mandatory(
    *,
    case_summary: Mapping[str, Any],
    relaxed_live: Sequence[Mapping[str, Any]],
    relaxed_replay: Sequence[Mapping[str, Any]],
    stop_class: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    d1_live = next((r for r in relaxed_live if r.get("definition_id") == "D1"), {})
    d1_replay = next((r for r in relaxed_replay if r.get("definition_id") == "D1"), {})
    d4_live = next((r for r in relaxed_live if r.get("definition_id") == "D4"), {})
    return {
        "1_phase522_zero_reason": case_summary.get("phase522_zero_reasons"),
        "2_5074_live_stop_chain": case_summary.get("is_stop_chain_live"),
        "2_5074_data_available": case_summary.get("data_available"),
        "3_exit_reason_classification_gap": "overlap_replaced_review masks structural stop_hit in live",
        "4_period_out_of_range": case_summary.get("period_gap"),
        "5_relaxed_d1_live": d1_live.get("count"),
        "5_relaxed_d1_replay": d1_replay.get("count"),
        "5_relaxed_d4_live": d4_live.get("count"),
        "6_reentry_guard_revalidation_needed": bool(d1_live.get("count")) and not d1_replay.get("count"),
        "stop_to_stop_live": next((r for r in stop_class if r.get("follow_up_class") == "stop_to_stop"), {}),
    }


def _part_b_mandatory(
    *,
    baseline: Sequence[Mapping[str, Any]],
    strategies: Mapping[str, Sequence[Mapping[str, Any]]],
    trade_overlaps: Sequence[Mapping[str, Any]],
    symbol_overlaps: Sequence[Mapping[str, Any]],
    rising_rows: Sequence[Mapping[str, Any]],
    top10_rows: Sequence[Mapping[str, Any]],
    coexistence: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    base_pnl = sum(_float(t.get("pnl_yen_100")) for t in baseline)

    def _overlap(sid: str, top_n: int = 10) -> Mapping[str, Any]:
        return next(
            (r for r in trade_overlaps if r.get("strategy_b") == sid and r.get("top_n") == top_n),
            {},
        )

    def _sym(sid: str) -> Mapping[str, Any]:
        return next((r for r in symbol_overlaps if r.get("strategy_b") == sid and r.get("top_n") == 20), {})

    def _rising_lead(sid: str) -> bool:
        o = [r for r in rising_rows if r.get("strategy_id") == sid and r.get("top_n") == 10]
        b = [r for r in rising_rows if r.get("strategy_id") == BASELINE_STRATEGY_ID and r.get("top_n") == 10]
        return statistics.mean([_float(x.get("capture_rate")) for x in o]) > statistics.mean([_float(x.get("capture_rate")) for x in b]) if o and b else False

    def _top10_row(sid: str) -> Mapping[str, Any]:
        return next((r for r in top10_rows if r.get("strategy_id") == sid), {})

    g3_excl = _top10_row("G3_G4")
    or_excl = _top10_row("O_R003_OR")
    g3_co = next((c for c in coexistence if c.get("strategy_id") == "G3_G4"), {})
    or_co = next((c for c in coexistence if c.get("strategy_id") == "O_R003_OR"), {})

    g3_top = _overlap("G3_G4")
    or_top = _overlap("O_R003_OR")

    return {
        "1_g3_top_profit_same_as_pbv2": _float(g3_top.get("jaccard_similarity")) >= 0.3,
        "1_g3_jaccard_top10": g3_top.get("jaccard_similarity"),
        "2_or_top_profit_same_as_pbv2": _float(or_top.get("jaccard_similarity")) >= 0.3,
        "2_or_jaccard_top10": or_top.get("jaccard_similarity"),
        "3_g3_rising_capture_higher": _rising_lead("G3_G4"),
        "4_or_rising_capture_higher": _rising_lead("O_R003_OR"),
        "5_g3_top10_accidental": not bool(g3_excl.get("top10_is_trend_capture")),
        "6_or_top10_accidental": not bool(or_excl.get("top10_is_trend_capture")),
        "7_top10_exclusion_still_viable": any(
            _float(r.get("non_top10_pnl")) > 0 for r in top10_rows if r.get("strategy_id") != BASELINE_STRATEGY_ID
        ),
        "8_g3_coexistence": g3_co.get("classification"),
        "9_or_coexistence": or_co.get("classification"),
        "10_shadow_continue": "O_R003_OR" if str(or_co.get("classification", "")).startswith(("C", "E")) else (
            "G3_G4" if str(g3_co.get("classification", "")).startswith("E") else "neither"
        ),
        "overlay_unique_symbols_6976_excluded": {
            sid: [s for s, _ in _symbol_pnl_rank(trades)[:20] if s != "6976" and s not in {x[0] for x in _symbol_pnl_rank(baseline)[:20]}]
            for sid, trades in strategies.items()
        },
        "baseline_pnl": round(base_pnl, 2),
        "adopt_not_allowed": True,
    }


@dataclass
class Phase523Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        workers = min(max(1, self.max_workers), MAX_WORKERS)
        kabu = resolve_kabu_root(self.repo_root)
        price_idx = _build_price_index_to(kabu, period_end=PERIOD_END_523)
        bar_cache, replay_days = _build_bar_cache(self.repo_root)

        replay_trades = _replay_baseline_rows(self.repo_root, price_idx)
        live_trades = _enrich_live_mfe(_load_live_trades(self.repo_root, start=PERIOD_START, end=PERIOD_END_523), price_idx)
        combined = replay_trades + live_trades

        timeline = _timeline_rows(combined)
        stop_class = _stop_reentry_classification(combined)
        relaxed_replay = _relaxed_chains(replay_trades, data_source="replay_cap")
        relaxed_live = _relaxed_chains(live_trades, data_source="live_paper")
        case_rows, case_summary = _case_5074(live_trades, replay_trades)

        overlay_strategies = _build_overlay_strategies(self.repo_root, parallel=self.parallel, workers=workers)
        baseline = replay_trades
        trades_by_strategy: dict[str, list[dict[str, Any]]] = {
            BASELINE_STRATEGY_ID: baseline,
            **overlay_strategies,
        }

        trade_overlap_rows: list[dict[str, Any]] = []
        symbol_overlap_rows: list[dict[str, Any]] = []
        for sid in OVERLAY_STRATEGIES:
            for top_n in (10, 20, 50):
                trade_overlap_rows.append(_top_trade_overlap(baseline, overlay_strategies[sid], strategy_a=BASELINE_STRATEGY_ID, strategy_b=sid, top_n=top_n))
                trade_overlap_rows.append(_top_trade_overlap(overlay_strategies["G3_G4"], overlay_strategies["O_R003_OR"], strategy_a="G3_G4", strategy_b="O_R003_OR", top_n=top_n))
            for top_n in (10, 20, 50):
                symbol_overlap_rows.append(_top_symbol_overlap(baseline, overlay_strategies[sid], strategy_a=BASELINE_STRATEGY_ID, strategy_b=sid, top_n=top_n))

        universe = _universe_symbols([])
        try:
            replay_pool, _, _ = _prepare_runtime_env(self.repo_root)
            universe = _universe_symbols(replay_pool)
        except Exception:
            pass

        rising_rows = _rising_capture_detail(
            baseline=baseline,
            strategies={BASELINE_STRATEGY_ID: baseline, **overlay_strategies},
            price_idx=price_idx,
            universe=universe,
            days=replay_days,
        )

        rising_by_day: dict[str, set[str]] = {}
        for day in replay_days:
            rising_by_day[f"{day}:10"] = _day_risers(price_idx, universe, day, 10)

        top10_rows = [
            _top10_meaning(sid, trades, rising_by_day=rising_by_day)
            for sid, trades in trades_by_strategy.items()
        ]

        coexistence_rows: list[dict[str, Any]] = []
        for sid in OVERLAY_STRATEGIES:
            to = next((r for r in trade_overlap_rows if r.get("strategy_b") == sid and r.get("top_n") == 10), {})
            so = next((r for r in symbol_overlap_rows if r.get("strategy_b") == sid and r.get("top_n") == 20), {})
            t10 = next((r for r in top10_rows if r.get("strategy_id") == sid), {})
            coexistence_rows.append(_coexistence_class(sid, trade_overlap=to, symbol_overlap=so, rising_rows=rising_rows, top10_row=t10))

        part_a = _part_a_mandatory(case_summary=case_summary, relaxed_live=relaxed_live, relaxed_replay=relaxed_replay, stop_class=stop_class)
        part_b = _part_b_mandatory(
            baseline=baseline,
            strategies=overlay_strategies,
            trade_overlaps=trade_overlap_rows,
            symbol_overlaps=symbol_overlap_rows,
            rising_rows=rising_rows,
            top10_rows=top10_rows,
            coexistence=coexistence_rows,
        )

        return {
            "verdict": PHASE523_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": PERIOD_END_523,
            "replay_days": replay_days,
            "live_trade_count": len(live_trades),
            "replay_trade_count": len(replay_trades),
            "parallel_workers": workers,
            "reentry_timeline_count": len(timeline),
            "stop_reentry_classification": stop_class,
            "relaxed_chains_replay": relaxed_replay,
            "relaxed_chains_live": relaxed_live,
            "case_5074_summary": case_summary,
            "part_a_mandatory": part_a,
            "part_b_mandatory": part_b,
            "mandatory_answers": {**part_a, **part_b},
            "reentry_timeline": timeline,
            "case_5074_rows": case_rows,
            "relaxed_chain_rows": relaxed_replay + relaxed_live,
            "trade_overlap": trade_overlap_rows,
            "symbol_overlap": symbol_overlap_rows,
            "rising_capture": rising_rows,
            "top10_meaning": top10_rows,
            "coexistence": coexistence_rows,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "reentry_def": reports / "phase523_reentry_definition_audit.csv",
            "case_5074": reports / "phase523_5074_case_study.csv",
            "relaxed": reports / "phase523_stop_chain_relaxed_definitions.csv",
            "trade_overlap": reports / "phase523_top_trade_overlap.csv",
            "symbol_overlap": reports / "phase523_top_symbol_overlap.csv",
            "rising": reports / "phase523_rising_capture_detail.csv",
            "top10": reports / "phase523_top10_meaning_audit.csv",
            "coexistence": reports / "phase523_coexistence_classification.csv",
            "report": reports / "phase523_report.json",
            "docs": kabu / "docs" / "operations" / "phase523_reentry_definition_overlay_edge_reality_audit.md",
        }
        _write_csv(paths["reentry_def"], REENTRY_TIMELINE_FIELDS, list(result.get("reentry_timeline") or []))
        _write_csv(paths["case_5074"], CASE_5074_FIELDS, list(result.get("case_5074_rows") or []))
        _write_csv(paths["relaxed"], RELAXED_CHAIN_FIELDS, list(result.get("relaxed_chain_rows") or []))
        _write_csv(paths["trade_overlap"], TOP_TRADE_OVERLAP_FIELDS, list(result.get("trade_overlap") or []))
        _write_csv(paths["symbol_overlap"], TOP_SYMBOL_OVERLAP_FIELDS, list(result.get("symbol_overlap") or []))
        _write_csv(paths["rising"], RISING_DETAIL_FIELDS, list(result.get("rising_capture") or []))
        _write_csv(paths["top10"], TOP10_MEANING_FIELDS, list(result.get("top10_meaning") or []))
        _write_csv(paths["coexistence"], COEXISTENCE_FIELDS, list(result.get("coexistence") or []))
        slim = {k: v for k, v in result.items() if k not in ("reentry_timeline",)}
        paths["report"].write_text(json.dumps(slim, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    pa = result.get("part_a_mandatory") or {}
    pb = result.get("part_b_mandatory") or {}
    cs = result.get("case_5074_summary") or {}
    lines = [
        "# Phase523 — Re-Entry Definition + Overlay Edge Reality Audit",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        f"**Live trades loaded:** {result.get('live_trade_count')}",
        f"**Replay trades:** {result.get('replay_trade_count')}",
        "",
        "## Part A — why Phase522 showed 0 chains",
        "",
        f"- 5074 live data available: **{cs.get('data_available')}**",
        f"- Period gap: {cs.get('period_gap')}",
        "",
    ]
    for i, r in enumerate(cs.get("phase522_zero_reasons") or [], 1):
        lines.append(f"{i}. {r}")
    lines.extend(["", "## Part A mandatory", ""])
    for k, v in sorted(pa.items()):
        lines.append(f"- {k}: **{v}**")
    lines.extend(["", "## Part B mandatory", ""])
    for k, v in sorted(pb.items()):
        lines.append(f"- {k}: **{v}**")
    lines.append("")
    lines.append("Research only — no Runtime adoption.")
    return "\n".join(lines)
