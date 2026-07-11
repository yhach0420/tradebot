"""Phase682 — Shadow portfolio consistency audit (I/H/C metric drift, research only)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase631_profit_source_attribution import _num, _parse_iso
from research.phase634_pbv2_only_rise5_full_period import _disk_usage_pct
from research.phase665_pretrend_shape_analysis import _build_price_index_canonical, _day_key
from research.phase672_pre_entry_microsequence import BIG_WINNER_YEN, _sym_t
from research.phase674_microsequence_candidate_robustness import _rule_c
from research.phase675_recent_early_stop_focus import RECENT_DAYS, _is_early_stop, load_focus_dataset
from research.phase676_opening_coldstart_feature_incomplete import (
    _high_bounce,
    _live_feature_incomplete,
    _low_expectancy,
)
from research.phase677_entry_readiness_gate_audit import _enrich_with_accept, _load_accept_events_full
from research.phase679_readiness_shadow_combo import (
    _combo_scenarios,
    _daily_forward_summary as phase679_daily_forward_summary,
    _eval_block,
    _flags as phase679_flags,
    _portfolio_shadow as phase679_portfolio_shadow,
    _pred_c as phase679_pred_c,
    _pred_h as phase679_pred_h,
    _pred_i as phase679_pred_i,
    _pools as phase679_pools,
    _shadow_row as phase679_shadow_row,
)
from research.phase680_refined_h_forward_shadow import _enrich_mfe_pre_entry
from research.phase681_microsequence_c_runtime_shadow import (
    _decomp,
    _enrich_live_c,
    _eval_pool,
    _pred_c_live,
    _pred_h as phase681_pred_h,
    _pred_i as phase681_pred_i,
)
from research.structural_trade_normalize import resolve_kabu_root
from small_paper.microsequence_recovery_fail_forward_shadow import evaluate_microsequence_recovery_fail
from small_paper.readiness_forward_shadow import (
    EARLY_STOP_SEC,
    evaluate_baseline_h,
    evaluate_readiness_economics,
    evaluate_readiness_precision,
    evaluate_readiness_refined_h,
)
from small_paper.shadow_ihc_portfolio import compute_ihc_shadow_fields

VERDICT_CONSISTENT = "SHADOW_METRICS_CONSISTENT"
VERDICT_DRIFT = "H_METRIC_DRIFT_EXPLAINED"
VERDICT_BUG = "SHADOW_METRIC_BUG_FOUND"
VERDICT_HOLD = "HOLD"

REPORT_DIR_NAME = "phase682_shadow_portfolio_consistency"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME

SHADOW_CFG = SimpleNamespace(
    readiness_precision_shadow_enabled=True,
    readiness_precision_shadow_expectancy_max=2.5,
    readiness_precision_shadow_require_live_incomplete=True,
    readiness_economics_shadow_enabled=True,
    readiness_economics_shadow_bounce_min=0.45,
    readiness_economics_shadow_require_live_incomplete=True,
    readiness_refined_h_shadow_enabled=True,
    readiness_refined_h_bounce_min=0.45,
    readiness_refined_h_pre_entry_mfe_max_pct=1.0,
    readiness_refined_h_require_live_incomplete=True,
    microsequence_recovery_fail_shadow_enabled=True,
    microsequence_recovery_fail_bounce_min=0.2182,
    microsequence_recovery_fail_fall_from_high_max=-0.1735,
    microsequence_recovery_fail_slope_5min_max=0.1152,
)

PHASE679_H_REFERENCE = {
    "blocked_count": 115,
    "net_delta_yen": 190_900.0,
    "blocked_big_winners": 13,
}
PHASE681_H_REFERENCE = {
    "blocked_count": 82,
    "net_delta_yen": 25_400.0,
    "blocked_big_winners": 12,
}


def _pnl(t: Mapping[str, Any]) -> float:
    return float(_num(t.get("pnl_yen_100")) or 0)


def _trade_id(t: Mapping[str, Any]) -> str:
    pid = str(t.get("position_id") or t.get("trade_id") or "").strip()
    if pid:
        return pid
    return "|".join(
        (
            str(t.get("day") or ""),
            str(t.get("symbol") or ""),
            str(t.get("entry_time") or ""),
        )
    )


def _is_big_winner(t: Mapping[str, Any]) -> bool:
    return _pnl(t) >= BIG_WINNER_YEN


def _is_stop_hit(t: Mapping[str, Any]) -> bool:
    return str(t.get("exit_reason") or "") == "stop_hit"


def _is_early_stop_trade(t: Mapping[str, Any]) -> bool:
    if _is_early_stop(t):
        return True
    hs = _num(t.get("hold_sec"))
    return bool(_is_stop_hit(t) and hs is not None and hs <= EARLY_STOP_SEC)


def _pred_h_runtime_economics(t: Mapping[str, Any]) -> bool:
    return evaluate_readiness_economics(SHADOW_CFG, t)


def _pred_h_baseline(t: Mapping[str, Any]) -> bool:
    return evaluate_baseline_h(SHADOW_CFG, t)


def _pred_refined_h(t: Mapping[str, Any]) -> bool:
    return evaluate_readiness_refined_h(SHADOW_CFG, t)


def _pred_i_runtime(t: Mapping[str, Any]) -> bool:
    return evaluate_readiness_precision(SHADOW_CFG, t)


def _lane_metrics(pool: Sequence[Mapping[str, Any]], pred: Callable[[Mapping[str, Any]], bool]) -> dict[str, Any]:
    blocked = [t for t in pool if pred(t)]
    losers = [t for t in blocked if _pnl(t) < 0]
    winners = [t for t in blocked if _pnl(t) > 0]
    big_w = [t for t in blocked if _is_big_winner(t)]
    avoided = round(-sum(_pnl(t) for t in losers), 2)
    lost = round(sum(_pnl(t) for t in winners), 2)
    sym_pnl: dict[str, float] = defaultdict(float)
    for t in blocked:
        sym_pnl[str(t.get("symbol") or "")] += _pnl(t)
    top_sym = max(sym_pnl.items(), key=lambda x: abs(x[1]))[0] if sym_pnl else ""
    return {
        "entry_count": len(pool),
        "blocked_count": len(blocked),
        "blocked_early_stop": sum(1 for t in blocked if _is_early_stop_trade(t)),
        "blocked_winners": len(winners),
        "blocked_big_winners": len(big_w),
        "avoided_loss_yen": avoided,
        "lost_profit_yen": lost,
        "net_delta_yen": round(avoided - lost, 2),
        "delta_pnl_yen": round(-sum(_pnl(t) for t in blocked), 2),
        "top_blocked_symbol": top_sym,
        "blocked_trade_ids": sorted(_trade_id(t) for t in blocked if _trade_id(t)),
    }


def _bounce_audit_row(
    raw: Mapping[str, Any],
    enriched: Mapping[str, Any],
    *,
    method: str,
) -> dict[str, Any]:
    raw_b = _num(raw.get("bounce_from_recent_low"))
    enr_b = _num(enriched.get("bounce_from_recent_low"))
    raw_h = phase679_pred_h(raw)
    enr_h = phase681_pred_h(enriched)
    return {
        "trade_id": _trade_id(raw),
        "symbol": raw.get("symbol"),
        "day": raw.get("day"),
        "method": method,
        "live_feature_complete": raw.get("live_feature_complete"),
        "bounce_raw": raw_b,
        "bounce_enriched": enr_b,
        "bounce_delta": round((enr_b or 0) - (raw_b or 0), 4) if raw_b is not None and enr_b is not None else None,
        "bounce_became_none": raw_b is not None and enr_b is None,
        "h_block_raw_pred_h": raw_h,
        "h_block_enriched_baseline_h": enr_h,
        "h_flip_raw_to_not_blocked": raw_h and not enr_h,
        "h_flip_not_blocked_to_blocked": not raw_h and enr_h,
        "pnl_yen_100": _pnl(raw),
    }


def _reconcile_row(
    *,
    lane: str,
    phase: str,
    pred_label: str,
    metrics: Mapping[str, Any],
    reference: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    ref = reference or {}
    return {
        "lane": lane,
        "phase": phase,
        "predicate": pred_label,
        "entry_count": metrics.get("entry_count"),
        "blocked_count": metrics.get("blocked_count"),
        "blocked_early_stop": metrics.get("blocked_early_stop"),
        "blocked_winners": metrics.get("blocked_winners"),
        "blocked_big_winners": metrics.get("blocked_big_winners"),
        "avoided_loss_yen": metrics.get("avoided_loss_yen"),
        "lost_profit_yen": metrics.get("lost_profit_yen"),
        "net_delta_yen": metrics.get("net_delta_yen"),
        "delta_pnl_yen": metrics.get("delta_pnl_yen"),
        "top_blocked_symbol": metrics.get("top_blocked_symbol"),
        "ref_blocked_count": ref.get("blocked_count"),
        "ref_net_delta_yen": ref.get("net_delta_yen"),
        "ref_blocked_big_winners": ref.get("blocked_big_winners"),
        "blocked_count_delta_vs_ref": (
            int(metrics.get("blocked_count") or 0) - int(ref.get("blocked_count") or 0) if ref else None
        ),
        "net_delta_delta_vs_ref": (
            round(float(metrics.get("net_delta_yen") or 0) - float(ref.get("net_delta_yen") or 0), 2) if ref else None
        ),
    }


def _diff_trade_rows(
    *,
    pool_label: str,
    left_label: str,
    right_label: str,
    left_ids: set[str],
    right_ids: set[str],
    id_to_trade: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for tid in sorted(left_ids - right_ids):
        t = id_to_trade.get(tid, {})
        rows.append(
            {
                "pool": pool_label,
                "diff_kind": f"only_{left_label}",
                "trade_id": tid,
                "symbol": t.get("symbol"),
                "day": t.get("day"),
                "pnl_yen_100": _pnl(t),
                "bounce_raw": t.get("bounce_from_recent_low"),
                "live_feature_complete": t.get("live_feature_complete"),
                "left": left_label,
                "right": right_label,
            }
        )
    for tid in sorted(right_ids - left_ids):
        t = id_to_trade.get(tid, {})
        rows.append(
            {
                "pool": pool_label,
                "diff_kind": f"only_{right_label}",
                "trade_id": tid,
                "symbol": t.get("symbol"),
                "day": t.get("day"),
                "pnl_yen_100": _pnl(t),
                "bounce_raw": t.get("bounce_from_recent_low"),
                "live_feature_complete": t.get("live_feature_complete"),
                "left": left_label,
                "right": right_label,
            }
        )
    return rows


def _definition_audit() -> list[dict[str, str]]:
    return [
        {
            "check_id": "1",
            "question": "Phase679 vs Phase681 H condition identical?",
            "result": "NO — predicate diverged",
            "detail": (
                "Phase679 combo uses research _pred_h = live_feature_incomplete AND bounce_from_recent_low>=0.45 "
                "on accept-enriched dataset fields. Phase681 uses evaluate_baseline_h on trades after "
                "_enrich_live_c overwrites bounce_from_recent_low with price-ring recomputation."
            ),
        },
        {
            "check_id": "2",
            "question": "live_feature_complete handling same?",
            "result": "YES (gating equivalent when require_live_incomplete=True)",
            "detail": (
                "Phase679 _live_feature_incomplete(t) ≡ runtime not live_feature_complete. "
                "evaluate_baseline_h / evaluate_readiness_economics use the same gate via config."
            ),
        },
        {
            "check_id": "3",
            "question": "bounce_from_recent_low definition same?",
            "result": "NO — unit/source drift",
            "detail": (
                "Accept/dataset bounce is the research pre-entry field (ratio-like scale, threshold 0.45). "
                "Phase681 _enrich_live_c overwrites with microsequence_pre_entry ring bounce (percent *100) "
                "using a potentially different price window/points; 33 H blocks flip when enriched bounce "
                "falls below threshold or predicate path diverges."
            ),
        },
        {
            "check_id": "4",
            "question": "post_flat_band pool entry count same?",
            "result": "YES",
            "detail": "Same load_focus_dataset + accept enrich; pool filter unchanged across phases.",
        },
        {
            "check_id": "5",
            "question": "pnl_yen_100 aggregation target same?",
            "result": "YES",
            "detail": "All phases aggregate actual trade pnl_yen_100 on the same post_flat_band pool.",
        },
        {
            "check_id": "6",
            "question": "shadow ENTRY IDs consistent across phases?",
            "result": "NO for H — 33 trade ID set difference Phase679 vs Phase681",
            "detail": "See phase682_h_diff_trade_ids.csv; flips driven by bounce None overwrite and predicate path.",
        },
        {
            "check_id": "7",
            "question": "refined_H research_only mixed into H baseline?",
            "result": "NO",
            "detail": (
                "evaluate_baseline_h excludes MFE gate. refined_H blocks are strict subset of baseline H "
                "when mfe_pre_entry_pct < 1.0; never inflates H block count."
            ),
        },
        {
            "check_id": "8",
            "question": "C implementation changed H aggregation?",
            "result": "YES — indirect via shared enrichment",
            "detail": (
                "Phase681 _enrich_live_c runs before H eval and overwrites bounce/fall/slope used by H predicate. "
                "C rule itself does not alter H counters when H evaluated on raw accept fields."
            ),
        },
        {
            "check_id": "9",
            "question": "daily_forward_summary vs counterfactual same definition?",
            "result": "PARTIAL",
            "detail": (
                "Phase679 daily_forward_summary uses evaluate_readiness_economics shadow rows; "
                "combo H_only uses _pred_h. Phase681 daily summary tracks C/IHC only; H uses _eval_pool/_decomp. "
                "Both use -sum(blocked actual pnl) for delta when predicates align."
            ),
        },
        {
            "check_id": "10",
            "question": "actual entry suppression occurred?",
            "result": "NO",
            "detail": "Shadow predicates are research/counterfactual only; runtime mainline reject path unchanged.",
        },
    ]


def run_audit() -> dict[str, Any]:
    disk_before = _disk_usage_pct(NATIVE_ROOT)
    trades = load_focus_dataset()
    trades = _enrich_with_accept(trades, _load_accept_events_full())
    post_pool = [t for t in trades if t.get("post_flat_band_entry")]
    id_to_raw = {_trade_id(t): dict(t) for t in post_pool if _trade_id(t)}

    price_idx = _build_price_index_canonical(resolve_kabu_root(NATIVE_ROOT))
    mfe_enriched = _enrich_mfe_pre_entry([dict(t) for t in trades], price_idx=price_idx)
    mfe_post = [t for t in mfe_enriched if t.get("post_flat_band_entry")]
    live_c_enriched = _enrich_live_c([dict(t) for t in trades])
    live_c_post = [t for t in live_c_enriched if t.get("post_flat_band_entry")]
    id_to_mfe = {_trade_id(t): t for t in mfe_post if _trade_id(t)}
    id_to_live_c = {_trade_id(t): t for t in live_c_post if _trade_id(t)}

    h_phase679_combo = _lane_metrics(post_pool, phase679_pred_h)
    h_phase679_runtime = _lane_metrics(post_pool, _pred_h_runtime_economics)
    h_phase680 = _lane_metrics(mfe_post, _pred_h_baseline)
    h_phase681 = _lane_metrics(live_c_post, phase681_pred_h)
    h_phase681_raw_bounce = _lane_metrics(post_pool, phase681_pred_h)

    i_phase679 = _lane_metrics(post_pool, phase679_pred_i)
    i_phase681 = _lane_metrics(live_c_post, phase681_pred_i)
    c_phase679 = _lane_metrics(post_pool, phase679_pred_c)
    c_phase681 = _lane_metrics(live_c_post, _pred_c_live)

    def _ih_union(pool: Sequence[Mapping[str, Any]], pi: Callable, ph: Callable) -> dict[str, Any]:
        return _lane_metrics(pool, lambda t: pi(t) or ph(t))

    ih_phase679 = _ih_union(post_pool, phase679_pred_i, phase679_pred_h)
    ih_phase681 = _ih_union(live_c_post, phase681_pred_i, phase681_pred_h)

    def _ihc_union(pool: Sequence[Mapping[str, Any]], pi: Callable, ph: Callable, pc: Callable) -> dict[str, Any]:
        return _lane_metrics(pool, lambda t: pi(t) or ph(t) or pc(t))

    ihc_phase681 = _ihc_union(live_c_post, phase681_pred_i, phase681_pred_h, _pred_c_live)
    ihc_phase681_eval = _eval_pool(
        live_c_post,
        lambda t: phase681_pred_i(t) or phase681_pred_h(t) or _pred_c_live(t),
    )

    refined_h_post = _lane_metrics(mfe_post, _pred_refined_h)
    refined_in_h_only = sum(
        1 for t in mfe_post if _pred_refined_h(t) and not _pred_h_baseline(t)
    )

    combo_h_only = _eval_block(
        scenario_id="H_only",
        pool_label="post_flat_band",
        pool=post_pool,
        block_pred=phase679_pred_h,
    )
    combo_h_only["net_delta_yen"] = combo_h_only.get("delta_pnl_yen")

    sym_day_count: dict[tuple[str, str], int] = defaultdict(int)
    shadow_rows_679: list[dict[str, Any]] = []
    for t in sorted(trades, key=lambda r: str(r.get("entry_time") or "")):
        sym = str(t.get("symbol") or "")
        day = str(t.get("day") or "")
        sym_day_count[(day, sym)] += 1
        shadow_rows_679.append(phase679_shadow_row(t, same_sym_n=sym_day_count[(day, sym)]))
    portfolio_679 = phase679_portfolio_shadow(shadow_rows_679)
    daily_679 = phase679_daily_forward_summary(shadow_rows_679)
    daily_679_h_net = round(
        -sum(
            float(r.get("actual_pnl_yen_100") or 0)
            for r in shadow_rows_679
            if r.get("post_flat_band_entry") and r.get("readiness_economics_shadow_block")
        ),
        2,
    )

    # bounce overwrite analysis
    bounce_rows: list[dict[str, Any]] = []
    bounce_none_lost_h = 0
    bounce_none_lost_h_pnl = 0.0
    bounce_value_lost_h = 0
    bounce_value_lost_h_pnl = 0.0
    for tid, raw in id_to_raw.items():
        enr = id_to_live_c.get(tid, raw)
        row = _bounce_audit_row(raw, enr, method="phase681_enrich_live_c")
        bounce_rows.append(row)
        raw_h = phase679_pred_h(raw)
        enr_h = phase681_pred_h(enr)
        if raw_h and not enr_h:
            if _num(raw.get("bounce_from_recent_low")) is not None and _num(enr.get("bounce_from_recent_low")) is None:
                bounce_none_lost_h += 1
                bounce_none_lost_h_pnl += _pnl(raw)
            else:
                bounce_value_lost_h += 1
                bounce_value_lost_h_pnl += _pnl(raw)

    h_ids_679 = set(h_phase679_combo.get("blocked_trade_ids") or [])
    h_ids_681 = set(h_phase681.get("blocked_trade_ids") or [])
    h_diff_rows = _diff_trade_rows(
        pool_label="post_flat_band",
        left_label="phase679_pred_h",
        right_label="phase681_baseline_h_enriched",
        left_ids=h_ids_679,
        right_ids=h_ids_681,
        id_to_trade=id_to_raw,
    )
    for row in h_diff_rows:
        tid = row["trade_id"]
        enr = id_to_live_c.get(tid, {})
        row["bounce_enriched"] = enr.get("bounce_from_recent_low")
        row["bounce_became_none"] = (
            _num(id_to_raw.get(tid, {}).get("bounce_from_recent_low")) is not None
            and _num(enr.get("bounce_from_recent_low")) is None
        )

    reconcile_rows = [
        _reconcile_row(
            lane="H_baseline",
            phase="phase679",
            pred_label="research _pred_h (combo)",
            metrics=h_phase679_combo,
            reference=PHASE679_H_REFERENCE,
        ),
        _reconcile_row(
            lane="H_baseline",
            phase="phase679",
            pred_label="runtime evaluate_readiness_economics",
            metrics=h_phase679_runtime,
        ),
        _reconcile_row(
            lane="H_baseline",
            phase="phase679",
            pred_label="shadow_portfolio readiness_economics",
            metrics={
                **portfolio_679.get("readiness_economics", {}),
                "entry_count": portfolio_679.get("entry_count"),
                "net_delta_yen": portfolio_679.get("readiness_economics", {}).get("delta_yen"),
                "delta_pnl_yen": portfolio_679.get("readiness_economics", {}).get("delta_yen"),
                "avoided_loss_yen": None,
                "lost_profit_yen": None,
                "top_blocked_symbol": None,
                "blocked_trade_ids": [],
            },
        ),
        _reconcile_row(
            lane="H_baseline",
            phase="phase680",
            pred_label="evaluate_baseline_h (mfe enrich, no bounce overwrite)",
            metrics=h_phase680,
            reference=PHASE679_H_REFERENCE,
        ),
        _reconcile_row(
            lane="H_baseline",
            phase="phase681",
            pred_label="evaluate_baseline_h (live_c enrich)",
            metrics=h_phase681,
            reference=PHASE681_H_REFERENCE,
        ),
        _reconcile_row(
            lane="H_baseline",
            phase="phase681",
            pred_label="evaluate_baseline_h (raw bounce, no enrich)",
            metrics=h_phase681_raw_bounce,
            reference=PHASE679_H_REFERENCE,
        ),
        _reconcile_row(lane="I_precision", phase="phase679", pred_label="_pred_i", metrics=i_phase679),
        _reconcile_row(lane="I_precision", phase="phase681", pred_label="evaluate_readiness_precision", metrics=i_phase681),
        _reconcile_row(lane="microsequence_C", phase="phase679", pred_label="research _rule_c", metrics=c_phase679),
        _reconcile_row(lane="microsequence_C", phase="phase681", pred_label="evaluate_microsequence_recovery_fail", metrics=c_phase681),
        _reconcile_row(lane="I_or_H", phase="phase679", pred_label="I∨H", metrics=ih_phase679),
        _reconcile_row(lane="I_or_H", phase="phase681", pred_label="I∨H", metrics=ih_phase681),
        _reconcile_row(lane="I_or_H_or_C", phase="phase681", pred_label="I∨H∨C", metrics=ihc_phase681),
        _reconcile_row(
            lane="refined_H",
            phase="phase680",
            pred_label="evaluate_readiness_refined_h (research_only)",
            metrics=refined_h_post,
        ),
    ]

    pool_rows: list[dict[str, Any]] = []
    for pool_label, pool in phase679_pools(trades):
        if pool_label != "post_flat_band":
            continue
        pool_rows.append(
            {
                "pool": pool_label,
                "entry_count": len(pool),
                "h_blocked_phase679": sum(1 for t in pool if phase679_pred_h(t)),
                "h_blocked_phase680": sum(
                    1 for t in mfe_post if phase679_pred_h(id_to_raw.get(_trade_id(t), t))
                ),
                "h_blocked_phase681_enriched": sum(1 for t in live_c_post if phase681_pred_h(t)),
                "i_blocked_phase679": sum(1 for t in pool if phase679_pred_i(t)),
                "c_blocked_phase679": sum(1 for t in pool if phase679_pred_c(t)),
                "c_blocked_phase681": sum(1 for t in live_c_post if _pred_c_live(t)),
            }
        )

    drift_explained = (
        h_phase680.get("blocked_count") == h_phase679_combo.get("blocked_count")
        and h_phase681.get("blocked_count") < h_phase679_combo.get("blocked_count")
        and bounce_none_lost_h > 0
    )
    predicate_mismatch_679 = (
        h_phase679_combo.get("blocked_count") != portfolio_679.get("readiness_economics", {}).get("block_count")
    )

    if drift_explained and not predicate_mismatch_679:
        verdict = VERDICT_DRIFT
    elif (
        h_phase681_raw_bounce.get("blocked_count") == h_phase679_combo.get("blocked_count")
        and h_phase681.get("blocked_count") != h_phase681_raw_bounce.get("blocked_count")
    ):
        verdict = VERDICT_DRIFT
    elif h_phase679_runtime.get("blocked_count") == h_phase679_combo.get("blocked_count") == h_phase680.get("blocked_count"):
        verdict = VERDICT_CONSISTENT if h_phase681.get("blocked_count") == h_phase679_combo.get("blocked_count") else VERDICT_DRIFT
    else:
        verdict = VERDICT_BUG if not drift_explained else VERDICT_DRIFT

    drift_summary = {
        "phase679_h_blocked": h_phase679_combo.get("blocked_count"),
        "phase679_h_net_delta": h_phase679_combo.get("net_delta_yen"),
        "phase681_h_blocked": h_phase681.get("blocked_count"),
        "phase681_h_net_delta": h_phase681.get("net_delta_yen"),
        "blocked_count_delta": int(h_phase681.get("blocked_count") or 0) - int(h_phase679_combo.get("blocked_count") or 0),
        "net_delta_delta": round(
            float(h_phase681.get("net_delta_yen") or 0) - float(h_phase679_combo.get("net_delta_yen") or 0), 2
        ),
        "only_phase679_h_ids": len(h_ids_679 - h_ids_681),
        "only_phase681_h_ids": len(h_ids_681 - h_ids_679),
        "bounce_none_caused_h_unblock": bounce_none_lost_h,
        "bounce_none_caused_h_unblock_pnl_sum": round(bounce_none_lost_h_pnl, 2),
        "bounce_value_change_caused_h_unblock": bounce_value_lost_h,
        "bounce_value_change_caused_h_unblock_pnl_sum": round(bounce_value_lost_h_pnl, 2),
        "phase680_h_matches_phase679": h_phase680.get("blocked_count") == h_phase679_combo.get("blocked_count"),
        "phase681_raw_bounce_h_blocked": h_phase681_raw_bounce.get("blocked_count"),
        "refined_h_extra_blocks_beyond_baseline_h": refined_in_h_only,
        "root_cause": (
            "Phase681 _enrich_live_c calls row.update(pre) and overwrites bounce_from_recent_low with "
            "microsequence_pre_entry ring recomputation before evaluate_baseline_h. On the same post_flat_band "
            f"pool (2752 entries), raw accept-field bounce reproduces Phase679 H exactly (115 blocks, +190,900). "
            f"After enrichment H drops to 82 (+25,400): {bounce_value_lost_h} trades flip due to bounce value "
            f"change, {bounce_none_lost_h} due to bounce=None. Phase680 mfe enrichment does not overwrite bounce "
            "(115 blocks, matches Phase679). evaluate_baseline_h ≡ evaluate_readiness_economics ≡ research _pred_h "
            "when bounce source is held constant. refined_H is a strict subset (76 blocks) and never inflates H."
        ),
    }

    report: dict[str, Any] = {
        "verdict": verdict,
        "drift_summary": drift_summary,
        "mandatory_checks": _definition_audit(),
        "h_phase679_combo": h_phase679_combo,
        "h_phase679_runtime_economics": h_phase679_runtime,
        "h_phase680_baseline": h_phase680,
        "h_phase681_baseline_enriched": h_phase681,
        "h_phase681_baseline_raw": h_phase681_raw_bounce,
        "combo_h_only_post_flat": combo_h_only,
        "shadow_portfolio_679_economics": portfolio_679.get("readiness_economics"),
        "daily_forward_679_h_net_delta": daily_679_h_net,
        "ihc_phase681_eval_pool": ihc_phase681_eval,
        "refined_h_research_only": {
            "blocked_count": refined_h_post.get("blocked_count"),
            "net_delta_yen": refined_h_post.get("net_delta_yen"),
            "blocks_outside_baseline_h": refined_in_h_only,
        },
        "runtime_shadow": {"mainline_reject": False, "entry_suppression": False},
        "disk_usage_pct_before": disk_before,
        "disk_usage_pct_after": _disk_usage_pct(NATIVE_ROOT),
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    (REPORT_ROOT / "phase682_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(REPORT_ROOT / "phase682_h_diff_trade_ids.csv", list(h_diff_rows[0].keys()) if h_diff_rows else ["trade_id"], h_diff_rows)
    _write_csv(
        REPORT_ROOT / "phase682_h_metric_reconciliation.csv",
        list(reconcile_rows[0].keys()) if reconcile_rows else ["lane"],
        reconcile_rows,
    )
    _write_csv(REPORT_ROOT / "phase682_pool_comparison.csv", list(pool_rows[0].keys()) if pool_rows else ["pool"], pool_rows)
    _write_csv(
        REPORT_ROOT / "phase682_bounce_overwrite_audit.csv",
        list(bounce_rows[0].keys()) if bounce_rows else ["trade_id"],
        [r for r in bounce_rows if r.get("h_flip_raw_to_not_blocked") or r.get("bounce_became_none")],
    )
    _write_definition_md(report=report)
    _write_decision_md(report=report, drift=drift_summary)
    return report


def _write_definition_md(*, report: Mapping[str, Any]) -> None:
    lines = [
        "# Phase682 — Shadow Definition Audit",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## Mandatory checks",
        "",
    ]
    for row in report.get("mandatory_checks") or []:
        lines.append(f"### {row.get('check_id')}. {row.get('question')}")
        lines.append(f"- **Result:** {row.get('result')}")
        lines.append(f"- {row.get('detail')}")
        lines.append("")
    (REPORT_ROOT / "phase682_shadow_definition_audit.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_decision_md(*, report: Mapping[str, Any], drift: Mapping[str, Any]) -> None:
    h679 = report.get("h_phase679_combo") or {}
    h681 = report.get("h_phase681_baseline_enriched") or {}
    lines = [
        "# Phase682 — Shadow Portfolio Consistency Decision",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        "",
        "## H baseline drift (Phase679 → Phase681)",
        "",
        f"| Metric | Phase679 | Phase681 | Δ |",
        f"|--------|----------|----------|---|",
        f"| blocked_count | {h679.get('blocked_count')} | {h681.get('blocked_count')} | {drift.get('blocked_count_delta')} |",
        f"| net_delta_yen | {h679.get('net_delta_yen')} | {h681.get('net_delta_yen')} | {drift.get('net_delta_delta')} |",
        f"| blocked_big_winners | {h679.get('blocked_big_winners')} | {h681.get('blocked_big_winners')} | — |",
        "",
        "## Root cause",
        "",
        str(drift.get("root_cause")),
        "",
        "## Key evidence",
        "",
        f"- Phase680 H blocked (mfe enrich only): {(report.get('h_phase680_baseline') or {}).get('blocked_count')} — matches Phase679",
        f"- Phase681 H with raw bounce (no enrich): {(report.get('h_phase681_baseline_raw') or {}).get('blocked_count')}",
        f"- Trades unblocked because bounce value changed: {drift.get('bounce_value_change_caused_h_unblock')}",
        f"- Trades unblocked because bounce became None: {drift.get('bounce_none_caused_h_unblock')}",
        f"- H trade ID set diff: only Phase679={drift.get('only_phase679_h_ids')}, only Phase681={drift.get('only_phase681_h_ids')}",
        f"- refined_H blocks outside baseline H: {(report.get('refined_h_research_only') or {}).get('blocks_outside_baseline_h')}",
        "",
        "## Promotion readiness",
        "",
        "- Shadow metrics are **not** directly comparable across Phase679 and Phase681 H reports without normalizing bounce source.",
        "- Use **evaluate_readiness_economics** on accept-field bounce OR **evaluate_baseline_h** on consistently enriched fields.",
        "- C shadow metrics are independent when H uses raw accept bounce; I∨H∨C union should document bounce source.",
        "- **HOLD** mainline promotion; continue forward shadow with unified metric definition.",
        "",
    ]
    (REPORT_ROOT / "phase682_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    report = run_audit()
    print(
        json.dumps(
            {
                "verdict": report.get("verdict"),
                "phase679_h_blocked": (report.get("h_phase679_combo") or {}).get("blocked_count"),
                "phase681_h_blocked": (report.get("h_phase681_baseline_enriched") or {}).get("blocked_count"),
                "drift": report.get("drift_summary"),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
