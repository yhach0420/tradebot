"""V10 RCI/Board incremental gates inside T3+Pullback. Nested arms only. No retune. No EXIT."""
from __future__ import annotations

from typing import Any

from research.simple_tech_entry_family.v8_analyze import a4_diagnostics, core_edge_gate
from research.simple_tech_entry_family.v8_spec import ROLE_MIN_EXECUTABLE_N
from research.simple_tech_entry_family.v10_spec import ROLE_MIN_EXECUTABLE_N as V10_MIN

STACK = {
    "B0_T3_PULLBACK": "T3_PULLBACK",
    "B1_RCI": "T3_PULLBACK_RCI",
    "B2_RCI_BOARD": "T3_PULLBACK_RCI_BOARD",
}


def selected_arm_id(rci: bool, board: bool, rci_harm: bool, board_harm: bool) -> str:
    if rci_harm:
        return "B0_T3_PULLBACK"
    if board_harm:
        return "B1_RCI" if rci else "B0_T3_PULLBACK"
    if rci and board:
        return "B2_RCI_BOARD"
    if rci:
        return "B1_RCI"
    if board:
        return "B2_RCI_BOARD"
    return "B0_T3_PULLBACK"


def coverage_ok(arm: dict[str, Any]) -> bool:
    n = int(arm.get("EXECUTABLE_SIGNAL_N") or 0)
    return bool(n >= int(V10_MIN) and n >= int(ROLE_MIN_EXECUTABLE_N))


def edge_arm(arm: dict[str, Any], *, integrity_ok: bool) -> dict[str, Any]:
    pack = {
        "MARKOUT60_MEAN": arm.get("MARKOUT60_MEAN"),
        "MARKOUT180_MEAN": arm.get("MARKOUT180_MEAN"),
        "MARKOUT300_MEAN": arm.get("MARKOUT300_MEAN"),
        "MARKOUT180_MEDIAN": arm.get("MARKOUT180_MEDIAN"),
        "MARKOUT300_MEDIAN": arm.get("MARKOUT300_MEDIAN"),
        "POSITIVE_DAY_N_180": arm.get("POSITIVE_DAY_N_180"),
        "NEGATIVE_DAY_N_180": arm.get("NEGATIVE_DAY_N_180"),
        "POSITIVE_DAY_N_300": arm.get("POSITIVE_DAY_N_300"),
        "NEGATIVE_DAY_N_300": arm.get("NEGATIVE_DAY_N_300"),
        "EX_BEST_180": arm.get("EX_BEST_180"),
        "EX_BEST_300": arm.get("EX_BEST_300"),
        "EXECUTABLE_SIGNAL_N": arm.get("EXECUTABLE_SIGNAL_N"),
    }
    return core_edge_gate(pack, integrity_ok=integrity_ok)


def pq3_diagnostic(arm: dict[str, Any]) -> dict[str, Any]:
    body = a4_diagnostics(list(arm.get("exe_rows") or []))
    body["diagnostic_only"] = True
    body["not_a_gate"] = True
    return body


def decide_case(
    *,
    rci: bool,
    board: bool,
    rci_harm: bool,
    board_harm: bool,
    integrity_ok: bool,
) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "CASE": "INTEGRITY",
            "VERDICT": "SIMPLE_TECH_V10_INTEGRITY_FAILED",
            "SELECTED_STACK": None,
            "PRIMARY_INTERPRETATION": "INTEGRITY_FAILURE",
            "NEXT": "NON_INTERFERENCE_FAIL",
        }
    if rci_harm:
        return {
            "CASE": "RCI_HARMFUL",
            "VERDICT": "SIMPLE_TECH_V10_RCI_HARD_CONFIRM_NOT_SUPPORTED",
            "SELECTED_STACK": STACK["B0_T3_PULLBACK"],
            "PRIMARY_INTERPRETATION": (
                "Inside T3+Pullback, adding RCI as a hard confirm is harmful vs B0. "
                "Do not delete RCI from the family. Do not retune -80. Board incremental is not interpreted after RCI harm."
            ),
            "NEXT": (
                "Keep T3+Pullback as the reference stack. Do not use RCI as a hard confirm inside this context. "
                "Do not restore PA/Volume/Persistence. No EXIT."
            ),
        }
    if board_harm:
        sel = STACK["B1_RCI"] if rci else STACK["B0_T3_PULLBACK"]
        return {
            "CASE": "BOARD_HARMFUL",
            "VERDICT": "SIMPLE_TECH_V10_BOARD_VETO_NOT_SUPPORTED",
            "SELECTED_STACK": sel,
            "PRIMARY_INTERPRETATION": (
                "Inside T3+Pullback+RCI, adding Board as a hard veto is harmful vs B1. "
                "Board stays SUPPORT/VETO in the family, not an incremental hard gate here."
            ),
            "NEXT": (
                f"Freeze {sel}. Do not use Board as a hard veto inside this T3 context. "
                "Do not restore PA/Volume/Persistence. No EXIT."
            ),
        }
    if rci and board:
        return {
            "CASE": "BOTH",
            "VERDICT": "SIMPLE_TECH_V10_RCI_AND_BOARD_INCREMENTAL_SUPPORTED",
            "SELECTED_STACK": STACK["B2_RCI_BOARD"],
            "PRIMARY_INTERPRETATION": (
                "Inside T3+Pullback, RCI then Board each add incremental markout quality. "
                "T3 remains a reference context, not a standalone direction bet."
            ),
            "NEXT": (
                "Freeze T3+Pullback+RCI+Board as the nested stack. Do not retune RCI or Board. "
                "Do not restore PA/Volume/Persistence. No EXIT."
            ),
        }
    if rci:
        return {
            "CASE": "RCI_ONLY",
            "VERDICT": "SIMPLE_TECH_V10_RCI_INCREMENTAL_SUPPORTED",
            "SELECTED_STACK": STACK["B1_RCI"],
            "PRIMARY_INTERPRETATION": (
                "Inside T3+Pullback, RCI is an incremental confirm. Board veto is not incremental on top of that."
            ),
            "NEXT": (
                "Freeze T3+Pullback+RCI. Do not use Board as a hard veto here. "
                "Do not restore PA/Volume/Persistence. No EXIT."
            ),
        }
    if board:
        return {
            "CASE": "BOARD_ONLY",
            "VERDICT": "SIMPLE_TECH_V10_BOARD_INCREMENTAL_SUPPORTED",
            "SELECTED_STACK": STACK["B2_RCI_BOARD"],
            "PRIMARY_INTERPRETATION": (
                "Board veto is incremental on top of T3+Pullback+RCI, but RCI itself is not incremental vs T3+Pullback. "
                "Do not read this as Board-without-RCI; that arm was not a selection arm."
            ),
            "NEXT": (
                "Keep Board as a nested veto after RCI, but do not treat RCI as a proven incremental confirm. "
                "Do not restore PA/Volume/Persistence. No EXIT."
            ),
        }
    return {
        "CASE": "NEITHER",
        "VERDICT": "SIMPLE_TECH_V10_RCI_BOARD_INCREMENTAL_NOT_SUPPORTED",
        "SELECTED_STACK": STACK["B0_T3_PULLBACK"],
        "PRIMARY_INTERPRETATION": (
            "Inside T3+Pullback, neither RCI nor Board adds robust incremental markout quality. "
            "Do not delete them from the family. Do not generalize V8 A4 vs A5/A6 (Trend-off)."
        ),
        "NEXT": (
            "Freeze T3+Pullback as the reference stack. Reassess remaining ENTRY deficiency without extra arms. "
            "Do not restore PA/Volume/Persistence. No EXIT."
        ),
    }
