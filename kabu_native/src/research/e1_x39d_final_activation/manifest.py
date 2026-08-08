"""Write V1R_PAPER_PRIMARY_ACTIVATION_V1.json operational activation manifest."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from . import (
    CAPITAL_1M_ROLE,
    CHECKPOINTS,
    MODEL_ARTIFACT_SHA,
    NOTIFY_PREFIXES,
    PBV2_ROLE,
    PRECOMMIT_U1_SHA,
    PRIMARY_ROLE,
    STARTUP_ORDER,
    UNIVERSE_BINDING_SHA,
    UNIVERSE_CONTRACT,
    V1R_SHA,
    X38_RUN_ID,
    X39B_RUN_ID,
    X39C_RUN_ID,
    X39_RUN_ID,
)


def write_activation_manifest(out: Path, *, run_id: str) -> dict[str, Any]:
    body = {
        "manifest_id": "V1R_PAPER_PRIMARY_ACTIVATION_V1",
        "kind": "operational_activation_manifest_not_strategy",
        "v1r_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "universe_binding_sha": UNIVERSE_BINDING_SHA,
        "prospective_precommit_sha": PRECOMMIT_U1_SHA,
        "universe_contract": UNIVERSE_CONTRACT,
        "lineage_runs": {
            "x38_wiring": X38_RUN_ID,
            "x39_recovery": X39_RUN_ID,
            "x39b_bridge": X39B_RUN_ID,
            "x39c_concentration": X39C_RUN_ID,
            "x39d_activation": run_id,
        },
        "runtime_roles": {
            "primary": PRIMARY_ROLE,
            "strategy": "PASSIVE_FIXED600_FULL_STRATEGY_V1R",
            "pbv2": PBV2_ROLE,
            "capital_1m": CAPITAL_1M_ROLE,
        },
        "startup_order": list(STARTUP_ORDER),
        "notification_routing": NOTIFY_PREFIXES,
        "evaluation_checkpoints": CHECKPOINTS,
        "paper_only": True,
        "submit_cancel_live": "0/0/0",
        "live_order_path_enabled": False,
        "opened_20260810": False,
        "prospective_observer_started": False,
        "does_not_overwrite_v1r": True,
        "no_285a_exclusion_policy": True,
        "concentration_monitor_only": True,
    }
    raw = json.dumps(body, sort_keys=True, default=str).encode()
    body["sha256"] = hashlib.sha256(raw).hexdigest()
    path = out / "V1R_PAPER_PRIMARY_ACTIVATION_V1.json"
    path.write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    return {"path": str(path), "sha256": body["sha256"], "body": body}
