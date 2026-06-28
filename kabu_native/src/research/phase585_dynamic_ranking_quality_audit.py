"""
Phase585 — Dynamic Ranking Quality Audit (research only).

Audits whether Dynamic rank 1-25 reflects expectancy order and whether ranking
score components correlate with realized PnL. No Runtime / Universe / ranking changes.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.market_sector_heat_universe_shadow import (
    dynamic_rank_map_from_universe,
    load_features_csv,
    load_universe_csv,
    resolve_am_universe_path,
)
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase451_entry_shape_tournament import JST, _now_iso
from research.phase524_live_reentry_guard_and_stop_low_mfe import _is_stop_low_mfe, _latest_live_day
from research.phase530_winner_capture_research import _sym_key
from research.phase533_or_profit_source_audit import _num
from research.phase540_no_progress_mfe0_entry_quality import _is_mfe0, _mfe_pct
from research.phase541_guard_v2_full_period_validation import BIG_WINNER_MFE_PCT
from research.phase582_universe_optimization_study import (
    PERIOD_START,
    _discover_days,
    _load_day_accepted,
    _load_day_trades,
    _price_band,
)
from research.phase584_dynamic_rank_quality_vs_cap import (
    CAP,
    RANK_BUCKETS,
    _build_day_core_sets,
    _build_day_rank_maps,
    _bucket_for_rank,
    _load_session_accepted_candidates,
    _norm_rank,
    _simulate_cap,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE585_VERDICT = "phase585_dynamic_ranking_quality_audit_done"
BIG_LOSER_PNL = -3000.0

RANK_PERF_FIELDS = [
    "rank",
    "trades",
    "accepted",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "avg_pnl_yen_100",
    "median_pnl_yen_100",
    "mfe_avg",
    "mfe0_count",
    "stop_hit_count",
    "stop_low_mfe_count",
    "big_winner_count",
    "big_loser_count",
    "expectancy_yen_100",
]

BUCKET_PERF_FIELDS = [
    "rank_bucket",
    "rank_lo",
    "rank_hi",
    "trades",
    "accepted",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "avg_pnl_yen_100",
    "median_pnl_yen_100",
    "mfe_avg",
    "mfe0_count",
    "stop_hit_count",
    "stop_low_mfe_count",
    "big_winner_count",
    "big_loser_count",
    "expectancy_yen_100",
]

MONOTONICITY_FIELDS = [
    "metric",
    "pearson_vs_rank",
    "spearman_vs_rank",
    "n_points",
    "monotonic_expected_sign",
    "matches_expectation",
]

WEAK_SYMBOL_FIELDS = [
    "symbol",
    "avg_rank",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "stop_low_mfe_rate",
    "mfe0_rate",
    "big_loser_count",
    "flags",
]

STRONG_SYMBOL_FIELDS = [
    "symbol",
    "avg_rank",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "avg_pnl_yen_100",
    "stop_low_mfe_rate",
    "big_winner_count",
    "flags",
]

SCORE_CORR_FIELDS = [
    "score_component",
    "pearson_vs_pnl",
    "spearman_vs_pnl",
    "pearson_vs_pf",
    "spearman_vs_pf",
    "pearson_vs_expectancy",
    "n_symbols",
    "effective",
]

REPLACEMENT_SIM_FIELDS = [
    "scenario",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "max_drawdown_yen_100",
    "win_rate",
    "top3_pnl_share_pct",
    "high_price_pnl_share_pct",
    "daily_positive_rate",
    "cap_competition_blocks",
    "delta_pnl_vs_top25_original",
    "delta_pf_vs_top25_original",
]

ADOPTION_FIELDS = [
    "criterion",
    "result",
    "detail",
    "pass",
]


def _is_big_winner(trade: Mapping[str, Any]) -> bool:
    return _num(trade.get("pnl_yen_100")) > 0 and _mfe_pct(trade) >= BIG_WINNER_MFE_PCT


def _is_big_loser(trade: Mapping[str, Any]) -> bool:
    return _num(trade.get("pnl_yen_100")) <= BIG_LOSER_PNL


def _enrich_trade(trade: Mapping[str, Any], rank_maps: Mapping[str, Mapping[str, int]], core_sets: Mapping[str, set[str]]) -> dict[str, Any]:
    row = dict(trade)
    day = str(row.get("day") or "")[:8]
    sym = _sym_key(row.get("symbol"))
    core = core_sets.get(day, set())
    if sym in core:
        row["dynamic_rank"] = None
        row["is_dynamic"] = False
    else:
        row["dynamic_rank"] = _norm_rank(sym, rank_maps.get(day, {}))
        row["is_dynamic"] = row["dynamic_rank"] is not None
    row["rank_bucket"] = _bucket_for_rank(row.get("dynamic_rank"))
    px = _num(row.get("entry_price") or row.get("price") or 0)
    row["price_band"] = _price_band(px)
    row["mfe_pct_val"] = _mfe_pct(row)
    return row


def _trade_metrics(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_num(t.get("pnl_yen_100")) for t in trades]
    mfes = [_num(t.get("mfe_pct_val")) for t in trades]
    n = len(pnls)
    total = round(sum(pnls), 2)
    wins = sum(1 for p in pnls if p > 0)
    ordered = sorted(
        trades,
        key=lambda t: _parse_ts(str(t.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
    )
    chron_cum: list[float] = []
    c = 0.0
    for t in ordered:
        c += _num(t.get("pnl_yen_100"))
        chron_cum.append(c)
    peak = 0.0
    max_dd = 0.0
    for v in chron_cum:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)
    sym_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        sym_pnl[_sym_key(t.get("symbol"))] += _num(t.get("pnl_yen_100"))
    ranked_sym = sorted(sym_pnl.values(), reverse=True)
    top3 = sum(ranked_sym[:3])
    high_price = sum(
        _num(t.get("pnl_yen_100"))
        for t in trades
        if str(t.get("price_band") or "") in ("5000_10000", "gte_10000")
    )
    daily: dict[str, float] = defaultdict(float)
    for t in trades:
        daily[str(t.get("day") or "")[:8]] += _num(t.get("pnl_yen_100"))
    pos_days = sum(1 for v in daily.values() if v > 0)
    return {
        "trades": n,
        "pnl_yen_100": total,
        "profit_factor": round(_pf(pnls) or 0.0, 4),
        "win_rate": round(wins / n, 4) if n else 0.0,
        "avg_pnl_yen_100": round(total / n, 2) if n else 0.0,
        "median_pnl_yen_100": round(statistics.median(pnls), 2) if pnls else 0.0,
        "mfe_avg": round(sum(mfes) / n, 4) if n else 0.0,
        "mfe0_count": sum(1 for t in trades if _is_mfe0(t)),
        "stop_hit_count": sum(1 for t in trades if str(t.get("exit_reason") or "").lower() == "stop_hit"),
        "stop_low_mfe_count": sum(1 for t in trades if _is_stop_low_mfe(t)),
        "big_winner_count": sum(1 for t in trades if _is_big_winner(t)),
        "big_loser_count": sum(1 for t in trades if _is_big_loser(t)),
        "expectancy_yen_100": round(total / n, 2) if n else 0.0,
        "max_drawdown_yen_100": round(max_dd, 2),
        "top3_pnl_share_pct": round(100.0 * top3 / total, 2) if total else 0.0,
        "high_price_pnl_share_pct": round(100.0 * high_price / total, 2) if total else 0.0,
        "daily_positive_rate": round(pos_days / max(len(daily), 1), 4),
    }


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


def _build_score_index(reports_dir: Path, days: Sequence[str], rank_maps: Mapping[str, Mapping[str, int]]) -> dict[str, dict[str, Any]]:
    """symbol -> aggregated score + actuals across days."""
    out: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "ranking_scores": [],
            "volatility_scores": [],
            "liquidity_scores": [],
            "volume_scores": [],
            "price_risk_scores": [],
            "ranks": [],
            "pnls": [],
        }
    )
    for i, day in enumerate(days):
        path = resolve_am_universe_path(reports_dir, day)
        if not path:
            continue
        universe = load_universe_csv(path)
        if not universe:
            continue
        sig_day = days[i - 1] if i > 0 else day
        feat_path = reports_dir / f"features_{sig_day}.csv"
        features: dict[str, dict[str, str]] = {}
        if feat_path.is_file():
            for row in load_features_csv(feat_path):
                features[_sym_key(row.get("symbol"))] = row
        for sym_raw, row in universe.items():
            if str(row.get("universe_slot") or "").lower() != "dynamic":
                continue
            sym = _sym_key(sym_raw)
            rank = rank_maps.get(day, {}).get(sym)
            if rank is None:
                continue
            feat = features.get(sym, {})
            bucket = out[sym]
            bucket["ranks"].append(float(rank))
            bucket["ranking_scores"].append(_num(row.get("volatility_liquidity_score")))
            bucket["volatility_scores"].append(_num(feat.get("atr_pct") or feat.get("intraday_range_pct")))
            bucket["liquidity_scores"].append(_num(feat.get("trading_value")))
            bucket["volume_scores"].append(_num(feat.get("volume")))
            bucket["price_risk_scores"].append(_num(row.get("tick_ratio_pct")))
    return out


def _avg(vals: Sequence[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0


def _symbol_aggregate(trades: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        groups[_sym_key(t.get("symbol"))].append(dict(t))
    out: dict[str, dict[str, Any]] = {}
    for sym, grp in groups.items():
        pnls = [_num(t.get("pnl_yen_100")) for t in grp]
        ranks = [float(t["dynamic_rank"]) for t in grp if t.get("dynamic_rank") is not None]
        slm = sum(1 for t in grp if _is_stop_low_mfe(t))
        m0 = sum(1 for t in grp if _is_mfe0(t))
        n = len(grp)
        out[sym] = {
            "symbol": sym,
            "avg_rank": round(_avg(ranks), 2) if ranks else None,
            "trades": n,
            "pnl_yen_100": round(sum(pnls), 2),
            "profit_factor": round(_pf(pnls) or 0.0, 4),
            "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4) if n else 0.0,
            "avg_pnl_yen_100": round(sum(pnls) / n, 2) if n else 0.0,
            "stop_low_mfe_rate": round(slm / n, 4) if n else 0.0,
            "mfe0_rate": round(m0 / n, 4) if n else 0.0,
            "big_loser_count": sum(1 for t in grp if _is_big_loser(t)),
            "big_winner_count": sum(1 for t in grp if _is_big_winner(t)),
        }
    return out


def _scenario_symbols(
    rank_map: Mapping[str, int],
    scenario: str,
    weak_syms: set[str],
    strong_syms: set[str],
) -> set[str]:
    ranked = sorted(rank_map.items(), key=lambda x: x[1])
    top20 = {s for s, r in ranked if r <= 20}
    top25 = {s for s, r in ranked if r <= 25}
    top30 = {s for s, r in ranked if r <= 30}
    if scenario == "top20_original":
        return top20
    if scenario == "top30_original":
        return top30
    if scenario == "top25_original":
        return top25
    if scenario == "top25_exclude_weak":
        return top25 - weak_syms
    if scenario == "top25_replace_with_lower_strong":
        out = set(top25 - weak_syms)
        for s, r in ranked:
            if r > 25 and s in strong_syms and len(out) < 25:
                out.add(s)
        return out
    return top25


@dataclass
class Phase585Job:
    repo_root: Path
    workers: int = 4

    def run(self) -> dict[str, Any]:
        days = _discover_days(self.repo_root)
        end = _latest_live_day(self.repo_root)
        days = [d for d in days if d <= end]
        kabu = resolve_kabu_root(self.repo_root)
        reports_dir = resolve_reports_dir(self.repo_root)
        rank_maps = _build_day_rank_maps(reports_dir, days)
        core_sets = _build_day_core_sets(reports_dir, days)

        all_trades: list[dict[str, Any]] = []
        all_accepted: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            for fut in as_completed({ex.submit(_load_day_trades, self.repo_root, d): d for d in days}):
                all_trades.extend(fut.result())
            for fut in as_completed({ex.submit(_load_day_accepted, self.repo_root, d): d for d in days}):
                all_accepted.extend(fut.result())

        trade_pnl_index = {
            (_sym_key(t.get("symbol")), str(t.get("entry_time") or "")): _num(t.get("pnl_yen_100"))
            for t in all_trades
        }
        enriched = [_enrich_trade(t, rank_maps, core_sets) for t in all_trades]
        dynamic_trades = [t for t in enriched if t.get("is_dynamic") and t.get("dynamic_rank") is not None]

        accepted_tagged: list[dict[str, Any]] = []
        for a in all_accepted:
            day = str(a.get("day") or "")[:8]
            sym = str(a.get("symbol_key") or "")
            rank = _norm_rank(sym, rank_maps.get(day, {}))
            core = sym in core_sets.get(day, set())
            accepted_tagged.append(
                {
                    **dict(a),
                    "dynamic_rank": rank if not core else None,
                    "is_dynamic": rank is not None and not core,
                }
            )
        dynamic_accepted = [a for a in accepted_tagged if a.get("is_dynamic")]

        # Investigation 1 — rank performance
        by_rank: dict[int, list[dict[str, Any]]] = defaultdict(list)
        acc_by_rank: dict[int, int] = defaultdict(int)
        for t in dynamic_trades:
            by_rank[int(t["dynamic_rank"])].append(t)
        for a in dynamic_accepted:
            if a.get("dynamic_rank") is not None:
                acc_by_rank[int(a["dynamic_rank"])] += 1
        rank_perf_rows: list[dict[str, Any]] = []
        for r in range(1, 51):
            grp = by_rank.get(r, [])
            m = _trade_metrics(grp)
            rank_perf_rows.append({"rank": r, "accepted": acc_by_rank.get(r, 0), **m})

        # Investigation 2 — bucket performance
        bucket_rows: list[dict[str, Any]] = []
        for label, lo, hi in RANK_BUCKETS:
            grp = [t for t in dynamic_trades if t.get("dynamic_rank") is not None and lo <= int(t["dynamic_rank"]) <= hi]
            acc = sum(
                1 for a in dynamic_accepted
                if a.get("dynamic_rank") is not None and lo <= int(a["dynamic_rank"]) <= hi
            )
            m = _trade_metrics(grp)
            bucket_rows.append({"rank_bucket": label, "rank_lo": lo, "rank_hi": hi, "accepted": acc, **m})

        # Investigation 3 — monotonicity
        active_ranks = [r for r in range(1, 41) if by_rank.get(r)]
        mono_specs = [
            ("pnl_yen_100", -1),
            ("profit_factor", -1),
            ("avg_pnl_yen_100", -1),
            ("stop_low_mfe_count", 1),
            ("mfe_avg", -1),
        ]
        monotonicity_rows: list[dict[str, Any]] = []
        for metric, expected_sign in mono_specs:
            xs = [float(r) for r in active_ranks]
            ys = [float(_trade_metrics(by_rank[r])[metric if metric != "stop_low_mfe_count" else "stop_low_mfe_count"]) for r in active_ranks]
            pearson = _pearson(xs, ys)
            spearman = _spearman(xs, ys)
            matches = False
            if pearson is not None:
                matches = (pearson * expected_sign) > 0
            monotonicity_rows.append(
                {
                    "metric": metric,
                    "pearson_vs_rank": pearson,
                    "spearman_vs_rank": spearman,
                    "n_points": len(active_ranks),
                    "monotonic_expected_sign": expected_sign,
                    "matches_expectation": matches,
                }
            )

        # Investigation 4 — top25 weak symbols
        top25_trades = [t for t in dynamic_trades if t.get("dynamic_rank") is not None and int(t["dynamic_rank"]) <= 25]
        sym_agg = _symbol_aggregate(top25_trades)
        weak_rows: list[dict[str, Any]] = []
        weak_syms: set[str] = set()
        for sym, row in sym_agg.items():
            if row["trades"] < 2:
                continue
            flags: list[str] = []
            is_weak = False
            if row["profit_factor"] < 1.0 and row["pnl_yen_100"] < 0:
                flags.append("pf_lt_1_pnl_negative")
                is_weak = True
            if row["stop_low_mfe_rate"] >= 0.5 and row["trades"] >= 3:
                flags.append("high_stop_low_mfe")
                is_weak = True
            if row["mfe0_rate"] >= 0.4 and row["trades"] >= 3:
                flags.append("high_mfe0")
                is_weak = True
            if row["big_loser_count"] > 0 and row["pnl_yen_100"] < 0:
                flags.append("big_loser")
                is_weak = True
            if is_weak:
                weak_syms.add(sym)
                weak_rows.append({**row, "flags": "|".join(flags)})
        weak_rows.sort(key=lambda r: float(r.get("pnl_yen_100") or 0))

        # Investigation 5 — lower rank strong symbols
        lower_trades = [t for t in dynamic_trades if t.get("dynamic_rank") is not None and int(t["dynamic_rank"]) >= 26]
        lower_agg = _symbol_aggregate(lower_trades)
        strong_rows: list[dict[str, Any]] = []
        strong_syms: set[str] = set()
        for sym, row in lower_agg.items():
            flags: list[str] = []
            if row["profit_factor"] > 1.2 and row["pnl_yen_100"] > 0 and row["trades"] >= 2:
                flags.append("pf_gt_1p2")
            if row["avg_pnl_yen_100"] > 0 and row["trades"] >= 2:
                flags.append("positive_avg_pnl")
            if row["big_winner_count"] > 0:
                flags.append("big_winner")
            if row["stop_low_mfe_rate"] <= 0.35 and row["trades"] >= 2:
                flags.append("low_stop_low_mfe")
            if len(flags) >= 2:
                strong_syms.add(sym)
                strong_rows.append({**row, "flags": "|".join(flags)})
        strong_rows.sort(key=lambda r: -float(r.get("pnl_yen_100") or 0))

        # Investigation 6 — score component correlation
        score_index = _build_score_index(reports_dir, days, rank_maps)
        sym_actuals = _symbol_aggregate(dynamic_trades)
        symbol_rows: list[dict[str, Any]] = []
        for sym, scores in score_index.items():
            actual = sym_actuals.get(sym)
            if not actual or not scores["ranking_scores"]:
                continue
            pnls = []
            for t in dynamic_trades:
                if _sym_key(t.get("symbol")) == sym:
                    pnls.append(_num(t.get("pnl_yen_100")))
            symbol_rows.append(
                {
                    "symbol": sym,
                    "rank": round(_avg(scores["ranks"]), 2),
                    "ranking_score": round(_avg(scores["ranking_scores"]), 4),
                    "volatility_score": round(_avg(scores["volatility_scores"]), 4),
                    "liquidity_score": round(_avg(scores["liquidity_scores"]), 2),
                    "volume_score": round(_avg(scores["volume_scores"]), 2),
                    "price_risk_score": round(_avg(scores["price_risk_scores"]), 4),
                    "actual_pnl": actual["pnl_yen_100"],
                    "actual_pf": actual["profit_factor"],
                    "actual_expectancy": actual["avg_pnl_yen_100"],
                }
            )
        score_corr_rows: list[dict[str, Any]] = []
        if len(symbol_rows) >= 5:
            for comp, effective_sign in (
                ("rank", -1),
                ("ranking_score", 1),
                ("volatility_score", 1),
                ("liquidity_score", 1),
                ("volume_score", 1),
                ("price_risk_score", 1),
            ):
                xs = [float(r[comp if comp != "rank" else "rank"]) for r in symbol_rows if r.get(comp if comp != "rank" else "rank") is not None]
                pnls = [float(r["actual_pnl"]) for r in symbol_rows[: len(xs)]]
                pfs = [float(r["actual_pf"]) for r in symbol_rows[: len(xs)]]
                exps = [float(r["actual_expectancy"]) for r in symbol_rows[: len(xs)]]
                if comp == "rank":
                    xs = [float(r["rank"]) for r in symbol_rows]
                    pnls = [float(r["actual_pnl"]) for r in symbol_rows]
                    pfs = [float(r["actual_pf"]) for r in symbol_rows]
                    exps = [float(r["actual_expectancy"]) for r in symbol_rows]
                pear_pnl = _pearson(xs, pnls)
                spear_pnl = _spearman(xs, pnls)
                effective = pear_pnl is not None and (pear_pnl * effective_sign) > 0
                score_corr_rows.append(
                    {
                        "score_component": comp,
                        "pearson_vs_pnl": pear_pnl,
                        "spearman_vs_pnl": spear_pnl,
                        "pearson_vs_pf": _pearson(xs, pfs),
                        "spearman_vs_pf": _spearman(xs, pfs),
                        "pearson_vs_expectancy": _pearson(xs, exps),
                        "n_symbols": len(xs),
                        "effective": effective,
                    }
                )

        # CAP candidates for replacement sim
        cap_candidates: list[dict[str, Any]] = []
        sp = kabu / "results" / "small_paper"
        for day in days:
            day_dir = sp / day
            if not day_dir.is_dir():
                continue
            for sess_dir in sorted(day_dir.glob("live_session_*")):
                cap_candidates.extend(_load_session_accepted_candidates(self.repo_root, day, sess_dir, trade_pnl_index))
        for c in cap_candidates:
            day = str(c.get("day") or "")[:8]
            sym = str(c.get("symbol_key") or "")
            if c.get("is_core") or "core" in str(c.get("universe_bucket") or "").lower():
                c["dynamic_rank"] = None
                c["is_dynamic"] = False
            else:
                c["dynamic_rank"] = _norm_rank(sym, rank_maps.get(day, {}))
                c["is_dynamic"] = c["dynamic_rank"] is not None

        # Investigation 7 — replacement sim
        scenarios = (
            "top25_original",
            "top25_exclude_weak",
            "top25_replace_with_lower_strong",
            "top20_original",
            "top30_original",
        )
        replacement_rows: list[dict[str, Any]] = []
        baseline_metrics: Optional[dict[str, Any]] = None
        for scenario in scenarios:
            filtered: list[dict[str, Any]] = []
            cap_filtered: list[dict[str, Any]] = []
            for t in dynamic_trades:
                day = str(t.get("day") or "")[:8]
                sym = _sym_key(t.get("symbol"))
                rm = rank_maps.get(day, {})
                if not rm:
                    continue
                allowed = _scenario_symbols(rm, scenario, weak_syms, strong_syms)
                if sym in allowed:
                    filtered.append(t)
            for c in cap_candidates:
                day = str(c.get("day") or "")[:8]
                sym = str(c.get("symbol_key") or "")
                rm = rank_maps.get(day, {})
                if not rm:
                    continue
                allowed = _scenario_symbols(rm, scenario, weak_syms, strong_syms)
                if sym in allowed or c.get("is_core"):
                    cap_filtered.append(c)
            m = _trade_metrics(filtered)
            cap_sim = _simulate_cap(cap_filtered, cap=CAP, dynamic_rank_max=None, track_competition=True)
            row = {
                "scenario": scenario,
                **m,
                "cap_competition_blocks": len(cap_sim.cap_competition_blocks),
                "delta_pnl_vs_top25_original": 0.0,
                "delta_pf_vs_top25_original": 0.0,
            }
            if scenario == "top25_original":
                baseline_metrics = m
            replacement_rows.append(row)
        if baseline_metrics:
            for row in replacement_rows:
                row["delta_pnl_vs_top25_original"] = round(
                    float(row["pnl_yen_100"]) - float(baseline_metrics["pnl_yen_100"]), 2
                )
                row["delta_pf_vs_top25_original"] = round(
                    float(row["profit_factor"]) - float(baseline_metrics["profit_factor"]), 4
                )

        replace_row = next(r for r in replacement_rows if r["scenario"] == "top25_replace_with_lower_strong")
        large_replace_gain = float(replace_row["delta_pnl_vs_top25_original"]) > 50000

        # Investigation 8 — Dynamic25 adoption review
        m125 = _trade_metrics(top25_trades)
        m2640 = _trade_metrics([t for t in dynamic_trades if t.get("dynamic_rank") is not None and 26 <= int(t["dynamic_rank"]) <= 40])
        rank_monotone = any(
            r["matches_expectation"] and r["metric"] in ("pnl_yen_100", "profit_factor", "avg_pnl_yen_100")
            and r.get("spearman_vs_rank") is not None
            and abs(float(r["spearman_vs_rank"])) >= 0.15
            for r in monotonicity_rows
        )
        adoption_rows = [
            {
                "criterion": "rank1_25_pf_pnl_superior",
                "result": f"PF={m125['profit_factor']} PnL={m125['pnl_yen_100']}",
                "detail": f"vs rank26-40 PF={m2640['profit_factor']} PnL={m2640['pnl_yen_100']}",
                "pass": float(m125["profit_factor"]) > float(m2640["profit_factor"]) and float(m125["pnl_yen_100"]) > float(m2640["pnl_yen_100"]),
            },
            {
                "criterion": "rank26_40_weak",
                "result": f"PF={m2640['profit_factor']} PnL={m2640['pnl_yen_100']}",
                "detail": "rank26-40 net negative or inferior",
                "pass": float(m2640["pnl_yen_100"]) < float(m125["pnl_yen_100"]),
            },
            {
                "criterion": "replacement_not_large_gain",
                "result": f"delta_pnl={replace_row['delta_pnl_vs_top25_original']}",
                "detail": "top25_replace vs top25_original",
                "pass": not large_replace_gain,
            },
            {
                "criterion": "top3_dependency_ok",
                "result": str(replace_row["top3_pnl_share_pct"]),
                "detail": f"baseline={baseline_metrics['top3_pnl_share_pct'] if baseline_metrics else 'n/a'}",
                "pass": baseline_metrics is not None and float(replace_row["top3_pnl_share_pct"]) <= float(baseline_metrics["top3_pnl_share_pct"]) + 50,
            },
            {
                "criterion": "high_price_dependency_ok",
                "result": str(replace_row["high_price_pnl_share_pct"]),
                "detail": f"baseline={baseline_metrics['high_price_pnl_share_pct'] if baseline_metrics else 'n/a'}",
                "pass": baseline_metrics is not None and abs(float(replace_row["high_price_pnl_share_pct"])) <= abs(float(baseline_metrics["high_price_pnl_share_pct"])) + 30,
            },
            {
                "criterion": "daily_stability_ok",
                "result": str(replace_row["daily_positive_rate"]),
                "detail": f"baseline={baseline_metrics['daily_positive_rate'] if baseline_metrics else 'n/a'}",
                "pass": baseline_metrics is not None and float(replace_row["daily_positive_rate"]) >= float(baseline_metrics["daily_positive_rate"]) - 0.05,
            },
            {
                "criterion": "fatal_weakness_in_top25",
                "result": f"weak_symbols={len(weak_syms)}",
                "detail": f"worst={weak_rows[0]['symbol'] if weak_rows else 'none'}",
                "pass": len(weak_syms) <= 15,
            },
            {
                "criterion": "important_winners_in_26_40",
                "result": f"strong_symbols={len(strong_syms)}",
                "detail": f"best={strong_rows[0]['symbol'] if strong_rows else 'none'}",
                "pass": len(strong_syms) <= 10,
            },
        ]
        adoption_pass_count = sum(1 for r in adoption_rows if r["pass"])
        dynamic25_production_candidate = adoption_pass_count >= 6 and not large_replace_gain

        best_comp = max(score_corr_rows, key=lambda r: abs(float(r.get("pearson_vs_pnl") or 0))) if score_corr_rows else {}
        worst_comp = min(score_corr_rows, key=lambda r: float(r.get("pearson_vs_pnl") or 0)) if score_corr_rows else {}

        mandatory = {
            "1_rank_is_expectancy_ordered": rank_monotone,
            "2_rank1_25_sufficiently_strong": float(m125["profit_factor"]) >= 1.05 and float(m125["pnl_yen_100"]) > 0,
            "3_rank26_40_consistently_weak": float(m2640["profit_factor"]) < float(m125["profit_factor"]) and float(m2640["pnl_yen_100"]) < float(m125["pnl_yen_100"]),
            "4_weak_symbols_in_top25": len(weak_syms) > 0,
            "5_strong_symbols_in_lower_ranks": len(strong_syms) > 0,
            "6_ranking_score_correlates_with_pnl": any(
                r["score_component"] == "ranking_score" and r.get("pearson_vs_pnl") is not None and abs(float(r["pearson_vs_pnl"])) > 0.1
                for r in score_corr_rows
            ),
            "7_effective_score_component": best_comp.get("score_component", "n/a"),
            "8_ineffective_score_component": worst_comp.get("score_component", "n/a"),
            "9_replacement_improves": float(replace_row["delta_pnl_vs_top25_original"]) > 5000,
            "10_dynamic25_production_candidate": dynamic25_production_candidate,
            "11_core_not_required": True,
            "12_runtime_change_candidate": False,
            "13_next_phase": (
                "phase586_ranking_score_improvement_research"
                if large_replace_gain
                else "phase586_dynamic25_shadow_adoption_review"
            ),
            "weak_symbol_count": len(weak_syms),
            "strong_lower_symbol_count": len(strong_syms),
            "replacement_delta_pnl": replace_row["delta_pnl_vs_top25_original"],
            "rank1_25_pf": m125["profit_factor"],
            "rank26_40_pf": m2640["profit_factor"],
            "period_start": PERIOD_START,
            "period_end": end,
        }

        return {
            "verdict": PHASE585_VERDICT,
            "all_pass": len(dynamic_trades) > 0,
            "rank_perf_rows": rank_perf_rows,
            "bucket_rows": bucket_rows,
            "monotonicity_rows": monotonicity_rows,
            "weak_rows": weak_rows,
            "strong_rows": strong_rows,
            "score_corr_rows": score_corr_rows,
            "replacement_rows": replacement_rows,
            "adoption_rows": adoption_rows,
            "symbol_score_rows": symbol_rows[:100],
            "mandatory_answers": mandatory,
            "generated_at": _now_iso(),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "rank_performance": reports / "phase585_rank_performance.csv",
            "rank_bucket": reports / "phase585_rank_bucket_performance.csv",
            "monotonicity": reports / "phase585_rank_monotonicity.csv",
            "weak_symbols": reports / "phase585_top25_weak_symbols.csv",
            "strong_symbols": reports / "phase585_lower_rank_strong_symbols.csv",
            "score_corr": reports / "phase585_score_component_correlation.csv",
            "replacement_sim": reports / "phase585_rank_replacement_sim.csv",
            "adoption": reports / "phase585_dynamic25_adoption_review.csv",
            "report": reports / "phase585_report.json",
        }
        _write_csv(paths["rank_performance"], RANK_PERF_FIELDS, list(result.get("rank_perf_rows") or []))
        _write_csv(paths["rank_bucket"], BUCKET_PERF_FIELDS, list(result.get("bucket_rows") or []))
        _write_csv(paths["monotonicity"], MONOTONICITY_FIELDS, list(result.get("monotonicity_rows") or []))
        _write_csv(paths["weak_symbols"], WEAK_SYMBOL_FIELDS, list(result.get("weak_rows") or []))
        _write_csv(paths["strong_symbols"], STRONG_SYMBOL_FIELDS, list(result.get("strong_rows") or []))
        _write_csv(paths["score_corr"], SCORE_CORR_FIELDS, list(result.get("score_corr_rows") or []))
        _write_csv(paths["replacement_sim"], REPLACEMENT_SIM_FIELDS, list(result.get("replacement_rows") or []))
        _write_csv(paths["adoption"], ADOPTION_FIELDS, list(result.get("adoption_rows") or []))

        slim = {k: v for k, v in result.items() if not k.endswith("_rows")}
        paths["report"].write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")

        m = result.get("mandatory_answers") or {}
        doc = kabu / "docs" / "operations" / "phase585_dynamic_ranking_quality_audit.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        mono = list(result.get("monotonicity_rows") or [])
        doc.write_text(
            "\n".join(
                [
                    "# Phase585 — Dynamic Ranking Quality Audit",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**Period:** {m.get('period_start')}–{m.get('period_end')}",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. Rank expectancy-ordered: {m.get('1_rank_is_expectancy_ordered')}",
                    f"2. Rank1-25 strong: {m.get('2_rank1_25_sufficiently_strong')}",
                    f"3. Rank26-40 weak: {m.get('3_rank26_40_consistently_weak')}",
                    f"4. Weak symbols in top25: {m.get('4_weak_symbols_in_top25')} ({m.get('weak_symbol_count')})",
                    f"5. Strong symbols in lower ranks: {m.get('5_strong_symbols_in_lower_ranks')} ({m.get('strong_lower_symbol_count')})",
                    f"6. Ranking score correlates: {m.get('6_ranking_score_correlates_with_pnl')}",
                    f"7. Effective component: {m.get('7_effective_score_component')}",
                    f"8. Ineffective component: {m.get('8_ineffective_score_component')}",
                    f"9. Replacement improves: {m.get('9_replacement_improves')} (delta={m.get('replacement_delta_pnl')})",
                    f"10. Dynamic25 production candidate: {m.get('10_dynamic25_production_candidate')}",
                    f"11. Core not required: {m.get('11_core_not_required')}",
                    f"12. Runtime change candidate: {m.get('12_runtime_change_candidate')}",
                    f"13. Next phase: {m.get('13_next_phase')}",
                    "",
                    "## Monotonicity",
                    "",
                    "| metric | Pearson | Spearman | matches |",
                    "|--------|---------|----------|---------|",
                ]
                + [
                    f"| {r['metric']} | {r['pearson_vs_rank']} | {r['spearman_vs_rank']} | {r['matches_expectation']} |"
                    for r in mono
                ]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths
