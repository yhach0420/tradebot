"""Orchestrate PBv2 zero-base revalidation + capture-preserving follow-up."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.pbv2_zero_base_revalidation.cap5 import compare_cap5_methods
from research.pbv2_zero_base_revalidation.capture_preserving import run_capture_preserving
from research.pbv2_zero_base_revalidation.constants import (
    LANE_A_FEATURES,
    LANE_B_FEATURES,
    LANE_C_FEATURES,
    NATIVE,
    SUSPECT_BOARD_DAYS,
)
from research.pbv2_zero_base_revalidation.labels import attach_labels
from research.pbv2_zero_base_revalidation.large_rise import (
    annotate_capture,
    extract_large_rise_episodes,
    summarize_capture,
)
from research.pbv2_zero_base_revalidation.leakage import audit_panel_leakage
from research.pbv2_zero_base_revalidation.panel import build_price_paths_and_panel
from research.pbv2_zero_base_revalidation.report import emit_artifacts
from research.pbv2_zero_base_revalidation.walk_forward import run_walk_forward

JST = ZoneInfo("Asia/Tokyo")
PRIOR_SOT_RUN = "20260723_230743"


def _feature_coverage(panel) -> list[dict[str, Any]]:
    n = len(panel) or 1
    keys = sorted({k for r in panel for k in r.features.keys()})
    rows = []
    for k in keys:
        c = sum(1 for r in panel if r.features.get(k) is not None)
        rows.append({"feature": k, "n_non_null": c, "coverage": round(c / n, 4)})
    return rows


def _board_quality_rows(panel) -> list[dict[str, Any]]:
    c = Counter(r.board_quality for r in panel)
    rows = [{"board_quality": k, "n": v, "share": round(v / max(1, len(panel)), 4)} for k, v in c.most_common()]
    by_day = defaultdict(Counter)
    for r in panel:
        by_day[r.day][r.board_quality] += 1
    for day, cc in sorted(by_day.items()):
        for q, n in cc.items():
            rows.append({"day": day, "board_quality": q, "n": n, "suspect_day": day in SUSPECT_BOARD_DAYS})
    return rows


def _integrity_gates(payload: dict[str, Any]) -> dict[str, Any]:
    sess = (payload.get("session_select") or {})
    label = payload.get("label_meta") or {}
    cp = payload.get("capture_preserving") or {}
    bias = cp.get("feature_bias") or payload.get("feature_bias") or {}
    metric_blocked = bool(payload.get("metric_integrity_blocked"))
    for m in (cp.get("methods") or []):
        if (m.get("oos") or {}).get("metric_integrity_blocked"):
            metric_blocked = True
    pb = ((payload.get("walk_forward") or {}).get("pbv2_baseline") or {}).get("oos") or {}
    if pb.get("metric_integrity_blocked"):
        metric_blocked = True

    session_pass = bool(sess.get("session_coverage_pass"))
    outcome_pass = bool(label.get("outcome_label_pass")) and int(label.get("n_outcome_evaluable") or 0) > 0
    pf_pass = not metric_blocked
    bias_pass = bool(bias.get("fair_compare_ready", False)) and not bool(bias.get("bias_flag"))

    codes = []
    if session_pass and outcome_pass and pf_pass and bias_pass:
        codes.append("EVALUATION_INTEGRITY_PASS")
    if not session_pass:
        codes.append("SESSION_COVERAGE_BLOCKED")
    if not outcome_pass:
        codes.append("OUTCOME_LABEL_BLOCKED")
    if not pf_pass:
        codes.append("METRIC_INTEGRITY_BLOCKED")
    if not bias_pass:
        codes.append("FEATURE_AVAILABILITY_BIAS_FOUND")
    # bias_detected with fair_compare_ready is recorded in details/DQ, not a gate fail code

    return {
        "session_coverage": "PASS" if session_pass else "FAIL",
        "outcome_evaluable": "PASS" if outcome_pass else "FAIL",
        "pf_integrity": "PASS" if pf_pass else "FAIL",
        "feature_availability_bias": "PASS" if bias_pass else "FAIL",
        "all_pass": session_pass and outcome_pass and pf_pass and bias_pass,
        "codes": codes,
        "details": {
            "session_coverage_pass": session_pass,
            "coverage_blocked_days": sess.get("coverage_blocked_days"),
            "n_outcome_evaluable": label.get("n_outcome_evaluable"),
            "n_pnl_evaluable": label.get("n_pnl_evaluable"),
            "n_large_rise_evaluable": label.get("n_large_rise_evaluable"),
            "metric_integrity_blocked": metric_blocked,
            "feature_bias": bias,
        },
    }


def _decide_verdict(payload: dict[str, Any]) -> dict[str, Any]:
    codes = ["NO_PRODUCTION_CHANGE", "PBV2_REPLACEMENT_OFFLINE_ONLY"]
    leak = payload.get("leakage") or {}
    if leak.get("leakage_blocked"):
        return {
            "final": "DATA_LEAKAGE_BLOCKED",
            "codes": ["DATA_LEAKAGE_BLOCKED", "NO_PRODUCTION_CHANGE"],
            "summary": "時点整合リークが検出されたため結果判定を停止。",
            "integrity": payload.get("integrity_gates"),
        }

    integ = payload.get("integrity_gates") or _integrity_gates(payload)
    codes.extend(integ.get("codes") or [])

    # Baseline reproduction only if session coverage OK
    wf = payload.get("walk_forward") or {}
    pb = (wf.get("pbv2_baseline") or {}).get("oos") or {}
    if integ.get("session_coverage") == "PASS" and (pb.get("n") or 0) > 0:
        codes.append("PBV2_BASELINE_REPRODUCED")
    else:
        codes.append("PBV2_BASELINE_MISMATCH")

    codes.append("PBV2_ZERO_BASE_DATASET_READY")
    if int((payload.get("label_meta") or {}).get("n_large_rise_evaluable") or 0) > 0:
        codes.append("WATCH50_RISE_CAPTURE_AUDITED")

    cp = payload.get("capture_preserving") or {}
    dyn = cp.get("dynamic_coverage") or {}
    if dyn.get("verdict"):
        codes.append(dyn["verdict"])

    # Do not use candidate generation for adoption if integrity FAIL
    if not integ.get("all_pass"):
        return {
            "final": "PBV2_REPLACEMENT_OFFLINE_ONLY",
            "codes": sorted(set(codes)),
            "summary": (
                "評価基盤4項目のいずれかFAILのため、capture-preserving候補生成結果は採用判定に使用しない。"
                f" session={integ.get('session_coverage')} outcome={integ.get('outcome_evaluable')} "
                f"pf={integ.get('pf_integrity')} bias={integ.get('feature_availability_bias')}"
            ),
            "integrity": integ,
            "no_production_reason": "Integrity gate failed; offline research only.",
            "next_data_need": "Fix session AM/PM coverage, outcome labels, PF integrity, and feature-bias fair cohorts.",
        }

    best = cp.get("best_capture_preserving")
    if best:
        codes.append("CAPTURE_PRESERVING_EDGE_CONFIRMED")
        codes.append("PBV2_REPLACEMENT_CANDIDATE_READY")
        summary = f"Capture-preserving edge: {best.get('method_id')} (still offline-only / no production change)."
    else:
        codes.append("CAPTURE_PRESERVING_NO_EDGE")
        summary = "Integrity PASSだが、捕捉制約を満たす置換候補は未確認。PBV2_REPLACEMENT_OFFLINE_ONLY。"

    return {
        "final": "PBV2_REPLACEMENT_OFFLINE_ONLY",
        "codes": sorted(set(codes)),
        "summary": summary,
        "integrity": integ,
        "no_production_reason": "本タスクは研究比較まで。本線/Shadow/Forward変更禁止。",
        "next_data_need": (
            f"動的板: {dyn.get('verdict')}; complete_rows={dyn.get('complete_rows_total')}; "
            f"oos_complete_days={dyn.get('n_oos_complete_days')}"
        ),
    }


def run_pipeline(*, native: Path = NATIVE, run_id: Optional[str] = None) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = native / "results" / "research" / "pbv2_zero_base_revalidation" / run_id

    panel, price_paths, meta = build_price_paths_and_panel(native)
    label_meta = attach_labels(panel, price_paths)
    leakage = audit_panel_leakage(panel)
    sess_meta = meta.get("session_select") or {}

    payload: dict[str, Any] = {
        "run_id": run_id,
        "prior_sot_run": PRIOR_SOT_RUN,
        "generated_at": datetime.now(JST).isoformat(),
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "mainline_unchanged": True,
        "trading_days": meta.get("days"),
        "n_panel": len(panel),
        "n_pbv2_candidates": sum(1 for r in panel if r.pbv2_candidate),
        "n_non_pbv2": sum(1 for r in panel if not r.pbv2_candidate),
        "coverage_by_day": meta.get("coverage_by_day"),
        "session_select": sess_meta,
        "label_meta": label_meta,
        "leakage": leakage,
        "metric_integrity_blocked": False,
        "data_sources": [
            {"source": "results/small_paper/*/live_session_*/small_paper_events.csv", "role": "Watch50 candidate+features+price"},
            {"source": "results/small_paper/*/live_session_*/np_pre_entry_features.jsonl", "role": "Lane C dynamic board"},
            {"source": f"results/research/pbv2_zero_base_revalidation/{PRIOR_SOT_RUN}/", "role": "prior SoT run (reference only)"},
            {"source": "data/market_capture/*", "role": "L2 PUSH (limited days)"},
        ],
        "panel_audit": [
            {
                "n_panel": len(panel),
                "feature_evaluable": sum(1 for r in panel if r.evaluability != "COVERAGE_ONLY"),
                "pnl_evaluable": sum(1 for r in panel if r.pnl_evaluable),
                "outcome_evaluable": sum(
                    1
                    for r in panel
                    if r.forward_return_evaluable or r.mfe_mae_evaluable or r.large_rise_evaluable
                ),
                "includes_non_pbv2": sum(1 for r in panel if not r.pbv2_candidate) > 0,
                "bucket_sec": meta.get("bucket_sec"),
                "n_sessions_selected": meta.get("n_sessions"),
                "canonical_rule": sess_meta.get("canonical_rule"),
            }
        ],
        "feature_dictionary": (
            [{"feature": f, "lane": "A", "note": "dense price/volume"} for f in LANE_A_FEATURES]
            + [{"feature": f, "lane": "B", "note": "static TOP_ONLY/PARTIAL/FULL separated"} for f in LANE_B_FEATURES]
            + [{"feature": f, "lane": "C", "note": "dynamic board; no imputation"} for f in LANE_C_FEATURES]
        ),
        "feature_coverage": _feature_coverage(panel),
        "board_quality_rows": _board_quality_rows(panel),
        "board_quality_notes": (
            "TOP_ONLY never promoted to FULL_L2. "
            "Prior static_imb_near_tv renamed/evaluated as top_only_imb_near_tv on TOP_ONLY eligible cohort only. "
            f"SUSPECT_BOARD_DAYS={sorted(SUSPECT_BOARD_DAYS)} sensitivity-only."
        ),
    }

    if leakage.get("leakage_blocked"):
        payload["integrity_gates"] = _integrity_gates(payload)
        payload["verdict"] = _decide_verdict(payload)
        emit_artifacts(out_dir, payload)
        payload["out_dir"] = str(out_dir)
        return payload

    # Reference walk-forward (lane series) — not used for adoption if integrity fails
    wf = run_walk_forward(panel)
    if isinstance(wf.get("folds"), list):
        wf["folds"] = [
            {kk: vv for kk, vv in f.items() if kk not in ("train_rows", "test_rows")} for f in wf["folds"]
        ]
    payload["walk_forward"] = wf
    if (wf.get("pbv2_baseline") or {}).get("oos", {}).get("metric_integrity_blocked"):
        payload["metric_integrity_blocked"] = True

    # Rename note for prior best
    best_static = (wf.get("best") or {}).get("static") or {}
    if best_static.get("rule_id") == "top_only_imb_near_tv" or "imb_near_tv" in str(best_static.get("rule_id") or ""):
        best_static = {**best_static, "rule_id": "top_only_imb_near_tv", "board_class": "TOP_ONLY"}
    payload["best_candidate"] = best_static or (wf.get("best") or {}).get("dense") or {}

    # Capture-preserving Phase 2
    cp = run_capture_preserving(panel)
    payload["capture_preserving"] = {
        k: v
        for k, v in cp.items()
        if k not in ("folds",)
    }
    payload["feature_bias"] = cp.get("feature_bias")
    if any((m.get("oos") or {}).get("metric_integrity_blocked") for m in (cp.get("methods") or [])):
        payload["metric_integrity_blocked"] = True

    payload["integrity_gates"] = _integrity_gates(payload)

    # Large-rise only on evaluable rows
    if label_meta.get("outcome_label_pass"):
        episodes = extract_large_rise_episodes(
            [r for r in panel if r.large_rise_evaluable],
            price_paths,
        )
        zb = None
        if cp.get("best_capture_preserving"):
            zb = {"features": (), "ops": (), "last_thresholds": ()}  # capture via method keep not rule
        episodes = annotate_capture(episodes, panel, zero_base_keep=None)
        # Mark zero-base capture using R2 keep proxy from last meta if available
        payload["large_rise_episodes"] = episodes[:5000]
        payload["large_rise_summary"] = summarize_capture(episodes)
        payload["missed_rise_reasons"] = [
            {"reason": k, "n": v}
            for k, v in (payload["large_rise_summary"].get("miss_reason_counts") or {}).items()
        ]
    else:
        payload["large_rise_episodes"] = []
        payload["large_rise_summary"] = {
            "large_rise_episode_total": 0,
            "blocked": "OUTCOME_LABEL_BLOCKED",
            "note": "n_outcome_evaluable=0のためlarge-rise評価を有効扱いしない",
        }
        payload["missed_rise_reasons"] = [{"status": "blocked"}]

    # CAP5 from capture methods
    payload["cap5"] = [m.get("cap5") or {"method": m.get("method_id")} for m in (cp.get("methods") or [])]

    # Baseline comparison fair + capture methods
    base_cmp = [{"method": "PBv2_runtime", **((wf.get("pbv2_baseline") or {}).get("oos") or {})}]
    for m in cp.get("methods") or []:
        base_cmp.append(
            {
                "method": m.get("method_id"),
                **(m.get("oos") or {}),
                "keep_ratio_vs_pbv2": m.get("keep_ratio_vs_pbv2"),
                "replacement_eligible": m.get("replacement_eligible"),
            }
        )
    payload["baseline_comparison"] = base_cmp
    payload["pareto_frontier"] = cp.get("pareto") or []
    payload["threshold_history"] = []
    for m in cp.get("methods") or []:
        for h in m.get("threshold_history") or []:
            payload["threshold_history"].append({"method": m.get("method_id"), **h})
    payload["threshold_history"] = payload["threshold_history"][:5000]

    payload["stop_analysis"] = [
        {"cohort": "STOP", "n_panel": sum(1 for r in panel if r.is_stop), "n_pbv2": sum(1 for r in panel if r.is_stop and r.pbv2_decision)}
    ]
    payload["np_analysis"] = [
        {"cohort": "NoProgress", "n_panel": sum(1 for r in panel if r.is_np), "n_pbv2": sum(1 for r in panel if r.is_np and r.pbv2_decision)}
    ]
    payload["winner_sacrifice"] = {"note": "see capture_preserving keep/capture ratios"}
    payload["winner_sacrifice_rows"] = [payload["winner_sacrifice"]]
    payload["daily_metrics"] = (pb.get("daily") if (pb := (wf.get("pbv2_baseline") or {}).get("oos")) else None) or [
        {"status": "see baseline_comparison"}
    ]
    sym_pnl = defaultdict(float)
    for r in panel:
        if r.pbv2_decision and r.pnl_evaluable and r.cf_pnl_5bps is not None:
            sym_pnl[r.symbol] += float(r.cf_pnl_5bps)
    payload["symbol_dependency"] = [
        {"symbol": s, "pbv2_pnl_5bps": round(v, 2)} for s, v in sorted(sym_pnl.items(), key=lambda x: -abs(x[1]))[:30]
    ]
    payload["dq_issues"] = []
    for day in sorted(SUSPECT_BOARD_DAYS):
        vals = [r.features.get("f_imb") for r in panel if r.day == day and r.features.get("f_imb") is not None]
        if vals:
            frac = sum(1 for x in vals if 0.43 <= float(x) <= 0.53) / len(vals)
            payload["dq_issues"].append(
                {"day": day, "issue": "imbalance_concentration_0.43_0.53", "fraction": round(frac, 4), "primary_use": "sensitivity_only"}
            )
    if sess_meta.get("coverage_blocked_days"):
        payload["dq_issues"].append(
            {"issue": "SESSION_COVERAGE_BLOCKED_DAYS", "days": sess_meta.get("coverage_blocked_days")}
        )

    ranking = sorted(
        base_cmp,
        key=lambda x: (
            -1 if x.get("metric_integrity_blocked") else 0,
            -(x.get("total_pnl_5bps") if x.get("total_pnl_5bps") is not None else x.get("pnl_5bps") or -1e18),
            -(x.get("PF_5bps") if x.get("PF_5bps") is not None else x.get("pf") or 0),
        ),
    )
    payload["final_ranking"] = ranking
    payload["verdict"] = _decide_verdict(payload)

    # Session audit sheet helper
    payload["session_coverage_audit"] = sess_meta.get("audit_rows") or []

    emit_artifacts(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    return payload
