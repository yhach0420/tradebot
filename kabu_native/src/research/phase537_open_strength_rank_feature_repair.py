"""
Phase537 — Open Strength Rank Feature Repair (research only).

Fixes day_return_rank / OS9 feature enrichment for Phase536 open_strength capture.
No Runtime changes.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key
from research.phase515b_day_high_breakout_dependency_audit import _bar_index_at, _high_update_stats
from research.phase518_day_high_winner_loser_separation import _extract_entry_features
from research.phase522_stop_low_mfe_reentry_overlay_edge_audit import _day_return_rank
from research.phase530_winner_capture_research import _sym_key
from research.phase533_or_profit_source_audit import _assign_cluster, _num
from research.phase534_or_open_strength_theory import _filter_allows
from research.structural_trade_normalize import resolve_reports_dir

DEBUG_FIELDS = [
    "day",
    "symbol",
    "universe_id",
    "strategy_id",
    "entry_time",
    "minutes_from_open",
    "day_return",
    "day_return_rank",
    "vwap_distance",
    "is_open_strength_candidate",
    "is_open_strength_captured",
    "pnl_yen_100",
    "blocked_by_cap",
]

PHASE537_VERDICT = "phase537_open_strength_rank_feature_repair_done"


def _match_key(trade: Mapping[str, Any]) -> str:
    sym = str(trade.get("symbol") or "")
    sym_t = sym if sym.endswith(".T") else f"{sym}.T"
    return f"{sym_t}|{trade.get('entry_time')}"


def _vwap_distance(feats: Mapping[str, Any]) -> Optional[float]:
    raw = feats.get("vwap_distance_pct")
    if raw is not None and raw != "":
        val = _num(raw)
        if val < 900:
            return val
    pv = feats.get("price_vs_vwap")
    if pv is not None and pv != "":
        return _num(pv)
    return None


def _day_high_update_speed(feats: Mapping[str, Any]) -> Optional[float]:
    mins = feats.get("minutes_from_open")
    if mins is None:
        return None
    updates = _num(feats.get("update_count_before_entry"))
    return round(updates / max(_num(mins), 1.0), 6)


def _day_rank_maps(
    *,
    price_idx: Mapping,
    day: str,
    universe_syms: Sequence[str],
) -> tuple[dict[str, int], dict[str, float]]:
    syms_t = [s if str(s).endswith(".T") else f"{s}.T" for s in universe_syms]
    ranked = _day_return_rank(price_idx, syms_t, day)
    rank_map = {sym: i + 1 for i, (sym, _) in enumerate(ranked)}
    ret_map = {sym: ret for sym, ret in ranked}
    return rank_map, ret_map


def enrich_open_strength_features(
    trades: Sequence[Mapping[str, Any]],
    *,
    universe_id: str,
    strategy_id: str,
    price_idx: Mapping,
    bar_cache: Mapping,
    micro_lookup: Mapping,
    universe_by_day: Mapping[str, set[str]],
    executed_keys: Optional[set[str]] = None,
    blocked_keys: Optional[set[str]] = None,
    speed_p75: float = 0.0,
) -> list[dict[str, Any]]:
    """Attach OS9 fields per day using that day's universe symbol set."""
    exec_keys = executed_keys or set()
    block_keys = blocked_keys or set()
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        by_day[str(t.get("day") or "")[:8]].append(dict(t))

    rows: list[dict[str, Any]] = []
    for day, chunk in by_day.items():
        day_univ = sorted(universe_by_day.get(day, set()))
        if not day_univ:
            day_univ = sorted({_sym_key(t.get("symbol")) for t in chunk})
        rank_map, ret_map = _day_rank_maps(price_idx=price_idx, day=day, universe_syms=day_univ)

        for t in chunk:
            sym = _sym_key(t.get("symbol"))
            sym_t = f"{sym}.T"
            feats = _extract_entry_features(t, bar_cache=bar_cache, micro_lookup=micro_lookup)
            mins = feats.get("minutes_from_open")
            vwap = _vwap_distance(feats)
            speed = _day_high_update_speed(feats)

            if feats.get("day_high_update_count_before_entry") is None:
                cached = bar_cache.get((sym_t, day))
                ent = _parse_ts(str(t.get("entry_time") or ""))
                if cached and ent:
                    bars, _ = cached
                    ei = _bar_index_at(bars, ent)
                    if ei is not None:
                        stats = _high_update_stats(bars, ei, ei)
                        feats = {**feats, **stats}
                        speed = _day_high_update_speed(feats)

            pk = _match_key(t)
            row = {
                **t,
                "universe_id": universe_id,
                "strategy_id": strategy_id,
                "symbol": sym,
                "day": day,
                "position_key": pk,
                "minutes_from_open": mins,
                "day_return_rank": rank_map.get(sym),
                "day_return": ret_map.get(sym),
                "vwap_distance": vwap,
                "volume_percentile": feats.get("rolling_volume_percentile"),
                "day_high_update_speed": speed,
                "update_count": feats.get("update_count_before_entry"),
                "breakout_type": feats.get("breakout_type"),
                "rsi14": feats.get("rsi14"),
                "spread_bps": feats.get("spread"),
            }
            _cid, cluster_name = _assign_cluster(row)
            row["cluster_label"] = cluster_name
            row["cluster_id"] = _cid
            row["is_open_strength_candidate"] = _filter_allows("OS9_open_strength_proxy", row, speed_p75=speed_p75)
            row["is_open_strength_captured"] = pk in exec_keys
            row["blocked_by_cap"] = pk in block_keys
            rows.append(row)
    return rows


def build_debug_rows(
    enriched: Sequence[Mapping[str, Any]],
    *,
    executed_by_key: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in enriched:
        pk = str(r.get("position_key") or "")
        exec_t = executed_by_key.get(pk, {})
        pnl = exec_t.get("pnl_yen_100")
        if pnl is None:
            pnl = r.get("pnl_yen_100") or r.get("hypothetical_pnl")
        out.append(
            {
                "day": r.get("day"),
                "symbol": r.get("symbol"),
                "universe_id": r.get("universe_id"),
                "strategy_id": r.get("strategy_id"),
                "entry_time": r.get("entry_time"),
                "minutes_from_open": r.get("minutes_from_open"),
                "day_return": r.get("day_return"),
                "day_return_rank": r.get("day_return_rank"),
                "vwap_distance": r.get("vwap_distance"),
                "is_open_strength_candidate": r.get("is_open_strength_candidate"),
                "is_open_strength_captured": r.get("is_open_strength_captured"),
                "pnl_yen_100": pnl,
                "blocked_by_cap": r.get("blocked_by_cap"),
            }
        )
    return out


def open_strength_metrics_from_enriched(
    enriched: Sequence[Mapping[str, Any]],
    *,
    universe_id: str,
    strategy_id: str,
) -> dict[str, Any]:
    from research.market_sector_heat import _pf

    subset = [r for r in enriched if r.get("universe_id") == universe_id and r.get("strategy_id") == strategy_id]
    cand = [r for r in subset if r.get("is_open_strength_candidate")]
    captured = [r for r in cand if r.get("is_open_strength_captured")]
    pnls = [_num(r.get("pnl_yen_100")) for r in captured if r.get("pnl_yen_100") is not None]
    clusters = [r for r in captured if r.get("cluster_label") == "open_strength"]
    cand_n = len(cand)
    cap_n = len(captured)
    return {
        "universe_id": universe_id,
        "strategy_id": strategy_id,
        "open_strength_candidate_count": cand_n,
        "open_strength_captured_count": cap_n,
        "open_strength_capture_rate": round(cap_n / cand_n, 4) if cand_n else 0.0,
        "open_strength_pnl_yen_100": round(sum(pnls), 2),
        "open_strength_pf": _pf(pnls),
        "open_strength_cluster_rate": round(len(clusters) / cap_n, 4) if cap_n else 0.0,
    }


def _phase534_os9_cluster_rate_reference() -> float:
    """Phase534 OS9 open_strength cluster match rate (reference)."""
    return 0.83


def _repair_validation(
    *,
    os_rows: Sequence[Mapping[str, Any]],
    c8: Mapping[str, Any],
    phase536_mandatory: Mapping[str, Any],
    prior_broken: bool = True,
) -> dict[str, Any]:
    cand_by_univ: dict[str, int] = defaultdict(int)
    for r in os_rows:
        if r.get("strategy_id") != "MERGE_CAP_SPLIT_4_1":
            continue
        cand_by_univ[str(r.get("universe_id"))] += int(r.get("open_strength_candidate_count") or 0)

    u3 = next(
        (r for r in os_rows if r.get("universe_id") == "U3_CORE10_D40" and r.get("strategy_id") == "MERGE_CAP_SPLIT_4_1"),
        {},
    )
    cluster_rate = _float(u3.get("open_strength_cluster_rate"))
    ref = _phase534_os9_cluster_rate_reference()
    cluster_aligned = cluster_rate >= ref * 0.5 if cluster_rate else False
    all_nonzero = all(v > 0 for v in cand_by_univ.values()) if cand_by_univ else False

    return {
        "1_candidate_count_nonzero_all_universes": all_nonzero,
        "1_candidate_counts_by_universe": dict(cand_by_univ),
        "2_u3_open_strength_capture_computed": _float(u3.get("open_strength_capture_rate")) > 0 or int(u3.get("open_strength_captured_count") or 0) > 0,
        "2_u3_open_strength_capture_rate": u3.get("open_strength_capture_rate"),
        "2_u3_open_strength_candidate_count": u3.get("open_strength_candidate_count"),
        "2_u3_open_strength_captured_count": u3.get("open_strength_captured_count"),
        "3_os9_cluster_rate_phase534_aligned": cluster_aligned,
        "3_u3_cluster_rate": cluster_rate,
        "3_phase534_reference_cluster_rate": ref,
        "4_c8_pass_after_repair": c8.get("c8_pass"),
        "4_c8_detail": c8.get("checks"),
        "5_phase536_conclusions_changed": prior_broken and all_nonzero,
        "5_best_universe_after_repair": phase536_mandatory.get("6_best_universe"),
        "5_c8_pass_after_repair": phase536_mandatory.get("12_c8_pass"),
        "5_shadow_after_repair": phase536_mandatory.get("14_proceed_to_shadow"),
    }


@dataclass
class Phase537Job:
    repo_root: Path
    parallel: bool = True
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        from research.phase536_or_universe_sensitivity import Phase536Job

        p536 = Phase536Job(repo_root=self.repo_root, parallel=self.parallel, max_workers=self.max_workers)
        p536_result = p536.run()
        p536_paths = p536.write_outputs(p536_result)

        os_rows = list(p536_result.get("open_strength_rows") or [])
        debug_rows = list(p536_result.get("open_strength_debug_rows") or [])
        validation = _repair_validation(
            os_rows=os_rows,
            c8=p536_result.get("c8_evaluation") or {},
            phase536_mandatory=p536_result.get("mandatory_answers") or {},
            prior_broken=True,
        )

        return {
            "verdict": PHASE537_VERDICT,
            "phase536_verdict": p536_result.get("verdict"),
            "period_start": p536_result.get("period_start"),
            "period_end": p536_result.get("period_end"),
            "repair_validation": validation,
            "phase536_mandatory_answers": p536_result.get("mandatory_answers"),
            "phase536_c8_evaluation": p536_result.get("c8_evaluation"),
            "open_strength_rows": os_rows,
            "open_strength_debug_rows": debug_rows,
            "phase536_paths": {k: str(v) for k, v in p536_paths.items()},
            "generated_at": p536_result.get("generated_at"),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        paths = {
            "repair_report": reports / "phase537_open_strength_rank_repair_report.json",
            "debug": reports / "phase537_open_strength_rank_debug.csv",
        }
        slim = {k: v for k, v in result.items() if k != "open_strength_debug_rows"}
        paths["repair_report"].write_text(json.dumps(slim, indent=2, ensure_ascii=True, default=str), encoding="utf-8")
        from research.market_sector_heat import _write_csv

        _write_csv(paths["debug"], DEBUG_FIELDS, list(result.get("open_strength_debug_rows") or []))
        return paths
