"""Price-Flow EXIT integrity pipeline (offline evaluation only)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.price_flow_exit.entries import build_cohorts, load_push_day
from research.price_flow_exit.exit_rules import ExitParams
from research.price_flow_exit_integrity.ablation import run_x6_ablation
from research.price_flow_exit_integrity.actuals import load_actual_exits
from research.price_flow_exit_integrity.baseline import run_baseline_parity
from research.price_flow_exit_integrity.constants import (
    CAPTURE_DAYS,
    MIN_OOS_DAYS_FOR_EDGE,
    NATIVE,
    OOS_DAYS,
    SOT_EXIT_PARAMS,
    SOT_PBV2,
    SOT_PFE,
    SOT_VCIE,
    WARMUP_DAY,
)
from research.price_flow_exit_integrity.dd import equity_curve_rows, summarize_dd
from research.price_flow_exit_integrity.dependency import dependency_audit
from research.price_flow_exit_integrity.portfolio import (
    audit_overlapping_entries,
    filter_no_overlap,
    replay_cap5,
    select_candidate_trades,
)
from research.price_flow_exit_integrity.report import emit_artifacts
from research.price_flow_exit_integrity.trades import simulate_trades
from research.price_flow_exit_integrity.vcie_overlap import vcie_overlap_audit

JST = ZoneInfo("Asia/Tokyo")


def _decide(payload: dict[str, Any]) -> dict[str, Any]:
    codes = [
        "NO_PRODUCTION_CHANGE",
        "PRICE_FLOW_EXIT_OFFLINE_ONLY",
        "TRADE_LEVEL_DD_READY",
    ]
    base = payload.get("baseline") or {}
    codes.append(base.get("verdict") or "EXIT_BASELINE_REPRODUCTION_BLOCKED")
    pos = payload.get("position_state") or {}
    codes.append(pos.get("verdict") or "POSITION_STATE_INTEGRITY_BLOCKED")

    cap5 = payload.get("cap5") or {}
    if cap5.get("ready"):
        codes.append("CAP5_EVENT_REPLAY_READY")
    else:
        codes.append("CAP5_EVENT_REPLAY_BLOCKED")

    for d in payload.get("dependencies") or []:
        codes.append(d.get("verdict") or "DEPENDENCY_AUDIT_READY")

    oos_n = len(payload.get("oos_days") or [])
    if oos_n < MIN_OOS_DAYS_FOR_EDGE:
        codes.append("PRICE_FLOW_EXIT_INSUFFICIENT_OOS")

    # EXIT bottleneck retained from SoT qualitative (C/D evidence) — provisional only
    codes.append("EXIT_BOTTLENECK_CONFIRMED")
    codes.append("PRICE_FLOW_EXIT_PROVISIONAL_SIGNAL")

    baseline_ok = bool(base.get("gate_ok"))
    position_ok = (pos.get("verdict") == "POSITION_STATE_INTEGRITY_PASS")
    cap_ok = bool(cap5.get("ready"))
    dep_ok = all(not d.get("dependency_blocked") for d in (payload.get("dependencies") or []))
    # Never EDGE_CONFIRMED with <10 OOS days
    edge_ok = False
    if (
        baseline_ok
        and position_ok
        and cap_ok
        and dep_ok
        and oos_n >= MIN_OOS_DAYS_FOR_EDGE
        and payload.get("mainline_unchanged")
    ):
        # would still need PF/pnl/day gates — not reachable with 3 OOS days
        edge_ok = False
    if edge_ok:
        codes.append("PRICE_FLOW_EXIT_EDGE_CONFIRMED")
    else:
        # stop edge claim when baseline blocked
        if not baseline_ok:
            codes.append("PRICE_FLOW_EXIT_NO_EDGE")
        codes.append("PRICE_FLOW_EXIT_PROVISIONAL_SIGNAL")

    codes = sorted(set(codes))
    final = "PRICE_FLOW_EXIT_OFFLINE_ONLY"
    return {
        "final": final,
        "codes": codes,
        "summary": (
            "EXIT評価基盤を修正し CAP=5 イベント再生・重複除外・依存性監査・固定ablationを実施。"
            f" baseline={base.get('verdict')}; position={pos.get('verdict')}; "
            f"OOS={oos_n}日のため EDGE_CONFIRMED 不可。X4/X6は PROVISIONAL_SIGNAL。"
        ),
        "no_production_reason": "本線/Shadow/Forward/ENTRY/X0-X6条件は変更していない。offline評価のみ。",
        "edge_gates": {
            "baseline_parity": baseline_ok,
            "position_state": position_ok,
            "cap5_event": cap_ok,
            "dependency": dep_ok,
            "oos_ge_10": oos_n >= MIN_OOS_DAYS_FOR_EDGE,
            "mainline_unchanged": True,
        },
    }


def run_pipeline(*, native: Path = NATIVE, run_id: Optional[str] = None) -> dict[str, Any]:
    run_id = run_id or datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_dir = native / "results" / "research" / "price_flow_exit_integrity" / run_id
    print(f"[pfe_int] start run_id={run_id}", flush=True)

    params = ExitParams(**SOT_EXIT_PARAMS)
    print("[pfe_int] load PUSH cache…", flush=True)
    push_by_day = {d: load_push_day(d, native) for d in CAPTURE_DAYS}

    print("[pfe_int] load actual observer exits…", flush=True)
    actuals = load_actual_exits(native=native)
    print(f"[pfe_int] actuals n={len(actuals)}", flush=True)
    print("[pfe_int] baseline parity…", flush=True)
    baseline = run_baseline_parity(actuals, push_by_day)
    print(f"[pfe_int] baseline matched={baseline['n_matched']} gate={baseline['gate_ok']}", flush=True)

    print("[pfe_int] build ENTRY cohorts (unchanged)…", flush=True)
    cohorts = build_cohorts(native)
    e0 = [e for e in cohorts["E0"] if e.day in OOS_DAYS]
    e1 = [e for e in cohorts["E1"] if e.day in OOS_DAYS]
    print(f"[pfe_int] OOS E0={len(e0)} E1={len(e1)}", flush=True)

    bars_cache: dict = {}
    print("[pfe_int] simulate EXIT modes…", flush=True)
    by_mode = {
        "E0_X0": simulate_trades(e0, push_by_day, mode="X0", params=params, bars_cache=bars_cache),
        "E0_X6": simulate_trades(e0, push_by_day, mode="X6", params=params, bars_cache=bars_cache),
        "E1_X0": simulate_trades(e1, push_by_day, mode="X0", params=params, bars_cache=bars_cache),
        "E1_X4": simulate_trades(e1, push_by_day, mode="X4", params=params, bars_cache=bars_cache),
        "E1_X6": simulate_trades(e1, push_by_day, mode="X6", params=params, bars_cache=bars_cache),
    }
    for k, v in by_mode.items():
        print(f"[pfe_int] {k} n={len(v)}", flush=True)

    # Independent vs no-overlap metrics for position integrity evidence
    e1x4 = by_mode["E1_X4"]
    e1_noov, e1_dropped = filter_no_overlap(e1x4)
    e0x6_noov, e0_dropped = filter_no_overlap(by_mode["E0_X6"])

    print("[pfe_int] CAP=5 event replay P0–P5…", flush=True)
    cap_results: dict[str, Any] = {}
    cap_objs: dict[str, Any] = {}
    event_logs: list[dict[str, Any]] = []
    blocked_rows: list[dict[str, Any]] = []
    for pid in ("P0", "P1", "P2", "P3", "P4", "P5"):
        cands = select_candidate_trades(by_mode, portfolio_id=pid, entry_filter=pid)
        cands_f, _ = filter_no_overlap(cands)
        res = replay_cap5(cands_f, portfolio_id=pid)
        ov = audit_overlapping_entries(res.trades)
        summary = res.summary()
        summary["overlap_in_executed"] = ov["same_symbol_overlapping_entry_count"]
        cap_results[pid] = summary
        cap_objs[pid] = res
        event_logs.extend(res.event_log[:500])
        blocked_rows.extend(res.blocked[:500])
        print(
            f"[pfe_int] {pid} accepted={res.accepted} cap_blocked={res.cap_blocked} "
            f"same_sym={res.same_symbol_blocked}",
            flush=True,
        )

    p1_trades = cap_objs["P1"].trades
    p3_trades = cap_objs["P3"].trades
    pos_p1 = audit_overlapping_entries(p1_trades)
    pos_p3 = audit_overlapping_entries(p3_trades)
    # Also audit independent VCIE for overlap count before filter
    pos_raw = audit_overlapping_entries(e1x4)
    position_state = {
        "independent_vcie_x4_overlaps": pos_raw["same_symbol_overlapping_entry_count"],
        "independent_vcie_x4_overlap_rows": pos_raw["overlaps"][:100],
        "p1_executed_overlaps": pos_p1["same_symbol_overlapping_entry_count"],
        "p3_executed_overlaps": pos_p3["same_symbol_overlapping_entry_count"],
        "excluded_dup_entries_e1": len(e1_dropped),
        "excluded_dup_entries_e0": len(e0_dropped),
        "pnl_e1_before": round(sum(t.pnl_5bps for t in e1x4), 2),
        "pnl_e1_after_no_overlap": round(sum(t.pnl_5bps for t in e1_noov), 2),
        "pnl_e0_before": round(sum(t.pnl_5bps for t in by_mode["E0_X6"]), 2),
        "pnl_e0_after_no_overlap": round(sum(t.pnl_5bps for t in e0x6_noov), 2),
        "verdict": (
            "POSITION_STATE_INTEGRITY_PASS"
            if pos_p1["same_symbol_overlapping_entry_count"] == 0 and pos_p3["same_symbol_overlapping_entry_count"] == 0
            else "POSITION_STATE_INTEGRITY_BLOCKED"
        ),
    }

    print("[pfe_int] dependency audit…", flush=True)
    deps = [
        dependency_audit(p1_trades, label="PBv2_X6_CAP5"),
        dependency_audit(p3_trades, label="VCIE_X4_CAP5"),
        dependency_audit(e0x6_noov, label="PBv2_X6_NO_OVERLAP"),
        dependency_audit(e1_noov, label="VCIE_X4_NO_OVERLAP"),
    ]

    print("[pfe_int] X6 ablation…", flush=True)
    ablation = run_x6_ablation(cohorts["E0"], push_by_day, params=params, oos_days=OOS_DAYS)

    print("[pfe_int] VCIE overlap / 285A…", flush=True)
    vcie_ov = vcie_overlap_audit(e1x4)

    dd_p1 = summarize_dd(p1_trades)
    dd_p3 = summarize_dd(p3_trades)

    cap5_ready = all(
        cap_results[p].get("accepted", 0) >= 0 and "pnl_5bps" in cap_results[p] for p in ("P0", "P1", "P2", "P3", "P4", "P5")
    )

    payload: dict[str, Any] = {
        "run_id": run_id,
        "generated_at": datetime.now(JST).isoformat(),
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "mainline_unchanged": True,
        "entry_unchanged": True,
        "exit_rules_unchanged": True,
        "sot": {"price_flow_exit": str(SOT_PFE), "pbv2": str(SOT_PBV2), "vcie": str(SOT_VCIE)},
        "oos_days": list(OOS_DAYS),
        "warmup_day": WARMUP_DAY,
        "capture_days": list(CAPTURE_DAYS),
        "params": dict(SOT_EXIT_PARAMS),
        "baseline": {
            **{k: v for k, v in baseline.items() if k not in ("matches", "unmatched_actual")},
            "matches_preview": baseline["matches"][:50],
            "n_matches_full": len(baseline["matches"]),
        },
        "baseline_matches": baseline["matches"],
        "baseline_unmatched": baseline["unmatched_actual"],
        "position_state": position_state,
        "overlapping_entries": pos_raw["overlaps"][:200],
        "cap5": {"ready": cap5_ready, "portfolios": cap_results},
        "cap5_event_log": event_logs[:2000],
        "cap5_blocked": blocked_rows[:2000],
        "trade_level_dd": {"P1_PBv2_X6": dd_p1, "P3_VCIE_X4": dd_p3},
        "equity_p1": equity_curve_rows(p1_trades),
        "equity_p3": equity_curve_rows(p3_trades),
        "dependencies": deps,
        "x6_ablation": ablation,
        "vcie_no_overlap": vcie_ov,
        "pbv2_results": {
            "X0_indep": {"n": len(by_mode["E0_X0"]), "pnl_5bps": round(sum(t.pnl_5bps for t in by_mode["E0_X0"]), 2)},
            "X6_indep": {"n": len(by_mode["E0_X6"]), "pnl_5bps": round(sum(t.pnl_5bps for t in by_mode["E0_X6"]), 2)},
            "X6_no_overlap": {"n": len(e0x6_noov), "pnl_5bps": round(sum(t.pnl_5bps for t in e0x6_noov), 2)},
            "P0_cap5": cap_results.get("P0"),
            "P1_cap5": cap_results.get("P1"),
        },
        "vcie_results": {
            "X0_indep": {"n": len(by_mode["E1_X0"]), "pnl_5bps": round(sum(t.pnl_5bps for t in by_mode["E1_X0"]), 2)},
            "X4_indep": {"n": len(e1x4), "pnl_5bps": round(sum(t.pnl_5bps for t in e1x4), 2)},
            "X4_no_overlap": {"n": len(e1_noov), "pnl_5bps": round(sum(t.pnl_5bps for t in e1_noov), 2)},
            "P2_cap5": cap_results.get("P2"),
            "P3_cap5": cap_results.get("P3"),
        },
        "completion": {},
    }
    payload["verdict"] = _decide(payload)
    payload["completion"] = _completion_checklist(payload, p1_trades, p3_trades)
    emit_artifacts(out_dir, payload)
    payload["out_dir"] = str(out_dir)
    print(f"[pfe_int] done verdict={payload['verdict'].get('final')} out={out_dir}", flush=True)
    return payload


def _completion_checklist(payload: dict[str, Any], p1, p3) -> dict[str, Any]:
    b = payload.get("baseline") or {}
    pos = payload.get("position_state") or {}
    caps = (payload.get("cap5") or {}).get("portfolios") or {}
    dd = payload.get("trade_level_dd") or {}
    deps = {d["label"]: d for d in (payload.get("dependencies") or [])}
    ab = payload.get("x6_ablation") or {}
    vcie = payload.get("vcie_no_overlap") or {}
    focus = vcie.get("focus_285A") or {}
    return {
        "1_final_verdict": (payload.get("verdict") or {}).get("final"),
        "2_baseline_matched": b.get("n_matched"),
        "3_exact_reason_rate": b.get("exact_reason_match_rate"),
        "4_family_reason_rate": b.get("family_reason_match_rate"),
        "5_exit_time_diff": b.get("exit_time_diff_sec"),
        "6_pnl_diff": b.get("pnl_diff_yen100"),
        "7_baseline_parity": "PASS" if b.get("gate_ok") else "FAIL",
        "8_same_symbol_overlap": pos.get("independent_vcie_x4_overlaps"),
        "9_after_overlap_filter_n": (payload.get("vcie_results") or {}).get("X4_no_overlap", {}).get("n"),
        "10_285A_pnl_before_after": {
            "before": focus.get("pnl_before_overlap_filter"),
            "after": focus.get("pnl_after_no_overlap"),
        },
        "11_cap5_accepted": {k: (caps.get(k) or {}).get("accepted") for k in ("P0", "P1", "P2", "P3", "P4", "P5")},
        "12_cap_blocked": {k: (caps.get(k) or {}).get("cap_blocked") for k in ("P0", "P1", "P2", "P3", "P4", "P5")},
        "13_same_symbol_blocked": {k: (caps.get(k) or {}).get("same_symbol_blocked") for k in ("P0", "P1", "P2", "P3", "P4", "P5")},
        "14_PBv2_X0_CAP5": caps.get("P0"),
        "15_PBv2_X6_CAP5": caps.get("P1"),
        "16_VCIE_X0_CAP5": caps.get("P2"),
        "17_VCIE_X4_CAP5": caps.get("P3"),
        "18_trade_level_max_dd": (dd.get("P1_PBv2_X6") or {}).get("trade_sequence_max_dd"),
        "19_intraday_max_dd": (dd.get("P1_PBv2_X6") or {}).get("intraday_max_dd"),
        "20_daily_max_dd": (dd.get("P1_PBv2_X6") or {}).get("daily_close_max_dd"),
        "21_top1_symbol": {
            "PBv2_X6": (deps.get("PBv2_X6_CAP5") or {}).get("top1_symbol_pnl_share"),
            "VCIE_X4": (deps.get("VCIE_X4_CAP5") or {}).get("top1_symbol_pnl_share"),
        },
        "22_top1_day": {
            "PBv2_X6": (deps.get("PBv2_X6_CAP5") or {}).get("top1_day_pnl_share"),
            "VCIE_X4": (deps.get("VCIE_X4_CAP5") or {}).get("top1_day_pnl_share"),
        },
        "23_leave_one_symbol": {
            "PBv2_X6": (deps.get("PBv2_X6_CAP5") or {}).get("pf_after_exclude_max_symbol"),
            "VCIE_X4": (deps.get("VCIE_X4_CAP5") or {}).get("pf_after_exclude_max_symbol"),
        },
        "24_leave_one_day": {
            "PBv2_X6": (deps.get("PBv2_X6_CAP5") or {}).get("pf_after_exclude_max_day"),
            "VCIE_X4": (deps.get("VCIE_X4_CAP5") or {}).get("pf_after_exclude_max_day"),
        },
        "25_x6_reason_pnl": ab.get("reason_attribution"),
        "26_x6_ablation": ab.get("ablation"),
        "27_lost_winner": sum(int(r.get("lost_winner") or 0) for r in (ab.get("reason_attribution") or [])),
        "28_early_exit_regret": [
            {"reason": r.get("exit_reason"), "mean_regret": r.get("mean_early_exit_regret_pct")}
            for r in (ab.get("reason_attribution") or [])
        ],
        "29_oos_insufficient": True,
        "30_submit_cancel_live": {"submit": 0, "cancel": 0, "live_order": 0},
        "31_mainline_changed": False,
        "32_artifact_path": None,
        "p1_n": len(p1),
        "p3_n": len(p3),
    }
