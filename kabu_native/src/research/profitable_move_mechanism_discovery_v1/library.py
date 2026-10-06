"""Finite mechanism templates. At most 2 decision primitives plus one onset identity. No threshold search."""
from __future__ import annotations

from typing import Any

from research.profitable_move_mechanism_discovery_v1 import TEMPLATE_ORDER
from research.profitable_move_mechanism_discovery_v1.predicates import FAMILY, SOURCE_IDENTITY

RAW_PREDICATE_IDS = (
    "S_MA_TREND_UP",
    "S_CLOSE_ABOVE_EMA21",
    "S_BB_ABOVE_MID",
    "S_RCI_ABOVE_NEG80",
    "S_VOL_CONFIRM_1M",
    "S_CLOSE_ABOVE_VWAP",
    "S_BID_GT_ASK_QTY",
    "S_BOARD_SUPPORT",
    "S_PULLBACK_SETUP",
    "S_PRICE_ACTION",
    "S_CLOSE_GT_PREV_CLOSE",
    "S_BREADTH_EXPANDING",
)

CROSS_PAIRS = (
    ("S_MA_TREND_UP", "S_VOL_CONFIRM_1M"),
    ("S_CLOSE_ABOVE_EMA21", "S_VOL_CONFIRM_1M"),
    ("S_MA_TREND_UP", "S_CLOSE_ABOVE_VWAP"),
    ("S_CLOSE_ABOVE_EMA21", "S_CLOSE_ABOVE_VWAP"),
    ("S_MA_TREND_UP", "S_BID_GT_ASK_QTY"),
    ("S_CLOSE_ABOVE_EMA21", "S_BID_GT_ASK_QTY"),
    ("S_MA_TREND_UP", "S_BB_ABOVE_MID"),
    ("S_MA_TREND_UP", "S_RCI_ABOVE_NEG80"),
    ("S_MA_TREND_UP", "S_BREADTH_EXPANDING"),
    ("S_VOL_CONFIRM_1M", "S_CLOSE_ABOVE_VWAP"),
    ("S_VOL_CONFIRM_1M", "S_BID_GT_ASK_QTY"),
    ("S_VOL_CONFIRM_1M", "S_BB_ABOVE_MID"),
    ("S_VOL_CONFIRM_1M", "S_RCI_ABOVE_NEG80"),
    ("S_VOL_CONFIRM_1M", "S_BREADTH_EXPANDING"),
    ("S_CLOSE_ABOVE_VWAP", "S_BID_GT_ASK_QTY"),
    ("S_CLOSE_ABOVE_VWAP", "S_BB_ABOVE_MID"),
    ("S_CLOSE_ABOVE_VWAP", "S_RCI_ABOVE_NEG80"),
    ("S_CLOSE_ABOVE_VWAP", "S_BREADTH_EXPANDING"),
    ("S_BID_GT_ASK_QTY", "S_BB_ABOVE_MID"),
    ("S_BID_GT_ASK_QTY", "S_RCI_ABOVE_NEG80"),
    ("S_PULLBACK_SETUP", "S_VOL_CONFIRM_1M"),
    ("S_PULLBACK_SETUP", "S_BID_GT_ASK_QTY"),
    ("S_PRICE_ACTION", "S_VOL_CONFIRM_1M"),
    ("S_PRICE_ACTION", "S_CLOSE_ABOVE_VWAP"),
    ("S_CLOSE_GT_PREV_CLOSE", "S_CLOSE_ABOVE_VWAP"),
    ("S_BOARD_SUPPORT", "S_VOL_CONFIRM_1M"),
    ("S_BOARD_SUPPORT", "S_MA_TREND_UP"),
)

SEQUENCES = (
    ("S_MA_TREND_UP", "S_VOL_CONFIRM_1M"),
    ("S_CLOSE_ABOVE_VWAP", "S_VOL_CONFIRM_1M"),
    ("S_VOL_CONFIRM_1M", "S_CLOSE_ABOVE_VWAP"),
    ("S_BID_GT_ASK_QTY", "S_VOL_CONFIRM_1M"),
    ("S_PULLBACK_SETUP", "S_PRICE_ACTION"),
    ("S_RCI_ABOVE_NEG80", "S_VOL_CONFIRM_1M"),
    ("S_BB_ABOVE_MID", "S_VOL_CONFIRM_1M"),
    ("S_BREADTH_EXPANDING", "S_MA_TREND_UP"),
)

ONSET_CONTEXT = (
    ("S_MA_TREND_UP", "S_VOL_CONFIRM_1M"),
    ("S_MA_TREND_UP", "S_CLOSE_ABOVE_VWAP"),
    ("S_MA_TREND_UP", "S_BID_GT_ASK_QTY"),
    ("S_MA_TREND_UP", "S_BREADTH_EXPANDING"),
    ("S_VOL_CONFIRM_1M", "S_MA_TREND_UP"),
    ("S_VOL_CONFIRM_1M", "S_CLOSE_ABOVE_VWAP"),
    ("S_CLOSE_ABOVE_VWAP", "S_VOL_CONFIRM_1M"),
    ("S_CLOSE_ABOVE_EMA21", "S_VOL_CONFIRM_1M"),
    ("S_BID_GT_ASK_QTY", "S_VOL_CONFIRM_1M"),
    ("S_RCI_ABOVE_NEG80", "S_MA_TREND_UP"),
    ("S_BB_ABOVE_MID", "S_VOL_CONFIRM_1M"),
    ("S_PRICE_ACTION", "S_VOL_CONFIRM_1M"),
    ("S_PULLBACK_SETUP", "S_VOL_CONFIRM_1M"),
)


def _src(pid: str) -> str:
    return str(SOURCE_IDENTITY[pid])


def _fam(pid: str) -> str:
    return str(FAMILY[pid])


def _card(
    mech_id: str,
    template: str,
    primitives: tuple[str, ...],
    onset_of: str | None,
    definition: str,
) -> dict[str, Any]:
    fams = [_fam(p) for p in primitives]
    n_prim = len(primitives)
    if onset_of:
        assert onset_of in primitives
    if template == "C_CROSS_FAMILY_PAIR":
        assert n_prim == 2
        assert fams[0] != fams[1]
    if template in ("D_ONE_STEP_SEQUENCE", "E_ONSET_WITH_CONTEXT"):
        assert n_prim == 2
        assert fams[0] != fams[1]
    if template == "A_SINGLE_STATE":
        assert n_prim == 1
        assert onset_of is None
    if template == "B_ONSET":
        assert n_prim == 1
        assert onset_of == primitives[0]
    assert n_prim <= 2
    return {
        "MECHANISM_ID": mech_id,
        "TEMPLATE": template,
        "PRIMITIVES": list(primitives),
        "ONSET_OF": onset_of,
        "PRIMITIVE_N": int(n_prim),
        "FAMILIES": fams,
        "DEFINITION": definition,
        "SOURCE_IDENTITIES": {p: _src(p) for p in primitives},
        "TEMPLATE_RANK": int(TEMPLATE_ORDER.index(template)),
    }


def candidate_library() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for pid in RAW_PREDICATE_IDS:
        out.append(
            _card(
                f"A_{pid}",
                "A_SINGLE_STATE",
                (pid,),
                None,
                f"P(t) = {pid}",
            )
        )
        out.append(
            _card(
                f"B_ONSET_{pid}",
                "B_ONSET",
                (pid,),
                pid,
                f"NOT {pid}(t-1) AND {pid}(t)",
            )
        )
    for p, q in CROSS_PAIRS:
        out.append(
            _card(
                f"C_{p}__{q}",
                "C_CROSS_FAMILY_PAIR",
                (p, q),
                None,
                f"{p}(t) AND {q}(t)",
            )
        )
    for p, q in SEQUENCES:
        out.append(
            _card(
                f"D_{p}__THEN_{q}",
                "D_ONE_STEP_SEQUENCE",
                (p, q),
                None,
                f"{p}(t-1) AND {q}(t)",
            )
        )
    for p, q in ONSET_CONTEXT:
        out.append(
            _card(
                f"E_ONSET_{p}__CTX_{q}",
                "E_ONSET_WITH_CONTEXT",
                (p, q),
                p,
                f"NOT {p}(t-1) AND {p}(t) AND {q}(t)",
            )
        )
    ids = [str(r["MECHANISM_ID"]) for r in out]
    assert len(ids) == len(set(ids))
    return out


def library_by_id() -> dict[str, dict[str, Any]]:
    return {str(r["MECHANISM_ID"]): r for r in candidate_library()}


def evaluate_row(cand: dict[str, Any], now: dict[str, bool], prev: dict[str, bool]) -> bool:
    template = str(cand["TEMPLATE"])
    prim = list(cand["PRIMITIVES"])
    if template == "A_SINGLE_STATE":
        return bool(now.get(prim[0]))
    if template == "B_ONSET":
        p = prim[0]
        return (not bool(prev.get(p))) and bool(now.get(p))
    if template == "C_CROSS_FAMILY_PAIR":
        return bool(now.get(prim[0])) and bool(now.get(prim[1]))
    if template == "D_ONE_STEP_SEQUENCE":
        return bool(prev.get(prim[0])) and bool(now.get(prim[1]))
    if template == "E_ONSET_WITH_CONTEXT":
        p, q = prim[0], prim[1]
        return (not bool(prev.get(p))) and bool(now.get(p)) and bool(now.get(q))
    raise ValueError(template)
