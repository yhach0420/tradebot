"""E1_X34C runner — passive fill ENTRY deployability (research/paper only)."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x28_executable_joint.board import verify_board_mapping
from research.e1_x31_population_direction.identity import ab_identity, reproduce_population
from research.e1_x32_upstream_attribution.eval_stages import load_boards_for_symbols
from research.e1_x33b_neutral_anchor.neutral import (
    candidate_symbols_by_day,
    planned_neutral_anchors,
)

from . import (
    ANALYSIS_ID,
    ANCHOR_ID,
    ANCHOR_SHA,
    BOARD_MAPPING_SHA,
    DOCUMENT_ID,
    EXEC_POLICY_SHA,
    EXPECTED_SIGNALS,
    EXPECTED_X34A_FILLS,
    EXPECTED_X34A_OPP600,
    FORBIDDEN_FROM,
    NEXT_FAIL,
    NEXT_PASS,
    POSITION_CAP,
    SOURCE_X34A_RUN,
    SOURCE_X34B_RUN,
    WAIT_SEC,
)
from .capacity import fill_burst, order_fanout, pending_risk_audit, simulate_capacity
from .events import build_events
from .metrics import dist_stats, lodo, loso, signal_vs_fill_delta, summarize_mode
from .publish import publish
from .verdict import decide_verdict, freeze_manifest

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x34c_passive_deployability"
X33B = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"
X34A = NATIVE / "results" / "research" / "e1_x34a_execution_policy"
X34B = NATIVE / "results" / "research" / "e1_x34b_entry_execution"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x34c_passive_deployability.py"
    env = {**os.environ, "PYTHONPATH": str(NATIVE / "src")}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(tp), "-q", "--tb=line"],
        cwd=str(NATIVE), capture_output=True, text=True, env=env,
    )
    out = (p.stdout or "") + (p.stderr or "")
    passed = failed = 0
    m = re.search(r"(\d+) passed", out)
    if m:
        passed = int(m.group(1))
    m2 = re.search(r"(\d+) failed", out)
    if m2:
        failed = int(m2.group(1))
    return {"passed": passed, "failed": failed, "returncode": p.returncode, "tail": out[-2000:]}


def _verify_shas() -> dict[str, Any]:
    anchor = json.loads((X33B / "NEUTRAL_FIXED_CLOCK_ANCHOR_V1.json").read_text(encoding="utf-8"))
    pol = json.loads((X34A / "ENTRY_EXECUTION_POLICY_V1.json").read_text(encoding="utf-8"))
    x34b = json.loads((X34B / "report.json").read_text(encoding="utf-8"))
    a_raw = {k: v for k, v in anchor.items() if k != "sha256"}
    p_raw = {k: v for k, v in pol.items() if k != "sha256"}
    return {
        "anchor_sha": anchor.get("sha256"),
        "anchor_ok": anchor.get("sha256") == ANCHOR_SHA and hashlib.sha256(
            json.dumps(a_raw, sort_keys=True, default=str).encode()
        ).hexdigest() == ANCHOR_SHA,
        "exec_sha": pol.get("sha256"),
        "exec_ok": pol.get("sha256") == EXEC_POLICY_SHA and hashlib.sha256(
            json.dumps(p_raw, sort_keys=True, default=str).encode()
        ).hexdigest() == EXEC_POLICY_SHA,
        "x34b_run": x34b.get("run_id"),
        "x34b_match": x34b.get("run_id") == SOURCE_X34B_RUN,
        "wait_sec": pol.get("wait_window_sec"),
    }


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x34c_deploy_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    mapping = verify_board_mapping()
    assert mapping.get("ok") and mapping.get("mapping_sha") == BOARD_MAPPING_SHA
    shas = _verify_shas()
    assert shas["anchor_ok"] and shas["exec_ok"] and shas["x34b_match"], shas
    assert float(shas["wait_sec"]) == WAIT_SEC
    print("  SHA / X34B bind OK", flush=True)

    print("=== population ===", flush=True)
    rows_pop, labels, identity = reproduce_population()
    ab_pop = ab_identity(rows_pop, labels, identity)
    assert ab_pop["ok"], ab_pop
    assert not any(str(r.get("date") or "") >= FORBIDDEN_FROM for r in rows_pop)
    pool = candidate_symbols_by_day(rows_pop)
    planned = planned_neutral_anchors(pool)
    pairs = sorted({(a["date"], a["symbol"]) for a in planned})
    boards = load_boards_for_symbols(pairs)

    print("=== build passive fill events ===", flush=True)
    events = build_events(planned, boards)
    fills = [e for e in events if e.get("filled")]
    print(f"  signals={len(events)} fills={len(fills)}", flush=True)
    assert len(events) == EXPECTED_SIGNALS
    assert len(fills) == EXPECTED_X34A_FILLS

    # Unlimited signal-based must reproduce X34A
    unlimited_sig = summarize_mode(events, mode="unlimited", ret_key_prefix="signal_based_ret")
    x34a_match = abs(float(unlimited_sig["opp_w_ret600"]) - EXPECTED_X34A_OPP600) < 1e-6
    print(f"  unlimited signal opp600={unlimited_sig['opp_w_ret600']} match_x34a={x34a_match}", flush=True)
    assert x34a_match

    unlimited_fill = summarize_mode(events, mode="unlimited", ret_key_prefix="fill_based_ret")
    delta_audit = signal_vs_fill_delta(fills)
    delay_stats = dist_stats([float(f["fill_delay_ms"]) for f in fills])

    fanout = order_fanout(events)
    burst = fill_burst(fills)
    pending = pending_risk_audit(events)

    print("=== capacity simulation ===", flush=True)
    cap = simulate_capacity(events)
    dep_events = cap["events"]
    deployable = summarize_mode(dep_events, mode="deployable", ret_key_prefix="fill_based_ret")
    # also signal-based deployable for reference
    deployable_sig = summarize_mode(dep_events, mode="deployable", ret_key_prefix="signal_based_ret")

    capacity_drag = None
    if unlimited_fill.get("opp_w_ret600") is not None and deployable.get("opp_w_ret600") is not None:
        capacity_drag = float(unlimited_fill["opp_w_ret600"] - deployable["opp_w_ret600"])

    print(
        f"  accepted={cap['accepted_fills']} cap_block={cap['capacity_blocked']} "
        f"dup={cap['duplicate_blocked']} dep_opp600={deployable.get('opp_w_ret600')} "
        f"drag={capacity_drag}",
        flush=True,
    )

    lodo_u = lodo(events, mode="unlimited")
    lodo_d = lodo(dep_events, mode="deployable")
    loso_d = loso(dep_events, mode="deployable")

    # day-level fill robustness
    day_fill = []
    by_day: dict[str, list] = {}
    for e in dep_events:
        by_day.setdefault(e["date"], []).append(e)
    for day in sorted(by_day):
        g = by_day[day]
        sm = summarize_mode(g, mode="deployable", ret_key_prefix="fill_based_ret")
        day_fill.append({
            "date": day,
            "fills_accepted": sm["accepted_fills"],
            "opp600": sm["opp_w_ret600"],
            "pf": sm["pf_equiv_600"],
        })

    decision = decide_verdict(
        unlimited=unlimited_fill,
        deployable=deployable,
        delta_audit=delta_audit,
        capacity_sim=cap,
        x34a_match=x34a_match,
    )
    print(f"  verdict={decision['verdict']}", flush=True)

    manifest = None
    if decision.get("freeze"):
        manifest = freeze_manifest(decision=decision, capacity_sim=cap)
        (OUT / "PASSIVE_FILL_ENTRY_V1.json").write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8",
        )

    # A/B: deterministic re-summarize
    unlimited_b = summarize_mode(events, mode="unlimited", ret_key_prefix="signal_based_ret")
    ab_ok = abs(float(unlimited_b["opp_w_ret600"]) - float(unlimited_sig["opp_w_ret600"])) < 1e-15
    opp_a = unlimited_sig["opp_w_ret600"]
    _ = opp_a

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": decision["verdict"],
        "verdict_detail": decision,
        "anchor_id": ANCHOR_ID,
        "anchor_sha": ANCHOR_SHA,
        "execution_sha": EXEC_POLICY_SHA,
        "source_x34a_run": SOURCE_X34A_RUN,
        "source_x34b_run": SOURCE_X34B_RUN,
        "sha_verify": shas,
        "position_cap": POSITION_CAP,
        "wait_sec": WAIT_SEC,
        "signal_count": len(events),
        "raw_fills": len(fills),
        "fill_delay_ms": delay_stats,
        "entry_timestamp_is_fill_time": True,
        "signal_vs_fill": delta_audit,
        "order_fanout": fanout,
        "fill_burst": burst,
        "pending_risk": pending,
        "capacity_simulation": {
            k: v for k, v in cap.items() if k != "events"
        },
        "unlimited": {
            "signal_based": {k: v for k, v in unlimited_sig.items() if k != "day_means"},
            "fill_based": {k: v for k, v in unlimited_fill.items() if k != "day_means"},
        },
        "deployable": {
            "fill_based": {k: v for k, v in deployable.items() if k != "day_means"},
            "signal_based": {k: v for k, v in deployable_sig.items() if k != "day_means"},
        },
        "capacity_drag_bps": capacity_drag,
        "capacity_accepted_fills": cap["accepted_fills"],
        "capacity_blocked_fills": cap["capacity_blocked"],
        "duplicate_blocks": cap["duplicate_blocked"],
        "blocked_share": cap["blocked_share"],
        "day_level_deployable": day_fill,
        "lodo_unlimited": {k: v for k, v in lodo_u.items() if k != "folds"},
        "lodo_deployable": {k: v for k, v in lodo_d.items() if k != "folds"},
        "lodo_deployable_folds": lodo_d.get("folds"),
        "loso_deployable": loso_d,
        "manifest_created": bool(manifest),
        "manifest_sha": (manifest or {}).get("sha256"),
        "recommended_next": NEXT_PASS if decision.get("freeze") else NEXT_FAIL,
        "opened_20260810": False,
        "prospective_observer_started": False,
        "prospective_evidence_consumed": False,
        "no_entry_performance_search": True,
        "no_fill_prediction": True,
        "no_exit_design": True,
        "no_runtime_change": True,
        "no_queue_assumption": True,
        "capacity_no_future_ranking": True,
        "safety": {
            "research_paper_only": True,
            "submit_cancel_live": "0/0/0",
            "discord_production": False,
        },
        "ab_determinism": {"ok": ab_ok, "population": ab_pop},
        "x34a_identity": {
            "opp600_match": x34a_match,
            "fills_match": len(fills) == EXPECTED_X34A_FILLS,
            "signals_match": len(events) == EXPECTED_SIGNALS,
        },
    }

    interim = {
        "run_id": run_id,
        "verdict": decision["verdict"],
        "anchor_sha": ANCHOR_SHA,
        "execution_sha": EXEC_POLICY_SHA,
        "signal_count": len(events),
        "raw_fills": len(fills),
        "entry_timestamp_is_fill_time": True,
        "x34a_identity": report["x34a_identity"],
        "x34b_run": SOURCE_X34B_RUN,
        "unlimited_opp600_signal": unlimited_sig["opp_w_ret600"],
        "deployable_opp600": deployable["opp_w_ret600"],
        "capacity_drag_bps": capacity_drag,
        "capacity_accepted_fills": cap["accepted_fills"],
        "capacity_blocked_fills": cap["capacity_blocked"],
        "duplicate_blocks": cap["duplicate_blocked"],
        "order_fanout": fanout,
        "fill_burst": burst,
        "fill_delay_ms": delay_stats,
        "signal_vs_fill": delta_audit,
        "position_cap": POSITION_CAP,
        "lodo_deployable": report["lodo_deployable"],
        "loso_deployable": {k: v for k, v in loso_d.items() if k != "sample"},
        "manifest_created": bool(manifest),
        "manifest_sha": (manifest or {}).get("sha256"),
        "opened_20260810": False,
        "no_entry_performance_search": True,
        "no_exit_design": True,
        "no_runtime_change": True,
        "capacity_no_future_ranking": True,
        "submit_cancel_live": "0/0/0",
        "ab_determinism": report["ab_determinism"],
        "pending_expiry_sec": WAIT_SEC,
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{
            "run_id": run_id,
            "verdict": decision["verdict"],
            "unlimited_opp600": unlimited_fill.get("opp_w_ret600"),
            "deployable_opp600": deployable.get("opp_w_ret600"),
            "drag": capacity_drag,
            "accepted": cap["accepted_fills"],
            "blocked": cap["capacity_blocked"],
            "dup": cap["duplicate_blocked"],
        }],
        "day_deployable": day_fill,
        "fanout": [fanout.get("orders_per_timestamp") or {}],
        "burst": [
            {"window": "500ms", **(burst.get("window_500ms") or {})},
            {"window": "1s", **(burst.get("window_1s") or {})},
        ],
        "lodo": lodo_d.get("folds") or [],
    }
    publish(OUT, report, sheets)

    print("=== tests ===", flush=True)
    # skip second full rebuild in tests path — ab already done
    tests = _run_tests()
    report["tests"] = tests
    publish(OUT, report, sheets)

    print(f"=== DONE {decision['verdict']} ===", flush=True)
    print(json.dumps({
        "run_id": run_id,
        "verdict": decision["verdict"],
        "unlimited": unlimited_fill.get("opp_w_ret600"),
        "deployable": deployable.get("opp_w_ret600"),
        "drag": capacity_drag,
        "accepted": cap["accepted_fills"],
        "blocked": cap["capacity_blocked"],
        "manifest": bool(manifest),
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
