"""
Phase451B — Entry shape filter tournament (Board mid OR high).

Same as Phase451 but candidate population is Momentum:low + (Board:mid OR Board:high)
instead of Momentum:low + Board:mid only.

Research only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY, _precompute_np_shadows
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase443_full_runtime_combined_capital_sim import (
    _chronological_pnls_from_log,
    simulate_capacity_replay,
)
from research.phase450_momentum_redesign_shadow import _passes_baseline_entry
from research.phase451_entry_shape_tournament import (
    COMPARISON_FIELDS,
    DAY_618,
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    TARGET_SYMBOLS,
    _enrich_candidates,
    _guard_b_opening_peak,
    _guard_c_strong_opening_peak,
    _guard_d_no_high_update,
    _guard_e_weak_shape,
    _guard_f_uptrend_preference,
    _guard_g_combined_conservative,
    _guard_h_combined_aggressive,
    _load_candidate_stream,
    _metrics_from_state,
    _now_iso,
    _rank_variants,
    _classify_eod_shape,
    _build_price_index_to,
)
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_expectancy_score_shadow import (
    ENTRY_SCORE_V2_GATE_MIN,
    SCORE_POINTS_V2,
    active_score_tokens_v2,
    momentum_low_required_for_v2,
)
from small_paper.entry_expectancy_score_shadow import _feature_token as _board_feature_token

VARIANTS_B: tuple[tuple[str, str, Optional[Callable[[Mapping[str, Any]], bool]]], ...] = (
    ("A_baseline_mid_high", "baseline_mid_high", None),
    ("B_opening_peak_guard", "opening_peak_guard", _guard_b_opening_peak),
    ("C_strong_opening_peak", "strong_opening_peak", _guard_c_strong_opening_peak),
    ("D_no_high_update", "no_high_update", _guard_d_no_high_update),
    ("E_weak_shape_reject", "weak_shape_reject", _guard_e_weak_shape),
    ("F_uptrend_preference", "uptrend_preference", _guard_f_uptrend_preference),
    ("G_combined_conservative", "combined_conservative", _guard_g_combined_conservative),
    ("H_combined_aggressive", "combined_aggressive", _guard_h_combined_aggressive),
)


def _v2_entry_score(trade: Mapping[str, Any]) -> int:
    tokens = active_score_tokens_v2(trade)
    return sum(SCORE_POINTS_V2.get(tok, 0) for tok in tokens)


def _board_token(trade: Mapping[str, Any]) -> Optional[str]:
    return _board_feature_token("Board", trade)


def _passes_baseline_mid_high(trade: Mapping[str, Any]) -> bool:
    if not momentum_low_required_for_v2(trade):
        return False
    tok = _board_token(trade)
    if tok == "Board:mid":
        return _v2_entry_score(trade) >= ENTRY_SCORE_V2_GATE_MIN
    if tok == "Board:high":
        return True
    return False


def _passes_baseline_mid_only(trade: Mapping[str, Any]) -> bool:
    return _passes_baseline_entry(trade)


def _board_bucket(trade: Mapping[str, Any]) -> str:
    tok = _board_token(trade) or "unknown"
    return tok.split(":", 1)[-1] if ":" in tok else tok


def _runtime_entry_block_mid_high(shape_guard: Optional[Callable[[Mapping[str, Any]], bool]] = None):
    def block(trade: Mapping[str, Any]) -> bool:
        if not _passes_baseline_mid_high(trade):
            return True
        if guard_high_drift(trade):
            return True
        if shape_guard is not None and shape_guard(trade):
            return True
        return False

    return block


def _cohort_replay_metrics(
    candidates: Sequence[Mapping[str, Any]],
    np_shadows: Mapping[str, Any],
    *,
    pass_fn: Callable[[Mapping[str, Any]], bool],
    label: str,
) -> dict[str, Any]:
    filtered = [dict(t) for t in candidates if pass_fn(t)]
    state = simulate_capacity_replay(
        filtered,
        np_shadows,
        mode=label,
        entry_block_fn=lambda t: guard_high_drift(t),
        baseline_accepted_keys=set(),
    )
    chron = _chronological_pnls_from_log(state.trade_log)
    return {
        "label": label,
        "candidate_count": len(filtered),
        "accepted_count": state.accepted_trade_count,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
    }


def _board_high_only(trade: Mapping[str, Any]) -> bool:
    return momentum_low_required_for_v2(trade) and _board_token(trade) == "Board:high"


def _board_mid_only(trade: Mapping[str, Any]) -> bool:
    return _passes_baseline_mid_only(trade)


def _board_verdict(
    *,
    high_only: Mapping[str, Any],
    mid_only: Mapping[str, Any],
    mid_high_baseline: Mapping[str, Any],
    phase451_mid_baseline_pnl: Optional[float],
) -> str:
    high_pf = float(high_only.get("profit_factor") or 0)
    mid_pf = float(mid_only.get("profit_factor") or 0)
    high_pnl = float(high_only.get("total_pnl_yen") or 0)
    added = int(high_only.get("candidate_count") or 0)
    if added == 0:
        return "board_high_noise"
    if high_pf > mid_pf and high_pnl > 0:
        return "board_high_superior"
    if phase451_mid_baseline_pnl is not None:
        if float(mid_high_baseline.get("total_pnl_yen") or 0) > phase451_mid_baseline_pnl:
            return "board_high_additive"
    if float(mid_high_baseline.get("total_pnl_yen") or 0) >= float(mid_only.get("total_pnl_yen") or 0):
        return "board_high_additive"
    return "board_high_noise"


def _shape_tournament_verdict(*, best_variant: str, best: Mapping[str, Any]) -> str:
    delta = float(best.get("delta_pnl_vs_baseline") or 0)
    d619 = float(best.get("delta_daily_pnl_619") or 0)
    ddd = float(best.get("delta_maxdd_vs_baseline") or 0)
    d6976 = float(best.get("delta_symbol_pnl_6976") or 0)
    if delta > 20000 and d619 > 0 and ddd <= 0 and d6976 >= -5000:
        return "runtime_ready"
    prefix = str(best_variant).split("_", 1)[0]
    mapping = {
        "B": "opening_peak_candidate",
        "C": "opening_peak_candidate",
        "E": "weak_shape_candidate",
        "F": "uptrend_candidate",
        "G": "combined_candidate",
        "H": "combined_candidate",
    }
    return mapping.get(prefix, "combined_candidate")


def run_phase451b_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    candidates = _load_candidate_stream(repo_root)
    enriched = _enrich_candidates(candidates, kabu=kabu)
    np_shadows = _precompute_np_shadows(enriched, kabu=kabu, np_policy=BEST_NP_POLICY)

    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    eod_shape = {key: _classify_eod_shape(series, day=key[1]) for key, series in price_idx.items()}
    eod_uptrend_keys = {key for key, shape in eod_shape.items() if shape == "uptrend"}

    mid_only_pop = [t for t in enriched if _passes_baseline_mid_only(t)]
    mid_high_pop = [t for t in enriched if _passes_baseline_mid_high(t)]
    high_only_pop = [t for t in enriched if _board_high_only(t)]
    high_added = [t for t in mid_high_pop if _board_bucket(t) == "high"]

    mid_cohort = _cohort_replay_metrics(enriched, np_shadows, pass_fn=_board_mid_only, label="board_mid_only")
    high_cohort = _cohort_replay_metrics(enriched, np_shadows, pass_fn=_board_high_only, label="board_high_only")

    phase451_mid_pnl: Optional[float] = None
    p451_path = resolve_reports_dir(repo_root) / "phase451_entry_shape_summary.json"
    if p451_path.is_file():
        try:
            phase451_mid_pnl = float(json.loads(p451_path.read_text(encoding="utf-8")).get("comparison", [{}])[0].get("total_pnl_yen"))
        except (TypeError, ValueError, IndexError, json.JSONDecodeError):
            phase451_mid_pnl = None

    metrics_rows: list[dict[str, Any]] = []
    for variant_id, _label, shape_guard in VARIANTS_B:
        state = simulate_capacity_replay(
            enriched,
            np_shadows,
            mode=variant_id,
            entry_block_fn=_runtime_entry_block_mid_high(shape_guard),
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

    challengers = [m for m in metrics_rows if not str(m["variant"]).startswith("A_")]
    best = max(challengers, key=lambda r: float(r["delta_pnl_vs_baseline"]))
    board_verdict = _board_verdict(
        high_only=high_cohort,
        mid_only=mid_cohort,
        mid_high_baseline=baseline,
        phase451_mid_baseline_pnl=phase451_mid_pnl,
    )
    shape_verdict = _shape_tournament_verdict(best_variant=str(best["variant"]), best=best)

    summary = {
        "phase": "451B-Entry-Shape-Tournament-Mid-High",
        "generated_at": _now_iso(),
        "verdict": board_verdict,
        "shape_tournament_verdict": shape_verdict,
        "period": f"{PERIOD_START}..{PERIOD_END}",
        "baseline_stack": {
            "entry": "Momentum:low + (Board:mid OR Board:high) + High Drift",
            "exit": "Hard Stop -1.2% → No Progress → Board Dynamic Trailing",
        },
        "population": {
            "mid_only_candidates": len(mid_only_pop),
            "mid_high_candidates": len(mid_high_pop),
            "board_high_added_candidates": len(mid_high_pop) - len(mid_only_pop),
            "board_high_only_candidates": len(high_only_pop),
            "board_high_added_exclusive": len(high_added),
            "total_enriched": len(enriched),
        },
        "board_cohort_analysis": {
            "board_mid_only": mid_cohort,
            "board_high_only": high_cohort,
            "phase451_mid_baseline_pnl_reference": phase451_mid_pnl,
            "mid_high_baseline_pnl": baseline.get("total_pnl_yen"),
            "delta_vs_phase451_mid_baseline": (
                round(float(baseline.get("total_pnl_yen") or 0) - phase451_mid_pnl, 2)
                if phase451_mid_pnl is not None
                else None
            ),
        },
        "comparison": [{k: m.get(k) for k in COMPARISON_FIELDS} for m in metrics_rows],
        "rankings": {
            "pnl": _rank_variants(metrics_rows, "total_pnl_yen"),
            "pf": _rank_variants(metrics_rows, "profit_factor"),
            "maxdd": _rank_variants(metrics_rows, "max_drawdown_yen", reverse=False),
        },
        "best_variant": best["variant"],
        "mandatory_answers": {
            "1_board_high_added_candidates": len(mid_high_pop) - len(mid_only_pop),
            "2_board_high_only_pf": high_cohort.get("profit_factor"),
            "3_board_high_only_pnl": high_cohort.get("total_pnl_yen"),
            "4_board_mid_vs_high": {
                "mid_only": mid_cohort,
                "high_only": high_cohort,
            },
            "5_best_variant": best["variant"],
            "6_pnl_ranking": _rank_variants(metrics_rows, "total_pnl_yen"),
            "7_pf_ranking": _rank_variants(metrics_rows, "profit_factor"),
            "8_maxdd_ranking": _rank_variants(metrics_rows, "max_drawdown_yen", reverse=False),
            "9_6976_impact_best": best.get("delta_symbol_pnl_6976"),
            "10_4062_impact_best": best.get("delta_symbol_pnl_4062"),
            "11_delta_618_best": best.get("delta_daily_pnl_618"),
            "12_delta_619_best": best.get("delta_daily_pnl_619"),
            "13_runtime_candidate": shape_verdict in ("runtime_ready", "weak_shape_candidate", "opening_peak_candidate"),
            "13_recommended_shadow": "E_weak_shape_reject",
        },
    }

    return {"summary": summary, "_comparison_rows": [{k: m.get(k) for k in COMPARISON_FIELDS} for m in metrics_rows]}


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    m = s.get("mandatory_answers") or {}
    pop = s.get("population") or {}
    board = s.get("board_cohort_analysis") or {}
    mid = board.get("board_mid_only") or {}
    high = board.get("board_high_only") or {}
    cmp_ = s.get("comparison") or []
    rk = s.get("rankings") or {}
    lines = [
        "# Phase451B — Entry Shape Tournament (Board Mid+High)",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Board verdict: **`{s.get('verdict')}`**",
        f"Shape verdict: **`{s.get('shape_tournament_verdict')}`**",
        f"Period: {s.get('period')}",
        "",
        "## Population",
        "",
        f"- Mid-only candidates: {pop.get('mid_only_candidates')}",
        f"- Mid+High candidates: {pop.get('mid_high_candidates')}",
        f"- **Board:high added: {pop.get('board_high_added_candidates')}**",
        f"- Board:high exclusive: {pop.get('board_high_added_exclusive')}",
        "",
        "## Board cohort (HD+NP, no shape guard)",
        "",
        f"| Cohort | Candidates | Accepted | PnL | PF |",
        f"|--------|------------|----------|-----|-----|",
        f"| Board:mid only | {mid.get('candidate_count')} | {mid.get('accepted_count')} | {mid.get('total_pnl_yen')} | {mid.get('profit_factor')} |",
        f"| Board:high only | {high.get('candidate_count')} | {high.get('accepted_count')} | {high.get('total_pnl_yen')} | {high.get('profit_factor')} |",
        "",
        f"Phase451 mid baseline reference PnL: {board.get('phase451_mid_baseline_pnl_reference')}",
        f"451B mid+high baseline PnL: {board.get('mid_high_baseline_pnl')} (Δ {board.get('delta_vs_phase451_mid_baseline')})",
        "",
        "## Tournament",
        "",
        "| Variant | PnL | ΔPnL | PF | MaxDD | Acc | 6/18 Δ | 6/19 Δ | 6976 Δ | 4062 Δ |",
        "|---------|-----|------|-----|-------|-----|--------|--------|--------|--------|",
    ]
    for row in cmp_:
        lines.append(
            f"| {row.get('variant')} | {row.get('total_pnl_yen')} | {row.get('delta_pnl_vs_baseline')} | "
            f"{row.get('profit_factor')} | {row.get('max_drawdown_yen')} | {row.get('accepted_count')} | "
            f"{row.get('delta_daily_pnl_618')} | {row.get('delta_daily_pnl_619')} | "
            f"{row.get('delta_symbol_pnl_6976')} | {row.get('delta_symbol_pnl_4062')} |"
        )
    lines.extend(
        [
            "",
            "## Mandatory answers",
            "",
            f"1. Board:high追加候補: **{m.get('1_board_high_added_candidates')}**",
            f"2. Board:high単独PF: {m.get('2_board_high_only_pf')}",
            f"3. Board:high単独PnL: {m.get('3_board_high_only_pnl')} yen",
            f"4. Mid vs High: mid PnL={mid.get('total_pnl_yen')} PF={mid.get('profit_factor')} / high PnL={high.get('total_pnl_yen')} PF={high.get('profit_factor')}",
            f"5. 最良variant: **{m.get('5_best_variant')}**",
            f"6. PnL順位: {m.get('6_pnl_ranking')}",
            f"7. PF順位: {m.get('7_pf_ranking')}",
            f"8. maxDD順位: {m.get('8_maxdd_ranking')}",
            f"9. 6976影響: {m.get('9_6976_impact_best')} yen",
            f"10. 4062影響: {m.get('10_4062_impact_best')} yen",
            f"11. 6/18影響: {m.get('11_delta_618_best')} yen",
            f"12. 6/19影響: {m.get('12_delta_619_best')} yen",
            f"13. Runtime候補: {m.get('13_runtime_candidate')} (recommended: {m.get('13_recommended_shadow')})",
        ]
    )
    return "\n".join(lines) + "\n"


@dataclass
class Phase451BJob:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase451b_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "comparison": reports / "phase451b_entry_shape_tournament_mid_high.csv",
            "summary": reports / "phase451b_entry_shape_summary.json",
            "report": kabu / "docs" / "operations" / "phase451b_entry_shape_tournament_mid_high.md",
        }
        _write_csv(paths["comparison"], COMPARISON_FIELDS, result.get("_comparison_rows") or [])
        paths["summary"].write_text(
            json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths
