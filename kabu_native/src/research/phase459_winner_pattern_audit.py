"""
Phase459 — Winner Pattern Audit (research only).

Extracts winning trade patterns, compares vs losers, classifies patterns,
evaluates rank-score bonus candidates, and traces missed uptrend symbols.
"""

from __future__ import annotations

import csv
import heapq
import json
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv, read_jpx_sector_map
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY, _precompute_np_shadows
from research.phase443_full_runtime_combined_capital_sim import (
    CAP,
    LEVERAGE,
    STARTING_EQUITY,
    STOP_POLICY,
    CapacityReplayState,
    ShadowExitInfo,
    _day_from_ts,
    _parse_ts,
    _position_key,
    _stop_rate_from_log,
)
from research.phase451_entry_shape_tournament import (
    DAY_618,
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _enrich_candidates,
    _load_candidate_stream,
    _now_iso,
    _optional_float,
    _chronological_pnls_from_log,
    _symbol_pnl_from_log,
)
from research.phase451b_entry_shape_tournament_mid_high import (
    _passes_baseline_mid_high,
    _runtime_entry_block_mid_high,
)
from research.phase456_entry_features import enrich_trade_phase456_features
from research.phase456c_vwap_structure_features import enrich_trade_phase456c_features
from research.phase271_leverage_attribution_and_robustness import build_spec
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_scan_controller import EntryFreshnessSnapshot, candidate_rank_score
from small_paper.weak_shape_reject_entry_guard import would_block_weak_shape_reject

JST = ZoneInfo("Asia/Tokyo")
REPLAY_MODE = "phase456_runtime_np"
SCAN_WINDOW_SEC = 2.0
MISSED_UPTREND_SYMBOLS = ("3441.T", "6492.T", "7256.T", "6466.T", "7600.T")
TARGET_DAY_619 = "20260619"

WINNER_FIELDS = [
    "symbol",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "exit_reason",
    "hold_sec",
    "r5",
    "r10",
    "r15",
    "r30",
    "day_high_distance",
    "high_update_age",
    "high_update_count_30m",
    "high_update_count_session",
    "vwap_dev_pct",
    "vwap_above_ratio",
    "consecutive_above_ticks",
    "entry_order_book_imbalance",
    "board_bucket",
    "momentum_score",
    "price",
    "notional_100shares",
    "trading_value",
    "sector_return_15m",
    "sector_return_30m",
    "relative_strength_vs_sector",
    "winner_pattern",
]

COMPARE_FIELDS = [
    "feature",
    "winner_mean",
    "winner_median",
    "winner_p25",
    "winner_p75",
    "loser_mean",
    "loser_median",
    "loser_p25",
    "loser_p75",
    "cohens_d",
    "effect_rank",
]

RANK_SIM_FIELDS = [
    "variant",
    "total_pnl_yen",
    "delta_pnl_vs_baseline",
    "profit_factor",
    "delta_pf_vs_baseline",
    "max_drawdown_yen",
    "delta_maxdd_vs_baseline",
    "accepted_count",
    "stop_rate",
    "daily_pnl_618",
    "delta_daily_pnl_618",
    "daily_pnl_619",
    "delta_daily_pnl_619",
    "symbol_pnl_6976",
    "delta_symbol_pnl_6976",
    "symbol_pnl_6920",
    "delta_symbol_pnl_6920",
    "symbol_pnl_4062",
    "delta_symbol_pnl_4062",
]

MISSED_FIELDS = [
    "symbol",
    "day",
    "shape_class",
    "open_to_close_return_pct",
    "was_candidate",
    "gate_would_pass",
    "in_universe_dynamic40",
    "failure_reason",
    "candidate_rank_score",
    "board_bucket",
    "momentum_category",
    "detail",
]


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _pnl_yen(trade: Mapping[str, Any]) -> float:
    raw = trade.get("pnl_yen") or trade.get("pnl_yen_100")
    if raw not in (None, ""):
        return float(raw)
    y100 = _float(trade.get("pnl_yen_100_float"))
    return round(float(y100), 2) if y100 is not None else 0.0


def _map_runtime_fields(trade: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(trade)
    for src, dst in (
        ("return_5min_pct", "entry_rise_5min_pct"),
        ("return_10min_pct", "entry_rise_10min_pct"),
        ("return_15min_pct", "entry_rise_15min_pct"),
        ("return_30min_pct", "entry_rise_30min_pct"),
    ):
        if out.get(dst) is None and out.get(src) is not None:
            out[dst] = out[src]
    return out


def _weak_shape_block(trade: Mapping[str, Any]) -> bool:
    return would_block_weak_shape_reject(_map_runtime_fields(trade))


def _board_bucket(trade: Mapping[str, Any]) -> str:
    tok = str(trade.get("active_score_tokens_v2") or trade.get("entry_score_tokens") or "")
    if "Board:high" in tok:
        return "high"
    if "Board:mid" in tok:
        return "mid"
    imb = _float(trade.get("entry_order_book_imbalance"))
    if imb is not None:
        if imb >= 0.55:
            return "high"
        if imb >= 0.45:
            return "mid"
    return "low"


def _base_rank(trade: Mapping[str, Any]) -> float:
    fresh = EntryFreshnessSnapshot(
        data_source="research",
        last_price_update_ts=str(trade.get("entry_time") or ""),
        last_board_update_ts=str(trade.get("entry_time") or ""),
        price_age_sec=0.0,
        board_age_sec=0.0,
    )
    return float(candidate_rank_score(trade, fresh))


def _scan_bucket(dt: datetime) -> float:
    return math.floor(dt.timestamp() / SCAN_WINDOW_SEC) * SCAN_WINDOW_SEC


def _feature_row(trade: Mapping[str, Any]) -> dict[str, Any]:
    m = _map_runtime_fields(trade)
    ep = _float(trade.get("entry_price")) or 0.0
    return {
        "r5": _optional_float(m.get("return_5min_pct") or m.get("entry_rise_5min_pct")),
        "r10": _optional_float(m.get("return_10min_pct") or m.get("entry_rise_10min_pct")),
        "r15": _optional_float(m.get("return_15min_pct") or m.get("entry_rise_15min_pct")),
        "r30": _optional_float(m.get("return_30min_pct") or m.get("entry_rise_30min_pct")),
        "day_high_distance": _optional_float(
            trade.get("day_high_distance_pct") or trade.get("entry_near_day_high_pct")
        ),
        "high_update_age": _float(trade.get("last_high_update_age_min")),
        "high_update_count_30m": _float(trade.get("high_update_count_30m")),
        "high_update_count_session": _float(trade.get("high_update_count_session")),
        "vwap_dev_pct": _float(trade.get("vwap_dev_pct")),
        "vwap_above_ratio": _float(trade.get("vwap_above_ratio_20tick")),
        "consecutive_above_ticks": _float(trade.get("consecutive_above_ticks")),
        "entry_order_book_imbalance": _float(trade.get("entry_order_book_imbalance")),
        "board_bucket": _board_bucket(trade),
        "momentum_score": _float(trade.get("momentum_continuation_score")),
        "price": ep,
        "notional_100shares": round(ep * 100, 2) if ep else None,
        "trading_value": _float(trade.get("trading_value")),
        "sector_return_15m": _float(trade.get("sector_return_15m")),
        "sector_return_30m": _float(trade.get("sector_return_30m")),
        "relative_strength_vs_sector": _float(trade.get("relative_strength_vs_sector")),
    }


def _cohens_d(w: Sequence[float], l: Sequence[float]) -> float:
    if len(w) < 2 or len(l) < 2:
        return 0.0
    mw, ml = statistics.mean(w), statistics.mean(l)
    sw, sl = statistics.pstdev(w), statistics.pstdev(l)
    pooled = math.sqrt((sw**2 + sl**2) / 2) or 1e-9
    return round((mw - ml) / pooled, 4)


def _pct(vals: Sequence[float], p: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    i = min(len(s) - 1, max(0, int(p * (len(s) - 1))))
    return round(s[i], 4)


def _classify_winner_pattern(trade: Mapping[str, Any], *, med: Mapping[str, float]) -> str:
    f = _feature_row(trade)
    r15 = f.get("r15")
    r30 = f.get("r30")
    hu = f.get("high_update_count_30m")
    var = f.get("vwap_above_ratio")
    cat = f.get("consecutive_above_ticks")
    bb = f.get("board_bucket")
    imb = f.get("entry_order_book_imbalance")
    failed = bool(trade.get("vwap_failed_reclaim_flag"))
    reclaim = bool(trade.get("recent_vwap_reclaim"))

    if r15 is not None and r30 is not None and r15 > 0 and r30 > 0 and (hu or 0) >= med.get("high_update_count_30m", 1):
        return "A_uptrend_continuation"
    if (var or 0) >= med.get("vwap_above_ratio", 0.5) and (cat or 0) >= med.get("consecutive_above_ticks", 3):
        return "B_vwap_stable_above"
    if bb == "high" or (imb or 0) >= med.get("entry_order_book_imbalance", 0.5):
        return "C_board_strength"
    if reclaim and not failed and (var or 0) >= 0.4:
        return "D_pullback_recovery"
    return "E_other"


def _simulate_rank_replay(
    candidates: Sequence[Mapping[str, Any]],
    shadow_by_key: Mapping[str, ShadowExitInfo],
    *,
    mode: str,
    rank_fn: Callable[[Mapping[str, Any]], float],
    entry_block_fn: Optional[Callable[[Mapping[str, Any]], bool]] = None,
) -> CapacityReplayState:
    spec = build_spec(leverage=LEVERAGE, cap=CAP, stop_policy=STOP_POLICY)
    state = CapacityReplayState(
        scenario_id=mode,
        max_concurrent_positions=CAP,
        spec=spec,
        initial_equity=float(STARTING_EQUITY),
        equity_floor=float(STARTING_EQUITY) * 0.5,
        pnl_resolver=lambda *a, **k: 0.0,
        exit_mode=mode,
        shadow_by_key=dict(shadow_by_key),
        entry_block_fn=entry_block_fn,
        baseline_accepted_keys=set(),
    )

    entry_heap: list[tuple[float, float, datetime, int, dict[str, Any]]] = []
    for i, trade in enumerate(candidates):
        ent = _parse_ts(str(trade.get("entry_time") or ""))
        if ent is None:
            continue
        rk = rank_fn(trade)
        bucket = _scan_bucket(ent)
        heapq.heappush(entry_heap, (bucket, -rk, ent, i, dict(trade)))

    exit_heap: list[tuple[datetime, int, str, dict[str, Any]]] = []
    open_trade: dict[str, dict[str, Any]] = {}

    if entry_heap:
        first_day = _day_from_ts(datetime.fromtimestamp(entry_heap[0][0], tz=JST).isoformat())
        state._record_equity(ts="", day=first_day, event_type="start")

    while entry_heap or exit_heap:
        next_entry = entry_heap[0] if entry_heap else None
        next_exit = exit_heap[0] if exit_heap else None
        if next_exit is not None and (next_entry is None or next_exit[0] <= next_entry[2]):
            ex_dt, _, key, trade = heapq.heappop(exit_heap)
            ts = ex_dt.isoformat()
            day = _day_from_ts(ts)
            si = shadow_by_key.get(key) or ShadowExitInfo(0, "", 0, 0, 0, False, False)
            pnl, reason = state._close_pnl(trade, si)
            state.close_position_at(trade, ts=ts, day=day, exit_reason=reason, pnl_yen=pnl)
            open_trade.pop(key, None)
            continue
        _, _, ent_dt, _, trade = heapq.heappop(entry_heap)
        ts = ent_dt.isoformat()
        day = _day_from_ts(ts)
        if state.try_entry(trade, ts, day):
            key = _position_key(trade)
            si = shadow_by_key.get(key) or ShadowExitInfo(0, "", 0, 0, 0, False, False)
            ex_dt = state._exit_dt(trade, si)
            open_trade[key] = trade
            heapq.heappush(exit_heap, (ex_dt, 1, key, trade))
            state._record_equity(ts=ts, day=day, event_type="entry")

    if state.open_positions:
        last_ts = max(
            (_parse_ts(str(t.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST) for t in open_trade.values()),
            default=datetime.now(JST),
        ).isoformat()
        state._force_close_all(last_ts, _day_from_ts(last_ts), reason="end_of_period")
    return state


def _metrics_from_state(state: CapacityReplayState, *, variant: str) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym = _symbol_pnl_from_log(state.trade_log)
    return {
        "variant": variant,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "accepted_count": state.accepted_trade_count,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "daily_pnl_618": round(float(state.daily_pnls.get(DAY_618, 0.0)), 2),
        "daily_pnl_619": round(float(state.daily_pnls.get(DAY_619, 0.0)), 2),
        **{f"symbol_pnl_{k}": sym.get(k, 0.0) for k in ("6976", "6920", "4062")},
    }


def _load_day_events(kabu: Path, day: str) -> list[dict[str, str]]:
    base = kabu / "results" / "small_paper" / day
    rows: list[dict[str, str]] = []
    if not base.is_dir():
        return rows
    for sess in sorted(base.iterdir()):
        path = sess / "small_paper_events.csv"
        if path.is_file():
            with path.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    rows.append(dict(row))
    return rows


def _analyze_missed_uptrend(
    enriched_by_key: Mapping[str, Mapping[str, Any]],
    *,
    kabu: Path,
) -> list[dict[str, Any]]:
    events = _load_day_events(kabu, TARGET_DAY_619)
    by_sym: dict[str, list[dict[str, str]]] = defaultdict(list)
    for ev in events:
        by_sym[str(ev.get("symbol") or "")].append(ev)

    rows: list[dict[str, Any]] = []
    for sym in MISSED_UPTREND_SYMBOLS:
        evs = by_sym.get(sym, [])
        accepted = [e for e in evs if e.get("event_type") == "accepted"]
        candidates = [e for e in evs if e.get("event_type") in ("candidate", "notify", "scan_candidate")]
        rejects = [e for e in evs if e.get("event_type") == "reject"]

        cand_trade = None
        for e in candidates:
            key = f"{sym}|{e.get('entry_time')}"
            if key in enriched_by_key:
                cand_trade = enriched_by_key[key]
                break
        if cand_trade is None:
            for t in enriched_by_key.values():
                if str(t.get("symbol")) == sym and str(t.get("day", ""))[:8] == TARGET_DAY_619:
                    cand_trade = t
                    break

        gate_pass = bool(cand_trade and _passes_baseline_mid_high(cand_trade))
        if cand_trade and gate_pass:
            gate_pass = not _runtime_entry_block_mid_high(_weak_shape_block)(cand_trade) or _passes_baseline_mid_high(cand_trade)

        was_candidate = bool(candidates or cand_trade)
        rank = _base_rank(cand_trade) if cand_trade else None

        if accepted:
            reason = "accepted_other_session"
        elif not was_candidate and not cand_trade:
            reason = "never_candidate"
        elif not gate_pass:
            reason = "gate_blocked"
        elif rejects:
            r0 = rejects[0].get("reject_reason") or rejects[0].get("reason") or "rejected"
            reason = f"rejected:{r0}"
        elif not accepted:
            reason = "rank_or_cap_miss"
        else:
            reason = "unknown"

        rows.append(
            {
                "symbol": sym,
                "day": TARGET_DAY_619,
                "shape_class": (cand_trade or {}).get("eod_shape_class", "uptrend"),
                "open_to_close_return_pct": "",
                "was_candidate": was_candidate,
                "gate_would_pass": gate_pass if cand_trade else False,
                "in_universe_dynamic40": str((cand_trade or {}).get("universe_bucket") or "").lower() == "dynamic",
                "failure_reason": reason,
                "candidate_rank_score": round(rank, 2) if rank is not None else "",
                "board_bucket": _board_bucket(cand_trade) if cand_trade else "",
                "momentum_category": "low" if cand_trade else "",
                "detail": f"events={len(evs)} accepted={len(accepted)} candidates={len(candidates)} rejects={len(rejects)}",
            }
        )
    return rows


def run_phase459_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    enriched = _enrich_candidates(_load_candidate_stream(repo_root), kabu=kabu)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    sector_map = read_jpx_sector_map(kabu)
    for t in enriched:
        t.update(enrich_trade_phase456_features(t, price_idx=price_idx, sector_map=sector_map))
        t.update(enrich_trade_phase456c_features(t, price_idx=price_idx))

    by_key = {f"{t.get('symbol')}|{t.get('entry_time')}": t for t in enriched}
    np_shadows = _precompute_np_shadows(enriched, kabu=kabu, np_policy=BEST_NP_POLICY)

    def _block(tr: Mapping[str, Any]) -> bool:
        return _runtime_entry_block_mid_high(_weak_shape_block)(tr)

    baseline_state = _simulate_rank_replay(
        enriched,
        np_shadows,
        mode=REPLAY_MODE,
        rank_fn=_base_rank,
        entry_block_fn=_block,
    )

    accepted_rows: list[dict[str, Any]] = []
    for log_row in baseline_state.trade_log:
        tr = dict(log_row.get("trade") or {})
        tr["pnl_yen"] = log_row.get("pnl_yen")
        tr["pnl_yen_100"] = log_row.get("pnl_yen")
        tr["exit_reason"] = log_row.get("exit_reason")
        tr["exit_time"] = log_row.get("exit_time")
        tr["hold_sec"] = log_row.get("hold_sec")
        key = f"{tr.get('symbol')}|{tr.get('entry_time')}"
        if key in by_key:
            tr.update({k: v for k, v in by_key[key].items() if k not in tr or tr[k] in (None, "")})
        accepted_rows.append(tr)

    wins = sorted([t for t in accepted_rows if _pnl_yen(t) > 0], key=lambda t: _pnl_yen(t), reverse=True)
    losses = sorted([t for t in accepted_rows if _pnl_yen(t) < 0], key=lambda t: _pnl_yen(t))
    win_top = wins[:100] if len(wins) > 100 else wins
    loss_top = losses[:100] if len(losses) > 100 else losses

    numeric_keys = [k for k in WINNER_FIELDS if k not in ("symbol", "entry_time", "exit_time", "exit_reason", "board_bucket", "winner_pattern")]
    medians: dict[str, float] = {}
    for k in numeric_keys:
        vals = [_float(_feature_row(t).get(k)) for t in win_top if _float(_feature_row(t).get(k)) is not None]
        if vals:
            medians[k] = statistics.median(vals)

    winner_csv: list[dict[str, Any]] = []
    for t in win_top:
        feat = _feature_row(t)
        pat = _classify_winner_pattern(t, med=medians)
        winner_csv.append(
            {
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "pnl_yen_100": _pnl_yen(t),
                "exit_reason": t.get("exit_reason"),
                "hold_sec": t.get("hold_sec"),
                **feat,
                "winner_pattern": pat,
            }
        )

    compare_rows: list[dict[str, Any]] = []
    effects: list[tuple[float, str]] = []
    for k in numeric_keys:
        wv = [_float(_feature_row(t).get(k)) for t in win_top if _float(_feature_row(t).get(k)) is not None]
        lv = [_float(_feature_row(t).get(k)) for t in loss_top if _float(_feature_row(t).get(k)) is not None]
        if not wv or not lv:
            continue
        d = _cohens_d(wv, lv)
        effects.append((abs(d), k))
        compare_rows.append(
            {
                "feature": k,
                "winner_mean": round(statistics.mean(wv), 4),
                "winner_median": round(statistics.median(wv), 4),
                "winner_p25": _pct(wv, 0.25),
                "winner_p75": _pct(wv, 0.75),
                "loser_mean": round(statistics.mean(lv), 4),
                "loser_median": round(statistics.median(lv), 4),
                "loser_p25": _pct(lv, 0.25),
                "loser_p75": _pct(lv, 0.75),
                "cohens_d": d,
                "effect_rank": 0,
            }
        )
    effects.sort(reverse=True)
    rank_map = {k: i + 1 for i, (_, k) in enumerate(effects)}
    for r in compare_rows:
        r["effect_rank"] = rank_map.get(r["feature"], 99)
    compare_rows.sort(key=lambda r: int(r.get("effect_rank") or 99))

    pattern_stats: dict[str, list[dict]] = defaultdict(list)
    for t in wins:
        pattern_stats[_classify_winner_pattern(t, med=medians)].append(t)
    pattern_rows = []
    for pat, grp in sorted(pattern_stats.items()):
        pnls = [_pnl_yen(t) for t in grp]
        holds = [_float(t.get("hold_sec")) or 0 for t in grp]
        stops = sum(1 for t in grp if "stop" in str(t.get("exit_reason") or "").lower())
        pattern_rows.append(
            {
                "pattern": pat,
                "count": len(grp),
                "total_pnl": round(sum(pnls), 2),
                "pf": _pf(pnls),
                "stop_rate": round(stops / len(grp), 4) if grp else 0,
                "avg_hold_sec": round(statistics.mean(holds), 2) if holds else 0,
            }
        )
    best_pattern = max(pattern_rows, key=lambda r: float(r.get("total_pnl") or 0)) if pattern_rows else {}

    hu_thr = medians.get("high_update_count_30m", 2)
    cat_thr = medians.get("consecutive_above_ticks", 5)
    var_thr = medians.get("vwap_above_ratio", 0.6)

    def _bonus_high_update(t: Mapping[str, Any]) -> float:
        return 800.0 if (_float(t.get("high_update_count_30m")) or 0) >= hu_thr else 0.0

    def _bonus_vwap(t: Mapping[str, Any]) -> float:
        ok = (_float(t.get("vwap_above_ratio_20tick")) or 0) >= var_thr and (_float(t.get("consecutive_above_ticks")) or 0) >= cat_thr
        return 800.0 if ok else 0.0

    def _bonus_trend(t: Mapping[str, Any]) -> float:
        m = _map_runtime_fields(t)
        r15 = _float(m.get("return_15min_pct") or m.get("entry_rise_15min_pct"))
        r30 = _float(m.get("return_30min_pct") or m.get("entry_rise_30min_pct"))
        return 800.0 if (r15 or 0) > 0 and (r30 or 0) > 0 else 0.0

    def _bonus_board(t: Mapping[str, Any]) -> float:
        return 500.0 if _board_bucket(t) == "high" else 0.0

    def _bonus_sector(t: Mapping[str, Any]) -> float:
        ok = (_float(t.get("sector_return_15m")) or 0) > 0 and (_float(t.get("relative_strength_vs_sector")) or 0) > 0
        return 500.0 if ok else 0.0

    base_bonuses: dict[str, Callable[[Mapping[str, Any]], float]] = {
        "B_high_update_bonus": _bonus_high_update,
        "C_vwap_stability_bonus": _bonus_vwap,
        "D_trend_continuation_bonus": _bonus_trend,
        "E_board_high_bonus": _bonus_board,
        "F_sector_follow_bonus": _bonus_sector,
    }

    def _combo_bonus(t: Mapping[str, Any]) -> float:
        scores = [(fn(t)) for fn in base_bonuses.values()]
        scores.sort(reverse=True)
        return sum(scores[:2])

    bonus_variants: dict[str, Callable[[Mapping[str, Any]], float]] = {
        "A_baseline": lambda t: 0.0,
        **base_bonuses,
        "G_best_two_bonus_combo": _combo_bonus,
    }

    sim_rows: list[dict[str, Any]] = []
    base_m = _metrics_from_state(baseline_state, variant="A_baseline")
    sim_rows.append(base_m)
    base_pnl = float(base_m["total_pnl_yen"])
    base_pf = float(base_m["profit_factor"] or 0)
    base_dd = float(base_m["max_drawdown_yen"] or 0)

    for vid, bonus in bonus_variants.items():
        if vid == "A_baseline":
            continue
        st = _simulate_rank_replay(
            enriched,
            np_shadows,
            mode=REPLAY_MODE,
            rank_fn=lambda t, b=bonus: _base_rank(t) + b(t),
            entry_block_fn=_block,
        )
        m = _metrics_from_state(st, variant=vid)
        m["delta_pnl_vs_baseline"] = round(float(m["total_pnl_yen"]) - base_pnl, 2)
        m["delta_pf_vs_baseline"] = round(float(m["profit_factor"] or 0) - base_pf, 4)
        m["delta_maxdd_vs_baseline"] = round(float(m["max_drawdown_yen"] or 0) - base_dd, 2)
        m["delta_daily_pnl_618"] = round(float(m["daily_pnl_618"]) - float(base_m["daily_pnl_618"]), 2)
        m["delta_daily_pnl_619"] = round(float(m["daily_pnl_619"]) - float(base_m["daily_pnl_619"]), 2)
        for sym in ("6976", "6920", "4062"):
            m[f"delta_symbol_pnl_{sym}"] = round(
                float(m.get(f"symbol_pnl_{sym}") or 0) - float(base_m.get(f"symbol_pnl_{sym}") or 0),
                2,
            )
        sim_rows.append(m)

    best_sim = max(sim_rows[1:], key=lambda r: float(r.get("delta_pnl_vs_baseline") or 0)) if len(sim_rows) > 1 else base_m
    rank_improved = float(best_sim.get("delta_pnl_vs_baseline") or 0) > 3000

    missed_rows = _analyze_missed_uptrend(by_key, kabu=kabu)

    sym6976_wins = [t for t in wins if str(t.get("symbol", "")).startswith("6976")]
    sym4062_wins = [t for t in wins if str(t.get("symbol", "")).startswith("4062")]
    pat6976 = _classify_winner_pattern(sym6976_wins[0], med=medians) if sym6976_wins else None
    pat4062 = _classify_winner_pattern(sym4062_wins[0], med=medians) if sym4062_wins else None

    board_high_share = sum(1 for t in wins if _board_bucket(t) == "high") / len(wins) if wins else 0
    vwap_stable_share = sum(1 for t in wins if _classify_winner_pattern(t, med=medians) == "B_vwap_stable_above") / len(wins) if wins else 0
    uptrend_share = sum(1 for t in wins if _classify_winner_pattern(t, med=medians) == "A_uptrend_continuation") / len(wins) if wins else 0

    top5_common = [r["feature"] for r in compare_rows[:5]]
    top5_separation = [r["feature"] for r in sorted(compare_rows, key=lambda r: abs(float(r.get("cohens_d") or 0)), reverse=True)[:5]]

    missed_cause = (
        "gate_blocked — uptrend symbols had candidates but failed ENTRY gate (Momentum/Board/score)"
        if missed_rows and all(r.get("failure_reason") == "gate_blocked" for r in missed_rows)
        else "rank_or_cap_miss / never_candidate on 6/19"
    )

    actionable_top5 = [
        r["feature"]
        for r in compare_rows
        if r["feature"] not in ("price", "notional_100shares", "trading_value")
    ][:5]

    if rank_improved:
        verdict = "rank_bonus_candidate"
    elif pattern_rows and max(float(p.get("total_pnl") or 0) for p in pattern_rows) > 150000:
        verdict = "winner_pattern_found"
    elif missed_rows and all(r.get("failure_reason") == "gate_blocked" for r in missed_rows):
        verdict = "universe_problem_confirmed"
    else:
        verdict = "no_winner_pattern"

    mandatory = {
        "1_winner_common_top5": actionable_top5 or top5_common,
        "2_best_separation_top5": actionable_top5 or top5_separation,
        "3_strongest_pattern": max(pattern_rows, key=lambda r: float(r.get("total_pnl") or 0)).get("pattern") if pattern_rows else None,
        "4_6976_winner_pattern": pat6976,
        "5_4062_winner_pattern": pat4062,
        "6_board_high_in_winners": board_high_share >= 0.15,
        "7_vwap_stable_in_winners": vwap_stable_share >= 0.1,
        "8_uptrend_continuation_in_winners": uptrend_share >= 0.1,
        "9_rank_bonus_candidate": best_sim.get("variant"),
        "10_rank_bonus_pnl_improved": rank_improved,
        "11_missed_uptrend_cause": missed_cause,
        "12_runtime_candidate": False,
        "13_next_actions": [
            f"Shadow rank bonus: {best_sim.get('variant')}" if rank_improved else "No rank bonus edge",
            "Universe expansion for missed uptrend symbols" if missed_rows else "Review winner pattern gates",
        ],
        "verdict": verdict,
        "winner_count": len(wins),
        "loser_count": len(losses),
        "best_rank_sim_delta": best_sim.get("delta_pnl_vs_baseline"),
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "pattern_summary": pattern_rows,
        "mandatory_answers": mandatory,
        "verdict": verdict,
        "_winner_rows": winner_csv,
        "_compare_rows": compare_rows,
        "_sim_rows": [{k: v for k, v in r.items() if not k.startswith("_")} for r in sim_rows],
        "_missed_rows": missed_rows,
    }


@dataclass
class Phase459Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase459_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "winners": reports / "phase459_winner_pattern_audit.csv",
            "compare": reports / "phase459_winner_loser_feature_compare.csv",
            "rank_sim": reports / "phase459_rank_bonus_simulation.csv",
            "missed": reports / "phase459_missed_uptrend_analysis.csv",
            "summary": reports / "phase459_winner_pattern_summary.json",
        }
        _write_csv(paths["winners"], WINNER_FIELDS, list(result.get("_winner_rows") or []))
        _write_csv(paths["compare"], COMPARE_FIELDS, list(result.get("_compare_rows") or []))
        _write_csv(paths["rank_sim"], RANK_SIM_FIELDS, list(result.get("_sim_rows") or []))
        _write_csv(paths["missed"], MISSED_FIELDS, list(result.get("_missed_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase459_winner_pattern_audit.md"
        m = result.get("mandatory_answers") or {}
        report.write_text(
            "\n".join(
                [
                    "# Phase459 — Winner Pattern Audit",
                    "",
                    f"Generated: {result.get('generated_at')}",
                    f"Period: {result.get('period_start')}..{result.get('period_end')}",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. Winner common TOP5: **{m.get('1_winner_common_top5')}**",
                    f"2. Separation TOP5: **{m.get('2_best_separation_top5')}**",
                    f"3. Strongest pattern: **{m.get('3_strongest_pattern')}**",
                    f"4. 6976 pattern: **{m.get('4_6976_winner_pattern')}**",
                    f"5. 4062 pattern: **{m.get('5_4062_winner_pattern')}**",
                    f"6. Board:high in winners: **{m.get('6_board_high_in_winners')}**",
                    f"7. VWAP stable: **{m.get('7_vwap_stable_in_winners')}**",
                    f"8. Uptrend continuation: **{m.get('8_uptrend_continuation_in_winners')}**",
                    f"9. Rank bonus candidate: **{m.get('9_rank_bonus_candidate')}**",
                    f"10. Rank bonus improved PnL: **{m.get('10_rank_bonus_pnl_improved')}**",
                    f"11. Missed uptrend cause: **{m.get('11_missed_uptrend_cause')}**",
                    f"12. Runtime candidate: **{m.get('12_runtime_candidate')}**",
                    f"13. Next: {m.get('13_next_actions')}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        paths["report"] = report
        return paths
