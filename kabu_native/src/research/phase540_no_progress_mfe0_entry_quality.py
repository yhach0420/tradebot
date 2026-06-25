"""
Phase540 — NoProgress / MFE0 entry quality root cause study (research only).

Live paper observer_exit trades. No Runtime changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase463_trend_pullback_population_tournament import _momentum_score
from research.phase465b_trend_gate_redesign import _cohens_d
from research.phase507_classic_indicators import Bar1m, ticks_to_1m_bars
from research.phase509_t15_t13_signal_audit import MIN_BARS_WARMUP, _bar_at_entry
from research.phase515b_day_high_breakout_dependency_audit import (
    _bar_index_at,
    _high_update_stats,
    _session_open_ts,
)
from research.phase518_day_high_winner_loser_separation import (
    _build_micro_lookup,
    _extract_entry_features,
    _percentile,
    _separation_score,
)
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    _build_bar_cache_for_days,
    _entry_indicators,
    _is_stop_low_mfe,
    _num,
    _prior_breaks_fixed,
)
from research.phase527_entry_quality_guard import _chron_pnls
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.canonical_summary import collect_canonical_trades

PHASE540_VERDICT = "phase540_no_progress_mfe0_entry_quality_done"
DEFAULT_DAY = "20260625"
MAX_WORKERS = 4

ENTRY_FEATURE_IDS: tuple[str, ...] = (
    "board_imbalance",
    "spread_bps",
    "volume",
    "volume_ratio",
    "volume_percentile",
    "vwap_distance_pct",
    "rsi14",
    "adx14",
    "atr",
    "momentum_score",
    "update_count_before_entry",
    "day_high_distance_pct",
    "day_high_update_speed",
    "five_min_position",
    "moving_average_position",
    "prior_low_break",
    "prior_high_break",
    "high_update_recent",
    "pullback_after_spike",
    "trend_direction",
    "open_strength",
    "day_return_rank",
    "minutes_from_open",
)

GUARD_IDS: tuple[str, ...] = (
    "A_baseline",
    "G1_spread_le40",
    "G2_volume_pct_ge80",
    "G3_adx_le30",
    "G4_update_le4",
    "G5_trend_not_down",
    "G6_no_prior_low_break",
    "G7_high_update_recent",
    "G8_spread40_vol80",
    "G9_trend_no_plb",
    "G10_spread_trend_no_plb",
    "G11_mfe0_best_feature",
    "G12_mfe0_best_2feature",
)

GUARD_SHADOW_FIELDS = [
    "guard_id",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trade_count",
    "win_rate",
    "mfe0_count",
    "stop_low_mfe_count",
    "no_progress_count",
    "blocked_trade_count",
    "blocked_future_mfe_median",
    "blocked_future_pnl_yen_100",
    "lost_winner_count",
    "prevented_mfe0_count",
    "net_improvement_yen_100",
]


def _load_events(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append(dict(row))
    return rows


def _resolved_exit_reason(row: Mapping[str, Any]) -> str:
    reason = str(row.get("exit_reason") or "").strip()
    structural = str(row.get("structural_exit_reason") or "").strip()
    if reason == "overlap_replaced_review":
        return structural or reason
    return reason or structural


def _mfe_pct(row: Mapping[str, Any]) -> float:
    for key in ("peak_mfe_pct", "mfe_pct", "rolling_mfe_pct"):
        v = row.get(key)
        if v not in (None, ""):
            return _num(v)
    return 0.0


def _mae_pct(row: Mapping[str, Any]) -> float:
    for key in ("rolling_mae_pct", "mae_pct"):
        v = row.get(key)
        if v not in (None, ""):
            return _num(v)
    return 0.0


def _is_mfe0(row: Mapping[str, Any], *, epsilon: float = 0.0) -> bool:
    return _mfe_pct(row) <= epsilon


def _is_mfe0_relaxed(row: Mapping[str, Any]) -> bool:
    return _mfe_pct(row) <= 0.01


def _is_winner(row: Mapping[str, Any]) -> bool:
    return _num(row.get("pnl_yen_100")) > 0


def _is_no_progress(row: Mapping[str, Any]) -> bool:
    return _resolved_exit_reason(row) == "no_progress_exit"


def _entry_type_label(row: Mapping[str, Any]) -> str:
    et = str(row.get("entry_type") or "").strip().upper()
    if not et or et == "PBV2":
        return "PBV2"
    if "OR" in et:
        return "OR"
    return et


def _or_pbv2_label(row: Mapping[str, Any]) -> str:
    return _entry_type_label(row)


def _cap_pool(row: Mapping[str, Any]) -> str:
    return str(row.get("cap_pool") or row.get("universe_bucket") or row.get("source_bucket") or "")


def _hold_sec(row: Mapping[str, Any]) -> float:
    hs = row.get("hold_sec")
    if hs not in (None, ""):
        return _num(hs)
    ent = _parse_ts(str(row.get("entry_time") or ""))
    ex = _parse_ts(str(row.get("exit_time") or ""))
    if ent and ex:
        return max(0.0, (ex - ent).total_seconds())
    return 0.0


def _select_session_dirs(day_dir: Path, *, session_filter: Optional[str] = None) -> list[Path]:
    """Pick session dir(s) for a day. Default: latest ``live_session_*`` only."""
    if session_filter:
        p = day_dir / session_filter
        return [p] if p.is_dir() else []
    sessions = sorted(day_dir.glob("live_session_*"))
    if not sessions:
        return []
    if len(sessions) == 1:
        return sessions
    # Multiple runs same day: use latest session unless caller passes --session.
    return [sessions[-1]]


def _load_canonical_trades_for_day(
    repo_root: Path,
    day: str,
    *,
    session: Optional[str] = None,
    all_sessions: bool = False,
) -> list[dict[str, Any]]:
    kabu = resolve_kabu_root(repo_root)
    day_dir = kabu / "results" / "small_paper" / day
    if not day_dir.is_dir():
        day_dir = kabu / "results" / "paper_trade" / day
    trades: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    if all_sessions:
        sess_dirs = sorted(day_dir.glob("live_session_*"))
    else:
        sess_dirs = _select_session_dirs(day_dir, session_filter=session)
    for sess_dir in sess_dirs:
        events_path = sess_dir / "small_paper_events.csv"
        if not events_path.is_file():
            continue
        events = _load_events(events_path)
        accepted: dict[str, dict[str, Any]] = {}
        for row in events:
            if str(row.get("event_type") or "") != "accepted":
                continue
            key = f"{row.get('symbol')}|{row.get('entry_time')}"
            accepted[key] = row
        for t in collect_canonical_trades(events):
            key = (str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
            if key in seen:
                continue
            seen.add(key)
            row = dict(t)
            row["day"] = day
            row["session"] = sess_dir.name
            acc = accepted.get(f"{key[0]}|{key[1]}", {})
            row["entry_type"] = acc.get("entry_type") or row.get("entry_type") or "PBV2"
            row["cap_pool"] = (
                acc.get("universe_bucket")
                or acc.get("source_bucket")
                or row.get("universe_bucket")
                or row.get("source_bucket")
                or ""
            )
            row["exit_reason"] = _resolved_exit_reason(row)
            row["mfe_pct"] = _mfe_pct(row)
            row["mae_pct"] = _mae_pct(row)
            row["pnl_yen_100"] = round(_num(row.get("pnl_yen_100")), 2)
            row["pnl_pct"] = _num(row.get("pnl_pct"))
            row["hold_sec"] = round(_hold_sec(row), 1)
            trades.append(row)
    trades.sort(
        key=lambda r: _parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST)
    )
    return trades


def _duplicate_flags(trades: Sequence[Mapping[str, Any]]) -> dict[tuple[str, str], bool]:
    sym_counts: Counter[str] = Counter()
    for t in trades:
        sym_counts[str(t.get("symbol") or "")] += 1
    out: dict[tuple[str, str], bool] = {}
    for t in trades:
        sym = str(t.get("symbol") or "")
        key = (sym, str(t.get("entry_time") or ""))
        out[key] = sym_counts[sym] > 1
    return out


def _five_min_position(bars: Sequence[Bar1m], i: int) -> Optional[float]:
    start = max(0, i - 4)
    window = bars[start : i + 1]
    if not window:
        return None
    hi = max(b.high for b in window)
    lo = min(b.low for b in window)
    close = bars[i].close
    if hi <= lo:
        return 50.0
    return round((close - lo) / (hi - lo) * 100.0, 4)


def _trend_direction(bars: Sequence[Bar1m], ind_rows: Sequence[Any], i: int) -> Optional[str]:
    if i < 5:
        return None
    ind = ind_rows[i].values
    ind_prev = ind_rows[i - 5].values
    ema = ind.get("EMA20")
    ema_prev = ind_prev.get("EMA20")
    close = bars[i].close
    if ema is None or ema_prev is None:
        return None
    if close > float(ema) and float(ema) > float(ema_prev):
        return "up"
    if close < float(ema) and float(ema) < float(ema_prev):
        return "down"
    return "sideways"


def _day_return_rank(trades: Sequence[Mapping[str, Any]], bar_cache: Mapping) -> dict[str, float]:
    returns: list[tuple[str, float]] = []
    for t in trades:
        sym_t = f"{str(t.get('symbol') or '').replace('.T', '')}.T"
        day = str(t.get("day") or "")[:8]
        ent = _parse_ts(str(t.get("entry_time") or ""))
        cached = bar_cache.get((sym_t, day))
        if not cached or ent is None:
            continue
        bars, _ = cached
        ei = _bar_index_at(bars, ent)
        if ei is None or not bars:
            continue
        open_px = bars[0].open
        close = bars[ei].close
        if open_px <= 0:
            continue
        ret = (close - open_px) / open_px * 100.0
        key = f"{sym_t}|{t.get('entry_time')}"
        returns.append((key, ret))
    if not returns:
        return {}
    vals = sorted(r for _, r in returns)
    out: dict[str, float] = {}
    for key, ret in returns:
        rank = sum(1 for v in vals if v <= ret)
        out[key] = round(rank / len(vals) * 100.0, 2)
    return out


def _phase540_entry_features(
    trade: Mapping[str, Any],
    *,
    bar_cache: Mapping,
    micro_lookup: Mapping,
    day_return_ranks: Mapping[str, float],
) -> dict[str, Any]:
    feats518 = _extract_entry_features(dict(trade), bar_cache=bar_cache, micro_lookup=micro_lookup)
    ind = _entry_indicators(trade, bar_cache)
    plb, phb = _prior_breaks_fixed(trade, bar_cache)
    sym_t = f"{str(trade.get('symbol') or '').replace('.T', '')}.T"
    day = str(trade.get("day") or "")[:8]
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    ex = _parse_ts(str(trade.get("exit_time") or ""))
    out: dict[str, Any] = {fid: None for fid in ENTRY_FEATURE_IDS}

    spread = feats518.get("spread")
    if spread is None and trade.get("spread_bps") not in (None, ""):
        spread = _num(trade.get("spread_bps"))

    board = feats518.get("board_imbalance")
    if board is None and trade.get("entry_order_book_imbalance") not in (None, ""):
        board = _num(trade.get("entry_order_book_imbalance"))

    uc = feats518.get("update_count_before_entry")
    if uc is None and trade.get("update_count_before_entry") not in (None, ""):
        uc = int(_num(trade.get("update_count_before_entry")))

    rsi = ind.get("rsi14")
    if rsi is None and trade.get("rsi14") not in (None, ""):
        rsi = _num(trade.get("rsi14"))

    mom = _momentum_score(trade)
    if mom is None and trade.get("entry_momentum_continuation_score") not in (None, ""):
        mom = _num(trade.get("entry_momentum_continuation_score"))
    if mom is None and trade.get("entry_momentum_score") not in (None, ""):
        mom = _num(trade.get("entry_momentum_score"))

    mins_open = feats518.get("minutes_from_open")
    day_high_dist = _num(trade.get("day_high_distance_pct")) if trade.get("day_high_distance_pct") not in (None, "") else None
    if day_high_dist is None:
        near = trade.get("entry_near_day_high_pct")
        if near not in (None, ""):
            day_high_dist = _num(near)

    mins_since_high = trade.get("minutes_since_day_high_update")
    high_update_speed = None
    if mins_since_high not in (None, "") and float(mins_since_high) > 0:
        high_update_speed = round(float(uc or 0) / float(mins_since_high), 4) if uc is not None else None

    high_recent = trade.get("entry_high_break_recent")
    if high_recent in (None, ""):
        if mins_since_high not in (None, ""):
            high_recent = float(mins_since_high) <= 15.0
    else:
        high_recent = str(high_recent).lower() in ("true", "1", "yes")

    rise10 = _num(trade.get("entry_rise_10min_pct")) if trade.get("entry_rise_10min_pct") not in (None, "") else 0.0
    drawdown = _num(trade.get("high_to_now_drawdown_pct")) if trade.get("high_to_now_drawdown_pct") not in (None, "") else 0.0
    pullback_spike = rise10 > 1.0 and drawdown < -0.3

    atr_val = None
    five_min_pos = None
    ma_pos = feats518.get("price_vs_ema20")
    trend_dir = None
    volume = None
    cached = bar_cache.get((sym_t, day))
    if cached and ent is not None:
        bars, ind_rows = cached
        ei = _bar_index_at(bars, ent)
        if ei is not None:
            volume = bars[ei].volume
            atr_raw = ind_rows[ei].values.get("ATR14")
            if atr_raw is not None and bars[ei].close > 0:
                atr_val = round(float(atr_raw) / bars[ei].close * 100.0, 4)
            five_min_pos = _five_min_position(bars, ei)
            trend_dir = _trend_direction(bars, ind_rows, ei)
            if day_high_dist is None and ei >= 0:
                day_hi = max(b.high for b in bars[: ei + 1])
                if day_hi > 0:
                    day_high_dist = round((bars[ei].close - day_hi) / day_hi * 100.0, 4)

    rank_key = f"{sym_t}|{trade.get('entry_time')}"
    out.update(
        {
            "board_imbalance": board,
            "spread_bps": spread,
            "volume": volume,
            "volume_ratio": feats518.get("volume_ratio"),
            "volume_percentile": feats518.get("rolling_volume_percentile"),
            "vwap_distance_pct": feats518.get("vwap_distance_pct"),
            "rsi14": rsi,
            "adx14": ind.get("adx14") or feats518.get("adx14"),
            "atr": atr_val,
            "momentum_score": mom,
            "update_count_before_entry": uc,
            "day_high_distance_pct": day_high_dist,
            "day_high_update_speed": high_update_speed,
            "five_min_position": five_min_pos,
            "moving_average_position": ma_pos,
            "prior_low_break": plb,
            "prior_high_break": phb,
            "high_update_recent": high_recent,
            "pullback_after_spike": pullback_spike,
            "trend_direction": trend_dir,
            "open_strength": _num(trade.get("open_strength")) if trade.get("open_strength") not in (None, "") else None,
            "day_return_rank": day_return_ranks.get(rank_key),
            "minutes_from_open": mins_open,
        }
    )
    return out


def _enrich_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    bar_cache: Mapping,
    micro_lookup: Mapping,
) -> list[dict[str, Any]]:
    day_return_ranks = _day_return_rank(trades, bar_cache)
    dup = _duplicate_flags(trades)
    enriched: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        feats = _phase540_entry_features(
            row, bar_cache=bar_cache, micro_lookup=micro_lookup, day_return_ranks=day_return_ranks
        )
        row.update(feats)
        key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
        row["duplicate_entry_observed"] = dup.get(key, False)
        row["or_pbv2"] = _or_pbv2_label(row)
        enriched.append(row)
    return enriched


def _mfe_bucket(row: Mapping[str, Any]) -> str:
    if _is_winner(row):
        return "D_winner"
    mfe = _mfe_pct(row)
    if mfe <= 0.0:
        return "A_mfe0"
    if mfe <= 0.2:
        return "B_low_mfe"
    if mfe <= 0.5:
        return "C_mid_mfe"
    return "D_winner" if _is_winner(row) else "C_mid_mfe"


def _bucket_summary_row(bucket: str, rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_num(r.get("pnl_yen_100")) for r in rows]
    mfes = [_mfe_pct(r) for r in rows]
    maes = [_mae_pct(r) for r in rows]
    holds = [_hold_sec(r) for r in rows]
    wins = sum(1 for p in pnls if p > 0)
    return {
        "bucket": bucket,
        "trade_count": len(rows),
        "total_pnl_yen_100": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "win_rate": round(wins / len(rows), 4) if rows else 0.0,
        "avg_mfe_pct": round(statistics.mean(mfes), 4) if mfes else None,
        "avg_mae_pct": round(statistics.mean(maes), 4) if maes else None,
        "avg_hold_sec": round(statistics.mean(holds), 1) if holds else None,
        "stop_hit_count": sum(1 for r in rows if r.get("stop_hit") in (True, "True", "true", "1")),
        "no_progress_count": sum(1 for r in rows if _is_no_progress(r)),
    }


def _feature_separation_rows(
    winners: Sequence[Mapping[str, Any]],
    mfe0: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feat in ENTRY_FEATURE_IDS:
        wv_num = [_num(r.get(feat)) for r in winners if r.get(feat) is not None and not isinstance(r.get(feat), bool)]
        mv_num = [_num(r.get(feat)) for r in mfe0 if r.get(feat) is not None and not isinstance(r.get(feat), bool)]
        wv_bool = [1.0 if r.get(feat) else 0.0 for r in winners if isinstance(r.get(feat), bool)]
        mv_bool = [1.0 if r.get(feat) else 0.0 for r in mfe0 if isinstance(r.get(feat), bool)]
        if feat in ("prior_low_break", "prior_high_break", "high_update_recent", "pullback_after_spike"):
            wv, mv = wv_bool, mv_bool
        else:
            wv, mv = wv_num, mv_num
        w_miss = 1.0 - (len(wv) / len(winners)) if winners else 0.0
        m_miss = 1.0 - (len(mv) / len(mfe0)) if mfe0 else 0.0
        if len(wv) < 2 or len(mv) < 2:
            rows.append(
                {
                    "feature": feat,
                    "winner_median": None,
                    "mfe0_median": None,
                    "winner_p25": None,
                    "winner_p75": None,
                    "mfe0_p25": None,
                    "mfe0_p75": None,
                    "winner_mean": None,
                    "mfe0_mean": None,
                    "winner_missing_rate": round(w_miss, 4),
                    "mfe0_missing_rate": round(m_miss, 4),
                    "cohens_d": None,
                    "separation_score": None,
                }
            )
            continue
        rows.append(
            {
                "feature": feat,
                "winner_median": round(statistics.median(wv), 6),
                "mfe0_median": round(statistics.median(mv), 6),
                "winner_p25": _percentile(wv, 25),
                "winner_p75": _percentile(wv, 75),
                "mfe0_p25": _percentile(mv, 25),
                "mfe0_p75": _percentile(mv, 75),
                "winner_mean": round(statistics.mean(wv), 6),
                "mfe0_mean": round(statistics.mean(mv), 6),
                "winner_missing_rate": round(w_miss, 4),
                "mfe0_missing_rate": round(m_miss, 4),
                "cohens_d": round(_cohens_d(wv, mv), 4),
                "separation_score": round(_separation_score(wv, mv), 4),
            }
        )
    rows.sort(key=lambda r: abs(_num(r.get("separation_score"))), reverse=True)
    return rows


def _best_threshold_rule(
    feat: str,
    winners: Sequence[Mapping[str, Any]],
    mfe0: Sequence[Mapping[str, Any]],
) -> Optional[tuple[str, Any]]:
    row = next((r for r in _feature_separation_rows(winners, mfe0) if r.get("feature") == feat), None)
    if not row or row.get("winner_median") is None or row.get("mfe0_median") is None:
        return None
    wm = _num(row["winner_median"])
    mm = _num(row["mfe0_median"])
    if isinstance(winners[0].get(feat) if winners else None, bool):
        return feat, True if wm > mm else False
    if wm >= mm:
        thr = _percentile([_num(r.get(feat)) for r in mfe0 if r.get(feat) is not None], 75)
        return feat, ("ge", thr if thr is not None else wm)
    thr = _percentile([_num(r.get(feat)) for r in mfe0 if r.get(feat) is not None], 25)
    return feat, ("le", thr if thr is not None else wm)


def _eval_threshold(feats: Mapping[str, Any], feat: str, rule: Any) -> bool:
    val = feats.get(feat)
    if val is None:
        return True
    if isinstance(rule, bool):
        return bool(val) == rule
    op, thr = rule
    if thr is None:
        return True
    if op == "ge":
        return _num(val) >= float(thr)
    return _num(val) <= float(thr)


def _guard_allows(guard_id: str, feats: Mapping[str, Any], *, best_rules: Mapping[str, Any]) -> bool:
    spread = feats.get("spread_bps")
    vol_pct = feats.get("volume_percentile")
    adx = feats.get("adx14")
    uc = feats.get("update_count_before_entry")
    trend = str(feats.get("trend_direction") or "")
    plb = feats.get("prior_low_break")
    high_recent = feats.get("high_update_recent")

    def _spread_ok(th: float) -> bool:
        return spread is not None and float(spread) <= th

    def _vol_ok(th: float) -> bool:
        return vol_pct is not None and float(vol_pct) >= th

    def _adx_ok(th: float) -> bool:
        return adx is not None and float(adx) <= th

    def _uc_ok(th: float) -> bool:
        return uc is not None and float(uc) <= th

    if guard_id == "A_baseline":
        return True
    if guard_id == "G1_spread_le40":
        return _spread_ok(40.0)
    if guard_id == "G2_volume_pct_ge80":
        return _vol_ok(80.0)
    if guard_id == "G3_adx_le30":
        return _adx_ok(30.0)
    if guard_id == "G4_update_le4":
        return _uc_ok(4.0)
    if guard_id == "G5_trend_not_down":
        return trend != "down"
    if guard_id == "G6_no_prior_low_break":
        return plb is not True
    if guard_id == "G7_high_update_recent":
        return high_recent is True
    if guard_id == "G8_spread40_vol80":
        return _spread_ok(40.0) and _vol_ok(80.0)
    if guard_id == "G9_trend_no_plb":
        return trend != "down" and plb is not True
    if guard_id == "G10_spread_trend_no_plb":
        return _spread_ok(40.0) and trend != "down" and plb is not True
    if guard_id == "G11_mfe0_best_feature":
        rule = best_rules.get("g11")
        if not rule:
            return True
        f, r = rule
        return _eval_threshold(feats, f, r)
    if guard_id == "G12_mfe0_best_2feature":
        rules = best_rules.get("g12") or []
        if not rules:
            return True
        return all(_eval_threshold(feats, f, r) for f, r in rules)
    return True


def _guard_metrics(
    accepted: Sequence[Mapping[str, Any]],
    blocked: Sequence[Mapping[str, Any]],
    baseline_pnl: float,
) -> dict[str, Any]:
    pnls = [_num(t.get("pnl_yen_100")) for t in accepted]
    blocked_pnls = [_num(t.get("pnl_yen_100")) for t in blocked]
    blocked_mfe = [_mfe_pct(t) for t in blocked]
    wins = sum(1 for p in pnls if p > 0)
    total = round(sum(pnls), 2)
    return {
        "total_pnl_yen_100": total,
        "profit_factor": _pf(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(_chron_pnls(accepted)) if accepted else 0.0, 2),
        "trade_count": len(pnls),
        "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
        "mfe0_count": sum(1 for t in accepted if _is_mfe0(t)),
        "stop_low_mfe_count": sum(1 for t in accepted if _is_stop_low_mfe(t)),
        "no_progress_count": sum(1 for t in accepted if _is_no_progress(t)),
        "blocked_trade_count": len(blocked),
        "blocked_future_mfe_median": round(statistics.median(blocked_mfe), 4) if blocked_mfe else None,
        "blocked_future_pnl_yen_100": round(sum(blocked_pnls), 2),
        "lost_winner_count": sum(1 for p in blocked_pnls if p > 0),
        "prevented_mfe0_count": sum(1 for t in blocked if _is_mfe0(t)),
        "net_improvement_yen_100": round(total - baseline_pnl, 2),
    }


def _no_progress_subgroup(row: Mapping[str, Any]) -> str:
    if not _is_no_progress(row):
        return "other"
    mfe = _mfe_pct(row)
    if mfe <= 0.0:
        return "mfe0_no_progress"
    if mfe <= 0.2:
        return "low_mfe_no_progress"
    return "positive_mfe_no_progress"


def _hypothesis_checks(sep_rows: Sequence[Mapping[str, Any]], enriched: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    winners = [r for r in enriched if _is_winner(r)]
    mfe0 = [r for r in enriched if _is_mfe0(r)]
    by_feat = {str(r["feature"]): r for r in sep_rows if r.get("feature")}

    def _dir(feat: str, higher_worse: bool = False) -> Optional[str]:
        row = by_feat.get(feat)
        if not row or row.get("mfe0_median") is None or row.get("winner_median") is None:
            return "insufficient_data"
        wm = _num(row["winner_median"])
        mm = _num(row["mfe0_median"])
        if higher_worse:
            return "yes" if mm > wm else "no"
        return "yes" if mm < wm else "no"

    mfe0_pb = [r for r in mfe0 if r.get("pullback_after_spike")]
    mfe0_down = [r for r in mfe0 if str(r.get("trend_direction") or "") == "down"]
    return {
        "mfe0_wider_spread": _dir("spread_bps", higher_worse=True),
        "mfe0_worse_board_imbalance": _dir("board_imbalance"),
        "mfe0_weaker_volume": _dir("volume"),
        "mfe0_weaker_volume_ratio": _dir("volume_ratio"),
        "mfe0_worse_vwap_distance": _dir("vwap_distance_pct"),
        "mfe0_rsi_extreme": (
            "yes"
            if mfe0
            and statistics.median([_num(r.get("rsi14")) for r in mfe0 if r.get("rsi14") is not None] or [50])
            < statistics.median([_num(r.get("rsi14")) for r in winners if r.get("rsi14") is not None] or [50])
            else "mixed"
        ),
        "mfe0_bad_five_min_position": _dir("five_min_position"),
        "mfe0_not_high_update_recent": (
            "yes"
            if sum(1 for r in mfe0 if r.get("high_update_recent") is True)
            < sum(1 for r in winners if r.get("high_update_recent") is True)
            else "no"
        ),
        "mfe0_pullback_misread": "yes" if len(mfe0_pb) >= max(1, len(mfe0) // 2) else "partial",
        "mfe0_counter_trend": "yes" if len(mfe0_down) >= max(1, len(mfe0) // 2) else "partial",
    }


def _mandatory_answers(
    trades: Sequence[Mapping[str, Any]],
    enriched: Sequence[Mapping[str, Any]],
    guard_rows: Sequence[Mapping[str, Any]],
    sep_rows: Sequence[Mapping[str, Any]],
    np_analysis: Sequence[Mapping[str, Any]],
    hypotheses: Mapping[str, Any],
) -> dict[str, Any]:
    no_progress = [t for t in trades if _is_no_progress(t)]
    mfe0_strict = [t for t in trades if _is_mfe0(t)]
    mfe0_relaxed = [t for t in trades if _is_mfe0_relaxed(t)]
    losers = [t for t in trades if _num(t.get("pnl_yen_100")) < 0]
    mfe0_loss = sum(_num(t.get("pnl_yen_100")) for t in mfe0_strict)
    total_loss = sum(_num(t.get("pnl_yen_100")) for t in losers)

    baseline = next((g for g in guard_rows if g.get("guard_id") == "A_baseline"), {})
    best = max(
        (g for g in guard_rows if g.get("guard_id") != "A_baseline"),
        key=lambda g: (_num(g.get("net_improvement_yen_100")), _num(g.get("total_pnl_yen_100"))),
        default={},
    )
    top_feat = sep_rows[0]["feature"] if sep_rows else None

    np_rows = {r["subgroup"]: r for r in np_analysis if r.get("subgroup") != "all_trades"}
    mfe0_np = np_rows.get("mfe0_no_progress", {})
    all_np_pnl = sum(_num(t.get("pnl_yen_100")) for t in no_progress)
    non_np_pnl = sum(_num(t.get("pnl_yen_100")) for t in trades if not _is_no_progress(t))

    adopt = (
        _num(best.get("net_improvement_yen_100")) > 0
        and _num(best.get("lost_winner_count", 0)) <= max(1, len(trades) // 5)
        and _num(best.get("prevented_mfe0_count", 0)) >= 1
    )

    return {
        "1_no_progress_exit_count": len(no_progress),
        "2_mfe0_count_strict": len(mfe0_strict),
        "2_mfe0_count_relaxed_0_01": len(mfe0_relaxed),
        "3_mfe0_primary_loss_driver": (
            abs(mfe0_loss) >= abs(total_loss) * 0.35 if total_loss < 0 else False
        ),
        "3_mfe0_loss_yen_100": round(mfe0_loss, 2),
        "3_total_loss_yen_100": round(total_loss, 2),
        "4_mfe0_common_traits": hypotheses,
        "5_top_separation_feature": top_feat,
        "6_mfe0_pullback_misread": hypotheses.get("mfe0_pullback_misread"),
        "7_mfe0_counter_trend_bounce": hypotheses.get("mfe0_counter_trend"),
        "8_no_progress_exit_effective": all_np_pnl > total_loss * 0.5 if total_loss < 0 else "n/a",
        "9_preventable_at_entry": _num(mfe0_np.get("trade_count", 0)) > 0,
        "10_entry_guard_v2_candidates_exist": len(guard_rows) > 1,
        "11_best_guard_candidate": best.get("guard_id"),
        "12_production_adoption_candidate": adopt,
        "13_next_phase": (
            "Forward-shadow best guard on 5+ live days; validate MFE0 block rate vs lost winners."
            if adopt
            else "Collect more days; refine G11/G12 thresholds; inspect false-negative winners."
        ),
        "no_progress_total_pnl": round(all_np_pnl, 2),
        "non_no_progress_total_pnl": round(non_np_pnl, 2),
        "baseline_pnl": baseline.get("total_pnl_yen_100"),
        "best_guard_net_improvement": best.get("net_improvement_yen_100"),
    }


@dataclass
class Phase540Job:
    repo_root: Path
    days: Sequence[str]
    session: Optional[str] = None
    all_sessions: bool = False
    parallel: bool = True
    max_workers: int = MAX_WORKERS

    def run(self) -> dict[str, Any]:
        repo_root = self.repo_root.resolve()
        days = list(self.days)
        period_end = max(days) if days else DEFAULT_DAY
        price_idx = _build_price_index_to(resolve_kabu_root(repo_root), period_end=period_end)
        all_trades: list[dict[str, Any]] = []
        session_used: dict[str, str] = {}
        for day in days:
            day_trades = _load_canonical_trades_for_day(
                repo_root,
                day,
                session=self.session,
                all_sessions=self.all_sessions,
            )
            if day_trades:
                session_used[day] = str(day_trades[0].get("session") or "")
            all_trades.extend(day_trades)

        symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in all_trades})
        bar_cache = _build_bar_cache_for_days(repo_root, days=days, symbols=symbols, price_idx=price_idx)
        micro_lookup = _build_micro_lookup(all_trades)
        enriched = _enrich_trades(all_trades, bar_cache=bar_cache, micro_lookup=micro_lookup)

        dup_flags = _duplicate_flags(all_trades)
        no_progress_rows = []
        for t in enriched:
            if not _is_no_progress(t):
                continue
            sym = str(t.get("symbol") or "")
            key = (sym, str(t.get("entry_time") or ""))
            no_progress_rows.append(
                {
                    "symbol": sym,
                    "entry_time": t.get("entry_time"),
                    "entry_price": t.get("entry_price"),
                    "exit_time": t.get("exit_time"),
                    "exit_price": t.get("exit_price"),
                    "exit_reason": t.get("exit_reason"),
                    "pnl_yen_100": t.get("pnl_yen_100"),
                    "pnl_pct": t.get("pnl_pct"),
                    "MFE": round(_mfe_pct(t), 4),
                    "MAE": round(_mae_pct(t), 4),
                    "hold_sec": t.get("hold_sec"),
                    "hold_min": round(_hold_sec(t) / 60.0, 2),
                    "entry_type": _entry_type_label(t),
                    "or_pbv2": _or_pbv2_label(t),
                    "duplicate_entry_observed": dup_flags.get(key, False),
                }
            )

        mfe0_rows = []
        for t in enriched:
            if not (_is_mfe0(t) or _is_mfe0_relaxed(t)):
                continue
            mfe0_rows.append(
                {
                    "symbol": t.get("symbol"),
                    "entry_time": t.get("entry_time"),
                    "entry_price": t.get("entry_price"),
                    "exit_time": t.get("exit_time"),
                    "exit_reason": t.get("exit_reason"),
                    "MFE": round(_mfe_pct(t), 4),
                    "MAE": round(_mae_pct(t), 4),
                    "hold_sec": t.get("hold_sec"),
                    "pnl_yen_100": t.get("pnl_yen_100"),
                    "pnl_pct": t.get("pnl_pct"),
                    "entry_type": _entry_type_label(t),
                    "or_pbv2": _or_pbv2_label(t),
                    "cap_pool": _cap_pool(t),
                    "mfe0_strict": _is_mfe0(t),
                    "mfe0_relaxed_0_01": _is_mfe0_relaxed(t),
                }
            )

        bucket_order = ("A_mfe0", "B_low_mfe", "C_mid_mfe", "D_winner")
        bucket_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in enriched:
            b = _mfe_bucket(t)
            bucket_map[b].append(dict(t))
        bucket_summary = [_bucket_summary_row(b, bucket_map.get(b, [])) for b in bucket_order]

        feature_missing: dict[str, float] = {}
        entry_feature_rows = []
        for t in enriched:
            row = {
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "exit_reason": t.get("exit_reason"),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "MFE": round(_mfe_pct(t), 4),
            }
            for fid in ENTRY_FEATURE_IDS:
                row[fid] = t.get(fid)
                if t.get(fid) is None:
                    feature_missing[fid] = feature_missing.get(fid, 0) + 1
            entry_feature_rows.append(row)
        missing_rates = {
            fid: round(feature_missing.get(fid, 0) / len(enriched), 4) if enriched else 0.0
            for fid in ENTRY_FEATURE_IDS
        }

        winners = [r for r in enriched if _is_winner(r)]
        mfe0_only = [r for r in enriched if _is_mfe0(r)]
        sep_rows = _feature_separation_rows(winners, mfe0_only)
        hypotheses = _hypothesis_checks(sep_rows, enriched)

        best_rules: dict[str, Any] = {}
        ranked = [r for r in sep_rows if r.get("separation_score") is not None]
        if ranked:
            f1 = str(ranked[0]["feature"])
            r1 = _best_threshold_rule(f1, winners, mfe0_only)
            if r1:
                best_rules["g11"] = r1
            if len(ranked) > 1:
                f2 = str(ranked[1]["feature"])
                r2 = _best_threshold_rule(f2, winners, mfe0_only)
                rules = [best_rules["g11"]] if best_rules.get("g11") else []
                if r2:
                    rules.append(r2)
                best_rules["g12"] = rules

        baseline_pnl = round(sum(_num(t.get("pnl_yen_100")) for t in enriched), 2)
        guard_rows = []
        for gid in GUARD_IDS:
            accepted, blocked = [], []
            for t in enriched:
                feats = {fid: t.get(fid) for fid in ENTRY_FEATURE_IDS}
                if _guard_allows(gid, feats, best_rules=best_rules):
                    accepted.append(t)
                else:
                    blocked.append(t)
            guard_rows.append({"guard_id": gid, **_guard_metrics(accepted, blocked, baseline_pnl)})

        np_subgroups = ["mfe0_no_progress", "low_mfe_no_progress", "positive_mfe_no_progress", "all_no_progress"]
        np_analysis = []
        for sg in np_subgroups:
            if sg == "all_no_progress":
                subset = [t for t in enriched if _is_no_progress(t)]
            else:
                subset = [t for t in enriched if _no_progress_subgroup(t) == sg]
            pnls = [_num(t.get("pnl_yen_100")) for t in subset]
            np_analysis.append(
                {
                    "subgroup": sg,
                    "trade_count": len(subset),
                    "total_pnl_yen_100": round(sum(pnls), 2),
                    "avg_mfe_pct": round(statistics.mean([_mfe_pct(t) for t in subset]), 4) if subset else None,
                    "avg_hold_sec": round(statistics.mean([_hold_sec(t) for t in subset]), 1) if subset else None,
                    "avg_mae_pct": round(statistics.mean([_mae_pct(t) for t in subset]), 4) if subset else None,
                    "loss_share_of_day": round(sum(p for p in pnls if p < 0), 2),
                }
            )
        non_np_loss = sum(_num(t.get("pnl_yen_100")) for t in enriched if not _is_no_progress(t) and _num(t.get("pnl_yen_100")) < 0)
        np_analysis.append(
            {
                "subgroup": "counterfactual_hold_no_np_losses",
                "trade_count": len(no_progress_rows),
                "total_pnl_yen_100": round(non_np_loss, 2),
                "notes": "Rough floor: non-NoProgress realized losses on day",
            }
        )

        mandatory = _mandatory_answers(enriched, enriched, guard_rows, sep_rows, np_analysis, hypotheses)

        return {
            "verdict": PHASE540_VERDICT,
            "generated_at": _now_iso(),
            "target_days": days,
            "session_filter": self.session,
            "all_sessions": self.all_sessions,
            "sessions_used": session_used,
            "trade_count": len(enriched),
            "feature_missing_rates": missing_rates,
            "best_guard_rules": best_rules,
            "mandatory_answers": mandatory,
            "hypothesis_checks": hypotheses,
            "no_progress_trades": no_progress_rows,
            "mfe0_trades": mfe0_rows,
            "mfe_bucket_summary": bucket_summary,
            "entry_features": entry_feature_rows,
            "feature_separation": sep_rows,
            "no_progress_analysis": np_analysis,
            "guard_v2_shadow": guard_rows,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "no_progress_trades": reports / "phase540_no_progress_trades.csv",
            "mfe0_trades": reports / "phase540_mfe0_trades.csv",
            "mfe_bucket_summary": reports / "phase540_mfe_bucket_summary.csv",
            "entry_features": reports / "phase540_entry_features.csv",
            "feature_separation": reports / "phase540_feature_separation.csv",
            "no_progress_analysis": reports / "phase540_no_progress_analysis.csv",
            "guard_v2_shadow": reports / "phase540_guard_v2_shadow.csv",
            "report": reports / "phase540_report.json",
            "docs": kabu / "docs" / "operations" / "phase540_no_progress_mfe0_entry_quality.md",
        }

        np_fields = list((result.get("no_progress_trades") or [{}])[0].keys()) if result.get("no_progress_trades") else []
        mfe0_fields = list((result.get("mfe0_trades") or [{}])[0].keys()) if result.get("mfe0_trades") else []
        bucket_fields = list((result.get("mfe_bucket_summary") or [{}])[0].keys()) if result.get("mfe_bucket_summary") else []
        ef_fields = (
            ["symbol", "entry_time", "exit_time", "exit_reason", "pnl_yen_100", "MFE", *ENTRY_FEATURE_IDS]
        )
        sep_fields = list((result.get("feature_separation") or [{}])[0].keys()) if result.get("feature_separation") else []
        npa_fields = list((result.get("no_progress_analysis") or [{}])[0].keys()) if result.get("no_progress_analysis") else []

        _write_csv(paths["no_progress_trades"], np_fields, list(result.get("no_progress_trades") or []))
        _write_csv(paths["mfe0_trades"], mfe0_fields, list(result.get("mfe0_trades") or []))
        _write_csv(paths["mfe_bucket_summary"], bucket_fields, list(result.get("mfe_bucket_summary") or []))
        _write_csv(paths["entry_features"], ef_fields, list(result.get("entry_features") or []))
        _write_csv(paths["feature_separation"], sep_fields, list(result.get("feature_separation") or []))
        _write_csv(paths["no_progress_analysis"], npa_fields, list(result.get("no_progress_analysis") or []))
        _write_csv(paths["guard_v2_shadow"], GUARD_SHADOW_FIELDS, list(result.get("guard_v2_shadow") or []))

        report_payload = {k: v for k, v in result.items() if k not in ("entry_features",)}
        paths["report"].write_text(
            json.dumps(report_payload, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    hyp = result.get("hypothesis_checks") or {}
    lines = [
        "# Phase540 — NoProgress / MFE0 Entry Quality Root Cause Study",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Generated:** {result.get('generated_at')}",
        f"**Days:** {', '.join(result.get('target_days') or [])}",
        f"**Trades:** {result.get('trade_count')}",
        "",
        "## Mandatory answers",
        "",
    ]
    for i, key in enumerate(
        [
            "1_no_progress_exit_count",
            "2_mfe0_count_strict",
            "3_mfe0_primary_loss_driver",
            "4_mfe0_common_traits",
            "5_top_separation_feature",
            "6_mfe0_pullback_misread",
            "7_mfe0_counter_trend_bounce",
            "8_no_progress_exit_effective",
            "9_preventable_at_entry",
            "10_entry_guard_v2_candidates_exist",
            "11_best_guard_candidate",
            "12_production_adoption_candidate",
            "13_next_phase",
        ],
        start=1,
    ):
        lines.append(f"{i}. **{key}:** {ma.get(key)}")
    lines.extend(["", "## Hypothesis checks (MFE0 vs Winner)", ""])
    for k, v in hyp.items():
        lines.append(f"- {k}: {v}")
    lines.extend(
        [
            "",
            "## Outputs",
            "",
            "- `results/reports/phase540_no_progress_trades.csv`",
            "- `results/reports/phase540_mfe0_trades.csv`",
            "- `results/reports/phase540_mfe_bucket_summary.csv`",
            "- `results/reports/phase540_entry_features.csv`",
            "- `results/reports/phase540_feature_separation.csv`",
            "- `results/reports/phase540_no_progress_analysis.csv`",
            "- `results/reports/phase540_guard_v2_shadow.csv`",
            "- `results/reports/phase540_report.json`",
            "",
            "## Constraints",
            "",
            "Research only. No Runtime / EXIT changes. No unlimited combinatorial search.",
        ]
    )
    return "\n".join(lines) + "\n"
