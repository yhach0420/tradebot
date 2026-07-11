"""Phase687W5B/B1 — Live account margin capability profile (read-only).

Provenance hardening (W5B1):
  - Fixture / synthetic / config NEVER become VERIFIED_FROM_LIVE_*.
  - Strings containing "live_shaped" are NOT live.
  - VERIFIED_FROM_LIVE_POSITION requires LIVE_API_POSITION_RESPONSE + full evidence.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

CAPABILITY_SCHEMA_VERSION = "687W5B1.1"

MARGIN_TRADE_SYSTEM = 1
MARGIN_TRADE_GENERAL = 2
MARGIN_TRADE_DAY = 3

# Default max age for live position responses to count as verified
DEFAULT_MAX_RESPONSE_AGE_SEC = 300.0


class CapabilityProvenance(str, Enum):
    LIVE_API_ACCOUNT_RESPONSE = "LIVE_API_ACCOUNT_RESPONSE"
    LIVE_API_POSITION_RESPONSE = "LIVE_API_POSITION_RESPONSE"
    LIVE_API_ORDER_RESPONSE = "LIVE_API_ORDER_RESPONSE"
    CONFIG = "CONFIG"
    FIXTURE = "FIXTURE"
    SYNTHETIC = "SYNTHETIC"
    UNKNOWN = "UNKNOWN"


class CapabilityStatus(str, Enum):
    VERIFIED_FROM_LIVE_POSITION = "VERIFIED_FROM_LIVE_POSITION"
    VERIFIED_FROM_LIVE_ACCOUNT_RESPONSE = "VERIFIED_FROM_LIVE_ACCOUNT_RESPONSE"
    CONFIG_ONLY = "CONFIG_ONLY"
    FIXTURE_ONLY = "FIXTURE_ONLY"
    SYNTHETIC_ONLY = "SYNTHETIC_ONLY"
    NOT_VERIFIED = "NOT_VERIFIED"
    CONFLICT = "CONFLICT"
    LIVE_API_NO_POSITIONS = "LIVE_API_NO_POSITIONS"
    UNKNOWN = "UNKNOWN"


class MarginTradeTypeStatus(str, Enum):
    VERIFIED_FROM_LIVE_POSITION = "VERIFIED_FROM_LIVE_POSITION"
    VERIFIED_FROM_LIVE_ACCOUNT_RESPONSE = "VERIFIED_FROM_LIVE_ACCOUNT_RESPONSE"
    NOT_VERIFIED = "NOT_VERIFIED"
    CONFIG_ONLY = "CONFIG_ONLY"
    CONFLICT = "CONFLICT"
    UNKNOWN = "UNKNOWN"


def normalize_provenance(raw: str | CapabilityProvenance | None) -> str:
    """Map caller strings to provenance. Never promote fixture/live_shaped to LIVE_*."""
    if raw is None:
        return CapabilityProvenance.UNKNOWN.value
    if isinstance(raw, CapabilityProvenance):
        return raw.value
    s = str(raw).strip()
    if not s:
        return CapabilityProvenance.UNKNOWN.value
    # Exact enum values first
    for p in CapabilityProvenance:
        if s == p.value:
            return p.value
    low = s.lower()
    # Explicit non-live markers (including historical W5B "fixture_live_shaped_*")
    if "fixture" in low or "live_shaped" in low:
        return CapabilityProvenance.FIXTURE.value
    if "synthetic" in low:
        return CapabilityProvenance.SYNTHETIC.value
    if low in ("config", "wiring_default", "wiring") or "config" in low:
        return CapabilityProvenance.CONFIG.value
    if low in ("live_positions", "live_api_position_response", "soak_readonly_refresh"):
        # soak_readonly_refresh alone is not enough — caller must pass evidence flags
        if "soak" in low:
            return CapabilityProvenance.UNKNOWN.value  # require explicit LIVE_API_* + evidence
        return CapabilityProvenance.LIVE_API_POSITION_RESPONSE.value
    if "live_account" in low or low == "live_api_account_response":
        return CapabilityProvenance.LIVE_API_ACCOUNT_RESPONSE.value
    if "live_order" in low:
        return CapabilityProvenance.LIVE_API_ORDER_RESPONSE.value
    return CapabilityProvenance.UNKNOWN.value


@dataclass
class LiveVerificationEvidence:
    """Required evidence for LIVE verification promotion."""

    provenance: str = CapabilityProvenance.UNKNOWN.value
    token_acquired: bool = False
    positions_endpoint_ok: bool = False
    account_endpoint_ok: bool = False
    response_timestamp: str = ""
    fixture_used: bool = False
    synthetic_used: bool = False
    schema_validation_pass: bool = False
    max_age_sec: float = DEFAULT_MAX_RESPONSE_AGE_SEC
    now: Optional[datetime] = None

    def response_age_sec(self) -> Optional[float]:
        if not self.response_timestamp:
            return None
        try:
            ts = datetime.fromisoformat(self.response_timestamp.replace("Z", "+00:00"))
            now = self.now or datetime.now(JST)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=JST)
            return max(0.0, (now - ts).total_seconds())
        except (TypeError, ValueError):
            return None

    def is_stale(self) -> bool:
        age = self.response_age_sec()
        if age is None:
            return True
        return age > float(self.max_age_sec)


def lot_has_required_live_fields(lot: Mapping[str, Any]) -> bool:
    """MarginTradeType, Exchange, AccountType must be present in the response lot."""
    for key in ("margin_trade_type", "exchange", "account_type"):
        if lot.get(key) is None:
            # also accept kabusapi raw keys if not yet normalized
            raw_map = {
                "margin_trade_type": "MarginTradeType",
                "exchange": "Exchange",
                "account_type": "AccountType",
            }
            if lot.get(raw_map[key]) is None:
                return False
    return True


def can_verify_from_live_position(
    *,
    evidence: LiveVerificationEvidence,
    position_lots: Sequence[Mapping[str, Any]],
) -> tuple[bool, str]:
    """All conditions must pass for VERIFIED_FROM_LIVE_POSITION."""
    prov = normalize_provenance(evidence.provenance)
    if prov != CapabilityProvenance.LIVE_API_POSITION_RESPONSE.value:
        return False, f"provenance_not_live_position:{prov}"
    if evidence.fixture_used:
        return False, "fixture_used"
    if evidence.synthetic_used:
        return False, "synthetic_used"
    if not evidence.token_acquired:
        return False, "token_not_acquired"
    if not evidence.positions_endpoint_ok:
        return False, "positions_endpoint_not_ok"
    if not evidence.response_timestamp:
        return False, "missing_response_timestamp"
    if evidence.is_stale():
        return False, "stale_live_response"
    if not evidence.schema_validation_pass:
        return False, "schema_validation_failed"
    if not position_lots:
        return False, "zero_positions"
    for i, lot in enumerate(position_lots):
        if not lot_has_required_live_fields(lot):
            return False, f"lot_missing_required_fields:{i}"
        lot_prov = normalize_provenance(lot.get("provenance") or prov)
        if lot_prov == CapabilityProvenance.FIXTURE.value:
            return False, "fixture_lot_in_live_set"
        if lot_prov == CapabilityProvenance.SYNTHETIC.value:
            return False, "synthetic_lot_in_live_set"
    return True, ""


@dataclass
class AccountCapabilityProfile:
    account_status: str = "UNKNOWN"
    account_type: Optional[int] = None
    margin_account_readable: bool = False
    margin_buying_power_present: bool = False
    cash_buying_power_present: bool = False
    supported_margin_trade_types_observed: list[int] = field(default_factory=list)
    observed_position_margin_trade_types: list[int] = field(default_factory=list)
    observed_position_exchanges: list[int] = field(default_factory=list)
    observed_position_account_types: list[int] = field(default_factory=list)
    au_money_connect_status_known: bool = False
    capability_source: str = "UNKNOWN"  # legacy alias
    capability_provenance: str = CapabilityProvenance.UNKNOWN.value
    capability_status: str = CapabilityStatus.UNKNOWN.value
    margin_trade_type_status: str = MarginTradeTypeStatus.NOT_VERIFIED.value
    verification_confidence: str = "low"
    verified_at: str = ""
    verification_failure_reason: str = ""
    fixture_used: bool = False
    synthetic_used: bool = False
    live_account_response_received: bool = False
    live_position_response_received: bool = False
    live_position_count: int = 0
    margin_trade_type_live_verified: bool = False
    exchange_live_verified: bool = False
    hold_id_live_verified: bool = False
    verified_response_time: str = ""
    wiring_default_margin_trade_type: int = MARGIN_TRADE_DAY
    wiring_default_treated_as_verified: bool = False
    request_valid_for_submit: bool = False
    production_authorized: bool = False
    no_secrets: bool = True
    schema_version: str = CAPABILITY_SCHEMA_VERSION
    notes: list[str] = field(default_factory=list)

    def to_safe_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["no_secrets"] = True
        d["request_valid_for_submit"] = False
        d["production_authorized"] = False
        d["wiring_default_treated_as_verified"] = False
        return d


def _uniq_ints(values: Sequence[Any]) -> list[int]:
    out: list[int] = []
    for v in values:
        try:
            iv = int(v)
        except (TypeError, ValueError):
            continue
        if iv not in out:
            out.append(iv)
    return sorted(out)


def _detect_mixed_provenance(lots: Sequence[Mapping[str, Any]], default_prov: str) -> bool:
    seen = set()
    for lot in lots:
        seen.add(normalize_provenance(lot.get("provenance") or default_prov))
    live = {CapabilityProvenance.LIVE_API_POSITION_RESPONSE.value}
    non_live = {
        CapabilityProvenance.FIXTURE.value,
        CapabilityProvenance.SYNTHETIC.value,
        CapabilityProvenance.CONFIG.value,
    }
    return bool(seen & live) and bool(seen & non_live)


def build_account_capability_profile(
    *,
    account_status: str = "UNKNOWN",
    cash_buying_power: Optional[float] = None,
    margin_buying_power: Optional[float] = None,
    position_lots: Optional[Sequence[Mapping[str, Any]]] = None,
    capability_source: str = "UNKNOWN",
    capability_provenance: str | CapabilityProvenance | None = None,
    account_type_observed: Optional[int] = None,
    au_money_connect_status_known: bool = False,
    fixture_only: bool = False,
    evidence: Optional[LiveVerificationEvidence] = None,
) -> AccountCapabilityProfile:
    """Build capability profile with strict provenance (W5B1)."""
    lots = list(position_lots or [])
    mtts = _uniq_ints(
        p.get("margin_trade_type", p.get("MarginTradeType"))
        for p in lots
        if p.get("margin_trade_type", p.get("MarginTradeType")) is not None
    )
    exchanges = _uniq_ints(
        p.get("exchange", p.get("Exchange"))
        for p in lots
        if p.get("exchange", p.get("Exchange")) is not None
    )
    acct_types = _uniq_ints(
        p.get("account_type", p.get("AccountType"))
        for p in lots
        if p.get("account_type", p.get("AccountType")) is not None
    )

    # Resolve provenance: explicit arg > evidence > capability_source (normalized)
    if capability_provenance is not None:
        prov = normalize_provenance(capability_provenance)
    elif evidence is not None:
        prov = normalize_provenance(evidence.provenance)
    else:
        prov = normalize_provenance(capability_source)

    if fixture_only:
        prov = CapabilityProvenance.FIXTURE.value

    ev = evidence or LiveVerificationEvidence(provenance=prov)
    # Align evidence provenance with resolved
    if normalize_provenance(ev.provenance) == CapabilityProvenance.UNKNOWN.value:
        ev = LiveVerificationEvidence(
            provenance=prov,
            token_acquired=ev.token_acquired,
            positions_endpoint_ok=ev.positions_endpoint_ok,
            account_endpoint_ok=ev.account_endpoint_ok,
            response_timestamp=ev.response_timestamp,
            fixture_used=ev.fixture_used or (prov == CapabilityProvenance.FIXTURE.value),
            synthetic_used=ev.synthetic_used or (prov == CapabilityProvenance.SYNTHETIC.value),
            schema_validation_pass=ev.schema_validation_pass,
            max_age_sec=ev.max_age_sec,
            now=ev.now,
        )
    else:
        # Force fixture/synthetic flags from provenance
        if prov == CapabilityProvenance.FIXTURE.value:
            ev.fixture_used = True
        if prov == CapabilityProvenance.SYNTHETIC.value:
            ev.synthetic_used = True

    profile = AccountCapabilityProfile(
        account_status=str(account_status or "UNKNOWN"),
        account_type=account_type_observed
        if account_type_observed is not None
        else (acct_types[0] if len(acct_types) == 1 else None),
        margin_account_readable=margin_buying_power is not None,
        margin_buying_power_present=margin_buying_power is not None and float(margin_buying_power) >= 0,
        cash_buying_power_present=cash_buying_power is not None and float(cash_buying_power) >= 0,
        supported_margin_trade_types_observed=list(mtts),
        observed_position_margin_trade_types=list(mtts),
        observed_position_exchanges=list(exchanges),
        observed_position_account_types=list(acct_types),
        au_money_connect_status_known=bool(au_money_connect_status_known),
        capability_source=str(capability_source or prov),
        capability_provenance=prov,
        verified_at=datetime.now(JST).isoformat(timespec="seconds"),
        fixture_used=bool(ev.fixture_used) or prov == CapabilityProvenance.FIXTURE.value,
        synthetic_used=bool(ev.synthetic_used) or prov == CapabilityProvenance.SYNTHETIC.value,
        live_account_response_received=bool(ev.account_endpoint_ok and ev.token_acquired),
        live_position_response_received=bool(ev.positions_endpoint_ok and ev.token_acquired),
        live_position_count=len(lots) if prov == CapabilityProvenance.LIVE_API_POSITION_RESPONSE.value else 0,
        verified_response_time=ev.response_timestamp or "",
        wiring_default_margin_trade_type=MARGIN_TRADE_DAY,
        wiring_default_treated_as_verified=False,
        request_valid_for_submit=False,
        production_authorized=False,
    )

    # Mixed fixture/live → CONFLICT
    if _detect_mixed_provenance(lots, prov):
        profile.capability_status = CapabilityStatus.CONFLICT.value
        profile.margin_trade_type_status = MarginTradeTypeStatus.CONFLICT.value
        profile.verification_confidence = "none"
        profile.verification_failure_reason = "fixture_live_mixed"
        profile.notes.append("fixture/live mixed provenance → CONFLICT; not policy evidence")
        return profile

    # FIXTURE
    if prov == CapabilityProvenance.FIXTURE.value or ev.fixture_used:
        profile.capability_status = CapabilityStatus.FIXTURE_ONLY.value
        profile.margin_trade_type_status = MarginTradeTypeStatus.NOT_VERIFIED.value
        profile.verification_confidence = "low"
        profile.verification_failure_reason = "fixture_provenance"
        profile.notes.append("fixture must not be used as live verification or policy evidence")
        profile.live_position_count = 0
        return profile

    # SYNTHETIC
    if prov == CapabilityProvenance.SYNTHETIC.value or ev.synthetic_used:
        profile.capability_status = CapabilityStatus.SYNTHETIC_ONLY.value
        profile.margin_trade_type_status = MarginTradeTypeStatus.NOT_VERIFIED.value
        profile.verification_confidence = "none"
        profile.verification_failure_reason = "synthetic_provenance"
        profile.live_position_count = 0
        return profile

    # CONFIG
    if prov == CapabilityProvenance.CONFIG.value:
        profile.capability_status = CapabilityStatus.CONFIG_ONLY.value
        profile.margin_trade_type_status = MarginTradeTypeStatus.CONFIG_ONLY.value
        profile.verification_confidence = "none"
        profile.verification_failure_reason = "config_only"
        profile.notes.append("MarginTradeType=3 wiring default is NOT VERIFIED")
        return profile

    # LIVE account path (no position verification)
    if prov == CapabilityProvenance.LIVE_API_ACCOUNT_RESPONSE.value:
        if ev.token_acquired and ev.account_endpoint_ok and not ev.fixture_used:
            profile.capability_status = CapabilityStatus.VERIFIED_FROM_LIVE_ACCOUNT_RESPONSE.value
            profile.margin_trade_type_status = MarginTradeTypeStatus.NOT_VERIFIED.value
            profile.verification_confidence = "medium"
            profile.notes.append("account live ok; MTT not verified from positions")
        else:
            profile.capability_status = CapabilityStatus.NOT_VERIFIED.value
            profile.margin_trade_type_status = MarginTradeTypeStatus.NOT_VERIFIED.value
            profile.verification_failure_reason = "live_account_evidence_incomplete"
        return profile

    # LIVE position path
    if prov == CapabilityProvenance.LIVE_API_POSITION_RESPONSE.value:
        profile.live_position_count = len(lots)
        if ev.positions_endpoint_ok and ev.token_acquired and len(lots) == 0:
            profile.capability_status = CapabilityStatus.LIVE_API_NO_POSITIONS.value
            profile.margin_trade_type_status = MarginTradeTypeStatus.NOT_VERIFIED.value
            profile.verification_confidence = "medium"
            profile.verification_failure_reason = "live_api_success_zero_positions"
            profile.notes.append("positions endpoint ok but no lots → MTT cannot be live-verified")
            return profile

        ok, reason = can_verify_from_live_position(evidence=ev, position_lots=lots)
        if ok:
            profile.capability_status = CapabilityStatus.VERIFIED_FROM_LIVE_POSITION.value
            profile.margin_trade_type_status = MarginTradeTypeStatus.VERIFIED_FROM_LIVE_POSITION.value
            profile.verification_confidence = "high"
            profile.margin_trade_type_live_verified = True
            profile.exchange_live_verified = True
            profile.hold_id_live_verified = all(
                bool(p.get("masked_hold_id") or p.get("raw_hold_id") or p.get("ExecutionID") or p.get("HoldID"))
                for p in lots
            )
            profile.verification_failure_reason = ""
            return profile

        profile.capability_status = CapabilityStatus.NOT_VERIFIED.value
        profile.margin_trade_type_status = MarginTradeTypeStatus.NOT_VERIFIED.value
        profile.verification_confidence = "low"
        profile.verification_failure_reason = reason
        profile.notes.append(f"live_position_verification_failed:{reason}")
        return profile

    # UNKNOWN / other
    profile.capability_status = CapabilityStatus.NOT_VERIFIED.value
    profile.margin_trade_type_status = MarginTradeTypeStatus.NOT_VERIFIED.value
    profile.verification_confidence = "low"
    profile.verification_failure_reason = f"provenance_unknown_or_unsupported:{prov}"
    profile.notes.append("provenance unknown → not policy evidence; production forbidden")
    return profile


def margin_trade_type_matrix_rows(profile: AccountCapabilityProfile) -> list[dict[str, Any]]:
    rows = []
    for mtt, label in (
        (1, "system_credit"),
        (2, "general_credit_long"),
        (3, "general_credit_daytrade"),
    ):
        observed = mtt in profile.observed_position_margin_trade_types
        live_verified = (
            observed
            and profile.margin_trade_type_status
            == MarginTradeTypeStatus.VERIFIED_FROM_LIVE_POSITION.value
            and profile.capability_provenance
            == CapabilityProvenance.LIVE_API_POSITION_RESPONSE.value
        )
        rows.append(
            {
                "margin_trade_type": mtt,
                "label": label,
                "observed_on_lots": observed,
                "provenance": profile.capability_provenance,
                "account_usable_known": False,
                "symbol_usable_known": False,
                "entry_new_usable_known": False,
                "exit_repay_value_source": "broker_position_only" if live_verified else "NOT_VERIFIED",
                "wiring_default": mtt == MARGIN_TRADE_DAY,
                "treated_as_verified": live_verified,
                "request_valid_for_submit": False,
            }
        )
    return rows


def entry_margin_trade_type_for_submit(
    *,
    profile: AccountCapabilityProfile,
    symbol_mtt_from_api: Optional[int] = None,
) -> tuple[Optional[int], str, bool]:
    """Return (mtt, status, request_valid_for_submit). Submit always false."""
    if symbol_mtt_from_api is not None:
        return int(symbol_mtt_from_api), MarginTradeTypeStatus.VERIFIED_FROM_LIVE_ACCOUNT_RESPONSE.value, False
    if profile.margin_trade_type_status == MarginTradeTypeStatus.VERIFIED_FROM_LIVE_POSITION.value:
        return None, MarginTradeTypeStatus.NOT_VERIFIED.value, False
    return None, MarginTradeTypeStatus.NOT_VERIFIED.value, False


def exit_margin_trade_type_from_position(
    position_lot: Mapping[str, Any],
) -> tuple[Optional[int], str]:
    """EXIT MTT from broker lot. Fixture/synthetic lots are NOT_VERIFIED."""
    prov = normalize_provenance(position_lot.get("provenance"))
    if prov in (
        CapabilityProvenance.FIXTURE.value,
        CapabilityProvenance.SYNTHETIC.value,
        CapabilityProvenance.CONFIG.value,
    ):
        return None, MarginTradeTypeStatus.NOT_VERIFIED.value
    raw = position_lot.get("margin_trade_type", position_lot.get("MarginTradeType"))
    if raw is None:
        return None, MarginTradeTypeStatus.NOT_VERIFIED.value
    try:
        mtt = int(raw)
    except (TypeError, ValueError):
        return None, MarginTradeTypeStatus.UNKNOWN.value
    if prov == CapabilityProvenance.LIVE_API_POSITION_RESPONSE.value:
        return mtt, MarginTradeTypeStatus.VERIFIED_FROM_LIVE_POSITION.value
    # Observed value without live provenance → return value but NOT_VERIFIED status
    return mtt, MarginTradeTypeStatus.NOT_VERIFIED.value


def provenance_matrix_rows() -> list[dict[str, Any]]:
    return [
        {
            "provenance": p.value,
            "may_become_verified_from_live_position": p
            == CapabilityProvenance.LIVE_API_POSITION_RESPONSE,
            "policy_evidence_allowed": False,
            "request_valid_for_submit": False,
        }
        for p in CapabilityProvenance
    ]


def soak_provenance_fields(profile: AccountCapabilityProfile) -> dict[str, Any]:
    """Fields to merge into W4S soak snapshot (no secrets)."""
    return {
        "capability_provenance": profile.capability_provenance,
        "fixture_used": profile.fixture_used,
        "synthetic_used": profile.synthetic_used,
        "live_account_response_received": profile.live_account_response_received,
        "live_position_response_received": profile.live_position_response_received,
        "live_position_count": profile.live_position_count,
        "margin_trade_type_live_verified": profile.margin_trade_type_live_verified,
        "exchange_live_verified": profile.exchange_live_verified,
        "hold_id_live_verified": profile.hold_id_live_verified,
        "verified_response_time": profile.verified_response_time,
        "verification_failure_reason": profile.verification_failure_reason,
        "account_capability_status": profile.capability_status,
        "margin_trade_type_status": profile.margin_trade_type_status,
        "request_valid_for_submit": False,
        "production_authorized": False,
    }
