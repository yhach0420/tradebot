"""
Phase463 — Trend/Pullback Population Tournament (research only).

Compares Pullback / Trend / Hybrid entry populations across all ENTRY candidates.
"""

from __future__ import annotations

import json
import pickle
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv, read_jpx_sector_map
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase365_production_stack_validation import phase364_blocked_only
from research.phase440_boundary_capacity_audit import ShadowExitInfo
from research.phase460_entry_gate_failure_audit import _load_dynamic40_records
from research.phase436_pullback_guard_redesign_shadow import _price_at_or_before, guard_high_drift
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY, _precompute_np_shadows
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import (
    DAY_618,
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _chronological_pnls_from_log,
    _enrich_candidates,
    _load_candidate_stream,
    _now_iso,
    _optional_float,
    _symbol_pnl_from_log,
)
from research.phase451b_entry_shape_tournament_mid_high import _board_token, _passes_baseline_mid_high
from research.phase456_entry_features import enrich_trade_phase456_features
from research.phase456c_vwap_structure_features import enrich_trade_phase456c_features
from research.phase459_winner_pattern_audit import _stop_rate_from_log
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.weak_shape_reject_entry_guard import would_block_weak_shape_reject

JST = ZoneInfo("Asia/Tokyo")
REPLAY_MODE = "phase456_runtime_np"
TARGET_SYMBOLS = ("6976.T", "4062.T", "3441.T", "6492.T", "7256.T", "7600.T")
UPTREND_CAPTURE = ("3441.T", "6492.T", "7256.T", "7600.T")

TOURNAMENT_FIELDS = [
    "variant",
    "group",
    "accepted_count",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "stop_rate",
    "delta_pnl_vs_a0",
    "delta_pf_vs_a0",
    "delta_maxdd_vs_a0",
    "daily_pnl_618",
    "daily_pnl_619",
    "delta_daily_pnl_618",
    "delta_daily_pnl_619",
    "symbol_pnl_6976",
    "symbol_pnl_4062",
    "delta_symbol_pnl_6976",
    "delta_symbol_pnl_4062",
    "captured_3441",
    "captured_6492",
    "captured_7256",
    "captured_7600",
    "top_day_share",
    "top_symbol_share",
]

REGIME_FIELDS = [
    "regime_label",
    "count",
    "accepted_count",
    "would_pnl_yen",
    "replay_pnl_yen",
    "profit_factor",
    "stop_rate",
]

SYMBOL_CAPTURE_FIELDS = [
    "variant",
    "symbol",
    "captured",
    "symbol_pnl_yen",
    "delta_symbol_pnl_vs_a0",
]

_CACHE_VERSION = 4

_INJECT_REASONS = (
    "near_day_high_low_momentum_dynamic40_guard",
    "high_drift_pullback",
    "pullback_misread_dynamic40_guard",
    "max_concurrent",
    "max_entries_per_scan",
    "momentum_low_required",
)

_SKIP_REJECT_PREFIX = ("data_stale", "daytrade_suitability", "am_pm_entry_stop", "REJECT_SAME_SYMBOL")


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


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


def _rise(trade: Mapping[str, Any], mins: int) -> Optional[float]:
    return _float(trade.get(f"return_{mins}min_pct")) or _float(trade.get(f"entry_rise_{mins}min_pct"))


def _vwap_above_ratio(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("vwap_above_ratio")) or _float(trade.get("vwap_above_ratio_20tick"))


def _vwap_dev(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("entry_vwap_dev_pct")) or _float(trade.get("vwap_dev_pct"))


def _board_mid_high(trade: Mapping[str, Any]) -> bool:
    tok = _board_token(trade) or ""
    return tok in ("Board:mid", "Board:high")


def _board_bucket(trade: Mapping[str, Any]) -> str:
    tok = _board_token(trade) or ""
    return tok.split(":", 1)[-1] if ":" in tok else "unknown"


def _weak_shape_block(trade: Mapping[str, Any]) -> bool:
    return would_block_weak_shape_reject(_map_runtime_fields(trade))


def _momentum_score(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("momentum_continuation_score")) or _float(trade.get("entry_momentum_score"))


def _valid_replay_trade(
    trade: Mapping[str, Any],
    shadow: Any = None,
) -> bool:
    et = _parse_ts(str(trade.get("entry_time") or ""))
    if et is None:
        return False
    try:
        day = et.astimezone(JST).strftime("%Y%m%d")
    except (OverflowError, OSError, ValueError):
        return False
    if day < PERIOD_START or day > PERIOD_END:
        return False
    if (_float(trade.get("entry_price")) or 0) <= 0:
        return False
    if shadow is not None and getattr(shadow, "eval_ok", False):
        try:
            ex = datetime.fromtimestamp(float(shadow.shadow_exit_ts), tz=JST)
            ex.astimezone(JST).strftime("%Y%m%d")
        except (OverflowError, OSError, ValueError, TypeError):
            return False
    return True


def _fill_close_proxy_shadows(
    replay_pool: Sequence[Mapping[str, Any]],
    np_shadows: Mapping[str, ShadowExitInfo],
    *,
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> dict[str, ShadowExitInfo]:
    out = dict(np_shadows)
    filled = 0
    for trade in replay_pool:
        key = _position_key(trade)
        sh = out.get(key)
        if sh and sh.eval_ok:
            continue
        day = str(trade.get("day") or "")[:8]
        if not day:
            continue
        try:
            close_dt = datetime.strptime(f"{day} 15:30:00", "%Y%m%d %H:%M:%S").replace(tzinfo=JST)
            close_dt.astimezone(JST).strftime("%Y%m%d")
        except (OverflowError, OSError, ValueError):
            continue
        pnl = _close_proxy_pnl(trade, price_idx)
        out[key] = ShadowExitInfo(
            shadow_exit_ts=close_dt.timestamp(),
            shadow_exit_reason="close_proxy",
            shadow_pnl_yen=pnl,
            baseline_pnl_yen=pnl,
            baseline_cap_ts=close_dt.timestamp(),
            post_baseline_violation=False,
            eval_ok=True,
        )
        filled += 1
    if filled:
        print(f"phase463 close_proxy shadows filled: {filled}", flush=True)
    return out


def _filter_replay_pool(
    replay_pool: Sequence[Mapping[str, Any]],
    np_shadows: Mapping[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    dropped = 0
    for trade in replay_pool:
        key = _position_key(trade)
        if _valid_replay_trade(trade, np_shadows.get(key)):
            out.append(dict(trade))
        else:
            dropped += 1
    if dropped:
        print(f"phase463 dropped invalid replay trades: {dropped}", flush=True)
    return out


def _is_actionable_record(rec: Mapping[str, Any]) -> bool:
    reason = str(rec.get("gate_reject_reason") or rec.get("reject_reason") or "")
    return not any(reason.startswith(p) for p in _SKIP_REJECT_PREFIX)


def _should_inject_for_replay(rec: Mapping[str, Any]) -> bool:
    reason = str(rec.get("gate_reject_reason") or rec.get("reject_reason") or "")
    if any(reason.startswith(p) for p in _SKIP_REJECT_PREFIX):
        return False
    return any(s in reason for s in _INJECT_REASONS)


def _enrich_light(
    rec: Mapping[str, Any],
    canon_by_key: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    key = _position_key(rec)
    t = dict(canon_by_key.get(key, rec))
    t.update({k: v for k, v in rec.items() if v not in (None, "")})
    t.setdefault("day", rec.get("day"))
    px = _float(t.get("entry_price")) or _float(t.get("current_price"))
    if px:
        t["entry_price"] = px
    t = _map_runtime_fields(t)
    t["day_high_distance_pct"] = _optional_float(t.get("day_high_distance_pct")) or _optional_float(
        t.get("entry_near_day_high_pct")
    )
    t["board_bucket"] = _board_bucket(t)
    t["momentum_score"] = _momentum_score(t)
    t["vwap_dev_pct"] = _vwap_dev(t)
    t["vwap_above_ratio"] = _vwap_above_ratio(t)
    return t


def _build_population_and_replay(
    *,
    repo_root: Path,
    kabu: Path,
    price_idx: Mapping[tuple[str, str], list],
    sector_map: Mapping[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return (regime_population_light, replay_pool_enriched, meta)."""
    all_dynamic40 = _load_dynamic40_records(kabu)
    raw_records = [r for r in all_dynamic40 if _is_actionable_record(r)]
    enriched_canon = _enrich_candidates(_load_candidate_stream(repo_root), kabu=kabu)
    canon_by_key = {_position_key(t): dict(t) for t in enriched_canon}
    canon_keys = set(canon_by_key)

    inject_raw = [
        r
        for r in raw_records
        if _position_key(r) not in canon_keys and _should_inject_for_replay(r)
    ]
    print(
        f"phase463 population: dynamic40_actionable={len(raw_records)} "
        f"canon={len(enriched_canon)} inject={len(inject_raw)}",
        flush=True,
    )

    regime_population = [_enrich_light(r, canon_by_key) for r in raw_records]

    inject_enriched = _enrich_population(inject_raw, price_idx=price_idx, sector_map=sector_map)
    replay_pool = list(enriched_canon) + inject_enriched
    replay_pool.sort(
        key=lambda r: (
            str(r.get("day") or ""),
            str(r.get("entry_time") or ""),
            str(r.get("symbol") or ""),
        )
    )
    meta = {
        "dynamic40_total": len(all_dynamic40),
        "dynamic40_actionable": len(raw_records),
        "canonical_count": len(enriched_canon),
        "inject_count": len(inject_raw),
        "replay_pool_count": len(replay_pool),
    }
    return regime_population, replay_pool, meta


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


def _enrich_population(
    rows: Sequence[Mapping[str, Any]],
    *,
    price_idx: Mapping[tuple[str, str], list],
    sector_map: Mapping[str, str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    total = len(rows)
    for i, raw in enumerate(rows, start=1):
        if total >= 5000 and i % 5000 == 0:
            print(f"phase463 enrich {i}/{total}", flush=True)
        t = _map_runtime_fields(dict(raw))
        t.setdefault("day", raw.get("day"))
        px = _float(t.get("entry_price")) or _float(t.get("current_price"))
        if px:
            t["entry_price"] = px
        t["day_high_distance_pct"] = _optional_float(t.get("day_high_distance_pct")) or _optional_float(
            t.get("entry_near_day_high_pct")
        )
        t.update(enrich_trade_phase456_features(t, price_idx=price_idx, sector_map=sector_map))
        t.update(enrich_trade_phase456c_features(t, price_idx=price_idx))
        t["board_bucket"] = _board_bucket(t)
        t["momentum_score"] = _momentum_score(t)
        t["vwap_dev_pct"] = _vwap_dev(t)
        t["vwap_above_ratio"] = _vwap_above_ratio(t)
        out.append(t)
    return out


def _pass_a0_core(t: Mapping[str, Any]) -> bool:
    if not _passes_baseline_mid_high(t):
        return False
    if guard_high_drift(t):
        return False
    if _weak_shape_block(t):
        return False
    return True


def pass_a0_baseline(t: Mapping[str, Any]) -> bool:
    if not _pass_a0_core(t):
        return False
    if phase364_blocked_only(t):
        return False
    return True


def pass_a1_strict_pullback(t: Mapping[str, Any]) -> bool:
    if not _pass_a0_core(t):
        return False
    if phase364_blocked_only(t):
        return False
    if (_vwap_above_ratio(t) or 0) < 0.5:
        return False
    if t.get("vwap_failed_reclaim_flag"):
        return False
    return True


def pass_a2_near_high_exception(t: Mapping[str, Any]) -> bool:
    if not _pass_a0_core(t):
        return False
    if phase364_blocked_only(t) and (_rise(t, 5) or -1e18) <= 0:
        return False
    return True


def pass_a3_vwap_stable_pullback(t: Mapping[str, Any]) -> bool:
    if not pass_a0_baseline(t):
        return False
    return (_vwap_above_ratio(t) or 0) >= 0.5


def pass_b1_trend_r15_r30(t: Mapping[str, Any]) -> bool:
    return (
        _board_mid_high(t)
        and (_rise(t, 15) or -1e18) > 0
        and (_rise(t, 30) or -1e18) > 0
    )


def pass_b2_trend_high_update(t: Mapping[str, Any]) -> bool:
    return _board_mid_high(t) and (_float(t.get("high_update_count_30m")) or 0) >= 2


def pass_b3_trend_vwap_stable(t: Mapping[str, Any]) -> bool:
    return (
        _board_mid_high(t)
        and (_vwap_above_ratio(t) or 0) >= 0.7
        and (_float(t.get("consecutive_above_ticks")) or 0) >= 20
    )


def pass_b4_trend_composite(t: Mapping[str, Any]) -> bool:
    if not _board_mid_high(t):
        return False
    r30 = (_rise(t, 30) or -1e18) > 0
    if not r30:
        return False
    return (_vwap_above_ratio(t) or 0) >= 0.7 or (_float(t.get("high_update_count_30m")) or 0) >= 2


def _make_or(*fns: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
    def combined(t: Mapping[str, Any]) -> bool:
        return any(fn(t) for fn in fns)

    return combined


VARIANT_GROUPS: dict[str, tuple[str, Callable[[Mapping[str, Any]], bool]]] = {
    "A0_baseline_pullback": ("A", pass_a0_baseline),
    "A1_strict_pullback": ("A", pass_a1_strict_pullback),
    "A2_pullback_near_high_exception": ("A", pass_a2_near_high_exception),
    "A3_pullback_vwap_stable": ("A", pass_a3_vwap_stable_pullback),
    "B1_trend_r15_r30": ("B", pass_b1_trend_r15_r30),
    "B2_trend_high_update": ("B", pass_b2_trend_high_update),
    "B3_trend_vwap_stable": ("B", pass_b3_trend_vwap_stable),
    "B4_trend_composite": ("B", pass_b4_trend_composite),
}


def _entry_block(pass_fn: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
    def block(trade: Mapping[str, Any]) -> bool:
        return not pass_fn(trade)

    return block


def _concentration(trade_log: Sequence[Mapping[str, Any]]) -> tuple[float, float]:
    if not trade_log:
        return 0.0, 0.0
    total = sum(abs(_float(r.get("pnl_yen")) or 0) for r in trade_log)
    if total <= 0:
        return 0.0, 0.0
    by_day: Counter[str] = Counter()
    by_sym: Counter[str] = Counter()
    for r in trade_log:
        tr = r.get("trade") or {}
        day = str(tr.get("day") or "")[:8]
        sym = str(tr.get("symbol") or "")
        pnl = abs(_float(r.get("pnl_yen")) or 0)
        by_day[day] += pnl
        by_sym[sym] += pnl
    return round(max(by_day.values()) / total, 4), round(max(by_sym.values()) / total, 4)


def _variant_metrics(
    state: Any,
    *,
    variant: str,
    group: str,
    baseline: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym_pnl = _symbol_pnl_from_log(state.trade_log)
    accepted_syms = {str(r.get("symbol") or "") for r in state.trade_log}
    top_day, top_sym = _concentration(state.trade_log)
    row = {
        "variant": variant,
        "group": group,
        "accepted_count": state.accepted_trade_count,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "daily_pnl_618": round(float(state.daily_pnls.get(DAY_618, 0.0)), 2),
        "daily_pnl_619": round(float(state.daily_pnls.get(DAY_619, 0.0)), 2),
        "symbol_pnl_6976": sym_pnl.get("6976", 0.0),
        "symbol_pnl_4062": sym_pnl.get("4062", 0.0),
        "top_day_share": top_day,
        "top_symbol_share": top_sym,
        **{f"captured_{s.replace('.T', '')}": s in accepted_syms for s in UPTREND_CAPTURE},
    }
    if baseline:
        row["delta_pnl_vs_a0"] = round(float(row["total_pnl_yen"]) - float(baseline["total_pnl_yen"]), 2)
        row["delta_pf_vs_a0"] = round(float(row["profit_factor"] or 0) - float(baseline["profit_factor"] or 0), 4)
        row["delta_maxdd_vs_a0"] = round(float(row["max_drawdown_yen"]) - float(baseline["max_drawdown_yen"]), 2)
        row["delta_daily_pnl_618"] = round(float(row["daily_pnl_618"]) - float(baseline["daily_pnl_618"]), 2)
        row["delta_daily_pnl_619"] = round(float(row["daily_pnl_619"]) - float(baseline["daily_pnl_619"]), 2)
        row["delta_symbol_pnl_6976"] = round(float(row["symbol_pnl_6976"]) - float(baseline["symbol_pnl_6976"]), 2)
        row["delta_symbol_pnl_4062"] = round(float(row["symbol_pnl_4062"]) - float(baseline["symbol_pnl_4062"]), 2)
    else:
        row["delta_pnl_vs_a0"] = 0.0
        row["delta_pf_vs_a0"] = 0.0
        row["delta_maxdd_vs_a0"] = 0.0
        row["delta_daily_pnl_618"] = 0.0
        row["delta_daily_pnl_619"] = 0.0
        row["delta_symbol_pnl_6976"] = 0.0
        row["delta_symbol_pnl_4062"] = 0.0
    return row


def _regime_label(t: Mapping[str, Any]) -> str:
    if (_rise(t, 30) or -1e18) > 0 and (_vwap_above_ratio(t) or 0) >= 0.7:
        return "Trend-like"
    if (_float(t.get("high_update_count_30m")) or 0) >= 2:
        return "High-update-like"
    if _passes_baseline_mid_high(t):
        return "Pullback-like"
    if (_vwap_above_ratio(t) or 0) >= 0.7 and (_float(t.get("consecutive_above_ticks")) or 0) >= 20:
        return "VWAP-stable-like"
    return "Other"


def _would_pnl(
    t: Mapping[str, Any],
    np_shadows: Mapping[str, Any],
    price_idx: Mapping[tuple[str, str], list],
) -> float:
    key = _position_key(t)
    sh = np_shadows.get(key)
    if sh and sh.eval_ok:
        return float(sh.shadow_pnl_yen)
    return _close_proxy_pnl(t, price_idx)


def _run_variant_replay(
    variant: str,
    group: str,
    pass_fn: Callable[[Mapping[str, Any]], bool],
    *,
    candidates: Sequence[Mapping[str, Any]],
    np_shadows: Mapping[str, Any],
    baseline: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    st = simulate_capacity_replay(
        candidates,
        np_shadows,
        mode=f"{REPLAY_MODE}_{variant}",
        entry_block_fn=_entry_block(pass_fn),
        baseline_accepted_keys=set(),
    )
    return _variant_metrics(st, variant=variant, group=group, baseline=baseline)


def _cache_dir(reports: Path) -> Path:
    d = reports / ".phase463_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save_cache(
    reports: Path,
    *,
    replay_pool: list[dict],
    regime_population: list[dict],
    np_shadows: dict,
    meta: dict[str, Any],
) -> Path:
    cache = _cache_dir(reports)
    payload = {
        "version": _CACHE_VERSION,
        "replay_pool": replay_pool,
        "regime_population": regime_population,
        "np_shadows": np_shadows,
        "meta": meta,
    }
    path = cache / "population.pkl"
    with path.open("wb") as fh:
        pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
    return path


def _load_cache(reports: Path) -> Optional[tuple[list[dict], list[dict], dict, dict[str, Any]]]:
    path = _cache_dir(reports) / "population.pkl"
    if not path.is_file():
        return None
    with path.open("rb") as fh:
        payload = pickle.load(fh)
    if payload.get("version") != _CACHE_VERSION:
        return None
    return (
        payload["replay_pool"],
        payload["regime_population"],
        payload["np_shadows"],
        payload.get("meta") or {},
    )


def _parallel_worker(args: tuple[str, str, str]) -> dict[str, Any]:
    import sys
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[2]
    kabu = _Path(__file__).resolve().parents[1]
    for p in (kabu / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    variant, cache_path, baseline_variant = args
    with Path(cache_path).open("rb") as fh:
        payload = pickle.load(fh)
    candidates = payload["replay_pool"]
    np_shadows = payload["np_shadows"]
    baseline = payload.get("baselines", {}).get(baseline_variant)
    group, pass_fn = VARIANT_GROUPS[variant]
    return _run_variant_replay(
        variant,
        group,
        pass_fn,
        candidates=candidates,
        np_shadows=np_shadows,
        baseline=baseline,
    )


def _verdict(
    *,
    a0: Mapping[str, Any],
    best_pullback: Mapping[str, Any],
    best_trend: Mapping[str, Any],
    best_hybrid: Mapping[str, Any],
    trend_independent: bool,
) -> str:
    p_a0 = float(a0.get("total_pnl_yen") or 0)
    p_h = float(best_hybrid.get("total_pnl_yen") or 0)
    p_t = float(best_trend.get("total_pnl_yen") or 0)
    if trend_independent and p_t > 0 and p_t > p_a0 * 0.3:
        if p_h > p_a0 + 5000:
            return "hybrid_candidate"
        return "trend_independent_edge"
    if p_h > p_a0 + 5000:
        return "hybrid_candidate"
    if float(best_pullback.get("total_pnl_yen") or 0) >= p_t and float(best_pullback.get("total_pnl_yen") or 0) >= p_h:
        return "pullback_dominant"
    if p_t <= 0 and p_h <= p_a0:
        return "no_trend_edge"
    if p_h > p_a0:
        return "regime_split_candidate"
    return "pullback_dominant"


def run_phase463_tournament(
    *,
    repo_root: Path,
    parallel: bool = False,
    max_workers: int = 4,
) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    sector_map = read_jpx_sector_map(kabu)

    cached = _load_cache(reports)
    if cached:
        replay_pool, regime_population, np_shadows, pop_meta = cached
        print(f"phase463 cache hit replay={len(replay_pool)} regime={len(regime_population)}", flush=True)
        np_shadows = _fill_close_proxy_shadows(replay_pool, np_shadows, price_idx=price_idx)
        replay_pool = _filter_replay_pool(replay_pool, np_shadows)
    else:
        regime_population, replay_pool, pop_meta = _build_population_and_replay(
            repo_root=repo_root,
            kabu=kabu,
            price_idx=price_idx,
            sector_map=sector_map,
        )
        print(f"phase463 precompute NP shadows on {len(replay_pool)} trades...", flush=True)
        np_shadows = _precompute_np_shadows(replay_pool, kabu=kabu, np_policy=BEST_NP_POLICY)
        np_shadows = _fill_close_proxy_shadows(replay_pool, np_shadows, price_idx=price_idx)
        replay_pool = _filter_replay_pool(replay_pool, np_shadows)
        pop_meta["replay_pool_count"] = len(replay_pool)
        _save_cache(
            reports,
            replay_pool=replay_pool,
            regime_population=regime_population,
            np_shadows=np_shadows,
            meta=pop_meta,
        )

    population = replay_pool

    # Regime label analysis (Part D)
    baseline_state = simulate_capacity_replay(
        population,
        np_shadows,
        mode=f"{REPLAY_MODE}_a0_regime",
        entry_block_fn=_entry_block(pass_a0_baseline),
        baseline_accepted_keys=set(),
    )
    replay_pnl_by_key = {
        _position_key(dict(r.get("trade") or {})): _float(r.get("pnl_yen")) or 0.0 for r in baseline_state.trade_log
    }
    regime_buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in regime_population:
        regime_buckets[_regime_label(t)].append(t)

    regime_rows: list[dict[str, Any]] = []
    for label, grp in sorted(regime_buckets.items()):
        would_pnls = [_would_pnl(t, np_shadows, price_idx) for t in grp]
        replay_pnls = [replay_pnl_by_key.get(_position_key(t), 0.0) for t in grp]
        accepted_n = sum(1 for t in grp if pass_a0_baseline(t))
        regime_rows.append(
            {
                "regime_label": label,
                "count": len(grp),
                "accepted_count": accepted_n,
                "would_pnl_yen": round(sum(would_pnls), 2),
                "replay_pnl_yen": round(sum(replay_pnls), 2),
                "profit_factor": _pf(replay_pnls) if any(replay_pnls) else None,
                "stop_rate": None,
            }
        )

    # Variant replay — A0 first, then A/B rest, then hybrids
    tournament_rows: list[dict[str, Any]] = []
    a0_metrics = _run_variant_replay(
        "A0_baseline_pullback",
        "A",
        pass_a0_baseline,
        candidates=population,
        np_shadows=np_shadows,
        baseline=None,
    )
    tournament_rows.append(a0_metrics)

    rest = [(vid, grp, fn) for vid, (grp, fn) in VARIANT_GROUPS.items() if vid != "A0_baseline_pullback"]
    if parallel and len(rest) > 1:
        cache_path = _save_cache(
            reports,
            replay_pool=population,
            regime_population=regime_population,
            np_shadows=np_shadows,
            meta=pop_meta,
        )
        with cache_path.open("rb") as fh:
            payload = pickle.load(fh)
        payload["baselines"] = {"A0_baseline_pullback": a0_metrics}
        with cache_path.open("wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
        args = [(vid, str(cache_path), "A0_baseline_pullback") for vid, _, _ in rest]
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            for fut in as_completed(ex.submit(_parallel_worker, a) for a in args):
                tournament_rows.append(fut.result())
    else:
        for vid, grp, fn in rest:
            tournament_rows.append(
                _run_variant_replay(
                    vid,
                    grp,
                    fn,
                    candidates=population,
                    np_shadows=np_shadows,
                    baseline=a0_metrics,
                )
            )

    trend_rows = [r for r in tournament_rows if r["group"] == "B"]
    best_trend_id = max(trend_rows, key=lambda r: float(r.get("total_pnl_yen") or 0))["variant"]
    best_trend_fn = VARIANT_GROUPS[best_trend_id][1]

    hybrid_specs = {
        "C1_a0_or_b1": ("C", _make_or(pass_a0_baseline, pass_b1_trend_r15_r30)),
        "C2_a0_or_b2": ("C", _make_or(pass_a0_baseline, pass_b2_trend_high_update)),
        "C3_a0_or_b3": ("C", _make_or(pass_a0_baseline, pass_b3_trend_vwap_stable)),
        "C4_a0_or_best_trend": ("C", _make_or(pass_a0_baseline, best_trend_fn)),
        "C5_a2_or_best_trend": ("C", _make_or(pass_a2_near_high_exception, best_trend_fn)),
    }
    for vid, (grp, fn) in hybrid_specs.items():
        tournament_rows.append(
            _run_variant_replay(
                vid,
                grp,
                fn,
                candidates=population,
                np_shadows=np_shadows,
                baseline=a0_metrics,
            )
        )

    tournament_rows.sort(key=lambda r: float(r.get("total_pnl_yen") or 0), reverse=True)

    symbol_rows: list[dict[str, Any]] = []
    for row in tournament_rows:
        for sym in TARGET_SYMBOLS:
            code = sym.replace(".T", "")
            symbol_rows.append(
                {
                    "variant": row["variant"],
                    "symbol": sym,
                    "captured": row.get(f"captured_{code}"),
                    "symbol_pnl_yen": row.get(f"symbol_pnl_{code}", 0.0),
                    "delta_symbol_pnl_vs_a0": row.get(f"delta_symbol_pnl_{code}")
                    if code in ("6976", "4062")
                    else None,
                }
            )

    best_pullback = max((r for r in tournament_rows if r["group"] == "A"), key=lambda r: float(r["total_pnl_yen"] or 0))
    best_trend = max((r for r in tournament_rows if r["group"] == "B"), key=lambda r: float(r["total_pnl_yen"] or 0))
    best_hybrid = max((r for r in tournament_rows if r["group"] == "C"), key=lambda r: float(r["total_pnl_yen"] or 0))
    trend_independent = float(best_trend["total_pnl_yen"] or 0) > 0 and float(best_trend["total_pnl_yen"] or 0) > float(
        a0_metrics["total_pnl_yen"] or 0
    ) * 0.5

    capture_variants = [
        r["variant"]
        for r in tournament_rows
        if all(r.get(f"captured_{s.replace('.T','')}") for s in UPTREND_CAPTURE)
    ]
    safe_6976 = [r["variant"] for r in tournament_rows if float(r.get("delta_symbol_pnl_6976") or 0) >= -5000]
    improve_4062 = [r["variant"] for r in tournament_rows if float(r.get("delta_symbol_pnl_4062") or 0) > 0]

    pnl_rank = [r["variant"] for r in sorted(tournament_rows, key=lambda r: float(r["total_pnl_yen"] or 0), reverse=True)]
    pf_rank = [r["variant"] for r in sorted(tournament_rows, key=lambda r: float(r["profit_factor"] or 0), reverse=True)]
    dd_rank = [r["variant"] for r in sorted(tournament_rows, key=lambda r: float(r["max_drawdown_yen"] or 0))]

    overfit_risk = any(float(r.get("top_day_share") or 0) > 0.5 for r in tournament_rows) or any(
        float(r.get("top_symbol_share") or 0) > 0.5 for r in tournament_rows
    )

    verdict = _verdict(
        a0=a0_metrics,
        best_pullback=best_pullback,
        best_trend=best_trend,
        best_hybrid=best_hybrid,
        trend_independent=trend_independent,
    )

    mandatory = {
        "1_best_pullback_variant": best_pullback["variant"],
        "2_best_trend_variant": best_trend["variant"],
        "3_best_hybrid_variant": best_hybrid["variant"],
        "4_trend_independent_edge": trend_independent,
        "5_pullback_dominant": float(best_pullback["total_pnl_yen"] or 0) >= float(best_hybrid["total_pnl_yen"] or 0),
        "6_hybrid_beats_pullback": float(best_hybrid["total_pnl_yen"] or 0) > float(a0_metrics["total_pnl_yen"] or 0),
        "7_uptrend_capture_variants": capture_variants,
        "8_safe_6976_variants": safe_6976,
        "9_improve_4062_variants": improve_4062,
        "10_pnl_rank": pnl_rank,
        "11_pf_rank": pf_rank,
        "12_maxdd_rank": dd_rank,
        "13_overfit_risk": overfit_risk,
        "14_runtime_candidate": verdict in ("hybrid_candidate", "regime_split_candidate"),
        "15_next_actions": [
            f"Shadow-test {best_hybrid['variant']} if hybrid beats A0",
            f"Near-high exception path: {best_pullback['variant']}",
            "Walk-forward on Trend variants with independent edge only",
        ],
        "verdict": verdict,
        "population_count": pop_meta.get("dynamic40_actionable", len(regime_population)),
        "dynamic40_total": pop_meta.get("dynamic40_total"),
        "replay_pool_count": pop_meta.get("replay_pool_count", len(population)),
        "best_trend_id_for_hybrid": best_trend_id,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "mandatory_answers": mandatory,
        "verdict": verdict,
        "part_d_regime": regime_rows,
        "_tournament_rows": tournament_rows,
        "_regime_rows": regime_rows,
        "_symbol_rows": symbol_rows,
    }


@dataclass
class Phase463Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase463_tournament(
            repo_root=self.repo_root,
            parallel=self.parallel,
            max_workers=self.max_workers,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "tournament": reports / "phase463_trend_pullback_population_tournament.csv",
            "regime": reports / "phase463_regime_label_analysis.csv",
            "symbols": reports / "phase463_symbol_capture_analysis.csv",
            "summary": reports / "phase463_summary.json",
        }
        _write_csv(paths["tournament"], TOURNAMENT_FIELDS, list(result.get("_tournament_rows") or []))
        _write_csv(paths["regime"], REGIME_FIELDS, list(result.get("_regime_rows") or []))
        _write_csv(paths["symbols"], SYMBOL_CAPTURE_FIELDS, list(result.get("_symbol_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase463_trend_pullback_population_tournament.md"
        m = result.get("mandatory_answers") or {}
        lines = [
            "# Phase463 — Trend/Pullback Population Tournament",
            "",
            f"Generated: {result.get('generated_at')}",
            f"Period: {result.get('period_start')}..{result.get('period_end')}",
            f"Population: **{m.get('population_count')}** candidates",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
            "## Tournament leaderboard (PnL)",
            "",
            "| rank | variant | PnL | PF | maxDD | Δvs A0 |",
            "|---:|---|---:|---:|---:|---:|",
        ]
        for i, vid in enumerate(m.get("10_pnl_rank") or [], start=1):
            row = next(r for r in (result.get("_tournament_rows") or []) if r["variant"] == vid)
            lines.append(
                f"| {i} | {vid} | {row.get('total_pnl_yen')} | {row.get('profit_factor')} "
                f"| {row.get('max_drawdown_yen')} | {row.get('delta_pnl_vs_a0')} |"
            )
        lines.extend(
            [
                "",
                "## Mandatory answers",
                "",
                f"1. Best Pullback: **{m.get('1_best_pullback_variant')}**",
                f"2. Best Trend: **{m.get('2_best_trend_variant')}**",
                f"3. Best Hybrid: **{m.get('3_best_hybrid_variant')}**",
                f"4. Trend independent: **{m.get('4_trend_independent_edge')}**",
                f"5. Pullback dominant: **{m.get('5_pullback_dominant')}**",
                f"6. Hybrid beats Pullback: **{m.get('6_hybrid_beats_pullback')}**",
                f"7. Uptrend capture: **{m.get('7_uptrend_capture_variants')}**",
                f"8. Safe 6976: **{m.get('8_safe_6976_variants')}**",
                f"9. Improve 4062: **{m.get('9_improve_4062_variants')}**",
                f"13. Overfit risk: **{m.get('13_overfit_risk')}**",
                f"14. Runtime candidate: **{m.get('14_runtime_candidate')}**",
                f"15. Next: {m.get('15_next_actions')}",
                "",
            ]
        )
        report.write_text("\n".join(lines), encoding="utf-8")
        paths["report"] = report
        return paths
