"""Raw push event -> evaluation input record (score-free by construction).

Quote semantics follow the mainline canonical convention (Buy1/Sell1 SoT):
standard bid = Buy1.Price (kabu label "AskPrice"), standard ask = Sell1.Price
(kabu label "BidPrice"). No score field exists anywhere in this pipeline; any
extraneous keys (e.g. an injected score column) are ignored, which is verified
by the score-independence test.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

_ALLOWED_INPUT_KEYS = "documented below; extraneous payload keys are never read"


@dataclass(frozen=True)
class EvalEvent:
    symbol: str
    ts_epoch: float          # ingress (recorded_at)
    bid: Optional[float]     # Buy1.Price  (standard bid)
    ask: Optional[float]     # Sell1.Price (standard ask)
    bid_qty: Optional[float]
    ask_qty: Optional[float]
    volume: Optional[float]  # cumulative TradingVolume
    vwap: Optional[float]
    board_buy_qty10: Optional[float]   # sum Buy1..Buy10 Qty (None unless all present)
    board_sell_qty10: Optional[float]  # sum Sell1..Sell10 Qty


def _f(v: Any) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def event_from_payload(symbol: str, recorded_at: str, payload: dict[str, Any]) -> Optional[EvalEvent]:
    try:
        ts = datetime.fromisoformat(recorded_at).timestamp()
    except (TypeError, ValueError):
        return None
    b1 = payload.get("Buy1") or {}
    s1 = payload.get("Sell1") or {}
    bid = _f(b1.get("Price"))
    ask = _f(s1.get("Price"))

    def _side_sum(side: str) -> Optional[float]:
        total = 0.0
        for lv in range(1, 11):
            d = payload.get(f"{side}{lv}") or {}
            q = _f(d.get("Qty"))
            if q is None:
                return None
            total += q
        return total

    return EvalEvent(
        symbol=symbol,
        ts_epoch=ts,
        bid=bid,
        ask=ask,
        bid_qty=_f(b1.get("Qty")),
        ask_qty=_f(s1.get("Qty")),
        volume=_f(payload.get("TradingVolume")),
        vwap=_f(payload.get("VWAP")),
        board_buy_qty10=_side_sum("Buy"),
        board_sell_qty10=_side_sum("Sell"),
    )


def iter_raw_events(fp: Path) -> Iterator[EvalEvent]:
    """Stream one raw symbol-day JSONL file into EvalEvents (order preserved)."""
    sym = fp.stem
    with fp.open("rb") as f:
        for lineb in f:
            try:
                d = json.loads(lineb)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            ev = event_from_payload(sym, d.get("recorded_at"), d.get("payload") or {})
            if ev is not None:
                yield ev
