"""
Phase461 — Near Day High Guard Exception Audit (research only).

Audits near_day_high_low_momentum_dynamic40_guard rejections and tests
exception conditions without removing the guard wholesale.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv, read_jpx_sector_map
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import _price_at_or_before, _stream_events, guard_high_drift
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import (
    _chronological_pnls_from_log,
    _symbol_pnl_from_log,
)
from research.phase459_winner_pattern_audit import _stop_rate_from_log
from research.phase382_capital_constrained_backtest import _position_key
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY, _precompute_np_shadows
from research.phase451_entry_shape_tournament import (
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _enrich_candidates,
    _load_candidate_stream,
    _now_iso,
    _optional_float,
)
from research.phase451b_entry_shape_tournament_mid_high import (
    _board_token,
    _passes_baseline_mid_high,
    _v2_entry_score,
)
from research.phase456_entry_features import enrich_trade_phase456_features
from research.phase456c_vwap_structure_features import enrich_trade_phase456c_features
from research.phase365_production_stack_validation import phase364_blocked_only
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.near_day_high_low_momentum_dynamic40_entry_guard import (
    REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD,
)
from small_paper.weak_shape_reject_entry_guard import would_block_weak_shape_reject

JST = ZoneInfo("Asia/Tokyo")
REPLAY_MODE = "phase456_runtime_np"
GUARD_REASON = REJECT_NEAR_DAY_HIGH_LOW_MOMENTUM_DYNAMIC40_GUARD
TARGET_SYMBOLS = ("6976.T", "4062.T", "3441.T", "6492.T", "7256.T", "7600.T")

AUDIT_FIELDS = [
    "symbol",
    "day",
    "entry_time",
    "would_pnl",
    "win_flag",
    "day_high_distance_pct",
    "entry_momentum_score",
    "r5",
    "r10",
    "r15",
    "r30",
    "vwap_dev",
    "vwap_above_ratio",
    "consecutive_above_ticks",
    "high_update_age",
    "high_update_count_30m",
    "board_bucket",
    "gate_reject_reason",
]

TOURNAMENT_FIELDS = [
    "variant",
    "rescued_count",
    "rescued_win_count",
    "rescued_loss_count",
    "rescued_pnl_yen",
    "remaining_blocked_pnl_yen",
    "total_pnl_yen",
    "delta_pnl_vs_baseline",
    "profit_factor",
    "delta_pf_vs_baseline",
    "max_drawdown_yen",
    "delta_maxdd_vs_baseline",
    "stop_rate",
    "accepted_count",
    "captured_6976",
    "captured_4062",
    "captured_3441",
    "captured_6492",
    "captured_7256",
    "captured_7600",
]

COMPARE_FIELDS = [
    "feature",
    "winner_mean",
    "winner_median",
    "loser_mean",
    "loser_median",
    "delta_mean",
    "effect_size_cohens_d",
    "rank_by_abs_effect",
]


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _iter_sessions(kabu_root: Path) -> list[tuple[str, Path]]:
    base = kabu_root / "results" / "small_paper"
    out: list[tuple[str, Path]] = []
    if not base.is_dir():
        return out
    for day_dir in sorted(base.iterdir()):
        if not day_dir.is_dir():
            continue
        day = day_dir.name
        if day < PERIOD_START or day > PERIOD_END:
            continue
        for sess in sorted(day_dir.iterdir()):
            if sess.is_dir() and sess.name.startswith("live_session"):
                out.append((day, sess))
    return out


def _is_dynamic40(row: Mapping[str, Any]) -> bool:
    slot = str(row.get("universe_slot") or "").lower()
    bucket = str(row.get("universe_bucket") or "").lower()
    grp = str(row.get("universe_group") or "").lower()
    src = str(row.get("source_bucket") or "").lower()
    return (
        slot == "dynamic"
        or bucket in ("dynamic", "dynamic40")
        or grp == "dynamic40"
        or "dynamic40" in src
    )


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
    tok = _board_token(trade) or ""
    return tok.split(":", 1)[-1] if ":" in tok else "unknown"


def _rise(trade: Mapping[str, Any], mins: int) -> Optional[float]:
    return _float(trade.get(f"return_{mins}min_pct")) or _float(trade.get(f"entry_rise_{mins}min_pct"))


def _vwap_above_ratio(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("vwap_above_ratio")) or _float(trade.get("vwap_above_ratio_20tick"))


def _high_update_age(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("last_high_update_age_min")) or _float(trade.get("minutes_since_day_high_update"))


def _feature_row(trade: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "r5": _rise(trade, 5),
        "r10": _rise(trade, 10),
        "r15": _rise(trade, 15),
        "r30": _rise(trade, 30),
        "vwap_dev": _float(trade.get("entry_vwap_dev_pct")) or _float(trade.get("vwap_dev_pct")),
        "vwap_above_ratio": _vwap_above_ratio(trade),
        "consecutive_above_ticks": _float(trade.get("consecutive_above_ticks")),
        "high_update_age": _high_update_age(trade),
        "high_update_count_30m": _float(trade.get("high_update_count_30m")),
        "board_bucket": _board_bucket(trade),
    }


def _load_guard_blocks(kabu: Path) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    sym_day_dynamic: set[tuple[str, str]] = set()

    for day, sess in _iter_sessions(kabu):
        for row in _stream_events(sess / "small_paper_events.csv"):
            if _is_dynamic40(row):
                sym_day_dynamic.add((str(row.get("symbol") or ""), day))

        rejects_path = sess / "small_paper_rejects.csv"
        if rejects_path.is_file():
            with rejects_path.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    reason = str(row.get("gate_reject_reason") or row.get("reject_reason") or "")
                    if reason != GUARD_REASON:
                        continue
                    if not _is_dynamic40(row):
                        sym = str(row.get("symbol") or "")
                        if (sym, day) not in sym_day_dynamic:
                            continue
                    et = str(row.get("entry_time") or row.get("event_time") or "")
                    sym = str(row.get("symbol") or "")
                    if not sym or not et:
                        continue
                    key = f"{sym}|{et}"
                    rec = records.setdefault(
                        key,
                        {
                            "symbol": sym,
                            "day": day,
                            "entry_time": et,
                            "entry_price": _float(row.get("entry_price") or row.get("current_price")),
                            "gate_reject_reason": reason,
                            "outcome": "rejected",
                        },
                    )
                    for fld in row:
                        if row.get(fld) not in (None, ""):
                            rec[fld] = row[fld]

        for row in _stream_events(sess / "small_paper_events.csv"):
            reason = str(row.get("gate_reject_reason") or row.get("reject_reason") or "")
            etype = str(row.get("event_type") or "")
            if reason != GUARD_REASON and etype not in ("rejected", "reject"):
                continue
            if reason != GUARD_REASON:
                continue
            sym = str(row.get("symbol") or "")
            et = str(row.get("entry_time") or row.get("event_time") or "")
            if not sym or not et:
                continue
            if not _is_dynamic40(row) and (sym, day) not in sym_day_dynamic:
                continue
            key = f"{sym}|{et}"
            rec = records.setdefault(
                key,
                {
                    "symbol": sym,
                    "day": day,
                    "entry_time": et,
                    "entry_price": _float(row.get("entry_price") or row.get("current_price")),
                    "gate_reject_reason": reason,
                    "outcome": "rejected",
                },
            )
            for fld in row:
                if row.get(fld) not in (None, ""):
                    rec[fld] = row[fld]

    return list(records.values())


def _feature_stats(vals: Sequence[float]) -> dict[str, Optional[float]]:
    if not vals:
        return {"mean": None, "median": None}
    return {
        "mean": round(statistics.mean(vals), 4),
        "median": round(statistics.median(vals), 4),
    }


def _cohens_d(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    sa, sb = statistics.pstdev(a), statistics.pstdev(b)
    pooled = math.sqrt((sa * sa + sb * sb) / 2.0)
    if pooled <= 1e-12:
        return 0.0
    return round((ma - mb) / pooled, 4)


def _exception_specs() -> dict[str, Callable[[Mapping[str, Any]], bool]]:
    def _gt0(v: Optional[float]) -> bool:
        return v is not None and v > 0

    a = lambda t: _gt0(_rise(t, 15))
    b = lambda t: _gt0(_rise(t, 30))
    c = lambda t: (_float(t.get("high_update_count_30m")) or 0) >= 2
    d = lambda t: (_vwap_above_ratio(t) or 0) >= 0.7
    e = lambda t: (_float(t.get("consecutive_above_ticks")) or 0) >= 20
    return {
        "A_r15_gt0": a,
        "B_r30_gt0": b,
        "C_high_update_ge2": c,
        "D_vwap_above_ge07": d,
        "E_consec_above_ge20": e,
        "F_A_and_B": lambda t: a(t) and b(t),
        "G_A_and_D": lambda t: a(t) and d(t),
        "H_B_and_D": lambda t: b(t) and d(t),
        "I_A_and_B_and_D": lambda t: a(t) and b(t) and d(t),
    }


def _runtime_baseline_block(trade: Mapping[str, Any]) -> bool:
    if not _passes_baseline_mid_high(trade):
        return True
    if guard_high_drift(trade):
        return True
    if _weak_shape_block(trade):
        return True
    if phase364_blocked_only(trade):
        return True
    return False


def _runtime_exception_block(exception_fn: Callable[[Mapping[str, Any]], bool]):
    def block(trade: Mapping[str, Any]) -> bool:
        if not _passes_baseline_mid_high(trade):
            return True
        if guard_high_drift(trade):
            return True
        if _weak_shape_block(trade):
            return True
        if phase364_blocked_only(trade) and not exception_fn(trade):
            return True
        return False

    return block


def _replay_metrics(state: Any, *, variant: str) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym_pnl = _symbol_pnl_from_log(state.trade_log)
    accepted_syms = {str(r.get("symbol") or "") for r in state.trade_log}
    return {
        "variant": variant,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "accepted_count": state.accepted_trade_count,
        "daily_pnl_619": round(float(state.daily_pnls.get(DAY_619, 0.0)), 2),
        **{f"captured_{s.replace('.T', '')}": s in accepted_syms for s in TARGET_SYMBOLS},
        **{f"symbol_pnl_{s.replace('.T', '')}": sym_pnl.get(s.replace(".T", ""), 0.0) for s in TARGET_SYMBOLS},
    }


def _compare_features(
    winners: Sequence[Mapping[str, Any]],
    losers: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    numeric = (
        "r5",
        "r10",
        "r15",
        "r30",
        "vwap_dev",
        "vwap_above_ratio",
        "consecutive_above_ticks",
        "high_update_age",
        "high_update_count_30m",
    )
    rows: list[dict[str, Any]] = []
    for feat in numeric:
        w_vals = [_float(t.get(feat)) for t in winners]
        l_vals = [_float(t.get(feat)) for t in losers]
        w_vals = [x for x in w_vals if x is not None]
        l_vals = [x for x in l_vals if x is not None]
        ws, ls = _feature_stats(w_vals), _feature_stats(l_vals)
        delta = None
        if ws["mean"] is not None and ls["mean"] is not None:
            delta = round(ws["mean"] - ls["mean"], 4)
        effect = _cohens_d(w_vals, l_vals)
        rows.append(
            {
                "feature": feat,
                "winner_mean": ws["mean"],
                "winner_median": ws["median"],
                "loser_mean": ls["mean"],
                "loser_median": ls["median"],
                "delta_mean": delta,
                "effect_size_cohens_d": effect,
            }
        )
    rows.sort(key=lambda r: abs(float(r.get("effect_size_cohens_d") or 0)), reverse=True)
    for i, row in enumerate(rows, start=1):
        row["rank_by_abs_effect"] = i
    return rows


def _winner_loser_only_features(
    compare_rows: Sequence[Mapping[str, Any]],
    *,
    winners: Sequence[Mapping[str, Any]],
    losers: Sequence[Mapping[str, Any]],
) -> tuple[list[str], list[str]]:
    winner_only: list[str] = []
    loser_only: list[str] = []
    for row in compare_rows:
        feat = str(row.get("feature") or "")
        w_vals = [_float(t.get(feat)) for t in winners if _float(t.get(feat)) is not None]
        l_vals = [_float(t.get(feat)) for t in losers if _float(t.get(feat)) is not None]
        if not w_vals or not l_vals:
            continue
        w_med = statistics.median(w_vals)
        l_med = statistics.median(l_vals)
        if w_med > l_med and all(v <= w_med for v in l_vals):
            winner_only.append(feat)
        if l_med > w_med and all(v <= l_med for v in w_vals):
            loser_only.append(feat)
    return winner_only[:10], loser_only[:10]


def _verdict(
    *,
    guard_pnl: float,
    win_blocks: int,
    loss_blocks: int,
    best_delta: float,
    best_captures: int,
    best_variant: str,
) -> str:
    if best_captures >= 3 and best_delta > 0:
        return "near_high_exception_candidate"
    if best_captures >= 1 and best_delta > 10000:
        return "near_high_exception_candidate"
    if guard_pnl < -20000 and win_blocks < loss_blocks:
        return "guard_keep_no_exception"
    if win_blocks > loss_blocks * 1.2 and best_captures == 0:
        return "guard_keep_no_exception"
    if best_delta > 0 and best_captures == 0:
        return "exception_overfit"
    if best_delta > 0:
        return "near_high_exception_candidate"
    return "guard_keep_no_exception"


def _close_proxy_pnl(
    trade: Mapping[str, Any],
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> float:
    sym = str(trade.get("symbol") or "")
    day = str(trade.get("day") or "")[:8]
    ep = _float(trade.get("entry_price"))
    if not sym or not day or ep is None or ep <= 0:
        return 0.0
    series = price_idx.get((sym, day), [])
    if not series:
        return 0.0
    close_dt = datetime.strptime(f"{day} 15:30:00", "%Y%m%d %H:%M:%S").replace(tzinfo=JST)
    close_px = _price_at_or_before(series, close_dt)
    if close_px is None or close_px <= 0:
        return 0.0
    return round((close_px - ep) * 100.0, 2)


def _inject_guard_for_replay(
    guard_trades: Sequence[Mapping[str, Any]],
    specs: Mapping[str, Callable[[Mapping[str, Any]], bool]],
) -> list[dict[str, Any]]:
    """Inject 6/19 guard blocks so replay can evaluate uptrend symbol capture."""
    del specs
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for t in guard_trades:
        key = _position_key(t)
        if key in seen:
            continue
        if str(t.get("day") or "") != DAY_619:
            continue
        seen.add(key)
        out.append(dict(t))
    return out


def _enrich_guard_trade_light(
    rec: Mapping[str, Any],
    canon: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    key = f"{rec.get('symbol')}|{rec.get('entry_time')}"
    t = dict(canon.get(key, rec))
    t.update({k: v for k, v in rec.items() if v not in (None, "")})
    t.setdefault("day", rec.get("day"))
    px = _float(t.get("entry_price")) or _float(t.get("current_price"))
    if px:
        t["entry_price"] = px
    t = _map_runtime_fields(t)
    t["day_high_distance_pct"] = _optional_float(t.get("day_high_distance_pct")) or _optional_float(
        t.get("entry_near_day_high_pct")
    )
    t["entry_momentum_score"] = _float(t.get("entry_momentum_score")) or _float(
        t.get("momentum_continuation_score")
    )
    t.update(_feature_row(t))
    return t


def _enrich_guard_trade_full(
    rec: Mapping[str, Any],
    canon: Mapping[str, Mapping[str, Any]],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
    sector_map: Mapping[str, str],
) -> dict[str, Any]:
    t = _enrich_guard_trade_light(rec, canon)
    t.update(enrich_trade_phase456_features(t, price_idx=price_idx, sector_map=sector_map))
    t.update(enrich_trade_phase456c_features(t, price_idx=price_idx))
    t.update(_feature_row(t))
    return t


def run_phase461_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    raw_blocks = _load_guard_blocks(kabu)
    enriched_all = _enrich_candidates(_load_candidate_stream(repo_root), kabu=kabu)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    sector_map = read_jpx_sector_map(kabu)

    canon_by_key = {f"{t.get('symbol')}|{t.get('entry_time')}": dict(t) for t in enriched_all}
    guard_trades: list[dict[str, Any]] = []
    for rec in raw_blocks:
        t = _enrich_guard_trade_light(rec, canon_by_key)
        if phase364_blocked_only(t):
            guard_trades.append(t)

    specs = _exception_specs()
    specs["K_r5_gt0"] = lambda t: (_rise(t, 5) or -1e18) > 0
    specs["L_r5_and_vwap_dev_lt2"] = lambda t: (_rise(t, 5) or -1e18) > 0 and (_float(t.get("vwap_dev")) or 1e18) < 2.0
    inject_raw = [rec for rec in raw_blocks if str(rec.get("day") or "") == DAY_619]
    inject_guard = [
        _enrich_guard_trade_full(rec, canon_by_key, price_idx=price_idx, sector_map=sector_map)
        for rec in inject_raw
        if phase364_blocked_only(_enrich_guard_trade_light(rec, canon_by_key))
    ]
    inject_guard = list({_position_key(t): t for t in inject_guard}.values())

    enriched_keys = {_position_key(t) for t in enriched_all}
    replay_candidates = list(enriched_all) + [t for t in inject_guard if _position_key(t) not in enriched_keys]
    np_shadows = _precompute_np_shadows(replay_candidates, kabu=kabu, np_policy=BEST_NP_POLICY)

    # Full tick features for compare/tournament on 6/19 guard blocks (uptrend focus day).
    compare_pool = list(inject_guard)
    for t in compare_pool:
        key = _position_key(t)
        shadow = np_shadows.get(key)
        if shadow and shadow.eval_ok:
            t["would_pnl"] = float(shadow.shadow_pnl_yen)
            t["pnl_source"] = "np_shadow"
        else:
            t["would_pnl"] = _close_proxy_pnl(t, price_idx)
            t["pnl_source"] = "close_proxy"

    audit_rows: list[dict[str, Any]] = []
    for t in guard_trades:
        key = _position_key(t)
        shadow = np_shadows.get(key)
        if shadow and shadow.eval_ok:
            would_pnl = float(shadow.shadow_pnl_yen)
            pnl_source = "np_shadow"
        else:
            would_pnl = _close_proxy_pnl(t, price_idx)
            pnl_source = "close_proxy"
        t["would_pnl"] = would_pnl
        t["pnl_source"] = pnl_source
        feats = _feature_row(t)
        audit_rows.append(
            {
                "symbol": t.get("symbol"),
                "day": t.get("day"),
                "entry_time": t.get("entry_time"),
                "would_pnl": round(would_pnl, 2),
                "win_flag": would_pnl > 0,
                "day_high_distance_pct": t.get("day_high_distance_pct"),
                "entry_momentum_score": t.get("entry_momentum_score"),
                **feats,
                "gate_reject_reason": t.get("gate_reject_reason") or GUARD_REASON,
            }
        )

    pnls = [float(r["would_pnl"]) for r in audit_rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    win_blocks = len(wins)
    loss_blocks = len(losses)
    guard_pnl = round(sum(pnls), 2)

    winners = [t for t in guard_trades if float(t.get("would_pnl") or 0) > 0]
    losers = [t for t in guard_trades if float(t.get("would_pnl") or 0) < 0]
    compare_rows = _compare_features(winners, losers)
    compare_rows = [r for r in compare_rows if r.get("effect_size_cohens_d") is not None]
    for i, row in enumerate(compare_rows, start=1):
        row["rank_by_abs_effect"] = i
    winner_only, loser_only = _winner_loser_only_features(compare_rows, winners=winners, losers=losers)

    top2 = [r["feature"] for r in compare_rows[:2]] or ["r5", "vwap_dev"]
    feat_predicates: dict[str, Callable[[Mapping[str, Any]], bool]] = {
        "r15": lambda t: (_rise(t, 15) or -1e18) > 0,
        "r30": lambda t: (_rise(t, 30) or -1e18) > 0,
        "high_update_count_30m": lambda t: (_float(t.get("high_update_count_30m")) or 0) >= 2,
        "vwap_above_ratio": lambda t: (_vwap_above_ratio(t) or 0) >= 0.7,
        "consecutive_above_ticks": lambda t: (_float(t.get("consecutive_above_ticks")) or 0) >= 20,
        "r5": lambda t: (_rise(t, 5) or -1e18) > 0,
        "r10": lambda t: (_rise(t, 10) or -1e18) > 0,
        "vwap_dev": lambda t: (_float(t.get("vwap_dev")) or 1e18) < 2.0,
        "high_update_age": lambda t: (_high_update_age(t) or 1e18) <= 15,
    }
    if len(top2) >= 2:
        f1, f2 = top2[0], top2[1]
        p1, p2 = feat_predicates.get(f1, lambda _t: False), feat_predicates.get(f2, lambda _t: False)
        specs["J_best_two_feature_combo"] = lambda t, a=p1, b=p2: a(t) and b(t)

    baseline_state = simulate_capacity_replay(
        replay_candidates,
        np_shadows,
        mode=REPLAY_MODE,
        entry_block_fn=_runtime_baseline_block,
        baseline_accepted_keys=set(),
    )
    baseline = _replay_metrics(baseline_state, variant="baseline_guard")

    tournament_rows: list[dict[str, Any]] = []
    for vid, exc_fn in specs.items():
        rescued = [t for t in guard_trades if exc_fn(t)]
        remaining = [t for t in guard_trades if not exc_fn(t)]
        rescued_pnl = round(sum(float(t.get("would_pnl") or 0) for t in rescued), 2)
        remaining_pnl = round(sum(float(t.get("would_pnl") or 0) for t in remaining), 2)
        st = simulate_capacity_replay(
            replay_candidates,
            np_shadows,
            mode=f"phase461_{vid}",
            entry_block_fn=_runtime_exception_block(exc_fn),
            baseline_accepted_keys=set(),
        )
        m = _replay_metrics(st, variant=vid)
        m["rescued_count"] = len(rescued)
        m["rescued_win_count"] = sum(1 for t in rescued if float(t.get("would_pnl") or 0) > 0)
        m["rescued_loss_count"] = sum(1 for t in rescued if float(t.get("would_pnl") or 0) < 0)
        m["rescued_pnl_yen"] = rescued_pnl
        m["remaining_blocked_pnl_yen"] = remaining_pnl
        m["delta_pnl_vs_baseline"] = round(float(m["total_pnl_yen"]) - float(baseline["total_pnl_yen"]), 2)
        m["delta_pf_vs_baseline"] = round(float(m["profit_factor"] or 0) - float(baseline["profit_factor"] or 0), 4)
        m["delta_maxdd_vs_baseline"] = round(float(m["max_drawdown_yen"] or 0) - float(baseline["max_drawdown_yen"] or 0), 2)
        tournament_rows.append(m)

    eligible = [r for r in tournament_rows if not str(r["variant"]).startswith("I_")]
    uptrend_syms = ("3441", "6492", "7256", "7600")

    def _capture_score(row: Mapping[str, Any]) -> tuple[int, float]:
        caps = sum(1 for s in uptrend_syms if row.get(f"captured_{s}"))
        return (caps, float(row.get("delta_pnl_vs_baseline") or 0))

    best = max(eligible, key=_capture_score) if eligible else baseline
    best_variant = str(best.get("variant") or "baseline_guard")
    best_delta = float(best.get("delta_pnl_vs_baseline") or 0)
    best_captures = sum(1 for s in uptrend_syms if best.get(f"captured_{s}"))

    verdict = _verdict(
        guard_pnl=guard_pnl,
        win_blocks=win_blocks,
        loss_blocks=loss_blocks,
        best_delta=best_delta,
        best_captures=best_captures,
        best_variant=best_variant,
    )

    mandatory = {
        "1_guard_block_count": len(audit_rows),
        "2_guard_pnl_yen": guard_pnl,
        "2b_guard_pnl_median_yen": round(statistics.median(pnls), 2) if pnls else 0,
        "2c_guard_pnl_note": "close_to_close_100sh_proxy_sum_across_all_scan_rejects",
        "3_win_block_count": win_blocks,
        "4_loss_block_count": loss_blocks,
        "5_best_exception": best_variant,
        "6_pnl_improvement_yen": best_delta,
        "7_pf_improvement": best.get("delta_pf_vs_baseline"),
        "8_maxdd_change_yen": best.get("delta_maxdd_vs_baseline"),
        "9_captured_3441": bool(best.get("captured_3441")),
        "10_captured_6492": bool(best.get("captured_6492")),
        "11_captured_7256": bool(best.get("captured_7256")),
        "12_captured_7600": bool(best.get("captured_7600")),
        "13_runtime_candidate": verdict == "near_high_exception_candidate" and best_delta > 0 and best_captures >= 1,
        "14_shadow_candidate": verdict == "near_high_exception_candidate" or (best_delta > 0 and best_captures >= 1),
        "15_next_actions": [
            f"Shadow-test {best_variant} exception on near_day_high guard before runtime",
            "Do not remove guard wholesale — blocks net counterfactual losses" if guard_pnl < 0 else "Review guard thresholds",
            "Walk-forward validate exception on days after 6/19",
        ],
        "verdict": verdict,
        "guard_win_rate": round(win_blocks / max(len(audit_rows), 1), 4),
        "guard_pf": _pf(pnls),
        "np_shadow_rows": sum(1 for t in guard_trades if t.get("pnl_source") == "np_shadow"),
        "close_proxy_rows": sum(1 for t in guard_trades if t.get("pnl_source") == "close_proxy"),
        "replay_injected_guard_count": len(inject_guard),
        "compare_pool_day": DAY_619,
        "compare_pool_count": len(compare_pool),
        "winner_only_features": winner_only,
        "loser_only_features": loser_only,
        "top10_effect_features": compare_rows[:10],
        "baseline_replay": baseline,
        "best_exception_replay": best,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "mandatory_answers": mandatory,
        "verdict": verdict,
        "part_a_summary": {
            "count": len(audit_rows),
            "would_pnl_total": guard_pnl,
            "win_rate": mandatory["guard_win_rate"],
            "profit_factor": mandatory["guard_pf"],
        },
        "part_d_compare": compare_rows,
        "part_d_winner_only": winner_only,
        "part_d_loser_only": loser_only,
        "_audit_rows": audit_rows,
        "_tournament_rows": tournament_rows,
    }


@dataclass
class Phase461Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase461_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "audit": reports / "phase461_near_high_exception_audit.csv",
            "tournament": reports / "phase461_near_high_exception_tournament.csv",
            "summary": reports / "phase461_near_high_exception_summary.json",
        }
        _write_csv(paths["audit"], AUDIT_FIELDS, list(result.get("_audit_rows") or []))
        _write_csv(paths["tournament"], TOURNAMENT_FIELDS, list(result.get("_tournament_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase461_near_high_exception_audit.md"
        m = result.get("mandatory_answers") or {}
        part_a = result.get("part_a_summary") or {}
        compare = list(result.get("part_d_compare") or [])[:10]
        tournament = list(result.get("_tournament_rows") or [])

        lines = [
            "# Phase461 — Near Day High Guard Exception Audit",
            "",
            f"Generated: {result.get('generated_at')}",
            f"Period: {result.get('period_start')}..{result.get('period_end')}",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
            "## Part A — Guard監査",
            "",
            f"- block count: **{part_a.get('count')}**",
            f"- guard would_pnl: **{part_a.get('would_pnl_total')}**",
            f"- win_rate: **{part_a.get('win_rate')}**",
            f"- PF: **{part_a.get('profit_factor')}**",
            "",
            "## Part D — Winner vs Loser (TOP10 effect size)",
            "",
            "| feature | winner_mean | loser_mean | delta | cohens_d |",
            "|---|---:|---:|---:|---:|",
        ]
        for row in compare:
            lines.append(
                f"| {row.get('feature')} | {row.get('winner_mean')} | {row.get('loser_mean')} "
                f"| {row.get('delta_mean')} | {row.get('effect_size_cohens_d')} |"
            )
        lines.extend(
            [
                "",
                f"- 勝ち側だけが持つ特徴: {m.get('winner_only_features')}",
                f"- 負け側だけが持つ特徴: {m.get('loser_only_features')}",
                "",
                "## Part E/F — Exception Tournament & Replay",
                "",
                "| variant | rescued | ΔPnL | PF | maxDD | captured 3441/6492/7256/7600 |",
                "|---|---:|---:|---:|---:|---|",
            ]
        )
        for row in tournament:
            caps = "/".join(
                "Y" if row.get(f"captured_{s}") else "N"
                for s in ("3441", "6492", "7256", "7600")
            )
            lines.append(
                f"| {row.get('variant')} | {row.get('rescued_count')} | {row.get('delta_pnl_vs_baseline')} "
                f"| {row.get('profit_factor')} | {row.get('max_drawdown_yen')} | {caps} |"
            )
        lines.extend(
            [
                "",
                "## Mandatory answers",
                "",
                f"1. guard block件数: **{m.get('1_guard_block_count')}**",
                    f"2. guard PnL (close-hold proxy sum): **{m.get('2_guard_pnl_yen')}** (median **{m.get('2b_guard_pnl_median_yen')}**)",
                f"3. 勝ちblock: **{m.get('3_win_block_count')}**",
                f"4. 負けblock: **{m.get('4_loss_block_count')}**",
                f"5. 最良例外: **{m.get('5_best_exception')}**",
                f"6. PnL改善: **{m.get('6_pnl_improvement_yen')}**",
                f"7. PF改善: **{m.get('7_pf_improvement')}**",
                f"8. maxDD変化: **{m.get('8_maxdd_change_yen')}**",
                f"9–12. 3441/6492/7256/7600: **{m.get('9_captured_3441')}/{m.get('10_captured_6492')}/{m.get('11_captured_7256')}/{m.get('12_captured_7600')}**",
                f"13. Runtime候補: **{m.get('13_runtime_candidate')}**",
                f"14. Shadow候補: **{m.get('14_shadow_candidate')}**",
                f"15. 次アクション: {m.get('15_next_actions')}",
                "",
            ]
        )
        report.write_text("\n".join(lines), encoding="utf-8")
        paths["report"] = report
        return paths
