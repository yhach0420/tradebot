"""
Phase582 — Universe Optimization Study (research only).

Compares Core/Dynamic universe size variants by filtering canonical accepted trades
through counterfactual universe membership. No Runtime or universe-generation logic changes.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv, read_jpx_sector_map
from research.market_sector_heat_universe_shadow import (
    core_symbols_from_universe,
    dynamic_rank_map_from_universe,
    load_features_csv,
    load_universe_csv,
    resolve_am_universe_path,
)
from research.phase382_capital_constrained_backtest import _float, _parse_ts
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import JST, _now_iso
from research.phase524_live_reentry_guard_and_stop_low_mfe import _is_stop_low_mfe, _latest_live_day
from research.phase530_winner_capture_research import _sym_key
from research.phase533_or_profit_source_audit import _num
from research.phase540_no_progress_mfe0_entry_quality import _load_canonical_trades_for_day
from research.phase572_runtime_pipeline_visualization import SESSION_DIR_RE
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from research.small_paper_performance_review import _load_events
from universe.core_watchlist import load_core_watchlist
from universe.price_risk_filter import passes_dynamic_price_risk

PHASE582_VERDICT = "phase582_universe_optimization_study_done"
PERIOD_START = "20260529"
BASELINE_ID = "C"
MAX_WORKERS = 4

UNIVERSE_VARIANTS: tuple[tuple[str, str, int, int], ...] = (
    ("A", "Core10+Dynamic20", 10, 20),
    ("B", "Core10+Dynamic30", 10, 30),
    ("C", "Core10+Dynamic40", 10, 40),
    ("D", "Core10+Dynamic50", 10, 50),
    ("E", "Core5+Dynamic40", 5, 40),
    ("F", "Core15+Dynamic35", 15, 35),
    ("G", "Dynamic50_only", 0, 50),
    ("H", "Core10_only", 10, 0),
)

DYNAMIC_SIZE_IDS = ("A", "B", "C", "D")

PRICE_BANDS: tuple[tuple[str, float, Optional[float]], ...] = (
    ("lt_500", 0, 500),
    ("500_1000", 500, 1000),
    ("1000_3000", 1000, 3000),
    ("3000_5000", 3000, 5000),
    ("5000_10000", 5000, 10000),
    ("gte_10000", 10000, None),
)

SUMMARY_FIELDS = [
    "universe_id",
    "universe_label",
    "core_slots",
    "dynamic_slots",
    "total_slots",
    "trades",
    "accepted",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "max_drawdown_yen_100",
    "avg_pnl_yen_100",
    "stop_hit_count",
    "stop_low_mfe_count",
    "am_pnl_yen_100",
    "pm_pnl_yen_100",
    "am_pf",
    "pm_pf",
    "top1_pnl_share_pct",
    "top3_pnl_share_pct",
    "top5_pnl_share_pct",
    "high_price_pnl_share_pct",
    "sector_hhi",
    "unique_symbols",
]

SYMBOL_DIFF_FIELDS = [
    "universe_id",
    "diff_type",
    "symbol",
    "trade_count",
    "pnl_yen_100",
    "profit_factor",
    "pnl_contribution_yen_100",
    "pf_vs_universe",
]

PRICE_BAND_FIELDS = [
    "universe_id",
    "price_band",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "pnl_share_pct",
]

SECTOR_FIELDS = [
    "universe_id",
    "sector",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "pnl_share_pct",
    "role",
]

SYMBOL_DEP_FIELDS = [
    "universe_id",
    "metric",
    "symbol",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "pnl_share_pct",
]

DYNAMIC_SIZE_FIELDS = [
    "universe_id",
    "dynamic_slots",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "max_drawdown_yen_100",
    "top3_pnl_share_pct",
    "delta_pf_vs_prev",
    "delta_pnl_vs_prev",
]


def _discover_days(repo_root: Path) -> list[str]:
    kabu = resolve_kabu_root(repo_root)
    sp = kabu / "results" / "small_paper"
    days: list[str] = []
    if not sp.is_dir():
        return days
    for d in sorted(sp.iterdir()):
        if not d.is_dir() or len(d.name) != 8 or not d.name.isdigit():
            continue
        if d.name < PERIOD_START:
            continue
        if any(d.glob("live_session_*")):
            days.append(d.name)
    return days


def _session_kind(session_name: str) -> str:
    m = SESSION_DIR_RE.match(session_name)
    if not m:
        return "unknown"
    return "pm" if int(m.group(1)[:2]) >= 12 else "am"


def _price_band(entry_price: float) -> str:
    if entry_price <= 0:
        return "unknown"
    for label, lo, hi in PRICE_BANDS:
        if hi is None and entry_price >= lo:
            return label
        if hi is not None and lo <= entry_price < hi:
            return label
    return "unknown"


def _is_stop_hit(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("exit_reason") or "").lower() == "stop_hit"


def _chron_pnls(trades: Sequence[Mapping[str, Any]]) -> list[float]:
    ordered = sorted(
        trades,
        key=lambda t: _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
    )
    out: list[float] = []
    cum = 0.0
    for t in ordered:
        cum += _num(t.get("pnl_yen_100"))
        out.append(cum)
    return out


def _global_dynamic_rank(trades: Sequence[Mapping[str, Any]], core: set[str]) -> dict[str, int]:
    freq: Counter[str] = Counter()
    for t in trades:
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
    out = set(dynamic)
    for _, sym in scored[:need]:
        out.add(sym)
    return out


def _select_core_symbols(
    ordered_core: Sequence[str],
    core_slots: int,
    fallback_rank: Mapping[str, int],
) -> set[str]:
    if core_slots <= 0:
        return set()
    selected: list[str] = []
    for sym in ordered_core:
        s = _sym_key(sym)
        if s and s not in selected:
            selected.append(s)
        if len(selected) >= core_slots:
            break
    if len(selected) < core_slots:
        for sym in sorted(fallback_rank.keys(), key=lambda s: fallback_rank[s]):
            if sym not in selected:
                selected.append(sym)
            if len(selected) >= core_slots:
                break
    return set(selected[:core_slots])


def _universe_symbols_for_day(
    day: str,
    *,
    core_slots: int,
    dynamic_slots: int,
    reports_dir: Path,
    ordered_core: Sequence[str],
    fallback_rank: Mapping[str, int],
    days: Sequence[str],
) -> set[str]:
    path = resolve_am_universe_path(reports_dir, day)
    core_day = _select_core_symbols(ordered_core, core_slots, fallback_rank)
    dynamic: set[str] = set()

    if path and path.is_file():
        universe = load_universe_csv(path)
        if universe:
            csv_core = {_sym_key(s) for s in core_symbols_from_universe(universe)}
            if csv_core and core_slots > 0:
                merged: list[str] = []
                for sym in ordered_core:
                    s = _sym_key(sym)
                    if s in csv_core and s not in merged:
                        merged.append(s)
                for s in sorted(csv_core):
                    if s not in merged:
                        merged.append(s)
                core_day = set(merged[:core_slots]) if core_slots else set()
            rank_map = dynamic_rank_map_from_universe(universe)
            if dynamic_slots > 0:
                dynamic = {sym for sym, rank in rank_map.items() if rank <= dynamic_slots}
                dynamic = {_sym_key(s) for s in dynamic} - core_day
                if len(dynamic) < dynamic_slots:
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
        dynamic = {
            _sym_key(sym)
            for sym, rank in fallback_rank.items()
            if rank <= dynamic_slots and _sym_key(sym) not in core_day
        }

    if not core_day and core_slots > 0 and fallback_rank:
        core_day = set(sorted(fallback_rank, key=lambda s: fallback_rank[s])[:core_slots])

    return core_day | dynamic


def _build_universe_maps(
    *,
    repo_root: Path,
    days: Sequence[str],
    reports_dir: Path,
    all_trades: Sequence[Mapping[str, Any]],
) -> dict[str, dict[str, set[str]]]:
    try:
        core_raw, _ = load_core_watchlist(repo_root)
    except Exception:
        core_raw = []
    ordered_core = [_sym_key(s) for s in core_raw if _sym_key(s)]
    if not ordered_core:
        all_syms = {_sym_key(t.get("symbol")) for t in all_trades if _sym_key(t.get("symbol"))}
        ordered_core = sorted(all_syms)[:10]
    core_set = set(ordered_core)
    fallback_rank = _global_dynamic_rank(all_trades, core_set)

    out: dict[str, dict[str, set[str]]] = {}
    for uid, _, core_slots, dynamic_slots in UNIVERSE_VARIANTS:
        by_day: dict[str, set[str]] = {}
        for day in days:
            by_day[day] = _universe_symbols_for_day(
                day,
                core_slots=core_slots,
                dynamic_slots=dynamic_slots,
                reports_dir=reports_dir,
                ordered_core=ordered_core,
                fallback_rank=fallback_rank,
                days=days,
            )
        out[uid] = by_day
    return out


def _load_day_trades(repo_root: Path, day: str) -> list[dict[str, Any]]:
    trades = _load_canonical_trades_for_day(repo_root, day, all_sessions=True)
    out: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        row["day"] = day
        row["session_kind"] = _session_kind(str(row.get("session") or ""))
        px = _num(row.get("entry_price") or row.get("price") or 0)
        row["price_band"] = _price_band(px)
        row["symbol_key"] = _sym_key(row.get("symbol"))
        row["pnl_yen_100"] = round(_num(row.get("pnl_yen_100")), 2)
        out.append(row)
    return out


def _load_day_accepted(repo_root: Path, day: str) -> list[dict[str, Any]]:
    kabu = resolve_kabu_root(repo_root)
    day_dir = kabu / "results" / "small_paper" / day
    if not day_dir.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for sess_dir in sorted(day_dir.glob("live_session_*")):
        if not (sess_dir / "small_paper_events.csv").is_file() and not (
            sess_dir / "small_paper_events.jsonl"
        ).is_file():
            continue
        for ev in _load_events(sess_dir):
            if str(ev.get("event_type") or "") != "accepted":
                continue
            rows.append(
                {
                    "day": day,
                    "symbol_key": _sym_key(ev.get("symbol")),
                    "session_kind": _session_kind(sess_dir.name),
                }
            )
    return rows


def _filter_trades(
    trades: Sequence[Mapping[str, Any]],
    universe_by_day: Mapping[str, set[str]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        day = str(t.get("day") or "")[:8]
        sym = _sym_key(t.get("symbol"))
        if sym in universe_by_day.get(day, set()):
            out.append(dict(t))
    return out


def _filter_accepted(
    accepted: Sequence[Mapping[str, Any]],
    universe_by_day: Mapping[str, set[str]],
) -> int:
    return sum(
        1
        for a in accepted
        if a.get("symbol_key") in universe_by_day.get(str(a.get("day") or "")[:8], set())
    )


def _symbol_pnls(trades: Sequence[Mapping[str, Any]]) -> dict[str, list[float]]:
    out: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        out[_sym_key(t.get("symbol"))].append(_num(t.get("pnl_yen_100")))
    return out


def _metrics_for_trades(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_num(t.get("pnl_yen_100")) for t in trades]
    total = round(sum(pnls), 2)
    chron = _chron_pnls(trades)
    sym_pnls = _symbol_pnls(trades)
    ranked = sorted(((s, sum(v)) for s, v in sym_pnls.items()), key=lambda x: x[1], reverse=True)
    top1 = ranked[0][1] if ranked else 0.0
    top3 = sum(p for _, p in ranked[:3])
    top5 = sum(p for _, p in ranked[:5])
    high_price = sum(
        _num(t.get("pnl_yen_100"))
        for t in trades
        if t.get("price_band") in ("5000_10000", "gte_10000")
    )
    am_trades = [t for t in trades if t.get("session_kind") == "am"]
    pm_trades = [t for t in trades if t.get("session_kind") == "pm"]
    am_pnls = [_num(t.get("pnl_yen_100")) for t in am_trades]
    pm_pnls = [_num(t.get("pnl_yen_100")) for t in pm_trades]
    return {
        "trades": len(pnls),
        "pnl_yen_100": total,
        "profit_factor": round(_pf(pnls) or 0.0, 4),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else 0.0,
        "max_drawdown_yen_100": round(_max_drawdown_yen(chron) if chron else 0.0, 2),
        "avg_pnl_yen_100": round(total / len(pnls), 2) if pnls else 0.0,
        "stop_hit_count": sum(1 for t in trades if _is_stop_hit(t)),
        "stop_low_mfe_count": sum(1 for t in trades if _is_stop_low_mfe(t)),
        "am_pnl_yen_100": round(sum(am_pnls), 2),
        "pm_pnl_yen_100": round(sum(pm_pnls), 2),
        "am_pf": round(_pf(am_pnls) or 0.0, 4),
        "pm_pf": round(_pf(pm_pnls) or 0.0, 4),
        "top1_pnl_share_pct": round(100.0 * top1 / total, 2) if total else 0.0,
        "top3_pnl_share_pct": round(100.0 * top3 / total, 2) if total else 0.0,
        "top5_pnl_share_pct": round(100.0 * top5 / total, 2) if total else 0.0,
        "high_price_pnl_share_pct": round(100.0 * high_price / total, 2) if total else 0.0,
        "unique_symbols": len(sym_pnls),
        "sym_pnls": sym_pnls,
    }


def _sector_hhi(trades: Sequence[Mapping[str, Any]], sector_map: Mapping[str, str]) -> float:
    sector_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        sym = _sym_key(t.get("symbol"))
        sector_pnl[sector_map.get(sym, "unknown")] += _num(t.get("pnl_yen_100"))
    total = sum(abs(v) for v in sector_pnl.values()) or 1.0
    shares = [abs(v) / total for v in sector_pnl.values()]
    return round(sum(s * s for s in shares), 4)


def _price_band_rows(universe_id: str, trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        groups[str(t.get("price_band") or "unknown")].append(_num(t.get("pnl_yen_100")))
    total = sum(sum(v) for v in groups.values()) or 1.0
    rows: list[dict[str, Any]] = []
    for label, _, _ in PRICE_BANDS:
        pnls = groups.get(label, [])
        wins = sum(1 for p in pnls if p > 0)
        rows.append(
            {
                "universe_id": universe_id,
                "price_band": label,
                "trades": len(pnls),
                "pnl_yen_100": round(sum(pnls), 2),
                "profit_factor": round(_pf(pnls) or 0.0, 4),
                "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
                "pnl_share_pct": round(100.0 * sum(pnls) / total, 2),
            }
        )
    if groups.get("unknown"):
        pnls = groups["unknown"]
        rows.append(
            {
                "universe_id": universe_id,
                "price_band": "unknown",
                "trades": len(pnls),
                "pnl_yen_100": round(sum(pnls), 2),
                "profit_factor": round(_pf(pnls) or 0.0, 4),
                "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4),
                "pnl_share_pct": round(100.0 * sum(pnls) / total, 2),
            }
        )
    return rows


def _sector_rows(
    universe_id: str,
    trades: Sequence[Mapping[str, Any]],
    sector_map: Mapping[str, str],
) -> list[dict[str, Any]]:
    groups: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        sym = _sym_key(t.get("symbol"))
        groups[sector_map.get(sym, "unknown")].append(_num(t.get("pnl_yen_100")))
    total = sum(sum(v) for v in groups.values()) or 1.0
    rows: list[dict[str, Any]] = []
    for sector, pnls in sorted(groups.items(), key=lambda kv: sum(kv[1]), reverse=True):
        s = sum(pnls)
        role = "profit_source" if s > 0 else "loss_source" if s < 0 else "neutral"
        wins = sum(1 for p in pnls if p > 0)
        rows.append(
            {
                "universe_id": universe_id,
                "sector": sector,
                "trades": len(pnls),
                "pnl_yen_100": round(s, 2),
                "profit_factor": round(_pf(pnls) or 0.0, 4),
                "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
                "pnl_share_pct": round(100.0 * s / total, 2),
                "role": role,
            }
        )
    return rows


def _dependency_rows(universe_id: str, trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    sym_pnls = _symbol_pnls(trades)
    total = sum(sum(v) for v in sym_pnls.values()) or 1.0
    ranked = sorted(sym_pnls.items(), key=lambda kv: sum(kv[1]), reverse=True)
    rows: list[dict[str, Any]] = []
    for metric, n in (("top1", 1), ("top3", 3), ("top5", 5)):
        subset = ranked[:n]
        sub_pnls = [p for _, vals in subset for p in vals]
        sym_label = ",".join(s for s, _ in subset)
        rows.append(
            {
                "universe_id": universe_id,
                "metric": metric,
                "symbol": sym_label,
                "trades": len(sub_pnls),
                "pnl_yen_100": round(sum(sub_pnls), 2),
                "profit_factor": round(_pf(sub_pnls) or 0.0, 4),
                "pnl_share_pct": round(100.0 * sum(sub_pnls) / total, 2),
            }
        )
    return rows


def _symbol_diff_rows(
    variant_id: str,
    variant_syms: set[str],
    baseline_syms: set[str],
    trades: Sequence[Mapping[str, Any]],
    universe_pf: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sym_pnls = _symbol_pnls(trades)
    for diff_type, syms in (
        ("added_vs_baseline", variant_syms - baseline_syms),
        ("removed_vs_baseline", baseline_syms - variant_syms),
    ):
        for sym in sorted(syms):
            pnls = sym_pnls.get(sym, [])
            sym_pf = round(_pf(pnls) or 0.0, 4) if pnls else 0.0
            rows.append(
                {
                    "universe_id": variant_id,
                    "diff_type": diff_type,
                    "symbol": sym,
                    "trade_count": len(pnls),
                    "pnl_yen_100": round(sum(pnls), 2),
                    "profit_factor": sym_pf,
                    "pnl_contribution_yen_100": round(sum(pnls), 2),
                    "pf_vs_universe": round(sym_pf - universe_pf, 4),
                }
            )
    return rows


def _dynamic40_dead_symbols(
    baseline_trades: Sequence[Mapping[str, Any]],
    baseline_universe_by_day: Mapping[str, set[str]],
    ordered_core: Sequence[str],
    reports_dir: Path,
    days: Sequence[str],
) -> list[dict[str, Any]]:
    core_set = set(_sym_key(s) for s in ordered_core)
    dynamic_syms: set[str] = set()
    for day in days:
        path = resolve_am_universe_path(reports_dir, day)
        if not path:
            continue
        universe = load_universe_csv(path)
        if not universe:
            continue
        rank_map = dynamic_rank_map_from_universe(universe)
        dynamic_syms |= {_sym_key(s) for s in rank_map if _sym_key(s) not in core_set}

    sym_pnls = _symbol_pnls([t for t in baseline_trades if _sym_key(t.get("symbol")) in dynamic_syms])
    rows: list[dict[str, Any]] = []
    for sym in sorted(dynamic_syms):
        pnls = sym_pnls.get(sym, [])
        total = sum(pnls)
        pf = round(_pf(pnls) or 0.0, 4) if pnls else 0.0
        flags: list[str] = []
        if not pnls:
            flags.append("never_traded")
        elif total <= 0 and all(p <= 0 for p in pnls):
            flags.append("never_profitable")
        if pnls and pf < 0.8 and len(pnls) >= 2:
            flags.append("low_pf")
        if flags:
            rows.append(
                {
                    "universe_id": BASELINE_ID,
                    "symbol": sym,
                    "trade_count": len(pnls),
                    "pnl_yen_100": round(total, 2),
                    "profit_factor": pf,
                    "flags": "|".join(flags),
                }
            )
    return rows


@dataclass
class _VariantInput:
    uid: str
    label: str
    core_slots: int
    dynamic_slots: int
    universe_by_day: Mapping[str, set[str]]
    all_trades: Sequence[Mapping[str, Any]]
    all_accepted: Sequence[Mapping[str, Any]]
    sector_map: Mapping[str, str]
    baseline_syms: set[str]


def _evaluate_variant(inp: _VariantInput) -> dict[str, Any]:
    trades = _filter_trades(inp.all_trades, inp.universe_by_day)
    accepted = _filter_accepted(inp.all_accepted, inp.universe_by_day)
    metrics = _metrics_for_trades(trades)
    hhi = _sector_hhi(trades, inp.sector_map)
    variant_syms: set[str] = set()
    for syms in inp.universe_by_day.values():
        variant_syms |= syms

    summary = {
        "universe_id": inp.uid,
        "universe_label": inp.label,
        "core_slots": inp.core_slots,
        "dynamic_slots": inp.dynamic_slots,
        "total_slots": inp.core_slots + inp.dynamic_slots,
        "accepted": accepted,
        "sector_hhi": hhi,
        **{k: v for k, v in metrics.items() if k != "sym_pnls"},
    }

    return {
        "summary": summary,
        "trades": trades,
        "price_band_rows": _price_band_rows(inp.uid, trades),
        "sector_rows": _sector_rows(inp.uid, trades, inp.sector_map),
        "dependency_rows": _dependency_rows(inp.uid, trades),
        "symbol_diff_rows": (
            _symbol_diff_rows(
                inp.uid,
                variant_syms,
                inp.baseline_syms,
                trades,
                float(metrics["profit_factor"] or 0),
            )
            if inp.uid != BASELINE_ID
            else []
        ),
        "variant_syms": variant_syms,
    }


@dataclass
class Phase582Job:
    repo_root: Path
    workers: int = MAX_WORKERS
    period_end: Optional[str] = None

    def run(self) -> dict[str, Any]:
        days = _discover_days(self.repo_root)
        end = self.period_end or _latest_live_day(self.repo_root)
        days = [d for d in days if d <= end]
        kabu = resolve_kabu_root(self.repo_root)
        reports_dir = resolve_reports_dir(self.repo_root)
        sector_map = read_jpx_sector_map(kabu)

        all_trades: list[dict[str, Any]] = []
        all_accepted: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            trade_futs = {ex.submit(_load_day_trades, self.repo_root, d): d for d in days}
            acc_futs = {ex.submit(_load_day_accepted, self.repo_root, d): d for d in days}
            for fut in as_completed(trade_futs):
                all_trades.extend(fut.result())
            for fut in as_completed(acc_futs):
                all_accepted.extend(fut.result())

        all_trades.sort(
            key=lambda t: _parse_ts(str(t.get("entry_time") or ""))
            or datetime.min.replace(tzinfo=JST)
        )

        universe_maps = _build_universe_maps(
            repo_root=self.repo_root,
            days=days,
            reports_dir=reports_dir,
            all_trades=all_trades,
        )
        baseline_syms: set[str] = set()
        for syms in universe_maps[BASELINE_ID].values():
            baseline_syms |= syms

        inputs = [
            _VariantInput(
                uid=uid,
                label=label,
                core_slots=core,
                dynamic_slots=dyn,
                universe_by_day=universe_maps[uid],
                all_trades=all_trades,
                all_accepted=all_accepted,
                sector_map=sector_map,
                baseline_syms=baseline_syms,
            )
            for uid, label, core, dyn in UNIVERSE_VARIANTS
        ]

        results: dict[str, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(_evaluate_variant, inp): inp.uid for inp in inputs}
            for fut in as_completed(futs):
                uid = futs[fut]
                results[uid] = fut.result()

        summary_rows = [results[u]["summary"] for u, _, _, _ in UNIVERSE_VARIANTS]
        baseline = next(r for r in summary_rows if r["universe_id"] == BASELINE_ID)

        price_band_rows: list[dict[str, Any]] = []
        sector_rows: list[dict[str, Any]] = []
        dependency_rows: list[dict[str, Any]] = []
        symbol_diff_rows: list[dict[str, Any]] = []
        for uid, _, _, _ in UNIVERSE_VARIANTS:
            price_band_rows.extend(results[uid]["price_band_rows"])
            sector_rows.extend(results[uid]["sector_rows"])
            dependency_rows.extend(results[uid]["dependency_rows"])
            symbol_diff_rows.extend(results[uid]["symbol_diff_rows"])

        dynamic_size_rows: list[dict[str, Any]] = []
        prev: Optional[dict[str, Any]] = None
        for uid in DYNAMIC_SIZE_IDS:
            row = dict(results[uid]["summary"])
            row["delta_pf_vs_prev"] = round(float(row["profit_factor"]) - float(prev["profit_factor"]), 4) if prev else 0.0
            row["delta_pnl_vs_prev"] = round(float(row["pnl_yen_100"]) - float(prev["pnl_yen_100"]), 2) if prev else 0.0
            dynamic_size_rows.append(row)
            prev = results[uid]["summary"]

        try:
            core_raw, _ = load_core_watchlist(self.repo_root)
            ordered_core = [_sym_key(s) for s in core_raw if _sym_key(s)]
        except Exception:
            ordered_core = []
        dynamic40_dead = _dynamic40_dead_symbols(
            results[BASELINE_ID]["trades"],
            universe_maps[BASELINE_ID],
            ordered_core,
            reports_dir,
            days,
        )

        best_pf = max(summary_rows, key=lambda r: float(r["profit_factor"] or 0))
        best_pnl = max(summary_rows, key=lambda r: float(r["pnl_yen_100"] or 0))
        best_composite = max(
            summary_rows,
            key=lambda r: float(r["profit_factor"] or 0) * 0.6 + (float(r["pnl_yen_100"] or 0) / 100000.0) * 0.4,
        )

        d20 = next(r for r in summary_rows if r["universe_id"] == "A")
        d40 = baseline
        d50 = next(r for r in summary_rows if r["universe_id"] == "D")
        core5 = next(r for r in summary_rows if r["universe_id"] == "E")
        core15 = next(r for r in summary_rows if r["universe_id"] == "F")
        core10_only = next(r for r in summary_rows if r["universe_id"] == "H")
        dyn_only = next(r for r in summary_rows if r["universe_id"] == "G")

        pf_trend = [float(r["profit_factor"] or 0) for r in dynamic_size_rows]
        pf_increases = all(pf_trend[i] <= pf_trend[i + 1] for i in range(len(pf_trend) - 1))
        pf_decreases = all(pf_trend[i] >= pf_trend[i + 1] for i in range(len(pf_trend) - 1))

        mandatory = {
            "1_best_universe": f"{best_composite['universe_id']} ({best_composite['universe_label']})",
            "2_best_pf": f"{best_pf['universe_id']} PF={best_pf['profit_factor']}",
            "3_best_pnl": f"{best_pnl['universe_id']} PnL={best_pnl['pnl_yen_100']}",
            "4_delta_vs_current40_pnl": round(float(best_composite["pnl_yen_100"]) - float(baseline["pnl_yen_100"]), 2),
            "4_delta_vs_current40_pf": round(float(best_composite["profit_factor"]) - float(baseline["profit_factor"]), 4),
            "5_dynamic40_too_many": float(d50["profit_factor"] or 0) < float(d40["profit_factor"] or 0),
            "6_dynamic20_too_few": float(d20["profit_factor"] or 0) < float(d40["profit_factor"] or 0)
            or float(d20["pnl_yen_100"] or 0) < float(d40["pnl_yen_100"] or 0) * 0.9,
            "7_core10_optimal": best_composite["universe_id"] in ("C", "B", "D")
            and float(core5["profit_factor"] or 0) <= float(baseline["profit_factor"] or 0)
            and float(core15["profit_factor"] or 0) <= float(baseline["profit_factor"] or 0),
            "8_high_price_dependency_improves": float(best_composite["high_price_pnl_share_pct"] or 0)
            <= float(baseline["high_price_pnl_share_pct"] or 0),
            "9_symbol_dependency_improves": float(best_composite["top3_pnl_share_pct"] or 0)
            <= float(baseline["top3_pnl_share_pct"] or 0),
            "10_universe_change_worth_it": best_composite["universe_id"] != BASELINE_ID
            and (
                float(best_composite["profit_factor"] or 0) > float(baseline["profit_factor"] or 0) + 0.02
                or float(best_composite["pnl_yen_100"] or 0) > float(baseline["pnl_yen_100"] or 0) + 5000
            ),
            "11_runtime_change_needed": False,
            "12_next_phase": (
                "phase583_universe_shadow_adoption_review"
                if best_composite["universe_id"] != BASELINE_ID
                and (
                    float(best_composite["profit_factor"] or 0) > float(baseline["profit_factor"] or 0) + 0.02
                    or float(best_composite["pnl_yen_100"] or 0) > float(baseline["pnl_yen_100"] or 0) + 5000
                )
                else "phase583_universe_monitor_continue_current40"
            ),
            "period_start": PERIOD_START,
            "period_end": end,
            "trade_count_baseline": baseline["trades"],
            "dynamic_size_pf_monotonic_up": pf_increases,
            "dynamic_size_pf_monotonic_down": pf_decreases,
            "dynamic40_dead_symbol_count": len(dynamic40_dead),
            "core10_only_pf": core10_only["profit_factor"],
            "dynamic50_only_pf": dyn_only["profit_factor"],
        }

        return {
            "verdict": PHASE582_VERDICT,
            "all_pass": len(all_trades) > 0 and len(summary_rows) == len(UNIVERSE_VARIANTS),
            "summary_rows": summary_rows,
            "symbol_diff_rows": symbol_diff_rows,
            "price_band_rows": price_band_rows,
            "sector_rows": sector_rows,
            "dependency_rows": dependency_rows,
            "dynamic_size_rows": dynamic_size_rows,
            "dynamic40_dead_symbols": dynamic40_dead,
            "mandatory_answers": mandatory,
            "generated_at": _now_iso(),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase582_universe_summary.csv",
            "symbol_diff": reports / "phase582_universe_symbol_diff.csv",
            "price_band": reports / "phase582_price_band.csv",
            "sector": reports / "phase582_sector_analysis.csv",
            "symbol_dependency": reports / "phase582_symbol_dependency.csv",
            "dynamic_size": reports / "phase582_dynamic_size_comparison.csv",
            "report": reports / "phase582_report.json",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary_rows") or []))
        _write_csv(paths["symbol_diff"], SYMBOL_DIFF_FIELDS, list(result.get("symbol_diff_rows") or []))
        _write_csv(paths["price_band"], PRICE_BAND_FIELDS, list(result.get("price_band_rows") or []))
        _write_csv(paths["sector"], SECTOR_FIELDS, list(result.get("sector_rows") or []))
        _write_csv(paths["symbol_dependency"], SYMBOL_DEP_FIELDS, list(result.get("dependency_rows") or []))
        _write_csv(paths["dynamic_size"], DYNAMIC_SIZE_FIELDS, list(result.get("dynamic_size_rows") or []))

        slim = {k: v for k, v in result.items() if not k.endswith("_rows")}
        paths["report"].write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")

        m = result.get("mandatory_answers") or {}
        doc = kabu / "docs" / "operations" / "phase582_universe_optimization_study.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        summary = list(result.get("summary_rows") or [])
        doc.write_text(
            "\n".join(
                [
                    "# Phase582 — Universe Optimization Study",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**Period:** {m.get('period_start')}–{m.get('period_end')}",
                    f"**Baseline trades (C):** {m.get('trade_count_baseline')}",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. Best universe: {m.get('1_best_universe')}",
                    f"2. Best PF: {m.get('2_best_pf')}",
                    f"3. Best PnL: {m.get('3_best_pnl')}",
                    f"4. Delta vs Current40 — PnL: {m.get('4_delta_vs_current40_pnl')}, PF: {m.get('4_delta_vs_current40_pf')}",
                    f"5. Dynamic40 too many: {m.get('5_dynamic40_too_many')}",
                    f"6. Dynamic20 too few: {m.get('6_dynamic20_too_few')}",
                    f"7. Core10 optimal: {m.get('7_core10_optimal')}",
                    f"8. High-price dependency improves with best: {m.get('8_high_price_dependency_improves')}",
                    f"9. Symbol dependency improves with best: {m.get('9_symbol_dependency_improves')}",
                    f"10. Universe change worth it: {m.get('10_universe_change_worth_it')}",
                    f"11. Runtime change needed: {m.get('11_runtime_change_needed')}",
                    f"12. Next phase: {m.get('12_next_phase')}",
                    "",
                    "## Universe summary",
                    "",
                    "| ID | Label | Trades | PnL | PF | MaxDD | Top3% |",
                    "|----|-------|--------|-----|----|----|-------|",
                ]
                + [
                    f"| {r['universe_id']} | {r['universe_label']} | {r['trades']} | {r['pnl_yen_100']} | {r['profit_factor']} | {r['max_drawdown_yen_100']} | {r['top3_pnl_share_pct']} |"
                    for r in summary
                ]
                + [
                    "",
                    "## Dynamic size curve (A→D)",
                    "",
                    f"- PF monotonic up: {m.get('dynamic_size_pf_monotonic_up')}",
                    f"- PF monotonic down: {m.get('dynamic_size_pf_monotonic_down')}",
                    f"- Dynamic40 dead/low symbols: {m.get('dynamic40_dead_symbol_count')}",
                ]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths
