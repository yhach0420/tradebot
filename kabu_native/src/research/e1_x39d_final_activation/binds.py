"""SHA / artifact binds for final activation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.e1_x37_prospective.freeze import load_model_artifact, load_v1r, verify_model_identity
from research.e1_x37_prospective.wiring import assert_prospective_unopened

from . import (
    MODEL_ARTIFACT_SHA,
    OLD_PRECOMMIT_SHA,
    PRECOMMIT_U1_SHA,
    UNIVERSE_BINDING_SHA,
    UNIVERSE_CONTRACT,
    V1R_SHA,
    X38_RUN_ID,
    X39B_RUN_ID,
    X39C_RUN_ID,
    X39_RUN_ID,
)

NATIVE = Path(__file__).resolve().parents[3]
X37 = NATIVE / "results" / "research" / "e1_x37_prospective"
X38 = NATIVE / "results" / "research" / "e1_x38_operational_wiring"
X39 = NATIVE / "results" / "research" / "e1_x39_activation_lock"
X39B = NATIVE / "results" / "research" / "e1_x39b_universe_bridge"
X39C = NATIVE / "results" / "research" / "e1_x39c_concentration_reconciliation"


def _sha_body(body: dict, key: str = "sha256") -> str:
    raw = {k: v for k, v in body.items() if k != key}
    return hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()


def verify_all_binds() -> dict[str, Any]:
    unopened = assert_prospective_unopened()
    v1r = load_v1r()
    ser = load_model_artifact()
    mid = verify_model_identity(ser)

    ub = json.loads((X39C / "V1R_OPERATIONAL_UNIVERSE_BINDING_V1.json").read_text(encoding="utf-8"))
    pc = json.loads((X39C / "PROSPECTIVE_PRECOMMIT_V1R_U1.json").read_text(encoding="utf-8"))
    old = json.loads((X37 / "PROSPECTIVE_PRECOMMIT_V1.json").read_text(encoding="utf-8"))
    superseded = json.loads(
        (X39C / "PROSPECTIVE_PRECOMMIT_V1_SUPERSEDED_BEFORE_PROSPECTIVE_START.json").read_text(encoding="utf-8")
    )

    checks = {
        "v1r_sha": v1r.get("sha256") == V1R_SHA,
        "model_sha": mid.get("pass") and ser.get("model_artifact_sha256") == MODEL_ARTIFACT_SHA,
        "universe_binding_sha": (
            ub.get("sha256") == UNIVERSE_BINDING_SHA
            and _sha_body(ub) == UNIVERSE_BINDING_SHA
            and ub.get("universe_contract") == UNIVERSE_CONTRACT
        ),
        "precommit_u1_sha": (
            pc.get("sha256") == PRECOMMIT_U1_SHA
            and _sha_body(pc) == PRECOMMIT_U1_SHA
            and pc.get("universe_binding_sha") == UNIVERSE_BINDING_SHA
        ),
        "old_precommit_unchanged": (
            old.get("sha256") == OLD_PRECOMMIT_SHA
            and _sha_body(old) == OLD_PRECOMMIT_SHA
        ),
        "old_precommit_superseded_marker": (
            superseded.get("status") == "SUPERSEDED_BEFORE_PROSPECTIVE_START"
            and superseded.get("original_sha256") == OLD_PRECOMMIT_SHA
        ),
        "x38_run": json.loads((X38 / "report.json").read_text(encoding="utf-8")).get("run_id") == X38_RUN_ID,
        "x39_run": json.loads((X39 / "_interim.json").read_text(encoding="utf-8")).get("run_id") == X39_RUN_ID,
        "x39b_run": json.loads((X39B / "report.json").read_text(encoding="utf-8")).get("run_id") == X39B_RUN_ID,
        "x39c_run": json.loads((X39C / "report.json").read_text(encoding="utf-8")).get("run_id") == X39C_RUN_ID,
        "opened_20260810": unopened.get("opened_20260810") is False,
        "does_not_overwrite_v1r": ub.get("does_not_overwrite_v1r") is True,
    }
    return {
        "checks": checks,
        "pass": all(checks.values()),
        "unopened": unopened,
        "universe_contract": UNIVERSE_CONTRACT,
        "checkpoints": pc.get("evaluation_checkpoints"),
    }
