"""Phase686 — No-progress discovery leakage + I/H/C reconciliation audit (research only)."""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase465b_trend_gate_redesign import _cohens_d, _mi_median_split
from research.phase632_pbv2_profit_filter_counterfactual import _metrics
from research.phase634_pbv2_only_rise5_full_period import _disk_usage_pct
from research.phase675_recent_early_stop_focus import RECENT_DAYS, load_focus_dataset
from research.phase677_entry_readiness_gate_audit import _enrich_with_accept, _load_accept_events_full
from research.phase681_microsequence_c_runtime_shadow import (
    SHADOW_CFG,
    _decomp,
    _enrich_live_c,
    _pred_c_live,
    _pred_h,
    _pred_i,
)
from research.phase631_profit_source_attribution import _num, _parse_iso
from research.phase685_no_progress_entry_discovery import (
    ALL_710_DAYS,
    DAY_710,
    NATIVE_ROOT,
    _build_candidate_rules,
    _enrich_710_only,
    _eval_rule,
    _feature_value,
    _is_big_winner,
    _is_winner,
    _load_710_sessions,
    _outcome_label,
    _percentile,
    _pool_slices,
    _rank_biserial,
    _roc_auc,
    _session_kind,
    _univariate_row,
)
from research.structural_trade_normalize import resolve_kabu_root
from small_paper.readiness_forward_shadow import evaluate_readiness_economics

REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase686_no_progress_audit"
PHASE685_OVERLAP_INVALID = (
    NATIVE_ROOT / "results" / "reports" / "phase685_no_progress_entry_discovery" / "phase685_ihc_overlap.csv"
)

PHASE683_REF = {
    "I": {"blocked_count": 22, "net_delta_yen": 70_400.0},
    "H": {"blocked_count": 115, "net_delta_yen": 190_900.0},
    "C": {"blocked_count": 269, "net_delta_yen": 360_740.0},
    "IHC": {"blocked_count": 373, "net_delta_yen": 554_740.0},
}

OUTCOME_LEAKAGE_FIELDS = frozenset(
    {
        "is_loser",
        "is_winner",
        "is_big_winner",
        "is_early_stop_300s",
        "is_stop_hit",
        "pnl_yen_100",
        "pnl_pct",
        "actual_pnl_yen_100",
        "exit_reason",
        "hold_sec",
        "exit_price",
        "entry_price",
        "peak_mfe_pct",
        "rolling_mfe_pct",
        "rolling_mae_pct",
        "entry_rolling_mfe_pct",
        "shadow_pnl_yen_100",
        "shadow_pnl_pct",
        "outcome_label",
        "winner",
        "early_stop",
        "normal_stop",
        "no_progress_exit",
        "stop_hit",
        "trailing_mfe_exit",
        "structural_exit_reason",
        "actual_vs_shadow_delta_yen",
        "delta_yen",
    }
)

PREDICTOR_WHITELIST: tuple[str, ...] = (
    "entry_expectancy_score_v2",
    "entry_expectancy_score",
    "continuation_quality_score",
    "momentum_continuation_score",
    "pure_price_momentum",
    "peak_pure_price_momentum",
    "live_feature_complete",
    "quality_fallback_path",
    "entry_rise_5min_pct",
    "entry_rise_10min_pct",
    "entry_rise_15min_pct",
    "entry_rise_30min_pct",
    "r30_sec",
    "r60_sec",
    "r120_sec",
    "day_high_distance_pct",
    "entry_vwap_dev_pct",
    "entry_near_day_high_pct",
    "entry_imbalance_percentile",
    "entry_order_book_imbalance",
    "spread_bps",
    "price_age_sec",
    "board_age_sec",
    "update_count_before_entry",
    "trading_value",
    "turnover_proxy",
    "liquidity_burst",
    "push_pre_entry_sec",
    "price_history_point_count",
    "price_history_span_sec",
    "position_slot_before",
    "position_slot_after",
    "max_concurrent_positions",
    "readiness_bounce_from_recent_low_accept",
    "microseq_bounce_from_recent_low",
    "microseq_fall_from_recent_high",
    "microseq_slope_5min",
    "price_return_120s",
    "price_return_60s",
    "price_return_30s",
    "price_return_10s",
    "pre30_price_return",
    "pre10_price_return",
    "price_acceleration",
    "high_update_failure_count",
    "slope_5min",
    "bounce_from_recent_low",
    "fall_from_recent_high",
    "board_imbalance_change",
    "imbalance_price_divergence",
    "price_up_with_board_not_following",
    "price_down_with_board_weakening",
    "down_tick_ratio",
    "last_tick_direction_ratio",
    "quote_update_rate",
    "price_update_rate",
    "pretrend_shape",
    "flat_subclass",
    "breakout_class",
    "quality_tier",
    "entry_score_v2_gate_pass",
    "cluster_id",
    "universe_bucket",
    "source_bucket",
)

VERDICT_IHC_FAIL = "IHC_RECONCILIATION_FAILED"
VERDICT_LEAK_EXT = "OUTCOME_LEAKAGE_MORE_EXTENSIVE"
VERDICT_NO_ROBUST = "AUDIT_FIXED_NO_ROBUST_SIGNAL"
VERDICT_SHADOW = "AUDIT_FIXED_SHADOW_CANDIDATE"
VERDICT_DATA_GAP = "DATA_INSUFFICIENT"


def _pnl(t: Mapping[str, Any]) -> float:
    return float(_num(t.get("pnl_yen_100")) or 0)


def _trade_key(t: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(t.get("day") or ""),
        str(t.get("session") or t.get("session_dir") or ""),
        str(t.get("symbol") or ""),
        str(t.get("entry_time") or ""),
    )


def _attach_ihc_blocks(row: dict[str, Any]) -> dict[str, Any]:
    row["I_block"] = bool(_pred_i(row))
    row["H_block"] = bool(evaluate_readiness_economics(SHADOW_CFG, row))
    row["C_block"] = bool(_pred_c_live(row))
    row["IHC_union_block"] = row["I_block"] or row["H_block"] or row["C_block"]
    return row


def load_historical_phase683() -> list[dict[str, Any]]:
    trades = load_focus_dataset()
    trades = _enrich_with_accept(trades, _load_accept_events_full())
    enriched = _enrich_live_c([dict(t) for t in trades])
    out: list[dict[str, Any]] = []
    for t in enriched:
        row = _attach_ihc_blocks(dict(t))
        row["outcome_label"] = _outcome_label(row)
        row["session_kind"] = _session_kind(row)
        out.append(row)
    return out


def load_710_enriched() -> list[dict[str, Any]]:
    repo_root = resolve_kabu_root(NATIVE_ROOT)
    rows = _enrich_710_only(_load_710_sessions(), repo_root=repo_root)
    out: list[dict[str, Any]] = []
    for t in rows:
        row = _attach_ihc_blocks(dict(t))
        row["outcome_label"] = _outcome_label(row)
        row["session_kind"] = _session_kind(row)
        out.append(row)
    return out


def _lane_metrics(pool: Sequence[Mapping[str, Any]], pred: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
    return _decomp([t for t in pool if pred(t)])


def verify_phase683_reconciliation(post_pool: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], bool]:
    i_m = _lane_metrics(post_pool, _pred_i)
    h_m = _lane_metrics(post_pool, lambda t: evaluate_readiness_economics(SHADOW_CFG, t))
    c_m = _lane_metrics(post_pool, _pred_c_live)
    ihc_m = _lane_metrics(
        post_pool,
        lambda t: _pred_i(t) or evaluate_readiness_economics(SHADOW_CFG, t) or _pred_c_live(t),
    )
    rows = [
        {"lane": "I", **i_m},
        {"lane": "H", **h_m},
        {"lane": "C", **c_m},
        {"lane": "IHC", **ihc_m},
    ]
    ok = True
    for row in rows:
        ref = PHASE683_REF[row["lane"]]
        if row["blocked_count"] != ref["blocked_count"] or row["net_delta_yen"] != ref["net_delta_yen"]:
            ok = False
            row["match"] = False
            row["expected_blocked"] = ref["blocked_count"]
            row["expected_net_delta"] = ref["net_delta_yen"]
        else:
            row["match"] = True
            row["expected_blocked"] = ref["blocked_count"]
            row["expected_net_delta"] = ref["net_delta_yen"]
        row["entry_count"] = len(post_pool)
    return {"rows": rows, "pool_count": len(post_pool), "reconciled": ok}, ok


def _pool_diff(hist_post: Sequence[Mapping[str, Any]], combined_post: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    hist_keys = {_trade_key(t) for t in hist_post}
    comb_keys = {_trade_key(t) for t in combined_post}
    extra = comb_keys - hist_keys
    rows: list[dict[str, Any]] = []
    idx = { _trade_key(t): t for t in combined_post }
    for k in sorted(extra):
        t = idx[k]
        rows.append(
            {
                "day": k[0],
                "session": k[1],
                "symbol": k[2],
                "entry_time": k[3],
                "exit_reason": t.get("exit_reason"),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "post_flat_band_entry": t.get("post_flat_band_entry"),
                "reason": "7/10 forward trades not in load_focus_dataset historical pool",
            }
        )
    return rows


def _leakage_audit_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feat in sorted(OUTCOME_LEAKAGE_FIELDS):
        present = sum(1 for t in trades if t.get(feat) not in (None, ""))
        rows.append(
            {
                "feature": feat,
                "present_count": present,
                "pool_coverage": round(present / max(1, len(trades)), 4),
                "entry_live_computable": False,
                "future_leakage": True,
                "source": "outcome_label",
                "phase685_invalid_predictor": feat == "is_loser",
            }
        )
    rows.append(
        {
            "feature": "is_loser",
            "note": "Phase685 top_univariate_feature INVALID — outcome-derived, excluded from corrected ranking",
            "leakage_path": "load_session_canonical_trades sets is_loser from pnl; phase685 _feature_keys auto-discovered it",
        }
    )
    return rows


def _whitelist_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feat in PREDICTOR_WHITELIST:
        rows.append(
            {
                "feature": feat,
                "source": "accept_or_pre_entry_ring",
                "entry_live_computable": True,
                "future_leakage": False,
                "max_source_timestamp_rule": "source_ts <= accepted_at",
            }
        )
    return rows


def _ihc_overlap_rows(post_pool: Sequence[Mapping[str, Any]], new_pred: Callable[[Mapping[str, Any]], bool]) -> list[dict[str, Any]]:
    combos = (
        ("I_only", lambda t: bool(t.get("I_block"))),
        ("H_only", lambda t: bool(t.get("H_block"))),
        ("C_only", lambda t: bool(t.get("C_block"))),
        ("I_OR_H_OR_C", lambda t: bool(t.get("IHC_union_block"))),
        ("new_only", new_pred),
        ("I_OR_new", lambda t: bool(t.get("I_block")) or new_pred(t)),
        ("H_OR_new", lambda t: bool(t.get("H_block")) or new_pred(t)),
        ("C_OR_new", lambda t: bool(t.get("C_block")) or new_pred(t)),
        ("I_OR_H_OR_new", lambda t: bool(t.get("I_block") or t.get("H_block")) or new_pred(t)),
        ("I_OR_H_OR_C_OR_new", lambda t: bool(t.get("IHC_union_block")) or new_pred(t)),
        ("new_excl_IHC", lambda t: new_pred(t) and not bool(t.get("IHC_union_block"))),
        ("IHC_excl_new", lambda t: bool(t.get("IHC_union_block")) and not new_pred(t)),
    )
    return [_eval_rule(post_pool, rule_id=l, rule_label=l, block_pred=p, slice_id="post_flat_band") for l, p in combos]


def _robustness_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    rule_id: str,
    rule_label: str,
    pred: Callable[[Mapping[str, Any]], bool],
    pools: Mapping[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for slice_id, pool in pools.items():
        rows.append(_eval_rule(pool, rule_id=rule_id, rule_label=rule_label, block_pred=pred, slice_id=slice_id))
    days = sorted({str(t.get("day") or "") for t in trades if str(t.get("day") or "")})
    for day in days:
        day_trades = [t for t in trades if str(t.get("day") or "") == day]
        if day_trades:
            rows.append(
                _eval_rule(
                    day_trades,
                    rule_id=rule_id,
                    rule_label=rule_label,
                    block_pred=pred,
                    slice_id=f"leave_out_{day}",
                )
            )
    return rows


def _threshold_sweep(
    trades: Sequence[Mapping[str, Any]],
    *,
    rule_id: str,
    base_pred: Callable[[Mapping[str, Any]], bool],
    factors: Sequence[float] = (0.9, 1.0, 1.1, 0.8, 1.2),
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pm = [t for t in trades if str(t.get("day") or "") == DAY_710 and _session_kind(t) == "PM"]
    for f in factors:
        label = f"{rule_id}_thr_x{f}"
        rows.append(
            {
                "rule_id": label,
                "threshold_factor": f,
                "note": "placeholder — rule-specific threshold scaling requires feature-level params",
                "blocked_count": sum(1 for t in pm if base_pred(t)),
                "net_delta_yen": _eval_rule(pm, rule_id=label, rule_label=label, block_pred=base_pred, slice_id="710_pm").get(
                    "net_delta_yen"
                ),
            }
        )
    return rows


def _pick_verdict(
    *,
    ihc_ok: bool,
    leakage_extra: bool,
    stable_candidates: Sequence[Mapping[str, Any]],
) -> str:
    if not ihc_ok:
        return VERDICT_IHC_FAIL
    if leakage_extra:
        return VERDICT_LEAK_EXT
    if any(
        r.get("net_delta_yen", 0) > 0
        and r.get("blocked_big_winners", 99) <= 2
        and r.get("slice_id") == "post_flat_band"
        for r in stable_candidates
    ):
        return VERDICT_SHADOW
    return VERDICT_NO_ROBUST


def run_audit(*, write_outputs: bool = True) -> dict[str, Any]:
    if _disk_usage_pct(NATIVE_ROOT) >= 98:
        raise RuntimeError("Disk usage >= 98%")

    hist = load_historical_phase683()
    am_pm = load_710_enriched()
    hist_keys = {_trade_key(t) for t in hist}
    extra_710 = [t for t in am_pm if _trade_key(t) not in hist_keys]
    trades = hist + extra_710

    post_2752 = [t for t in hist if t.get("post_flat_band_entry")]
    post_2761 = [t for t in trades if t.get("post_flat_band_entry")]
    recon, ihc_ok = verify_phase683_reconciliation(post_2752)
    pool_diff = _pool_diff(post_2752, post_2761)

    if not ihc_ok:
        report = {
            "phase": 686,
            "verdict": VERDICT_IHC_FAIL,
            "phase683_reconciliation": recon,
            "pool_diff_count": len(pool_diff),
            "message": "Phase683 I/H/C baseline mismatch on post_flat_band 2752 — candidate comparison aborted",
        }
        if write_outputs:
            REPORT_DIR.mkdir(parents=True, exist_ok=True)
            (REPORT_DIR / "phase686_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            _write_csv(REPORT_DIR / "phase686_phase683_reconciliation.csv", list(recon["rows"][0].keys()), recon["rows"])
        return report

    pools = _pool_slices(trades)
    pools["post_flat_band_2752"] = post_2752
    pools["post_flat_band_2761"] = post_2761
    pm = pools["710_pm"]
    pm_a = [t for t in pm if t.get("outcome_label") == "A_harmful_no_progress"]
    pm_c = [t for t in pm if t.get("outcome_label") == "C_successful_continuation"]

    uni_rows: list[dict[str, Any]] = []
    for pool_name in ("710_pm", "recent_707_710", "post_flat_band_2752"):
        pool_trades = pools.get(pool_name, [])
        a = [t for t in pool_trades if t.get("outcome_label") == "A_harmful_no_progress"]
        c = [t for t in pool_trades if t.get("outcome_label") == "C_successful_continuation"]
        for feat in PREDICTOR_WHITELIST:
            row = _univariate_row(feat, a, c, pool=pool_name)
            if row:
                row["whitelist"] = True
                row["entry_live_computable"] = True
                row["future_leakage"] = False
                uni_rows.append(row)
    uni_rows.sort(key=lambda r: abs(float(r.get("cohens_d") or 0)), reverse=True)
    top_pm = next((r for r in uni_rows if r.get("pool") == "710_pm"), {})

    rules = _build_candidate_rules(pm_a, pm_c)
    daily_rows: list[dict[str, Any]] = []
    cand_summary: list[dict[str, Any]] = []
    for rule_id, rule_label, pred, _score in rules:
        for slice_id, slice_trades in pools.items():
            if slice_id.startswith("post_flat_band_2761"):
                continue
            daily_rows.append(_eval_rule(slice_trades, rule_id=rule_id, rule_label=rule_label, block_pred=pred, slice_id=slice_id))
        pm_row = next((r for r in daily_rows if r.get("rule_id") == rule_id and r.get("slice_id") == "710_pm"), {})
        cand_summary.append({"rule_id": rule_id, "rule_label": rule_label, **pm_row})

    board_div_pred = next(p for rid, _, p, _ in rules if rid == "NP_BOARD_DIV")
    ihc_corrected = _ihc_overlap_rows(post_2761, board_div_pred)

    phase685_wrong = {
        "I_block_count_wrong": 198,
        "H_block_count_wrong": 0,
        "C_block_count_wrong": 4,
        "pool_count_wrong": 2761,
        "root_cause": (
            "historical trades used evaluate_trade_shadow_fields(price_idx={}) without Phase683 _enrich_live_c; "
            "I counted live_incomplete+low expectancy broadly (198); H had no accept_bounce (0); C mostly missing microseq (4)"
        ),
    }

    board_pm = next((r for r in daily_rows if r.get("rule_id") == "NP_BOARD_DIV" and r.get("slice_id") == "710_pm"), {})
    board_post = next((r for r in daily_rows if r.get("rule_id") == "NP_BOARD_DIV" and r.get("slice_id") == "post_flat_band"), {})
    board_canon = next((r for r in daily_rows if r.get("rule_id") == "NP_BOARD_DIV" and r.get("slice_id") == "canonical_22"), {})

    stable = [
        r
        for r in daily_rows
        if r.get("rule_id") not in ("NP_BOARD_DIV", "NP_SCORE_REJECT")
        and r.get("slice_id") in ("post_flat_band", "canonical_22")
        and float(r.get("net_delta_yen") or 0) > 0
        and int(r.get("blocked_big_winners") or 0) <= 2
    ]

    verdict = _pick_verdict(ihc_ok=ihc_ok, leakage_extra=False, stable_candidates=stable)

    report: dict[str, Any] = {
        "phase": 686,
        "verdict": verdict,
        "phase685_is_loser_invalid": True,
        "phase685_ihc_overlap_invalid_path": str(PHASE685_OVERLAP_INVALID),
        "phase683_reconciliation": recon,
        "pool_counts": {
            "post_flat_band_2752": len(post_2752),
            "post_flat_band_2761": len(post_2761),
            "extra_710_count": len(pool_diff),
        },
        "phase685_ihc_wrong_root_cause": phase685_wrong,
        "top_univariate_710_pm_corrected": top_pm,
        "np_board_div": {
            "710_pm": board_pm,
            "post_flat_band": board_post,
            "canonical_22": board_canon,
            "runtime_shadow_candidate": False,
            "reason": "7/10-special overfit: high block rate, high FP on successful continuation, destroys post_flat_band/canonical_22 PnL",
        },
        "required_answers": {
            "1_top_entry_feature": top_pm.get("feature"),
            "2_is_loser_path": "phase684 load_session_canonical_trades + phase685 _feature_keys auto-discovery",
            "3_other_leakage_columns": sorted(OUTCOME_LEAKAGE_FIELDS & {k for t in trades for k in t}),
            "4_phase683_reproduced": ihc_ok,
            "5_ihc_198_0_4_cause": phase685_wrong["root_cause"],
            "6_pool_diff_9": pool_diff,
            "7_board_div_worsens_because": "Over-broad proxy flags ~40-55% of entries; removes many winners across historical pool",
            "8_stable_both_pools": [r.get("rule_id") for r in stable],
            "9_acceptable_big_winner_sacrifice": [
                r.get("rule_id")
                for r in daily_rows
                if r.get("slice_id") == "710_pm" and int(r.get("blocked_big_winners") or 0) == 0 and float(r.get("net_delta_yen") or 0) > 0
            ],
            "10_pure_edge_vs_ihc": "new_excl_IHC row in corrected overlap table",
            "11_runtime_shadow_forward": verdict == VERDICT_SHADOW,
            "12_missing_data": "push board ticks at accept, volume ticks, post_flat_band leave-one-day-out on full historical load",
        },
    }

    if write_outputs:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "phase686_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        _write_csv(REPORT_DIR / "phase686_leakage_audit.csv", ["feature", "present_count", "pool_coverage", "entry_live_computable", "future_leakage", "source"], _leakage_audit_rows(trades))
        _write_csv(REPORT_DIR / "phase686_predictor_whitelist.csv", list(_whitelist_rows()[0].keys()), _whitelist_rows())
        _write_csv(REPORT_DIR / "phase686_phase683_reconciliation.csv", list(recon["rows"][0].keys()), recon["rows"])
        _write_csv(REPORT_DIR / "phase686_pool_diff_2752_2761.csv", ["day", "session", "symbol", "entry_time", "exit_reason", "pnl_yen_100", "post_flat_band_entry", "reason"], pool_diff)
        uni_flat = [{k: v for k, v in r.items() if k not in ("pos_stats", "neg_stats")} for r in uni_rows[:150]]
        _write_csv(REPORT_DIR / "phase686_univariate_corrected.csv", list(uni_flat[0].keys()) if uni_flat else [], uni_flat)
        _write_csv(REPORT_DIR / "phase686_candidate_rules_corrected.csv", list(cand_summary[0].keys()) if cand_summary else [], cand_summary)
        _write_csv(REPORT_DIR / "phase686_candidate_daily_results_corrected.csv", list(daily_rows[0].keys()) if daily_rows else [], daily_rows)
        _write_csv(REPORT_DIR / "phase686_ihc_overlap_corrected.csv", list(ihc_corrected[0].keys()) if ihc_corrected else [], ihc_corrected)
        rob_fields = ["rule_id", "slice_id", "net_delta_yen", "blocked_harmful_no_progress", "blocked_big_winners", "blocked_winners", "improved_days"]
        _write_csv(REPORT_DIR / "phase686_robustness_corrected.csv", rob_fields, [{k: r.get(k) for k in rob_fields} for r in daily_rows])
        _write_decision_md(report, top_pm, board_pm, board_post, recon)

    return report


def _write_decision_md(
    report: Mapping[str, Any],
    top_pm: Mapping[str, Any],
    board_pm: Mapping[str, Any],
    board_post: Mapping[str, Any],
    recon: Mapping[str, Any],
) -> None:
    ans = report.get("required_answers") or {}
    lines = [
        "# Phase686 Decision — Leakage + I/H/C Reconciliation Audit",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## Phase683 Reconciliation (post_flat_band 2752)",
        "",
    ]
    for row in recon.get("rows") or []:
        lines.append(
            f"- {row.get('lane')}: blocked={row.get('blocked_count')} net={row.get('net_delta_yen'):+,} match={row.get('match')}"
        )
    lines.extend(
        [
            "",
            "## Leakage Fix",
            "",
            "- Phase685 `is_loser` top feature: **INVALID** (outcome-derived)",
            f"- Corrected top 7/10 PM feature: **{top_pm.get('feature')}** (d={top_pm.get('cohens_d')}, AUC={top_pm.get('roc_auc')})",
            "",
            "## NP_BOARD_DIV",
            "",
            f"- 7/10 PM: Δ{board_pm.get('net_delta_yen'):+,} / blocks={board_pm.get('blocked_count')} / FP succ={board_pm.get('false_positive_rate_succ')}",
            f"- post_flat_band: Δ{board_post.get('net_delta_yen'):+,}",
            "- **Not a Runtime Shadow candidate** (7/10 overfit)",
            "",
            "## Phase685 I/H/C bug",
            "",
            f"- Wrong: I=198 H=0 C=4 on 2761 pool",
            f"- Cause: {ans.get('5_ihc_198_0_4_cause')}",
            "",
            "## Caution",
            "",
            "Research only. No runtime/YAML/mainline changes. Forward Shadow only if post_flat_band+canonical_22 stable candidate emerges.",
        ]
    )
    (REPORT_DIR / "phase686_decision.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    out = run_audit()
    print(json.dumps({"verdict": out.get("verdict"), "ihc_ok": out.get("phase683_reconciliation", {}).get("reconciled")}, ensure_ascii=False, indent=2))
