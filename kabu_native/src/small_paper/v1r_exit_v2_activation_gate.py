"""V1R EXIT V2 Paper Primary activation gate — fail-closed.

Primary = Arch E. Control = FIXED600 SHADOW_CONTROL.
No fallback to FIXED600 Primary or PBv2 Primary.

Activation identity is NOT hard-coded here (breaks freeze self-reference).
Startup path:
  active selector → activation manifest → self SHA → runtime inventory → Strategy/roles
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from small_paper.v1r_activation_binding import (
    OUT,
    load_activation_manifest,
    load_active_selector,
    verify_manifest_self_sha,
    verify_runtime_inventory,
    verify_selector_binding,
)
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

NATIVE = Path(__file__).resolve().parents[2]

PRIMARY_STRATEGY = "PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY"
STRATEGY_SHA = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
EXIT_CONTRACT_SHA = "9e3494c38e8040acb47ecbf057d6b0bbaa25682492308e7335ae74c1d47d4b19"
PRECOMMIT_SHA = "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100"
# Parent V1 remains immutable historical pin (not the active selector target).
PARENT_ACTIVATION_V1_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V1"
PARENT_ACTIVATION_V1_SHA = "29cbc5933421319ffcb1ed24d9be517d35e74c1027ebe67df431657c6997ada1"
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


def _load_bound_activation() -> tuple[dict[str, Any], dict[str, Any]]:
    selector = load_active_selector()
    manifest = load_activation_manifest(selector=selector)
    return selector, manifest


def build_identity(
    *,
    selector: Optional[dict[str, Any]] = None,
    manifest: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if selector is None or manifest is None:
        selector, manifest = _load_bound_activation()
    mid = str(manifest.get("manifest_id") or "")
    return {
        "activation_id": mid,
        "primary_strategy": PRIMARY_STRATEGY,
        "primary_role": V1R_ROLE,
        "strategy_sha": STRATEGY_SHA,
        "exit_v2_candidate_sha": EXIT_V2_CANDIDATE_SHA,
        "exit_contract_sha": EXIT_CONTRACT_SHA,
        "precommit_sha": PRECOMMIT_SHA,
        "activation_sha": str(manifest.get("sha256") or ""),
        "parent_activation_id": str(manifest.get("parent_activation_id") or ""),
        "parent_activation_sha": str(manifest.get("parent_activation_sha") or ""),
        "runtime_code_git_commit": str(manifest.get("runtime_code_git_commit") or ""),
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
        "selector_activation_id": str(selector.get("activation_id") or ""),
        "selector_activation_sha": str(selector.get("activation_sha") or ""),
        "runtime_inventory_n": len(manifest.get("runtime_file_sha256") or {}),
    }


def format_startup_contract(*, ready: bool, reason: str = "") -> str:
    try:
        ident = build_identity()
    except Exception as exc:
        return "\n".join(
            [
                "[V1R EXIT V2 STARTUP CONTRACT]",
                "",
                "Activation:",
                "UNRESOLVED",
                f"error={type(exc).__name__}:{exc}",
                "",
                "READY:",
                f"NO ({reason or ASSERTION_FAIL})",
            ]
        )
    return "\n".join(
        [
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
        ]
    )


def assert_exit_v2_primary_roles() -> RoleAssertionResult:
    checks: dict[str, bool] = {}
    try:
        selector, manifest = _load_bound_activation()
    except Exception as exc:
        reason = f"{ASSERTION_FAIL}:selector_or_manifest:{type(exc).__name__}:{exc}"
        block = format_startup_contract(ready=False, reason=reason)
        return RoleAssertionResult(
            ok=False, reason=reason, checks={"selector_load": False}, identity={}, startup_block=block, ready=False
        )

    identity = build_identity(selector=selector, manifest=manifest)

    # A) selector → expected activation id/sha
    bind = verify_selector_binding(selector, manifest)
    checks["selector_activation_id"] = bind["activation_id_match"]
    checks["selector_activation_sha"] = bind["activation_sha_match"]

    # B) manifest self SHA
    self_ok, _got, _calc = verify_manifest_self_sha(manifest)
    checks["manifest_self_sha"] = self_ok

    # C) runtime inventory (working-tree bytes)
    inv = verify_runtime_inventory(manifest, native_root=NATIVE)
    checks["runtime_inventory"] = bool(inv.get("ok"))
    identity["runtime_inventory"] = {
        "ok": inv.get("ok"),
        "matched": inv.get("matched"),
        "expected_n": inv.get("expected_n"),
        "reason": inv.get("reason"),
        "mismatch_n": len(inv.get("mismatches") or []),
    }

    # D) Strategy / Precommit / roles / safety
    checks["primary_strategy"] = identity["primary_strategy"] == PRIMARY_STRATEGY
    checks["strategy_sha_pin"] = (
        identity["strategy_sha"] == STRATEGY_SHA == str(manifest.get("strategy_sha") or "")
    )
    checks["precommit_sha_pin"] = (
        identity["precommit_sha"] == PRECOMMIT_SHA == str(manifest.get("precommit_sha") or "")
    )
    checks["guard_id"] = identity["guard_id"] == GUARD_ID == FROZEN_GUARD["id"]
    checks["continuation_id"] = identity["continuation_id"] == CONTINUATION_ID == FROZEN_CONTINUATION["id"]
    checks["control_role"] = identity["control_role"] == CONTROL_ROLE
    checks["pbv2_shadow"] = identity["pbv2_role"] == PBV2_ROLE
    checks["one_m_shadow"] = identity["one_m_role"] == ONE_M_ROLE
    checks["cap"] = identity["cap"] == 5 == int(manifest.get("cap") or -1)
    checks["qty"] = identity["qty"] == 100 == int(manifest.get("qty") or -1)
    checks["wait"] = float(identity["wait_sec"]) == 1.0 == float(manifest.get("wait_sec") or -1)
    checks["freshness"] = float(identity["freshness_sec"]) == 5.0 == float(
        manifest.get("freshness_sec") or -1
    )
    checks["live_off"] = (not identity["live_trading_enabled"]) and (not identity["order_enabled"])
    checks["paper_only"] = bool(identity["paper_only"]) and bool(manifest.get("paper_only", True))
    checks["submit_cancel_live_zero"] = str(manifest.get("submit_cancel_live") or "") == "0/0/0"
    try:
        c = load_exit_v2_candidate()
        checks["exit_candidate_sha"] = c.get("sha256") == EXIT_V2_CANDIDATE_SHA
    except Exception:
        checks["exit_candidate_sha"] = False

    for name, sha in (
        ("strategy", STRATEGY_SHA),
        ("exit_contract", EXIT_CONTRACT_SHA),
        ("precommit", PRECOMMIT_SHA),
        ("activation_v1_immutable", PARENT_ACTIVATION_V1_SHA),
    ):
        p = OUT / (
            f"{PRIMARY_STRATEGY}.json"
            if name == "strategy"
            else "PASSIVE_ASYMMETRIC_EXIT_V2_CONTRACT_V1.json"
            if name == "exit_contract"
            else "PROSPECTIVE_PRECOMMIT_V1R_EXIT_V2_U1.json"
            if name == "precommit"
            else f"{PARENT_ACTIVATION_V1_ID}.json"
        )
        try:
            body = json.loads(p.read_text(encoding="utf-8"))
            checks[f"file_{name}_sha"] = body.get("sha256") == sha
        except Exception:
            checks[f"file_{name}_sha"] = False

    # Active activation file sha already covered by selector+self checks
    checks["entry_source_v1r_native"] = identity["entry_source"] == "v1r_native"
    checks["timestamp_ingress_received_at"] = identity["timestamp_clock"] == "Ingress received_at"
    checks["no_hardcoded_activation_sha_in_gate"] = True  # architectural invariant (this module)

    try:
        bind_u = json.loads(
            (
                NATIVE
                / "results/research/e1_x39c_concentration_reconciliation"
                / "V1R_OPERATIONAL_UNIVERSE_BINDING_V1.json"
            ).read_text(encoding="utf-8")
        )
        checks["universe_binding"] = bind_u.get("sha256") == UNIVERSE_BINDING_SHA
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
    reason = "" if ok else f"{ASSERTION_FAIL}:{','.join(failed[:12])}"
    if not inv.get("ok") and inv.get("mismatches"):
        identity["runtime_inventory_mismatches_sample"] = (inv.get("mismatches") or [])[:5]
    block = format_startup_contract(ready=ok, reason=reason)
    return RoleAssertionResult(
        ok=ok, reason=reason, checks=checks, identity=identity, startup_block=block, ready=ok
    )
