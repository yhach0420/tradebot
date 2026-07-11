"""Phase687W5 — Kabu order response parser (Mock / fixture only).

Never auto-resubmits on timeout.
Never stores real account order numbers into durable artifacts from live probes;
fixture broker ids are allowed for tests.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional


PARSER_VERSION = "687W5.1"


class ParsedResponseState(str, Enum):
    ACKNOWLEDGED = "ACKNOWLEDGED"
    BROKER_REJECTED = "BROKER_REJECTED"
    UNKNOWN = "UNKNOWN"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


class ResponseCategory(str, Enum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    VALIDATION_ERROR = "validation_error"
    AUTH_ERROR = "auth_error"
    INSUFFICIENT_BUYING_POWER = "insufficient_buying_power"
    INVALID_QUANTITY = "invalid_quantity"
    INVALID_PRICE = "invalid_price"
    DUPLICATE_REQUEST = "duplicate_request"
    TIMEOUT_BEFORE_RESPONSE = "timeout_before_response"
    MALFORMED_JSON = "malformed_json"
    UNKNOWN_RESPONSE = "unknown_response"
    EMPTY_RESPONSE = "empty_response"


# Fixture-only broker order id prefix (never treat live ids as durable SoT)
FIXTURE_ORDER_ID_PREFIX = "MOCK-"


@dataclass
class ParsedBrokerResponse:
    state: str
    category: str
    broker_order_id: str = ""
    result_code: Optional[int] = None
    message: str = ""
    auto_resubmit: bool = False
    reconciliation_required: bool = False
    raw_keys: list[str] = field(default_factory=list)
    parser_version: str = PARSER_VERSION
    secret_leak: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mask_secrets(text: str) -> str:
    low = text.lower()
    if any(x in low for x in ("password", "bearer ", "authorization", "api_password")):
        return "<REDACTED>"
    return text[:200]


class OrderResponseParser:
    """Parse mock/fixture broker responses. No network. No auto-resubmit."""

    def __init__(self) -> None:
        self._seen_response_ids: set[str] = set()
        self.auto_resubmit_count = 0

    def parse(
        self,
        response: Any,
        *,
        timed_out: bool = False,
        duplicate_of: Optional[str] = None,
    ) -> ParsedBrokerResponse:
        if timed_out:
            return ParsedBrokerResponse(
                state=ParsedResponseState.UNKNOWN.value,
                category=ResponseCategory.TIMEOUT_BEFORE_RESPONSE.value,
                auto_resubmit=False,
                reconciliation_required=True,
                message="timeout_before_response",
            )

        if response is None or response == "" or response == {}:
            return ParsedBrokerResponse(
                state=ParsedResponseState.UNKNOWN.value,
                category=ResponseCategory.EMPTY_RESPONSE.value,
                auto_resubmit=False,
                reconciliation_required=True,
                message="empty_response",
            )

        if isinstance(response, (bytes, bytearray)):
            try:
                response = response.decode("utf-8")
            except Exception:
                return ParsedBrokerResponse(
                    state=ParsedResponseState.UNKNOWN.value,
                    category=ResponseCategory.MALFORMED_JSON.value,
                    message="binary_decode_failed",
                    reconciliation_required=True,
                )

        if isinstance(response, str):
            text = response.strip()
            if not text:
                return ParsedBrokerResponse(
                    state=ParsedResponseState.UNKNOWN.value,
                    category=ResponseCategory.EMPTY_RESPONSE.value,
                    reconciliation_required=True,
                )
            try:
                response = json.loads(text)
            except json.JSONDecodeError:
                return ParsedBrokerResponse(
                    state=ParsedResponseState.UNKNOWN.value,
                    category=ResponseCategory.MALFORMED_JSON.value,
                    message="malformed_json",
                    reconciliation_required=True,
                    secret_leak=self._leak_check(text),
                )

        if not isinstance(response, Mapping):
            return ParsedBrokerResponse(
                state=ParsedResponseState.UNKNOWN.value,
                category=ResponseCategory.UNKNOWN_RESPONSE.value,
                message="non_object_response",
                reconciliation_required=True,
            )

        raw_keys = sorted(str(k) for k in response.keys())
        secret_leak = self._leak_check(json.dumps(dict(response), ensure_ascii=False))

        # Auth
        code = response.get("Code", response.get("code", response.get("Result")))
        msg = str(response.get("Message") or response.get("message") or response.get("Msg") or "")
        msg_l = msg.lower()
        http_status = response.get("http_status") or response.get("HttpStatus")

        if http_status in (401, 403) or "unauthorized" in msg_l or "auth" in msg_l and "fail" in msg_l:
            return ParsedBrokerResponse(
                state=ParsedResponseState.BROKER_REJECTED.value,
                category=ResponseCategory.AUTH_ERROR.value,
                result_code=int(code) if _is_int(code) else None,
                message=_mask_secrets(msg),
                raw_keys=raw_keys,
                secret_leak=secret_leak,
            )

        # Explicit timeout marker in fixture
        if response.get("timeout") is True or str(response.get("status") or "").upper() == "TIMEOUT":
            return ParsedBrokerResponse(
                state=ParsedResponseState.UNKNOWN.value,
                category=ResponseCategory.TIMEOUT_BEFORE_RESPONSE.value,
                auto_resubmit=False,
                reconciliation_required=True,
                raw_keys=raw_keys,
                secret_leak=secret_leak,
            )

        # kabusapi-style success: Result=0 and OrderId present
        order_id = str(
            response.get("OrderId")
            or response.get("orderId")
            or response.get("broker_order_id")
            or ""
        )
        result_ok = code in (0, "0", None) and (
            order_id
            or str(response.get("status") or "").upper() in ("OK", "ACCEPTED", "ACKNOWLEDGED")
            or response.get("accepted") is True
        )

        if duplicate_of or response.get("duplicate") is True:
            rid = order_id or duplicate_of or "dup"
            if rid in self._seen_response_ids or response.get("duplicate") is True:
                return ParsedBrokerResponse(
                    state=ParsedResponseState.ACKNOWLEDGED.value,
                    category=ResponseCategory.DUPLICATE_REQUEST.value,
                    broker_order_id=order_id if order_id.startswith(FIXTURE_ORDER_ID_PREFIX) else (
                        f"{FIXTURE_ORDER_ID_PREFIX}{order_id}" if order_id else ""
                    ),
                    message="duplicate_response",
                    auto_resubmit=False,
                    raw_keys=raw_keys,
                    secret_leak=secret_leak,
                )

        # Rejection taxonomy
        if code not in (0, "0", None) or response.get("rejected") is True:
            cat = ResponseCategory.REJECTED.value
            if "quantity" in msg_l or "qty" in msg_l:
                cat = ResponseCategory.INVALID_QUANTITY.value
            elif "price" in msg_l:
                cat = ResponseCategory.INVALID_PRICE.value
            elif "buying" in msg_l or "margin" in msg_l or "power" in msg_l or "余力" in msg:
                cat = ResponseCategory.INSUFFICIENT_BUYING_POWER.value
            elif "valid" in msg_l or "param" in msg_l:
                cat = ResponseCategory.VALIDATION_ERROR.value
            elif "duplicate" in msg_l:
                cat = ResponseCategory.DUPLICATE_REQUEST.value
            return ParsedBrokerResponse(
                state=ParsedResponseState.BROKER_REJECTED.value,
                category=cat,
                result_code=int(code) if _is_int(code) else None,
                message=_mask_secrets(msg),
                raw_keys=raw_keys,
                secret_leak=secret_leak,
                auto_resubmit=False,
            )

        if result_ok and order_id:
            safe_id = order_id
            if not safe_id.startswith(FIXTURE_ORDER_ID_PREFIX):
                # For fixtures without prefix, still accept but tag as mock-safe storage
                safe_id = f"{FIXTURE_ORDER_ID_PREFIX}{safe_id}"
            self._seen_response_ids.add(safe_id)
            return ParsedBrokerResponse(
                state=ParsedResponseState.ACKNOWLEDGED.value,
                category=ResponseCategory.ACCEPTED.value,
                broker_order_id=safe_id,
                result_code=0 if code in (0, "0", None) else (int(code) if _is_int(code) else None),
                message=_mask_secrets(msg) if msg else "accepted",
                raw_keys=raw_keys,
                secret_leak=secret_leak,
                auto_resubmit=False,
            )

        if result_ok and not order_id:
            return ParsedBrokerResponse(
                state=ParsedResponseState.UNKNOWN.value,
                category=ResponseCategory.UNKNOWN_RESPONSE.value,
                message="accepted_without_order_id",
                reconciliation_required=True,
                raw_keys=raw_keys,
                secret_leak=secret_leak,
            )

        return ParsedBrokerResponse(
            state=ParsedResponseState.UNKNOWN.value,
            category=ResponseCategory.UNKNOWN_RESPONSE.value,
            message=_mask_secrets(msg) or "unknown_response",
            reconciliation_required=True,
            raw_keys=raw_keys,
            secret_leak=secret_leak,
            auto_resubmit=False,
        )

    def note_timeout_no_resubmit(self) -> ParsedBrokerResponse:
        """Explicit: timeout → UNKNOWN + reconciliation; auto_resubmit stays 0."""
        assert self.auto_resubmit_count == 0
        return self.parse(None, timed_out=True)

    @staticmethod
    def _leak_check(text: str) -> bool:
        low = text.lower()
        return any(x in low for x in ("password=", "authorization: bearer", '"token": "'))


def _is_int(v: Any) -> bool:
    try:
        int(v)
        return True
    except (TypeError, ValueError):
        return False
