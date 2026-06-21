"""
Phase458 — VWAP Structure Similarity Audit (research only).

Uses 18 D4-blocked trades as seeds; k-NN in feature space vs runtime-eligible pool.
Re-evaluates D4 with 6976 / 4062 / both excluded from guard application.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY, _precompute_np_shadows
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import (
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _enrich_candidates,
    _load_candidate_stream,
    _now_iso,
    _optional_float,
)
from research.phase451b_entry_shape_tournament_mid_high import _runtime_entry_block_mid_high
from research.phase456c_vwap_structure_features import enrich_trade_phase456c_features
from research.phase457_vwap_structure_robustness import (
    D4_CONSECUTIVE_ABOVE_LO,
    D4_VWAP_DEV_PCT_LO,
    _entry_block,
    _float,
    _map_runtime_fields,
    _pnl_yen,
    _runtime_baseline_block,
    _weak_shape_block,
    d4_guard,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.weak_shape_reject_entry_guard import would_block_weak_shape_reject

REPLAY_MODE = "phase456_runtime_np"
K_NEIGHBORS = 8
FEATURE_KEYS = (
    "r5",
    "r10",
    "r15",
    "r30",
    "vwap_dev",
    "consecutive_above_ticks",
    "day_high_distance",
    "board_imbalance",
)

SIMILARITY_FIELDS = [
    "row_type",
    "seed_symbol",
    "seed_entry_time",
    "seed_pnl_yen",
    "match_symbol",
    "match_entry_time",
    "match_pnl_yen",
    "distance",
    "rank",
    "r5",
    "r10",
    "r15",
    "r30",
    "vwap_dev",
    "consecutive_above_ticks",
    "day_high_distance",
    "board_imbalance",
]


def _sym_key(trade: Mapping[str, Any]) -> str:
    return str(trade.get("symbol") or "").replace(".T", "")


def _trade_key(trade: Mapping[str, Any]) -> str:
    return f"{trade.get('symbol')}|{trade.get('entry_time')}"


def _feature_vector(trade: Mapping[str, Any]) -> dict[str, Optional[float]]:
    mapped = _map_runtime_fields(trade)
    return {
        "r5": _optional_float(mapped.get("return_5min_pct") or mapped.get("entry_rise_5min_pct")),
        "r10": _optional_float(mapped.get("return_10min_pct") or mapped.get("entry_rise_10min_pct")),
        "r15": _optional_float(mapped.get("return_15min_pct") or mapped.get("entry_rise_15min_pct")),
        "r30": _optional_float(mapped.get("return_30min_pct") or mapped.get("entry_rise_30min_pct")),
        "vwap_dev": _float(trade.get("vwap_dev_pct")),
        "consecutive_above_ticks": _float(trade.get("consecutive_above_ticks")),
        "day_high_distance": _optional_float(
            trade.get("day_high_distance_pct") or trade.get("entry_near_day_high_pct")
        ),
        "board_imbalance": _float(trade.get("entry_order_book_imbalance")),
    }


def _vectorize(feat: Mapping[str, Optional[float]], stats: Mapping[str, tuple[float, float]]) -> list[float]:
    out: list[float] = []
    for k in FEATURE_KEYS:
        v = feat.get(k)
        mu, sd = stats[k]
        if v is None or sd <= 1e-12:
            out.append(0.0)
        else:
            out.append((float(v) - mu) / sd)
    return out


def _compute_stats(pool: Sequence[Mapping[str, Optional[float]]]) -> dict[str, tuple[float, float]]:
    stats: dict[str, tuple[float, float]] = {}
    for k in FEATURE_KEYS:
        vals = [float(f[k]) for f in pool if f.get(k) is not None]
        if len(vals) < 2:
            stats[k] = (0.0, 1.0)
        else:
            stats[k] = (statistics.mean(vals), statistics.pstdev(vals) or 1.0)
    return stats


def _dist(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _blocked_seeds(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(t) for t in enriched if not _runtime_baseline_block(t) and d4_guard(t)]


def _eligible_pool(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [dict(t) for t in enriched if not _runtime_baseline_block(t)]


def _find_neighbors(
    seeds: Sequence[Mapping[str, Any]],
    pool: Sequence[Mapping[str, Any]],
    *,
    stats: Mapping[str, tuple[float, float]],
    k: int,
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    seed_keys = {_trade_key(s) for s in seeds}
    pool_feats = { _trade_key(t): _feature_vector(t) for t in pool }
    pool_vecs = { tk: _vectorize(f, stats) for tk, f in pool_feats.items() }

    rows: list[dict[str, Any]] = []
    unique_matches: dict[str, dict[str, Any]] = {}

    for seed in seeds:
        sk = _trade_key(seed)
        svec = _vectorize(pool_feats[sk], stats)
        candidates: list[tuple[float, str]] = []
        for t in pool:
            tk = _trade_key(t)
            if tk in seed_keys:
                continue
            d = _dist(svec, pool_vecs[tk])
            candidates.append((d, tk))
        candidates.sort(key=lambda x: x[0])
        for rank, (d, tk) in enumerate(candidates[:k], 1):
            match = next(t for t in pool if _trade_key(t) == tk)
            feat = pool_feats[tk]
            row = {
                "row_type": "neighbor",
                "seed_symbol": seed.get("symbol"),
                "seed_entry_time": seed.get("entry_time"),
                "seed_pnl_yen": _pnl_yen(seed),
                "match_symbol": match.get("symbol"),
                "match_entry_time": match.get("entry_time"),
                "match_pnl_yen": _pnl_yen(match),
                "distance": round(d, 4),
                "rank": rank,
                **feat,
            }
            rows.append(row)
            if tk not in unique_matches or d < unique_matches[tk]["distance"]:
                unique_matches[tk] = {**row, "row_type": "similar_case"}

    return rows, unique_matches


def d4_guard_skip_symbols(skip: frozenset[str]):
    def block(tr: Mapping[str, Any]) -> bool:
        if _sym_key(tr) in skip:
            return False
        return d4_guard(tr)

    return block


def _replay_delta(
    enriched: Sequence[Mapping[str, Any]],
    np_shadows: Mapping[str, Any],
    *,
    extra_guard: Optional[Any] = None,
) -> float:
    base = simulate_capacity_replay(
        enriched,
        np_shadows,
        mode=REPLAY_MODE,
        entry_block_fn=_entry_block(None),
        baseline_accepted_keys=set(),
    )
    d4 = simulate_capacity_replay(
        enriched,
        np_shadows,
        mode=REPLAY_MODE,
        entry_block_fn=_entry_block(extra_guard or d4_guard),
        baseline_accepted_keys=set(),
    )
    return round(sum(d4.daily_pnls.values()) - sum(base.daily_pnls.values()), 2)


def _verdict(
    *,
    delta_full: float,
    delta_excl_both: float,
    similar_count: int,
    similar_symbol_count: int,
    similar_pnl: float,
) -> str:
    if (
        delta_excl_both > 15000
        and similar_count >= 15
        and similar_symbol_count >= 5
        and similar_pnl < 0
    ):
        return "generalizable_pattern"
    if delta_full - delta_excl_both > 25000:
        return "symbol_specific_pattern"
    if delta_excl_both > 10000 and similar_symbol_count >= 4:
        return "generalizable_pattern"
    return "symbol_specific_pattern"


def run_phase458_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    enriched = _enrich_candidates(_load_candidate_stream(repo_root), kabu=kabu)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    for t in enriched:
        t.update(enrich_trade_phase456c_features(t, price_idx=price_idx))

    seeds = _blocked_seeds(enriched)
    pool = _eligible_pool(enriched)
    pool_feats = [_feature_vector(t) for t in pool]
    stats = _compute_stats(pool_feats)

    neighbor_rows, similar = _find_neighbors(seeds, pool, stats=stats, k=K_NEIGHBORS)
    similar_cases = list(similar.values())
    similar_pnls = [_pnl_yen({"pnl_yen": r.get("match_pnl_yen")}) for r in similar_cases]
    similar_symbols = {_sym_key({"symbol": r.get("match_symbol")}) for r in similar_cases}

    np_shadows = _precompute_np_shadows(enriched, kabu=kabu, np_policy=BEST_NP_POLICY)
    delta_full = _replay_delta(enriched, np_shadows)
    delta_excl_6976 = _replay_delta(enriched, np_shadows, extra_guard=d4_guard_skip_symbols(frozenset({"6976"})))
    delta_excl_4062 = _replay_delta(enriched, np_shadows, extra_guard=d4_guard_skip_symbols(frozenset({"4062"})))
    delta_excl_both = _replay_delta(
        enriched, np_shadows, extra_guard=d4_guard_skip_symbols(frozenset({"6976", "4062"}))
    )

    seed_symbol_counts = {}
    for s in seeds:
        k = _sym_key(s)
        seed_symbol_counts[k] = seed_symbol_counts.get(k, 0) + 1

    structural = (
        len(similar_cases) >= 15
        and len(similar_symbols) >= 5
        and sum(similar_pnls) < 0
    )
    verdict = _verdict(
        delta_full=delta_full,
        delta_excl_both=delta_excl_both,
        similar_count=len(similar_cases),
        similar_symbol_count=len(similar_symbols),
        similar_pnl=round(sum(similar_pnls), 2),
    )
    runtime_candidate = verdict == "generalizable_pattern" and delta_excl_both > 20000

    mandatory = {
        "1_similar_case_count": len(similar_cases),
        "2_similar_case_pnl_yen": round(sum(similar_pnls), 2),
        "3_delta_excl_6976": delta_excl_6976,
        "4_delta_excl_4062": delta_excl_4062,
        "5_delta_excl_both": delta_excl_both,
        "6_structural_pattern": structural,
        "7_runtime_candidate": runtime_candidate,
        "verdict": verdict,
        "delta_full": delta_full,
        "seed_count": len(seeds),
        "similar_symbol_count": len(similar_symbols),
        "similar_loss_count": sum(1 for p in similar_pnls if p < 0),
        "similar_win_count": sum(1 for p in similar_pnls if p > 0),
        "seed_symbol_breakdown": seed_symbol_counts,
    }

    csv_rows: list[dict[str, Any]] = []
    for s in seeds:
        feat = _feature_vector(s)
        csv_rows.append(
            {
                "row_type": "seed",
                "seed_symbol": s.get("symbol"),
                "seed_entry_time": s.get("entry_time"),
                "seed_pnl_yen": _pnl_yen(s),
                "match_symbol": "",
                "match_entry_time": "",
                "match_pnl_yen": "",
                "distance": "",
                "rank": "",
                **feat,
            }
        )
    csv_rows.extend(neighbor_rows)
    for scenario, val in (
        ("full", delta_full),
        ("excl_6976", delta_excl_6976),
        ("excl_4062", delta_excl_4062),
        ("excl_both", delta_excl_both),
    ):
        csv_rows.append(
            {
                "row_type": "exclusion_delta",
                "seed_symbol": scenario,
                "seed_entry_time": "",
                "seed_pnl_yen": val,
                "match_symbol": "",
                "match_entry_time": "",
                "match_pnl_yen": "",
                "distance": "",
                "rank": "",
            }
        )

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "d4_rule": {
            "consecutive_above_ticks_lt": D4_CONSECUTIVE_ABOVE_LO,
            "vwap_dev_pct_lt": D4_VWAP_DEV_PCT_LO,
        },
        "k_neighbors": K_NEIGHBORS,
        "feature_keys": list(FEATURE_KEYS),
        "mandatory_answers": mandatory,
        "verdict": verdict,
        "_csv_rows": csv_rows,
        "_similar_cases": similar_cases,
    }


@dataclass
class Phase458Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase458_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "similarity": reports / "phase458_vwap_structure_similarity.csv",
            "summary": reports / "phase458_vwap_structure_summary.json",
        }
        _write_csv(paths["similarity"], SIMILARITY_FIELDS, list(result.get("_csv_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase458_vwap_structure_similarity_audit.md"
        m = result.get("mandatory_answers") or {}
        report.write_text(
            "\n".join(
                [
                    "# Phase458 — VWAP Structure Similarity Audit",
                    "",
                    f"Generated: {result.get('generated_at')}",
                    f"Period: {result.get('period_start')}..{result.get('period_end')}",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. Similar case count: **{m.get('1_similar_case_count')}**",
                    f"2. Similar case PnL: **{m.get('2_similar_case_pnl_yen')}** yen",
                    f"3. Delta excl 6976: **{m.get('3_delta_excl_6976')}**",
                    f"4. Delta excl 4062: **{m.get('4_delta_excl_4062')}**",
                    f"5. Delta excl both: **{m.get('5_delta_excl_both')}**",
                    f"6. Structural pattern: **{m.get('6_structural_pattern')}**",
                    f"7. Runtime candidate: **{m.get('7_runtime_candidate')}**",
                    "",
                    "See phase458_vwap_structure_similarity.csv for seed/neighbor detail.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        paths["report"] = report
        return paths
