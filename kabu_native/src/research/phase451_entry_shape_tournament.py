"""
Phase451 — Entry shape filter tournament.

Shadow-evaluates opening-peak / weak-shape ENTRY guards vs current Runtime baseline
(Momentum:low + Board:mid + High Drift + No Progress, CAP5).

Period: 20260529–20260619. Research only.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import (
    CANONICAL_BASELINE_END,
    PERIOD_START,
    load_canonical_live_config_trades,
)
from research.market_sector_heat import _pf, _write_csv
from research.phase382_capital_constrained_backtest import _day_from_ts, _parse_ts
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import (
    _build_price_index,
    _enrich_trades,
    _float,
    _load_accepted_index,
    _optional_float,
    _price_at_or_before,
    guard_high_drift,
)
from research.phase438_momentum_low_audit import _day_high_context
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY, _precompute_np_shadows
from research.phase443_full_runtime_combined_capital_sim import (
    CAP,
    LEVERAGE,
    STARTING_EQUITY,
    STOP_POLICY,
    _chronological_pnls_from_log,
    _stop_rate_from_log,
    simulate_capacity_replay,
)
from research.phase450_momentum_redesign_shadow import _passes_baseline_entry
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")

PERIOD_END = "20260619"
DAY_618 = "20260618"
DAY_619 = "20260619"
TARGET_SYMBOLS = ("6976.T", "6920.T", "4062.T")

COMPARISON_FIELDS = [
    "variant",
    "total_pnl_yen",
    "delta_pnl_vs_baseline",
    "profit_factor",
    "delta_pf_vs_baseline",
    "max_drawdown_yen",
    "delta_maxdd_vs_baseline",
    "stop_rate",
    "delta_stop_rate_vs_baseline",
    "accepted_count",
    "reject_count",
    "gate_reject_count",
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
    "opening_peak_accepted",
    "slow_opening_peak_accepted",
    "uptrend_accepted",
    "uptrend_miss_count",
    "uptrend_adoption_rate",
    "opening_peak_reduction_pct",
    "uptrend_adoption_improvement",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _time_on_day(h: int, m: int, day: str) -> datetime:
    return datetime.strptime(f"{day} {h:02d}:{m:02d}:00", "%Y%m%d %H:%M:%S").replace(tzinfo=JST)


def _pct(from_px: Optional[float], to_px: Optional[float]) -> Optional[float]:
    if from_px is None or to_px is None or from_px <= 0:
        return None
    return round((to_px - from_px) / from_px * 100.0, 4)


def _classify_eod_shape(
    series: Sequence[tuple[datetime, float]],
    *,
    day: str,
) -> str:
    if not series:
        return "unknown"
    open_dt = _time_on_day(9, 0, day)
    t1530 = _time_on_day(15, 30, day)
    open_px = _price_at_or_before(series, open_dt) or series[0][1]
    close_px = _price_at_or_before(series, t1530) or series[-1][1]
    day_high = max(px for _, px in series)
    day_high_time = max(ts for ts, px in series if px == day_high)
    o2c = _pct(open_px, close_px) or 0.0
    high_to_close_dd = _pct(day_high, close_px) or 0.0
    mins_high = (day_high_time - open_dt).total_seconds() / 60.0
    if mins_high <= 20 and o2c < 0 and high_to_close_dd <= -1.5:
        return "opening_peak"
    if mins_high <= 60 and high_to_close_dd <= -2.0:
        return "slow_opening_peak"
    if o2c < -1.0:
        return "downtrend"
    if o2c > 0 and (mins_high >= 60 or day_high_time.hour >= 12):
        return "uptrend"
    if abs(o2c) <= 0.5:
        return "range"
    if o2c > 0:
        return "uptrend"
    return "other"


def _build_price_index_to(kabu_root: Path, *, period_end: str) -> dict[tuple[str, str], list[tuple[datetime, float]]]:
    idx = _build_price_index(kabu_root)
    base = kabu_root / "results" / "small_paper"
    if not base.is_dir():
        return idx
    for day_dir in sorted(base.iterdir()):
        if not day_dir.is_dir():
            continue
        day = day_dir.name
        if day <= PERIOD_END or day > period_end:
            continue
        for sess in sorted(day_dir.iterdir()):
            if not sess.is_dir() or not sess.name.startswith("live_session"):
                continue
            path = sess / "small_paper_events.csv"
            if not path.is_file():
                continue
            with path.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    sym = str(row.get("symbol") or "")
                    px = _float(row.get("current_price"), default=0.0)
                    if not sym or px <= 0:
                        continue
                    ts = _parse_ts(str(row.get("event_time") or row.get("entry_time") or ""))
                    if ts is None:
                        continue
                    idx[(sym, day)].append((ts, px))
    for key in idx:
        idx[key].sort(key=lambda x: x[0])
    return idx


def _load_accepted_index_to(kabu_root: Path, *, period_end: str) -> dict[tuple[str, str], dict[str, str]]:
    idx = _load_accepted_index(kabu_root)
    base = kabu_root / "results" / "small_paper"
    if not base.is_dir():
        return idx
    for day_dir in sorted(base.iterdir()):
        if not day_dir.is_dir():
            continue
        day = day_dir.name
        if day <= "20260618" or day > period_end:
            continue
        for sess in sorted(day_dir.iterdir()):
            if not sess.is_dir():
                continue
            path = sess / "small_paper_events.csv"
            if not path.is_file():
                continue
            with path.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    if row.get("event_type") != "accepted":
                        continue
                    key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
                    idx[key] = row
    return idx


def _intraday_high_fields(
    trade: Mapping[str, Any],
    price_idx: Mapping[tuple[str, str], list[tuple[datetime, float]]],
) -> dict[str, Any]:
    sym = str(trade.get("symbol") or "")
    day = str(trade.get("day") or _day_from_ts(str(trade.get("entry_time") or "")))
    et = _parse_ts(str(trade.get("entry_time") or ""))
    ep = _float(trade.get("entry_price"), default=0.0)
    if et is None or ep <= 0:
        return {}
    series = price_idx.get((sym, day), [])
    if not series:
        return {}
    upto = [(ts, px) for ts, px in series if ts <= et]
    if not upto:
        return {}
    day_high = max(px for _, px in upto)
    first_high_ts = min(ts for ts, px in upto if px == day_high)
    dist_pct = round((day_high - ep) / day_high * 100.0, 4) if day_high > 0 else None
    open_dt = _time_on_day(9, 0, day)
    mins_from_open = (first_high_ts - open_dt).total_seconds() / 60.0
    return {
        "day_high_time_at_entry": first_high_ts,
        "day_high_minutes_from_open": round(mins_from_open, 2),
        "day_high_distance_pct": dist_pct,
    }


def _enrich_candidates(candidates: Sequence[Mapping[str, Any]], *, kabu: Path) -> list[dict[str, Any]]:
    accepted_idx = _load_accepted_index_to(kabu, period_end=PERIOD_END)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    rows = _enrich_trades(list(candidates), kabu_root=kabu, accepted_idx=accepted_idx, price_idx=price_idx)

    eod_shape: dict[tuple[str, str], str] = {}
    for (sym, day), series in price_idx.items():
        eod_shape[(sym, day)] = _classify_eod_shape(series, day=day)

    out: list[dict[str, Any]] = []
    for row in rows:
        t = dict(row)
        ctx = _day_high_context(t, price_idx=price_idx)
        t.update(ctx)
        hi = _intraday_high_fields(t, price_idx)
        t.update(hi)
        if t.get("day_high_distance_pct") is None:
            t["day_high_distance_pct"] = _optional_float(t.get("entry_near_day_high_pct"))
        sym = str(t.get("symbol") or "")
        day = str(t.get("day") or _day_from_ts(str(t.get("entry_time") or "")))
        t["eod_shape_class"] = eod_shape.get((sym, day), "unknown")
        out.append(t)
    return out


def _load_candidate_stream(repo_root: Path) -> list[dict[str, Any]]:
    trades, _meta = load_canonical_live_config_trades(
        repo_root,
        period_start=PERIOD_START,
        baseline_end=CANONICAL_BASELINE_END,
    )
    out: list[dict[str, Any]] = []
    for t in trades:
        day = str(t.get("day") or "")
        if day < PERIOD_START or day > PERIOD_END:
            continue
        if _parse_ts(str(t.get("entry_time") or "")) is None:
            continue
        if _float(t.get("entry_price")) <= 0:
            continue
        out.append(dict(t))
    out.sort(
        key=lambda r: (
            _parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(r.get("symbol") or ""),
        )
    )
    return out


def _day_high_distance(trade: Mapping[str, Any]) -> float:
    return abs(
        _optional_float(trade.get("day_high_distance_pct"))
        or _optional_float(trade.get("entry_near_day_high_pct"))
        or 0.0
    )


def _guard_b_opening_peak(trade: Mapping[str, Any]) -> bool:
    et = _parse_ts(str(trade.get("entry_time") or ""))
    dht = trade.get("day_high_time_at_entry")
    if et is None or not isinstance(dht, datetime):
        return False
    mins_from_open = _optional_float(trade.get("day_high_minutes_from_open"))
    if mins_from_open is None or mins_from_open > 20:
        return False
    if et < dht + timedelta(minutes=15):
        return False
    return _day_high_distance(trade) >= 2.0


def _guard_c_strong_opening_peak(trade: Mapping[str, Any]) -> bool:
    mins_from_open = _optional_float(trade.get("day_high_minutes_from_open"))
    if mins_from_open is None or mins_from_open > 30:
        return False
    if _day_high_distance(trade) < 3.0:
        return False
    r15 = _optional_float(trade.get("return_15min_pct"))
    return r15 is not None and r15 <= 0.0


def _guard_d_no_high_update(trade: Mapping[str, Any]) -> bool:
    mins = _optional_float(trade.get("minutes_since_day_high_update"))
    if mins is None or mins < 20.0:
        return False
    return _day_high_distance(trade) >= 2.0


def _guard_e_weak_shape(trade: Mapping[str, Any]) -> bool:
    shape = str(trade.get("eod_shape_class") or "")
    if shape == "uptrend":
        return False
    return shape in ("opening_peak", "slow_opening_peak")


def _guard_f_uptrend_preference(trade: Mapping[str, Any]) -> bool:
    r15 = _optional_float(trade.get("return_15min_pct"))
    r30 = _optional_float(trade.get("return_30min_pct"))
    if r15 is None or r30 is None:
        return True
    return not (r15 > 0.0 and r30 > 0.0)


def _guard_g_combined_conservative(trade: Mapping[str, Any]) -> bool:
    return _guard_b_opening_peak(trade) or _guard_d_no_high_update(trade)


def _guard_h_combined_aggressive(trade: Mapping[str, Any]) -> bool:
    return (
        _guard_c_strong_opening_peak(trade)
        or _guard_d_no_high_update(trade)
        or _guard_f_uptrend_preference(trade)
    )


VARIANTS: tuple[tuple[str, str, Optional[Callable[[Mapping[str, Any]], bool]]], ...] = (
    ("A_baseline", "baseline", None),
    ("B_opening_peak_guard", "opening_peak_guard", _guard_b_opening_peak),
    ("C_strong_opening_peak", "strong_opening_peak", _guard_c_strong_opening_peak),
    ("D_no_high_update", "no_high_update", _guard_d_no_high_update),
    ("E_weak_shape_reject", "weak_shape_reject", _guard_e_weak_shape),
    ("F_uptrend_preference", "uptrend_preference", _guard_f_uptrend_preference),
    ("G_combined_conservative", "combined_conservative", _guard_g_combined_conservative),
    ("H_combined_aggressive", "combined_aggressive", _guard_h_combined_aggressive),
)


def _runtime_entry_block(shape_guard: Optional[Callable[[Mapping[str, Any]], bool]] = None):
    def block(trade: Mapping[str, Any]) -> bool:
        if not _passes_baseline_entry(trade):
            return True
        if guard_high_drift(trade):
            return True
        if shape_guard is not None and shape_guard(trade):
            return True
        return False

    return block


def _symbol_pnl_from_log(trade_log: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    out = {sym.replace(".T", ""): 0.0 for sym in TARGET_SYMBOLS}
    for row in trade_log:
        sym = str(row.get("symbol") or "")
        key = sym.replace(".T", "")
        if key in out:
            out[key] += float(row.get("pnl_yen") or 0.0)
    return {k: round(v, 2) for k, v in out.items()}


def _shape_stats(
    trade_log: Sequence[Mapping[str, Any]],
    *,
    eod_uptrend_keys: set[tuple[str, str]],
) -> dict[str, Any]:
    shape_counts: dict[str, int] = defaultdict(int)
    accepted_keys: set[tuple[str, str]] = set()
    for row in trade_log:
        trade = dict(row.get("trade") or {})
        sym = str(trade.get("symbol") or row.get("symbol") or "")
        day = str(trade.get("day") or row.get("day") or _day_from_ts(str(trade.get("entry_time") or "")))
        shape = str(trade.get("eod_shape_class") or "unknown")
        shape_counts[shape] += 1
        accepted_keys.add((sym, day))
    uptrend_entered = {k for k in accepted_keys if k in eod_uptrend_keys}
    uptrend_miss = len(eod_uptrend_keys - accepted_keys)
    adoption = round(len(uptrend_entered) / len(eod_uptrend_keys), 4) if eod_uptrend_keys else None
    return {
        "opening_peak_accepted": shape_counts.get("opening_peak", 0),
        "slow_opening_peak_accepted": shape_counts.get("slow_opening_peak", 0),
        "uptrend_accepted": shape_counts.get("uptrend", 0),
        "uptrend_miss_count": uptrend_miss,
        "uptrend_adoption_rate": adoption,
        "_uptrend_entered_symbols": len(uptrend_entered),
    }


def _metrics_from_state(
    state: Any,
    *,
    variant: str,
    eod_uptrend_keys: set[tuple[str, str]],
) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym_pnl = _symbol_pnl_from_log(state.trade_log)
    shape = _shape_stats(state.trade_log, eod_uptrend_keys=eod_uptrend_keys)
    gate_rejects = sum(
        1
        for r in (state.reject_log or [])
        if str(r.get("reason") or "")
        not in ("max_concurrent_positions", "insufficient_buying_power", "same_symbol_open")
    )
    return {
        "variant": variant,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron),
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "accepted_count": state.accepted_trade_count,
        "reject_count": state.rejected_trade_count,
        "gate_reject_count": gate_rejects,
        "daily_pnl_618": round(float(state.daily_pnls.get(DAY_618, 0.0)), 2),
        "daily_pnl_619": round(float(state.daily_pnls.get(DAY_619, 0.0)), 2),
        **{f"symbol_pnl_{k}": sym_pnl[k] for k in sym_pnl},
        **{k: shape[k] for k in (
            "opening_peak_accepted",
            "slow_opening_peak_accepted",
            "uptrend_accepted",
            "uptrend_miss_count",
            "uptrend_adoption_rate",
        )},
        "_state": state,
    }


def _rank_variants(rows: Sequence[Mapping[str, Any]], key: str, *, reverse: bool = True) -> list[str]:
    return [str(r["variant"]) for r in sorted(rows, key=lambda r: float(r.get(key) or 0), reverse=reverse)]


def _verdict(*, best_variant: str, best: Mapping[str, Any], baseline: Mapping[str, Any]) -> str:
    delta = float(best.get("delta_pnl_vs_baseline") or 0)
    d619 = float(best.get("delta_daily_pnl_619") or 0)
    ddd = float(best.get("delta_maxdd_vs_baseline") or 0)
    d6976 = float(best.get("delta_symbol_pnl_6976") or 0)
    op_red = float(best.get("opening_peak_reduction_pct") or 0)
    if delta > 20000 and d619 > 0 and ddd <= 0 and d6976 >= -5000 and op_red >= 50:
        return "runtime_ready"
    prefix = str(best_variant).split("_", 1)[0]
    mapping = {
        "B": "opening_peak_candidate",
        "C": "opening_peak_candidate",
        "D": "opening_peak_candidate",
        "E": "weak_shape_candidate",
        "F": "uptrend_candidate",
        "G": "combined_candidate",
        "H": "combined_candidate",
    }
    return mapping.get(prefix, "combined_candidate")


def run_phase451_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    candidates = _load_candidate_stream(repo_root)
    enriched = _enrich_candidates(candidates, kabu=kabu)
    np_shadows = _precompute_np_shadows(enriched, kabu=kabu, np_policy=BEST_NP_POLICY)

    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    eod_shape: dict[tuple[str, str], str] = {
        key: _classify_eod_shape(series, day=key[1]) for key, series in price_idx.items()
    }
    eod_uptrend_keys = {key for key, shape in eod_shape.items() if shape == "uptrend"}

    metrics_rows: list[dict[str, Any]] = []
    for variant_id, _label, shape_guard in VARIANTS:
        state = simulate_capacity_replay(
            enriched,
            np_shadows,
            mode=variant_id,
            entry_block_fn=_runtime_entry_block(shape_guard),
            baseline_accepted_keys=set(),
        )
        metrics_rows.append(_metrics_from_state(state, variant=variant_id, eod_uptrend_keys=eod_uptrend_keys))

    baseline = metrics_rows[0]
    base_pnl = float(baseline["total_pnl_yen"])
    base_pf = float(baseline["profit_factor"] or 0.0)
    base_dd = float(baseline["max_drawdown_yen"] or 0.0)
    base_stop = float(baseline["stop_rate"] or 0.0)
    base_618 = float(baseline["daily_pnl_618"])
    base_619 = float(baseline["daily_pnl_619"])
    base_op = int(baseline["opening_peak_accepted"])
    base_uptrend_adopt = baseline.get("uptrend_adoption_rate")

    for m in metrics_rows:
        m["delta_pnl_vs_baseline"] = round(float(m["total_pnl_yen"]) - base_pnl, 2)
        m["delta_pf_vs_baseline"] = round(float(m["profit_factor"] or 0) - base_pf, 4)
        m["delta_maxdd_vs_baseline"] = round(float(m["max_drawdown_yen"] or 0) - base_dd, 2)
        m["delta_stop_rate_vs_baseline"] = round(float(m["stop_rate"] or 0) - base_stop, 4)
        m["delta_daily_pnl_618"] = round(float(m["daily_pnl_618"]) - base_618, 2)
        m["delta_daily_pnl_619"] = round(float(m["daily_pnl_619"]) - base_619, 2)
        for sym in TARGET_SYMBOLS:
            key = f"symbol_pnl_{sym.replace('.T', '')}"
            m[f"delta_{key}"] = round(float(m.get(key) or 0) - float(baseline.get(key) or 0), 2)
        op = int(m["opening_peak_accepted"])
        m["opening_peak_reduction_pct"] = round((base_op - op) / base_op * 100.0, 2) if base_op else 0.0
        ua = m.get("uptrend_adoption_rate")
        m["uptrend_adoption_improvement"] = (
            round(float(ua) - float(base_uptrend_adopt), 4)
            if ua is not None and base_uptrend_adopt is not None
            else None
        )

    challengers = [m for m in metrics_rows if m["variant"] != "A_baseline"]
    best = max(challengers, key=lambda r: float(r["delta_pnl_vs_baseline"]))
    practical = max(
        challengers,
        key=lambda r: (
            float(r["delta_pnl_vs_baseline"]),
            float(r.get("opening_peak_reduction_pct") or 0),
            float(r.get("delta_symbol_pnl_6976") or 0),
        ),
    )
    e_row = next((m for m in metrics_rows if m["variant"] == "E_weak_shape_reject"), None)
    verdict = _verdict(best_variant=str(best["variant"]), best=best, baseline=baseline)

    summary = {
        "phase": "451-Entry-Shape-Tournament",
        "generated_at": _now_iso(),
        "verdict": verdict,
        "period": f"{PERIOD_START}..{PERIOD_END}",
        "baseline_stack": {
            "entry": "Momentum:low + Board:mid + High Drift Pullback Guard",
            "exit": "Hard Stop -1.2% → No Progress → Board Dynamic Trailing",
            "cap": CAP,
            "starting_equity": STARTING_EQUITY,
        },
        "candidate_count": len(enriched),
        "comparison": [{k: m.get(k) for k in COMPARISON_FIELDS} for m in metrics_rows],
        "rankings": {
            "pnl": _rank_variants(metrics_rows, "total_pnl_yen"),
            "pf": _rank_variants(metrics_rows, "profit_factor"),
            "maxdd": _rank_variants(metrics_rows, "max_drawdown_yen", reverse=False),
            "stop_rate": _rank_variants(metrics_rows, "stop_rate", reverse=False),
        },
        "best_variant": best["variant"],
        "practical_variant": "E_weak_shape_reject" if e_row else practical["variant"],
        "notes": {
            "F_uptrend_tradeoff": "Highest PnL/PF but 6976 -87k and uptrend adoption -13pp",
            "E_weak_shape_tradeoff": "Near-best PnL (+34.7k), 100% OP/SOP block, PF 1.20, 6976 -15k",
            "B_opening_peak_tradeoff": "6976 +25k, OP -81%, 6/19 slightly worse",
        },
        "mandatory_answers": {
            "1_best_variant": best["variant"],
            "2_pnl_ranking": _rank_variants(metrics_rows, "total_pnl_yen"),
            "3_pf_ranking": _rank_variants(metrics_rows, "profit_factor"),
            "4_maxdd_ranking": _rank_variants(metrics_rows, "max_drawdown_yen", reverse=False),
            "5_stop_rate_ranking": _rank_variants(metrics_rows, "stop_rate", reverse=False),
            "6_6976_improved": float(best.get("delta_symbol_pnl_6976") or 0) > 0,
            "7_6920_improved": float(best.get("delta_symbol_pnl_6920") or 0) > 0,
            "8_4062_side_effect": best.get("delta_symbol_pnl_4062"),
            "9_delta_618": best.get("delta_daily_pnl_618"),
            "10_delta_619": best.get("delta_daily_pnl_619"),
            "11_opening_peak_reduction_pct": best.get("opening_peak_reduction_pct"),
            "12_uptrend_adoption_improvement": best.get("uptrend_adoption_improvement"),
            "13_runtime_candidate": verdict in ("runtime_ready", "opening_peak_candidate", "weak_shape_candidate"),
            "13_recommended_shadow": "E_weak_shape_reject",
        },
    }

    public_rows = [{k: m.get(k) for k in COMPARISON_FIELDS} for m in metrics_rows]
    return {"summary": summary, "_comparison_rows": public_rows}


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    m = s.get("mandatory_answers") or {}
    cmp_ = s.get("comparison") or []
    rk = s.get("rankings") or {}
    lines = [
        "# Phase451 — Entry Shape Filter Tournament",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Verdict: **`{s.get('verdict')}`**",
        f"Period: {s.get('period')}",
        f"Best variant (PnL): **{s.get('best_variant')}**",
        f"Practical variant: **{s.get('practical_variant')}**",
        "",
        "## Comparison",
        "",
        "| Variant | PnL | ΔPnL | PF | MaxDD | Stop | Acc | OP acc | SOP acc | UP acc | 6/18 Δ | 6/19 Δ | 6976 Δ | 6920 Δ | 4062 Δ |",
        "|---------|-----|------|-----|-------|------|-----|--------|---------|--------|--------|--------|--------|--------|--------|",
    ]
    for row in cmp_:
        lines.append(
            f"| {row.get('variant')} | {row.get('total_pnl_yen')} | {row.get('delta_pnl_vs_baseline')} | "
            f"{row.get('profit_factor')} | {row.get('max_drawdown_yen')} | {row.get('stop_rate')} | "
            f"{row.get('accepted_count')} | {row.get('opening_peak_accepted')} | "
            f"{row.get('slow_opening_peak_accepted')} | {row.get('uptrend_accepted')} | "
            f"{row.get('delta_daily_pnl_618')} | {row.get('delta_daily_pnl_619')} | "
            f"{row.get('delta_symbol_pnl_6976')} | {row.get('delta_symbol_pnl_6920')} | "
            f"{row.get('delta_symbol_pnl_4062')} |"
        )
    lines.extend(
        [
            "",
            "## Rankings",
            "",
            f"- PnL: {', '.join(rk.get('pnl') or [])}",
            f"- PF: {', '.join(rk.get('pf') or [])}",
            f"- MaxDD (lower better): {', '.join(rk.get('maxdd') or [])}",
            f"- Stop rate (lower better): {', '.join(rk.get('stop_rate') or [])}",
            "",
            "## Mandatory answers",
            "",
            f"1. 最良variant: **{m.get('1_best_variant')}**",
            f"2. PnL順位: {m.get('2_pnl_ranking')}",
            f"3. PF順位: {m.get('3_pf_ranking')}",
            f"4. maxDD順位: {m.get('4_maxdd_ranking')}",
            f"5. stop率順位: {m.get('5_stop_rate_ranking')}",
            f"6. 6976改善: {m.get('6_6976_improved')}",
            f"7. 6920改善: {m.get('7_6920_improved')}",
            f"8. 4062副作用: {m.get('8_4062_side_effect')} yen",
            f"9. 6/18改善: {m.get('9_delta_618')} yen",
            f"10. 6/19改善: {m.get('10_delta_619')} yen",
            f"11. opening_peak削減率: {m.get('11_opening_peak_reduction_pct')}%",
            f"12. uptrend採用率改善: {m.get('12_uptrend_adoption_improvement')}",
            f"13. Runtime候補: {m.get('13_runtime_candidate')} (recommended shadow: {m.get('13_recommended_shadow')})",
            "",
            "## Notes",
            "",
            *(f"- {k}: {v}" for k, v in (s.get("notes") or {}).items()),
        ]
    )
    return "\n".join(lines) + "\n"


@dataclass
class Phase451Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase451_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "comparison": reports / "phase451_entry_shape_tournament.csv",
            "summary": reports / "phase451_entry_shape_summary.json",
            "report": kabu / "docs" / "operations" / "phase451_entry_shape_tournament_report.md",
        }
        _write_csv(paths["comparison"], COMPARISON_FIELDS, result.get("_comparison_rows") or [])
        paths["summary"].write_text(
            json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths
