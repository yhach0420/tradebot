"""Phase687W6 — Production enablement governance gate (fail-closed, no write adapter).

Evaluates machine-readable blockers for future production order enablement.
Does NOT enable live trading, order submit, or any write adapter.
Boolean defaults are False; unset / unknown / missing evidence → BLOCKED.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

SCHEMA_VERSION = "687W6.1"
PRODUCTION_ORDER_ENABLEMENT = "NOT_AUTHORIZED / NOT_IMPLEMENTED"
W4S_MIN_SESSIONS = 3
W4S_REQUIRED_VERDICT = "READONLY_SOAK_READY"

# Exit codes for check_production_enablement_readiness CLI
EXIT_TECH_PASS_NOT_AUTHORIZED = 0
EXIT_SOAK_INSUFFICIENT = 2
EXIT_CAPABILITY_POLICY = 3
EXIT_RECON_SAFETY = 4
EXIT_DESIGN_CONFIG = 5


class ApprovalStatus(str, Enum):
    NOT_AUTHORIZED = "NOT_AUTHORIZED"
    APPROVED = "APPROVED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    MISSING = "MISSING"


class BlockerCategory(str, Enum):
    SOAK = "soak"
    CAPABILITY_POLICY = "capability_policy"
    RECON_SAFETY = "reconciliation_safety"
    DESIGN_CONFIG = "design_config"
    APPROVAL = "approval"


@dataclass(frozen=True)
class Blocker:
    code: str
    category: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {"code": self.code, "category": self.category, "message": self.message}


@dataclass
class ProductionEnablementEvidence:
    """All fields fail-closed: unset / False / empty / negative sentinel → block.

    Do not default any enabling boolean to True.
    """

    # Soak / W4S
    w4s_session_count: Optional[int] = None
    w4s_verdict: Optional[str] = None
    readonly_success_sessions: Optional[int] = None

    # Integrity / safety counters (None = unknown → block)
    mapping_loss: Optional[int] = None
    duplicate_intent_created: Optional[int] = None
    reservation_leak: Optional[int] = None
    actual_submit_count: Optional[int] = None
    actual_cancel_count: Optional[int] = None
    unexplained_reconciliation_mismatch: Optional[int] = None

    # Latency / safety drills
    latency_p95_computed: bool = False
    safety_sm_sla_pass: bool = False
    journal_restore_pass: bool = False
    kill_switch_drill_pass: bool = False
    readonly_api_available: bool = False
    reconciliation_complete: bool = False

    # Provenance / capability
    live_api_provenance_confirmed: bool = False
    capability_status: Optional[str] = None
    capability_provenance: Optional[str] = None
    margin_trade_type_live_verified: bool = False
    verification_stale: bool = False  # True → block

    # Policy explicit approvals (future; default unselected)
    entry_exchange_policy_explicitly_approved: bool = False
    entry_exit_order_style_explicitly_approved: bool = False
    exit_exact_hold_id_close_confirmed: bool = False
    execution_policy_selected: bool = False
    approved_execution_policy_id: Optional[str] = None
    approved_exchange_policy: Optional[str] = None
    approved_close_policy: Optional[str] = None

    # Design / config
    config_sha256: Optional[str] = None
    expected_config_sha256: Optional[str] = None
    design_consistency_pass: bool = False
    documentation_review_pass: bool = False

    # Approval artifact
    approval_present: bool = False
    approval_status: Optional[str] = None
    approval_expires_at: Optional[str] = None
    approval_id: Optional[str] = None

    # Write adapter invariants (must remain fail)
    write_adapter_present: bool = False
    submit_hard_fail: bool = False  # must be True to pass safety

    # Optional notes
    notes: list[str] = field(default_factory=list)


def _parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt
    except ValueError:
        return None


def _is_missing_int(value: Optional[int]) -> bool:
    return value is None


def _is_nonzero_or_missing(value: Optional[int]) -> bool:
    return value is None or value != 0


def _fixture_or_synthetic(status: Optional[str], provenance: Optional[str]) -> bool:
    s = (status or "").upper()
    p = (provenance or "").upper()
    if "FIXTURE" in s or "SYNTHETIC" in s:
        return True
    if p in {"FIXTURE", "SYNTHETIC", "UNKNOWN", ""}:
        return True
    if p is None or status is None:
        return True
    return False


def evaluate_production_enablement(
    evidence: ProductionEnablementEvidence,
    *,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Fail-closed evaluation. Never enables orders."""
    now = now or datetime.now(JST)
    blockers: list[Blocker] = []

    # --- Soak ---
    if _is_missing_int(evidence.w4s_session_count) or (evidence.w4s_session_count or 0) < W4S_MIN_SESSIONS:
        blockers.append(
            Blocker(
                "W4S_SESSIONS_INSUFFICIENT",
                BlockerCategory.SOAK.value,
                f"w4s_session_count must be >= {W4S_MIN_SESSIONS} (got {evidence.w4s_session_count!r})",
            )
        )
    if not evidence.w4s_verdict or evidence.w4s_verdict != W4S_REQUIRED_VERDICT:
        blockers.append(
            Blocker(
                "W4S_VERDICT_NOT_READY",
                BlockerCategory.SOAK.value,
                f"w4s_verdict must be {W4S_REQUIRED_VERDICT} (got {evidence.w4s_verdict!r})",
            )
        )
    if _is_missing_int(evidence.readonly_success_sessions) or (evidence.readonly_success_sessions or 0) < 1:
        blockers.append(
            Blocker(
                "READONLY_SUCCESS_SESSIONS_INSUFFICIENT",
                BlockerCategory.SOAK.value,
                f"readonly_success_sessions must be >= 1 (got {evidence.readonly_success_sessions!r})",
            )
        )

    # --- Integrity / recon / safety ---
    if _is_nonzero_or_missing(evidence.mapping_loss):
        blockers.append(
            Blocker("MAPPING_LOSS_NONZERO_OR_UNKNOWN", BlockerCategory.RECON_SAFETY.value, "mapping_loss must be 0")
        )
    if _is_nonzero_or_missing(evidence.duplicate_intent_created):
        blockers.append(
            Blocker(
                "DUPLICATE_INTENT_CREATED",
                BlockerCategory.RECON_SAFETY.value,
                "duplicate_intent_created must be 0",
            )
        )
    if _is_nonzero_or_missing(evidence.reservation_leak):
        blockers.append(
            Blocker("RESERVATION_LEAK", BlockerCategory.RECON_SAFETY.value, "reservation_leak must be 0")
        )
    if _is_nonzero_or_missing(evidence.actual_submit_count):
        blockers.append(
            Blocker("ACTUAL_SUBMIT_NONZERO_OR_UNKNOWN", BlockerCategory.RECON_SAFETY.value, "actual_submit must be 0")
        )
    if _is_nonzero_or_missing(evidence.actual_cancel_count):
        blockers.append(
            Blocker("ACTUAL_CANCEL_NONZERO_OR_UNKNOWN", BlockerCategory.RECON_SAFETY.value, "actual_cancel must be 0")
        )
    if _is_nonzero_or_missing(evidence.unexplained_reconciliation_mismatch):
        blockers.append(
            Blocker(
                "RECONCILIATION_MISMATCH",
                BlockerCategory.RECON_SAFETY.value,
                "unexplained_reconciliation_mismatch must be 0",
            )
        )
    if not evidence.latency_p95_computed:
        blockers.append(
            Blocker("LATENCY_P95_NOT_COMPUTED", BlockerCategory.RECON_SAFETY.value, "latency p95 not computed")
        )
    if not evidence.safety_sm_sla_pass:
        blockers.append(Blocker("SAFETY_SM_SLA_FAIL", BlockerCategory.RECON_SAFETY.value, "SafetySM SLA not PASS"))
    if not evidence.journal_restore_pass:
        blockers.append(
            Blocker("JOURNAL_RESTORE_FAIL", BlockerCategory.RECON_SAFETY.value, "journal restore not PASS")
        )
    if not evidence.kill_switch_drill_pass:
        blockers.append(
            Blocker("KILL_SWITCH_DRILL_FAIL", BlockerCategory.RECON_SAFETY.value, "kill switch drill not PASS")
        )
    if not evidence.readonly_api_available:
        blockers.append(
            Blocker("READONLY_API_UNAVAILABLE", BlockerCategory.RECON_SAFETY.value, "read-only API unavailable")
        )
    if not evidence.reconciliation_complete:
        blockers.append(
            Blocker("RECONCILIATION_INCOMPLETE", BlockerCategory.RECON_SAFETY.value, "reconciliation incomplete")
        )
    if not evidence.submit_hard_fail:
        blockers.append(
            Blocker(
                "SUBMIT_HARD_FAIL_MISSING",
                BlockerCategory.RECON_SAFETY.value,
                "submit HARD_FAIL invariant not confirmed",
            )
        )
    if evidence.write_adapter_present:
        blockers.append(
            Blocker(
                "WRITE_ADAPTER_PRESENT",
                BlockerCategory.RECON_SAFETY.value,
                "production write adapter must not be present",
            )
        )

    # --- Capability / policy ---
    if not evidence.live_api_provenance_confirmed:
        blockers.append(
            Blocker(
                "LIVE_API_PROVENANCE_UNCONFIRMED",
                BlockerCategory.CAPABILITY_POLICY.value,
                "live API provenance not confirmed",
            )
        )
    if _fixture_or_synthetic(evidence.capability_status, evidence.capability_provenance):
        blockers.append(
            Blocker(
                "CAPABILITY_FIXTURE_OR_SYNTHETIC_OR_UNKNOWN",
                BlockerCategory.CAPABILITY_POLICY.value,
                f"capability_status={evidence.capability_status!r} provenance={evidence.capability_provenance!r}",
            )
        )
    if not evidence.margin_trade_type_live_verified:
        blockers.append(
            Blocker(
                "MARGIN_TRADE_TYPE_NOT_LIVE_VERIFIED",
                BlockerCategory.CAPABILITY_POLICY.value,
                "MarginTradeType not live-verified (includes zero-position case)",
            )
        )
    if evidence.verification_stale:
        blockers.append(
            Blocker("VERIFICATION_STALE", BlockerCategory.CAPABILITY_POLICY.value, "verification is stale")
        )
    if not evidence.entry_exchange_policy_explicitly_approved:
        blockers.append(
            Blocker(
                "ENTRY_EXCHANGE_POLICY_NOT_APPROVED",
                BlockerCategory.CAPABILITY_POLICY.value,
                "ENTRY Exchange Policy not explicitly approved",
            )
        )
    if not evidence.entry_exit_order_style_explicitly_approved:
        blockers.append(
            Blocker(
                "ORDER_STYLE_NOT_APPROVED",
                BlockerCategory.CAPABILITY_POLICY.value,
                "ENTRY/EXIT order style not explicitly approved",
            )
        )
    if not evidence.exit_exact_hold_id_close_confirmed:
        blockers.append(
            Blocker(
                "EXIT_EXACT_HOLD_ID_NOT_CONFIRMED",
                BlockerCategory.CAPABILITY_POLICY.value,
                "EXIT exact HoldID close not confirmed",
            )
        )
    if not evidence.execution_policy_selected:
        blockers.append(
            Blocker(
                "EXECUTION_POLICY_NOT_SELECTED",
                BlockerCategory.CAPABILITY_POLICY.value,
                "execution policy not selected",
            )
        )
    pol = (evidence.approved_execution_policy_id or "").upper()
    if not pol or pol in {"", "NOT_SELECTED", "PRODUCTION_FORBIDDEN", "UNKNOWN"}:
        blockers.append(
            Blocker(
                "EXECUTION_POLICY_ID_INVALID",
                BlockerCategory.CAPABILITY_POLICY.value,
                f"approved_execution_policy_id={evidence.approved_execution_policy_id!r}",
            )
        )

    # --- Design / config ---
    if not evidence.config_sha256 or not evidence.expected_config_sha256:
        blockers.append(
            Blocker("CONFIG_SHA_MISSING", BlockerCategory.DESIGN_CONFIG.value, "config SHA missing")
        )
    elif evidence.config_sha256 != evidence.expected_config_sha256:
        blockers.append(
            Blocker("CONFIG_SHA_MISMATCH", BlockerCategory.DESIGN_CONFIG.value, "config SHA mismatch")
        )
    if not evidence.design_consistency_pass:
        blockers.append(
            Blocker("DESIGN_CONSISTENCY_FAIL", BlockerCategory.DESIGN_CONFIG.value, "design consistency not PASS")
        )
    if not evidence.documentation_review_pass:
        blockers.append(
            Blocker("DOCUMENTATION_REVIEW_FAIL", BlockerCategory.DESIGN_CONFIG.value, "documentation review not PASS")
        )

    # --- Approval ---
    if not evidence.approval_present or not evidence.approval_status:
        blockers.append(
            Blocker("APPROVAL_MISSING", BlockerCategory.APPROVAL.value, "operator approval artifact missing")
        )
    else:
        status = evidence.approval_status
        if status == ApprovalStatus.EXPIRED.value:
            blockers.append(Blocker("APPROVAL_EXPIRED", BlockerCategory.APPROVAL.value, "approval expired"))
        elif status == ApprovalStatus.REVOKED.value:
            blockers.append(Blocker("APPROVAL_REVOKED", BlockerCategory.APPROVAL.value, "approval revoked"))
        elif status == ApprovalStatus.MISSING.value:
            blockers.append(Blocker("APPROVAL_MISSING", BlockerCategory.APPROVAL.value, "approval status MISSING"))
        elif status == ApprovalStatus.NOT_AUTHORIZED.value:
            blockers.append(
                Blocker(
                    "APPROVAL_NOT_AUTHORIZED",
                    BlockerCategory.APPROVAL.value,
                    "approval_status=NOT_AUTHORIZED (expected until explicit future authorization)",
                )
            )
        elif status != ApprovalStatus.APPROVED.value:
            blockers.append(
                Blocker(
                    "APPROVAL_STATUS_UNKNOWN",
                    BlockerCategory.APPROVAL.value,
                    f"approval_status={status!r}",
                )
            )
        else:
            exp = _parse_dt(evidence.approval_expires_at)
            if exp is None:
                blockers.append(
                    Blocker("APPROVAL_EXPIRES_AT_MISSING", BlockerCategory.APPROVAL.value, "expires_at missing")
                )
            elif exp <= now:
                blockers.append(Blocker("APPROVAL_EXPIRED", BlockerCategory.APPROVAL.value, "approval past expires_at"))

    by_cat = {
        BlockerCategory.SOAK.value: [],
        BlockerCategory.CAPABILITY_POLICY.value: [],
        BlockerCategory.RECON_SAFETY.value: [],
        BlockerCategory.DESIGN_CONFIG.value: [],
        BlockerCategory.APPROVAL.value: [],
    }
    for b in blockers:
        by_cat.setdefault(b.category, []).append(b)

    tech_blockers = [
        b
        for b in blockers
        if b.category != BlockerCategory.APPROVAL.value
        or b.code
        not in {
            "APPROVAL_NOT_AUTHORIZED",
        }
    ]
    # For exit-0: all non-approval blockers clear, and approval is present as NOT_AUTHORIZED only
    only_not_authorized = (
        len(tech_blockers) == 0
        and len(blockers) == 1
        and blockers[0].code == "APPROVAL_NOT_AUTHORIZED"
    )
    all_clear_including_approved = len(blockers) == 0

    # production_ready requires zero blockers AND approved — never true while NOT_AUTHORIZED
    # Also never true while write adapter absent path is the only path (this phase).
    production_ready = False
    if all_clear_including_approved and evidence.approval_status == ApprovalStatus.APPROVED.value:
        # Future path: still refuse while write adapter must remain absent / HARD_FAIL
        production_ready = False

    exit_code = _exit_code(by_cat, only_not_authorized=only_not_authorized)

    return {
        "schema_version": SCHEMA_VERSION,
        "production_order_enablement": PRODUCTION_ORDER_ENABLEMENT,
        "blocker_count": len(blockers),
        "blockers": [b.to_dict() for b in blockers],
        "blockers_by_category": {k: [b.to_dict() for b in v] for k, v in by_cat.items()},
        "soak_status": "PASS" if not by_cat[BlockerCategory.SOAK.value] else "BLOCKED",
        "provenance_status": (
            "PASS"
            if evidence.live_api_provenance_confirmed
            and not _fixture_or_synthetic(evidence.capability_status, evidence.capability_provenance)
            and evidence.margin_trade_type_live_verified
            and not evidence.verification_stale
            else "BLOCKED"
        ),
        "capability_status": evidence.capability_status or "UNKNOWN",
        "policy_status": (
            "PASS"
            if evidence.execution_policy_selected
            and evidence.entry_exchange_policy_explicitly_approved
            and evidence.entry_exit_order_style_explicitly_approved
            and evidence.exit_exact_hold_id_close_confirmed
            else "BLOCKED"
        ),
        "reconciliation_status": (
            "PASS" if not by_cat[BlockerCategory.RECON_SAFETY.value] else "BLOCKED"
        ),
        "latency_status": "PASS" if evidence.latency_p95_computed else "BLOCKED",
        "approval_status": evidence.approval_status or ApprovalStatus.MISSING.value,
        "production_ready": production_ready,
        "write_adapter_present": bool(evidence.write_adapter_present),
        "submit_hard_fail": bool(evidence.submit_hard_fail),
        "exit_code": exit_code,
        "technical_conditions_pass": only_not_authorized or all_clear_including_approved,
        "evaluated_at": now.isoformat(timespec="seconds"),
        "notes": list(evidence.notes),
        "live_trading_enabled": False,
        "order_enabled": False,
        "canary_execution_forbidden": True,
    }


def _exit_code(by_cat: Mapping[str, list[Blocker]], *, only_not_authorized: bool) -> int:
    """Priority: soak(2) → capability/policy/approval-non-NOT_AUTH(3) → recon(4) → design(5) → 0."""
    if only_not_authorized:
        return EXIT_TECH_PASS_NOT_AUTHORIZED
    if by_cat.get(BlockerCategory.SOAK.value):
        return EXIT_SOAK_INSUFFICIENT
    approval_hard = [
        b
        for b in by_cat.get(BlockerCategory.APPROVAL.value, [])
        if b.code != "APPROVAL_NOT_AUTHORIZED"
    ]
    if by_cat.get(BlockerCategory.CAPABILITY_POLICY.value) or approval_hard:
        return EXIT_CAPABILITY_POLICY
    if by_cat.get(BlockerCategory.RECON_SAFETY.value):
        return EXIT_RECON_SAFETY
    if by_cat.get(BlockerCategory.DESIGN_CONFIG.value):
        return EXIT_DESIGN_CONFIG
    if by_cat.get(BlockerCategory.APPROVAL.value):
        # only APPROVAL_NOT_AUTHORIZED remaining
        return EXIT_TECH_PASS_NOT_AUTHORIZED
    return EXIT_TECH_PASS_NOT_AUTHORIZED


def empty_evidence() -> ProductionEnablementEvidence:
    """Fully unset evidence — must evaluate to BLOCKED."""
    return ProductionEnablementEvidence()


def sample_approval_artifact_not_authorized(
    *,
    git_commit: str = "UNSET",
    config_sha256: str = "UNSET",
    design_schema_version: str = SCHEMA_VERSION,
) -> dict[str, Any]:
    """Schema sample only. Never generates APPROVED status in this phase."""
    return {
        "approval_id": "SAMPLE-NOT-AUTHORIZED",
        "approved_by": "NONE",
        "approved_at": "",
        "expires_at": "",
        "git_commit": git_commit,
        "config_sha256": config_sha256,
        "design_schema_version": design_schema_version,
        "approved_execution_policy_id": "NOT_SELECTED",
        "approved_exchange_policy": "NOT_SELECTED",
        "approved_close_policy": "NOT_SELECTED",
        "max_order_count": 0,
        "max_quantity": 0,
        "max_notional_yen": 0,
        "single_session_only": True,
        "approval_status": ApprovalStatus.NOT_AUTHORIZED.value,
        "secrets_present": False,
        "signing_keys_present": False,
        "note": "Phase687W6 sample only — not a production authorization",
    }


def canary_plan_schema() -> dict[str, Any]:
    """Future first live trade canary structure. Execution forbidden in this phase."""
    return {
        "schema_version": SCHEMA_VERSION,
        "canary_execution_forbidden": True,
        "canary_status": "NOT_AUTHORIZED / NOT_IMPLEMENTED",
        "constraints": {
            "max_orders": 1,
            "max_quantity": 100,
            "max_notional_yen": None,
            "new_entry_only": True,
            "max_concurrent_positions": 1,
            "same_intent_resubmit_forbidden": True,
            "explicit_operator_confirmation_required": True,
            "immediate_kill_switch_required": True,
            "auto_expire_at_session_end": True,
            "exit_path_verified_before_entry": True,
            "broker_position_reconcile_before_next_step": True,
        },
        "note": "Structure only — canary must not run in Phase687W6",
    }


def approval_schema_fields() -> list[str]:
    return [
        "approval_id",
        "approved_by",
        "approved_at",
        "expires_at",
        "git_commit",
        "config_sha256",
        "design_schema_version",
        "approved_execution_policy_id",
        "approved_exchange_policy",
        "approved_close_policy",
        "max_order_count",
        "max_quantity",
        "max_notional_yen",
        "single_session_only",
        "approval_status",
    ]


def blocker_matrix_rows() -> list[dict[str, Any]]:
    """Documentation matrix of required PASS conditions."""
    rows = [
        ("W4S_SESSIONS_INSUFFICIENT", "soak", f"W4S sessions >= {W4S_MIN_SESSIONS}"),
        ("W4S_VERDICT_NOT_READY", "soak", f"verdict = {W4S_REQUIRED_VERDICT}"),
        ("READONLY_SUCCESS_SESSIONS_INSUFFICIENT", "soak", "readonly success sessions >= 1"),
        ("MAPPING_LOSS_NONZERO_OR_UNKNOWN", "reconciliation_safety", "mapping loss = 0"),
        ("DUPLICATE_INTENT_CREATED", "reconciliation_safety", "duplicate intent created = 0"),
        ("RESERVATION_LEAK", "reconciliation_safety", "reservation leak = 0"),
        ("ACTUAL_SUBMIT_NONZERO_OR_UNKNOWN", "reconciliation_safety", "actual submit = 0"),
        ("ACTUAL_CANCEL_NONZERO_OR_UNKNOWN", "reconciliation_safety", "actual cancel = 0"),
        ("RECONCILIATION_MISMATCH", "reconciliation_safety", "unexplained recon mismatch = 0"),
        ("LATENCY_P95_NOT_COMPUTED", "reconciliation_safety", "latency p95 computed"),
        ("SAFETY_SM_SLA_FAIL", "reconciliation_safety", "SafetySM SLA PASS"),
        ("JOURNAL_RESTORE_FAIL", "reconciliation_safety", "journal restore PASS"),
        ("KILL_SWITCH_DRILL_FAIL", "reconciliation_safety", "kill switch drill PASS"),
        ("READONLY_API_UNAVAILABLE", "reconciliation_safety", "read-only API available"),
        ("RECONCILIATION_INCOMPLETE", "reconciliation_safety", "reconciliation complete"),
        ("LIVE_API_PROVENANCE_UNCONFIRMED", "capability_policy", "live API provenance confirmed"),
        ("CAPABILITY_FIXTURE_OR_SYNTHETIC_OR_UNKNOWN", "capability_policy", "capability not FIXTURE/SYNTHETIC"),
        ("MARGIN_TRADE_TYPE_NOT_LIVE_VERIFIED", "capability_policy", "MarginTradeType live verified"),
        ("VERIFICATION_STALE", "capability_policy", "verification not stale"),
        ("ENTRY_EXCHANGE_POLICY_NOT_APPROVED", "capability_policy", "ENTRY Exchange Policy approved"),
        ("ORDER_STYLE_NOT_APPROVED", "capability_policy", "ENTRY/EXIT order style approved"),
        ("EXIT_EXACT_HOLD_ID_NOT_CONFIRMED", "capability_policy", "EXIT exact HoldID close confirmed"),
        ("EXECUTION_POLICY_NOT_SELECTED", "capability_policy", "execution policy selected"),
        ("CONFIG_SHA_MISMATCH", "design_config", "config SHA match"),
        ("DESIGN_CONSISTENCY_FAIL", "design_config", "design consistency PASS"),
        ("DOCUMENTATION_REVIEW_FAIL", "design_config", "documentation review PASS"),
        ("APPROVAL_MISSING", "approval", "operator approval artifact present"),
        ("APPROVAL_EXPIRED", "approval", "approval not expired"),
        ("APPROVAL_NOT_AUTHORIZED", "approval", "approval_status must be APPROVED for production_ready"),
        ("WRITE_ADAPTER_PRESENT", "reconciliation_safety", "write adapter absent"),
        ("SUBMIT_HARD_FAIL_MISSING", "reconciliation_safety", "submit HARD_FAIL confirmed"),
    ]
    return [
        {"blocker_code": c, "category": cat, "required_condition": cond, "fail_closed": True}
        for c, cat, cond in rows
    ]


def technical_pass_evidence_not_authorized() -> ProductionEnablementEvidence:
    """All technical gates green; approval present as NOT_AUTHORIZED (exit 0 path)."""
    return ProductionEnablementEvidence(
        w4s_session_count=3,
        w4s_verdict=W4S_REQUIRED_VERDICT,
        readonly_success_sessions=1,
        mapping_loss=0,
        duplicate_intent_created=0,
        reservation_leak=0,
        actual_submit_count=0,
        actual_cancel_count=0,
        unexplained_reconciliation_mismatch=0,
        latency_p95_computed=True,
        safety_sm_sla_pass=True,
        journal_restore_pass=True,
        kill_switch_drill_pass=True,
        readonly_api_available=True,
        reconciliation_complete=True,
        live_api_provenance_confirmed=True,
        capability_status="VERIFIED_FROM_LIVE_POSITION",
        capability_provenance="LIVE_API_POSITION_RESPONSE",
        margin_trade_type_live_verified=True,
        verification_stale=False,
        entry_exchange_policy_explicitly_approved=True,
        entry_exit_order_style_explicitly_approved=True,
        exit_exact_hold_id_close_confirmed=True,
        execution_policy_selected=True,
        approved_execution_policy_id="FUTURE_APPROVED_POLICY",
        approved_exchange_policy="SOR",
        approved_close_policy="CLOSE_EXACT_HOLD_ID",
        config_sha256="abc123",
        expected_config_sha256="abc123",
        design_consistency_pass=True,
        documentation_review_pass=True,
        approval_present=True,
        approval_status=ApprovalStatus.NOT_AUTHORIZED.value,
        approval_expires_at="",
        approval_id="SAMPLE-NOT-AUTHORIZED",
        write_adapter_present=False,
        submit_hard_fail=True,
        notes=["synthetic technical-pass evidence for gate tests only"],
    )


def probe_current_workspace(
    *,
    native_root: Optional[Path] = None,
    confirm_hard_fail: bool = True,
) -> dict[str, Any]:
    """Probe current workspace without enabling anything or calling write methods for submit.

    Uses fail-closed defaults for soak/capability (typically BLOCKED until Monday W4S Forward).
    Does not mutate config flags.
    """
    root = native_root or Path(__file__).resolve().parents[2]
    submit_hard_fail = False
    if confirm_hard_fail:
        from small_paper.live_order_safety_sm import KabuBrokerAdapter

        try:
            KabuBrokerAdapter().submit_entry_order({"symbol": "PROBE", "quantity": 1})
        except RuntimeError as exc:
            submit_hard_fail = "HARD_FAIL" in str(exc)

    design_path = (
        root
        / "results"
        / "reports"
        / "phase687w3_e2e_readonly_reconciliation"
        / "phase687w3_design_consistency.json"
    )
    design_ok = False
    if design_path.is_file():
        try:
            import json

            design_ok = bool(json.loads(design_path.read_text(encoding="utf-8")).get("pass"))
        except Exception:
            design_ok = False

    evidence = ProductionEnablementEvidence(
        # Soak unknown → block
        w4s_session_count=None,
        w4s_verdict=None,
        readonly_success_sessions=None,
        mapping_loss=None,
        duplicate_intent_created=None,
        reservation_leak=None,
        actual_submit_count=0,
        actual_cancel_count=0,
        unexplained_reconciliation_mismatch=None,
        latency_p95_computed=False,
        safety_sm_sla_pass=False,
        journal_restore_pass=False,
        kill_switch_drill_pass=False,
        readonly_api_available=False,
        reconciliation_complete=False,
        live_api_provenance_confirmed=False,
        capability_status="UNKNOWN",
        capability_provenance="UNKNOWN",
        margin_trade_type_live_verified=False,
        design_consistency_pass=design_ok,
        documentation_review_pass=False,
        approval_present=True,
        approval_status=ApprovalStatus.NOT_AUTHORIZED.value,
        approval_id="SAMPLE-NOT-AUTHORIZED",
        write_adapter_present=False,
        submit_hard_fail=submit_hard_fail,
        notes=["workspace probe — soak/capability not asserted; fail-closed"],
    )
    result = evaluate_production_enablement(evidence)
    result["probe_mode"] = "workspace_fail_closed"
    result["flags_mutated"] = False
    return result


def evidence_from_mapping(data: Mapping[str, Any]) -> ProductionEnablementEvidence:
    """Build evidence from a mapping; unknown keys ignored; missing keys keep fail-closed defaults."""
    known = {f.name for f in ProductionEnablementEvidence.__dataclass_fields__.values()}  # type: ignore[attr-defined]
    kwargs = {k: data[k] for k in data if k in known}
    return ProductionEnablementEvidence(**kwargs)
