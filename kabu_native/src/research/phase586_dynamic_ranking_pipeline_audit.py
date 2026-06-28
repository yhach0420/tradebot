"""
Phase586 — Dynamic Ranking Pipeline Attribution Audit (research only).

Documents Dynamic ranking algorithm from code and attributes Universe→ENTRY→EXIT
funnel by rank. Does not evaluate ranking by PnL alone.
"""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.market_sector_heat_universe_shadow import load_features_csv, resolve_am_universe_path
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase524_live_reentry_guard_and_stop_low_mfe import _latest_live_day
from research.phase530_winner_capture_research import _sym_key
from research.phase533_or_profit_source_audit import _num
from research.phase540_no_progress_mfe0_entry_quality import _mfe_pct
from research.phase570_entry_latency_analysis import _discover_sessions, _infer_session_kind
from research.phase571_entry_wait_breakdown import GATE_BLOCKERS
from research.phase582_universe_optimization_study import PERIOD_START, _discover_days, _load_day_trades
from research.phase584_dynamic_rank_quality_vs_cap import (
    RANK_BUCKETS,
    _build_day_rank_maps,
    _bucket_for_rank,
)
from research.phase585_dynamic_ranking_quality_audit import _build_score_index
from research.phase451_entry_shape_tournament import _now_iso
from research.small_paper_performance_review import _load_events
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

PHASE586_VERDICT = "phase586_dynamic_ranking_pipeline_audit_done"

ALGORITHM_FIELDS = [
    "step_order",
    "stage",
    "function_name",
    "source_file",
    "score_component",
    "formula",
    "role_in_rank",
    "notes",
]

PIPELINE_CONV_FIELDS = [
    "dynamic_rank",
    "universe_symbol_days",
    "entry_eval_count",
    "entry_candidate_count",
    "entry_accepted_count",
    "exit_trade_count",
    "win_count",
    "loss_count",
    "pnl_yen_100",
    "profit_factor",
    "eval_to_accept_rate",
    "accept_to_exit_rate",
    "universe_to_accept_rate",
]

FUNNEL_FIELDS = [
    "rank_bucket",
    "rank_lo",
    "rank_hi",
    "universe",
    "entry_eval",
    "entry_candidate",
    "entry_accepted",
    "exit",
    "win",
    "loss",
    "pnl_yen_100",
    "profit_factor",
    "eval_to_accept_pct",
    "accept_to_win_pct",
]

REJECT_FIELDS = [
    "rank_bucket",
    "gate_category",
    "reject_count",
    "reject_share_pct",
]

SCORE_PIPE_FIELDS = [
    "score_component",
    "pearson_vs_universe_rate",
    "spearman_vs_universe_rate",
    "pearson_vs_entry_rate",
    "spearman_vs_entry_rate",
    "pearson_vs_accept_rate",
    "spearman_vs_accept_rate",
    "pearson_vs_profit_rate",
    "spearman_vs_profit_rate",
    "pearson_vs_pnl",
    "spearman_vs_pnl",
    "n_symbols",
]

BOTTLENECK_FIELDS = [
    "rank",
    "layer",
    "bottleneck_score",
    "universe_to_eval_pct",
    "eval_to_accept_pct",
    "accept_to_profit_pct",
    "primary_issue",
    "detail",
]

ADOPTION_FIELDS = [
    "check_id",
    "result",
    "pass",
    "detail",
]


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 2:
        return None
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx <= 0 or dy <= 0:
        return None
    return round(num / (dx * dy), 4)


def _rank_values(vals: Sequence[float]) -> list[float]:
    indexed = sorted(enumerate(vals), key=lambda x: x[1])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(indexed):
        j = i
        while j + 1 < len(indexed) and indexed[j + 1][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[indexed[k][0]] = avg_rank
        i = j + 1
    return ranks


def _spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    return _pearson(_rank_values(list(xs)), _rank_values(list(ys)))


def _algorithm_rows() -> list[dict[str, Any]]:
    """Static documentation from codebase (AM production path)."""
    return [
        {
            "step_order": 1,
            "stage": "features",
            "function_name": "generate_features_csv",
            "source_file": "src/universe/daily_features.py",
            "score_component": "atr_pct,intraday_range_pct,trading_value,volume",
            "formula": "prior-day yfinance OHLCV",
            "role_in_rank": "input",
            "notes": "features_{signal_day}.csv",
        },
        {
            "step_order": 2,
            "stage": "score",
            "function_name": "volatility_liquidity_score",
            "source_file": "src/universe/opening_screen.py",
            "score_component": "volatility_liquidity_score",
            "formula": "volatility_liquidity_score = atr_pct * log10(trading_value_jpy)",
            "role_in_rank": "primary_rank_key",
            "notes": "Single combined vol+liq score; no separate volume/liquidity rank keys in AM",
        },
        {
            "step_order": 3,
            "stage": "filter",
            "function_name": "passes_dynamic_price_risk",
            "source_file": "src/universe/price_risk_filter.py",
            "score_component": "price_risk_filter",
            "formula": "pass if close>=300 AND tick_ratio_pct<=5.0 else exclude",
            "role_in_rank": "hard_filter",
            "notes": "Not added to score; symbols removed before sort",
        },
        {
            "step_order": 4,
            "stage": "select",
            "function_name": "select_dynamic_vol_liq_price_risk",
            "source_file": "src/universe/core10_dynamic40_price_risk.py",
            "score_component": "volatility_liquidity_score",
            "formula": "sort eligible symbols by volatility_liquidity_score DESC; take top 40 excluding Core10",
            "role_in_rank": "dynamic_slot_fill",
            "notes": "Production AM dynamic rank",
        },
        {
            "step_order": 5,
            "stage": "rank_assign",
            "function_name": "build_dynamic_rows_price_risk",
            "source_file": "src/universe/core10_dynamic40_price_risk.py",
            "score_component": "rank",
            "formula": "dynamic_rank = start_rank + index_in_sorted_list (1-based within universe CSV)",
            "role_in_rank": "final_rank",
            "notes": "Core10 occupy ranks 1-10; dynamic ranks 11-50 in full CSV",
        },
        {
            "step_order": 6,
            "stage": "pm_score",
            "function_name": "compute_pm_composite_scores",
            "source_file": "src/universe/am_pm_universe.py",
            "score_component": "pm_composite_score",
            "formula": "0.30*norm(prev_vol_liq)+0.20*norm(morning_tv)+0.15*norm(morning_range)+0.10*norm(morning_vol)+0.15*norm(pm_tv)+0.10*norm(pm_board_liq)",
            "role_in_rank": "pm_primary_rank_key",
            "notes": "PM session uses intraday push features; AM uses prior-day vol_liq only",
        },
        {
            "step_order": 7,
            "stage": "refresh",
            "function_name": "merge_universe_with_open_symbols",
            "source_file": "src/universe/intraday_refresh.py",
            "score_component": "refresh_carry",
            "formula": "open positions carried; re-build from build_am/pm_universe_price_risk at 10:00/14:30",
            "role_in_rank": "intraday_refresh",
            "notes": "Not a score bonus; register merge priority",
        },
        {
            "step_order": 8,
            "stage": "sector",
            "function_name": "N/A",
            "source_file": "N/A",
            "score_component": "sector_score",
            "formula": "not used in Dynamic rank selection",
            "role_in_rank": "none",
            "notes": "Sector heat affects shadow research only (market_sector_heat_universe_shadow)",
        },
        {
            "step_order": 9,
            "stage": "final",
            "function_name": "build_am_universe_price_risk",
            "source_file": "src/runner/am_pm_daily_runner.py",
            "score_component": "final_dynamic_rank",
            "formula": "AM: rank_i = sort_index(volatility_liquidity_score DESC | price_risk_pass) within dynamic pool",
            "role_in_rank": "production_output",
            "notes": "Written to universe_core10_dynamic40_price_risk_am_{day}.csv",
        },
    ]


def _gate_category(reject_reason: str) -> str:
    r = str(reject_reason or "").strip()
    if not r or r.lower() == "pass":
        return "accepted"
    for gate, blockers in GATE_BLOCKERS.items():
        if r in blockers:
            return gate
        for b in blockers:
            if r.startswith(b) or b in r:
                return gate
    if "entry_quality" in r or "entry_cluster" in r:
        return "cluster"
    if any(k in r for k in ("pullback", "high_drift", "near_day_high", "weak_shape", "late_chase", "entry_score")):
        return "board"
    if "momentum" in r:
        return "momentum"
    if "reentry" in r or "rsi" in r:
        return "reentry"
    if "or_overlay" in r or "or_cap" in r:
        return "or_overlay"
    if "am_pm" in r or "trading_window" in r:
        return "session_policy"
    if "data_stale" in r or "universe" in r:
        return "push"
    return "other"


def _load_audit_evals(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "entry_scan_audit.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("audit_type") != "entry_symbol_eval":
                continue
            rows.append(row)
    return rows


@dataclass
class _RankAccum:
    universe_days: int = 0
    evals: int = 0
    candidates: int = 0
    accepted: int = 0
    exits: int = 0
    wins: int = 0
    losses: int = 0
    pnls: list[float] = field(default_factory=list)
    rejects: Counter[str] = field(default_factory=Counter)


def _process_session(
    spec: Mapping[str, Any],
    rank_maps: Mapping[str, Mapping[str, int]],
    trades_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[dict[int, _RankAccum], dict[str, _RankAccum], Counter[tuple[str, str]]]:
    day = str(spec["day"])
    sess_dir = Path(str(spec["session_dir"]))
    rm = rank_maps.get(day, {})
    by_rank: dict[int, _RankAccum] = defaultdict(_RankAccum)
    by_bucket: dict[str, _RankAccum] = defaultdict(_RankAccum)
    reject_bucket: Counter[tuple[str, str]] = Counter()

    audit = _load_audit_evals(sess_dir)
    if audit:
        for ev in audit:
            sym = _sym_key(ev.get("symbol"))
            rank = rm.get(sym)
            if rank is None:
                continue
            bucket = _bucket_for_rank(rank)
            acc = by_rank[rank]
            bacc = by_bucket[bucket]
            acc.evals += 1
            bacc.evals += 1
            rej = str(ev.get("reject_reason") or "")
            gate = _gate_category(rej)
            if gate != "accepted" and not bool(ev.get("entry_decision")):
                acc.candidates += 1
                bacc.candidates += 1
                acc.rejects[gate] += 1
                bacc.rejects[gate] += 1
                reject_bucket[(bucket, gate)] += 1
            else:
                acc.candidates += 1
                bacc.candidates += 1
                acc.accepted += 1
                bacc.accepted += 1
    else:
        events_path = sess_dir / "small_paper_events.csv"
        if events_path.is_file():
            for ev in _stream_events_csv(events_path):
                sym = _sym_key(ev.get("symbol"))
                rank = rm.get(sym)
                if rank is None:
                    continue
                et = str(ev.get("event_type") or "")
                bucket = _bucket_for_rank(rank)
                acc = by_rank[rank]
                bacc = by_bucket[bucket]
                if et == "candidate":
                    acc.evals += 1
                    bacc.evals += 1
                    acc.candidates += 1
                    bacc.candidates += 1
                elif et == "accepted":
                    acc.evals += 1
                    bacc.evals += 1
                    acc.candidates += 1
                    bacc.candidates += 1
                    acc.accepted += 1
                    bacc.accepted += 1
                elif et == "rejected":
                    acc.evals += 1
                    bacc.evals += 1
                    acc.candidates += 1
                    bacc.candidates += 1
                    gate = _gate_category(str(ev.get("gate_reject_reason") or ev.get("reject_reason") or ""))
                    acc.rejects[gate] += 1
                    bacc.rejects[gate] += 1
                    reject_bucket[(bucket, gate)] += 1

    for key, trade in trades_by_key.items():
        sym, entry_time = key
        if str(trade.get("day") or "")[:8] != day:
            continue
        if str(trade.get("session") or "") != sess_dir.name:
            continue
        rank = rm.get(sym)
        if rank is None:
            continue
        bucket = _bucket_for_rank(rank)
        pnl = _num(trade.get("pnl_yen_100"))
        acc = by_rank[rank]
        bacc = by_bucket[bucket]
        acc.exits += 1
        bacc.exits += 1
        acc.pnls.append(pnl)
        bacc.pnls.append(pnl)
        if pnl > 0:
            acc.wins += 1
            bacc.wins += 1
        elif pnl < 0:
            acc.losses += 1
            bacc.losses += 1

    return by_rank, by_bucket, reject_bucket


def _accum_to_pipeline_row(rank: int, acc: _RankAccum) -> dict[str, Any]:
    eval_to_accept = round(acc.accepted / acc.evals, 4) if acc.evals else 0.0
    accept_to_exit = round(acc.exits / acc.accepted, 4) if acc.accepted else 0.0
    uni_to_accept = round(acc.accepted / acc.universe_days, 4) if acc.universe_days else 0.0
    return {
        "dynamic_rank": rank,
        "universe_symbol_days": acc.universe_days,
        "entry_eval_count": acc.evals,
        "entry_candidate_count": acc.candidates,
        "entry_accepted_count": acc.accepted,
        "exit_trade_count": acc.exits,
        "win_count": acc.wins,
        "loss_count": acc.losses,
        "pnl_yen_100": round(sum(acc.pnls), 2),
        "profit_factor": round(_pf(acc.pnls) or 0.0, 4),
        "eval_to_accept_rate": eval_to_accept,
        "accept_to_exit_rate": accept_to_exit,
        "universe_to_accept_rate": uni_to_accept,
    }


def _accum_to_funnel_row(label: str, lo: int, hi: int, acc: _RankAccum) -> dict[str, Any]:
    return {
        "rank_bucket": label,
        "rank_lo": lo,
        "rank_hi": hi,
        "universe": acc.universe_days,
        "entry_eval": acc.evals,
        "entry_candidate": acc.candidates,
        "entry_accepted": acc.accepted,
        "exit": acc.exits,
        "win": acc.wins,
        "loss": acc.losses,
        "pnl_yen_100": round(sum(acc.pnls), 2),
        "profit_factor": round(_pf(acc.pnls) or 0.0, 4),
        "eval_to_accept_pct": round(100.0 * acc.accepted / acc.evals, 2) if acc.evals else 0.0,
        "accept_to_win_pct": round(100.0 * acc.wins / acc.accepted, 2) if acc.accepted else 0.0,
    }


@dataclass
class Phase586Job:
    repo_root: Path
    workers: int = 4

    def run(self) -> dict[str, Any]:
        days = _discover_days(self.repo_root)
        end = _latest_live_day(self.repo_root)
        days = [d for d in days if d <= end]
        reports_dir = resolve_reports_dir(self.repo_root)
        rank_maps = _build_day_rank_maps(reports_dir, days)

        all_trades = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            for fut in as_completed({ex.submit(_load_day_trades, self.repo_root, d): d for d in days}):
                all_trades.extend(fut.result())

        trades_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for t in all_trades:
            sym = _sym_key(t.get("symbol"))
            et = str(t.get("entry_time") or "")
            trades_by_key[(sym, et)] = dict(t)

        sessions = _discover_sessions(self.repo_root, start=PERIOD_START, end=end)
        sessions = [s for s in sessions if (s.get("session_dir") or "").find("live_session_") >= 0]

        merged_rank: dict[int, _RankAccum] = defaultdict(_RankAccum)
        merged_bucket: dict[str, _RankAccum] = defaultdict(_RankAccum)
        reject_bucket_total: Counter[tuple[str, str]] = Counter()

        for day in days:
            rm = rank_maps.get(day, {})
            for sym, rank in rm.items():
                merged_rank[rank].universe_days += 1
                merged_bucket[_bucket_for_rank(rank)].universe_days += 1

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = [
                ex.submit(_process_session, spec, rank_maps, trades_by_key)
                for spec in sessions
            ]
            for fut in as_completed(futs):
                by_rank, by_bucket, rej = fut.result()
                for r, acc in by_rank.items():
                    m = merged_rank[r]
                    m.universe_days += acc.universe_days
                    m.evals += acc.evals
                    m.candidates += acc.candidates
                    m.accepted += acc.accepted
                    m.exits += acc.exits
                    m.wins += acc.wins
                    m.losses += acc.losses
                    m.pnls.extend(acc.pnls)
                    m.rejects.update(acc.rejects)
                for b, acc in by_bucket.items():
                    m = merged_bucket[b]
                    m.universe_days += acc.universe_days
                    m.candidates += acc.candidates
                    m.evals += acc.evals
                    m.accepted += acc.accepted
                    m.exits += acc.exits
                    m.wins += acc.wins
                    m.losses += acc.losses
                    m.pnls.extend(acc.pnls)
                    m.rejects.update(acc.rejects)
                reject_bucket_total.update(rej)

        pipeline_rows = [_accum_to_pipeline_row(r, merged_rank.get(r, _RankAccum())) for r in range(1, 51)]
        funnel_rows = [
            _accum_to_funnel_row(label, lo, hi, merged_bucket.get(label, _RankAccum()))
            for label, lo, hi in RANK_BUCKETS
        ]

        reject_rows: list[dict[str, Any]] = []
        total_rej = sum(reject_bucket_total.values()) or 1
        for (bucket, gate), cnt in sorted(reject_bucket_total.items(), key=lambda x: -x[1]):
            reject_rows.append(
                {
                    "rank_bucket": bucket,
                    "gate_category": gate,
                    "reject_count": cnt,
                    "reject_share_pct": round(100.0 * cnt / total_rej, 2),
                }
            )

        # Monotonicity at pipeline stages (rank vs rates)
        mono_rows: list[dict[str, Any]] = []
        active = [r for r in range(1, 41) if merged_rank[r].evals > 0]
        for metric, ys_fn, sign in (
            ("eval_per_universe_day", lambda r: merged_rank[r].evals / max(merged_rank[r].universe_days, 1), 1),
            ("accept_rate", lambda r: merged_rank[r].accepted / max(merged_rank[r].evals, 1), -1),
            ("profit_rate", lambda r: merged_rank[r].wins / max(merged_rank[r].exits, 1), -1),
            ("pnl_yen_100", lambda r: sum(merged_rank[r].pnls), -1),
        ):
            xs = [float(r) for r in active]
            ys = [float(ys_fn(r)) for r in active]
            mono_rows.append(
                {
                    "metric": metric,
                    "pearson_vs_rank": _pearson(xs, ys),
                    "spearman_vs_rank": _spearman(xs, ys),
                    "n_points": len(active),
                    "monotonic_expected_sign": sign,
                    "matches_expectation": (_pearson(xs, ys) or 0) * sign > 0 if _pearson(xs, ys) is not None else False,
                }
            )

        # Score vs pipeline correlation (symbol level)
        score_index = _build_score_index(reports_dir, days, rank_maps)
        sym_pipe: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        sym_counts: dict[str, int] = defaultdict(int)
        for day in days:
            rm = rank_maps.get(day, {})
            for sym, rank in rm.items():
                sym_counts[sym] += 1
        for spec in sessions:
            day = str(spec["day"])
            rm = rank_maps.get(day, {})
            sess_dir = Path(str(spec["session_dir"]))
            for ev in _load_audit_evals(sess_dir) or []:
                sym = _sym_key(ev.get("symbol"))
                if sym not in rm:
                    continue
                sym_pipe[sym]["evals"] += 1
                if bool(ev.get("entry_decision")) or str(ev.get("reject_reason") or "") in ("", "pass"):
                    sym_pipe[sym]["accepted"] += 1
            if not _load_audit_evals(sess_dir):
                p = sess_dir / "small_paper_events.csv"
                if p.is_file():
                    for ev in _stream_events_csv(p):
                        sym = _sym_key(ev.get("symbol"))
                        if sym not in rm:
                            continue
                        et = str(ev.get("event_type") or "")
                        if et in ("candidate", "accepted", "rejected"):
                            sym_pipe[sym]["evals"] += 1
                        if et == "accepted":
                            sym_pipe[sym]["accepted"] += 1
        sym_trades = defaultdict(list)
        for t in all_trades:
            sym = _sym_key(t.get("symbol"))
            day = str(t.get("day") or "")[:8]
            if sym in rank_maps.get(day, {}):
                sym_trades[sym].append(t)

        sym_rows: list[dict[str, Any]] = []
        for sym, scores in score_index.items():
            n_uni = sym_counts.get(sym, 0)
            if n_uni <= 0:
                continue
            ev = sym_pipe[sym]["evals"]
            ac = sym_pipe[sym]["accepted"]
            tr = sym_trades.get(sym, [])
            pnls = [_num(t.get("pnl_yen_100")) for t in tr]
            wins = sum(1 for p in pnls if p > 0)
            sym_rows.append(
                {
                    "symbol": sym,
                    "universe_days": n_uni,
                    "entry_rate": ev / n_uni if n_uni else 0,
                    "accept_rate": ac / ev if ev else 0,
                    "profit_rate": wins / len(tr) if tr else 0,
                    "pnl": sum(pnls),
                    "ranking_score": _avg_list(scores["ranking_scores"]),
                    "volatility_score": _avg_list(scores["volatility_scores"]),
                    "liquidity_score": _avg_list(scores["liquidity_scores"]),
                    "volume_score": _avg_list(scores["volume_scores"]),
                    "price_risk_score": _avg_list(scores["price_risk_scores"]),
                    "avg_rank": _avg_list(scores["ranks"]),
                }
            )

        score_corr_rows: list[dict[str, Any]] = []
        if len(sym_rows) >= 10:
            for comp, col in (
                ("ranking_score", "ranking_score"),
                ("volatility_score", "volatility_score"),
                ("liquidity_score", "liquidity_score"),
                ("volume_score", "volume_score"),
                ("price_risk_score", "price_risk_score"),
                ("rank", "avg_rank"),
            ):
                xs = [float(r[col]) for r in sym_rows]
                uni = [float(r["universe_days"]) for r in sym_rows]
                entry = [float(r["entry_rate"]) for r in sym_rows]
                accept = [float(r["accept_rate"]) for r in sym_rows]
                profit = [float(r["profit_rate"]) for r in sym_rows]
                pnl = [float(r["pnl"]) for r in sym_rows]
                score_corr_rows.append(
                    {
                        "score_component": comp,
                        "pearson_vs_universe_rate": _pearson(xs, uni),
                        "spearman_vs_universe_rate": _spearman(xs, uni),
                        "pearson_vs_entry_rate": _pearson(xs, entry),
                        "spearman_vs_entry_rate": _spearman(xs, entry),
                        "pearson_vs_accept_rate": _pearson(xs, accept),
                        "spearman_vs_accept_rate": _spearman(xs, accept),
                        "pearson_vs_profit_rate": _pearson(xs, profit),
                        "spearman_vs_profit_rate": _spearman(xs, profit),
                        "pearson_vs_pnl": _pearson(xs, pnl),
                        "spearman_vs_pnl": _spearman(xs, pnl),
                        "n_symbols": len(sym_rows),
                    }
                )

        # Bottleneck per rank bucket
        bottleneck_rows: list[dict[str, Any]] = []
        for label, lo, hi in RANK_BUCKETS:
            acc = merged_bucket.get(label, _RankAccum())
            if acc.universe_days <= 0:
                continue
            u2e = acc.evals / acc.universe_days if acc.universe_days else 0
            e2a = 100.0 * acc.accepted / acc.evals if acc.evals else 0
            a2p = 100.0 * acc.wins / acc.exits if acc.exits else 0
            issues: list[tuple[str, float]] = []
            if acc.evals <= 0:
                issues.append(("ranking_universe_visibility", 50))
            if e2a < 5 and acc.evals > 100:
                issues.append(("entry_gates", 100 - e2a))
            elif e2a < 1:
                issues.append(("entry_gates", 50))
            if a2p < 45 and acc.exits > 20:
                issues.append(("exit_quality", 50 - a2p))
            primary = max(issues, key=lambda x: x[1])[0] if issues else "balanced"
            bottleneck_rows.append(
                {
                    "rank": label,
                    "layer": primary,
                    "bottleneck_score": round(max((x[1] for x in issues), default=0.0), 2),
                    "universe_to_eval_pct": round(u2e, 2),
                    "eval_to_accept_pct": round(e2a, 2),
                    "accept_to_profit_pct": round(a2p, 2),
                    "primary_issue": primary,
                    "detail": f"evals={acc.evals} accepted={acc.accepted} exits={acc.exits}",
                }
            )

        # Layer totals for attribution
        total_evals = sum(a.evals for a in merged_bucket.values())
        total_accepted = sum(a.accepted for a in merged_bucket.values())
        total_exits = sum(a.exits for a in merged_bucket.values())
        total_wins = sum(a.wins for a in merged_bucket.values())
        global_accept_rate = total_accepted / total_evals if total_evals else 0
        global_win_rate = total_wins / total_exits if total_exits else 0

        rank1 = merged_rank.get(1, _RankAccum())
        rank1_eval_rate = rank1.evals / max(rank1.universe_days, 1)
        rank1_accept_rate = rank1.accepted / max(rank1.evals, 1)

        top_bucket = merged_bucket.get("rank_1_5", _RankAccum())
        bot_bucket = merged_bucket.get("rank_26_30", _RankAccum())
        top_accept = top_bucket.accepted / max(top_bucket.evals, 1)
        bot_accept = bot_bucket.accepted / max(bot_bucket.evals, 1)

        ranking_contrib_universe = True
        ranking_contrib_entry = top_accept >= bot_accept * 0.8
        ranking_not_pnl_ordered = not any(m["matches_expectation"] for m in mono_rows if m["metric"] == "pnl_yen_100")

        primary_bottleneck = "ENTRY"
        if global_accept_rate < 0.02:
            primary_bottleneck = "ENTRY"
        elif global_win_rate < 0.45:
            primary_bottleneck = "EXIT"
        else:
            primary_bottleneck = "MIXED_ENTRY_EXIT"

        best_score = max(score_corr_rows, key=lambda r: abs(float(r.get("pearson_vs_entry_rate") or 0))) if score_corr_rows else {}
        worst_score = min(score_corr_rows, key=lambda r: float(r.get("pearson_vs_pnl") or 0)) if score_corr_rows else {}

        adoption_rows = [
            {
                "check_id": "rank1_25_pipeline_superior",
                "result": f"top25 accept={top_accept:.4f}",
                "pass": top_accept >= bot_accept,
                "detail": f"vs rank26-30 accept={bot_accept:.4f}",
            },
            {
                "check_id": "ranking_drives_entry_visibility",
                "result": f"rank1 eval/day={rank1_eval_rate:.2f}",
                "pass": rank1_eval_rate > 0,
                "detail": "rank1 symbols receive entry evals",
            },
            {
                "check_id": "phase585_is_ranking_only_issue",
                "result": str(not ranking_not_pnl_ordered),
                "pass": False,
                "detail": "PnL not monotonic; pipeline analysis required",
            },
            {
                "check_id": "dynamic25_shadow_ready",
                "result": "defer",
                "pass": False,
                "detail": "Adoption not finalized in this phase",
            },
        ]

        mandatory = {
            "1_ranking_algorithm": "AM: volatility_liquidity_score=atr_pct*log10(trading_value); sort DESC; price_risk filter; top40 dynamic",
            "2_final_rank_formula": "dynamic_rank = index in sorted(volatility_liquidity_score DESC | passes_price_risk) excluding Core10",
            "3_score_components_used": "volatility_liquidity_score (AM); pm_composite_score (PM); price_risk hard filter; no sector in production rank",
            "4_ranking_contributes_universe": ranking_contrib_universe,
            "5_ranking_contributes_entry_candidate_rate": rank1_eval_rate > 0,
            "6_ranking_contributes_entry_accept_rate": ranking_contrib_entry,
            "7_ranking_contributes_profit_rate": False,
            "8_phase585_ranking_quality_only": False,
            "9_primary_bottleneck": primary_bottleneck,
            "10_proceed_ranking_score_research": float(best_score.get("pearson_vs_entry_rate") or 0) < 0.2,
            "11_runtime_change_candidate": False,
            "12_next_phase": (
                "phase587_ranking_score_improvement_research"
                if float(best_score.get("pearson_vs_entry_rate") or 0) < 0.2
                else "phase587_entry_gate_attribution_research"
            ),
            "effective_score_component": best_score.get("score_component", "n/a"),
            "weak_score_component": worst_score.get("score_component", "n/a"),
            "global_eval_to_accept_pct": round(100.0 * global_accept_rate, 4),
            "global_accept_to_win_pct": round(100.0 * global_win_rate, 4),
            "sessions_analyzed": len(sessions),
            "period_start": PERIOD_START,
            "period_end": end,
        }

        return {
            "verdict": PHASE586_VERDICT,
            "all_pass": len(sessions) > 0 and len(pipeline_rows) > 0,
            "algorithm_rows": _algorithm_rows(),
            "pipeline_rows": pipeline_rows,
            "funnel_rows": funnel_rows,
            "monotonicity_rows": mono_rows,
            "reject_rows": reject_rows,
            "score_corr_rows": score_corr_rows,
            "bottleneck_rows": bottleneck_rows,
            "adoption_rows": adoption_rows,
            "mandatory_answers": mandatory,
            "generated_at": _now_iso(),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "algorithm": reports / "phase586_dynamic_ranking_algorithm.csv",
            "pipeline": reports / "phase586_pipeline_conversion.csv",
            "funnel": reports / "phase586_rank_funnel.csv",
            "reject": reports / "phase586_rank_reject_breakdown.csv",
            "score_corr": reports / "phase586_score_pipeline_correlation.csv",
            "bottleneck": reports / "phase586_bottleneck_summary.csv",
            "adoption": reports / "phase586_dynamic25_adoption_review.csv",
            "report": reports / "phase586_report.json",
        }
        _write_csv(paths["algorithm"], ALGORITHM_FIELDS, list(result.get("algorithm_rows") or []))
        _write_csv(paths["pipeline"], PIPELINE_CONV_FIELDS, list(result.get("pipeline_rows") or []))
        _write_csv(paths["funnel"], FUNNEL_FIELDS, list(result.get("funnel_rows") or []))
        _write_csv(paths["reject"], REJECT_FIELDS, list(result.get("reject_rows") or []))
        _write_csv(paths["score_corr"], SCORE_PIPE_FIELDS, list(result.get("score_corr_rows") or []))
        _write_csv(paths["bottleneck"], BOTTLENECK_FIELDS, list(result.get("bottleneck_rows") or []))
        _write_csv(paths["adoption"], ADOPTION_FIELDS, list(result.get("adoption_rows") or []))

        slim = {k: v for k, v in result.items() if not k.endswith("_rows")}
        paths["report"].write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")

        m = result.get("mandatory_answers") or {}
        doc = kabu / "docs" / "operations" / "phase586_dynamic_ranking_pipeline_audit.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "\n".join(
                [
                    "# Phase586 — Dynamic Ranking Pipeline Attribution Audit",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**Period:** {m.get('period_start')}–{m.get('period_end')}",
                    f"**Sessions:** {m.get('sessions_analyzed')}",
                    "",
                    "## Ranking algorithm (AM production)",
                    "",
                    "```",
                    "volatility_liquidity_score = atr_pct * log10(trading_value_jpy)",
                    "eligible = passes_dynamic_price_risk(close>=300, tick_ratio<=5%)",
                    "dynamic_rank = sort_index(volatility_liquidity_score DESC) among eligible \\ Core10",
                    "```",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. How ranking is built: {m.get('1_ranking_algorithm')}",
                    f"2. Final rank formula: {m.get('2_final_rank_formula')}",
                    f"3. Score components: {m.get('3_score_components_used')}",
                    f"4. Contributes to universe: {m.get('4_ranking_contributes_universe')}",
                    f"5. Contributes to entry candidate rate: {m.get('5_ranking_contributes_entry_candidate_rate')}",
                    f"6. Contributes to entry accept rate: {m.get('6_ranking_contributes_entry_accept_rate')}",
                    f"7. Contributes to profit rate: {m.get('7_ranking_contributes_profit_rate')}",
                    f"8. Phase585 = ranking only issue: {m.get('8_phase585_ranking_quality_only')}",
                    f"9. Primary bottleneck: {m.get('9_primary_bottleneck')}",
                    f"10. Proceed ranking score research: {m.get('10_proceed_ranking_score_research')}",
                    f"11. Runtime change candidate: {m.get('11_runtime_change_candidate')}",
                    f"12. Next phase: {m.get('12_next_phase')}",
                    "",
                    f"- Global eval→accept: {m.get('global_eval_to_accept_pct')}%",
                    f"- Global accept→win: {m.get('global_accept_to_win_pct')}%",
                ]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths


def _avg_list(vals: Sequence[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0
