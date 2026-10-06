"""Apply indicator EXIT to C0-eligible fills only. CURRENT keeps C14."""
from __future__ import annotations

from typing import Any

from research.am_c0_indicator_exit import EXIT_REASON, TERMINAL_REASON
from research.am_c0_indicator_exit.model import first_exit
from research.canonical_entry_performance_rebase.analyze import row_key


def exits_by_key(day_bodies: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for body in day_bodies:
        for tr in list(body.get("trades") or []):
            key = str(tr.get("row_key") or "")
            if not key:
                continue
            got = first_exit(tr)
            rec = dict(got)
            rec["row_key"] = key
            rec["date"] = tr.get("date")
            rec["symbol"] = tr.get("symbol")
            rec["anchor"] = tr.get("anchor")
            rec["role"] = tr.get("role")
            rec["c14_pnl_yen_100"] = tr.get("c14_pnl_yen_100")
            rec["fill_t"] = tr.get("fill_t")
            rec["fill_price"] = tr.get("fill_price")
            rec["n_states"] = tr.get("n_states")
            out[key] = rec
    return out


def apply_eind_exits(rows: list[dict[str, Any]], eind_by: dict[str, dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    out = []
    miss = 0
    for r in rows:
        rec = dict(r)
        rec["_c14_exit_t"] = r.get("exit_t")
        rec["_c14_exit_price"] = r.get("exit_price")
        rec["_c14_exit_reason"] = r.get("exit_reason")
        rec["_c14_pnl_yen_100"] = r.get("pnl_yen_100")
        rec["_eind_triggered"] = False
        if rec.get("_aug_eligible") and int(rec.get("Y_FILL5") or 0) == 1:
            key = str(rec.get("_row_key") or row_key(rec))
            e = eind_by.get(key)
            if e is None or not e.get("ok"):
                miss += 1
                rec["_eind_miss"] = True
            else:
                rec["exit_t"] = e.get("exit_t")
                rec["exit_price"] = e.get("exit_price")
                rec["exit_reason"] = e.get("exit_reason") or (EXIT_REASON if e.get("triggered") else TERMINAL_REASON)
                rec["pnl_yen_100"] = e.get("pnl_yen_100")
                rec["_eind_triggered"] = bool(e.get("triggered"))
                rec["_eind_p_exit"] = e.get("p_exit")
        out.append(rec)
    return out, miss
