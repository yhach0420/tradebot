#!/usr/bin/env python3
"""Probe last market prices for Phase676 Recovery symbols."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[1]

TARGETS = {
    "AM": {
        "session": "live_session_080044",
        "fc": "2026-07-21T11:25:00+09:00",
        "pids": {
            "3915.T_20260721T111027000000": 2119.0,
            "4592.T_20260721T111234000000": 1072.0,
            "5985.T_20260721T111612000000": 1248.0,
            "9238.T_20260721T111722000000": 472.0,
            "4413.T_20260721T111833000000": 3100.0,
        },
    },
    "PM": {
        "session": "live_session_124342",
        "fc": "2026-07-21T15:23:00+09:00",
        "pids": {
            "6058.T_20260721T141242000000": 1653.0,
            "5016.T_20260721T141633000000": 3611.0,
            "5985.T_20260721T142121000000": 1282.0,
            "3449.T_20260721T150345000000": 4825.0,
        },
    },
}


def parse_ts(ts):
    if not ts:
        return None
    t = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if t.tzinfo is None:
        t = t.replace(tzinfo=JST)
    return t


def main() -> None:
    for ampm, cfg in TARGETS.items():
        path = ROOT / "results" / "small_paper" / "20260721" / cfg["session"] / "small_paper_events.jsonl"
        fc = parse_ts(cfg["fc"])
        entry_info = {}
        with path.open(encoding="utf-8") as f:
            for i, line in enumerate(f):
                e = json.loads(line)
                if e.get("event_type") == "accepted" and e.get("position_id") in cfg["pids"]:
                    entry_info[e["position_id"]] = {
                        "symbol": e["symbol"],
                        "entry_time": parse_ts(e.get("entry_time") or e.get("event_time")),
                        "line": i,
                        "entry_price": float(e.get("entry_price")),
                    }
        last = {}
        bid_hits = 0
        with path.open(encoding="utf-8") as f:
            for i, line in enumerate(f):
                e = json.loads(line)
                sym = e.get("symbol")
                ts = parse_ts(e.get("current_price_time") or e.get("event_time") or e.get("board_time"))
                if ts is None or ts > fc:
                    continue
                bid = e.get("BidPrice") or e.get("board_bid") or e.get("best_bid") or e.get("bid")
                ask = e.get("AskPrice") or e.get("board_ask") or e.get("best_ask") or e.get("ask")
                px = e.get("current_price")
                for pid, info in entry_info.items():
                    if info["symbol"] != sym:
                        continue
                    if ts < info["entry_time"]:
                        continue
                    if bid not in (None, ""):
                        bid_hits += 1
                    try:
                        px_f = float(px) if px not in (None, "") else None
                    except (TypeError, ValueError):
                        px_f = None
                    if px_f is None or px_f <= 0:
                        continue
                    cur = last.get(pid)
                    if cur is None or ts >= cur["ts"]:
                        last[pid] = {
                            "ts": ts,
                            "px": px_f,
                            "line": i,
                            "etype": e.get("event_type"),
                            "bid": bid,
                            "ask": ask,
                        }
        print(f"=== {ampm} bid_field_hits={bid_hits} ===")
        for pid, ep in cfg["pids"].items():
            L = last.get(pid)
            if not L:
                print(ampm, pid, "NO PRICE")
                continue
            pnl = (L["px"] - ep) * 100
            age = (fc - L["ts"]).total_seconds()
            print(
                ampm,
                pid,
                "entry",
                ep,
                "last_px",
                L["px"],
                "ts",
                L["ts"].isoformat(),
                "age_sec",
                round(age, 1),
                "pnl",
                pnl,
                "line",
                L["line"],
                "etype",
                L["etype"],
            )


if __name__ == "__main__":
    main()
