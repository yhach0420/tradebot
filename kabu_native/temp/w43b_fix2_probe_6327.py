"""Probe 6327.T ghost accept artifacts (read-only)."""
import json
from pathlib import Path

sd = Path(r"c:\Users\yhach\Documents\tradebotfile\kabu_native\results\small_paper\20260717\live_session_081810")

print("=== np_pre_entry / entry_scan around accept ===")
for name in ["np_pre_entry_features.jsonl", "entry_scan_audit.jsonl", "errors.jsonl"]:
    p = sd / name
    if not p.is_file():
        continue
    for line in p.open(encoding="utf-8"):
        if "6327.T" not in line:
            continue
        if "09:05:1" not in line and "09:05:12" not in line:
            continue
        o = json.loads(line)
        keys = [
            k
            for k in o
            if "price" in k.lower()
            or k
            in (
                "symbol",
                "AskPrice",
                "BidPrice",
                "CurrentPrice",
                "CalcPrice",
                "trade_stale",
                "price_freshness_source",
            )
        ]
        print(name, {k: o.get(k) for k in keys})
        print("---")

print("=== accept event price fields ===")
for line in (sd / "small_paper_events.jsonl").open(encoding="utf-8"):
    o = json.loads(line)
    if o.get("symbol") == "6327.T" and o.get("event_type") == "accepted":
        for k in (
            "current_price",
            "entry_price",
            "price_freshness_source",
            "price_age_sec",
            "board_age_sec",
            "entry_order_book_imbalance",
            "position_id",
            "discord_sent_ts",
        ):
            print(f"  {k}={o.get(k)!r}")
