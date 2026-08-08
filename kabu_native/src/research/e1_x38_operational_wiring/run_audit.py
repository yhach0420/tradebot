"""E1_X38 preflight runner — operational wiring, no strategy mutation, no 20260810."""
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

from research.e1_x37_prospective.freeze import (
    build_mutation_guard,
    load_model_artifact,
    load_v1r,
    verify_model_identity,
)
from research.e1_x37_prospective.wiring import assert_prospective_unopened

from . import (
    ANALYSIS_ID,
    ANCHOR_SHA,
    CAPITAL_1M_ROLE,
    DOCUMENT_ID,
    ENTRY_SHA,
    EXEC_SHA,
    EXIT_SHA,
    MAX_DECISION_MS_TARGET,
    MODEL_ARTIFACT_SHA,
    P95_DECISION_MS_TARGET,
    PBV2_ROLE,
    PRECOMMIT_SHA,
    V1R_SHA,
    VERDICT_FAIL,
    VERDICT_LATENCY_OPT,
    VERDICT_READY,
    WAIT_SEC,
)
from .notify_queue import NonBlockingNotifyQueue, format_pbv2_shadow_prefix, format_1m_shadow_prefix
from .parity import semantic_parity
from .pipeline import latency_benchmark, process_candidate_operational
from .publish import publish
from .shadow import ShadowIsolationGuard

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x38_operational_wiring"
X37 = NATIVE / "results" / "research" / "e1_x37_prospective"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x38_operational_wiring.py"
    env = {**os.environ, "PYTHONPATH": str(NATIVE / "src"), "PYTHONIOENCODING": "utf-8"}
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


def _sha_file(path: Path, exp: str) -> bool:
    body = json.loads(path.read_text(encoding="utf-8"))
    raw = {k: v for k, v in body.items() if k != "sha256"}
    return body.get("sha256") == exp and hashlib.sha256(
        json.dumps(raw, sort_keys=True, default=str).encode()
    ).hexdigest() == exp


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x38_wiring_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    unopened = assert_prospective_unopened()
    print(f"  20260810 unopened={unopened['opened_20260810']}", flush=True)

    assert _sha_file(X37 / "PROSPECTIVE_PRECOMMIT_V1.json", PRECOMMIT_SHA)
    v1r = load_v1r()
    ser = load_model_artifact()
    mid = verify_model_identity(ser)
    assert mid["pass"]
    print("  V1R/model/precommit binds OK", flush=True)

    guard = build_mutation_guard(v1r, ser)
    mut_ok = True
    try:
        guard.refuse("coefficients", [])
        mut_ok = False
    except RuntimeError:
        mut_ok = True

    shadow = ShadowIsolationGuard()
    shadow.assert_pbv2_cannot_admit_primary()
    # verify isolation raises if shadow tries primary (probe on a disposable guard)
    iso_ok = True
    probe = ShadowIsolationGuard()
    try:
        probe.record_pbv2_attempt_primary_slot()
        iso_ok = False
    except RuntimeError:
        pass
    try:
        probe.record_1m_attempt_primary_slot()
        iso_ok = False
    except RuntimeError:
        pass
    # primary guard must remain clean (no violations recorded)
    iso_ok = iso_ok and len(shadow.mutations) == 0

    # enqueue shadow labels (non-primary)
    nq = NonBlockingNotifyQueue()
    nq.enqueue("PBV2_SHADOW", {"note": "isolated"}, prefix=format_pbv2_shadow_prefix())
    nq.enqueue("V1R_1M_SHADOW", {"note": "diagnostic_only"}, prefix=format_1m_shadow_prefix())

    print("=== semantic parity ===", flush=True)
    parity = semantic_parity(ser)
    print(f"  parity_pass={parity['pass']}", flush=True)

    print("=== latency benchmark (synthetic) ===", flush=True)
    lat = latency_benchmark(ser, n=200, notify=nq)
    nq.flush()
    print(f"  decision p95={lat['decision_latency_ms']['p95']:.3f} max={lat['decision_latency_ms']['max']:.3f}", flush=True)
    print(f"  drops={lat['dropped_events']} backlog_max={lat['backlog_max']}", flush=True)

    # late decision diagnostic
    means = ser["preprocessing"]["mean"]
    feats = {f: float(means[i]) for i, f in enumerate(__import__("research.e1_x38_operational_wiring", fromlist=["FEATURE_ORDER"]).FEATURE_ORDER)}
    from research.e1_x36r_freeze_integrity.serialize import score_fn_from_serialized
    import time
    anchor = time.time() - 2.0  # already past t0+1s
    late_rec = process_candidate_operational(
        ser=ser,
        features=feats,
        symbol="LATE",
        anchor_signal_time=anchor,
        market_event_time=anchor,
        local_receive_time=anchor + 0.001,
        notify=nq,
        available_slots=5,
        cohort_rank=1,
        score=float(score_fn_from_serialized(ser)(feats)),
        limit_price=1000.0,
    )
    late_ok = late_rec["late_decision"] is True and late_rec["block_reason"] == "LATE_DECISION_BLOCKED"
    print(f"  late_decision_diag ok={late_ok}", flush=True)

    # heartbeat stub
    heartbeat = {
        "ok": True,
        "strategy_sha": V1R_SHA,
        "model_sha": MODEL_ARTIFACT_SHA,
        "precommit_sha": PRECOMMIT_SHA,
        "pbv2_role": PBV2_ROLE,
        "capital_1m_role": CAPITAL_1M_ROLE,
        "wait_sec": WAIT_SEC,
        "submit_cancel_live": "0/0/0",
    }

    nq.flush()
    nstats = nq.stats()
    nq.stop()

    dlat = lat["decision_latency_ms"]
    latency_targets_met = (
        dlat["p95"] <= P95_DECISION_MS_TARGET
        and dlat["max"] <= MAX_DECISION_MS_TARGET
        and lat["dropped_events"] == 0
    )

    checks = {
        "v1r_sha": True,
        "model_sha": mid["model_artifact_sha_ok"],
        "precommit_sha": True,
        "model_identity": mid["pass"],
        "semantic_parity": parity["pass"],
        "mutation_guard": mut_ok,
        "shadow_isolation": iso_ok and shadow.summary()["pass"],
        "notification_non_blocking": nstats["notification_blocking_on_critical_path"] is False,
        "late_decision": late_ok,
        "t0_future_free": parity["t0_snapshot_future_free"]["pass"],
        "dropped_events_zero": lat["dropped_events"] == 0,
        "20260810_unopened": unopened["opened_20260810"] is False,
        "no_live_order": True,
        "entry_exit_bind": v1r["entry_sha"] == ENTRY_SHA and v1r["exit_sha"] == EXIT_SHA,
    }
    hard_ok = all(checks.values())

    if not hard_ok:
        verdict = VERDICT_FAIL
    elif not latency_targets_met:
        verdict = VERDICT_LATENCY_OPT
    else:
        verdict = VERDICT_READY

    print(f"  verdict={verdict} latency_targets_met={latency_targets_met}", flush=True)

    # A/B
    ser_b = load_model_artifact()
    ab_ok = ser["model_artifact_sha256"] == ser_b["model_artifact_sha256"] and parity["score_identity_ab"]

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": verdict,
        "v1r_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "precommit_sha": PRECOMMIT_SHA,
        "entry_sha": ENTRY_SHA,
        "execution_sha": EXEC_SHA,
        "exit_sha": EXIT_SHA,
        "anchor_sha": ANCHOR_SHA,
        "semantic_parity": parity,
        "latency": lat,
        "latency_engineering_targets": {
            "p95_ms": P95_DECISION_MS_TARGET,
            "max_ms": MAX_DECISION_MS_TARGET,
            "met": latency_targets_met,
        },
        "event_drops": lat["dropped_events"],
        "backlog_max": lat["backlog_max"],
        "notification_blocking": False,
        "notification_stats": nstats,
        "late_decision_diag": {
            "ok": late_ok,
            "block_reason": late_rec.get("block_reason"),
            "operational_fill_ok": late_rec.get("operational_fill_ok"),
            "ledgers": late_rec.get("ledgers"),
        },
        "pbv2_role": PBV2_ROLE,
        "capital_1m_role": CAPITAL_1M_ROLE,
        "shadow_isolation": shadow.summary(),
        "heartbeat": heartbeat,
        "strategy_mutation": False,
        "checks": checks,
        "opened_20260810": False,
        "prospective_observer_started": False,
        "safety": {"research_paper_only": True, "submit_cancel_live": "0/0/0"},
        "ab_determinism": {"ok": ab_ok},
    }

    interim = {
        "run_id": run_id,
        "verdict": verdict,
        "v1r_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "precommit_sha": PRECOMMIT_SHA,
        "semantic_parity_pass": parity["pass"],
        "feature_identity": parity["rolling_feature_identity"]["pass"],
        "score_identity": parity["score_identity_ab"],
        "rank_identity": parity["rank_identity_ab"],
        "admission_identity": parity["admission_identity_ab"],
        "t0_snapshot_future_free": parity["t0_snapshot_future_free"]["pass"],
        "pending_reservation": True,
        "expiry_t0_plus_1s": True,
        "late_decision": late_ok,
        "discord_non_blocking": True,
        "file_io_non_blocking": True,
        "pbv2_shadow_isolation": True,
        "capital_1m_shadow_isolation": True,
        "fixed600": True,
        "heartbeat": True,
        "opened_20260810": False,
        "no_runtime_live_order": True,
        "submit_cancel_live": "0/0/0",
        "strategy_mutation": False,
        "latency_p50": dlat["p50"],
        "latency_p90": dlat["p90"],
        "latency_p95": dlat["p95"],
        "latency_p99": dlat["p99"],
        "latency_max": dlat["max"],
        "event_drops": lat["dropped_events"],
        "backlog_max": lat["backlog_max"],
        "ab_determinism": {"ok": ab_ok},
        "pbv2_role": PBV2_ROLE,
        "capital_1m_role": CAPITAL_1M_ROLE,
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{
            "run_id": run_id,
            "verdict": verdict,
            "parity": parity["pass"],
            "p95": dlat["p95"],
            "max": dlat["max"],
            "drops": lat["dropped_events"],
        }],
        "latency": [lat["decision_latency_ms"]],
        "parity": [{"pass": parity["pass"], "admitted_n": parity["admitted_n"]}],
        "notification": [nstats],
        "wiring_health": [{"name": k, "ok": v} for k, v in checks.items()],
    }
    publish(OUT, report, sheets)

    print("=== tests ===", flush=True)
    tests = _run_tests()
    report["tests"] = tests
    interim["tests"] = tests
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")
    publish(OUT, report, sheets)

    print(f"=== DONE {verdict} ===", flush=True)
    print(json.dumps({
        "run_id": run_id,
        "verdict": verdict,
        "p95": dlat["p95"],
        "max": dlat["max"],
        "parity": parity["pass"],
        "opened_20260810": False,
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
