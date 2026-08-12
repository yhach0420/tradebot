"""V1R EXIT V2 Paper Primary activation gate — fail-closed.

Primary = Arch E. Control = FIXED600 SHADOW_CONTROL.
No fallback to FIXED600 Primary or PBv2 Primary.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from small_paper.v1r_exit_v2_contract import (
    EXIT_V2_CANDIDATE_SHA,
    FROZEN_CONTINUATION,
    FROZEN_GUARD,
    load_exit_v2_candidate,
)
from small_paper.v1r_primary_runtime import (
    ANCHOR_SHA,
    BOARD_FRESHNESS_SEC_V1R,
    CLOCK_GRID,
    DUPLICATE_RULE,
    LOT_QTY,
    MODEL_ARTIFACT_SHA,
    POSITION_CAP,
    UNIVERSE_BINDING_SHA,
    UNIVERSE_CONTRACT,
    V1R_SHA,
    WAIT_SEC,
    assert_v1r_not_contaminated,
    resolve_v1r_effective_from_production,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[2]
OUT = NATIVE / "results/research/v1r_exit_v2_prospective_activation"

PRIMARY_STRATEGY = "PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY"
STRATEGY_SHA = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
EXIT_CONTRACT_SHA = "9e3494c38e8040acb47ecbf057d6b0bbaa25682492308e7335ae74c1d47d4b19"
PRECOMMIT_SHA = "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100"
ACTIVATION_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V2"
ACTIVATION_SHA = "0cd4b6289e392269035448b7a71be0b1f2b449782b57991a9e028f8e1be7bd46"
PARENT_ACTIVATION_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V1"
PARENT_ACTIVATION_SHA = "29cbc5933421319ffcb1ed24d9be517d35e74c1027ebe67df431657c6997ada1"
RUNTIME_CODE_GIT_COMMIT = "68a915ad55bc455f028b25103db40405cf7f89a9"
CONTROL_STRATEGY_SHA = "dfd311d4dc32a802b8e55f6d28d75a2db12d4192a71fb53b48d5308573a58e0a"
ENTRY_SHA = "f2887bb2be539cc173aee438a43ee8afb8cfa2b8c31380937ecd843e90dd9b29"
EXEC_SHA = "040fa4b061e575d3f6cdb2a11ffd3f862da5351b298567b31363de923a590869"
GUARD_ID = "IMB_p5_t-10"
CONTINUATION_ID = "MFE60_IMB10"

V1R_ROLE = "PAPER_PRIMARY"
CONTROL_ROLE = "SHADOW_CONTROL"
PBV2_ROLE = "SHADOW_ONLY"
ONE_M_ROLE = "SHADOW_ONLY_DIAGNOSTIC"
ASSERTION_FAIL = "V1R_EXIT_V2_PRIMARY_ROLE_ASSERTION_FAILED"


@dataclass
class RoleAssertionResult:
    ok: bool
    reason: str = ""
    checks: dict[str, bool] = field(default_factory=dict)
    identity: dict[str, Any] = field(default_factory=dict)
    startup_block: str = ""
    ready: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_identity() -> dict[str, Any]:
    return {
        "activation_id": ACTIVATION_ID,
        "primary_strategy": PRIMARY_STRATEGY,
        "primary_role": V1R_ROLE,
        "strategy_sha": STRATEGY_SHA,
        "exit_v2_candidate_sha": EXIT_V2_CANDIDATE_SHA,
        "exit_contract_sha": EXIT_CONTRACT_SHA,
        "precommit_sha": PRECOMMIT_SHA,
        "activation_sha": ACTIVATION_SHA,
        "parent_activation_id": PARENT_ACTIVATION_ID,
        "parent_activation_sha": PARENT_ACTIVATION_SHA,
        "runtime_code_git_commit": RUNTIME_CODE_GIT_COMMIT,
        "entry_source": "v1r_native",
        "entry_sha": ENTRY_SHA,
        "execution_sha": EXEC_SHA,
        "entry_v1r_sha": V1R_SHA,
        "model_sha": MODEL_ARTIFACT_SHA,
        "universe_binding_sha": UNIVERSE_BINDING_SHA,
        "universe_contract": UNIVERSE_CONTRACT,
        "anchor_sha": ANCHOR_SHA,
        "control_role": CONTROL_ROLE,
        "control_strategy_sha": CONTROL_STRATEGY_SHA,
        "pbv2_role": PBV2_ROLE,
        "one_m_role": ONE_M_ROLE,
        "guard_id": GUARD_ID,
        "continuation_id": CONTINUATION_ID,
        "cap": POSITION_CAP,
        "qty": LOT_QTY,
        "wait_sec": WAIT_SEC,
        "freshness_sec": BOARD_FRESHNESS_SEC_V1R,
        "duplicate_rule": DUPLICATE_RULE,
        "anchor_count": len(CLOCK_GRID),
        "timestamp_clock": "Ingress received_at",
        "live_trading_enabled": False,
        "order_enabled": False,
        "paper_only": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
    }


def format_startup_contract(*, ready: bool, reason: str = "") -> str:
    ident = build_identity()
    return "\n".join([
        "[V1R EXIT V2 STARTUP CONTRACT]",
        "",
        "Activation:",
        f"{ident['activation_id']}",
        f"activation_sha={ident['activation_sha']}",
        f"runtime_code_git_commit={ident['runtime_code_git_commit']}",
        f"timestamp={ident['timestamp_clock']}",
        "",
        "Primary:",
        f"Arch E {ident['primary_role']}",
        f"strategy={ident['primary_strategy']}",
        f"ENTRY={ident['entry_source']} / PASSIVE_FILL_ENTRY_V1",
        f"guard={ident['guard_id']} continuation={ident['continuation_id']}",
        "",
        "Control:",
        f"FIXED600 {ident['control_role']}",
        "",
        "PBv2:",
        ident["pbv2_role"],
        "",
        "1M:",
        ident["one_m_role"],
        "",
        "Universe:",
        ident["universe_contract"],
        "",
        "Cap/Wait/Freshness:",
        f"{ident['cap']} / {ident['wait_sec']}s / {ident['freshness_sec']}s",
        "",
        "submit/cancel/live:",
        "0/0/0",
        "",
        "READY:",
        "YES" if ready else f"NO ({reason or ASSERTION_FAIL})",
    ])


def assert_exit_v2_primary_roles() -> RoleAssertionResult:
    checks: dict[str, bool] = {}
    identity = build_identity()
    checks["primary_strategy"] = identity["primary_strategy"] == PRIMARY_STRATEGY
    checks["strategy_sha_pin"] = identity["strategy_sha"] == STRATEGY_SHA
    checks["guard_id"] = identity["guard_id"] == GUARD_ID == FROZEN_GUARD["id"]
    checks["continuation_id"] = identity["continuation_id"] == CONTINUATION_ID == FROZEN_CONTINUATION["id"]
    checks["control_role"] = identity["control_role"] == CONTROL_ROLE
    checks["pbv2_shadow"] = identity["pbv2_role"] == PBV2_ROLE
    checks["one_m_shadow"] = identity["one_m_role"] == ONE_M_ROLE
    checks["cap"] = identity["cap"] == 5
    checks["qty"] = identity["qty"] == 100
    checks["wait"] = float(identity["wait_sec"]) == 1.0
    checks["freshness"] = float(identity["freshness_sec"]) == 5.0
    checks["live_off"] = (not identity["live_trading_enabled"]) and (not identity["order_enabled"])
    checks["paper_only"] = bool(identity["paper_only"])
    try:
        c = load_exit_v2_candidate()
        checks["exit_candidate_sha"] = c.get("sha256") == EXIT_V2_CANDIDATE_SHA
    except Exception:
        checks["exit_candidate_sha"] = False
    for name, sha in (
        ("strategy", STRATEGY_SHA),
        ("exit_contract", EXIT_CONTRACT_SHA),
        ("precommit", PRECOMMIT_SHA),
        ("activation", ACTIVATION_SHA),
        ("activation_v1_immutable", PARENT_ACTIVATION_SHA),
    ):
        p = OUT / (
            f"{PRIMARY_STRATEGY}.json" if name == "strategy"
            else "PASSIVE_ASYMMETRIC_EXIT_V2_CONTRACT_V1.json" if name == "exit_contract"
            else f"PROSPECTIVE_PRECOMMIT_V1R_EXIT_V2_U1.json" if name == "precommit"
            else f"V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V2.json" if name == "activation"
            else f"V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V1.json"
        )
        try:
            body = json.loads(p.read_text(encoding="utf-8"))
            checks[f"file_{name}_sha"] = body.get("sha256") == sha
        except Exception:
            checks[f"file_{name}_sha"] = False
    checks["activation_id"] = identity["activation_id"] == ACTIVATION_ID
    checks["entry_source_v1r_native"] = identity["entry_source"] == "v1r_native"
    checks["timestamp_ingress_received_at"] = identity["timestamp_clock"] == "Ingress received_at"
    try:
        bind = json.loads(
            (NATIVE / "results/research/e1_x39c_concentration_reconciliation"
             / "V1R_OPERATIONAL_UNIVERSE_BINDING_V1.json").read_text(encoding="utf-8")
        )
        checks["universe_binding"] = bind.get("sha256") == UNIVERSE_BINDING_SHA
    except Exception:
        checks["universe_binding"] = False
    try:
        eff = resolve_v1r_effective_from_production()
        iso = assert_v1r_not_contaminated(eff)
        checks["yaml_isolation"] = bool(iso.get("pass"))
        checks["pin_match"] = bool(eff.pin_match)
    except Exception:
        checks["yaml_isolation"] = False
        checks["pin_match"] = False

    failed = [k for k, v in checks.items() if not v]
    ok = len(failed) == 0
    reason = "" if ok else f"{ASSERTION_FAIL}:{','.join(failed[:10])}"
    block = format_startup_contract(ready=ok, reason=reason)
    return RoleAssertionResult(ok=ok, reason=reason, checks=checks, identity=identity, startup_block=block, ready=ok)
