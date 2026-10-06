#!/usr/bin/env python
"""Extract one 5801 20260827 ~09:05 board payload for golden tests. Offline only."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
CAP = NATIVE / "data" / "market_capture" / "20260827"
OUT = NATIVE / "tests" / "fixtures" / "5801_20260827_0905.json"


def main() -> int:
    parts = sorted((CAP / "session_ing_20260827_25152_1787780714_fdaedfbd").glob("push_part_*.jsonl"))
    t0 = datetime(2026, 8, 27, 9, 5, tzinfo=JST).timestamp()
    t1 = datetime(2026, 8, 27, 9, 6, tzinfo=JST).timestamp()
    found = None
    for p in parts[:8]:
        with p.open("r", encoding="utf-8") as fh:
            for line in fh:
                if "5801" not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                pay = rec.get("payload") or rec
                sym = str(pay.get("Symbol") or rec.get("symbol") or "")
                if "5801" not in sym:
                    continue
                ts = rec.get("received_at") or pay.get("received_at") or rec.get("recorded_at")
                try:
                    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=JST)
                    epoch = dt.astimezone(JST).timestamp()
                except Exception:
                    continue
                if epoch < t0 - 1 or epoch > t1:
                    continue
                found = {
                    "received_at": ts,
                    "sequence": rec.get("sequence"),
                    "payload": {
                        k: pay.get(k)
                        for k in (
                            "Symbol",
                            "CurrentPrice",
                            "CurrentPriceTime",
                            "CurrentPriceStatus",
                            "OpeningPrice",
                            "OpeningPriceTime",
                            "TradingVolume",
                            "TradingVolumeTime",
                            "AskSign",
                            "BidSign",
                            "Buy1",
                            "Sell1",
                            "SpecialQuote",
                        )
                    },
                }
                break
        if found:
            break
    if not found:
        print("NOT_FOUND")
        return 2
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(found, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(OUT)
    print(json.dumps(found["payload"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
