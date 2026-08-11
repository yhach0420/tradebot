"""V1R Paper Primary activation gate — fail-closed role / SHA assertions.

If assertion fails: NO PAPER PRIMARY. Never fall back to classic PBv2 Primary.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from small_paper.v1r_primary_runtime import (
    ACTIVATION_SHA,
    ANCHOR_SHA,
    BOARD_FRESHNESS_SEC_V1R,
    CLOCK_GRID,
    DUPLICATE_RULE,
    EXIT_HOLD_SEC,
    LOT_QTY,
    MODEL_ARTIFACT_SHA,
    POSITION_CAP,
    PRECOMMIT_U1_SHA,
    UNIVERSE_BINDING_SHA,
    UNIVERSE_CONTRACT,
    V1R_SHA,
    WAIT_SEC,
    resolve_v1r_effective_from_production,
    assert_v1r_not_contaminated,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[2]

PRIMARY_STRATEGY = "PASSIVE_FIXED600_FULL_STRATEGY_V1R"
V1R_ROLE = "PAPER_PRIMARY"
PBV2_ROLE = "SHADOW_ONLY"
ONE_M_ROLE = "SHADOW_ONLY_DIAGNOSTIC"
EXIT_CONTRACT = "FIRST_VALID_BUY1_AT_OR_AFTER_TARGET"

ASSERTION_FAIL = "V1R_PRIMARY_ROLE_ASSERTION_FAILED"


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


def _verify_freeze_files() -> dict[str, bool]:
    from research.e1_x37_prospective.freeze import load_model_artifact, load_v1r, verify_model_identity

    checks: dict[str, bool] = {}
    try:
        v1r = load_v1r()
        checks["strategy_sha"] = v1r.get("sha256") == V1R_SHA
    except Exception:
        checks["strategy_sha"] = False
    try:
        ser = load_model_artifact()
        checks["model_sha"] = ser.get("model_artifact_sha256") == MODEL_ARTIFACT_SHA
        checks["model_identity"] = bool(verify_model_identity(ser).get("pass"))
    except Exception:
        checks["model_sha"] = False
        checks["model_identity"] = False
    try:
        bind = json.loads(
            (NATIVE / "results/research/e1_x39c_concentration_reconciliation"
             / "V1R_OPERATIONAL_UNIVERSE_BINDING_V1.json").read_text(encoding="utf-8")
        )
        checks["universe_binding_sha"] = bind.get("sha256") == UNIVERSE_BINDING_SHA
        checks["universe_contract"] = bind.get("universe_contract") == UNIVERSE_CONTRACT
    except Exception:
        checks["universe_binding_sha"] = False
        checks["universe_contract"] = False
    try:
        pre = json.loads(
            (NATIVE / "results/research/e1_x39c_concentration_reconciliation"
             / "PROSPECTIVE_PRECOMMIT_V1R_U1.json").read_text(encoding="utf-8")
        )
        checks["precommit_sha"] = pre.get("sha256") == PRECOMMIT_U1_SHA
    except Exception:
        checks["precommit_sha"] = False
    try:
        act = json.loads(
            (NATIVE / "results/research/e1_x39d_final_activation"
             / "V1R_PAPER_PRIMARY_ACTIVATION_V1.json").read_text(encoding="utf-8")
        )
        roles = act.get("runtime_roles") or {}
        checks["activation_sha"] = act.get("sha256") == ACTIVATION_SHA
        checks["activation_primary_v1r"] = (
            roles.get("primary") == V1R_ROLE
            and roles.get("strategy") == PRIMARY_STRATEGY
        )
        checks["activation_pbv2_shadow"] = roles.get("pbv2") == PBV2_ROLE
        checks["activation_1m_shadow"] = roles.get("capital_1m") == ONE_M_ROLE
    except Exception:
        checks["activation_sha"] = False
        checks["activation_primary_v1r"] = False
        checks["activation_pbv2_shadow"] = False
        checks["activation_1m_shadow"] = False
    return checks


def build_identity() -> dict[str, Any]:
    return {
        "primary_strategy": PRIMARY_STRATEGY,
        "primary_role": V1R_ROLE,
        "v1r_role": V1R_ROLE,
        "pbv2_role": PBV2_ROLE,
        "one_m_role": ONE_M_ROLE,
        "strategy_sha": V1R_SHA,
        "model_sha": MODEL_ARTIFACT_SHA,
        "universe_binding_sha": UNIVERSE_BINDING_SHA,
        "universe_contract": UNIVERSE_CONTRACT,
        "precommit_sha": PRECOMMIT_U1_SHA,
        "activation_sha": ACTIVATION_SHA,
        "anchor_sha": ANCHOR_SHA,
        "cap": POSITION_CAP,
        "qty": LOT_QTY,
        "wait_sec": WAIT_SEC,
        "freshness_sec": BOARD_FRESHNESS_SEC_V1R,
        "hold_sec": EXIT_HOLD_SEC,
        "exit_contract": EXIT_CONTRACT,
        "duplicate_rule": DUPLICATE_RULE,
        "anchor_count": len(CLOCK_GRID),
        "anchors": [f"{h:02d}:{m:02d}" for h, m in CLOCK_GRID],
        "live_trading_enabled": False,
        "order_enabled": False,
        "paper_only": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
    }


def format_startup_contract(*, ready: bool, reason: str = "") -> str:
    ident = build_identity()
    lines = [
        "[V1R STARTUP CONTRACT]",
        "",
        "Primary:",
        f"V1R {ident['primary_role']}",
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
        "Anchors:",
        str(ident["anchor_count"]),
        "",
        "Cap:",
        str(ident["cap"]),
        "",
        "Wait:",
        f"{ident['wait_sec']}s",
        "",
        "Exit:",
        "FIXED600",
        "",
        "submit/cancel/live:",
        f"{ident['submit']}/{ident['cancel']}/{ident['live']}",
        "",
        "READY:",
        "YES" if ready else f"NO ({reason or ASSERTION_FAIL})",
    ]
    return "\n".join(lines)


def assert_v1r_primary_roles(
    *,
    forbid_pbv2_primary_fallback: bool = True,
    allow_missing_entry_webhook: bool = True,
) -> RoleAssertionResult:
    """Pre-market / pre-first-anchor identity check. Fail-closed."""
    checks: dict[str, bool] = {}
    identity = build_identity()

    checks["primary_strategy"] = identity["primary_strategy"] == PRIMARY_STRATEGY
    checks["v1r_role"] = identity["v1r_role"] == V1R_ROLE
    checks["pbv2_role"] = identity["pbv2_role"] == PBV2_ROLE
    checks["one_m_role"] = identity["one_m_role"] == ONE_M_ROLE
    checks["cap"] = identity["cap"] == 5
    checks["qty"] = identity["qty"] == 100
    checks["wait"] = float(identity["wait_sec"]) == 1.0
    checks["freshness"] = float(identity["freshness_sec"]) == 5.0
    checks["hold"] = float(identity["hold_sec"]) == 600.0
    checks["anchor_count"] = identity["anchor_count"] == 16
    checks["live_flags_off"] = (not identity["live_trading_enabled"]) and (not identity["order_enabled"])
    checks["paper_only"] = bool(identity["paper_only"])
    checks["no_pbv2_primary_fallback"] = bool(forbid_pbv2_primary_fallback)

    freeze = _verify_freeze_files()
    checks.update({f"freeze_{k}": v for k, v in freeze.items()})

    try:
        eff = resolve_v1r_effective_from_production()
        iso = assert_v1r_not_contaminated(eff)
        checks["yaml_isolation"] = bool(iso.get("pass"))
        checks["pin_match"] = bool(eff.pin_match)
        checks["eff_primary_v1r"] = eff.primary_role == "V1R"
        checks["eff_pbv2_shadow"] = eff.pbv2_role == PBV2_ROLE
        identity["yaml_sha256"] = eff.yaml_sha256
        identity["pin_match"] = eff.pin_match
    except Exception as exc:
        checks["yaml_isolation"] = False
        checks["pin_match"] = False
        checks["eff_primary_v1r"] = False
        checks["eff_pbv2_shadow"] = False
        identity["resolve_error"] = str(exc)

    # Entry webhook missing = notification fail-soft only (not strategy fallback)
    try:
        from notify.v1r_discord_routing import v1r_entry_webhook_missing
        missing = v1r_entry_webhook_missing()
        identity["v1r_entry_webhook_missing"] = missing
        checks["entry_webhook_or_failsoft"] = True if allow_missing_entry_webhook else (not missing)
    except Exception:
        identity["v1r_entry_webhook_missing"] = None
        checks["entry_webhook_or_failsoft"] = True

    failed = [k for k, v in checks.items() if not v]
    ok = len(failed) == 0
    reason = "" if ok else f"{ASSERTION_FAIL}:{','.join(failed[:8])}"
    block = format_startup_contract(ready=ok, reason=reason)
    return RoleAssertionResult(
        ok=ok,
        reason=reason,
        checks=checks,
        identity=identity,
        startup_block=block,
        ready=ok,
    )


def heartbeat_identity_fields(
    *,
    current_anchor: Optional[str] = None,
    next_anchor: Optional[str] = None,
    open_n: int = 0,
    pending_n: int = 0,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Fields that must appear on every V1R Primary heartbeat."""
    ident = build_identity()
    out = {
        "ts": datetime.now(JST).isoformat(),
        "primary_strategy": ident["primary_strategy"],
        "primary_role": ident["primary_role"],
        "strategy_sha": ident["strategy_sha"],
        "model_sha": ident["model_sha"],
        "universe_binding": ident["universe_binding_sha"],
        "universe_contract": ident["universe_contract"],
        "current_anchor": current_anchor,
        "next_anchor": next_anchor,
        "open": open_n,
        "pending": pending_n,
        "cap": ident["cap"],
        "pbv2_role": ident["pbv2_role"],
        "one_m_role": ident["one_m_role"],
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "paper_only": True,
        "order_enabled": False,
        "live_trading_enabled": False,
    }
    if extra:
        out.update(extra)
    return out
