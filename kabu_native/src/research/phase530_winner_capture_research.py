"""
Phase530 — Winner capture research (ENTRY signal discovery ability).

Evaluates whether ENTRY signals discover rising symbols (not exit/hold profitability).
Research only. No Runtime changes.

Strategies: BASELINE_RUNTIME, O_R003_OR, G3_G4
Period: 20260529 – latest available (replay CAP simulation).
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase382_capital_constrained_backtest import _float, _position_key
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase493_global_entry_failure_audit import PERIOD_START
from research.phase507_classic_strategy_battle import (
    BASELINE_STRATEGY_ID,
    _run_baseline_runtime,
    _simulate_precomputed_cap,
    _universe_symbols,
)
from research.phase509_t15_t13_signal_audit import _build_bar_cache
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
    _percentile,
)
from research.phase520_g3_g4_forward_shadow import _passes_g3_g4
from research.phase522_stop_low_mfe_reentry_overlay_edge_audit import (
    _baseline_trade_rows,
    _day_return_rank,
)
from research.phase523_reentry_definition_overlay_edge_reality_audit import (
    OVERLAY_STRATEGIES,
)
from research.phase480_pbv2_loss_cluster_audit import _mfe_mae_to_exit
from research.phase524_live_reentry_guard_and_stop_low_mfe import _latest_live_day
from research.phase488_current_runtime_replay import _filter_period
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE530_VERDICT = "phase530_winner_capture_research_done"
MAX_WORKERS = 4
STRATEGIES = (BASELINE_STRATEGY_ID, *OVERLAY_STRATEGIES)
MFE_THRESHOLDS = (0.5, 1.0, 2.0, 3.0, 5.0)
TOP_NS = (10, 20, 50)
UNIVERSE_TYPES = (
    "day_return",
    "day_max_mfe",
    "day_high_updates",
)

MFE_HIT_FIELDS = [
    "strategy_id",
    "mfe_threshold_pct",
    "hit_count",
    "hit_rate",
    "trade_count",
    "avg_mfe",
    "median_mfe",
    "p90_mfe",
]

CAPTURE_DETAIL_FIELDS = [
    "day",
    "universe_type",
    "top_n",
    "strategy_id",
    "universe_size",
    "capture_count",
    "capture_rate",
    "effective_capture_count",
    "effective_capture_rate",
    "strong_capture_count",
    "strong_capture_rate",
]

PBV2_MISSED_FIELDS = [
    "classification",
    "trade_count",
    "symbol",
    "day",
    "avg_mfe",
    "median_mfe",
    "avg_return",
]

SCORE_RANKING_FIELDS = [
    "strategy_id",
    "capture_rate",
    "effective_capture_rate",
    "strong_capture_rate",
    "winner_capture_score",
    "rank",
]

SUMMARY_FIELDS = [
    "strategy_id",
    "trade_count",
    "avg_mfe",
    "median_mfe",
    "p90_mfe",
    "mfe_gt_5_hit_rate",
    "day_return_top10_capture_rate",
    "day_return_top10_effective_capture_rate",
    "day_return_top10_strong_capture_rate",
    "winner_capture_score",
]


def _num(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _sym_key(sym: Any) -> str:
    return str(sym or "").replace(".T", "")


def _day_max_mfe_rank(
    price_idx: Mapping,
    universe: Sequence[str],
    day: str,
) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    for sym in universe:
        sym_t = sym if sym.endswith(".T") else f"{sym}.T"
        series = price_idx.get((sym_t, day), [])
        if len(series) < 2:
            continue
        o = float(series[0][1])
        if o <= 0:
            continue
        max_px = max(float(px) for _, px in series)
        rows.append((sym_t.replace(".T", ""), round((max_px - o) / o * 100.0, 4)))
    return sorted(rows, key=lambda x: x[1], reverse=True)


def _day_high_updates_rank(
    bar_cache: Mapping,
    universe: Sequence[str],
    day: str,
) -> list[tuple[str, float]]:
    rows: list[tuple[str, float]] = []
    for sym in universe:
        sym_t = sym if sym.endswith(".T") else f"{sym}.T"
        cached = bar_cache.get((sym_t, day))
        if not cached:
            continue
        bars, _ = cached
        if len(bars) < 2:
            continue
        running = bars[0].high
        updates = 0
        for b in bars[1:]:
            if b.high > running:
                updates += 1
                running = b.high
        rows.append((sym_t.replace(".T", ""), float(updates)))
    return sorted(rows, key=lambda x: x[1], reverse=True)


def _universe_top(
    universe_type: str,
    *,
    price_idx: Mapping,
    bar_cache: Mapping,
    universe: Sequence[str],
    day: str,
    top_n: int,
) -> set[str]:
    if universe_type == "day_return":
        ranked = _day_return_rank(price_idx, universe, day)
    elif universe_type == "day_max_mfe":
        ranked = _day_max_mfe_rank(price_idx, universe, day)
    elif universe_type == "day_high_updates":
        ranked = _day_high_updates_rank(bar_cache, universe, day)
        if top_n == 50 and len(ranked) > 50:
            ranked = ranked[:50]
        if top_n == 20:
            ranked = ranked[: max(top_n, len(ranked))]
    else:
        ranked = []
    return {s for s, _ in ranked[:top_n]}


def _mfe_stats(trades: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    mfes = [_num(t.get("mfe_pct")) for t in trades if t.get("mfe_pct") is not None]
    if not mfes:
        return {"avg_mfe": 0.0, "median_mfe": 0.0, "p90_mfe": 0.0}
    return {
        "avg_mfe": round(statistics.mean(mfes), 4),
        "median_mfe": round(statistics.median(mfes), 4),
        "p90_mfe": round(_percentile(mfes, 90) or 0.0, 4),
    }


def _mfe_hit_rows(trades_by_strategy: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sid, trades in trades_by_strategy.items():
        stats = _mfe_stats(trades)
        n = len(trades)
        mfes = [_num(t.get("mfe_pct")) for t in trades if t.get("mfe_pct") is not None]
        for thr in MFE_THRESHOLDS:
            hits = sum(1 for m in mfes if m > thr)
            rows.append(
                {
                    "strategy_id": sid,
                    "mfe_threshold_pct": thr,
                    "hit_count": hits,
                    "hit_rate": round(hits / n, 4) if n else 0.0,
                    "trade_count": n,
                    **stats,
                }
            )
    return rows


def _capture_for_day(
    *,
    day: str,
    universe_type: str,
    top_n: int,
    strategy_id: str,
    day_trades: Sequence[Mapping[str, Any]],
    universe_syms: set[str],
) -> dict[str, Any]:
    if not universe_syms:
        return {}
    entered_on_univ = [
        t
        for t in day_trades
        if _sym_key(t.get("symbol")) in universe_syms
    ]
    cap_n = len({ _sym_key(t.get("symbol")) for t in entered_on_univ})
    eff_n = sum(1 for t in entered_on_univ if _num(t.get("mfe_pct")) > 1.0)
    strong_n = sum(1 for t in entered_on_univ if _num(t.get("mfe_pct")) > 3.0)
    u = len(universe_syms)
    return {
        "day": day,
        "universe_type": universe_type,
        "top_n": top_n,
        "strategy_id": strategy_id,
        "universe_size": u,
        "capture_count": cap_n,
        "capture_rate": round(cap_n / u, 4),
        "effective_capture_count": eff_n,
        "effective_capture_rate": round(eff_n / u, 4),
        "strong_capture_count": strong_n,
        "strong_capture_rate": round(strong_n / u, 4),
    }


def _run_capture_day_job(
    day: str,
    strategy_id: str,
    trades: Sequence[Mapping[str, Any]],
    *,
    price_idx: Mapping,
    bar_cache: Mapping,
    universe: Sequence[str],
) -> list[dict[str, Any]]:
    day_trades = [t for t in trades if str(t.get("day") or "")[:8] == day]
    rows: list[dict[str, Any]] = []
    for universe_type in UNIVERSE_TYPES:
        top_ns = TOP_NS if universe_type != "day_high_updates" else (10, 20)
        for top_n in top_ns:
            univ = _universe_top(
                universe_type,
                price_idx=price_idx,
                bar_cache=bar_cache,
                universe=universe,
                day=day,
                top_n=top_n,
            )
            row = _capture_for_day(
                day=day,
                universe_type=universe_type,
                top_n=top_n,
                strategy_id=strategy_id,
                day_trades=day_trades,
                universe_syms=univ,
            )
            if row:
                rows.append(row)
    return rows


def _avg_capture(
    detail_rows: Sequence[Mapping[str, Any]],
    *,
    strategy_id: str,
    universe_type: str,
    top_n: int,
    field: str,
) -> float:
    vals = [
        _float(r.get(field))
        for r in detail_rows
        if r.get("strategy_id") == strategy_id
        and r.get("universe_type") == universe_type
        and int(r.get("top_n") or 0) == top_n
    ]
    return round(statistics.mean(vals), 4) if vals else 0.0


def _winner_capture_score(
    detail_rows: Sequence[Mapping[str, Any]],
    strategy_id: str,
) -> float:
    cap = _avg_capture(detail_rows, strategy_id=strategy_id, universe_type="day_return", top_n=10, field="capture_rate")
    eff = _avg_capture(
        detail_rows, strategy_id=strategy_id, universe_type="day_return", top_n=10, field="effective_capture_rate"
    )
    strong = _avg_capture(
        detail_rows, strategy_id=strategy_id, universe_type="day_return", top_n=10, field="strong_capture_rate"
    )
    return round(0.2 * cap + 0.3 * eff + 0.5 * strong, 6)


def _pbv2_missed_rows(
    *,
    days: Sequence[str],
    price_idx: Mapping,
    universe: Sequence[str],
    trades_by_strategy: Mapping[str, Sequence[Mapping[str, Any]]],
    top_n: int = 20,
) -> list[dict[str, Any]]:
    """Classify day-return winners by which strategies entered (A–F)."""
    baseline = trades_by_strategy.get(BASELINE_STRATEGY_ID, [])
    or_trades = trades_by_strategy.get("O_R003_OR", [])
    g3_trades = trades_by_strategy.get("G3_G4", [])

    def _symdays(trades: Sequence[Mapping[str, Any]]) -> set[tuple[str, str]]:
        return {
            (_sym_key(t.get("symbol")), str(t.get("day") or "")[:8])
            for t in trades
        }

    b_sd = _symdays(baseline)
    o_sd = _symdays(or_trades)
    g_sd = _symdays(g3_trades)

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for day in days:
        ranked = _day_return_rank(price_idx, universe, day)
        for sym, ret in ranked[:top_n]:
            key = (sym, day)
            pb = key in b_sd
            o = key in o_sd
            g = key in g_sd
            if pb and o and g:
                cls = "F_all"
            elif pb and o:
                cls = "D_pbv2_or"
            elif pb and g:
                cls = "E_pbv2_g3"
            elif pb:
                cls = "A_pbv2_only"
            elif o:
                cls = "B_or_only"
            elif g:
                cls = "C_g3_only"
            else:
                continue
            mfe_vals = [
                _num(t.get("mfe_pct"))
                for t in (*baseline, *or_trades, *g3_trades)
                if _sym_key(t.get("symbol")) == sym and str(t.get("day") or "")[:8] == day
                and t.get("mfe_pct") is not None
            ]
            buckets[cls].append(
                {
                    "symbol": sym,
                    "day": day,
                    "avg_return": ret,
                    "mfe_pct": statistics.mean(mfe_vals) if mfe_vals else None,
                }
            )

    rows: list[dict[str, Any]] = []
    for cls, items in sorted(buckets.items()):
        mfes = [_float(x.get("mfe_pct")) for x in items if x.get("mfe_pct") is not None]
        rets = [_float(x.get("avg_return")) for x in items]
        rows.append(
            {
                "classification": cls,
                "trade_count": len(items),
                "symbol": ",".join(sorted({str(x.get("symbol")) for x in items})[:5]),
                "day": ",".join(sorted({str(x.get("day")) for x in items})[:5]),
                "avg_mfe": round(statistics.mean(mfes), 4) if mfes else None,
                "median_mfe": round(statistics.median(mfes), 4) if mfes else None,
                "avg_return": round(statistics.mean(rets), 4) if rets else None,
            }
        )
    return rows


def _load_strategies(
    repo_root: Path,
    *,
    price_idx: Mapping,
    parallel: bool,
    workers: int,
) -> dict[str, list[dict[str, Any]]]:
    bar_cache, days = _build_bar_cache(repo_root)
    replay_pool, runtime_shadows, guard_c_block = _prepare_runtime_env(repo_root)
    universe = _universe_symbols(replay_pool)
    micro_lookup = _build_micro_lookup(replay_pool)

    baseline_state, _ = _run_baseline_runtime(repo_root)
    trade_by_key = {_position_key(t): t for t in replay_pool}
    baseline_trades = _baseline_trade_rows(baseline_state, trade_by_key, price_idx)

    pbv2_candidates = _pbv2_precomputed_candidates(replay_pool, runtime_shadows, guard_c_block)
    overlay_def = OVERLAY_DEFS["O_R003"]
    overlay_by: dict[str, list[dict[str, Any]]] = {s: [] for s in OVERLAY_STRATEGIES}
    jobs = [(sid, day) for sid in OVERLAY_STRATEGIES for day in days]

    def _scan(sid: str, day: str) -> tuple[str, list[dict[str, Any]]]:
        raw = _scan_overlay_day(
            overlay_def,
            day=day,
            universe=universe,
            bar_cache=bar_cache,
            price_idx=price_idx,
        )
        if sid == "G3_G4":
            raw = [
                t
                for t in raw
                if _passes_g3_g4(_extract_entry_features(t, bar_cache=bar_cache, micro_lookup=micro_lookup))
            ]
        return sid, raw

    if parallel and jobs:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_scan, sid, day): (sid, day) for sid, day in jobs}
            for fut in as_completed(futs):
                sid, chunk = fut.result()
                overlay_by[sid].extend(chunk)
    else:
        for sid, day in jobs:
            s, chunk = _scan(sid, day)
            overlay_by[s].extend(chunk)

    out: dict[str, list[dict[str, Any]]] = {BASELINE_STRATEGY_ID: baseline_trades}
    for sid in OVERLAY_STRATEGIES:
        merged = _merge_or_candidates(
            pbv2_candidates,
            overlay_by[sid],
            bar_cache=bar_cache,
            overlay=overlay_def,
            guard_c_block=guard_c_block,
        )
        state = _simulate_precomputed_cap(merged, mode=f"phase530_{sid.lower()}")
        rows: list[dict[str, Any]] = []
        for r in _trade_rows_from_state(state, sid):
            pk = str(r.get("position_key") or "")
            src = trade_by_key.get(pk, {})
            mfe, mae = _mfe_mae_to_exit(src or r, price_idx=price_idx, exit_ts_iso=str(r.get("exit_time") or ""))
            rows.append({**dict(r), "strategy_id": sid, "mfe_pct": mfe, "mae_pct": mae})
        out[sid] = rows
    return out


def _mandatory_answers(
    *,
    mfe_rows: Sequence[Mapping[str, Any]],
    score_rows: Sequence[Mapping[str, Any]],
    missed_rows: Sequence[Mapping[str, Any]],
    summary_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    def _best_hit(thr: float, field: str = "hit_rate") -> str:
        rows = [r for r in mfe_rows if _float(r.get("mfe_threshold_pct")) == thr]
        return max(rows, key=lambda r: _float(r.get(field)), default={}).get("strategy_id", "")

    best_score = max(score_rows, key=lambda r: _float(r.get("winner_capture_score")), default={})
    b_only = next((r for r in missed_rows if r.get("classification") == "B_or_only"), {})
    c_only = next((r for r in missed_rows if r.get("classification") == "C_g3_only"), {})
    a_only = next((r for r in missed_rows if r.get("classification") == "A_pbv2_only"), {})

    pbv2 = next((r for r in summary_rows if r.get("strategy_id") == BASELINE_STRATEGY_ID), {})
    or_row = next((r for r in summary_rows if r.get("strategy_id") == "O_R003_OR"), {})
    g3_row = next((r for r in summary_rows if r.get("strategy_id") == "G3_G4"), {})

    or_score = _float(next((r for r in score_rows if r.get("strategy_id") == "O_R003_OR"), {}).get("winner_capture_score"))
    g3_score = _float(next((r for r in score_rows if r.get("strategy_id") == "G3_G4"), {}).get("winner_capture_score"))
    pbv2_score = _float(pbv2.get("winner_capture_score"))

    missed_by_pbv2 = int(b_only.get("trade_count") or 0) + int(c_only.get("trade_count") or 0)

    return {
        "1_best_mfe_hit_rate_strategy": _best_hit(0.5),
        "2_best_mfe_gt5_strategy": _best_hit(5.0),
        "3_best_winner_capture_score_strategy": best_score.get("strategy_id"),
        "3_best_winner_capture_score": best_score.get("winner_capture_score"),
        "4_pbv2_missed_rising_count": missed_by_pbv2,
        "5_or_unique_rising_count": int(b_only.get("trade_count") or 0),
        "6_g3_unique_rising_count": int(c_only.get("trade_count") or 0),
        "7_or_is_rising_discoverer": or_score > pbv2_score and _float(or_row.get("day_return_top10_capture_rate")) > _float(pbv2.get("day_return_top10_capture_rate")),
        "8_g3_is_rising_discoverer": g3_score > pbv2_score and _float(g3_row.get("day_return_top10_capture_rate")) > _float(pbv2.get("day_return_top10_capture_rate")),
        "9_pbv2_discovery_insufficient": pbv2_score < max(or_score, g3_score),
        "10_pbv2_improvement_room": missed_by_pbv2 > 0,
        "11_next_research_candidate": (
            "O_R003_OR"
            if or_score >= g3_score and or_score > pbv2_score
            else "G3_G4"
            if g3_score > pbv2_score
            else "PBv2_entry_expansion"
        ),
        "pbv2_only_rising_count": int(a_only.get("trade_count") or 0),
    }


@dataclass
class Phase530Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        workers = min(max(1, self.max_workers), MAX_WORKERS)
        kabu = resolve_kabu_root(self.repo_root)
        period_end = _latest_live_day(self.repo_root)
        price_idx = _build_price_index_to(kabu, period_end=period_end)
        bar_cache, days = _build_bar_cache(self.repo_root)
        days = [d for d in days if d >= PERIOD_START and d <= period_end]
        replay_pool, _, _ = _prepare_runtime_env(self.repo_root)
        universe = _universe_symbols(_filter_period(replay_pool, start=PERIOD_START, end=period_end))

        trades_by_strategy = _load_strategies(
            self.repo_root, price_idx=price_idx, parallel=self.parallel, workers=workers
        )

        mfe_hit_rows = _mfe_hit_rows(trades_by_strategy)

        capture_jobs = [(day, sid) for day in days for sid in STRATEGIES]
        capture_detail: list[dict[str, Any]] = []

        def _cap_job(day: str, sid: str) -> list[dict[str, Any]]:
            return _run_capture_day_job(
                day,
                sid,
                trades_by_strategy.get(sid, []),
                price_idx=price_idx,
                bar_cache=bar_cache,
                universe=universe,
            )

        if self.parallel and capture_jobs:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futs = {ex.submit(_cap_job, day, sid): (day, sid) for day, sid in capture_jobs}
                for fut in as_completed(futs):
                    capture_detail.extend(fut.result())
        else:
            for day, sid in capture_jobs:
                capture_detail.extend(_cap_job(day, sid))

        pbv2_missed = _pbv2_missed_rows(
            days=days,
            price_idx=price_idx,
            universe=universe,
            trades_by_strategy=trades_by_strategy,
            top_n=20,
        )

        score_rows: list[dict[str, Any]] = []
        for sid in STRATEGIES:
            score = _winner_capture_score(capture_detail, sid)
            score_rows.append(
                {
                    "strategy_id": sid,
                    "capture_rate": _avg_capture(
                        capture_detail, strategy_id=sid, universe_type="day_return", top_n=10, field="capture_rate"
                    ),
                    "effective_capture_rate": _avg_capture(
                        capture_detail,
                        strategy_id=sid,
                        universe_type="day_return",
                        top_n=10,
                        field="effective_capture_rate",
                    ),
                    "strong_capture_rate": _avg_capture(
                        capture_detail,
                        strategy_id=sid,
                        universe_type="day_return",
                        top_n=10,
                        field="strong_capture_rate",
                    ),
                    "winner_capture_score": score,
                }
            )
        score_rows.sort(key=lambda r: _float(r.get("winner_capture_score")), reverse=True)
        for i, row in enumerate(score_rows, start=1):
            row["rank"] = i

        summary_rows: list[dict[str, Any]] = []
        for sid in STRATEGIES:
            trades = trades_by_strategy.get(sid, [])
            stats = _mfe_stats(trades)
            mfe5 = next(
                (r for r in mfe_hit_rows if r.get("strategy_id") == sid and _float(r.get("mfe_threshold_pct")) == 5.0),
                {},
            )
            summary_rows.append(
                {
                    "strategy_id": sid,
                    "trade_count": len(trades),
                    **stats,
                    "mfe_gt_5_hit_rate": mfe5.get("hit_rate"),
                    "day_return_top10_capture_rate": _avg_capture(
                        capture_detail, strategy_id=sid, universe_type="day_return", top_n=10, field="capture_rate"
                    ),
                    "day_return_top10_effective_capture_rate": _avg_capture(
                        capture_detail,
                        strategy_id=sid,
                        universe_type="day_return",
                        top_n=10,
                        field="effective_capture_rate",
                    ),
                    "day_return_top10_strong_capture_rate": _avg_capture(
                        capture_detail,
                        strategy_id=sid,
                        universe_type="day_return",
                        top_n=10,
                        field="strong_capture_rate",
                    ),
                    "winner_capture_score": _winner_capture_score(capture_detail, sid),
                }
            )

        mandatory = _mandatory_answers(
            mfe_rows=mfe_hit_rows,
            score_rows=score_rows,
            missed_rows=pbv2_missed,
            summary_rows=summary_rows,
        )

        return {
            "verdict": PHASE530_VERDICT,
            "generated_at": _now_iso(),
            "period_start": PERIOD_START,
            "period_end": period_end,
            "includes_20260624": "20260624" in days,
            "parallel_workers": workers,
            "days_count": len(days),
            "summary": summary_rows,
            "mfe_hit_rate": mfe_hit_rows,
            "capture_detail": capture_detail,
            "pbv2_missed_winners": pbv2_missed,
            "capture_score_ranking": score_rows,
            "mandatory_answers": mandatory,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "summary": reports / "phase530_winner_capture_summary.csv",
            "mfe_hit": reports / "phase530_mfe_hit_rate.csv",
            "detail": reports / "phase530_winner_capture_detail.csv",
            "missed": reports / "phase530_pbv2_missed_winners.csv",
            "ranking": reports / "phase530_capture_score_ranking.csv",
            "report": reports / "phase530_report.json",
            "docs": kabu / "docs" / "operations" / "phase530_winner_capture_research.md",
        }
        _write_csv(paths["summary"], SUMMARY_FIELDS, list(result.get("summary") or []))
        _write_csv(paths["mfe_hit"], MFE_HIT_FIELDS, list(result.get("mfe_hit_rate") or []))
        _write_csv(paths["detail"], CAPTURE_DETAIL_FIELDS, list(result.get("capture_detail") or []))
        _write_csv(paths["missed"], PBV2_MISSED_FIELDS, list(result.get("pbv2_missed_winners") or []))
        _write_csv(paths["ranking"], SCORE_RANKING_FIELDS, list(result.get("capture_score_ranking") or []))
        paths["report"].write_text(
            json.dumps(
                {k: v for k, v in result.items() if k not in ("capture_detail",)},
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
        "# Phase530 — Winner Capture Research",
        "",
        f"**Verdict:** `{result.get('verdict')}`",
        f"**Period:** {result.get('period_start')} – {result.get('period_end')}",
        f"**Includes 20260624:** {result.get('includes_20260624')}",
        "",
        "## Mandatory answers",
        "",
    ]
    for i in range(1, 12):
        key = [k for k in ma if k.startswith(f"{i}_")]
        for k in sorted(key):
            lines.append(f"- **{k}:** {ma.get(k)}")
    lines.extend(
        [
            "",
            "## Strategies",
            "",
            "- BASELINE_RUNTIME (PBv2)",
            "- O_R003_OR",
            "- G3_G4",
            "",
            "Research only — no Runtime adoption.",
            "",
        ]
    )
    return "\n".join(lines)
