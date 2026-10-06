"""Reference admission matching the existing exact-50 runtime contract."""
from __future__ import annotations


def reference_decision(symbol: str, dynamic50: frozenset[str], registered50: frozenset[str]) -> tuple[bool, str]:
    """Same order as resolve_registered_probe_symbol. Sets must be identical before a symbol passes."""
    if symbol not in registered50:
        return False, "NOT_REGISTERED"
    if dynamic50 != registered50:
        return False, "EXACT50_FAIL_CLOSED"
    if symbol not in dynamic50:
        return False, "NOT_DYNAMIC40"
    return True, "PASS"
