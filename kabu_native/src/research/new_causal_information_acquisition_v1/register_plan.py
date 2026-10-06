"""Exactly 50 registrations: 48 equity @1 + 2 futures @2. Reuse trusted PUT body. Never a 51st."""
from __future__ import annotations

import inspect
from typing import Any, Mapping, Sequence

from api.kabu_register import KABU_PUSH_REGISTER_LIMIT, register_symbols_cleared
from api.push_client import _register_symbols
from research.new_causal_information_acquisition_v1 import (
    FUTURES_EXCHANGE,
    FUTURES_N,
    STOCK_EXCHANGE,
    TOTAL_REGISTRATION_N,
)
from research.new_causal_information_acquisition_v1.exclusive import ExclusiveBlocked, require_exclusive

REGISTER_LIMIT = int(KABU_PUSH_REGISTER_LIMIT)


class RegistrationPlanError(RuntimeError):
    pass


def trusted_register_body(symbols_spec: Sequence[tuple[str, int]]) -> dict[str, Any]:
    """Same request builder as api.push_client._register_symbols. No PushType in this KabuS version."""
    return {"Symbols": [{"Symbol": str(s), "Exchange": int(ex)} for s, ex in symbols_spec]}


def push_type_in_trusted_builder() -> bool:
    src = inspect.getsource(_register_symbols)
    return "PushType" in src


def build_stock_specs(stock_symbols: Sequence[str]) -> list[tuple[str, int]]:
    return [(str(s), int(STOCK_EXCHANGE)) for s in stock_symbols]


def build_futures_specs(contracts: Sequence[Mapping[str, Any]]) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for c in contracts:
        sym = str(c.get("resolved_symbol") or "").split("@", 1)[0].strip()
        if not sym:
            raise RegistrationPlanError("futures spec missing resolved_symbol")
        out.append((sym, int(FUTURES_EXCHANGE)))
    return out


def assert_exact_50(specs: Sequence[tuple[str, int]]) -> list[tuple[str, int]]:
    n = len(specs)
    if n > REGISTER_LIMIT or n > TOTAL_REGISTRATION_N:
        raise RegistrationPlanError(
            f"51st registration forbidden: n={n} limit={REGISTER_LIMIT}"
        )
    if n != TOTAL_REGISTRATION_N:
        raise RegistrationPlanError(f"NEW_INFO registration must be exactly {TOTAL_REGISTRATION_N}, got {n}")
    return list(specs)


def never_append_51st(specs: Sequence[tuple[str, int]], extra: tuple[str, int]) -> None:
    trial = list(specs) + [extra]
    if len(trial) > REGISTER_LIMIT:
        raise RegistrationPlanError("51st registration attempt blocked")
    raise RegistrationPlanError("NEW_INFO plan is frozen at 50; extra symbol rejected")


def build_plan(
    *,
    stock_symbols: Sequence[str],
    contracts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    stocks = list(stock_symbols)
    futs = build_futures_specs(contracts)
    if len(stocks) != 48:
        raise RegistrationPlanError(f"stock N={len(stocks)} != 48")
    if len(futs) != FUTURES_N:
        raise RegistrationPlanError(f"futures N={len(futs)} != {FUTURES_N}")
    specs = assert_exact_50(build_stock_specs(stocks) + futs)
    body = trusted_register_body(specs)
    symbols_field = body["Symbols"]
    if any("PushType" in item for item in symbols_field):
        raise RegistrationPlanError("PushType must not be invented")
    return {
        "ok": True,
        "core_n": 10,
        "dynamic_n": 38,
        "stock_n": 48,
        "futures_n": FUTURES_N,
        "total_n": len(specs),
        "specs": specs,
        "body": body,
        "push_type_present": False,
        "stock_exchange": STOCK_EXCHANGE,
        "futures_exchange": FUTURES_EXCHANGE,
        "coordinator": "api.kabu_register.register_symbols_cleared",
        "request_builder": "api.push_client._register_symbols",
    }


def apply_registration_if_exclusive(
    push: Any,
    plan: Mapping[str, Any],
    *,
    native_root: Any,
    trading_date: str,
    isolated_state_root: Any,
) -> dict[str, Any]:
    """Reuse trusted coordinator only after exclusive probe on the REAL root.

    isolated_state_root receives paper_register_state so production Paper state is not overwritten.
    unregister/all is Station-global — exclusive() must have already proven no competitor.
    """
    probe = require_exclusive(native_root=native_root, trading_date=trading_date)
    specs = assert_exact_50(list(plan.get("specs") or []))
    result = register_symbols_cleared(
        push,
        specs,
        clear_first=True,
        native_root=isolated_state_root,
        trading_date=trading_date,
        allow_reuse_if_match=False,
    )
    return {
        "ok": bool(result.get("ok")),
        "exclusive": probe,
        "register": result,
        "symbol_count": result.get("symbol_count"),
        "unregistered_competitor": False,
    }


# Keep ExclusiveBlocked imported for launcher.
assert ExclusiveBlocked
assert int(REGISTER_LIMIT) == 50
