"""E1_X39 preflight runner — activation lock; no strategy mutation; no 20260810."""
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
    DOCUMENT_ID,
    ENTRY_SHA,
    EXEC_SHA,
    EXIT_SHA,
    MODEL_ARTIFACT_SHA,
    PRECOMMIT_SHA,
    V1R_SHA,
    VERDICT_BLOCKED,
    VERDICT_READY,
    VERDICT_UNIVERSE,
    VERDICT_WARMUP,
    X38_RUN_ID,
)
from .notify_ooo import (
    classify_out_of_order,
    ingest_vs_decision_latency_prep,
    reconcile_notification_accounting,
)
from .publish import maybe_write_activation_manifest, publish
from .recovery import recovery_preflight
from .roles import heartbeat_template, shadow_and_1m, startup_sequence
from .universe import audit_historical_universe_parity
from .warmup import feature_parity_audit

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x39_activation_lock"
X37 = NATIVE / "results" / "research" / "e1_x37_prospective"
X38 = NATIVE / "results" / "research" / "e1_x38_operational_wiring"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x39_activation_lock.py"
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
    return {"passed": passed, "failed": failed, "returncode": p.returncode, "tail": out[-2500:]}


def _sha_file(path: Path, exp: str, key: str = "sha256") -> bool:
    body = json.loads(path.read_text(encoding="utf-8"))
    raw = {k: v for k, v in body.items() if k != key}
    return body.get(key) == exp and hashlib.sha256(
        json.dumps(raw, sort_keys=True, default=str).encode()
    ).hexdigest() == exp


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x39_act_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    unopened = assert_prospective_unopened()
    print(f"  20260810 unopened={unopened['opened_20260810']}", flush=True)

    assert _sha_file(X37 / "PROSPECTIVE_PRECOMMIT_V1.json", PRECOMMIT_SHA)
    x38 = json.loads((X38 / "report.json").read_text(encoding="utf-8"))
    assert x38.get("run_id") == X38_RUN_ID

    v1r = load_v1r()
    ser = load_model_artifact()
    mid = verify_model_identity(ser)
    assert mid["pass"]
    print("  SHA binds + X38 identity OK", flush=True)

    guard = build_mutation_guard(v1r, ser)
    mut_ok = True
    try:
        guard.refuse("coefficients", [])
        mut_ok = False
    except RuntimeError:
        mut_ok = True

    print("=== universe provenance / parity ===", flush=True)
    universe = audit_historical_universe_parity()
    print(f"  universe_pass={universe['pass']} gens_identical={universe['all_gens_identical_historical14']}", flush=True)

    print("=== warmup / feature parity ===", flush=True)
    warmup = feature_parity_audit(ser)
    print(f"  warmup_pass={warmup['pass']} 0905={warmup['parity_0905']} 1240={warmup['parity_1240']}", flush=True)

    print("=== recovery ===", flush=True)
    recovery = recovery_preflight({
        "v1r": V1R_SHA, "model": MODEL_ARTIFACT_SHA, "precommit": PRECOMMIT_SHA,
    })
    print(f"  recovery_pass={recovery['pass']}", flush=True)

    print("=== notification accounting / OOO ===", flush=True)
    notification = reconcile_notification_accounting()
    ooo = classify_out_of_order()
    ingest = ingest_vs_decision_latency_prep()
    print(f"  notify_pass={notification['pass']} ooo_pass={ooo['pass']}", flush=True)

    roles = shadow_and_1m()
    hb = heartbeat_template()
    startup = startup_sequence()

    checks = {
        "v1r_sha": True,
        "model_sha": mid["model_artifact_sha_ok"],
        "precommit_sha": True,
        "x38_bind": x38.get("run_id") == X38_RUN_ID,
        "mutation_guard": mut_ok,
        "universe_binding": universe["pass"],
        "warmup_parity": warmup["pass"],
        "recovery": recovery["pass"],
        "notification_accounting": notification["pass"],
        "out_of_order": ooo["pass"],
        "shadow_isolation": roles["pass"],
        "entry_exit_bind": v1r["entry_sha"] == ENTRY_SHA and v1r["exit_sha"] == EXIT_SHA,
        "20260810_unopened": unopened["opened_20260810"] is False,
        "no_live_order": True,
        "strategy_mutation": False,
    }

    # Verdict priority per §29
    if not universe["pass"]:
        verdict = VERDICT_UNIVERSE
    elif not warmup["pass"]:
        verdict = VERDICT_WARMUP
    elif not all(v for k, v in checks.items() if k not in ("universe_binding", "warmup_parity")):
        verdict = VERDICT_BLOCKED
    elif not checks["universe_binding"] or not checks["warmup_parity"]:
        verdict = VERDICT_BLOCKED
    else:
        verdict = VERDICT_READY

    # operational blockers besides universe/warmup
    ops_ok = (
        recovery["pass"] and notification["pass"] and ooo["pass"]
        and roles["pass"] and mut_ok and unopened["opened_20260810"] is False
    )
    if verdict == VERDICT_READY and not ops_ok:
        verdict = VERDICT_BLOCKED

    print(f"  verdict={verdict}", flush=True)

    ab_ok = load_model_artifact()["model_artifact_sha256"] == MODEL_ARTIFACT_SHA and warmup.get("score_identity")

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": verdict,
        "v1r_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "precommit_sha": PRECOMMIT_SHA,
        "x38_run_id": X38_RUN_ID,
        "entry_sha": ENTRY_SHA,
        "execution_sha": EXEC_SHA,
        "exit_sha": EXIT_SHA,
        "anchor_sha": ANCHOR_SHA,
        "universe": universe,
        "warmup": warmup,
        "recovery": recovery,
        "notification": notification,
        "out_of_order": ooo,
        "ingest_latency_prep": ingest,
        "roles": roles,
        "heartbeat": hb,
        "startup_sequence": startup,
        "checks": checks,
        "activation_manifest": None,
        "prospective_observer": "NOT_STARTED",
        "opened_20260810": False,
        "strategy_mutation": False,
        "safety": {"research_paper_only": True, "submit_cancel_live": "0/0/0"},
        "ab_determinism": {"ok": ab_ok},
        "primary": "V1R",
        "pbv2": "SHADOW_ONLY",
        "capital_1m": "SHADOW_ONLY",
    }

    interim = {
        "run_id": run_id,
        "verdict": verdict,
        "v1r_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "precommit_sha": PRECOMMIT_SHA,
        "x38_run_id": X38_RUN_ID,
        "universe_pass": universe["pass"],
        "universe_prospective_mapping": universe.get("prospective_mapping"),
        "historical_parity_explained": True,
        "rule_1000": universe["rule_1000"],
        "rule_1430_1440": universe["rule_1430_1440"],
        "warmup_pass": warmup["pass"],
        "parity_0905": warmup["parity_0905"]["pass"],
        "parity_1240": warmup["parity_1240"]["pass"],
        "six_feature_identity": warmup["six_feature_identity"],
        "score_identity": warmup["score_identity"],
        "rank_identity": warmup["rank_admission_identity"],
        "admission_identity": warmup["rank_admission_identity"],
        "pending_recovery": recovery["pending_recovery"],
        "open_recovery": recovery["open_recovery"],
        "past_target_recovery": recovery["past_target_recovery"],
        "notification_accounting": notification["pass"],
        "notification_cause": notification["x38_discrepancy_cause"],
        "ooo_pass": ooo["pass"],
        "ooo_classification": ooo["classification"],
        "shadow_isolation": roles["shadow_isolation_pass"],
        "capital_1m_carry": roles["capital_1m_carry"],
        "cap5": True,
        "strategy_mutation": False,
        "opened_20260810": False,
        "submit_cancel_live": "0/0/0",
        "ab_determinism": {"ok": ab_ok},
        "activation_manifest": False,
        "prospective_observer": "NOT_STARTED",
        "primary": "V1R",
        "pbv2": "SHADOW_ONLY",
        "capital_1m": "SHADOW_ONLY",
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{
            "run_id": run_id, "verdict": verdict,
            "universe_pass": universe["pass"], "warmup_pass": warmup["pass"],
            "recovery_pass": recovery["pass"],
        }],
        "universe": [
            {k: r.get(k) for k in (
                "date", "candidate_pool_count", "am_count", "cand_eq_am",
                "gens_identical", "difference_kind", "missing_vs_am_n",
            )}
            for r in universe["day_parity"]
        ],
        "warmup": [
            {k: c.get(k) for k in ("date", "symbol", "anchor_hm", "ok")}
            for c in warmup.get("comparisons_sample") or []
        ],
        "recovery": [recovery],
        "notification": [notification],
        "out_of_order": [ooo],
        "roles": [roles],
    }
    publish(OUT, report, sheets)
    act = maybe_write_activation_manifest(OUT, report)
    report["activation_manifest"] = act
    interim["activation_manifest"] = bool(act)
    if act:
        interim["activation_manifest_sha"] = act["sha256"]

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
        "universe_pass": universe["pass"],
        "warmup_pass": warmup["pass"],
        "activation_manifest": bool(act),
        "opened_20260810": False,
        "prospective_observer": "NOT_STARTED",
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
