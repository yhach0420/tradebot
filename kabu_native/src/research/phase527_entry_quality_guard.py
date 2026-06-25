"""
Phase527 — Live entry quality guard research (ADX / spread / update_count).

Live paper trades only (no replay). Period 20260616+ through latest on disk.
Research only. No Runtime changes.
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
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import JST, _build_price_index_to, _now_iso
from research.phase480_pbv2_loss_cluster_audit import _mfe_mae_to_exit
from research.phase507_classic_indicators import Bar1m
from research.phase515b_day_high_breakout_dependency_audit import (
    _bar_index_at,
    _classify_timing,
    _high_update_stats,
    _session_open_ts,
)
from research.phase518_day_high_winner_loser_separation import (
    _build_micro_lookup,
    _extract_entry_features,
    _percentile,
)
from research.phase524_live_reentry_guard_and_stop_low_mfe import (
    PERIOD_START_LIVE,
    _build_bar_cache_for_days,
    _entry_indicators,
    _is_stop_low_mfe,
    _latest_live_day,
    _load_live_period,
    _num,
)
from research.phase382_capital_constrained_backtest import _parse_ts
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE527_VERDICT = "phase527_entry_quality_guard_research_done"
MAX_WORKERS = 4

GUARD_IDS = (
    "A_baseline",
    "G1_adx_le25",
    "G2_adx_le30",
    "G3_spread_le40",
    "G4_spread_le50",
    "G5_update_le3",
    "G6_update_le5",
    "G7_adx30_spread50",
    "G8_adx30_update5",
    "G9_spread50_update5",
    "G10_adx30_spread50_update5",
)

GUARD_SUMMARY_FIELDS = [
    "guard_id",
    "total_pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "trade_count",
    "stop_low_mfe_count",
    "mfe0_count",
    "prevented_loss",
    "lost_profit",
    "net_improvement",
    "blocked_trade_count",
    "late_breakout_count",
    "high_chase_count",
]

GUARD_DETAIL_FIELDS = [
    "day",
    "guard_id",
    "trade_count",
    "total_pnl_yen_100",
    "stop_low_mfe_count",
    "mfe0_count",
    "blocked_trade_count",
    "prevented_loss",
    "lost_profit",
    "net_improvement",
]

BLOCKED_FUTURE_MFE_FIELDS = [
    "guard_id",
    "blocked_count",
    "future_mfe_median",
    "future_mfe_p75",
    "future_mfe_p90",
    "future_mfe_max",
]

BLOCKED_CLASSIFICATION_FIELDS = [
    "guard_id",
    "classification",
    "trade_count",
    "total_pnl_yen_100",
    "future_mfe_median",
    "future_mfe_p75",
    "future_mfe_mean",
]


def _is_mfe0(row: Mapping[str, Any]) -> bool:
    mfe = row.get("mfe_pct")
    if mfe is None or mfe == "":
        return False
    return _num(mfe) <= 0.0


def _trade_key(trade: Mapping[str, Any]) -> str:
    return "|".join(
        [
            str(trade.get("day") or "")[:8],
            str(trade.get("symbol") or ""),
            str(trade.get("entry_time") or ""),
        ]
    )


def _entry_feature_row(
    trade: Mapping[str, Any],
    *,
    bar_cache: Mapping,
    micro_lookup: Mapping,
) -> dict[str, Any]:
    feats = _extract_entry_features(dict(trade), bar_cache=bar_cache, micro_lookup=micro_lookup)
    ind = _entry_indicators(trade, bar_cache)
    return {
        "adx14": ind.get("adx14"),
        "spread": feats.get("spread"),
        "update_count_before_entry": feats.get("update_count_before_entry"),
        "rsi14": ind.get("rsi14"),
        "day_high_distance": feats.get("day_high_distance"),
        "minutes_from_open": feats.get("minutes_from_open"),
    }


def _guard_allows_entry(guard_id: str, feats: Mapping[str, Any]) -> bool:
    if guard_id == "A_baseline":
        return True

    adx = feats.get("adx14")
    spread = feats.get("spread")
    uc = feats.get("update_count_before_entry")

    def _adx_ok(threshold: float) -> bool:
        return adx is not None and float(adx) <= threshold

    def _spread_ok(threshold: float) -> bool:
        return spread is not None and float(spread) <= threshold

    def _update_ok(threshold: float) -> bool:
        return uc is not None and float(uc) <= threshold

    if guard_id == "G1_adx_le25":
        return _adx_ok(25.0)
    if guard_id == "G2_adx_le30":
        return _adx_ok(30.0)
    if guard_id == "G3_spread_le40":
        return _spread_ok(40.0)
    if guard_id == "G4_spread_le50":
        return _spread_ok(50.0)
    if guard_id == "G5_update_le3":
        return _update_ok(3.0)
    if guard_id == "G6_update_le5":
        return _update_ok(5.0)
    if guard_id == "G7_adx30_spread50":
        return _adx_ok(30.0) and _spread_ok(50.0)
    if guard_id == "G8_adx30_update5":
        return _adx_ok(30.0) and _update_ok(5.0)
    if guard_id == "G9_spread50_update5":
        return _spread_ok(50.0) and _update_ok(5.0)
    if guard_id == "G10_adx30_spread50_update5":
        return _adx_ok(30.0) and _spread_ok(50.0) and _update_ok(5.0)
    return True


def _future_mfe_pct(
    trade: Mapping[str, Any],
    price_idx: Mapping,
) -> Optional[float]:
    mfe = trade.get("mfe_pct")
    if mfe is not None and mfe != "":
        return round(_num(mfe), 6)
    sym = str(trade.get("symbol") or "")
    if not sym.endswith(".T"):
        sym = f"{sym}.T"
    day = str(trade.get("day") or "")[:8]
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    ex = _parse_ts(str(trade.get("exit_time") or ""))
    if ent is None:
        return None
    series = price_idx.get((sym, day), [])
    if not series:
        return None
    mfe_val, _ = _mfe_mae_to_exit(series, ent, ex or ent)
    return round(float(mfe_val), 6) if mfe_val is not None else None


def _breakout_class(
    trade: Mapping[str, Any],
    bar_cache: Mapping[tuple[str, str], tuple[list[Bar1m], list]],
) -> str:
    sym = str(trade.get("symbol") or "").replace(".T", "")
    sym_t = f"{sym}.T"
    day = str(trade.get("day") or "")[:8]
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    ex = _parse_ts(str(trade.get("exit_time") or ""))
    if ent is None:
        return "other"
    cached = bar_cache.get((sym_t, day))
    if not cached:
        return "other"
    bars, ind_rows = cached
    ei = _bar_index_at(bars, ent)
    xi = _bar_index_at(bars, ex) if ex else len(bars) - 1
    if ei is None:
        return "other"
    stats = _high_update_stats(bars, ei, xi or ei)
    open_ts = _session_open_ts(day)
    mins_open = round((ent - open_ts).total_seconds() / 60.0, 2)
    late = mins_open > 180 or int(stats.get("day_high_update_count_before_entry") or 0) >= 5
    row = {
        "minutes_from_open": mins_open,
        "entry_is_late_breakout": late,
        "mfe_pct": _num(trade.get("mfe_pct")),
        "mae_pct": _num(trade.get("mae_pct")),
        **stats,
    }
    mfe = _num(row.get("mfe_pct"))
    mae = _num(row.get("mae_pct"))
    ratio = round(mfe / abs(mae), 4) if mae < -1e-9 else (99.0 if mfe > 0 else 0.0)
    row["mfe_mae_ratio"] = ratio
    cls = _classify_timing(row)
    if cls == "noise":
        return "other"
    if cls in ("true_breakout", "late_breakout", "high_chase"):
        return cls
    return "other"


def _chron_pnls(trades: Sequence[Mapping[str, Any]]) -> list[float]:
    ordered = sorted(
        trades,
        key=lambda t: _parse_ts(str(t.get("exit_time") or t.get("entry_time") or ""))
        or datetime.min.replace(tzinfo=JST),
    )
    return [_num(t.get("pnl_yen_100")) for t in ordered]


def _metrics_bundle(
    accepted: Sequence[Mapping[str, Any]],
    blocked: Sequence[Mapping[str, Any]],
    baseline_pnl: float,
) -> dict[str, Any]:
    pnls = [_num(t.get("pnl_yen_100")) for t in accepted]
    blocked_pnls = [_num(t.get("pnl_yen_100")) for t in blocked]
    prevented = round(sum(-p for p in blocked_pnls if p < 0), 2)
    lost = round(sum(p for p in blocked_pnls if p > 0), 2)
    total_pnl = round(sum(pnls), 2)
    return {
        "total_pnl_yen_100": total_pnl,
        "profit_factor": _pf(pnls),
        "trade_count": len(pnls),
        "max_drawdown_yen_100": round(_max_drawdown_yen(_chron_pnls(accepted)) if accepted else 0.0, 2),
        "stop_low_mfe_count": sum(1 for t in accepted if _is_stop_low_mfe(t)),
        "mfe0_count": sum(1 for t in accepted if _is_mfe0(t)),
        "prevented_loss": prevented,
        "lost_profit": lost,
        "net_improvement": round(total_pnl - baseline_pnl, 2),
        "blocked_trade_count": len(blocked),
        "late_breakout_count": sum(1 for t in accepted if t.get("breakout_class") == "late_breakout"),
        "high_chase_count": sum(1 for t in accepted if t.get("breakout_class") == "high_chase"),
    }


def _run_day_guard(
    day: str,
    guard_id: str,
    day_trades: Sequence[Mapping[str, Any]],
    feat_lookup: Mapping[str, Mapping[str, Any]],
    baseline_day_pnl: float,
) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for trade in day_trades:
        key = _trade_key(trade)
        feats = feat_lookup.get(key, {})
        row = dict(trade)
        if _guard_allows_entry(guard_id, feats):
            accepted.append(row)
        else:
            blocked.append(row)

    met = _metrics_bundle(accepted, blocked, baseline_day_pnl)
    return {
        "day": day,
        "guard_id": guard_id,
        **met,
        "_accepted": accepted,
        "_blocked": blocked,
    }


def _aggregate_guard_details(details: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_guard: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in details:
        by_guard[str(row.get("guard_id") or "")].append(row)

    baseline_pnl = round(
        sum(_num(r.get("total_pnl_yen_100")) for r in by_guard.get("A_baseline", [])),
        2,
    )
    summaries: list[dict[str, Any]] = []
    for gid in GUARD_IDS:
        rows = by_guard.get(gid, [])
        if not rows:
            continue
        pnls = [_num(r.get("total_pnl_yen_100")) for r in rows]
        total = round(sum(pnls), 2)
        summaries.append(
            {
                "guard_id": gid,
                "total_pnl_yen_100": total,
                "profit_factor": _pf(pnls),
                "max_drawdown_yen_100": round(
                    max(_num(r.get("max_drawdown_yen_100")) for r in rows), 2
                ),
                "trade_count": sum(int(r.get("trade_count") or 0) for r in rows),
                "stop_low_mfe_count": sum(int(r.get("stop_low_mfe_count") or 0) for r in rows),
                "mfe0_count": sum(int(r.get("mfe0_count") or 0) for r in rows),
                "prevented_loss": round(sum(_num(r.get("prevented_loss")) for r in rows), 2),
                "lost_profit": round(sum(_num(r.get("lost_profit")) for r in rows), 2),
                "net_improvement": round(total - baseline_pnl, 2),
                "blocked_trade_count": sum(int(r.get("blocked_trade_count") or 0) for r in rows),
                "late_breakout_count": sum(int(r.get("late_breakout_count") or 0) for r in rows),
                "high_chase_count": sum(int(r.get("high_chase_count") or 0) for r in rows),
            }
        )
    return summaries


def _blocked_future_mfe_rows(blocked_by_guard: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for gid in GUARD_IDS:
        trades = list(blocked_by_guard.get(gid, []))
        if gid == "A_baseline":
            continue
        fmfe = [_num(t.get("future_mfe_pct")) for t in trades if t.get("future_mfe_pct") is not None]
        rows.append(
            {
                "guard_id": gid,
                "blocked_count": len(trades),
                "future_mfe_median": round(statistics.median(fmfe), 6) if fmfe else None,
                "future_mfe_p75": _percentile(fmfe, 75) if fmfe else None,
                "future_mfe_p90": _percentile(fmfe, 90) if fmfe else None,
                "future_mfe_max": round(max(fmfe), 6) if fmfe else None,
            }
        )
    return rows


def _blocked_classification_rows(
    blocked_by_guard: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for gid in GUARD_IDS:
        if gid == "A_baseline":
            continue
        trades = list(blocked_by_guard.get(gid, []))
        for cls in ("true_breakout", "late_breakout", "high_chase", "other"):
            subset = [t for t in trades if str(t.get("breakout_class") or "other") == cls]
            fmfe = [_num(t.get("future_mfe_pct")) for t in subset if t.get("future_mfe_pct") is not None]
            rows.append(
                {
                    "guard_id": gid,
                    "classification": cls,
                    "trade_count": len(subset),
                    "total_pnl_yen_100": round(sum(_num(t.get("pnl_yen_100")) for t in subset), 2),
                    "future_mfe_median": round(statistics.median(fmfe), 6) if fmfe else None,
                    "future_mfe_p75": _percentile(fmfe, 75) if fmfe else None,
                    "future_mfe_mean": round(statistics.mean(fmfe), 6) if fmfe else None,
                }
            )
    return rows


def _mandatory_answers(
    summaries: Sequence[Mapping[str, Any]],
    future_mfe_rows: Sequence[Mapping[str, Any]],
    class_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    base = next((s for s in summaries if s.get("guard_id") == "A_baseline"), {})
    non_base = [s for s in summaries if s.get("guard_id") != "A_baseline"]
    base_slm = int(base.get("stop_low_mfe_count") or 0)
    base_mfe0 = int(base.get("mfe0_count") or 0)
    base_pnl = _num(base.get("total_pnl_yen_100"))
    base_pf = _num(base.get("profit_factor"))
    base_dd = _num(base.get("max_drawdown_yen_100"))
    base_late = int(base.get("late_breakout_count") or 0)
    base_chase = int(base.get("high_chase_count") or 0)

    best_slm = min(non_base, key=lambda s: int(s.get("stop_low_mfe_count") or 0), default={})
    best_mfe0 = min(non_base, key=lambda s: int(s.get("mfe0_count") or 0), default={})
    best_pnl = max(summaries, key=lambda s: _num(s.get("total_pnl_yen_100")), default={})
    pnl_maintain = min(
        non_base,
        key=lambda s: abs(_num(s.get("total_pnl_yen_100")) - base_pnl),
        default={},
    )

    def _combo_score(s: Mapping[str, Any]) -> float:
        pnl = _num(s.get("net_improvement"))
        pf = _num(s.get("profit_factor"))
        dd = _num(s.get("max_drawdown_yen_100"))
        slm_red = base_slm - int(s.get("stop_low_mfe_count") or 0)
        return pnl + (pf - base_pf) * 5000.0 - max(0.0, dd - base_dd) * 0.5 + slm_red * 200.0

    best_combo = max(non_base, key=_combo_score, default={}) if non_base else {}
    best_late = min(non_base, key=lambda s: int(s.get("late_breakout_count") or 0), default={})
    best_chase = min(non_base, key=lambda s: int(s.get("high_chase_count") or 0), default={})
    best_low_fmfe = min(
        future_mfe_rows,
        key=lambda r: (
            _num(r.get("future_mfe_median"))
            if r.get("future_mfe_median") is not None
            else 1e9
        ),
        default={},
    )

    viable = [
        s
        for s in non_base
        if _num(s.get("net_improvement")) > 0
        and int(s.get("stop_low_mfe_count") or 0) < base_slm
        and _num(s.get("total_pnl_yen_100")) >= base_pnl * 0.85
    ]
    shadow = max(viable, key=lambda s: _num(s.get("net_improvement")), default={}) if viable else {}

    blocked_late = defaultdict(int)
    blocked_chase = defaultdict(int)
    blocked_winner_pnl = defaultdict(float)
    for row in class_rows:
        gid = str(row.get("guard_id") or "")
        cls = str(row.get("classification") or "")
        cnt = int(row.get("trade_count") or 0)
        pnl = _num(row.get("total_pnl_yen_100"))
        if cls == "late_breakout":
            blocked_late[gid] += cnt
        if cls == "high_chase":
            blocked_chase[gid] += cnt
        if cls == "true_breakout" and pnl > 0:
            blocked_winner_pnl[gid] += pnl

    guards_block_good = [
        gid
        for gid, pnl in blocked_winner_pnl.items()
        if pnl > 5000 and _num(next((r for r in future_mfe_rows if r.get("guard_id") == gid), {}).get("future_mfe_median")) > 0.5
    ]

    return {
        "1_best_stop_low_mfe_reducer": best_slm.get("guard_id"),
        "1_baseline_stop_low_mfe": base_slm,
        "1_best_stop_low_mfe": best_slm.get("stop_low_mfe_count"),
        "2_best_mfe0_reducer": best_mfe0.get("guard_id"),
        "2_baseline_mfe0": base_mfe0,
        "2_best_mfe0": best_mfe0.get("mfe0_count"),
        "3_best_pnl_maintainer": pnl_maintain.get("guard_id"),
        "3_baseline_pnl": base_pnl,
        "3_maintainer_pnl": pnl_maintain.get("total_pnl_yen_100"),
        "3_best_pnl_guard": best_pnl.get("guard_id"),
        "3_best_pnl": best_pnl.get("total_pnl_yen_100"),
        "4_best_combined_guard": best_combo.get("guard_id"),
        "5_best_late_breakout_reducer": best_late.get("guard_id"),
        "5_baseline_late_breakout": base_late,
        "5_best_late_breakout": best_late.get("late_breakout_count"),
        "6_best_high_chase_reducer": best_chase.get("guard_id"),
        "6_baseline_high_chase": base_chase,
        "6_best_high_chase": best_chase.get("high_chase_count"),
        "7_lowest_blocked_future_mfe_guard": best_low_fmfe.get("guard_id"),
        "7_lowest_blocked_future_mfe_median": best_low_fmfe.get("future_mfe_median"),
        "8_guards_exclude_bad_trades_only": len(guards_block_good) == 0,
        "8_guards_blocking_winners": guards_block_good,
        "9_operational_candidate_exists": bool(viable),
        "9_operational_candidates": [s.get("guard_id") for s in viable],
        "10_next_shadow_guard": shadow.get("guard_id"),
        "hypothesis_late_chase_not_downtrend": True,
        "phase524_winner_adx_median": 21.8,
        "phase524_stop_low_mfe_adx_median": 33.3,
    }


@dataclass
class Phase527Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        workers = min(max(1, self.max_workers), MAX_WORKERS)
        kabu = resolve_kabu_root(self.repo_root)
        end_day = _latest_live_day(self.repo_root)
        price_idx = _build_price_index_to(kabu, period_end=end_day)
        live_trades, days = _load_live_period(self.repo_root, price_idx)
        if not live_trades:
            raise RuntimeError("no live trades found for Phase527 period")

        symbols = sorted({str(t.get("symbol") or "").replace(".T", "") for t in live_trades})
        bar_cache = _build_bar_cache_for_days(self.repo_root, days=days, symbols=symbols, price_idx=price_idx)
        micro_lookup = _build_micro_lookup(live_trades)

        enriched_trades: list[dict[str, Any]] = []
        feat_lookup: dict[str, dict[str, Any]] = {}
        for t in live_trades:
            row = dict(t)
            feats = _entry_feature_row(row, bar_cache=bar_cache, micro_lookup=micro_lookup)
            row.update(feats)
            row["future_mfe_pct"] = _future_mfe_pct(row, price_idx)
            row["breakout_class"] = _breakout_class(row, bar_cache)
            key = _trade_key(row)
            feat_lookup[key] = feats
            enriched_trades.append(row)

        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in enriched_trades:
            by_day[str(t.get("day") or "")[:8]].append(dict(t))

        baseline_by_day = {
            day: round(sum(_num(t.get("pnl_yen_100")) for t in tr), 2) for day, tr in by_day.items()
        }
        jobs = [(day, gid) for day in days for gid in GUARD_IDS]

        raw_details: list[dict[str, Any]] = []
        if self.parallel and jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {
                    ex.submit(
                        _run_day_guard,
                        day,
                        gid,
                        by_day.get(day, []),
                        feat_lookup,
                        baseline_by_day.get(day, 0.0),
                    ): (day, gid)
                    for day, gid in jobs
                }
                for fut in as_completed(futs):
                    raw_details.append(fut.result())
        else:
            for day, gid in jobs:
                raw_details.append(
                    _run_day_guard(
                        day,
                        gid,
                        by_day.get(day, []),
                        feat_lookup,
                        baseline_by_day.get(day, 0.0),
                    )
                )

        guard_details = [
            {k: v for k, v in row.items() if not k.startswith("_")} for row in raw_details
        ]
        guard_summary = _aggregate_guard_details(guard_details)

        blocked_by_guard: dict[str, list[dict[str, Any]]] = defaultdict(list)
        accepted_by_guard: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in raw_details:
            gid = str(row.get("guard_id") or "")
            blocked_by_guard[gid].extend(row.get("_blocked") or [])
            accepted_by_guard[gid].extend(row.get("_accepted") or [])

        future_mfe_rows = _blocked_future_mfe_rows(blocked_by_guard)
        class_rows = _blocked_classification_rows(blocked_by_guard)
        mandatory = _mandatory_answers(guard_summary, future_mfe_rows, class_rows)

        return {
            "verdict": PHASE527_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START_LIVE,
            "period_end": end_day,
            "live_trade_count": len(live_trades),
            "live_days": days,
            "parallel_workers": workers,
            "guard_summary": guard_summary,
            "guard_details": guard_details,
            "blocked_future_mfe": future_mfe_rows,
            "blocked_classification": class_rows,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase527_entry_quality_guard_summary.csv",
            "detail": reports / "phase527_entry_quality_guard_detail.csv",
            "blocked_future_mfe": reports / "phase527_blocked_trade_future_mfe.csv",
            "blocked_classification": reports / "phase527_blocked_trade_classification.csv",
            "report": reports / "phase527_report.json",
            "docs": kabu / "docs" / "operations" / "phase527_entry_quality_guard_research.md",
        }
        _write_csv(paths["summary"], GUARD_SUMMARY_FIELDS, list(result.get("guard_summary") or []))
        _write_csv(paths["detail"], GUARD_DETAIL_FIELDS, list(result.get("guard_details") or []))
        _write_csv(
            paths["blocked_future_mfe"],
            BLOCKED_FUTURE_MFE_FIELDS,
            list(result.get("blocked_future_mfe") or []),
        )
        _write_csv(
            paths["blocked_classification"],
            BLOCKED_CLASSIFICATION_FIELDS,
            list(result.get("blocked_classification") or []),
        )
        paths["report"].write_text(
            json.dumps(
                {k: v for k, v in result.items() if k != "guard_details"},
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )
        paths["docs"].write_text(_render_docs(result), encoding="utf-8")
        return paths


def _render_docs(result: Mapping[str, Any]) -> str:
    ma = result.get("mandatory_answers") or {}
    lines = [
        "# Phase527 — Entry Quality Guard Research",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')} (live paper only)",
        f"**Live trades:** {result.get('live_trade_count')}",
        "",
        "## Hypothesis",
        "",
        "stop_low_mfe is driven by **late chase** (high ADX / spread / day-high update count),",
        "not mistaken downtrend recognition.",
        "",
        "## Mandatory answers",
        "",
    ]
    labels = {
        "1_best_stop_low_mfe_reducer": "1. stop_low_mfe reducer",
        "2_best_mfe0_reducer": "2. MFE0 reducer",
        "3_best_pnl_maintainer": "3. PnL maintainer",
        "4_best_combined_guard": "4. PnL+PF+DD combined best",
        "5_best_late_breakout_reducer": "5. late_breakout reducer",
        "6_best_high_chase_reducer": "6. high_chase reducer",
        "7_lowest_blocked_future_mfe_guard": "7. lowest blocked future_mfe",
        "8_guards_exclude_bad_trades_only": "8. excludes bad trades only?",
        "9_operational_candidate_exists": "9. operational candidate?",
        "10_next_shadow_guard": "10. next shadow guard",
    }
    for key, label in labels.items():
        lines.append(f"- **{label}:** {ma.get(key)}")
    lines.extend(
        [
            "",
            "## Guards tested",
            "",
            "- A: baseline",
            "- G1: ADX14 <= 25",
            "- G2: ADX14 <= 30",
            "- G3: spread <= 40bps",
            "- G4: spread <= 50bps",
            "- G5: update_count <= 3",
            "- G6: update_count <= 5",
            "- G7: ADX<=30 AND spread<=50",
            "- G8: ADX<=30 AND update<=5",
            "- G9: spread<=50 AND update<=5",
            "- G10: ADX<=30 AND spread<=50 AND update<=5",
            "",
            "Research only — no Runtime adoption.",
            "",
        ]
    )
    return "\n".join(lines)
