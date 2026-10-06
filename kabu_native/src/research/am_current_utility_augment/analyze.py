"""Preservation audit, economic gate, CASE A-E. No threshold search."""
from __future__ import annotations

from typing import Any

from research.am_entry_profit_improvement.metrics import _pf_num, success_gate


def _keys(rows: list[dict[str, Any]], *, arm: str | None = None) -> set[tuple]:
    out: set[tuple] = set()
    for r in rows:
        if arm is not None and str(r.get("arm") or "") != arm:
            continue
        out.add(
            (
                str(r.get("date") or ""),
                str(r.get("symbol") or ""),
                float(r.get("t0") or 0.0),
            )
        )
    return out


def preservation_audit(baseline: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    base_adm = _keys(list(baseline.get("admissions") or []), arm="CURRENT")
    ov_cur_adm = _keys(list(overlay.get("admissions") or []), arm="CURRENT")
    base_fill = _keys(list(baseline.get("fills") or []), arm="CURRENT")
    ov_cur_fill = _keys(list(overlay.get("fills") or []), arm="CURRENT")
    lost_adm = base_adm - ov_cur_adm
    lost_fill = base_fill - ov_cur_fill

    base_tr = {
        (str(t.get("date")), str(t.get("symbol")), float(t.get("t0") or 0.0)): t
        for t in (baseline.get("trades") or [])
        if str(t.get("arm") or "CURRENT") == "CURRENT"
    }
    ov_tr = {
        (str(t.get("date")), str(t.get("symbol")), float(t.get("t0") or 0.0)): t
        for t in (overlay.get("trades") or [])
        if str(t.get("arm") or "") == "CURRENT"
    }
    exit_mismatch = 0
    pnl_mismatch = 0
    for k, bt in base_tr.items():
        ot = ov_tr.get(k)
        if ot is None:
            continue
        if (
            str(bt.get("exit_reason") or "") != str(ot.get("exit_reason") or "")
            or abs(float(bt.get("exit_time") or 0.0) - float(ot.get("exit_time") or 0.0)) > 1e-9
            or abs(float(bt.get("exit_price") or 0.0) - float(ot.get("exit_price") or 0.0)) > 1e-9
        ):
            exit_mismatch += 1
        if abs(float(bt.get("pnl_yen_100") or 0.0) - float(ot.get("pnl_yen_100") or 0.0)) > 1e-9:
            pnl_mismatch += 1

    admission_lost = len(lost_adm)
    fill_lost = len(lost_fill)
    pass_ok = admission_lost == 0 and fill_lost == 0 and exit_mismatch == 0 and pnl_mismatch == 0
    return {
        "CURRENT_BASELINE_ADMITTED_N": len(base_adm),
        "CURRENT_OVERLAY_ADMITTED_N": len(ov_cur_adm),
        "CURRENT_ADMISSION_LOST_N": admission_lost,
        "CURRENT_FILL_LOST_N": fill_lost,
        "CURRENT_EXTRA_BLOCK_N": int(overlay.get("current_cap_blocked") or 0)
        + int(overlay.get("current_same_symbol_blocked") or 0)
        - (int(baseline.get("current_cap_blocked") or 0) + int(baseline.get("current_same_symbol_blocked") or 0)),
        "CURRENT_EXIT_MISMATCH_N": exit_mismatch,
        "CURRENT_PNL_MISMATCH_N": pnl_mismatch,
        "CURRENT_SAME_SYMBOL_INTERFERENCE_N": int(overlay.get("current_same_symbol_interference") or 0),
        "CURRENT_CAP_INTERFERENCE_N": int(overlay.get("current_cap_interference") or 0),
        "CURRENT_PRESERVATION_PASS": bool(pass_ok),
    }


def overlay_gate(
    overlay: dict[str, Any],
    current: dict[str, Any],
    paired: dict[str, Any],
    *,
    preservation_pass: bool,
    integrity_ok: bool,
) -> dict[str, Any]:
    base = success_gate(overlay, current, paired, integrity_ok=True)
    gates = dict(base.get("gates") or {})
    gates.pop("H_INTEGRITY", None)
    gates["H_CURRENT_PRESERVATION"] = bool(preservation_pass)
    gates["I_INTEGRITY"] = bool(integrity_ok)
    return {
        "gates": gates,
        "AUGMENT_OVERLAY_PASS": all(gates.values()),
    }


def decide_case(
    *,
    integrity_ok: bool,
    preservation_pass: bool,
    overlay_pass: bool,
    augment_net: float,
    overlay_net: float,
    current_net: float,
) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "CASE": "E",
            "VERDICT": "AM_UTILITY_AUGMENT_INTEGRITY_FAILED",
            "NEXT": "STOP",
        }
    if not preservation_pass:
        return {
            "CASE": "C",
            "VERDICT": "AM_UTILITY_AUGMENT_PORTFOLIO_INTERFERENCE",
            "NEXT": "AM_AUGMENT_PORTFOLIO_INTEGRATION_REASSESSMENT",
        }
    if overlay_pass:
        return {
            "CASE": "A",
            "VERDICT": "AM_CURRENT_PRESERVING_UTILITY_AUGMENT_SUPPORTED",
            "NEXT": "AM_CURRENT_PRESERVING_UTILITY_AUGMENT_FREEZE_REVIEW",
        }
    if float(augment_net) > 0 and float(overlay_net) > float(current_net):
        return {
            "CASE": "B",
            "VERDICT": "AM_UTILITY_AUGMENT_PROFITABLE_BUT_RISK_GATE_FAIL",
            "NEXT": "AM_UTILITY_AUGMENT_RISK_INTEGRATION_REASSESSMENT",
        }
    return {
        "CASE": "D",
        "VERDICT": "AM_SELECTIVE_UTILITY_AUGMENT_NOT_ECONOMICALLY_SUPPORTED",
        "NEXT": "AM_ENTRY_INFORMATION_EXPANSION_REASSESSMENT",
    }
