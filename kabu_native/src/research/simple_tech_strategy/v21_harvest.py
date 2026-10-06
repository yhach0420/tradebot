"""Attach notional/bps fields to frozen V20 trades. No Capture restream. No sizing policy."""
from __future__ import annotations

from typing import Any

from research.simple_tech_exit_family.v14_analyze import _finite
from research.simple_tech_strategy.v21_spec import HYPOTHETICAL_NOTIONAL_YEN, SHARES


def enrich_trade(row: dict[str, Any]) -> dict[str, Any]:
    rec = dict(row)
    rec["symbol"] = str(rec.get("symbol") or "").replace(".T", "")
    rec["date"] = str(rec.get("date") or "")
    rec["shares"] = int(SHARES)
    entry = rec.get("fill_price") if _finite(rec.get("fill_price")) else rec.get("entry_price")
    exit_px = rec.get("exit_bid") if _finite(rec.get("exit_bid")) else rec.get("exit_price")
    rec["entry_price"] = float(entry) if _finite(entry) else None
    rec["exit_price"] = float(exit_px) if _finite(exit_px) else None
    bps = rec.get("exit_bps") if _finite(rec.get("exit_bps")) else rec.get("pnl_bps")
    if (not _finite(bps)) and _finite(rec.get("entry_price")) and _finite(rec.get("exit_price")) and float(rec["entry_price"]) > 0:
        bps = (float(rec["exit_price"]) / float(rec["entry_price"]) - 1.0) * 10000.0
    rec["pnl_bps"] = float(bps) if _finite(bps) else None
    yen = rec.get("pnl_yen_100")
    if (not _finite(yen)) and _finite(rec.get("entry_price")) and _finite(rec.get("exit_price")):
        yen = (float(rec["exit_price"]) - float(rec["entry_price"])) * float(SHARES)
    rec["pnl_yen_100"] = float(yen) if _finite(yen) else None
    rec["entry_notional_yen"] = (float(rec["entry_price"]) * float(SHARES)) if _finite(rec.get("entry_price")) else None
    rec["absolute_pnl_yen"] = abs(float(rec["pnl_yen_100"])) if _finite(rec.get("pnl_yen_100")) else None
    if _finite(rec.get("pnl_bps")):
        rec["NORMALIZED_PNL_1M"] = float(rec["pnl_bps"]) / 10000.0 * float(HYPOTHETICAL_NOTIONAL_YEN)
    else:
        rec["NORMALIZED_PNL_1M"] = None
    rec["normalized_is_policy"] = False
    return rec


def enrich_trades(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [enrich_trade(r) for r in rows]
