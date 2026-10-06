"""Economic failure decomposition for frozen Complete Strategy V1. Runtime 0/0/0."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.am_c0_indicator_exit.isolation import advanced
from research.fixed_daytrade_universe_v1.secrets import assert_no_secret
from research.pb1_v4_complete_strategy_build_and_economic_validation.freeze import complete_strategy_sha256
from research.pb1_v4_complete_strategy_build_and_economic_validation.gate import identity_gate as entry_gate
from research.pb1_v4_complete_strategy_economic_confirmation1 import EXPECTED_COMPLETE_STRATEGY_SHA256
from research.pb1_v4_complete_strategy_economic_failure_decomposition import (
    ANALYSIS_ID,
    CASE_FAIL,
    CASE_READY,
    PROGRAM_ID,
)
from research.pb1_v4_complete_strategy_economic_failure_decomposition.isolation import (
    CACHE,
    OUT,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.pb1_v4_complete_strategy_economic_failure_decomposition.load import (
    baseline_lock,
    bind_and_partition,
    load_parent_report,
    replay_confirmation_paths,
)
from research.pb1_v4_complete_strategy_economic_failure_decomposition.path import attach_paths
from research.pb1_v4_complete_strategy_economic_failure_decomposition.publish import build_answers, build_sheets, write_artifacts
from research.pb1_v4_complete_strategy_economic_failure_decomposition.slices import (
    apply_classes,
    by_key,
    execution_tax_stats,
    group_stats,
    notional_buckets,
    occupied_blocks,
    exit_reason_slice,
)
from research.pb1_v4_complete_strategy_economic_failure_decomposition.spec import source_sha256 as harness_sha256
from research.pb1_v4_complete_strategy_economic_failure_decomposition.verdict import mechanism_verdict
from research.pb1_v4_complete_strategy_economic_failure_decomposition.walks import asf_case, recross_case, session_flat_case
from research.pb1_v4_complete_strategy_economic_failure_decomposition.waterfall import waterfall

JST = ZoneInfo("Asia/Tokyo")


def _safety() -> dict[str, Any]:
    return {
        "V1_VERDICT_CHANGED": False,
        "V4_ENTRY_CHANGED": False,
        "COMPLETE_STRATEGY_CHANGED": False,
        "CONFIRMATION1_RESCORED": False,
        "FROZEN_VALIDATION_ECONOMIC_OPENED": False,
        "PROSPECTIVE_DATA_OPENED": False,
        "submit/cancel/live": "0/0/0",
        "orders_submit": 0,
        "orders_cancel": 0,
        "orders_live": 0,
        "research_only": True,
    }


def _finish(report: dict[str, Any]) -> int:
    report["answers"] = build_answers(report)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    write_artifacts(report, sheets)
    ans = dict(report.get("answers") or {})
    print(f"V1_VERDICT {ans.get('V1_VERDICT')} changed={ans.get('V1_VERDICT_CHANGED')}", flush=True)
    print(f"VERDICT {ans.get('VERDICT')}", flush=True)
    print(f"CONCLUSION {ans.get('CONCLUSION')}", flush=True)
    print(f"NEXT {ans.get('NEXT')}", flush=True)
    print("STOP", flush=True)
    return 0 if str(ans.get("VERDICT") or "") == CASE_READY else 1


def main() -> int:
    set_research_priority_below_normal()
    now = datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S%z")
    CACHE.mkdir(parents=True, exist_ok=True)
    iso_before = snapshot(phase="PRE")
    overlap = write_overlap_n(str(iso_before.get("ACTIVE_CAPTURE") or ""), str(iso_before.get("PAPER_SESSION") or ""))
    gate = entry_gate()
    live_cs = complete_strategy_sha256(
        machine_sha=str(gate.get("machine_sha") or ""),
        source_inventory_sha=str(gate.get("source_inventory_sha") or ""),
    )
    parent = load_parent_report()
    lock = baseline_lock(parent)
    print(f"IDENTITY_OK {gate.get('ok')} cs={live_cs} parent_fail={parent.get('ok')} baseline={lock.get('ok')}", flush=True)
    base_report = {
        "program_id": PROGRAM_ID,
        "analysis_id": ANALYSIS_ID,
        "created_at": now,
        "identity": {**gate, "COMPLETE_STRATEGY_SHA256": live_cs},
        "harness_source_sha256": harness_sha256(),
        "isolation_before": iso_before,
        "safety": _safety(),
        "baseline": lock,
        "paths": [],
        "mechanism": {"VERDICT": CASE_FAIL, "CONCLUSION": None, "NEXT": "STOP"},
    }
    if overlap != 0 or not gate.get("ok") or live_cs != EXPECTED_COMPLETE_STRATEGY_SHA256 or not parent.get("ok") or not lock.get("ok"):
        base_report["mechanism"] = {
            "VERDICT": CASE_FAIL,
            "CONCLUSION": None,
            "NEXT": "STOP",
            "reason": "identity_or_baseline_lock_failed",
        }
        return _finish(base_report)

    bp = bind_and_partition()
    if not bp.get("ok"):
        base_report["mechanism"] = {"VERDICT": CASE_FAIL, "NEXT": "STOP", "reason": "bind_failed"}
        return _finish(base_report)

    replay = replay_confirmation_paths(bind=bp["bind"], part=bp["part"])
    if not replay.get("ok"):
        base_report["mechanism"] = {"VERDICT": CASE_FAIL, "NEXT": "STOP", "reason": replay.get("reason")}
        return _finish(base_report)

    recs = replay["recs"]
    paths = apply_classes(attach_paths(list(replay.get("trades") or []), recs))
    blocked_raw = list(replay.get("blocked_rows") or [])
    blocked_paths = apply_classes(attach_paths(blocked_raw, recs))
    for r in blocked_paths:
        r["COUNTERFACTUAL_NOT_STRATEGY_RESULT"] = True
        r["block_reason"] = next((x.get("block_reason") for x in blocked_raw if x.get("execution_id") == r.get("execution_id")), "")

    setups = dict(replay.get("setups") or {})
    asf_walk = [
        asf_case(r, recs.get((str(r.get("date") or ""), str(r.get("symbol") or ""))), setups.get((str(r.get("symbol") or ""), str(r.get("date") or ""))))
        for r in paths
        if str(r.get("THESIS_LOST_REASON") or "") == "ACCEPTED_STRUCTURAL_FAILURE"
    ]
    recross_walk = [
        recross_case(r, recs.get((str(r.get("date") or ""), str(r.get("symbol") or ""))))
        for r in paths
        if str(r.get("THESIS_LOST_REASON") or "") == "REPEATED_OR_RECROSS"
    ]
    flats = [r for r in paths if r.get("ops_flatten")]
    session_flat = [
        session_flat_case(r, recs.get((str(r.get("date") or ""), str(r.get("symbol") or ""))), occupied_blocks(r, blocked_raw))
        for r in flats
    ]

    g = group_stats(paths)
    slices = {
        "entry_class": by_key(paths, "entry_class"),
        "exit_reasons": exit_reason_slice(paths),
        "long_short": by_key(paths, "side"),
        "e0_e1": by_key(paths, "entry_type"),
        "seed": by_key(paths, "seed_family"),
        "location": by_key(paths, "location_family"),
        "notional": notional_buckets(paths),
        "normalized": {
            "equal_trade_mean_gross_bps": (g.get("gross_bps") or {}).get("mean"),
            "equal_trade_mean_net_bps": (g.get("net_bps") or {}).get("mean"),
            "equal_trade_median_gross_bps": (g.get("gross_bps") or {}).get("median"),
            "yen_net_pnl": g.get("net_pnl_yen"),
            "not_a_v1_rescore": True,
        },
        "execution_tax": execution_tax_stats(paths),
        "all": g,
    }
    wf = waterfall(paths, blocked=blocked_paths)
    mech = mechanism_verdict(
        rows=paths,
        waterfall=wf,
        buckets=list(slices.get("notional") or []),
        blocked=blocked_paths,
        tax=dict(slices.get("execution_tax") or {}),
    )
    iso_after = snapshot(phase="POST")
    report = {
        **base_report,
        "ECONOMIC_DEVELOPMENT_EXPOSED": True,
        "replay_meta": {
            "e0_n": replay.get("e0_n"),
            "e1_n": replay.get("e1_n"),
            "signal_n": replay.get("signal_n"),
            "trade_n": len(paths),
            "cap_blocked_n": replay.get("cap_blocked_n"),
            "same_symbol_blocked_n": replay.get("same_symbol_blocked_n"),
        },
        "paths": paths,
        "asf_walk": asf_walk,
        "recross_walk": recross_walk,
        "session_flat": session_flat,
        "portfolio_blocks": blocked_paths,
        "slices": slices,
        "waterfall": wf,
        "mechanism": mech,
        "isolation_after": iso_after,
        "isolation_advanced": advanced(iso_before, iso_after),
        "out_dir": str(OUT).replace("\\", "/"),
    }
    (CACHE / "rca_meta.json").write_text(
        json.dumps({"CONCLUSION": mech.get("CONCLUSION"), "flags": mech.get("flags"), "trade_n": len(paths)}, indent=2),
        encoding="utf-8",
    )
    return _finish(report)


if __name__ == "__main__":
    raise SystemExit(main())
