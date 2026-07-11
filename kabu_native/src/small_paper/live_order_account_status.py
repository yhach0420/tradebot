"""Phase687W4 — Account / readonly status taxonomy for Kabu live reads."""

from __future__ import annotations

from enum import Enum


class AccountReadStatus(str, Enum):
    ONLINE_VALID = "ONLINE_VALID"
    ONLINE_ZERO_BALANCE = "ONLINE_ZERO_BALANCE"
    ONLINE_NO_POSITIONS = "ONLINE_NO_POSITIONS"
    ONLINE_NO_ORDERS = "ONLINE_NO_ORDERS"
    OFFLINE = "OFFLINE"
    AUTH_FAILED = "AUTH_FAILED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_REQUEST_FAILED = "TOKEN_REQUEST_FAILED"
    CLIENT_NOT_CONFIGURED = "CLIENT_NOT_CONFIGURED"
    TIMEOUT = "TIMEOUT"
    RESPONSE_INVALID = "RESPONSE_INVALID"
    ENDPOINT_NOT_SUPPORTED = "ENDPOINT_NOT_SUPPORTED"
    ENDPOINT_UNAVAILABLE = "ENDPOINT_UNAVAILABLE"
    KABU_STATION_NOT_RUNNING = "KABU_STATION_NOT_RUNNING"
    MARKET_CLOSED_READ_AVAILABLE = "MARKET_CLOSED_READ_AVAILABLE"
    MARKET_CLOSED_READ_UNAVAILABLE = "MARKET_CLOSED_READ_UNAVAILABLE"
    READONLY_API_WEEKEND_UNAVAILABLE = "READONLY_API_WEEKEND_UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


# Statuses that may feed Dry-run capital precheck with real values
CAPITAL_PRECHECK_ALLOWED = frozenset(
    {
        AccountReadStatus.ONLINE_VALID,
        AccountReadStatus.ONLINE_NO_POSITIONS,
        AccountReadStatus.ONLINE_NO_ORDERS,
        AccountReadStatus.MARKET_CLOSED_READ_AVAILABLE,
    }
)

# Explicit non-zero / non-failure empty states (must NOT be treated as API failure)
EMPTY_BUT_ONLINE = frozenset(
    {
        AccountReadStatus.ONLINE_ZERO_BALANCE,
        AccountReadStatus.ONLINE_NO_POSITIONS,
        AccountReadStatus.ONLINE_NO_ORDERS,
    }
)

API_FAILURE_STATUSES = frozenset(
    {
        AccountReadStatus.OFFLINE,
        AccountReadStatus.AUTH_FAILED,
        AccountReadStatus.TOKEN_EXPIRED,
        AccountReadStatus.TOKEN_REQUEST_FAILED,
        AccountReadStatus.CLIENT_NOT_CONFIGURED,
        AccountReadStatus.TIMEOUT,
        AccountReadStatus.RESPONSE_INVALID,
        AccountReadStatus.ENDPOINT_NOT_SUPPORTED,
        AccountReadStatus.ENDPOINT_UNAVAILABLE,
        AccountReadStatus.KABU_STATION_NOT_RUNNING,
        AccountReadStatus.MARKET_CLOSED_READ_UNAVAILABLE,
        AccountReadStatus.READONLY_API_WEEKEND_UNAVAILABLE,
        AccountReadStatus.UNKNOWN,
    }
)
