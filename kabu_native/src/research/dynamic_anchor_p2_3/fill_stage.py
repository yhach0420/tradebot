"""ADMITTED → FILLED|EXPIRED mapping. No WAIT_SEC retune."""
from __future__ import annotations

from typing import Any


def sym_key(s: Any) -> str:
    return str(s or "").replace(".T", "").strip().upper()


def resolve_admitted_fill_stage(
    rows: list[dict[str, Any]],
    fills: list[dict[str, Any]],
    *,
    wait_sec: float,
) -> list[str]:
    """Map ADMITTED → FILLED|EXPIRED. FILLED if a_fills matches symbol+anchor or wait window."""
    used: set[int] = set()
    leftover_pending_note: list[str] = []
    for row in rows:
        if row.get("entry_terminal") != "ADMITTED":
            row["fill_terminal"] = None
            row["canonical_terminal_outcome"] = row.get("entry_terminal")
            continue
        t1 = row.get("t1")
        if t1 is None:
            row["fill_terminal"] = "ADMITTED_UNRESOLVED"
            row["canonical_terminal_outcome"] = "ADMITTED_UNRESOLVED"
            leftover_pending_note.append(f"{row.get('date')}|{row.get('symbol')}|missing_t1")
            continue
        t1f = float(t1)
        an = str(row.get("anchor") or "")
        sym = sym_key(row.get("symbol"))
        hit = None
        for i, f in enumerate(fills):
            if i in used:
                continue
            if sym_key(f.get("symbol")) != sym:
                continue
            anc_ok = str(f.get("anchor") or "") == an and an != ""
            ft = f.get("fill_time")
            time_ok = False
            if ft is not None:
                ftf = float(ft)
                time_ok = (t1f - 1e-3) <= ftf <= (t1f + float(wait_sec) + 0.5)
            if anc_ok or time_ok:
                hit = i
                break
        if hit is not None:
            used.add(hit)
            row["fill_terminal"] = "FILLED"
            row["canonical_terminal_outcome"] = "FILLED"
        else:
            row["fill_terminal"] = "EXPIRED"
            row["canonical_terminal_outcome"] = "EXPIRED"
    return leftover_pending_note


def reconcile_fills_with_trades(
    rows: list[dict[str, Any]],
    trades: list[dict[str, Any]],
    *,
    wait_sec: float,
) -> int:
    """Upgrade EXPIRED→FILLED when DualLane trade fill_time sits in the admit wait window."""
    used: set[int] = set()
    upgraded = 0
    for row in rows:
        if row.get("entry_terminal") != "ADMITTED":
            continue
        if row.get("fill_terminal") == "FILLED":
            t1 = row.get("t1")
            if t1 is None:
                continue
            t1f = float(t1)
            sym = sym_key(row.get("symbol"))
            for i, tr in enumerate(trades):
                if i in used:
                    continue
                if sym_key(tr.get("symbol")) != sym:
                    continue
                ft = tr.get("fill_time")
                if ft is None:
                    continue
                if (t1f - 1e-3) <= float(ft) <= (t1f + float(wait_sec) + 0.5):
                    used.add(i)
                    break
            continue
        t1 = row.get("t1")
        if t1 is None:
            continue
        t1f = float(t1)
        sym = sym_key(row.get("symbol"))
        for i, tr in enumerate(trades):
            if i in used:
                continue
            if sym_key(tr.get("symbol")) != sym:
                continue
            ft = tr.get("fill_time")
            if ft is None:
                continue
            if (t1f - 1e-3) <= float(ft) <= (t1f + float(wait_sec) + 0.5):
                used.add(i)
                row["fill_terminal"] = "FILLED"
                row["canonical_terminal_outcome"] = "FILLED"
                upgraded += 1
                break
    return upgraded
