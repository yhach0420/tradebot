"""Phase687W5/W5A — Kabu Order Request Contract Builder (no network submit).

Converts validated OrderIntent + ExecutionPolicy → KabuOrderRequest payload.

Source-of-truth priority (W5A):
  1. Official kabusapi reference
  2. docs/live_trading/vendor/kabusapi_sendorder_contract.json
  3. live_order_api_wiring.py
  4. this builder
  5. test fixtures

This module holds NO HTTP client. Never calls submit/cancel/flatten.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional

from small_paper.kabu_order_execution_policy import (
    ExecutionPolicy,
    ExecutionPolicyId,
    OrderStyle,
    not_selected_policy,
)
from small_paper.kabu_sendorder_contract import (
    EXCHANGE_SOR,
    EXCHANGE_TSE,
    EXCHANGE_TSE_PLUS,
    NOT_IMPLEMENTED_FRONT_ORDER,
    ClosePositionMode,
    ExchangePolicy,
    FundTypeMode,
    TransactionType,
    apply_fund_type_for_margin,
    resolve_exchange_code,
    validate_close_position_xor,
    validate_new_order_exchange,
)
from small_paper.live_order_api_wiring import (
    ACCOUNT_TYPE_SPECIFIC,
    CASH_MARGIN_NEW,
    CASH_MARGIN_REPAY,
    FRONT_ORDER_LIMIT,
    FRONT_ORDER_MARKET,
    MARGIN_TRADE_DAY,
    MARGIN_TRADE_GENERAL,
    MARGIN_TRADE_SYSTEM,
    SECURITY_TYPE_STOCK,
    SIDE_BUY,
    SIDE_SELL,
    build_entry_sendorder_payload,
    build_exit_sendorder_payload,
    symbol_to_kabu_code,
)
from small_paper.live_order_dry_run_adapter import LOT_SIZE

BUILDER_VERSION = "687W5A.1"
REQUEST_SCHEMA_VERSION = "kabusapi-sendorder-v1-official-reconciled-687W5A"

KABU_API_FIELDS_ENTRY = (
    "Symbol",
    "Exchange",
    "SecurityType",
    "Side",
    "CashMargin",
    "MarginTradeType",
    "DelivType",
    "AccountType",
    "Qty",
    "FrontOrderType",
    "Price",
    "ExpireDay",
)
KABU_API_FIELDS_EXIT = KABU_API_FIELDS_ENTRY + ("ClosePositions", "ClosePositionOrder")

ALLOWED_SIDES = {SIDE_BUY, SIDE_SELL}
ALLOWED_EXCHANGES = {EXCHANGE_TSE, EXCHANGE_SOR, EXCHANGE_TSE_PLUS}
ALLOWED_FRONT_ORDER = {FRONT_ORDER_MARKET, FRONT_ORDER_LIMIT}
ALLOWED_CASH_MARGIN = {CASH_MARGIN_NEW, CASH_MARGIN_REPAY}
ALLOWED_MARGIN_TRADE = {MARGIN_TRADE_SYSTEM, MARGIN_TRADE_GENERAL, MARGIN_TRADE_DAY}
ALLOWED_ACCOUNT = {ACCOUNT_TYPE_SPECIFIC}
ALLOWED_SECURITY = {SECURITY_TYPE_STOCK}

FORBIDDEN_INTENT_KINDS = frozenset(
    {
        "shadow",
        "reject",
        "capacity_block",
        "debug",
        "notification_only",
        "virtual_position",
    }
)

REQUEST_MUTATION_DETECTED = "REQUEST_MUTATION_DETECTED"

_actual_broker_submit_count = 0
_actual_broker_cancel_count = 0
_network_call_count = 0


def actual_broker_submit_count() -> int:
    return _actual_broker_submit_count


def actual_broker_cancel_count() -> int:
    return _actual_broker_cancel_count


def network_call_count() -> int:
    return _network_call_count


@dataclass
class OrderIntentContract:
    """Intent layer: what to trade (not how)."""

    intent_id: str
    idempotency_key: str
    side: str
    symbol: str
    quantity: int
    position_id: str
    entry_or_exit: str
    limit_price: Optional[float] = None
    exit_reason: str = ""
    exchange: Optional[int] = None
    exchange_policy: str = ExchangePolicy.NOT_SELECTED.value
    open_position_exchange: Optional[int] = None
    transaction_type: str = ""
    hold_id: str = ""
    close_position_mode: str = ClosePositionMode.CLOSE_POSITION_ORDER.value
    close_position_order: int = 0
    margin_trade_type: int = MARGIN_TRADE_DAY
    margin_trade_type_source: str = "WIRING_DEFAULT_UNVERIFIED"
    expected_margin_trade_type: Optional[int] = None
    fund_type_mode: str = FundTypeMode.OMIT_AUTO_11.value
    account_status: str = "ONLINE_VALID"
    reconciliation_status: str = "MATCH"
    holding_qty: Optional[int] = None
    price_snapshot: Optional[float] = None
    board_age_sec: Optional[float] = None
    price_age_sec: Optional[float] = None
    capital_available: bool = True
    kill_switch: bool = False
    intent_kind: str = "actual"
    accepted: bool = True
    stale_price_threshold_sec: float = 5.0
    stale_board_threshold_sec: float = 5.0

    def resolved_transaction_type(self) -> str:
        if self.transaction_type:
            return self.transaction_type
        if self.entry_or_exit == "ENTRY":
            return TransactionType.MARGIN_NEW_BUY.value
        return TransactionType.MARGIN_REPAY_SELL.value

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BuildLatency:
    intent_to_policy_resolved_ms: float = 0.0
    policy_to_payload_built_ms: float = 0.0
    payload_validation_ms: float = 0.0
    fingerprint_ms: float = 0.0
    payload_total_ms: float = 0.0


@dataclass
class BuildResult:
    request_generated: bool = False
    request_valid: bool = False
    request_valid_for_submit: bool = False
    would_submit: bool = False
    final_state: str = "PRECHECK_REJECTED"
    recovery_action: str = "none"
    error_category: str = ""
    validation_errors: list[str] = field(default_factory=list)
    api_payload: dict[str, Any] = field(default_factory=dict)
    audit_payload: dict[str, Any] = field(default_factory=dict)
    masked_payload: dict[str, Any] = field(default_factory=dict)
    request_fingerprint: str = ""
    canonical_payload_hash: str = ""
    schema_version: str = REQUEST_SCHEMA_VERSION
    builder_version: str = BUILDER_VERSION
    policy: dict[str, Any] = field(default_factory=dict)
    exchange_policy: str = ExchangePolicy.NOT_SELECTED.value
    transaction_type: str = ""
    fund_type_audit: dict[str, Any] = field(default_factory=dict)
    latency: BuildLatency = field(default_factory=BuildLatency)
    actual_submit_count: int = 0
    actual_cancel_count: int = 0
    network_call_count: int = 0
    secret_leak: bool = False
    mutation_detected: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_finite_number(v: Any) -> bool:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return False
    return math.isfinite(f)


def _canonical_json(obj: Mapping[str, Any]) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def mask_payload_for_audit(payload: Mapping[str, Any]) -> dict[str, Any]:
    secret_keys = {
        "token",
        "password",
        "authorization",
        "api_password",
        "account_number",
        "accountnumber",
        "x-api-key",
    }
    out: dict[str, Any] = {}
    for k, v in payload.items():
        lk = str(k).lower()
        if any(s in lk for s in secret_keys):
            out[k] = "<REDACTED>"
            continue
        if isinstance(v, str) and any(x in v.lower() for x in ("bearer ", "password=", "token=")):
            out[k] = "<REDACTED>"
            continue
        if isinstance(v, dict):
            out[k] = mask_payload_for_audit(v)
        else:
            out[k] = v
    return out


def extract_api_payload(raw: Mapping[str, Any]) -> dict[str, Any]:
    meta = {
        "endpoint",
        "would_send",
        "dry_run",
        "client_order_id",
        "linked_paper_trade_id",
        "timeout_sec",
        "margin_type_label",
        "exit_reason",
        "order_type_label",
    }
    return {k: v for k, v in raw.items() if k not in meta}


def fingerprint_material(
    *,
    intent: OrderIntentContract,
    policy: ExecutionPolicy,
    api_payload: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "intent_id": intent.intent_id,
        "idempotency_key": intent.idempotency_key,
        "symbol": api_payload.get("Symbol") or symbol_to_kabu_code(intent.symbol),
        "side": api_payload.get("Side"),
        "quantity": api_payload.get("Qty"),
        "order_policy": policy.policy_id,
        "exchange_policy": intent.exchange_policy,
        "account_classification": api_payload.get("AccountType"),
        "CashMargin": api_payload.get("CashMargin"),
        "FrontOrderType": api_payload.get("FrontOrderType"),
        "Price": api_payload.get("Price"),
        "ExpireDay": api_payload.get("ExpireDay"),
        "Exchange": api_payload.get("Exchange"),
        "FundType": api_payload.get("FundType"),
        "position_id": intent.position_id,
        "ClosePositions": api_payload.get("ClosePositions"),
        "ClosePositionOrder": api_payload.get("ClosePositionOrder"),
        "schema_version": REQUEST_SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
    }


def compute_fingerprint(
    *,
    intent: OrderIntentContract,
    policy: ExecutionPolicy,
    api_payload: Mapping[str, Any],
) -> tuple[str, str]:
    material = fingerprint_material(intent=intent, policy=policy, api_payload=api_payload)
    canon = _canonical_json(material)
    fp = _sha256(canon)
    body_hash = _sha256(_canonical_json(dict(api_payload)))
    return fp, body_hash


class OrderRequestBuilder:
    """Intent → Policy → KabuOrderRequest. No HTTP client."""

    def __init__(self) -> None:
        self._fingerprint_by_key: dict[str, str] = {}
        self._payload_by_key: dict[str, dict[str, Any]] = {}

    def resolve_policy(
        self,
        intent: OrderIntentContract,
        policy: Optional[ExecutionPolicy] = None,
    ) -> ExecutionPolicy:
        if policy is None:
            return not_selected_policy(entry_or_exit=intent.entry_or_exit)
        if policy.production_authorized:
            return ExecutionPolicy(
                **{
                    **policy.to_dict(),
                    "production_authorized": False,
                    "policy_id": ExecutionPolicyId.PRODUCTION_FORBIDDEN.value,
                }
            )
        return policy

    def build(
        self,
        intent: OrderIntentContract,
        policy: Optional[ExecutionPolicy] = None,
    ) -> BuildResult:
        t0 = time.perf_counter()
        result = BuildResult(
            actual_submit_count=actual_broker_submit_count(),
            actual_cancel_count=actual_broker_cancel_count(),
            network_call_count=network_call_count(),
            exchange_policy=intent.exchange_policy,
            transaction_type=intent.resolved_transaction_type(),
        )

        t_pol0 = time.perf_counter()
        resolved = self.resolve_policy(intent, policy)
        result.policy = resolved.to_dict()
        result.latency.intent_to_policy_resolved_ms = (time.perf_counter() - t_pol0) * 1000.0

        tt = intent.resolved_transaction_type()
        if tt in (TransactionType.CASH_BUY.value, TransactionType.CASH_SELL.value):
            result.validation_errors = [f"transaction_type_NOT_IMPLEMENTED:{tt}"]
            result.error_category = "cash_order_NOT_IMPLEMENTED"
            result.request_valid = False
            result.request_valid_for_submit = False
            result.would_submit = False
            result.final_state = "PRECHECK_REJECTED"
            result.latency.payload_total_ms = (time.perf_counter() - t0) * 1000.0
            return result

        ex_code, ex_errs, ex_hint = resolve_exchange_code(
            exchange_policy=intent.exchange_policy,
            entry_or_exit=intent.entry_or_exit,
            open_position_exchange=intent.open_position_exchange,
        )
        if ex_errs:
            result.validation_errors = ex_errs
            result.error_category = ex_errs[0]
            result.request_valid = False
            result.request_valid_for_submit = False
            result.would_submit = False
            result.final_state = ex_hint or "PRECHECK_REJECTED"
            if "open_position_exchange_unknown" in ex_errs:
                result.recovery_action = "RECOVERY_REQUIRED"
            result.latency.payload_total_ms = (time.perf_counter() - t0) * 1000.0
            return result

        working_exchange = int(ex_code) if ex_code is not None else None
        if working_exchange is None:
            result.validation_errors = ["exchange_unresolved"]
            result.error_category = "exchange_unresolved"
            result.request_valid_for_submit = False
            result.final_state = "PRECHECK_REJECTED"
            result.latency.payload_total_ms = (time.perf_counter() - t0) * 1000.0
            return result

        pre_errors = self._precheck_intent(intent, resolved)
        if intent.entry_or_exit == "ENTRY":
            pre_errors.extend(
                validate_new_order_exchange(working_exchange, intent.exchange_policy)
            )
        if pre_errors:
            result.validation_errors = pre_errors
            result.error_category = pre_errors[0]
            result.request_valid = False
            result.request_valid_for_submit = False
            result.would_submit = False
            result.final_state = (
                "RECOVERY_REQUIRED"
                if any(
                    "MUTATION" in e or "RECONCILIATION" in e or "open_position" in e
                    for e in pre_errors
                )
                else "PRECHECK_REJECTED"
            )
            if REQUEST_MUTATION_DETECTED in pre_errors:
                result.mutation_detected = True
                result.recovery_action = "RECOVERY_REQUIRED"
                result.final_state = "RECOVERY_REQUIRED"
            result.latency.payload_total_ms = (time.perf_counter() - t0) * 1000.0
            return result

        if resolved.policy_id == ExecutionPolicyId.NOT_SELECTED.value:
            result.validation_errors = ["execution_policy_NOT_SELECTED"]
            result.error_category = "execution_policy_NOT_SELECTED"
            result.request_valid = False
            result.request_valid_for_submit = False
            result.would_submit = False
            result.final_state = "ORDER_INTENT_CREATED"
            result.latency.payload_total_ms = (time.perf_counter() - t0) * 1000.0
            return result

        t_build0 = time.perf_counter()
        try:
            raw = self._build_raw_payload(intent, resolved, working_exchange)
        except Exception as exc:
            result.validation_errors = [f"build_error:{type(exc).__name__}"]
            result.error_category = "build_error"
            result.final_state = "PRECHECK_REJECTED"
            result.latency.payload_total_ms = (time.perf_counter() - t0) * 1000.0
            return result
        result.latency.policy_to_payload_built_ms = (time.perf_counter() - t_build0) * 1000.0
        result.request_generated = True

        api = extract_api_payload(raw)
        api, fund_audit, fund_errs = apply_fund_type_for_margin(api, mode=intent.fund_type_mode)
        result.fund_type_audit = fund_audit.to_dict()
        if fund_errs:
            result.validation_errors = fund_errs
            result.error_category = fund_errs[0]
            result.request_valid = False
            result.request_valid_for_submit = False
            result.final_state = "PRECHECK_REJECTED"
            result.latency.payload_total_ms = (time.perf_counter() - t0) * 1000.0
            return result

        result.api_payload = api
        result.audit_payload = {
            "schema_version": REQUEST_SCHEMA_VERSION,
            "builder_version": BUILDER_VERSION,
            "field_names": sorted(api.keys()),
            "quantity": api.get("Qty"),
            "symbol": api.get("Symbol"),
            "side": api.get("Side"),
            "policy": resolved.policy_id,
            "exchange_policy": intent.exchange_policy,
            "transaction_type": tt,
            "fund_type_audit": result.fund_type_audit,
            "margin_trade_type_source": intent.margin_trade_type_source,
            "intent_id": intent.intent_id,
            "idempotency_key": intent.idempotency_key,
            "position_id": intent.position_id,
        }
        result.masked_payload = mask_payload_for_audit(
            {**api, "client_order_id": raw.get("client_order_id", "")}
        )

        t_val0 = time.perf_counter()
        val_errors = self._validate_api_payload(intent, resolved, api)
        result.latency.payload_validation_ms = (time.perf_counter() - t_val0) * 1000.0
        if val_errors:
            result.validation_errors = val_errors
            result.error_category = val_errors[0]
            result.request_valid = False
            result.request_valid_for_submit = False
            result.would_submit = False
            result.final_state = "PRECHECK_REJECTED"
            result.latency.payload_total_ms = (time.perf_counter() - t0) * 1000.0
            return result

        t_fp0 = time.perf_counter()
        fp, body_hash = compute_fingerprint(intent=intent, policy=resolved, api_payload=api)
        result.latency.fingerprint_ms = (time.perf_counter() - t_fp0) * 1000.0
        result.request_fingerprint = fp
        result.canonical_payload_hash = body_hash

        prev = self._fingerprint_by_key.get(intent.idempotency_key)
        if prev is not None and prev != fp:
            result.mutation_detected = True
            result.request_valid = False
            result.request_valid_for_submit = False
            result.would_submit = False
            result.error_category = REQUEST_MUTATION_DETECTED
            result.validation_errors = [REQUEST_MUTATION_DETECTED]
            result.final_state = "RECOVERY_REQUIRED"
            result.recovery_action = "RECOVERY_REQUIRED"
            result.latency.payload_total_ms = (time.perf_counter() - t0) * 1000.0
            return result

        if prev is not None and prev == fp:
            result.request_valid = True
            result.request_valid_for_submit = False
            result.would_submit = False
            result.final_state = "ORDER_INTENT_CREATED"
            result.recovery_action = "reuse_existing_request"
            result.latency.payload_total_ms = (time.perf_counter() - t0) * 1000.0
            return result

        self._fingerprint_by_key[intent.idempotency_key] = fp
        self._payload_by_key[intent.idempotency_key] = dict(api)

        result.request_valid = True
        result.request_valid_for_submit = False
        result.would_submit = False
        result.final_state = "ORDER_INTENT_CREATED"
        result.secret_leak = self._detect_secret_leak(result)
        result.latency.payload_total_ms = (time.perf_counter() - t0) * 1000.0
        return result

    def _precheck_intent(
        self, intent: OrderIntentContract, policy: ExecutionPolicy
    ) -> list[str]:
        errs: list[str] = []
        kind = str(intent.intent_kind or "").lower()
        if kind in FORBIDDEN_INTENT_KINDS:
            errs.append(f"forbidden_intent_kind:{kind}")
        if not intent.accepted:
            errs.append("intent_not_accepted")
        if not intent.intent_id:
            errs.append("missing_intent_id")
        if not intent.idempotency_key:
            errs.append("missing_idempotency_key")
        if not intent.position_id:
            errs.append("missing_position_id")
        if not intent.symbol or not str(intent.symbol).strip():
            errs.append("missing_symbol")
        if intent.kill_switch and intent.entry_or_exit == "ENTRY":
            errs.append("kill_switch_blocks_entry")
        if not intent.capital_available and intent.entry_or_exit == "ENTRY":
            errs.append("capital_unavailable")
        if intent.reconciliation_status != "MATCH":
            errs.append("reconciliation_mismatch")
        if intent.account_status not in (
            "ONLINE_VALID",
            "ONLINE_NO_POSITIONS",
            "ONLINE_NO_ORDERS",
            "MARKET_CLOSED_READ_AVAILABLE",
        ):
            if intent.entry_or_exit == "ENTRY":
                errs.append(f"account_status_not_online:{intent.account_status}")
        if intent.price_age_sec is not None and intent.price_age_sec > intent.stale_price_threshold_sec:
            errs.append("stale_price")
        if intent.board_age_sec is not None and intent.board_age_sec > intent.stale_board_threshold_sec:
            errs.append("stale_board")
        if intent.entry_or_exit == "ENTRY" and intent.price_snapshot is None and intent.limit_price is None:
            errs.append("missing_price_snapshot")
        if policy.production_authorized:
            errs.append("production_authorized_forbidden")
        if intent.entry_or_exit == "EXIT" and intent.expected_margin_trade_type is not None:
            if int(intent.margin_trade_type) != int(intent.expected_margin_trade_type):
                errs.append("MarginTradeType_mismatch_open_position")
        return errs

    def _build_raw_payload(
        self,
        intent: OrderIntentContract,
        policy: ExecutionPolicy,
        exchange: int,
    ) -> dict[str, Any]:
        if intent.entry_or_exit == "ENTRY":
            px = intent.limit_price if intent.limit_price is not None else intent.price_snapshot
            if px is None:
                raise ValueError("missing_limit_price")
            if policy.order_style == OrderStyle.MARKET.value and policy.allow_market:
                raw = build_entry_sendorder_payload(
                    symbol=intent.symbol,
                    exchange=exchange,
                    limit_price=float(px),
                    quantity=int(intent.quantity),
                    margin_trade_type=intent.margin_trade_type,
                    client_order_id=intent.idempotency_key,
                    linked_paper_trade_id=intent.position_id,
                )
                raw["FrontOrderType"] = FRONT_ORDER_MARKET
                raw["Price"] = 0.0
                return raw
            return build_entry_sendorder_payload(
                symbol=intent.symbol,
                exchange=exchange,
                limit_price=float(px),
                quantity=int(intent.quantity),
                margin_trade_type=intent.margin_trade_type,
                client_order_id=intent.idempotency_key,
                linked_paper_trade_id=intent.position_id,
            )

        exit_reason = intent.exit_reason or "session_close"
        if policy.order_style == OrderStyle.MARKET.value:
            exit_reason = "hard_stop"
        elif policy.order_style == OrderStyle.LIMIT.value:
            exit_reason = intent.exit_reason or "trailing_mfe_exit"

        hold_id = ""
        if intent.close_position_mode == ClosePositionMode.CLOSE_POSITIONS.value:
            hold_id = intent.hold_id or "MOCK-E00000000"
        raw = build_exit_sendorder_payload(
            symbol=intent.symbol,
            exchange=exchange,
            exit_reason=exit_reason,
            quantity=int(intent.quantity),
            limit_price=intent.limit_price,
            hold_id=hold_id,
            margin_trade_type=intent.margin_trade_type,
            client_order_id=intent.idempotency_key,
            linked_paper_trade_id=intent.position_id,
        )
        if intent.close_position_mode == ClosePositionMode.CLOSE_POSITION_ORDER.value:
            raw.pop("ClosePositions", None)
            raw["ClosePositionOrder"] = int(intent.close_position_order)
        elif intent.close_position_mode == ClosePositionMode.CLOSE_POSITIONS.value:
            raw.pop("ClosePositionOrder", None)
            if "ClosePositions" not in raw:
                raw["ClosePositions"] = [{"HoldID": hold_id, "Qty": int(intent.quantity)}]
        elif intent.close_position_mode == ClosePositionMode.BOTH_FORBIDDEN.value:
            raw["ClosePositions"] = [{"HoldID": "MOCK-E", "Qty": int(intent.quantity)}]
            raw["ClosePositionOrder"] = 0
        elif intent.close_position_mode == ClosePositionMode.NEITHER.value:
            raw.pop("ClosePositions", None)
            raw.pop("ClosePositionOrder", None)
        return raw

    def _validate_api_payload(
        self,
        intent: OrderIntentContract,
        policy: ExecutionPolicy,
        api: Mapping[str, Any],
    ) -> list[str]:
        errs: list[str] = []
        symbol = str(api.get("Symbol") or "")
        if not symbol:
            errs.append("missing_symbol")
        elif not symbol.isdigit() or len(symbol) < 3:
            errs.append("unknown_symbol_format")

        try:
            exchange = int(api.get("Exchange"))
        except (TypeError, ValueError):
            errs.append("unknown_exchange")
            exchange = -1
        if exchange not in ALLOWED_EXCHANGES:
            errs.append("unknown_exchange")
        if intent.entry_or_exit == "ENTRY":
            errs.extend(validate_new_order_exchange(exchange, intent.exchange_policy))
        if intent.entry_or_exit == "EXIT" and intent.open_position_exchange is not None:
            if exchange != int(intent.open_position_exchange):
                errs.append("repay_exchange_mismatch")

        side = str(api.get("Side") or "")
        if side not in ALLOWED_SIDES:
            errs.append("unknown_side")
        if intent.entry_or_exit == "ENTRY" and side != SIDE_BUY:
            errs.append("side_inversion")
        if intent.entry_or_exit == "EXIT" and side != SIDE_SELL:
            errs.append("side_inversion")

        try:
            cm = int(api.get("CashMargin"))
        except (TypeError, ValueError):
            errs.append("unknown_CashMargin")
            cm = -1
        if cm == 1:
            errs.append("cash_order_NOT_IMPLEMENTED")
        if intent.entry_or_exit == "ENTRY" and cm != CASH_MARGIN_NEW:
            errs.append("margin_new_CashMargin_mismatch")
        if intent.entry_or_exit == "EXIT" and cm != CASH_MARGIN_REPAY:
            errs.append("margin_repay_CashMargin_mismatch")

        if intent.entry_or_exit == "ENTRY":
            if int(api.get("DelivType", -1)) != 0:
                errs.append("margin_new_DelivType_must_be_0")
            if "ClosePositions" in api or "ClosePositionOrder" in api:
                errs.append("margin_new_must_not_have_close_fields")
        if intent.entry_or_exit == "EXIT":
            if int(api.get("DelivType", -1)) not in (2, 3):
                errs.append("margin_repay_DelivType_invalid")
            errs.extend(validate_close_position_xor(dict(api), required=True))

        qty = api.get("Qty")
        if qty is None:
            errs.append("missing_quantity")
        else:
            try:
                q = int(qty)
            except (TypeError, ValueError):
                errs.append("invalid_quantity")
                q = -1
            if isinstance(qty, float) and (math.isnan(qty) or math.isinf(qty)):
                errs.append("nan_or_infinity")
            if q < 0:
                errs.append("negative_quantity")
            if q == 0:
                errs.append("quantity_zero")
            if q > 0 and q < LOT_SIZE:
                errs.append("quantity_below_lot")
            if q > 0 and q % LOT_SIZE != 0:
                errs.append("quantity_not_lot_multiple")
            if intent.entry_or_exit == "ENTRY" and q > 0:
                if q != int(intent.quantity):
                    errs.append("entry_quantity_mismatch")
                if int(intent.quantity) < LOT_SIZE or int(intent.quantity) % LOT_SIZE != 0:
                    errs.append("entry_quantity_not_baseline_or_sized_lot")
            if intent.entry_or_exit == "EXIT":
                holding = intent.holding_qty
                if holding is None:
                    errs.append("broker_holding_unknown")
                elif q > int(holding):
                    errs.append("exit_quantity_exceeds_holding")

        for field_name, allowed in (
            ("CashMargin", ALLOWED_CASH_MARGIN),
            ("AccountType", ALLOWED_ACCOUNT),
            ("SecurityType", ALLOWED_SECURITY),
            ("MarginTradeType", ALLOWED_MARGIN_TRADE),
            ("FrontOrderType", ALLOWED_FRONT_ORDER),
        ):
            try:
                v = int(api.get(field_name))
            except (TypeError, ValueError):
                errs.append(f"unknown_{field_name}")
                continue
            if v not in allowed:
                if field_name == "FrontOrderType" and v in NOT_IMPLEMENTED_FRONT_ORDER:
                    errs.append("front_order_type_NOT_IMPLEMENTED")
                else:
                    errs.append(f"unknown_{field_name}")

        front = api.get("FrontOrderType")
        price = api.get("Price")
        if price is not None:
            try:
                pf = float(price)
                if math.isnan(pf) or math.isinf(pf):
                    errs.append("nan_or_infinity")
                if pf < 0:
                    errs.append("negative_limit_price")
            except (TypeError, ValueError):
                errs.append("invalid_price")

        if front == FRONT_ORDER_LIMIT:
            if price is None or not _is_finite_number(price) or float(price) <= 0:
                errs.append("limit_order_without_price")
        if front == FRONT_ORDER_MARKET:
            if price is not None and float(price) != 0.0:
                errs.append("market_order_with_invalid_price_field")

        if "ExpireDay" not in api:
            errs.append("missing_expiry")
        else:
            try:
                int(api["ExpireDay"])
            except (TypeError, ValueError):
                errs.append("missing_expiry")

        if "FundType" in api and api.get("FundType") not in (None, "11"):
            errs.append("fund_type_invalid_for_margin")

        for banned in ("submit_allowed", "order_enabled", "live_trading_enabled", "production_authorized"):
            if banned in api:
                errs.append(f"forbidden_flag_in_payload:{banned}")

        return errs

    def _detect_secret_leak(self, result: BuildResult) -> bool:
        blob = json.dumps(
            {"audit": result.audit_payload, "masked": result.masked_payload},
            ensure_ascii=False,
        ).lower()
        return any(n in blob for n in ("authorization: bearer", "password=", "api_password"))


def request_schema_document() -> dict[str, Any]:
    return {
        "schema_version": REQUEST_SCHEMA_VERSION,
        "builder_version": BUILDER_VERSION,
        "source_of_truth_priority": [
            "official_kabusapi_reference",
            "docs/live_trading/vendor/kabusapi_sendorder_contract.json",
            "src/small_paper/live_order_api_wiring.py",
            "OrderRequestBuilder",
            "test_fixtures",
        ],
        "official_snapshot": "docs/live_trading/vendor/kabusapi_sendorder_contract.json",
        "entry_required_fields": list(KABU_API_FIELDS_ENTRY),
        "exit_required_fields": list(KABU_API_FIELDS_ENTRY),
        "exit_close_one_of": ["ClosePositions", "ClosePositionOrder"],
        "enums": {
            "Side": {"BUY": SIDE_BUY, "SELL": SIDE_SELL},
            "CashMargin": {"NEW": CASH_MARGIN_NEW, "REPAY": CASH_MARGIN_REPAY},
            "FrontOrderType_supported": {"MARKET": FRONT_ORDER_MARKET, "LIMIT": FRONT_ORDER_LIMIT},
            "Exchange": {"TSE": EXCHANGE_TSE, "SOR": EXCHANGE_SOR, "TSE_PLUS": EXCHANGE_TSE_PLUS},
            "AccountType": {"SPECIFIC": ACCOUNT_TYPE_SPECIFIC},
            "SecurityType": {"STOCK": SECURITY_TYPE_STOCK},
            "MarginTradeType": {
                "SYSTEM": MARGIN_TRADE_SYSTEM,
                "GENERAL": MARGIN_TRADE_GENERAL,
                "DAY": MARGIN_TRADE_DAY,
            },
            "ExchangePolicy": [e.value for e in ExchangePolicy],
            "TransactionType": [e.value for e in TransactionType],
        },
        "lot_size": LOT_SIZE,
        "cash_orders": "NOT_IMPLEMENTED",
        "network_submit": "PRODUCTION_FORBIDDEN",
        "request_valid_for_submit_default": False,
        "mutation_error_code": REQUEST_MUTATION_DETECTED,
        "normal_new_exchange_tse": "FORBIDDEN",
        "fund_type_margin": "omit_or_11",
    }
