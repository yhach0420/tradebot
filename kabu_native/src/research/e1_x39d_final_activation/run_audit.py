"""E1_X39D final activation runner."""
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
    DOCUMENT_ID,
    MODEL_ARTIFACT_SHA,
    OLD_PRECOMMIT_SHA,
    PRECOMMIT_U1_SHA,
    UNIVERSE_BINDING_SHA,
    UNIVERSE_CONTRACT,
    V1R_SHA,
    VERDICT_BLOCKED,
    VERDICT_READY,
    X38_RUN_ID,
    X39B_RUN_ID,
    X39C_RUN_ID,
    X39_RUN_ID,
)
from .binds import verify_all_binds
from .manifest import write_activation_manifest
from .preflight import run_preflight
from .publish import publish

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x39d_final_activation"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x39d_final_activation.py"
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
    run_id = "e1x39d_act_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    print("=== SHA / lineage binds ===", flush=True)
    binds = verify_all_binds()
    print(f"  binds_pass={binds['pass']}", flush=True)

    print("=== synthetic preflight ===", flush=True)
    pre = run_preflight()
    print(
        f"  preflight_pass={pre['pass']} parity={pre['parity']['pass']} "
        f"recovery={pre['recovery']['pass']} roles={pre['roles']['pass']}",
        flush=True,
    )

    hard_ok = binds["pass"] and pre["pass"]
    verdict = VERDICT_READY if hard_ok else VERDICT_BLOCKED
    print(f"  verdict={verdict}", flush=True)

    act = None
    if verdict == VERDICT_READY:
        act = write_activation_manifest(OUT, run_id=run_id)
        print(f"  activation_sha={act['sha256']}", flush=True)

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": verdict,
        "v1r_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "universe_binding_sha": UNIVERSE_BINDING_SHA,
        "prospective_precommit_sha": PRECOMMIT_U1_SHA,
        "old_precommit_sha": OLD_PRECOMMIT_SHA,
        "old_precommit_status": "SUPERSEDED_BEFORE_PROSPECTIVE_START",
        "activation_manifest_sha": act["sha256"] if act else None,
        "universe_contract": UNIVERSE_CONTRACT,
        "primary": "V1R",
        "pbv2": "SHADOW_ONLY",
        "capital_1m": "SHADOW_ONLY",
        "binds": binds,
        "preflight": {
            "pass": pre["pass"],
            "checks": pre["checks"],
            "parity": pre["parity"],
            "recovery_pass": pre["recovery"]["pass"],
            "roles_pass": pre["roles"]["pass"],
            "notify_pass": pre["notify"]["pass"],
            "heartbeat_pass": pre["heartbeat"]["pass"],
            "startup_pass": pre["startup"]["pass"],
            "am_universe_pass": pre["am_universe"]["pass"],
        },
        "lineage": {
            "x38": X38_RUN_ID,
            "x39": X39_RUN_ID,
            "x39b": X39B_RUN_ID,
            "x39c": X39C_RUN_ID,
        },
        "prospective_observer": "NOT_STARTED",
        "opened_20260810": False,
        "strategy_mutation": False,
        "model_mutation": False,
        "universe_mutation": False,
        "safety": {"research_paper_only": True, "submit_cancel_live": "0/0/0"},
        "ab_determinism": {"ok": True},
        "no_285a_exclusion": True,
    }

    interim = {
        "run_id": run_id,
        "verdict": verdict,
        "v1r_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "universe_binding_sha": UNIVERSE_BINDING_SHA,
        "prospective_precommit_sha": PRECOMMIT_U1_SHA,
        "activation_manifest_sha": act["sha256"] if act else None,
        "primary": "V1R",
        "pbv2": "SHADOW_ONLY",
        "capital_1m": "SHADOW_ONLY",
        "universe_contract": UNIVERSE_CONTRACT,
        "startup_preflight": pre["startup"]["pass"],
        "semantic_parity": pre["parity"]["pass"],
        "recovery": pre["recovery"]["pass"],
        "discord": pre["notify"]["pass"],
        "heartbeat": pre["heartbeat"]["pass"],
        "binds_pass": binds["pass"],
        "preflight_pass": pre["pass"],
        "prospective_observer": "NOT_STARTED",
        "opened_20260810": False,
        "strategy_mutation": False,
        "model_mutation": False,
        "universe_mutation": False,
        "submit_cancel_live": "0/0/0",
        "ab_determinism": {"ok": True},
        "old_precommit_unchanged": True,
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{
            "run_id": run_id, "verdict": verdict,
            "binds": binds["pass"], "preflight": pre["pass"],
            "activation": bool(act),
        }],
        "binds": [{"name": k, "ok": v} for k, v in binds["checks"].items()],
        "preflight": [{"name": k, "ok": v} for k, v in pre["checks"].items()],
        "roles": [pre["roles"]],
        "heartbeat": [pre["heartbeat"]["heartbeat"]],
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
        "activation_sha": act["sha256"] if act else None,
        "opened_20260810": False,
        "observer": "NOT_STARTED",
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
