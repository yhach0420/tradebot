"""Paper trade stop/take, entry-quality, deferral, trace — imported from ``market.yahoo.paper_trade``."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from typing import Any, Optional

from market.yahoo.paper_trade import (
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    JST,
    _build_replay_shadow_filter_validation,
    _chase_extension_bucket,
)

# VWAP diagnostics + EQ columns (mirror tests)
_PAPER_TRADE_VWAP_DIAG_CSV_FIELDS: tuple[str, ...] = (
    "pre_entry_vwap_hold_bars",
    "pre_entry_vwap_under_bars",
    "post_breakout_vwap_hold_bars",
    "vwap_retouch_count_after_breakout",
    "vwap_break_early_risk_score",
)
_PAPER_TRADE_ENTRY_QUALITY_CSV_FIELDNAMES: tuple[str, ...] = (
    "breakout_vwap_hold_score",
    "breakout_candle_quality_score",
    "breakout_pullback_quality_score",
    "breakout_volume_continuation_score",
    "breakout_failure_risk_score",
    "breakout_freshness_score",
    "breakout_extension_pct",
    "breakout_bars_since",
    "breakout_late_entry_flag",
    "vwap_under_duration_bars",
    *_PAPER_TRADE_VWAP_DIAG_CSV_FIELDS,
    "failure_upper_wick_penalty",
    "failure_vwap_extension_penalty",
    "failure_high_refresh_penalty",
    "failure_reversal_penalty",
    "failure_bear_streak_penalty",
    "failure_post_vwap_break_penalty",
    "failure_exhaustion_penalty",
    "debug_post_breakout_low",
    "debug_post_peak",
    "debug_pullback_pct",
    "debug_post_vwap_dist_pct",
    "entry_quality_score",
)
_PAPER_TRADE_PHASE2_CSV_FIELDS: tuple[str, ...] = (
    "chase_extension_pct",
    "extension_bucket",
    "prev_signal_age_sec",
    "prev_signal_pnl",
    "prev_signal_exit_reason",
    "same_symbol_cooldown_would_block",
    "market_weakness_score",
    "market_breadth_score",
    "market_trend_pressure_score",
    "lt50_ratio",
    "quality_rank_in_day",
    "quality_rank_in_symbol",
    "quality_percentile",
    "phase2_entry_block_reason",
)


def _paper_trade_recent_5m_range_from_extras(px_in: dict[str, Any]) -> Optional[float]:
    highs = px_in.get("highs_1m")
    lows = px_in.get("lows_1m")
    if not isinstance(highs, list) or not isinstance(lows, list) or len(highs) < 7 or len(lows) < 7:
        return None
    hv = [float(x) for x in highs if isinstance(x, (int, float))]
    lv = [float(x) for x in lows if isinstance(x, (int, float))]
    if len(hv) < 7 or len(lv) < 7:
        return None
    w_h = hv[-6:-1]
    w_l = lv[-6:-1]
    if not w_h or not w_l:
        return None
    return float(max(w_h)) - float(min(w_l))


# Shadow tiers for structure min-RR counterfactuals (does not change live take; diagnostics only).
PAPER_STRUCTURE_RELAXED_SHADOW_RRS: tuple[float, ...] = (0.25, 0.35, 0.50)


def _structure_candidate_type_normalize(tag: str) -> str:
    """Map internal wall tags to stable summary keys (replay / paper_agg)."""
    t = str(tag or "").strip()
    aliases = {
        "day_high": "daily_high_raw",
        "recent_5m_high": "intraday_swing",
    }
    if t in aliases:
        return aliases[t]
    return t or "unknown"


def _paper_trade_round_number_wall(entry: float, min_gap: float) -> Optional[tuple[float, str]]:
    e = float(entry)
    if e <= 0:
        return None
    lo = e * (1.0 + float(min_gap))
    mag = 10.0 ** max(0, int(math.log10(lo + 1e-9)) - 1)
    if mag < 10.0:
        mag = 10.0
    step = 100.0 if lo >= 2000.0 else (50.0 if lo >= 500.0 else mag)
    w = math.ceil(lo / step) * step
    if w <= e * (1.0 + min_gap):
        w += step
    if w <= e * (1.0 + min_gap):
        return None
    return float(w), "round_number"


def rr_histogram_bucket(raw_rr: float) -> str:
    x = float(raw_rr)
    if x < 0.15:
        return "lt_0_15"
    if x < 0.30:
        return "r_0_15_0_30"
    if x < 0.50:
        return "r_0_30_0_50"
    if x <= 1.0:
        return "r_0_50_1_0"
    return "gt_1_0"


def _paper_trade_bump_structure_rr_from_candidate_row(exec_counts: Optional[dict[str, Any]], row: dict[str, Any]) -> None:
    if exec_counts is None or not isinstance(exec_counts, dict) or not row:
        return
    try:
        rr_v = float(row.get("raw_rr") or 0.0)
    except Exception:
        rr_v = 0.0
    hist = exec_counts.get("structure_rr_histogram")
    if not isinstance(hist, dict):
        hist = {
            "lt_0_15": 0,
            "r_0_15_0_30": 0,
            "r_0_30_0_50": 0,
            "r_0_50_1_0": 0,
            "gt_1_0": 0,
        }
        exec_counts["structure_rr_histogram"] = hist
    bk = rr_histogram_bucket(rr_v)
    hist[bk] = int(hist.get(bk) or 0) + 1
    ctype = str(row.get("candidate_type") or "").strip() or "unknown"
    byt = exec_counts.get("structure_rr_by_candidate_type")
    if not isinstance(byt, dict):
        byt = {}
        exec_counts["structure_rr_by_candidate_type"] = byt
    cur = byt.get(ctype)
    if not isinstance(cur, dict):
        cur = {"n": 0, "rr_sum": 0.0}
        byt[ctype] = cur
    cur["n"] = int(cur.get("n") or 0) + 1
    cur["rr_sum"] = float(cur.get("rr_sum") or 0.0) + rr_v


def _paper_trade_relaxed_shadow_sweep_bump_open(
    exec_counts: Optional[dict[str, Any]],
    *,
    rr_n: float,
    has_candidates: bool,
) -> None:
    if exec_counts is None or not isinstance(exec_counts, dict):
        return
    sw = exec_counts.get("structure_relaxed_rr_shadow_sweep")
    if not isinstance(sw, dict):
        sw = {}
        exec_counts["structure_relaxed_rr_shadow_sweep"] = sw
    for rel in PAPER_STRUCTURE_RELAXED_SHADOW_RRS:
        key = f"{rel:.2f}"
        b = sw.get(key)
        if not isinstance(b, dict):
            b = {
                "blocked_count": 0,
                "selected_count": 0,
                "pnl_delta_yen_100_sum": 0.0,
                "shadow_pnl_yen_100_sum": 0.0,
                "take_hit_delta": 0,
                "vwap_break_saved": 0,
                "stop_added": 0,
                "closed_positions": 0,
            }
            sw[key] = b
        if not has_candidates:
            b["blocked_count"] = int(b.get("blocked_count") or 0) + 1
        elif float(rr_n) + 1e-9 < float(rel):
            b["blocked_count"] = int(b.get("blocked_count") or 0) + 1
        else:
            b["selected_count"] = int(b.get("selected_count") or 0) + 1


def _paper_trade_relaxed_shadow_sweep_bump_close(
    exec_counts: Optional[dict[str, Any]],
    pos: Any,
    *,
    reason: str,
    exit_price: float,
    entry_price: float,
) -> None:
    if exec_counts is None or not isinstance(exec_counts, dict):
        return
    shadow = getattr(pos, "structure_relaxed_shadow_takes", None)
    if not isinstance(shadow, dict) or not shadow:
        return
    peak = float(getattr(pos, "max_price_after", exit_price) or exit_price)
    xp = float(exit_price)
    ep = float(entry_price)
    act_pnl = (xp - ep) * 100.0
    rsn = str(reason or "").strip().upper()
    sw = exec_counts.get("structure_relaxed_rr_shadow_sweep")
    if not isinstance(sw, dict):
        return
    for key, stp_raw in shadow.items():
        try:
            stp = float(stp_raw)
        except Exception:
            continue
        b = sw.get(str(key))
        if not isinstance(b, dict):
            continue
        try:
            rel_v = float(str(key))
        except Exception:
            rel_v = 0.0
        rr_best = float(getattr(pos, "_structure_nearest_rr_n", 0.0) or 0.0)
        if rr_best + 1e-9 < rel_v:
            continue
        b["closed_positions"] = int(b.get("closed_positions") or 0) + 1
        shadow_exit = stp if peak + 1e-9 >= stp else xp
        shadow_pnl = (shadow_exit - ep) * 100.0
        b["shadow_pnl_yen_100_sum"] = float(b.get("shadow_pnl_yen_100_sum") or 0.0) + shadow_pnl
        b["pnl_delta_yen_100_sum"] = float(b.get("pnl_delta_yen_100_sum") or 0.0) + (shadow_pnl - act_pnl)
        if peak + 1e-9 >= stp and rsn != "TAKE_HIT":
            b["take_hit_delta"] = int(b.get("take_hit_delta") or 0) + 1
        if rsn == "VWAP_BREAK_EXIT" and peak + 1e-9 >= stp:
            b["vwap_break_saved"] = int(b.get("vwap_break_saved") or 0) + 1
        if rsn == "STOP_HIT" and peak + 1e-9 >= stp:
            b["stop_added"] = int(b.get("stop_added") or 0) + 1


def _paper_trade_bump_dynamic_fallback_open(
    exec_counts: Optional[dict[str, Any]],
    *,
    entry: float,
    stop: float,
    take: float,
    tss: str,
    entry_quality_scores: Optional[dict[str, float]],
) -> None:
    if exec_counts is None or not isinstance(exec_counts, dict):
        return
    if str(tss).strip().upper() != "DYNAMIC":
        return
    risk = max(float(entry) - float(stop), 1e-9)
    rew = max(float(take) - float(entry), 0.0)
    rr = rew / risk
    exec_counts["dynamic_fallback_n"] = int(exec_counts.get("dynamic_fallback_n") or 0) + 1
    exec_counts["dynamic_fallback_sum_rr"] = float(exec_counts.get("dynamic_fallback_sum_rr") or 0.0) + float(rr)
    q = 0.0
    if isinstance(entry_quality_scores, dict):
        try:
            q = float(entry_quality_scores.get("entry_quality_score") or 0.0)
        except Exception:
            q = 0.0
    exec_counts["dynamic_fallback_quality_sum"] = float(exec_counts.get("dynamic_fallback_quality_sum") or 0.0) + q


def _paper_trade_bump_dynamic_fallback_close(exec_counts: Optional[dict[str, Any]], pos: Any, *, reason: str) -> None:
    if exec_counts is None or not isinstance(exec_counts, dict):
        return
    tss = str(getattr(pos, "take_structure_selection", "") or "").strip().upper()
    if tss != "DYNAMIC":
        return
    rsn = str(reason or "").strip().upper()
    exec_counts["dynamic_fallback_closed_n"] = int(exec_counts.get("dynamic_fallback_closed_n") or 0) + 1
    if rsn == "TAKE_HIT":
        exec_counts["dynamic_fallback_take_hits"] = int(exec_counts.get("dynamic_fallback_take_hits") or 0) + 1
    if rsn == "VWAP_BREAK_EXIT":
        exec_counts["dynamic_fallback_vwap_exits"] = int(exec_counts.get("dynamic_fallback_vwap_exits") or 0) + 1


def _paper_trade_structure_relaxed_gate(
    *,
    relaxed_ok: bool,
    proximity_relaxed_ok: bool,
    st_best_rr_val: Optional[float],
    entry_quality_scores: Optional[dict[str, float]],
    rtc: dict[str, Any],
) -> tuple[bool, str]:
    if not relaxed_ok:
        return False, ""
    if st_best_rr_val is not None and math.isfinite(float(st_best_rr_val)):
        rr_v = float(st_best_rr_val)
        if proximity_relaxed_ok:
            prox_floor = float(rtc.get("paper_structure_proximity_relaxed_min_rr") or 0.18)
            if rr_v < prox_floor - 1e-9:
                return False, "PROXIMITY_RELAXED_RR_TOO_LOW"
        else:
            min_rr = float(rtc.get("paper_structure_relaxed_min_rr") or 0.35)
            if rr_v < min_rr - 1e-9:
                return False, "RELAXED_RR_BELOW_MIN"
    if not bool(rtc.get("paper_structure_relaxed_quality_gate_enabled", True)):
        return True, ""
    if not isinstance(entry_quality_scores, dict) or "entry_quality_score" not in entry_quality_scores:
        return True, ""
    eq = entry_quality_scores
    vh_min = float(rtc.get("paper_structure_relaxed_min_vwap_hold") or 0.55)
    fr_max = float(rtc.get("paper_structure_relaxed_max_failure_risk") or 0.42)
    pull_min = float(rtc.get("paper_structure_relaxed_min_pullback") or 0.32)
    fresh_min = float(rtc.get("paper_structure_relaxed_min_freshness") or 0.35)
    vh = float(eq.get("breakout_vwap_hold_score") or 0.5)
    fr = float(eq.get("breakout_failure_risk_score") or 0.5)
    pull = float(eq.get("breakout_pullback_quality_score") or 0.5)
    fresh = float(eq.get("breakout_freshness_score") or 0.5)
    if vh < vh_min:
        return False, "RELAXED_QUALITY_VWAP_HOLD_LOW"
    if fr > fr_max:
        return False, "RELAXED_QUALITY_FAILURE_RISK_HIGH"
    if pull < pull_min:
        return False, "RELAXED_QUALITY_PULLBACK_LOW"
    if fresh < fresh_min:
        return False, "RELAXED_QUALITY_FRESHNESS_LOW"
    return True, ""


def _paper_trade_csv_header_insert_after(
    header: list[str], anchor: str, fields: tuple[str, ...] | list[str]
) -> list[str]:
    out = list(header)
    to_add = [str(c) for c in fields if str(c) not in out]
    if not to_add:
        return out
    if anchor in out:
        idx = out.index(anchor) + 1
        for j, col in enumerate(to_add):
            out.insert(idx + j, col)
        return out
    out.extend(to_add)
    return out


def _paper_trade_csv_header_sync_entry_quality_block(header: list[str]) -> list[str]:
    out = _paper_trade_csv_header_insert_after(
        list(header),
        "vwap_under_duration_bars",
        _PAPER_TRADE_VWAP_DIAG_CSV_FIELDS,
    )
    for col in _PAPER_TRADE_ENTRY_QUALITY_CSV_FIELDNAMES:
        if col not in out:
            out.append(col)
    return out


def _paper_trade_csv_header_extend_phase2(header: list[str]) -> list[str]:
    out = _paper_trade_csv_header_sync_entry_quality_block(header)
    for c in _PAPER_TRADE_PHASE2_CSV_FIELDS:
        if c not in out:
            out.append(c)
    return out


def _paper_trade_entry_quality_csv_columns(scores: Optional[dict[str, float]]) -> dict[str, str]:
    blank = {k: "" for k in _PAPER_TRADE_ENTRY_QUALITY_CSV_FIELDNAMES}
    if not isinstance(scores, dict):
        return blank

    def _fmtf(k: str, width: int = 4) -> str:
        v = scores.get(k)
        if not isinstance(v, (int, float)):
            return ""
        return f"{float(v):.{width}f}"

    out = dict(blank)
    for k in _PAPER_TRADE_ENTRY_QUALITY_CSV_FIELDNAMES:
        if k in scores and isinstance(scores[k], (int, float)):
            out[k] = _fmtf(k, 4)
    return out


def _paper_trade_default_runtime_controls() -> dict[str, Any]:
    return {
        "paper_trade_dry_run_continue_execution_on_stale": True,
        "paper_take_rr_floor_mult": 1.0,
        "paper_take_rr_cap_mult": 1.5,
        "paper_take_max_pct_from_entry": 0.03,
        "paper_structure_take_priority": True,
        "paper_structure_take_min_rr": 0.55,
        "paper_structure_relaxed_min_rr": 0.35,
        "paper_structure_proximity_relaxed_min_rr": 0.18,
        "paper_structure_relaxed_quality_gate_enabled": True,
        "paper_structure_relaxed_min_vwap_hold": 0.55,
        "paper_structure_relaxed_max_failure_risk": 0.42,
        "paper_structure_relaxed_min_pullback": 0.32,
        "paper_structure_relaxed_min_freshness": 0.35,
        "paper_structure_resistance_proximity_pct": 0.006,
        "paper_dynamic_take_enabled": True,
        "paper_structure_take_enabled": True,
        "paper_recent_5m_high_wall_mult": 1.002,
        "paper_structure_min_gap_pct": 0.0015,
        "paper_structure_resistance_epsilon_pct": 0.001,
        "paper_structure_round_number_wall_enabled": False,
        "paper_structure_vwap_extension_wall_enabled": True,
        "same_symbol_cooldown_sec": 300,
        "same_symbol_cooldown_shadow_only": True,
        "replay_chase_extension_autoblock_enabled": False,
        "replay_chase_extension_ge_pct": 0.5,
        "paper_entry_quality_min_for_open": None,
        "paper_entry_quality_strong_threshold": 0.75,
        "paper_entry_quality_failed_threshold": 0.25,
        "paper_entry_quality_failure_risk_threshold": 0.65,
        "lag_guard_enabled": True,
        "max_signal_notify_lag_sec": 120.0,
        "paper_structure_take_adjust_progress_pct": 60.0,
        "paper_structure_take_adjust_pullback_pct": 0.2,
        "paper_structure_take_adjust_peak_fail_count": 2,
        "paper_early_weak_exit_enabled": True,
        "paper_early_weak_exit_min_hold_sec": 600.0,
        "paper_early_weak_exit_progress_pct": -10.0,
        "paper_early_weak_exit_require_peak_fail": True,
        "paper_min_structure_rr_for_entry": 0.15,
        "paper_low_structure_rr_tier_mult": 0.88,
        "paper_low_structure_rr_entry_suppress_enabled": True,
        "paper_low_structure_rr_tier2_exclude_enabled": True,
    }


def _paper_trade_merge_runtime_controls(
    replay_cfg_like: dict[str, Any],
    overrides: Optional[dict[str, Any]],
) -> dict[str, Any]:
    out = _paper_trade_default_runtime_controls()
    if isinstance(replay_cfg_like, dict):
        for k, v in replay_cfg_like.items():
            if k.startswith("paper_") or k.startswith("same_symbol_") or k.startswith("replay_chase_") or k.startswith("lag_"):
                out[k] = v
    if isinstance(overrides, dict):
        out.update({k: v for k, v in overrides.items() if v is not None})
    return out


def _paper_trade_execution_counters_blank() -> dict[str, Any]:
    return {
        "opened_positions_count": 0,
        "closed_positions_count": 0,
        "take_hit_count": 0,
        "stop_hit_count": 0,
        "vwap_break_exit_count": 0,
        "recent_5m_low_exit_count": 0,
        "early_weak_exit_count": 0,
        "take_adjust_exit_count": 0,
        "time_exit_count": 0,
        "market_close_exit_count": 0,
        "suppressed_open_signal_count": 0,
        "stale_execution_continued_count": 0,
        "saved_profit_yen_100_shares": 0.0,
        "entry_quality_score_n": 0,
        "entry_quality_score_sum": 0.0,
        "strong_breakout_count": 0,
        "failed_breakout_count": 0,
        "resistance_take_hit_count": 0,
        "fixed_take_hit_count": 0,
        "take_before_resistance_count": 0,
        "structure_take_selected_count": 0,
        "dynamic_rr_fallback_count": 0,
        "structure_take_rr_relaxed_count": 0,
        "structure_wall_reject_count": 0,
        "structure_reject_reason_counts": {},
        "dynamic_rr_fallback_reason_counts": {},
        "low_structure_rr_suppressed_count": 0,
        "shadow_dynamic_low_rr_filter": {"lt_0.15": 0, "lt_0.20": 0, "lt_0.30": 0},
        "vwap_break_diag_n": 0,
        "vwap_break_progress_sum": 0.0,
        "vwap_break_peak_progress_sum": 0.0,
        "vwap_break_hold_sec_sum": 0.0,
        "vwap_break_extension_sum": 0.0,
        "vwap_break_failure_risk_sum": 0.0,
        "vwap_break_within_60s": 0,
        "vwap_break_within_180s": 0,
        "vwap_break_within_300s": 0,
        "structure_candidate_rr_sum": 0.0,
        "structure_candidate_n": 0,
        "structure_candidate_dist_sum": 0.0,
        "structure_candidate_fail_risk_sum": 0.0,
        "structure_rr_histogram": {
            "lt_0_15": 0,
            "r_0_15_0_30": 0,
            "r_0_30_0_50": 0,
            "r_0_50_1_0": 0,
            "gt_1_0": 0,
        },
        "structure_rr_by_candidate_type": {},
        "structure_relaxed_rr_shadow_sweep": {},
        "dynamic_fallback_n": 0,
        "dynamic_fallback_sum_rr": 0.0,
        "dynamic_fallback_quality_sum": 0.0,
        "dynamic_fallback_closed_n": 0,
        "dynamic_fallback_take_hits": 0,
        "dynamic_fallback_vwap_exits": 0,
    }


def _paper_trade_bump_entry_quality_summary(
    exec_counts: dict[str, Any],
    *,
    scores: dict[str, float],
    crossed: bool,
    rtc: dict[str, Any],
) -> None:
    eq = float(scores.get("entry_quality_score") or 0.0)
    exec_counts["entry_quality_score_n"] = int(exec_counts.get("entry_quality_score_n") or 0) + 1
    exec_counts["entry_quality_score_sum"] = float(exec_counts.get("entry_quality_score_sum") or 0.0) + eq
    strong_t = float(rtc.get("paper_entry_quality_strong_threshold") or 0.75)
    fail_t = float(rtc.get("paper_entry_quality_failed_threshold") or 0.25)
    fr_t = float(rtc.get("paper_entry_quality_failure_risk_threshold") or 0.65)
    fr = float(scores.get("breakout_failure_risk_score") or 0.5)
    if crossed and eq >= strong_t and fr < fr_t:
        exec_counts["strong_breakout_count"] = int(exec_counts.get("strong_breakout_count") or 0) + 1
    if eq < fail_t or fr >= fr_t:
        exec_counts["failed_breakout_count"] = int(exec_counts.get("failed_breakout_count") or 0) + 1


def _paper_trade_take_meta_from_csv(take_csv: dict[str, str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k_attr, k_csv in (
        ("take_selected_by", "take_selected_by"),
        ("take_exit_kind", "take_exit_kind"),
        ("take_structure_selection", "take_structure_selection"),
        ("resistance_take_preferred", "resistance_take_preferred"),
        ("structure_take_reject_reason", "structure_take_reject_reason"),
        ("dynamic_fallback_policy", "dynamic_fallback_policy"),
    ):
        s = str(take_csv.get(k_csv) or "").strip()
        if s:
            out[k_attr] = s
    nr = str(take_csv.get("nearest_resistance") or "").strip()
    if nr:
        try:
            out["nearest_resistance"] = float(nr)
        except Exception:
            pass
    scc = str(take_csv.get("structure_take_candidate_count") or "").strip()
    if scc:
        try:
            out["structure_take_candidate_count"] = int(scc)
        except Exception:
            pass
    sbc = str(take_csv.get("structure_take_best_candidate") or "").strip()
    if sbc:
        out["structure_take_best_candidate"] = sbc
    sbrr = str(take_csv.get("structure_take_best_rr") or "").strip()
    if sbrr:
        try:
            out["structure_take_best_rr"] = float(sbrr)
        except Exception:
            pass
    return out


def _paper_trade_bump_take_selection_counters(
    exec_counts: Optional[dict[str, Any]], take_meta: Optional[dict[str, Any]]
) -> None:
    if exec_counts is None or not isinstance(take_meta, dict):
        return
    sel = str(take_meta.get("take_structure_selection") or "").strip().upper()
    if sel in ("STRUCTURE", "STRUCTURE_RELAXED"):
        exec_counts["structure_take_selected_count"] = int(exec_counts.get("structure_take_selected_count") or 0) + 1
    if sel == "STRUCTURE_RELAXED":
        exec_counts["structure_take_rr_relaxed_count"] = int(exec_counts.get("structure_take_rr_relaxed_count") or 0) + 1
    if sel == "DYNAMIC":
        nr = take_meta.get("nearest_resistance")
        if isinstance(nr, (int, float)) and math.isfinite(float(nr)):
            exec_counts["dynamic_rr_fallback_count"] = int(exec_counts.get("dynamic_rr_fallback_count") or 0) + 1
            rc = exec_counts.get("dynamic_rr_fallback_reason_counts")
            if not isinstance(rc, dict):
                rc = {}
                exec_counts["dynamic_rr_fallback_reason_counts"] = rc
            tag = str(take_meta.get("structure_take_reject_reason") or "").strip()
            if "|" in tag:
                tag = tag.split("|", 1)[0].strip()
            if not tag:
                tag = "UNKNOWN"
            rc[tag] = int(rc.get(tag) or 0) + 1


def _paper_trade_deferral_row_set_lag_fields(
    row: dict[str, str],
    *,
    det_at: datetime,
    notify_t: datetime,
    max_lag: float,
    lag_guard: bool,
    poll_finished_at_jst: str,
) -> tuple[float, bool]:
    a = det_at if det_at.tzinfo else det_at.replace(tzinfo=timezone.utc)
    b = notify_t if notify_t.tzinfo else notify_t.replace(tzinfo=timezone.utc)
    lag_sec = abs((b - a).total_seconds())
    stale = bool(lag_guard and lag_sec > float(max_lag))
    row["signal_lag_sec"] = f"{float(lag_sec):.3f}"
    if stale:
        row["notify_sent"] = "0"
        row["skipped"] = "1"
        row["skip_reason"] = (str(row.get("skip_reason") or "").strip() + " STALE_SIGNAL_LAG_GT_120SEC").strip()
    return float(lag_sec), stale


def _paper_trade_breakout_entry_quality_scores(
    px_extras: dict[str, Any],
    intr: Any,
    price: float,
    entry_nf: float,
    rtc: dict[str, Any],
) -> dict[str, float]:
    """Lightweight continuation-v1 style scores for unit tests and diagnostics."""
    closes = px_extras.get("closes_1m")
    highs = px_extras.get("highs_1m")
    lows = px_extras.get("lows_1m")
    vols = px_extras.get("vols_1m")
    vwap = float(getattr(intr, "vwap", 0.0) or 0.0) if intr is not None else 0.0
    n = 0
    if isinstance(closes, list):
        n = len(closes)
    entry = float(entry_nf)
    ext = 0.0
    if n >= 2 and isinstance(closes[-1], (int, float)) and entry > 0:
        ext = max(0.0, (float(closes[-1]) - entry) / entry * 100.0)
    bars_since = float(max(0, n - 1))
    late = 1.0 if bars_since > 12 else 0.0
    vwap_hold = 0.55
    if vwap > 0 and isinstance(closes, list) and n >= 3:
        above = sum(1 for c in closes[-5:] if isinstance(c, (int, float)) and float(c) >= vwap * 0.999)
        vwap_hold = min(1.0, above / 5.0 + 0.25)
    candle_q = 0.5
    if isinstance(highs, list) and isinstance(lows, list) and isinstance(closes, list) and n:
        body = abs(float(closes[-1]) - float(px_extras.get("opens_1m", [closes[-1]])[-1] if isinstance(px_extras.get("opens_1m"), list) and px_extras["opens_1m"] else closes[-1]))
        rng = max(1e-9, float(highs[-1]) - float(lows[-1]))
        candle_q = min(1.0, body / rng)
    pullback_q = 0.5
    if isinstance(highs, list) and isinstance(lows, list) and n >= 3:
        peak = max(float(x) for x in highs[-8:] if isinstance(x, (int, float)))
        trough = min(float(x) for x in lows[-8:] if isinstance(x, (int, float)))
        if peak > entry:
            pb = (peak - float(price)) / max(1e-9, peak - entry)
            pullback_q = max(0.0, min(1.0, 1.0 - max(0.0, pb - 0.15) * 3.0))
    vol_cont = 0.5
    if isinstance(vols, list) and len(vols) >= 6:
        a = sum(float(x) for x in vols[-3:] if isinstance(x, (int, float)))
        b = sum(float(x) for x in vols[-6:-3] if isinstance(x, (int, float)))
        if b > 0:
            vol_cont = max(0.0, min(1.0, a / (2.0 * b)))
    fail_risk = max(0.0, min(1.0, 0.45 - vwap_hold * 0.2 + late * 0.25 + ext / 10.0 * 0.2))
    fresh = max(0.0, min(1.0, 1.0 - min(1.0, bars_since / 20.0)))
    vwap_under_bars = 0.0
    if vwap > 0 and isinstance(closes, list):
        for c in reversed(closes):
            if not isinstance(c, (int, float)):
                continue
            if float(c) < vwap * 0.999:
                vwap_under_bars += 1.0
            else:
                break
    pre_hold = min(5.0, max(0.0, bars_since - vwap_under_bars))
    retouch = 0.0
    if isinstance(closes, list) and vwap > 0:
        crossed = False
        for c in closes[-10:]:
            if not isinstance(c, (int, float)):
                continue
            if float(c) > vwap * 1.001:
                crossed = True
            elif crossed and float(c) <= vwap * 1.0005:
                retouch += 1.0
    early_risk = max(0.0, min(1.0, vwap_under_bars / 5.0 + retouch * 0.1))
    entry_q = (
        0.22 * vwap_hold
        + 0.18 * candle_q
        + 0.18 * pullback_q
        + 0.14 * vol_cont
        + 0.14 * fresh
        + 0.14 * (1.0 - fail_risk)
    )
    entry_q = max(0.0, min(1.0, entry_q))
    peak_dbg = max((float(x) for x in (highs or [])[-8:] if isinstance(x, (int, float))), default=float(price))
    low_dbg = min((float(x) for x in (lows or [])[-8:] if isinstance(x, (int, float))), default=float(price))
    post_vwap_dist = abs(float(price) - vwap) / max(vwap, 1e-9) * 100.0 if vwap > 0 else 0.0
    return {
        "breakout_vwap_hold_score": float(vwap_hold),
        "breakout_candle_quality_score": float(candle_q),
        "breakout_pullback_quality_score": float(pullback_q),
        "breakout_volume_continuation_score": float(vol_cont),
        "breakout_failure_risk_score": float(fail_risk),
        "breakout_freshness_score": float(fresh),
        "breakout_extension_pct": float(ext),
        "breakout_bars_since": float(bars_since),
        "breakout_late_entry_flag": float(late),
        "vwap_under_duration_bars": float(vwap_under_bars),
        "pre_entry_vwap_hold_bars": float(pre_hold),
        "pre_entry_vwap_under_bars": float(max(0.0, bars_since - pre_hold)),
        "post_breakout_vwap_hold_bars": float(min(5.0, pre_hold)),
        "vwap_retouch_count_after_breakout": float(retouch),
        "vwap_break_early_risk_score": float(early_risk),
        "failure_upper_wick_penalty": 0.0,
        "failure_vwap_extension_penalty": 0.0,
        "failure_high_refresh_penalty": 0.0,
        "failure_reversal_penalty": 0.0,
        "failure_bear_streak_penalty": 0.0,
        "failure_post_vwap_break_penalty": 0.0,
        "failure_exhaustion_penalty": 0.0,
        "debug_post_breakout_low": float(low_dbg),
        "debug_post_peak": float(peak_dbg),
        "debug_pullback_pct": float(max(0.0, (peak_dbg - float(price)) / max(1e-9, peak_dbg - entry) * 100.0) if peak_dbg > entry else 0.0),
        "debug_post_vwap_dist_pct": float(post_vwap_dist),
        "entry_quality_score": float(entry_q),
    }


def _paper_trade_compute_stop_take_for_signal(
    entry_nf: float,
    q: Any,
    intr: Any,
    pt_ex: dict[str, Any],
    rtc: dict[str, Any],
    ma25_screen: Any = None,
    entry_quality_scores: Optional[dict[str, float]] = None,
) -> tuple[float, float, dict[str, Any]]:
    entry = float(entry_nf)
    stop = float(entry * (1.0 - STOP_LOSS_PCT))
    risk = max(entry - stop, 1e-9)
    ex: dict[str, Any] = {
        "take_calc_method": "",
        "take_distance_pct": "",
        "take_exit_kind": "",
        "take_selected_by": "",
        "take_structure_selection": "",
        "structure_take_reject_reason": "",
        "nearest_resistance": "",
        "resistance_take_preferred": "",
        "structure_take_candidate_count": "0",
        "structure_take_best_candidate": "",
        "structure_take_best_rr": "",
        "structure_take_distance_pct": "",
        "structure_take_raw_rr": "",
        "structure_take_after_epsilon_rr": "",
        "structure_take_required_rr": "",
        "structure_take_failed_rule": "",
        "dynamic_fallback_policy": "",
        "nearest_resistance_source": "",
        "nearest_resistance_rank": "",
        "skipped_farther_structure_count": "0",
        "structure_candidates_diag_json": "",
    }

    if not bool(rtc.get("paper_dynamic_take_enabled", True)):
        take = float(entry * (1.0 + TAKE_PROFIT_PCT))
        ex.update(
            {
                "take_calc_method": "legacy_fixed_4pct",
                "take_exit_kind": "fixed",
                "take_structure_selection": "LEGACY_FIXED",
                "take_distance_pct": f"{((take - entry) / max(entry, 1e-9) * 100.0):.4f}",
            }
        )
        return stop, take, ex

    floor_m = float(rtc.get("paper_take_rr_floor_mult") or 1.0)
    cap_m = float(rtc.get("paper_take_rr_cap_mult") or 1.5)
    struct_min_rr = float(rtc.get("paper_structure_take_min_rr") or 0.55)
    prox_pct = float(rtc.get("paper_structure_resistance_proximity_pct") or 0.006)
    min_gap = float(rtc.get("paper_structure_min_gap_pct") or 0.0015)
    eps = float(rtc.get("paper_structure_resistance_epsilon_pct") or 0.001)
    mult_5m = float(rtc.get("paper_recent_5m_high_wall_mult") or 1.002)

    t_lo = entry + risk * floor_m
    t_hi = entry + risk * cap_m
    max_pct = float(rtc.get("paper_take_max_pct_from_entry") or 0.03)

    walls: list[tuple[float, str]] = []
    if intr is not None and getattr(intr, "recent_5m_high", None) is not None:
        walls.append((float(getattr(intr, "recent_5m_high")) * mult_5m, "recent_5m_high"))
    if getattr(q, "day_high", None) is not None:
        try:
            walls.append((float(q.day_high), "day_high"))
        except Exception:
            pass
    pds = pt_ex.get("paper_daily_structure") if isinstance(pt_ex.get("paper_daily_structure"), dict) else {}
    if pds.get("previous_day_high") is not None:
        walls.append((float(pds["previous_day_high"]), "previous_day_high"))
    for _k in ("daily_ma75", "ma75"):
        if pds.get(_k) is not None:
            try:
                walls.append((float(pds[_k]), "daily_ma75"))
            except Exception:
                pass
            break
    for _k in ("daily_ma200", "ma200"):
        if pds.get(_k) is not None:
            try:
                walls.append((float(pds[_k]), "daily_ma200"))
            except Exception:
                pass
            break
    if isinstance(ma25_screen, dict):
        if ma25_screen.get("ma25") is not None:
            try:
                walls.append((float(ma25_screen["ma25"]), "daily_ma25"))
            except Exception:
                pass
        if ma25_screen.get("ma75") is not None:
            try:
                walls.append((float(ma25_screen["ma75"]), "daily_ma75"))
            except Exception:
                pass
        if ma25_screen.get("ma200") is not None:
            try:
                walls.append((float(ma25_screen["ma200"]), "daily_ma200"))
            except Exception:
                pass
    if bool(rtc.get("paper_structure_round_number_wall_enabled", False)):
        rn_wall = _paper_trade_round_number_wall(entry, min_gap)
        if rn_wall is not None:
            walls.append(rn_wall)
    if bool(rtc.get("paper_structure_vwap_extension_wall_enabled", True)) and intr is not None and getattr(intr, "vwap", None) is not None:
        try:
            vw = float(getattr(intr, "vwap"))
            if vw > 0 and vw > entry * (1.0 + min_gap):
                walls.append((vw * (1.0 + max(1e-6, eps * 2.0)), "vwap_extension"))
        except Exception:
            pass

    # unique sorted walls above min gap
    raw: list[tuple[float, str]] = []
    seen: set[float] = set()
    for w, tag in walls:
        if w <= entry * (1.0 + min_gap):
            continue
        key = round(w, 6)
        if key in seen:
            continue
        seen.add(key)
        raw.append((w, tag))
    raw.sort(key=lambda x: x[0])
    ex["structure_take_candidate_count"] = str(len(raw))
    fr_val = 0.0
    if isinstance(entry_quality_scores, dict):
        fr_val = float(entry_quality_scores.get("breakout_failure_risk_score") or 0.0)
    fr_s = f"{fr_val:.6f}"
    if raw:
        nw0 = float(raw[0][0])
        ex["structure_candidate_rank"] = "1"
        ex["structure_candidate_distance_pct"] = f"{(nw0 - entry) / max(entry, 1e-9) * 100.0:.6f}"
        ex["structure_candidate_failure_risk"] = fr_s

    def rr_at(wv: float) -> float:
        return (wv - entry) / risk

    required_rr = max(struct_min_rr, floor_m)
    cand_rows: list[dict[str, Any]] = []
    for i, (w, tag) in enumerate(raw):
        rk = i + 1
        nx = _structure_candidate_type_normalize(tag)
        rrv = float(rr_at(w))
        distp = float((w - entry) / max(entry, 1e-9) * 100.0)
        er = float(rr_at(w * (1.0 - eps)))
        cand_rows.append(
            {
                "rank": int(rk),
                "candidate_type": nx,
                "raw_rr": round(rrv, 6),
                "distance_pct": round(distp, 6),
                "epsilon_after_rr": round(er, 6),
                "required_rr": round(float(required_rr), 6),
                "rejection_reason": "SKIPPED_FARTHER_STRUCTURE" if rk > 1 else "",
            }
        )
    if cand_rows:
        ex["_nearest_raw_rr_for_shadow"] = str(cand_rows[0]["raw_rr"])
    else:
        ex["_nearest_raw_rr_for_shadow"] = "0"
    if raw:
        ex["nearest_resistance_source"] = _structure_candidate_type_normalize(raw[0][1])
        ex["nearest_resistance_rank"] = "1"
    else:
        ex["nearest_resistance_source"] = ""
        ex["nearest_resistance_rank"] = "0"
    ex["skipped_farther_structure_count"] = str(max(0, len(raw) - 1))
    shadow_takes: dict[str, float] = {}
    if raw:
        nw_s, _tg_s = raw[0]
        rr_ns = float(rr_at(nw_s))
        wt0 = float(nw_s * (1.0 - eps))
        for rel in PAPER_STRUCTURE_RELAXED_SHADOW_RRS:
            if rr_ns + 1e-9 < float(rel):
                continue
            chosen_s = float(min(max(wt0, t_lo), min(t_hi, entry * (1.0 + max_pct))))
            shadow_takes[f"{rel:.2f}"] = chosen_s
    ex["_structure_relaxed_shadow_takes"] = shadow_takes

    best_rr = max((rr_at(w) for w, _ in raw), default=0.0)
    ex["structure_take_best_rr"] = f"{best_rr:.6f}"
    if raw:
        ex["structure_take_best_candidate"] = raw[0][1]

    chosen_take: Optional[float] = None
    selection = ""
    method = ""
    sel_by = ""
    rej = ""
    nr_s = ""
    resist_pref = "0"
    structure_attempt_rej = ""

    if raw and bool(rtc.get("paper_structure_take_enabled", True)):
        nearest_w, nearest_tag = raw[0]
        rr_n = rr_at(nearest_w)
        wall_take = float(nearest_w * (1.0 - eps))
        dist_pct = (nearest_w - entry) / max(entry, 1e-9)
        proximity_ok = dist_pct <= prox_pct
        strict_ok = rr_n >= max(struct_min_rr, floor_m - 1e-9)
        relaxed_use = False
        rr_mid_relaxed = (rr_n + 1e-12 >= struct_min_rr) and (rr_n < floor_m - 1e-9)
        if strict_ok:
            chosen_take = min(max(wall_take, t_lo), min(t_hi, entry * (1.0 + max_pct)))
            selection = "STRUCTURE"
            method = "structure_nearest_resistance"
            sel_by = nearest_tag
            rej = ""
            nr_s = f"{nearest_w:.6f}"
            resist_pref = "1" if proximity_ok else "0"
        elif proximity_ok or rr_mid_relaxed:
            ok_gate, gr = _paper_trade_structure_relaxed_gate(
                relaxed_ok=True,
                proximity_relaxed_ok=bool(proximity_ok),
                st_best_rr_val=rr_n,
                entry_quality_scores=entry_quality_scores,
                rtc=rtc,
            )
            if ok_gate:
                cand = float(wall_take)
                if rr_mid_relaxed and (not proximity_ok):
                    band_lo = entry + risk * struct_min_rr - 1.0
                    band_hi = entry + risk * floor_m - 1.0
                    chosen_take = min(max(cand, band_lo), band_hi - 1e-6)
                else:
                    cap_dyn = entry + risk * floor_m + 1.0 - 1e-9
                    chosen_take = min(cand, cap_dyn, entry * (1.0 + max_pct))
                selection = "STRUCTURE_RELAXED"
                method = "structure_nearest_resistance"
                sel_by = "nearest_resistance" if proximity_ok else nearest_tag
                rej = ""
                nr_s = f"{nearest_w:.6f}"
                resist_pref = "1" if proximity_ok else "0"
                relaxed_use = True
            else:
                rej = gr or "RELAXED_GATE_FAIL"
        else:
            rej = "BELOW_STRUCTURE_MIN_RR"

        structure_attempt_rej = str(rej or "")

        if chosen_take is not None:
            if cand_rows:
                cand_rows[0]["rejection_reason"] = ""
            ex["structure_candidates_diag_json"] = json.dumps(cand_rows, ensure_ascii=False)
            ex.update(
                {
                    "take_calc_method": method,
                    "take_exit_kind": "structure",
                    "take_selected_by": sel_by,
                    "take_structure_selection": selection,
                    "structure_take_reject_reason": rej,
                    "nearest_resistance": nr_s,
                    "resistance_take_preferred": resist_pref,
                    "structure_take_distance_pct": f"{dist_pct * 100.0:.6f}",
                    "structure_take_raw_rr": f"{rr_n:.6f}",
                    "structure_take_after_epsilon_rr": f"{rr_at(chosen_take):.6f}",
                    "structure_take_required_rr": f"{max(struct_min_rr, floor_m):.6f}",
                    "structure_take_failed_rule": "" if strict_ok or relaxed_use else "MIN_RR",
                }
            )
            return stop, float(chosen_take), ex

    # Dynamic RR floor fallback
    dyn_take = float(entry + risk * floor_m)
    dyn_take = min(dyn_take, entry * (1.0 + max_pct))
    if raw:
        nr0 = raw[0][0]
        nr_s = f"{nr0:.6f}"
        rej = "BELOW_GLOBAL_RR_FLOOR_NOT_RELAXABLE" if best_rr < floor_m else "NO_STRUCTURE_PASS"
    else:
        rej = "NO_RESISTANCE_ABOVE_ENTRY_GAP"
    if cand_rows:
        if structure_attempt_rej:
            cand_rows[0]["rejection_reason"] = f"{structure_attempt_rej}|FALLBACK:{rej}"
        else:
            cand_rows[0]["rejection_reason"] = str(rej or "")
    ex["structure_candidates_diag_json"] = json.dumps(cand_rows, ensure_ascii=False)
    ex.update(
        {
            "take_calc_method": "dynamic_min_rr_floor",
            "take_exit_kind": "dynamic",
            "take_selected_by": "dynamic_min_rr",
            "take_structure_selection": "DYNAMIC",
            "structure_take_reject_reason": rej,
            "nearest_resistance": nr_s,
            "resistance_take_preferred": "0",
            "dynamic_fallback_policy": "dynamic_min_rr_floor",
        }
    )
    ex["take_distance_pct"] = f"{((dyn_take - entry) / max(entry, 1e-9) * 100.0):.4f}"
    return stop, dyn_take, ex


def _append_structure_rr_candidate_diag_csv(results_dir: str, *, symbol: str, time_jst: str, rows: list[dict[str, Any]]) -> None:
    rd = str(results_dir or "").strip()
    if not rd or not rows:
        return
    os.makedirs(rd, exist_ok=True)
    path = os.path.join(rd, "structure_rr_candidates.csv")
    write_header = not os.path.isfile(path)
    with open(path, "a", encoding="utf-8", newline="") as cf:
        fn = ["symbol", "time_jst", "rank", "candidate_type", "raw_rr", "distance_pct", "epsilon_after_rr", "required_rr", "rejection_reason"]
        w = csv.DictWriter(cf, fieldnames=fn, extrasaction="ignore")
        if write_header:
            w.writeheader()
        for row in rows:
            w.writerow(
                {
                    "symbol": str(symbol),
                    "time_jst": str(time_jst),
                    "rank": str(row.get("rank", "")),
                    "candidate_type": str(row.get("candidate_type", "")),
                    "raw_rr": str(row.get("raw_rr", "")),
                    "distance_pct": str(row.get("distance_pct", "")),
                    "epsilon_after_rr": str(row.get("epsilon_after_rr", "")),
                    "required_rr": str(row.get("required_rr", "")),
                    "rejection_reason": str(row.get("rejection_reason", "")),
                }
            )


def _shared_engine_trace_jsonl_append(path: str, row: dict[str, Any]) -> None:
    p = str(path or "").strip()
    if not p:
        return
    d = os.path.dirname(p)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(p, "a", encoding="utf-8") as tf:
        tf.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _parse_bool_cell(cell: Any) -> Optional[bool]:
    if cell is None:
        return None
    if isinstance(cell, bool):
        return cell
    s = str(cell).strip().lower()
    if s in ("", "none", "null"):
        return None
    if s in ("1", "true", "yes", "y", "on"):
        return True
    if s in ("0", "false", "no", "n", "off"):
        return False
    return None


def _paper_trade_try_close_open_position(
    pos: Any,
    *,
    time_utc: datetime,
    price: float,
    vwap: Optional[float],
    recent_5m_low: Optional[float],
    early_exit_before_partial_take: bool,
    early_exit_vwap: bool,
    early_exit_recent_low: bool,
    rtc: dict[str, Any],
) -> None:
    """Shared paper/replay exit engine (continuation-v1 labels)."""
    if bool(getattr(pos, "resolved", False)):
        return
    p = float(price)
    rtc = dict(rtc or {})
    tu = time_utc if time_utc.tzinfo else time_utc.replace(tzinfo=timezone.utc)
    j = tu.astimezone(JST)
    hm = int(j.hour * 60 + j.minute)
    ep = float(getattr(pos, "entry_price", 0.0) or 0.0)
    exec_counts: Optional[dict[str, Any]] = getattr(pos, "_paper_exec_counters", None)
    trace_path: str = str(getattr(pos, "_paper_shared_trace_path", "") or "")
    sym = str(getattr(pos, "symbol", "") or "")

    def _finalize(reason: str, xp: float, tu2: datetime) -> None:
        xpf = float(xp)
        pct = float(((xpf - ep) / ep * 100.0) if ep > 0 else 0.0)
        t2 = tu2 if tu2.tzinfo else tu2.replace(tzinfo=timezone.utc)
        setattr(pos, "resolved", True)
        setattr(pos, "exit_reason", str(reason))
        setattr(pos, "exit_price", xpf)
        setattr(pos, "exit_time_utc", t2)
        setattr(pos, "final_profit_pct", float(pct))
        rr = str(reason).strip().upper()
        if rr == "TAKE_HIT":
            setattr(pos, "result", "WIN")
            setattr(pos, "take_hit", True)
        elif rr == "STOP_HIT":
            setattr(pos, "result", "LOSE")
            setattr(pos, "stop_hit", True)
        else:
            setattr(pos, "result", "WIN" if pct > 0 else ("LOSE" if pct < 0 else "HOLD"))
        if isinstance(exec_counts, dict) and (not bool(getattr(pos, "excluded_from_eval", False))):
            exec_counts["closed_positions_count"] = int(exec_counts.get("closed_positions_count") or 0) + 1
            if rr == "TAKE_HIT":
                exec_counts["take_hit_count"] = int(exec_counts.get("take_hit_count") or 0) + 1
            elif rr == "STOP_HIT":
                exec_counts["stop_hit_count"] = int(exec_counts.get("stop_hit_count") or 0) + 1
            elif rr == "VWAP_BREAK_EXIT":
                exec_counts["vwap_break_exit_count"] = int(exec_counts.get("vwap_break_exit_count") or 0) + 1
            elif rr == "RECENT_5M_LOW_BREAK_EXIT":
                exec_counts["recent_5m_low_exit_count"] = int(exec_counts.get("recent_5m_low_exit_count") or 0) + 1
            elif rr == "EARLY_WEAK_EXIT":
                exec_counts["early_weak_exit_count"] = int(exec_counts.get("early_weak_exit_count") or 0) + 1
            elif rr == "TAKE_ADJUST":
                exec_counts["take_adjust_exit_count"] = int(exec_counts.get("take_adjust_exit_count") or 0) + 1
            elif rr == "TIME_EXIT":
                exec_counts["time_exit_count"] = int(exec_counts.get("time_exit_count") or 0) + 1
            elif rr == "MARKET_CLOSE_EXIT":
                exec_counts["market_close_exit_count"] = int(exec_counts.get("market_close_exit_count") or 0) + 1
            if rr == "VWAP_BREAK_EXIT":
                n = int(exec_counts.get("vwap_break_diag_n") or 0) + 1
                exec_counts["vwap_break_diag_n"] = n
                prog = float(((p - ep) / ep * 100.0) if ep > 0 else 0.0)
                peakp = float(
                    ((float(getattr(pos, "max_price_after", p) or p) - ep) / ep * 100.0) if ep > 0 else 0.0
                )
                st = getattr(pos, "signal_time_utc", None)
                hold = 0.0
                if isinstance(st, datetime):
                    ss = st if st.tzinfo else st.replace(tzinfo=timezone.utc)
                    hold = abs((tu - ss).total_seconds())
                ext = float(getattr(pos, "entry_extension_at_open_pct", 0.0) or 0.0)
                fr = 0.0
                eqs = getattr(pos, "entry_quality_scores", None)
                if isinstance(eqs, dict):
                    fr = float(eqs.get("breakout_failure_risk_score") or 0.0)
                exec_counts["vwap_break_progress_sum"] = float(exec_counts.get("vwap_break_progress_sum") or 0.0) + prog
                exec_counts["vwap_break_peak_progress_sum"] = (
                    float(exec_counts.get("vwap_break_peak_progress_sum") or 0.0) + peakp
                )
                exec_counts["vwap_break_hold_sec_sum"] = float(exec_counts.get("vwap_break_hold_sec_sum") or 0.0) + hold
                exec_counts["vwap_break_extension_sum"] = float(exec_counts.get("vwap_break_extension_sum") or 0.0) + ext
                exec_counts["vwap_break_failure_risk_sum"] = (
                    float(exec_counts.get("vwap_break_failure_risk_sum") or 0.0) + fr
                )
                if hold <= 60.0 + 1e-9:
                    exec_counts["vwap_break_within_60s"] = int(exec_counts.get("vwap_break_within_60s") or 0) + 1
                if hold <= 180.0 + 1e-9:
                    exec_counts["vwap_break_within_180s"] = int(exec_counts.get("vwap_break_within_180s") or 0) + 1
                if hold <= 300.0 + 1e-9:
                    exec_counts["vwap_break_within_300s"] = int(exec_counts.get("vwap_break_within_300s") or 0) + 1
            _paper_trade_relaxed_shadow_sweep_bump_close(
                exec_counts,
                pos,
                reason=str(reason),
                exit_price=float(xpf),
                entry_price=float(ep),
            )
            _paper_trade_bump_dynamic_fallback_close(exec_counts, pos, reason=str(reason))
        if trace_path:
            eq = (
                getattr(pos, "entry_quality_scores", None)
                if isinstance(getattr(pos, "entry_quality_scores", None), dict)
                else {}
            )
            tc = getattr(pos, "take_diag_csv", None) if isinstance(getattr(pos, "take_diag_csv", None), dict) else {}
            tj = t2.astimezone(JST).strftime("%Y-%m-%d %H:%M:%S")
            row = _replay_shared_engine_trace_row(
                event="POSITION_CLOSE",
                symbol=sym,
                timestamp_jst=tj,
                engine_mode="paper_position_exec",
                entry_price=ep,
                stop_price=float(getattr(pos, "stop_price", 0.0) or 0.0),
                take_price=float(getattr(pos, "take_price", 0.0) or 0.0),
                exit_reason=str(reason),
                exit_price=xpf,
                eq_scores=dict(eq) if isinstance(eq, dict) else {},
                take_csv={k: str(v) for k, v in tc.items()} if isinstance(tc, dict) else {},
                event_type=("TAKE_ADJUST" if str(reason).strip().upper() == "TAKE_ADJUST" else "CLOSE_POSITION"),
            )
            _shared_engine_trace_jsonl_append(trace_path, row)

    paper_x = bool(getattr(pos, "_paper_position_exec", False))
    _do_early = bool(early_exit_before_partial_take) and (
        (not paper_x and str(getattr(pos, "exit_style", "")) != "fixed") or paper_x
    )

    if hm >= 15 * 60 + 25:
        _finalize("MARKET_CLOSE_EXIT", p, tu)
        return

    if _do_early:
        if bool(early_exit_vwap) and isinstance(vwap, (int, float)) and p < float(vwap):
            _finalize("VWAP_BREAK_EXIT", p, tu)
            return
        if bool(early_exit_recent_low) and isinstance(recent_5m_low, (int, float)) and p < float(recent_5m_low):
            _finalize("RECENT_5M_LOW_BREAK_EXIT", p, tu)
            return

    sp = float(getattr(pos, "stop_price", 0.0) or 0.0)
    tp = float(getattr(pos, "take_price", 0.0) or 0.0)
    if sp > 0 and p <= sp:
        _finalize("STOP_HIT", p, tu)
        return
    if tp > 0 and p >= tp:
        _finalize("TAKE_HIT", p, tu)
        return

    peak = float(getattr(pos, "max_price_after", p) or p)
    if ep > 0:
        prog_pct = (p - ep) / ep * 100.0
        peak_prog = (peak - ep) / ep * 100.0
        adj_prog = float(rtc.get("paper_structure_take_adjust_progress_pct") or 60.0)
        adj_pb = float(rtc.get("paper_structure_take_adjust_pullback_pct") or 0.2)
        if peak_prog >= adj_prog and peak > ep:
            pb = (peak - p) / max(1e-9, peak - ep) * 100.0
            if pb >= adj_pb - 1e-9:
                _finalize("TAKE_ADJUST", p, tu)
                return

        st0 = getattr(pos, "signal_time_utc", None)
        hold_sec = 0.0
        if isinstance(st0, datetime):
            s0 = st0 if st0.tzinfo else st0.replace(tzinfo=timezone.utc)
            hold_sec = abs((tu - s0).total_seconds())
        weak_en = bool(rtc.get("paper_early_weak_exit_enabled", True))
        weak_hold = float(rtc.get("paper_early_weak_exit_min_hold_sec") or 600.0)
        weak_prog = float(rtc.get("paper_early_weak_exit_progress_pct") or -10.0)
        req_pf = bool(rtc.get("paper_early_weak_exit_require_peak_fail", True))
        pfc = int(getattr(pos, "structure_peak_fail_count", 0) or 0)
        need_pf = int(rtc.get("paper_structure_take_adjust_peak_fail_count") or 2)
        if weak_en and hold_sec >= weak_hold and float(prog_pct) <= weak_prog:
            if (not req_pf) or pfc >= need_pf:
                _finalize("EARLY_WEAK_EXIT", p, tu)
                return


def _paper_trade_bump_structure_wall_reject(exec_counts: Optional[dict[str, Any]], rej: str) -> None:
    if exec_counts is None or not isinstance(exec_counts, dict):
        return
    rj = str(rej or "").strip().upper()
    if "WALL" in rj or "STRUCTURE_WALL" in rj:
        exec_counts["structure_wall_reject_count"] = int(exec_counts.get("structure_wall_reject_count") or 0) + 1
    rc = exec_counts.get("structure_reject_reason_counts")
    if not isinstance(rc, dict):
        rc = {}
        exec_counts["structure_reject_reason_counts"] = rc
    tag = str(rej or "").strip() or "UNKNOWN"
    if "|" in tag:
        tag = tag.split("|", 1)[0].strip()
    rc[tag] = int(rc.get(tag) or 0) + 1


def _paper_trade_bump_dynamic_rr_shadow(exec_counts: Optional[dict[str, Any]], *, entry: float, stop: float, take: float, tss: str) -> None:
    if exec_counts is None or not isinstance(exec_counts, dict):
        return
    if str(tss).strip().upper() != "DYNAMIC":
        return
    risk = max(float(entry) - float(stop), 1e-9)
    rew = max(float(take) - float(entry), 0.0)
    rr = rew / risk
    sh = exec_counts.get("shadow_dynamic_low_rr_filter")
    if not isinstance(sh, dict):
        sh = {"lt_0.15": 0, "lt_0.20": 0, "lt_0.30": 0}
        exec_counts["shadow_dynamic_low_rr_filter"] = sh
    if rr + 1e-12 < 0.15:
        sh["lt_0.15"] = int(sh.get("lt_0.15") or 0) + 1
    if rr + 1e-12 < 0.20:
        sh["lt_0.20"] = int(sh.get("lt_0.20") or 0) + 1
    if rr + 1e-12 < 0.30:
        sh["lt_0.30"] = int(sh.get("lt_0.30") or 0) + 1


def _paper_trade_update_peak_fail_count(pos: Any, *, price: float) -> None:
    p = float(price)
    peak = float(getattr(pos, "_structure_trailing_peak", p) or p)
    if p > peak:
        setattr(pos, "_structure_trailing_peak", p)
        return
    if peak > 0 and p < peak * (1.0 - 0.002):
        c = int(getattr(pos, "structure_peak_fail_count", 0) or 0) + 1
        setattr(pos, "structure_peak_fail_count", c)


def _paper_trade_bump_structure_candidate_aggregate(exec_counts: Optional[dict[str, Any]], take_csv: dict[str, str]) -> None:
    if exec_counts is None or not isinstance(exec_counts, dict) or not take_csv:
        return
    rr = str(take_csv.get("structure_take_best_rr") or "").strip()
    dp = str(take_csv.get("structure_candidate_distance_pct") or take_csv.get("structure_take_distance_pct") or "").strip()
    fr = str(take_csv.get("structure_candidate_failure_risk") or "").strip()
    if (not rr) and (not dp):
        return
    exec_counts["structure_candidate_n"] = int(exec_counts.get("structure_candidate_n") or 0) + 1
    if rr:
        try:
            exec_counts["structure_candidate_rr_sum"] = float(exec_counts.get("structure_candidate_rr_sum") or 0.0) + float(rr)
        except Exception:
            pass
    if dp:
        try:
            exec_counts["structure_candidate_dist_sum"] = float(exec_counts.get("structure_candidate_dist_sum") or 0.0) + float(dp)
        except Exception:
            pass
    if fr:
        try:
            exec_counts["structure_candidate_fail_risk_sum"] = float(
                exec_counts.get("structure_candidate_fail_risk_sum") or 0.0
            ) + float(fr)
        except Exception:
            pass


def _utc_to_jst_minute_key(dt: Any) -> str:
    if not isinstance(dt, datetime):
        return ""
    t = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return t.astimezone(JST).strftime("%Y-%m-%d %H:%M")


def _replay_logic_version_composite() -> str:
    from market.yahoo.paper_trade import (
        PAPER_TRADE_DRY_RUN_LOGIC_VERSION,
        SHARED_EXIT_ENGINE_VERSION,
        SHARED_SIGNAL_ENGINE_VERSION,
    )

    return (
        f"dry_run={PAPER_TRADE_DRY_RUN_LOGIC_VERSION};"
        f"sig={SHARED_SIGNAL_ENGINE_VERSION};exit={SHARED_EXIT_ENGINE_VERSION}"
    )


def _replay_config_file_sha256(path: str) -> str:
    p = str(path or "").strip()
    if not p or not os.path.isfile(p):
        return ""
    try:
        h = hashlib.sha256()
        with open(p, "rb") as bf:
            for chunk in iter(lambda: bf.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _write_replay_run_identity_file(
    *,
    results_dir: str,
    replay_batch_stamp: str,
    replay_logic_version: str,
    replay_config_hash: str,
    replay_range_label: str,
    replay_output_subdir: str,
    replay_config_path: str,
    paper_trade_dry_run: bool,
    alignment_csv: str = "",
    signals_csv: str = "",
) -> str:
    rd = str(results_dir or "").strip()
    if not rd:
        return ""
    os.makedirs(rd, exist_ok=True)
    path = os.path.join(rd, "replay_run_identity.json")
    payload: dict[str, Any] = {
        "replay_batch_stamp": str(replay_batch_stamp or "").strip(),
        "replay_logic_version": str(replay_logic_version or "").strip(),
        "replay_config_hash": str(replay_config_hash or "").strip(),
        "replay_range_label": str(replay_range_label or "").strip(),
        "replay_output_subdir": str(replay_output_subdir or "").strip(),
        "replay_config_path": str(replay_config_path or "").strip(),
        "paper_trade_dry_run": bool(paper_trade_dry_run),
        "alignment_csv": os.path.basename(str(alignment_csv or "").strip()) if alignment_csv else "",
        "signals_csv": os.path.basename(str(signals_csv or "").strip()) if signals_csv else "",
    }
    with open(path, "w", encoding="utf-8") as wf:
        json.dump(payload, wf, ensure_ascii=False, indent=2)
    return path


def _parse_iso_dt(cell: str) -> Optional[datetime]:
    s = str(cell or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None


def _paper_row_entry_minute_and_side(pr: dict[str, str]) -> tuple[str, str, str]:
    sym = str(pr.get("symbol") or "").strip()
    side = str(pr.get("entry_side") or "LONG").strip() or "LONG"
    eu = str(pr.get("entry_time_utc") or "").strip()
    if eu:
        t = _parse_iso_dt(eu)
        if t:
            return sym, _utc_to_jst_minute_key(t), side
    dj = str(pr.get("datetime_jst") or "").strip()
    if len(dj) >= 16:
        return sym, dj[:16], side
    return sym, "", side


def _paper_entry_dt_for_diff(pr: dict[str, str]) -> Optional[datetime]:
    eu = str(pr.get("entry_time_utc") or "").strip()
    if eu:
        t = _parse_iso_dt(eu)
        if t:
            return t
    dj = str(pr.get("datetime_jst") or "").strip()
    if len(dj) < 16:
        return None
    try:
        return datetime.strptime(dj[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=JST)
    except Exception:
        return None


def _replay_row_entry_minute_and_side(rr: dict[str, str], *, time_col: str) -> tuple[str, str, str]:
    sym = str(rr.get("symbol") or "").strip()
    side = str(rr.get("entry_side") or "LONG").strip() or "LONG"
    raw_t = rr.get(time_col)
    t = _parse_iso_dt(str(raw_t or ""))
    if not t:
        return sym, "", side
    return sym, _utc_to_jst_minute_key(t), side


def _find_replay_csv_for_identity(
    results_root: str,
    *,
    replay_batch_stamp: str,
    replay_logic_version: str,
    replay_config_hash: str,
) -> tuple[str, dict[str, Any], list[str]]:
    warnings: list[str] = []
    rb = str(replay_batch_stamp or "").strip()
    rl = str(replay_logic_version or "").strip()
    rh = str(replay_config_hash or "").strip()
    if not rb or not rl or not rh:
        return "", {}, []
    if not os.path.isdir(results_root):
        return "", {}, ["results_root_missing"]
    matches: list[tuple[float, str, dict[str, Any]]] = []
    for root, _dirs, files in os.walk(results_root):
        if "replay_run_identity.json" not in files:
            continue
        ip = os.path.join(root, "replay_run_identity.json")
        try:
            with open(ip, encoding="utf-8") as inf:
                ident = json.load(inf)
        except Exception as e:
            warnings.append(f"identity_read_error:{ip}:{e}")
            continue
        if not isinstance(ident, dict):
            continue
        if str(ident.get("replay_batch_stamp") or "").strip() != rb:
            continue
        if str(ident.get("replay_logic_version") or "").strip() != rl:
            continue
        if str(ident.get("replay_config_hash") or "").strip() != rh:
            continue
        ac = str(ident.get("alignment_csv") or "").strip()
        sc = str(ident.get("signals_csv") or "").strip()
        pick = ""
        if ac:
            ap = os.path.join(root, ac)
            if os.path.isfile(ap):
                pick = ap
        if not pick and sc:
            sp = os.path.join(root, sc)
            if os.path.isfile(sp):
                pick = sp
        if not pick:
            aln = [os.path.join(root, f) for f in files if str(f).endswith("_replay_alignment.csv")]
            sig = [os.path.join(root, f) for f in files if str(f).endswith("_signals.csv")]
            cand = aln + sig
            if not cand:
                warnings.append(f"identity_match_no_csv:{root}")
                continue
            pick = aln[0] if aln else sorted(sig)[-1]
        try:
            mt = os.path.getmtime(pick)
        except Exception:
            mt = 0.0
        matches.append((mt, pick, dict(ident)))
    if not matches:
        return "", {}, warnings + ["no_matching_replay_run_identity"]
    matches.sort(key=lambda x: x[0], reverse=True)
    return matches[0][1], matches[0][2], warnings


def _paper_trade_vwap_break_timing_from_signals(replay_signals: list[Any]) -> dict[str, Any]:
    w60 = w180 = w300 = 0
    hold_sum = 0.0
    peak_sum = 0.0
    n = 0
    for s in replay_signals:
        if bool(getattr(s, "excluded_from_eval", False)):
            continue
        er = str(getattr(s, "exit_reason", "") or "").strip().upper()
        if er != "VWAP_BREAK_EXIT":
            continue
        st = getattr(s, "signal_time_utc", None)
        et = getattr(s, "exit_time_utc", None)
        if not isinstance(st, datetime) or not isinstance(et, datetime):
            continue
        ss = st if st.tzinfo else st.replace(tzinfo=timezone.utc)
        ee = et if et.tzinfo else et.replace(tzinfo=timezone.utc)
        hold = abs((ee - ss).total_seconds())
        ep = float(getattr(s, "entry_price", 0.0) or 0.0)
        peakp = 0.0
        if ep > 0:
            peakp = (float(getattr(s, "max_price_after", ep) or ep) - ep) / ep * 100.0
        n += 1
        hold_sum += hold
        peak_sum += peakp
        if hold <= 60.0 + 1e-9:
            w60 += 1
        if hold <= 180.0 + 1e-9:
            w180 += 1
        if hold <= 300.0 + 1e-9:
            w300 += 1
    return {
        "vwap_break_exit_within_60s": w60,
        "vwap_break_exit_within_180s": w180,
        "vwap_break_exit_within_300s": w300,
        "avg_time_to_vwap_break_sec": (hold_sum / n) if n else 0.0,
        "avg_peak_profit_before_vwap_break_pct": (peak_sum / n) if n else 0.0,
        "vwap_break_timing_n": n,
    }


def _paper_trade_dynamic_low_rr_shadow_tables(replay_signals: list[Any]) -> list[dict[str, Any]]:
    from market.yahoo.paper_trade import _baseline_pnl, _shadow_exit_removed_counts

    cohort: list[dict[str, Any]] = []
    for s in replay_signals:
        if bool(getattr(s, "excluded_from_eval", False)):
            continue
        tc = getattr(s, "take_diag_csv", None)
        if not isinstance(tc, dict):
            continue
        if str(tc.get("take_structure_selection") or "").strip().upper() != "DYNAMIC":
            continue
        ep = float(getattr(s, "entry_price", 0.0) or 0.0)
        sp = float(getattr(s, "stop_price", 0.0) or 0.0)
        tp = float(getattr(s, "take_price", 0.0) or 0.0)
        risk = max(ep - sp, 1e-9)
        rew = max(tp - ep, 0.0)
        rr = rew / risk
        row = replay_signal_eval_to_shadow_row(s)
        row["_dyn_rr"] = float(rr)
        cohort.append(row)

    def one(th: float, key: str) -> dict[str, Any]:
        blocked = [x for x in cohort if float(x.get("_dyn_rr", 99.0)) < th - 1e-12]
        kept = [x for x in cohort if float(x.get("_dyn_rr", 99.0)) >= th - 1e-12]
        base = _baseline_pnl(cohort)
        after = _baseline_pnl(kept)
        ec = _shadow_exit_removed_counts(blocked)
        return {
            "key": key,
            "threshold_max_rr": th,
            "blocked_count": int(len(blocked)),
            "pnl_improvement": float(after - base),
            **ec,
        }

    return [
        one(0.15, "dynamic_rr_lt_0_15"),
        one(0.20, "dynamic_rr_lt_0_20"),
        one(0.30, "dynamic_rr_lt_0_30"),
    ]


def _build_paper_replay_divergence_report(
    *,
    paper_csv_path: str,
    results_root: str,
    replay_timestamp_bucket_mode: str = "minute_jst",
    replay_batch_stamp: str = "",
    replay_logic_version: str = "",
    replay_config_hash: str = "",
) -> dict[str, Any]:
    """Load paper vs replay CSV (optional identity filter) with match ratios and diff stats."""
    out: dict[str, Any] = {
        "paper_replay_divergence": [],
        "replay_events": 0,
        "replay_signal_csv_found": False,
        "replay_signal_rows_loaded": 0,
        "replay_matchable_rows": 0,
        "replay_timestamp_bucket_mode": str(replay_timestamp_bucket_mode),
        "replay_signal_csv_path": "",
        "divergence_warnings": [],
        "replay_run_identity_matched": {},
        "replay_paper_exact_match_ratio": 0.0,
        "replay_paper_partial_match_ratio": 0.0,
        "replay_only_count": 0,
        "paper_only_count": 0,
        "avg_entry_time_diff_sec": 0.0,
        "max_entry_time_diff_sec": 0.0,
        "avg_exit_time_diff_sec": 0.0,
        "max_exit_time_diff_sec": 0.0,
        "avg_price_diff_pct": 0.0,
        "avg_take_diff_pct": 0.0,
        "avg_stop_diff_pct": 0.0,
        "replay_identity_filter": "disabled",
    }
    paper_rows: list[dict[str, str]] = []
    if os.path.isfile(paper_csv_path):
        try:
            with open(paper_csv_path, newline="", encoding="utf-8") as pf:
                r = csv.DictReader(pf)
                for row in r:
                    if isinstance(row, dict):
                        paper_rows.append({str(k): str(v) for k, v in row.items()})
        except Exception as e:
            out["divergence_warnings"].append(f"paper_csv_read_error:{e}")

    replay_path = ""
    ident_matched: dict[str, Any] = {}
    use_identity = bool(
        str(replay_batch_stamp or "").strip()
        and str(replay_logic_version or "").strip()
        and str(replay_config_hash or "").strip()
    )
    if use_identity:
        out["replay_identity_filter"] = "strict"
        replay_path, ident_matched, id_warn = _find_replay_csv_for_identity(
            results_root,
            replay_batch_stamp=str(replay_batch_stamp),
            replay_logic_version=str(replay_logic_version),
            replay_config_hash=str(replay_config_hash),
        )
        out["divergence_warnings"].extend(id_warn)
        if ident_matched:
            out["replay_run_identity_matched"] = ident_matched
    else:
        out["divergence_warnings"].append("replay_identity_filter_disabled")
        newest_mtime = -1.0
        if os.path.isdir(results_root):
            for root, _dirs, files in os.walk(results_root):
                for fn in files:
                    if not (
                        str(fn).endswith("_signals.csv")
                        or str(fn).endswith("_replay_alignment.csv")
                    ):
                        continue
                    p = os.path.join(root, fn)
                    try:
                        mt = os.path.getmtime(p)
                    except Exception:
                        continue
                    if mt > newest_mtime:
                        newest_mtime = mt
                        replay_path = p
    if replay_path and os.path.isfile(replay_path):
        out["replay_signal_csv_found"] = True
        out["replay_signal_csv_path"] = replay_path

    replay_by_key: dict[tuple[str, str, str], dict[str, str]] = {}
    if replay_path:
        try:
            with open(replay_path, newline="", encoding="utf-8") as rf:
                rr = csv.DictReader(rf)
                flds = [x.strip() for x in (rr.fieldnames or []) if x]
                time_col = "entry_time_utc" if "entry_time_utc" in flds else ""
                for row in rr:
                    if not isinstance(row, dict):
                        continue
                    out["replay_signal_rows_loaded"] = int(out["replay_signal_rows_loaded"]) + 1
                    if not time_col:
                        continue
                    sym, minute, side = _replay_row_entry_minute_and_side(
                        {str(k): str(v) for k, v in row.items()},
                        time_col=time_col,
                    )
                    if not sym or not minute:
                        continue
                    replay_by_key[(sym, minute, side)] = {str(k): str(v) for k, v in row.items()}
        except Exception as e:
            out["divergence_warnings"].append(f"replay_csv_read_error:{e}")

    fields = [
        "entry_price",
        "stop_price",
        "take_price",
        "exit_reason",
        "entry_quality_score",
        "chase_extension_pct",
        "same_symbol_cooldown_would_block",
        "market_weakness_score",
        "take_structure_selection",
    ]
    div: list[dict[str, Any]] = []
    paper_keys: set[tuple[str, str, str]] = set()
    exact_n = 0

    entry_diffs: list[float] = []
    exit_diffs: list[float] = []
    price_dpcts: list[float] = []
    take_dpcts: list[float] = []
    stop_dpcts: list[float] = []

    for pr in paper_rows:
        if str(pr.get("signal_type") or "").upper() not in ("LIVE", "BASE", ""):
            continue
        sk = str(pr.get("skipped") or "")
        pb = _parse_bool_cell(sk)
        if pb is True:
            continue
        if sk.strip() == "1":
            continue
        sym, minute, side = _paper_row_entry_minute_and_side(pr)
        if not sym or not minute:
            continue
        paper_keys.add((sym, minute, side))
        rr = replay_by_key.get((sym, minute, side))
        if rr is None:
            div.append({"symbol": sym, "minute": minute, "entry_side": side, "status": "no_replay_row"})
            continue
        out["replay_matchable_rows"] = int(out["replay_matchable_rows"]) + 1
        erw = str(pr.get("exit_reason") or "").strip()
        prr = str(rr.get("exit_reason") or "").strip()
        ptss = str(pr.get("take_structure_selection") or "").strip()
        rtss = str(rr.get("take_structure_selection") or "").strip()
        if erw == prr and ptss == rtss:
            exact_n += 1

        pet = _paper_entry_dt_for_diff(pr)
        ret = _parse_iso_dt(str(rr.get("entry_time_utc") or ""))
        if pet is not None and ret is not None:
            petu = pet if pet.tzinfo else pet.replace(tzinfo=JST)
            retu = ret if ret.tzinfo else ret.replace(tzinfo=timezone.utc)
            entry_diffs.append(float(abs((retu - petu).total_seconds())))

        pxt = _parse_iso_dt(str(pr.get("exit_time_utc") or ""))
        rxt = _parse_iso_dt(str(rr.get("exit_time_utc") or ""))
        if pxt is not None and rxt is not None:
            pxu = pxt if pxt.tzinfo else pxt.replace(tzinfo=timezone.utc)
            rxu = rxt if rxt.tzinfo else rxt.replace(tzinfo=timezone.utc)
            exit_diffs.append(float(abs((rxu - pxu).total_seconds())))

        def _fp(x: str) -> Optional[float]:
            try:
                return float(str(x).strip())
            except Exception:
                return None

        pep = _fp(str(pr.get("entry_price") or ""))
        rep = _fp(str(rr.get("entry_price") or ""))
        if pep is not None and rep is not None and abs(rep) > 1e-12:
            price_dpcts.append(abs(pep - rep) / abs(rep) * 100.0)
        ptp = _fp(str(pr.get("take_price") or ""))
        rtp = _fp(str(rr.get("take_price") or ""))
        if ptp is not None and rtp is not None and abs(rtp) > 1e-12:
            take_dpcts.append(abs(ptp - rtp) / abs(rtp) * 100.0)
        psp = _fp(str(pr.get("stop_price") or ""))
        rsp = _fp(str(rr.get("stop_price") or ""))
        if psp is not None and rsp is not None and abs(rsp) > 1e-12:
            stop_dpcts.append(abs(psp - rsp) / abs(rsp) * 100.0)

        row_diff: dict[str, Any] = {"symbol": sym, "minute": minute, "entry_side": side}
        for f in fields:
            a = str(pr.get(f) or "").strip()
            b = str(rr.get(f) or "").strip()
            if f == "same_symbol_cooldown_would_block":
                ca = _parse_bool_cell(a)
                cb = _parse_bool_cell(b)
                if ca is not None and cb is not None and ca != cb:
                    row_diff[f] = {"paper": a, "replay": b}
                elif ca is None and a != b:
                    row_diff[f] = {"paper": a, "replay": b}
            elif a != b:
                row_diff[f] = {"paper": a, "replay": b}
        if len(row_diff) > 3:
            div.append(row_diff)

    replay_keys = set(replay_by_key.keys())
    out["paper_only_count"] = int(len(paper_keys - replay_keys))
    out["replay_only_count"] = int(len(replay_keys - paper_keys))
    denom = max(1, int(out["replay_matchable_rows"]))
    out["replay_paper_exact_match_ratio"] = float(exact_n) / float(denom)
    out["replay_paper_partial_match_ratio"] = float(denom - exact_n) / float(denom)
    if entry_diffs:
        out["avg_entry_time_diff_sec"] = float(sum(entry_diffs) / len(entry_diffs))
        out["max_entry_time_diff_sec"] = float(max(entry_diffs))
    if exit_diffs:
        out["avg_exit_time_diff_sec"] = float(sum(exit_diffs) / len(exit_diffs))
        out["max_exit_time_diff_sec"] = float(max(exit_diffs))
    if price_dpcts:
        out["avg_price_diff_pct"] = float(sum(price_dpcts) / len(price_dpcts))
    if take_dpcts:
        out["avg_take_diff_pct"] = float(sum(take_dpcts) / len(take_dpcts))
    if stop_dpcts:
        out["avg_stop_diff_pct"] = float(sum(stop_dpcts) / len(stop_dpcts))

    out["paper_replay_divergence"] = div
    out["replay_events"] = int(out["replay_matchable_rows"])
    if not out["replay_signal_csv_found"]:
        out["divergence_warnings"].append("no_replay_signals_csv_found_under_results")
    return out


def _replay_shared_engine_trace_row(
    *,
    event: str,
    symbol: str,
    timestamp_jst: str,
    engine_mode: str,
    entry_price: float,
    stop_price: float,
    take_price: float,
    exit_reason: str = "",
    exit_price: Optional[float] = None,
    eq_scores: Optional[dict[str, Any]] = None,
    take_csv: Optional[dict[str, str]] = None,
    take_meta: Optional[dict[str, Any]] = None,
    pos: Optional[dict[str, Any]] = None,
    crossed_true: Optional[bool] = None,
    signal_appended: Optional[bool] = None,
    replay_signal_written: Optional[bool] = None,
    event_type: str = "",
    skip_reason: str = "",
    excluded_from_eval: Optional[bool] = None,
    stale_signal: Optional[bool] = None,
    lag_sec: Optional[float] = None,
) -> dict[str, Any]:
    eq = dict(eq_scores or {})
    tc = dict(take_csv or {})
    tm = dict(take_meta or {})
    p = dict(pos or {})
    tss = str(
        p.get("take_structure_selection")
        or tc.get("take_structure_selection")
        or tm.get("take_structure_selection")
        or ""
    )
    dyn_fb = str(
        tc.get("dynamic_fallback_policy")
        or tm.get("dynamic_fallback_policy")
        or p.get("dynamic_fallback_policy")
        or ""
    )
    if not dyn_fb and tss == "DYNAMIC":
        dyn_fb = str(tc.get("take_calc_method") or "dynamic_min_rr_floor")
    nr_raw = (
        p.get("nearest_resistance")
        or tc.get("nearest_resistance")
        or tm.get("nearest_resistance")
    )
    nr: Optional[float] = None
    if isinstance(nr_raw, (int, float)):
        nr = float(nr_raw)
    elif nr_raw is not None and str(nr_raw).strip():
        try:
            nr = float(str(nr_raw).strip())
        except Exception:
            nr = None
    et = str(event_type or "").strip()
    if not et:
        ev = str(event or "").strip()
        if ev == "SIGNAL_OPEN":
            et = "OPEN_POSITION"
        elif ev == "POSITION_CLOSE":
            er0 = str(exit_reason or "").strip().upper()
            et = "TAKE_ADJUST" if er0 == "TAKE_ADJUST" else "CLOSE_POSITION"
        else:
            et = ev
    row: dict[str, Any] = {
        "event": str(event),
        "event_type": et,
        "symbol": str(symbol),
        "timestamp_jst": str(timestamp_jst),
        "time_jst": str(timestamp_jst),
        "engine_mode": str(engine_mode),
        "engine": str(engine_mode),
        "entry_price": float(entry_price),
        "stop_price": float(stop_price),
        "take_price": float(take_price),
        "exit_reason": str(exit_reason or ""),
        "exit_price": (float(exit_price) if isinstance(exit_price, (int, float)) else None),
        "entry_quality_score": float(eq.get("entry_quality_score") or 0.0),
        "breakout_freshness_score": float(eq.get("breakout_freshness_score") or 0.0),
        "breakout_failure_risk_score": float(eq.get("breakout_failure_risk_score") or 0.0),
        "breakout_late_entry_flag": float(eq.get("breakout_late_entry_flag") or 0.0),
        "breakout_extension_pct": float(eq.get("breakout_extension_pct") or 0.0),
        "chase_extension_pct": float(eq.get("chase_extension_pct") or 0.0),
        "same_symbol_cooldown_would_block": bool(eq.get("same_symbol_cooldown_would_block", False)),
        "market_weakness_score": float(eq.get("market_weakness_score") or 0.0),
        "vwap_under_duration_bars": float(eq.get("vwap_under_duration_bars") or 0.0),
        "pre_entry_vwap_hold_bars": float(eq.get("pre_entry_vwap_hold_bars") or 0.0),
        "pre_entry_vwap_under_bars": float(eq.get("pre_entry_vwap_under_bars") or 0.0),
        "post_breakout_vwap_hold_bars": float(eq.get("post_breakout_vwap_hold_bars") or 0.0),
        "vwap_retouch_count_after_breakout": float(eq.get("vwap_retouch_count_after_breakout") or 0.0),
        "vwap_break_early_risk_score": float(eq.get("vwap_break_early_risk_score") or 0.0),
        "take_structure_selection": str(tss),
        "structure_take_reject_reason": str(
            tc.get("structure_take_reject_reason") or tm.get("structure_take_reject_reason") or ""
        ),
        "nearest_resistance": nr,
        "dynamic_fallback_reason": dyn_fb,
        "breakout_volume_continuation_score": float(eq.get("breakout_volume_continuation_score") or 0.0),
        "crossed_true": crossed_true,
        "signal_appended": signal_appended,
        "replay_signal_written": replay_signal_written,
        "skip_reason": str(skip_reason or ""),
        "structure_take_distance_pct": (
            float(tc["structure_take_distance_pct"])
            if str(tc.get("structure_take_distance_pct") or "").strip()
            else None
        ),
        "structure_take_raw_rr": (
            float(tc["structure_take_raw_rr"]) if str(tc.get("structure_take_raw_rr") or "").strip() else None
        ),
        "structure_take_after_epsilon_rr": (
            float(tc["structure_take_after_epsilon_rr"])
            if str(tc.get("structure_take_after_epsilon_rr") or "").strip()
            else None
        ),
        "structure_take_required_rr": (
            float(tc["structure_take_required_rr"])
            if str(tc.get("structure_take_required_rr") or "").strip()
            else None
        ),
        "structure_take_failed_rule": str(tc.get("structure_take_failed_rule") or ""),
    }
    if excluded_from_eval is not None:
        row["excluded_from_eval"] = bool(excluded_from_eval)
    if stale_signal is not None:
        row["stale_signal"] = bool(stale_signal)
    if lag_sec is not None and isinstance(lag_sec, (int, float)):
        row["lag_sec"] = float(lag_sec)
    return row


def _paper_trade_rtc_merge_phase2_from_cfg(file_cfg: dict[str, Any], rtc: dict[str, Any]) -> dict[str, Any]:
    import market.yahoo.watch as yk

    out = dict(rtc or {})
    flags = yk._apply_replay_config_to_flags(cfg=file_cfg if isinstance(file_cfg, dict) else {})
    for k in (
        "replay_chase_extension_autoblock_enabled",
        "replay_chase_extension_ge_pct",
        "same_symbol_cooldown_sec",
        "same_symbol_cooldown_shadow_only",
        "paper_entry_quality_min_for_open",
        "use_paper_position_exec",
    ):
        if k in flags:
            out[k] = flags[k]
    return out


def _paper_trade_phase2_state_blank() -> dict[tuple[str, str], dict[str, Any]]:
    return {}


def _paper_trade_phase2_record_signal(
    state: dict[tuple[str, str], dict[str, Any]],
    *,
    day_jst: str,
    symbol: str,
    entry_price: float,
    signal_time: datetime,
) -> None:
    state[(str(day_jst), str(symbol))] = {"t": signal_time, "entry": float(entry_price)}


def _paper_trade_phase2_compute_entry_context(
    *,
    state: dict[tuple[str, str], dict[str, Any]],
    rtc: dict[str, Any],
    day_jst: str,
    symbol: str,
    entry_nf: float,
    signal_time: datetime,
    market_snap: dict[str, Any],
    entry_quality_score: Optional[float],
) -> dict[str, Any]:
    key = (str(day_jst), str(symbol))
    prev = state.get(key)
    prev_age: Optional[float] = None
    if isinstance(prev, dict) and isinstance(prev.get("t"), datetime):
        try:
            a = prev["t"]
            if a.tzinfo is None:
                a = a.replace(tzinfo=timezone.utc)
            b = signal_time if signal_time.tzinfo else signal_time.replace(tzinfo=timezone.utc)
            prev_age = abs((b - a).total_seconds())
        except Exception:
            prev_age = None
    cooldown_sec = int(rtc.get("same_symbol_cooldown_sec") or 300)
    shadow_only = bool(rtc.get("same_symbol_cooldown_shadow_only", True))
    would_block = bool(prev_age is not None and prev_age < float(cooldown_sec))
    hard_block = would_block and (not shadow_only)
    chase = 0.0
    if isinstance(market_snap, dict) and isinstance(market_snap.get("chase_extension_pct"), (int, float)):
        chase = float(market_snap["chase_extension_pct"])
    bucket = _chase_extension_bucket(chase) if chase else "none"
    return {
        "chase_extension_pct": chase,
        "extension_bucket": bucket,
        "prev_signal_age_sec": prev_age if prev_age is not None else "",
        "prev_signal_pnl": "",
        "prev_signal_exit_reason": "",
        "same_symbol_cooldown_would_block": bool(would_block),
        "market_weakness_score": float(market_snap.get("market_weakness_score") or 0.0),
        "market_breadth_score": market_snap.get("rising_ratio"),
        "market_trend_pressure_score": market_snap.get("high_ratio"),
        "lt50_ratio": float(market_snap.get("lt50_ratio") or 0.0),
        "phase2_entry_block_reason": "",
        "phase2_would_hard_block": bool(hard_block),
        "quality_rank_in_day": "",
        "quality_rank_in_symbol": "",
        "quality_percentile": "",
        "paper_entry_quality_min_for_open_applied": None,
        "replay_chase_autoblock_hit": False,
        "entry_quality_score_for_gate": entry_quality_score,
        "nearest_resistance_info": "",
    }


def _row_day_jst(row: dict[str, Any]) -> str:
    t = str(row.get("datetime_jst") or "")[:10]
    if len(t) == 10 and t[4] == "-" and t[7] == "-":
        return t
    return ""


def _parse_csv_float(cell: Any) -> Optional[float]:
    s = str(cell or "").strip()
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _paper_trade_phase2_enrich_quality_ranks(rows: list[dict[str, Any]]) -> None:
    groups: dict[str, list[tuple[int, float]]] = {}
    for i, r in enumerate(rows or []):
        if not isinstance(r, dict):
            continue
        if str(r.get("signal_type") or "").upper() != "LIVE":
            continue
        day = _row_day_jst(r)
        sym = str(r.get("symbol") or "")
        key = f"{day}::{sym}"
        eq = _parse_csv_float(r.get("entry_quality_score"))
        if eq is None:
            eq = 0.5
        groups.setdefault(key, []).append((i, float(eq)))
        kd = f"{day}::__day__"
        groups.setdefault(kd, []).append((i, float(eq)))
    for _k, lst in groups.items():
        lst_sorted = sorted(lst, key=lambda t: t[1])
        n = len(lst_sorted)
        for pos, (ii, _) in enumerate(lst_sorted):
            rank_pct = float(pos) / float(max(1, n - 1)) if n > 1 else 0.5
            tgt = rows[ii]
            if _k.endswith("::__day__"):
                tgt["quality_rank_in_day"] = f"{rank_pct:.4f}"
            else:
                tgt["quality_rank_in_symbol"] = f"{rank_pct:.4f}"
                tgt["quality_percentile"] = f"{rank_pct:.4f}"


def _paper_trade_phase2_shadow_from_csv_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cohort: list[dict[str, Any]] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        if str(r.get("signal_type") or "").upper() != "LIVE":
            continue
        if str(r.get("skipped") or "") not in ("0", "", "false", "False"):
            continue
        pnl_opt = _parse_csv_float(r.get("pnl_yen_100_shares"))
        rr = _parse_csv_float(r.get("rising_ratio"))
        breadth = float(rr) if rr is not None else 0.5
        cohort.append(
            {
                "symbol": str(r.get("symbol") or ""),
                "position_kind": "BASE",
                "excluded_from_eval": False,
                "pnl_yen_100_shares": float(pnl_opt or 0.0),
                "exit_reason": str(r.get("exit_reason") or ""),
                "entry_quality_score": float(_parse_csv_float(r.get("entry_quality_score")) or 0.5),
                "chase_extension_pct": float(_parse_csv_float(r.get("chase_extension_pct")) or 0.0),
                "rising_ratio": rr,
                "market_breadth_score": breadth,
                "lt50_ratio": float(_parse_csv_float(r.get("lt50_ratio")) or 0.0),
                "market_weakness_score": float(_parse_csv_float(r.get("market_weakness_score")) or 0.0),
                "take_structure_selection": str(r.get("take_structure_selection") or ""),
                "structure_take_best_rr": float(_parse_csv_float(r.get("structure_take_best_rr")) or 0.0),
                "nearest_resistance": _parse_csv_float(r.get("nearest_resistance")),
                "structure_take_reject_reason": str(r.get("structure_take_reject_reason") or ""),
                "momentum_decay_features": {"prev_signal_exists": False},
            }
        )
    return _build_replay_shadow_filter_validation(cohort)


def replay_signal_eval_to_shadow_row(s: Any) -> dict[str, Any]:
    """Map ReplaySignalEval (+ optional dynamic attrs) to shadow-validation dict."""
    sym = str(getattr(s, "symbol", "") or "")
    ep = float(getattr(s, "entry_price", 0.0) or 0.0)
    mdf: dict[str, Any] = {"prev_signal_exists": bool(getattr(s, "prev_signal_exists", False))}
    if getattr(s, "price_change_pct_from_prev_signal", None) is not None:
        try:
            mdf["price_change_pct_from_prev_signal"] = float(getattr(s, "price_change_pct_from_prev_signal"))
        except Exception:
            pass
    return {
        "symbol": sym,
        "position_kind": str(getattr(s, "position_kind", "BASE") or "BASE"),
        "excluded_from_eval": bool(getattr(s, "excluded_from_eval", False)),
        "pnl_yen_100_shares": float(getattr(s, "pnl_yen_100_shares", 0.0) or 0.0),
        "exit_reason": str(getattr(s, "exit_reason", "") or getattr(s, "trailing_exit_reason", "") or ""),
        "entry_price": ep,
        "high_update_count_before_entry": int(getattr(s, "high_update_count_before_entry", 0) or 0),
        "breakout_volume_continuation_score": float(getattr(s, "breakout_volume_continuation_score", 0.5) or 0.5),
        "entry_quality_score": float(getattr(s, "entry_quality_score", 0.5) or 0.5),
        "vwap_break_early_risk_score": float(getattr(s, "vwap_break_early_risk_score", 0.0) or 0.0),
        "momentum_decay_features": mdf,
        "structure_take_reject_reason": str(getattr(s, "structure_take_reject_reason", "") or ""),
        "take_structure_selection": str(getattr(s, "take_structure_selection", "") or ""),
        "nearest_resistance": getattr(s, "nearest_resistance", None),
        "chase_extension_pct": float(getattr(s, "chase_extension_pct", 0.0) or 0.0),
        "market_weakness_score": float(getattr(s, "market_weakness_score", 0.0) or 0.0),
        "market_breadth_score": float(getattr(s, "market_breadth_score", 0.5) or 0.5),
        "lt50_ratio": float(getattr(s, "lt50_ratio", 0.0) or 0.0),
    }


__all__ = [
    "_PAPER_TRADE_ENTRY_QUALITY_CSV_FIELDNAMES",
    "_PAPER_TRADE_VWAP_DIAG_CSV_FIELDS",
    "_build_paper_replay_divergence_report",
    "_paper_trade_breakout_entry_quality_scores",
    "_paper_trade_bump_dynamic_rr_shadow",
    "_paper_trade_bump_entry_quality_summary",
    "_paper_trade_bump_structure_wall_reject",
    "_paper_trade_bump_structure_candidate_aggregate",
    "_paper_trade_bump_take_selection_counters",
    "_paper_trade_compute_stop_take_for_signal",
    "_paper_trade_csv_header_extend_phase2",
    "_paper_trade_deferral_row_set_lag_fields",
    "_paper_trade_default_runtime_controls",
    "_paper_trade_entry_quality_csv_columns",
    "_paper_trade_execution_counters_blank",
    "_paper_trade_merge_runtime_controls",
    "_paper_trade_phase2_compute_entry_context",
    "_paper_trade_phase2_record_signal",
    "_paper_trade_phase2_shadow_from_csv_rows",
    "_paper_trade_recent_5m_range_from_extras",
    "_paper_trade_rtc_merge_phase2_from_cfg",
    "_paper_trade_structure_relaxed_gate",
    "_paper_trade_take_meta_from_csv",
    "_paper_trade_try_close_open_position",
    "_paper_trade_update_peak_fail_count",
    "_parse_bool_cell",
    "_replay_shared_engine_trace_row",
    "_shared_engine_trace_jsonl_append",
    "_replay_config_file_sha256",
    "_replay_logic_version_composite",
    "_write_replay_run_identity_file",
    "_paper_trade_vwap_break_timing_from_signals",
    "_paper_trade_dynamic_low_rr_shadow_tables",
    "replay_signal_eval_to_shadow_row",
    "run_paper_trade_dry_run_replay",
]


def run_paper_trade_dry_run_replay(*, dry_run_day: str, replay_config_path: str, script_dir: str) -> int:
    """Dry-run replay: delegates to market.yahoo.watch.run_paper_trade_dry_run_replay_impl (real replay loop)."""
    import market.yahoo.watch as _yk

    return int(
        _yk.run_paper_trade_dry_run_replay_impl(
            dry_run_day=str(dry_run_day or "").strip(),
            replay_config_path=str(replay_config_path or "").strip(),
            script_dir=str(script_dir or "").strip(),
        )
    )
