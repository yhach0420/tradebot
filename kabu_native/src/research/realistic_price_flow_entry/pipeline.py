"""RPFE offline research pipeline."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.pbv2_zero_base_revalidation.labels import attach_labels
from research.pbv2_zero_base_revalidation.leakage import audit_panel_leakage
from research.pbv2_zero_base_revalidation.panel import build_price_paths_and_panel
from research.realistic_price_flow_entry.constants import NATIVE, SOT_DIR, SOT_RUN
from research.realistic_price_flow_entry.evaluate import run_oos
from research.realistic_price_flow_entry.features import feature_lineage
from research.realistic_price_flow_entry.report import emit_artifacts
from research.realistic_price_flow_entry.state_machine import PATTERN_A_SPECS, PATTERN_B_SPECS

JST = ZoneInfo("Asia/Tokyo")


def _load_sot_integrity() -> dict[str, Any]:
    path = SOT_DIR / "report.json"
    if not path.exists():
        return {"all_pass": False, "error": f"missing SoT {path}"}
    d = json.loads(path.read_text(encoding="utf-8"))
    return d.get("integrity_gates") or {"all_pass": False}


def _decide_verdict(payload: dict[str, Any]) -> dict[str, Any]:
    """Integrity gates first; temporal edge only after all PASS. Default final=RPFE_OFFLINE_ONLY."""
    codes = ["NO_PRODUCTION_CHANGE", "RPFE_OFFLINE_ONLY"]
    if payload.get("leakage", {}).get("leakage_blocked"):
        return {
            "final": "DATA_LEAKAGE_BLOCKED",
            "codes": ["DATA_LEAKAGE_BLOCKED", "NO_PRODUCTION_CHANGE", "RPFE_OFFLINE_ONLY"],
            "summary": "リーク検出のため判定停止",
        }
    sot = payload.get("sot_integrity") or {}
    if not sot.get("all_pass"):
        codes.append("METRIC_INTEGRITY_BLOCKED")
        return {
            "final": "RPFE_OFFLINE_ONLY",
            "codes": sorted(set(codes)),
            "summary": "SoT EVALUATION_INTEGRITY_PASS 未確認のため採用判定不可",
            "no_production_reason": "SoT integrity missing",
        }

    ev = payload.get("evaluation") or {}
    methods = ev.get("methods") or {}
    edges = ev.get("edges") or {}
    dyn = ev.get("dynamic_coverage") or {}
    sm = ev.get("state_machine_integrity") or {}
    px = ev.get("price_cross_integrity") or {}
    early = ev.get("early_stop_label_audit") or {}
    matched = ev.get("matched_comparison") or {}

    if dyn.get("verdict") == "RPFE_FLOW_INSUFFICIENT_DATA":
        codes.append("RPFE_FLOW_INSUFFICIENT_DATA")

    sm_code = sm.get("verdict") or "STATE_MACHINE_INTEGRITY_BLOCKED"
    codes.append(sm_code)
    px_code = px.get("verdict") or "PRICE_TRIGGER_PROXY_REJECTED"
    codes.append(px_code)
    early_code = early.get("verdict") or "EARLY_STOP_LABEL_BLOCKED"
    codes.append(early_code)
    day_code = matched.get("verdict") or "DAY_MATCHED_COMPARISON_BLOCKED"
    codes.append(day_code)

    if any((m.get("oos") or {}).get("metric_integrity_blocked") for m in methods.values()):
        codes.append("METRIC_INTEGRITY_BLOCKED")
        return {
            "final": "RPFE_OFFLINE_ONLY",
            "codes": sorted(set(codes)),
            "summary": "PF/PnL整合性エラー",
        }

    integrity_pass = (
        sm.get("gate_ok") is True
        and px.get("gate_ok") is True
        and early.get("gate_ok") is True
        and matched.get("gate_ok") is True
    )

    if integrity_pass:
        # EDGE only after evaluation foundation PASS
        has_edge = bool(
            edges.get("R5_A_OR_B_PRICE") or edges.get("R7_PBv2_OR_RPFE") or edges.get("R8_PBv2_AND_RPFE")
            or edges.get("R1_Pullback_PRICE") or edges.get("R3_Compression_PRICE")
        )
        codes.append("RPFE_TEMPORAL_EDGE_CONFIRMED" if has_edge else "RPFE_TEMPORAL_NO_EDGE")
    else:
        codes.append("RPFE_TEMPORAL_NO_EDGE")

    checklist = {
        "same_timestamp_multi_step": sm.get("same_timestamp_multi_step_entries"),
        "latency_zero": sm.get("latency_zero_entries"),
        "states_advanced_gt1": sm.get("states_advanced_gt1_per_obs"),
        "real_micro_high_cross": sm.get("real_micro_high_cross_n"),
        "real_range_high_cross": sm.get("real_range_high_cross_n"),
        "early_stop_label": early_code,
        "day_matched": day_code,
    }

    return {
        "final": "RPFE_OFFLINE_ONLY",
        "codes": sorted(set(codes)),
        "summary": (
            "RPFE真時間状態機械の再検証完了（特徴探索なし）。"
            + (" 評価基盤PASS。" if integrity_pass else " 評価基盤にブロックあり。")
            + " 既定判定 RPFE_OFFLINE_ONLY。本線変更なし。"
        ),
        "integrity_pass": integrity_pass,
        "checklist": checklist,
        "no_production_reason": "本線/Shadow/Forward変更禁止。研究のみ。",
        "next_data_need": (
            f"dynamic complete rows={dyn.get('complete_rows_total')} "
            f"(need>={2000}), AM/PM complete days, OOS days={dyn.get('n_oos_days')}"
        ),
    }


def run_pipeline(*, native: Path = NATIVE, run_id: Optional[str] = None) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = native / "results" / "research" / "realistic_price_flow_entry" / run_id
    print(f"[rpfe] start run_id={run_id}", flush=True)

    sot_integrity = _load_sot_integrity()
    print(f"[rpfe] SoT integrity all_pass={sot_integrity.get('all_pass')}", flush=True)

    panel, price_paths, meta = build_price_paths_and_panel(native)
    label_meta = attach_labels(panel, price_paths)
    leakage = audit_panel_leakage(panel)

    payload: dict[str, Any] = {
        "run_id": run_id,
        "sot_run": SOT_RUN,
        "sot_integrity": sot_integrity,
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
        "label_meta": label_meta,
        "leakage": leakage,
        "data_sources": [
            {"source": str(SOT_DIR), "role": "evaluation integrity SoT"},
            {"source": "results/small_paper/*/live_session_*/small_paper_events.csv", "role": "Watch50 panel"},
            {"source": "results/small_paper/*/live_session_*/np_pre_entry_features.jsonl", "role": "dynamic board"},
        ],
        "feature_lineage": feature_lineage(),
        "state_definitions": [
            {"state": s, "note": "ordered; no IDLE→ENTRY skip"}
            for s in (
                "IDLE",
                "CONTEXT_READY",
                "SETUP_DETECTED",
                "SELL_PRESSURE_WEAKENED",
                "BUY_PRESSURE_CONFIRMED",
                "PRICE_TRIGGERED",
                "ENTRY",
                "INVALIDATED",
            )
        ],
        "state_transitions": [
            {
                "rule": "true temporal: max 1 state/obs; PRICE_TRIGGERED+ENTRY same obs only with real cross; "
                "CONTEXT→BUY same-ts forbidden; gap→IDLE; entry cooldown; session-scoped"
            }
        ],
        "invalidations": [
            {"reason": r}
            for r in (
                "price_stale",
                "board_stale",
                "price_history_insufficient",
                "chase_overheat",
                "pullback_excessive_atr",
                "accelerating_down",
                "spread_widening",
                "new_low_pressure",
                "context_lost",
            )
        ],
        "pullback_reclaim": [
            {"state": st, "specs": [list(x) for x in specs]} for st, specs in PATTERN_A_SPECS.items()
        ],
        "compression_breakout": [
            {"state": st, "specs": [list(x) for x in specs]} for st, specs in PATTERN_B_SPECS.items()
        ],
    }

    if leakage.get("leakage_blocked") or not sot_integrity.get("all_pass"):
        payload["evaluation"] = {}
        payload["verdict"] = _decide_verdict(payload)
        emit_artifacts(out_dir, payload)
        payload["out_dir"] = str(out_dir)
        return payload

    evaluation = run_oos(panel, price_paths=price_paths)
    payload["evaluation"] = evaluation

    # overlap stats
    methods = evaluation.get("methods") or {}
    k0_n = (methods.get("R0_PBv2") or {}).get("oos", {}).get("n") or 0
    k5_n = (methods.get("R5_A_OR_B_PRICE") or {}).get("oos", {}).get("n") or 0
    k8_n = (methods.get("R8_PBv2_AND_RPFE") or {}).get("oos", {}).get("n") or 0
    payload["overlap"] = {
        "pbv2_n": k0_n,
        "rpfe_price_or_n": k5_n,
        "pbv2_and_rpfe_n": k8_n,
        "rpfe_only_est": max(0, k5_n - k8_n),
    }

    sym_pnl = defaultdict(float)
    for r in panel:
        if r.pbv2_decision and r.pnl_evaluable and r.cf_pnl_5bps is not None:
            sym_pnl[r.symbol] += float(r.cf_pnl_5bps)
    payload["symbol_dependency"] = [
        {"symbol": s, "pbv2_pnl_5bps": round(v, 2)} for s, v in sorted(sym_pnl.items(), key=lambda x: -abs(x[1]))[:30]
    ]
    payload["missed_rises"] = [{"note": "large-rise capture rates in LARGE_RISE_CAPTURE sheet"}]
    payload["verdict"] = _decide_verdict(payload)

    emit_artifacts(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    print(f"[rpfe] done verdict={payload['verdict'].get('final')} out={out_dir}", flush=True)
    return payload
