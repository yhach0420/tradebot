"""Registration-gate contract against the live resolver. No replacement gate."""
from __future__ import annotations

from typing import Any

from small_paper.kabu_registration_authority import (
    NO_REGISTERED_KABU_PROBE_SYMBOL,
    resolve_registered_probe_symbol,
)
from small_paper.v1r_native_entry_live import resolve_day_fixed_am_runtime_universe

from research.fixed_entry_support_runtime_binding_evidence_recovery_v1.evidence import ROOT, bare


def _call(day: str, actual: list[str] | None, symbol: str | None) -> dict[str, Any]:
    return resolve_registered_probe_symbol(
        ROOT,
        day,
        actual_symbols=actual,
        proposed_symbol=symbol,
        push=None,
        write_audit=False,
    )


def run_contract() -> dict[str, Any]:
    day = "20260910"
    universe = resolve_day_fixed_am_runtime_universe(native_root=ROOT, trading_date=day)
    dynamic = [bare(symbol) for symbol in (universe.get("symbols") or []) if bare(symbol)]
    if len(dynamic) != 50 or not universe.get("ok"):
        raise RuntimeError("contract_dynamic40_unavailable")
    member = dynamic[0]
    outsider = "7203" if "7203" not in set(dynamic) else "9999"
    case_a = _call(day, list(dynamic), member)
    case_b = _call(day, [symbol for symbol in dynamic if symbol != member], member)
    swapped = [symbol for symbol in dynamic if symbol != member] + [outsider]
    case_c = _call(day, swapped, outsider)
    case_d = _call("20260722", None, member)
    results = {
        "function": "small_paper.kabu_registration_authority.resolve_registered_probe_symbol",
        "case_a": {
            "pass": bool(case_a.get("ok") and case_a.get("kabu_probe_symbol_registered")),
            "reason": str(case_a.get("reason") or ""),
            "expected": "registration gate PASS",
        },
        "case_b": {
            "pass": (not case_b.get("ok")) and (not case_b.get("kabu_probe_symbol_registered")) and str(case_b.get("reason") or "") == "probe_symbol_not_in_actual_registered_set",
            "reason": str(case_b.get("reason") or ""),
            "expected": "NOT_REGISTERED",
        },
        "case_c": {
            "pass": (not case_c.get("ok")) and str(case_c.get("reason") or "") == "actual_registered_exact50_required",
            "reason": str(case_c.get("reason") or ""),
            "expected": "actual_registered_exact50_required",
            "ordering": "registered-set mismatch fails closed before a non-Dynamic40 symbol is admitted",
        },
        "case_d": {
            "pass": (not case_d.get("ok")) and str(case_d.get("reason") or "") == NO_REGISTERED_KABU_PROBE_SYMBOL,
            "reason": str(case_d.get("reason") or ""),
            "expected": "FAIL CLOSED",
        },
    }
    results["all_pass"] = all(bool(results[name]["pass"]) for name in ("case_a", "case_b", "case_c", "case_d"))
    return results
