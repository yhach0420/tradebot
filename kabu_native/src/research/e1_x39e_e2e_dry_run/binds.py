"""SHA binds + 20260810 protection + broker counter checks."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from research.e1_x37_prospective.freeze import load_model_artifact, load_v1r, verify_model_identity
from research.e1_x37_prospective.wiring import assert_prospective_unopened

from . import (
    ACTIVATION_SHA,
    DEMO_DAY,
    FORBIDDEN_FROM,
    MODEL_ARTIFACT_SHA,
    PRECOMMIT_U1_SHA,
    UNIVERSE_BINDING_SHA,
    UNIVERSE_CONTRACT,
    V1R_SHA,
)

NATIVE = Path(__file__).resolve().parents[3]
X39C = NATIVE / "results" / "research" / "e1_x39c_concentration_reconciliation"
X39D = NATIVE / "results" / "research" / "e1_x39d_final_activation"


def _sha_body(body: dict, key: str = "sha256") -> str:
    raw = {k: v for k, v in body.items() if k != key}
    return hashlib.sha256(json.dumps(raw, sort_keys=True, default=str).encode()).hexdigest()


def verify_binds() -> dict[str, Any]:
    unopened = assert_prospective_unopened()
    v1r = load_v1r()
    ser = load_model_artifact()
    mid = verify_model_identity(ser)
    ub = json.loads((X39C / "V1R_OPERATIONAL_UNIVERSE_BINDING_V1.json").read_text(encoding="utf-8"))
    pc = json.loads((X39C / "PROSPECTIVE_PRECOMMIT_V1R_U1.json").read_text(encoding="utf-8"))
    act = json.loads((X39D / "V1R_PAPER_PRIMARY_ACTIVATION_V1.json").read_text(encoding="utf-8"))

    checks = {
        "v1r": v1r.get("sha256") == V1R_SHA,
        "model": mid["pass"] and ser.get("model_artifact_sha256") == MODEL_ARTIFACT_SHA,
        "universe_binding": ub.get("sha256") == UNIVERSE_BINDING_SHA and _sha_body(ub) == UNIVERSE_BINDING_SHA,
        "precommit_u1": pc.get("sha256") == PRECOMMIT_U1_SHA and _sha_body(pc) == PRECOMMIT_U1_SHA,
        "activation": act.get("sha256") == ACTIVATION_SHA and _sha_body(act) == ACTIVATION_SHA,
        "universe_contract": ub.get("universe_contract") == UNIVERSE_CONTRACT,
        "opened_20260810": unopened.get("opened_20260810") is False,
        "demo_day_not_20260810": DEMO_DAY != FORBIDDEN_FROM and DEMO_DAY < "21000101",
        "activation_observer_not_started": act.get("prospective_observer_started") is False,
    }
    return {"checks": checks, "pass": all(checks.values()), "ser": ser, "unopened": unopened}


def broker_counters() -> dict[str, Any]:
    from small_paper.kabu_order_request_builder import (
        actual_broker_cancel_count,
        actual_broker_submit_count,
    )
    submit = int(actual_broker_submit_count() or 0)
    cancel = int(actual_broker_cancel_count() or 0)
    return {
        "submit": submit,
        "cancel": cancel,
        "live": 0,
        "pass": submit == 0 and cancel == 0,
    }
