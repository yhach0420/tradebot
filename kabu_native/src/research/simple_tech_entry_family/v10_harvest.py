"""V10 join-only from V8 TF1 rows. Nested T3+Pullback then RCI then Board. No recapture."""
from __future__ import annotations

from typing import Any

from research.simple_tech_entry_family.harvest import CACHE
from research.simple_tech_entry_family.v10_spec import ARM_ORDER, STATE_ORDER

V10_CACHE = CACHE / "v10_rci_board_role_rca"


def t3_ok(row: dict[str, Any]) -> bool:
    return bool(row.get("trend"))


def base_ok(row: dict[str, Any]) -> bool:
    return bool(t3_ok(row) and row.get("pullback"))


def arm_pass(row: dict[str, Any], arm_id: str) -> bool:
    if not base_ok(row):
        return False
    if arm_id == "B0_T3_PULLBACK":
        return True
    if arm_id == "B1_RCI":
        return bool(row.get("rci"))
    if arm_id == "B2_RCI_BOARD":
        return bool(row.get("rci") and row.get("board_ok"))
    raise KeyError(arm_id)


def state_id(row: dict[str, Any]) -> str:
    r = 1 if row.get("rci") else 0
    b = 1 if row.get("board_ok") else 0
    return f"R{r}B{b}"


def filter_arm(rows: list[dict[str, Any]], arm_id: str) -> dict[str, Any]:
    if arm_id not in ARM_ORDER:
        raise KeyError(arm_id)
    hit = [r for r in rows if arm_pass(r, arm_id)]
    exe = [r for r in hit if r.get("executable_signal")]
    return {"ARM_ID": arm_id, "SIGNAL_N": len(hit), "EXECUTABLE_SIGNAL_N": len(exe), "rows": hit, "exe_rows": exe}


def filter_state(base_rows: list[dict[str, Any]], sid: str) -> dict[str, Any]:
    if sid not in STATE_ORDER:
        raise KeyError(sid)
    hit = [r for r in base_rows if state_id(r) == sid]
    exe = [r for r in hit if r.get("executable_signal")]
    return {"ARM_ID": sid, "SIGNAL_N": len(hit), "EXECUTABLE_SIGNAL_N": len(exe), "rows": hit, "exe_rows": exe}
