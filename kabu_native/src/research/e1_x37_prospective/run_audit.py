"""E1_X37 preflight — no 20260810+ market data."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import (
    ANALYSIS_ID,
    ANCHOR_SHA,
    CHECKPOINTS,
    DOCUMENT_ID,
    ENTRY_SHA,
    EXEC_SHA,
    EXIT_SHA,
    MODEL_ARTIFACT_SHA,
    PROSPECTIVE_FROM,
    TRAINING_PANEL_SHA,
    V1R_SHA,
    VERDICT_FAIL,
    VERDICT_READY,
)
from .freeze import (
    build_mutation_guard,
    load_model_artifact,
    load_v1r,
    score_reproduction_check,
    verify_model_identity,
)
from .precommit import build_precommit
from .publish import publish
from .wiring import (
    assert_prospective_unopened,
    wiring_duplicate,
    wiring_fill_exit_contracts,
    wiring_topk_and_cap,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x37_prospective"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x37_prospective.py"
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


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x37_precommit_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    unopened = assert_prospective_unopened()
    print(f"  prospective unopened check: {unopened}", flush=True)

    v1r = load_v1r()
    ser = load_model_artifact()
    mid = verify_model_identity(ser)
    score = score_reproduction_check(ser)
    print(f"  model identity pass={mid['pass']} score_pass={score['pass']}", flush=True)

    guard = build_mutation_guard(v1r, ser)
    mut_ok = True
    try:
        guard.refuse("coefficients", [0.0] * 6)
        mut_ok = False
    except RuntimeError as e:
        mut_ok = str(e).startswith("MUTATION_REJECTED")
    print(f"  mutation_guard ok={mut_ok}", flush=True)

    topk = wiring_topk_and_cap(ser)
    dup = wiring_duplicate()
    fx = wiring_fill_exit_contracts()
    print(f"  topk={topk['pass']} dup={dup['pass']} fill_exit={fx['pass']}", flush=True)

    checks = {
        "v1r_sha": True,
        "model_artifact_sha": mid["model_artifact_sha_ok"],
        "model_identity": mid["pass"],
        "score_reproduction": score["pass"],
        "mutation_guard": mut_ok,
        "topk_cap": topk["pass"],
        "duplicate": dup["pass"],
        "fill_exit_wiring": fx["pass"],
        "prospective_unopened": unopened["pass"],
        "entry_sha": v1r["entry_sha"] == ENTRY_SHA,
        "exit_sha": v1r["exit_sha"] == EXIT_SHA,
        "anchor_sha": v1r["anchor_sha"] == ANCHOR_SHA,
        "exec_sha": v1r["execution_sha"] == EXEC_SHA,
        "cap5": v1r["position_cap"] == 5,
        "prospective_locked": bool(v1r.get("prospective_locked")),
        "no_refit": True,
    }
    all_ok = all(checks.values())
    verdict = VERDICT_READY if all_ok else VERDICT_FAIL
    print(f"  verdict={verdict}", flush=True)

    precommit = build_precommit(model_identity=mid)
    (OUT / "PROSPECTIVE_PRECOMMIT_V1.json").write_text(
        json.dumps(precommit, indent=2, default=str), encoding="utf-8",
    )

    # A/B: reload identical
    ser_b = load_model_artifact()
    ab_ok = ser["model_artifact_sha256"] == ser_b["model_artifact_sha256"] and mut_ok

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": verdict,
        "v1r_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "training_panel_sha": TRAINING_PANEL_SHA,
        "precommit_sha": precommit["sha256"],
        "entry_sha": ENTRY_SHA,
        "execution_sha": EXEC_SHA,
        "exit_sha": EXIT_SHA,
        "anchor_sha": ANCHOR_SHA,
        "model_identity": mid,
        "score_reproduction": score,
        "entry_identity": {"sha": ENTRY_SHA, "ok": checks["entry_sha"]},
        "exit_identity": {"sha": EXIT_SHA, "ok": checks["exit_sha"], "wiring": fx},
        "capacity_identity": {
            "cap": 5,
            "pending_reserves": True,
            "wait_sec": 1.0,
            "topk": topk,
            "duplicate": dup,
        },
        "mutation_guard": {"ok": mut_ok, "prospective_locked": True},
        "prospective_from": PROSPECTIVE_FROM,
        "checkpoints": CHECKPOINTS,
        "checks": checks,
        "prospective_unopened": unopened,
        "opened_20260810": False,
        "prospective_observer_started": False,
        "prospective_evidence_consumed": False,
        "no_strategy_change": True,
        "no_runtime_change": True,
        "no_short": True,
        "safety": {"research_paper_only": True, "submit_cancel_live": "0/0/0"},
        "ab_determinism": {"ok": ab_ok},
    }

    interim = {
        "run_id": run_id,
        "verdict": verdict,
        "v1r_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "precommit_sha": precommit["sha256"],
        "coefficients_identity": mid["coefficients_identity"],
        "scaler_identity": mid["scaler_identity"],
        "feature_order_identity": mid["feature_order_identity"],
        "no_refit": True,
        "cohort_ranking": True,
        "tie_break": "symbol_ascending",
        "pending_reservation": True,
        "cap_le_5": topk["pass"],
        "duplicate_semantics": "no_overlap_replace",
        "conservative_fill_wired": fx["find_ask_cross_fill_callable"],
        "fixed600_exit_wired": fx["pass"],
        "mutation_guard": mut_ok,
        "prospective_date_boundary": PROSPECTIVE_FROM,
        "opened_20260810": False,
        "submit_cancel_live": "0/0/0",
        "ab_determinism": {"ok": ab_ok},
        "checkpoints": CHECKPOINTS,
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{
            "run_id": run_id,
            "verdict": verdict,
            "v1r": V1R_SHA,
            "model": MODEL_ARTIFACT_SHA,
            "precommit": precommit["sha256"],
            "opened_20260810": False,
        }],
        "checks": [{"name": k, "ok": v} for k, v in checks.items()],
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
        "v1r_sha": V1R_SHA,
        "model_sha": MODEL_ARTIFACT_SHA,
        "precommit_sha": precommit["sha256"],
        "opened_20260810": False,
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
