import json
from pathlib import Path

p = Path(r"C:\Users\yhach\Documents\tradebotfile\kabu_native\data\market_capture\20260902\session_ing_20260902_44816_1788303102_b36a2064")
prev = None
n = 0
for part in sorted(p.glob("push_part_*.jsonl")):
    with part.open(encoding="utf-8") as handle:
        for line in handle:
            if '"symbol":"7376"' not in line:
                continue
            if "T15:1" not in line and "T15:2" not in line:
                continue
            rec = json.loads(line)
            if rec.get("symbol") != "7376":
                continue
            recv = str(rec.get("received_at") or "")
            if recv < "2026-09-02T15:14:30" or recv >= "2026-09-02T15:25:05":
                continue
            pay = rec.get("original_payload") or {}
            buy = pay.get("Buy1") if isinstance(pay.get("Buy1"), dict) else {}
            key = (buy.get("Price"), buy.get("Qty"), buy.get("Time"), pay.get("CurrentPriceTime"), buy.get("Sign"), pay.get("CurrentPrice"))
            if key == prev:
                continue
            prev = key
            print(recv, "buy", buy.get("Price"), buy.get("Qty"), buy.get("Sign"), "buyT", buy.get("Time"), "cpt", pay.get("CurrentPriceTime"), "px", pay.get("CurrentPrice"))
            n += 1
print("rows", n)
