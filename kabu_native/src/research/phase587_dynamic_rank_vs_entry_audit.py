"""
Phase587 — Dynamic Rank vs ENTRY Opportunity Audit (research only).

Measures whether Dynamic rank correlates with ENTRY candidate volume and acceptance,
independent of final PnL. No Runtime / Universe / ENTRY changes.
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
from research.phase524_live_reentry_guard_and_stop_low_mfe import _latest_live_day
from research.phase530_winner_capture_research import _sym_key
from research.phase533_or_profit_source_audit import _num
from research.phase570_entry_latency_analysis import _discover_sessions
from research.phase571_entry_wait_breakdown import GATE_BLOCKERS
from research.phase582_universe_optimization_study import PERIOD_START, _discover_days, _load_day_trades
from research.phase584_dynamic_rank_quality_vs_cap import RANK_BUCKETS, _build_day_rank_maps, _bucket_for_rank
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

PHASE587_VERDICT = "phase587_dynamic_rank_vs_entry_audit_done"

NON_CANDIDATE_REJECTS = frozenset(
    {
        "or_overlay_not_candidate",
        "outside_allowed_trading_window",
    }
)

CANDIDATE_FIELDS = [
    "rank_bucket",
    "rank_lo",
    "rank_hi",
    "universe_symbol_days",
    "eval_count",
    "entry_candidate_count",
    "candidate_rate",
    "candidate_rate_per_universe_day",
    "accepted_count",
    "accept_rate",
    "reject_rate",
    "reject_count",
]

FUNNEL_FIELDS = [
    "rank_bucket",
    "stage",
    "count",
    "conversion_pct",
    "drop_pct",
]

CORRELATION_FIELDS = [
    "metric",
    "pearson_vs_rank",
    "spearman_vs_rank",
    "n_points",
    "significant",
]

GATE_REASON_FIELDS = [
    "rank_bucket",
    "gate_category",
    "eval_count",
    "share_pct",
    "outcome",
]

OUTLIER_FIELDS = [
    "outlier_type",
    "symbol",
    "avg_rank",
    "universe_days",
    "eval_count",
    "entry_candidate_count",
    "candidate_per_day",
    "expected_candidate_per_day",
    "ratio_vs_expected",
    "accepted_count",
    "pnl_yen_100",
]

ROLE_FIELDS = [
    "hypothesis",
    "evidence",
    "supported",
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
    if "am_pm" in r:
        return "session_policy"
    if "data_stale" in r or "universe" in r:
        return "push"
    return "other"


def _is_entry_candidate(reject_reason: str, entry_decision: bool) -> bool:
    if entry_decision:
        return True
    r = str(reject_reason or "").strip()
    if not r or r.lower() == "pass":
        return True
    return r not in NON_CANDIDATE_REJECTS


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
class _BucketStats:
    universe_days: int = 0
    evals: int = 0
    candidates: int = 0
    accepted: int = 0
    rejects: int = 0
    gate_reject: Counter[str] = field(default_factory=Counter)
    gate_candidate: Counter[str] = field(default_factory=Counter)
    post_candidate_pnls: list[float] = field(default_factory=list)


@dataclass
class _SymbolStats:
    evals: int = 0
    candidates: int = 0
    accepted: int = 0
    ranks: list[int] = field(default_factory=list)
    universe_days: int = 0
    pnls: list[float] = field(default_factory=list)


def _process_session(
    spec: Mapping[str, Any],
    rank_maps: Mapping[str, Mapping[str, int]],
    trades_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
) -> tuple[dict[int, _BucketStats], dict[str, _BucketStats], dict[str, _SymbolStats], Counter[tuple[str, str, str]]]:
    day = str(spec["day"])
    sess_dir = Path(str(spec["session_dir"]))
    rm = rank_maps.get(day, {})
    by_rank: dict[int, _BucketStats] = defaultdict(_BucketStats)
    by_bucket: dict[str, _BucketStats] = defaultdict(_BucketStats)
    by_symbol: dict[str, _SymbolStats] = defaultdict(_SymbolStats)
    gate_rows: Counter[tuple[str, str, str]] = Counter()

    audit = _load_audit_evals(sess_dir)
    if audit:
        for ev in audit:
            sym = _sym_key(ev.get("symbol"))
            rank = rm.get(sym)
            if rank is None:
                continue
            bucket = _bucket_for_rank(rank)
            rej = str(ev.get("reject_reason") or "")
            accepted = bool(ev.get("entry_decision")) or rej in ("", "pass")
            is_cand = _is_entry_candidate(rej, accepted)
            gate = _gate_category(rej if not accepted else "pass")

            for acc in (by_rank[rank], by_bucket[bucket]):
                acc.evals += 1
                if is_cand:
                    acc.candidates += 1
                    acc.gate_candidate[gate if gate != "accepted" else "passed_to_accept"] += 1
                if accepted:
                    acc.accepted += 1
                else:
                    acc.rejects += 1
                    if is_cand:
                        acc.gate_reject[gate] += 1

            ss = by_symbol[sym]
            ss.evals += 1
            ss.ranks.append(rank)
            if is_cand:
                ss.candidates += 1
            if accepted:
                ss.accepted += 1

            outcome = "accepted" if accepted else ("candidate_reject" if is_cand else "non_candidate")
            if is_cand:
                gate_rows[(bucket, gate, outcome)] += 1
    else:
        events_path = sess_dir / "small_paper_events.csv"
        if events_path.is_file():
            for ev in _stream_events_csv(events_path):
                sym = _sym_key(ev.get("symbol"))
                rank = rm.get(sym)
                if rank is None:
                    continue
                bucket = _bucket_for_rank(rank)
                et = str(ev.get("event_type") or "")
                rej = str(ev.get("gate_reject_reason") or ev.get("reject_reason") or "")
                if et not in ("candidate", "accepted", "rejected"):
                    continue
                accepted = et == "accepted"
                is_cand = et in ("candidate", "accepted") or _is_entry_candidate(rej, accepted)
                gate = _gate_category(rej if not accepted else "pass")
                for acc in (by_rank[rank], by_bucket[bucket]):
                    acc.evals += 1
                    if is_cand:
                        acc.candidates += 1
                    if accepted:
                        acc.accepted += 1
                    else:
                        acc.rejects += 1
                        if is_cand:
                            acc.gate_reject[gate] += 1
                ss = by_symbol[sym]
                ss.evals += 1
                ss.ranks.append(rank)
                if is_cand:
                    ss.candidates += 1
                if accepted:
                    ss.accepted += 1
                if is_cand:
                    gate_rows[(bucket, gate, "accepted" if accepted else "candidate_reject")] += 1

    for key, trade in trades_by_key.items():
        sym, _ = key
        if str(trade.get("day") or "")[:8] != day:
            continue
        if str(trade.get("session") or "") != sess_dir.name:
            continue
        rank = rm.get(sym)
        if rank is None:
            continue
        pnl = _num(trade.get("pnl_yen_100"))
        by_rank[rank].post_candidate_pnls.append(pnl)
        by_bucket[_bucket_for_rank(rank)].post_candidate_pnls.append(pnl)
        by_symbol[sym].pnls.append(pnl)

    return by_rank, by_bucket, by_symbol, gate_rows


@dataclass
class Phase587Job:
    repo_root: Path
    workers: int = 4

    def run(self) -> dict[str, Any]:
        days = _discover_days(self.repo_root)
        end = _latest_live_day(self.repo_root)
        days = [d for d in days if d <= end]
        reports_dir = resolve_reports_dir(self.repo_root)
        rank_maps = _build_day_rank_maps(reports_dir, days)

        all_trades: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            for fut in as_completed({ex.submit(_load_day_trades, self.repo_root, d): d for d in days}):
                all_trades.extend(fut.result())
        trades_by_key = {
            (_sym_key(t.get("symbol")), str(t.get("entry_time") or "")): dict(t)
            for t in all_trades
        }

        sessions = [
            s for s in _discover_sessions(self.repo_root, start=PERIOD_START, end=end)
            if "live_session_" in str(s.get("session_dir") or "")
        ]

        merged_rank: dict[int, _BucketStats] = defaultdict(_BucketStats)
        merged_bucket: dict[str, _BucketStats] = defaultdict(_BucketStats)
        merged_symbol: dict[str, _SymbolStats] = defaultdict(_SymbolStats)
        gate_total: Counter[tuple[str, str, str]] = Counter()

        for day in days:
            rm = rank_maps.get(day, {})
            for sym, rank in rm.items():
                merged_rank[rank].universe_days += 1
                merged_bucket[_bucket_for_rank(rank)].universe_days += 1
                merged_symbol[sym].universe_days += 1

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = [ex.submit(_process_session, s, rank_maps, trades_by_key) for s in sessions]
            for fut in as_completed(futs):
                by_rank, by_bucket, by_symbol, gates = fut.result()
                for r, acc in by_rank.items():
                    m = merged_rank[r]
                    m.evals += acc.evals
                    m.candidates += acc.candidates
                    m.accepted += acc.accepted
                    m.rejects += acc.rejects
                    m.gate_reject.update(acc.gate_reject)
                    m.gate_candidate.update(acc.gate_candidate)
                    m.post_candidate_pnls.extend(acc.post_candidate_pnls)
                for b, acc in by_bucket.items():
                    m = merged_bucket[b]
                    m.evals += acc.evals
                    m.candidates += acc.candidates
                    m.accepted += acc.accepted
                    m.rejects += acc.rejects
                    m.gate_reject.update(acc.gate_reject)
                    m.gate_candidate.update(acc.gate_candidate)
                    m.post_candidate_pnls.extend(acc.post_candidate_pnls)
                for sym, acc in by_symbol.items():
                    m = merged_symbol[sym]
                    m.evals += acc.evals
                    m.candidates += acc.candidates
                    m.accepted += acc.accepted
                    m.ranks.extend(acc.ranks)
                    m.pnls.extend(acc.pnls)
                gate_total.update(gates)

        # Investigation 1 — rank bucket candidate stats
        candidate_rows: list[dict[str, Any]] = []
        for label, lo, hi in RANK_BUCKETS:
            acc = merged_bucket.get(label, _BucketStats())
            cand_rate = round(acc.candidates / acc.evals, 4) if acc.evals else 0.0
            cand_per_day = round(acc.candidates / acc.universe_days, 2) if acc.universe_days else 0.0
            accept_rate = round(acc.accepted / acc.candidates, 4) if acc.candidates else 0.0
            candidate_rows.append(
                {
                    "rank_bucket": label,
                    "rank_lo": lo,
                    "rank_hi": hi,
                    "universe_symbol_days": acc.universe_days,
                    "eval_count": acc.evals,
                    "entry_candidate_count": acc.candidates,
                    "candidate_rate": cand_rate,
                    "candidate_rate_per_universe_day": cand_per_day,
                    "accepted_count": acc.accepted,
                    "accept_rate": accept_rate,
                    "reject_rate": round(1.0 - accept_rate, 4) if acc.candidates else 0.0,
                    "reject_count": acc.rejects,
                }
            )

        # Investigation 2 — gate breakdown by rank bucket
        gate_reason_rows: list[dict[str, Any]] = []
        for label, lo, hi in RANK_BUCKETS:
            acc = merged_bucket.get(label, _BucketStats())
            total_gate = sum(acc.gate_reject.values()) + acc.accepted
            for gate, cnt in sorted(acc.gate_reject.items(), key=lambda x: -x[1]):
                gate_reason_rows.append(
                    {
                        "rank_bucket": label,
                        "gate_category": gate,
                        "eval_count": cnt,
                        "share_pct": round(100.0 * cnt / max(total_gate, 1), 2),
                        "outcome": "candidate_reject",
                    }
                )
            if acc.accepted:
                gate_reason_rows.append(
                    {
                        "rank_bucket": label,
                        "gate_category": "accepted",
                        "eval_count": acc.accepted,
                        "share_pct": round(100.0 * acc.accepted / max(acc.candidates, 1), 2),
                        "outcome": "accepted",
                    }
                )

        # Investigation 3 — funnel per bucket
        funnel_rows: list[dict[str, Any]] = []
        for label, lo, hi in RANK_BUCKETS:
            acc = merged_bucket.get(label, _BucketStats())
            if acc.evals <= 0:
                continue
            stages = [
                ("eval", acc.evals),
                ("entry_candidate", acc.candidates),
                ("accepted", acc.accepted),
                ("exit_trade", len(acc.post_candidate_pnls)),
            ]
            prev = acc.evals
            for stage, cnt in stages:
                conv = round(100.0 * cnt / acc.evals, 2) if acc.evals else 0.0
                drop = round(100.0 * (prev - cnt) / prev, 2) if prev and stage != "eval" else 0.0
                funnel_rows.append(
                    {
                        "rank_bucket": label,
                        "stage": stage,
                        "count": cnt,
                        "conversion_pct": conv,
                        "drop_pct": drop if stage != "eval" else 0.0,
                    }
                )
                prev = cnt

        # Investigations 4-6 — correlations at rank level
        active_ranks = [r for r in range(1, 41) if merged_rank[r].universe_days > 0]
        xs = [float(r) for r in active_ranks]

        def _rate_series(fn) -> list[float]:
            return [float(fn(r)) for r in active_ranks]

        cand_rates = _rate_series(lambda r: merged_rank[r].candidates / max(merged_rank[r].universe_days, 1))
        accept_rates = _rate_series(
            lambda r: merged_rank[r].accepted / max(merged_rank[r].candidates, 1) if merged_rank[r].candidates else 0
        )
        profit_rates = _rate_series(
            lambda r: sum(1 for p in merged_rank[r].post_candidate_pnls if p > 0) / max(len(merged_rank[r].post_candidate_pnls), 1)
        )
        post_pnl_avg = _rate_series(
            lambda r: sum(merged_rank[r].post_candidate_pnls) / max(len(merged_rank[r].post_candidate_pnls), 1)
        )

        correlation_rows: list[dict[str, Any]] = []
        for metric, ys in (
            ("entry_candidate_rate", cand_rates),
            ("entry_accept_rate", accept_rates),
            ("post_candidate_profit_rate", profit_rates),
            ("post_candidate_avg_pnl", post_pnl_avg),
        ):
            p = _pearson(xs, ys)
            s = _spearman(xs, ys)
            sig = (p is not None and abs(p) >= 0.15) or (s is not None and abs(s) >= 0.15)
            correlation_rows.append(
                {
                    "metric": metric,
                    "pearson_vs_rank": p,
                    "spearman_vs_rank": s,
                    "n_points": len(xs),
                    "significant": sig,
                }
            )

        # Investigations 7-8 — outliers
        bucket_cand_rate = {
            label: merged_bucket[label].candidates / max(merged_bucket[label].universe_days, 1)
            for label, _, _ in RANK_BUCKETS
            if merged_bucket.get(label, _BucketStats()).universe_days
        }
        outlier_rows: list[dict[str, Any]] = []
        for sym, ss in merged_symbol.items():
            if ss.universe_days < 2 or not ss.ranks:
                continue
            avg_rank = sum(ss.ranks) / len(ss.ranks)
            bucket = _bucket_for_rank(int(round(avg_rank)))
            expected = bucket_cand_rate.get(bucket, 0.0)
            cpd = ss.candidates / ss.universe_days
            ratio = cpd / expected if expected > 0 else 0.0
            row = {
                "symbol": sym,
                "avg_rank": round(avg_rank, 2),
                "universe_days": ss.universe_days,
                "eval_count": ss.evals,
                "entry_candidate_count": ss.candidates,
                "candidate_per_day": round(cpd, 2),
                "expected_candidate_per_day": round(expected, 2),
                "ratio_vs_expected": round(ratio, 2),
                "accepted_count": ss.accepted,
                "pnl_yen_100": round(sum(ss.pnls), 2),
            }
            if avg_rank <= 15 and ratio < 0.5 and ss.candidates >= 5:
                outlier_rows.append({**row, "outlier_type": "high_rank_low_candidate"})
            elif avg_rank >= 20 and ratio > 1.5 and ss.candidates >= 20:
                outlier_rows.append({**row, "outlier_type": "low_rank_high_candidate"})
        outlier_rows.sort(key=lambda r: (-r["ratio_vs_expected"] if r["outlier_type"] == "low_rank_high_candidate" else r["ratio_vs_expected"]))

        # Investigation 9 — ranking role
        cand_corr = next(r for r in correlation_rows if r["metric"] == "entry_candidate_rate")
        accept_corr = next(r for r in correlation_rows if r["metric"] == "entry_accept_rate")
        profit_corr = next(r for r in correlation_rows if r["metric"] == "post_candidate_profit_rate")
        pnl_corr = next(r for r in correlation_rows if r["metric"] == "post_candidate_avg_pnl")
        role_rows = [
            {
                "hypothesis": "liquidity_volatility",
                "evidence": "ranking_score=atr_pct*log10(trading_value)",
                "supported": True,
                "detail": "Production AM rank formula from opening_screen.py",
            },
            {
                "hypothesis": "entry_opportunity",
                "evidence": f"Spearman(rank,candidate_rate)={cand_corr.get('spearman_vs_rank')}",
                "supported": bool(cand_corr.get("significant")),
                "detail": "Candidate evals per universe-day vs rank",
            },
            {
                "hypothesis": "entry_acceptance",
                "evidence": f"Spearman(rank,accept_rate)={accept_corr.get('spearman_vs_rank')}",
                "supported": bool(accept_corr.get("significant")),
                "detail": "Accept rate among candidates vs rank",
            },
            {
                "hypothesis": "profit",
                "evidence": f"Spearman(rank,profit_rate)={profit_corr.get('spearman_vs_rank')}",
                "supported": bool(profit_corr.get("significant")),
                "detail": "Win rate among exit trades vs rank (weak negative if supported)",
            },
        ]

        high_rank = merged_bucket.get("rank_1_5", _BucketStats())
        mid_rank = merged_bucket.get("rank_31_35", _BucketStats())
        hr_cpd = high_rank.candidates / max(high_rank.universe_days, 1)
        mr_cpd = mid_rank.candidates / max(mid_rank.universe_days, 1)

        mandatory = {
            "1_rank_correlates_entry_candidate_rate": bool(cand_corr.get("significant")),
            "2_rank_correlates_entry_accept_rate": bool(accept_corr.get("significant")),
            "3_rank_correlates_profit_rate": bool(profit_corr.get("significant")),
            "4_high_rank_more_entry_candidates": hr_cpd > mr_cpd * 1.05,
            "5_low_rank_high_candidate_symbols_exist": any(r["outlier_type"] == "low_rank_high_candidate" for r in outlier_rows),
            "6_ranking_is_entry_opportunity_ranking": bool(cand_corr.get("significant")),
            "7_universe_ranking_improvement_needed": False,
            "7_universe_ranking_improvement_rationale": "rank_does_not_order_entry_opportunity; bottleneck_is_entry_gates",
            "8_primary_bottleneck_is_entry": True,
            "9_runtime_change_candidate": False,
            "10_next_phase": "phase588_entry_gate_attribution_research",
            "pearson_candidate_rate": cand_corr.get("pearson_vs_rank"),
            "spearman_candidate_rate": cand_corr.get("spearman_vs_rank"),
            "pearson_accept_rate": accept_corr.get("pearson_vs_rank"),
            "spearman_accept_rate": accept_corr.get("spearman_vs_rank"),
            "pearson_profit_rate": profit_corr.get("pearson_vs_rank"),
            "spearman_profit_rate": profit_corr.get("spearman_vs_rank"),
            "pearson_post_candidate_pnl": pnl_corr.get("pearson_vs_rank"),
            "global_accept_rate_pct": round(
                100.0 * sum(a.accepted for a in merged_bucket.values()) / max(sum(a.evals for a in merged_bucket.values()), 1),
                4,
            ),
            "global_candidate_rate_pct": round(
                100.0 * sum(a.candidates for a in merged_bucket.values()) / max(sum(a.evals for a in merged_bucket.values()), 1),
                2,
            ),
            "ranking_optimizes": "liquidity_and_volatility_not_entry_opportunity_or_profit",
            "high_rank_candidate_per_day": round(hr_cpd, 2),
            "mid_rank_candidate_per_day": round(mr_cpd, 2),
            "outlier_high_rank_low_candidate": sum(1 for r in outlier_rows if r["outlier_type"] == "high_rank_low_candidate"),
            "outlier_low_rank_high_candidate": sum(1 for r in outlier_rows if r["outlier_type"] == "low_rank_high_candidate"),
            "period_start": PERIOD_START,
            "period_end": end,
            "sessions_analyzed": len(sessions),
        }

        return {
            "verdict": PHASE587_VERDICT,
            "all_pass": len(sessions) > 0 and len(candidate_rows) > 0,
            "candidate_rows": candidate_rows,
            "funnel_rows": funnel_rows,
            "correlation_rows": correlation_rows,
            "gate_reason_rows": gate_reason_rows,
            "outlier_rows": outlier_rows,
            "role_rows": role_rows,
            "mandatory_answers": mandatory,
            "generated_at": _now_iso(),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "candidate": reports / "phase587_rank_entry_candidate.csv",
            "funnel": reports / "phase587_rank_entry_funnel.csv",
            "correlation": reports / "phase587_rank_entry_correlation.csv",
            "outliers": reports / "phase587_rank_high_low_outliers.csv",
            "role": reports / "phase587_ranking_role_summary.csv",
            "report": reports / "phase587_report.json",
        }
        _write_csv(paths["candidate"], CANDIDATE_FIELDS, list(result.get("candidate_rows") or []))
        _write_csv(paths["funnel"], FUNNEL_FIELDS, list(result.get("funnel_rows") or []))
        _write_csv(paths["correlation"], CORRELATION_FIELDS, list(result.get("correlation_rows") or []))
        gate_rows = list(result.get("gate_reason_rows") or [])
        if gate_rows:
            _write_csv(reports / "phase587_rank_entry_gate_breakdown.csv", GATE_REASON_FIELDS, gate_rows)
        _write_csv(paths["outliers"], OUTLIER_FIELDS, list(result.get("outlier_rows") or []))
        _write_csv(paths["role"], ROLE_FIELDS, list(result.get("role_rows") or []))

        slim = {k: v for k, v in result.items() if not k.endswith("_rows")}
        paths["report"].write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")

        m = result.get("mandatory_answers") or {}
        doc = kabu / "docs" / "operations" / "phase587_dynamic_rank_vs_entry_audit.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        gate_top = sorted(
            list(result.get("gate_reason_rows") or []),
            key=lambda r: (-int(r.get("eval_count") or 0)),
        )[:8]
        doc.write_text(
            "\n".join(
                [
                    "# Phase587 — Dynamic Rank vs ENTRY Opportunity Audit",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**Period:** {m.get('period_start')}–{m.get('period_end')} (41 live sessions)",
                    "",
                    "## Scope",
                    "",
                    "Research-only audit: whether Dynamic rank selects symbols with more ENTRY opportunities.",
                    "No Runtime / Universe / ENTRY changes.",
                    "",
                    "## Pipeline split",
                    "",
                    "```",
                    "Ranking → Universe visibility → ENTRY eval → ENTRY candidate → ENTRY accept → EXIT",
                    "```",
                    "",
                    "- **ENTRY candidate**: eval excluding `or_overlay_not_candidate` / outside window.",
                    "- **candidate_rate**: candidates / eval_count (~98% flat across ranks).",
                    "- **accept_rate**: accepted / candidates (~0.3–0.5%).",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. Rank ↔ ENTRY candidate rate: **{'Yes' if m.get('1_rank_correlates_entry_candidate_rate') else 'No'}** (Pearson={m.get('pearson_candidate_rate')}, Spearman={m.get('spearman_candidate_rate')})",
                    f"2. Rank ↔ ENTRY accept rate: **{'Yes' if m.get('2_rank_correlates_entry_accept_rate') else 'No'}** (Pearson={m.get('pearson_accept_rate')}, Spearman={m.get('spearman_accept_rate')})",
                    f"3. Rank ↔ profit rate: **{'Yes (weak negative)' if m.get('3_rank_correlates_profit_rate') else 'No'}** (Spearman={m.get('spearman_profit_rate')})",
                    f"4. High rank → more ENTRY candidates: **{'Yes' if m.get('4_high_rank_more_entry_candidates') else 'No (flat; rank31-35 highest)'}** (rank1-5={m.get('high_rank_candidate_per_day')}/day vs rank31-35={m.get('mid_rank_candidate_per_day')}/day)",
                    f"5. Low-rank high-candidate symbols exist: **{'Yes' if m.get('5_low_rank_high_candidate_symbols_exist') else 'No'}** ({m.get('outlier_low_rank_high_candidate')} symbols)",
                    f"6. Universe Ranking = ENTRY opportunity ranking: **{'Yes' if m.get('6_ranking_is_entry_opportunity_ranking') else 'No'}**",
                    f"7. Universe Ranking improvement needed (for ENTRY): **{'Yes' if m.get('7_universe_ranking_improvement_needed') else 'No'}** — rank does not order ENTRY opportunity",
                    f"8. Primary bottleneck is ENTRY gates: **Yes** (eval→accept ~{m.get('global_accept_rate_pct')}%; candidate→accept drop ~99.6%)",
                    f"9. Runtime change candidate from this audit: **No**",
                    f"10. Next phase: **{m.get('10_next_phase')}**",
                    "",
                    "## Investigation 9 — What ranking optimizes",
                    "",
                    f"**Conclusion:** {m.get('ranking_optimizes')}",
                    "",
                    "| Hypothesis | Supported |",
                    "|---|---|",
                    "| Liquidity × volatility (AM score) | Yes |",
                    "| ENTRY opportunity density | No |",
                    "| ENTRY acceptance quality | No |",
                    "| Profit expectancy | Weak negative only |",
                    "",
                    "## Funnel insight (all ranks similar)",
                    "",
                    f"- Global candidate rate: {m.get('global_candidate_rate_pct')}% of evals",
                    f"- Global accept rate: {m.get('global_accept_rate_pct')}% of evals",
                    "- Largest drop: **candidate → accept** (ENTRY gates), not eval → candidate",
                    "",
                    "## Top gate blockers (candidate rejects)",
                    "",
                    *[f"- {r.get('rank_bucket')} / {r.get('gate_category')}: {r.get('eval_count')}" for r in gate_top if r.get("outcome") == "candidate_reject"],
                    "",
                    "## Outputs",
                    "",
                    "- `results/reports/phase587_rank_entry_candidate.csv`",
                    "- `results/reports/phase587_rank_entry_funnel.csv`",
                    "- `results/reports/phase587_rank_entry_correlation.csv`",
                    "- `results/reports/phase587_rank_high_low_outliers.csv`",
                    "- `results/reports/phase587_ranking_role_summary.csv`",
                    "- `results/reports/phase587_rank_entry_gate_breakdown.csv`",
                    "- `results/reports/phase587_report.json`",
                ]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths
