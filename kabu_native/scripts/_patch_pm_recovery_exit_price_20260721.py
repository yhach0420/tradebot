#!/usr/bin/env python3
"""Patch PM recovery_forced_close rows missing exit_price (20260721)."""
from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
SESS = Path(__file__).resolve().parents[1] / "results" / "small_paper" / "20260721" / "live_session_124342"


def main() -> int:
    jsonl = SESS / "small_paper_events.jsonl"
    csv_path = SESS / "small_paper_events.csv"
    bak = SESS / f"recovery_price_patch_backup_{datetime.now(JST).strftime('%H%M%S')}"
    bak.mkdir(parents=True, exist_ok=True)
    shutil.copy2(jsonl, bak / jsonl.name)
    if csv_path.is_file():
        shutil.copy2(csv_path, bak / csv_path.name)

    rows: list[dict] = []
    patched = 0
    with jsonl.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            if e.get("event_type") == "observer_exit" and e.get("exit_reason") == "recovery_forced_close":
                entry = e.get("entry_price")
                cur = e.get("current_price", entry)
                if e.get("exit_price") in (None, ""):
                    e["exit_price"] = cur if cur is not None else entry
                    patched += 1
                e["pnl_pct"] = float(e.get("pnl_pct") or 0.0)
                e["pnl_yen_100"] = float(e.get("pnl_yen_100") or 0.0)
                e["actual_pnl_yen_100"] = float(e.get("actual_pnl_yen_100") or 0.0)
                e["recovery_forced_close"] = True
                e.setdefault("structural_exit_reason", "recovery_forced_close")
            rows.append(e)

    with jsonl.open("w", encoding="utf-8") as f:
        for e in rows:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            fieldnames = list(reader.fieldnames or [])
            csv_rows = list(reader)
        for r in csv_rows:
            if r.get("event_type") == "observer_exit" and r.get("exit_reason") == "recovery_forced_close":
                entry = r.get("entry_price")
                cur = r.get("current_price") or entry
                if not r.get("exit_price"):
                    r["exit_price"] = str(cur) if cur is not None else str(entry)
                if not r.get("pnl_pct"):
                    r["pnl_pct"] = "0.0"
                if "pnl_yen_100" in fieldnames and not r.get("pnl_yen_100"):
                    r["pnl_yen_100"] = "0"
                if "actual_pnl_yen_100" in fieldnames and not r.get("actual_pnl_yen_100"):
                    r["actual_pnl_yen_100"] = "0"
        with csv_path.open("w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            w.writerows(csv_rows)

    summary_path = SESS / "small_paper_summary.json"
    s = json.loads(summary_path.read_text(encoding="utf-8"))
    exits = [e for e in rows if e.get("event_type") == "observer_exit"]
    rec = [e for e in exits if e.get("exit_reason") == "recovery_forced_close"]
    normal = [e for e in exits if e.get("exit_reason") != "recovery_forced_close"]
    accepted = [e for e in rows if e.get("event_type") == "accepted"]
    s["accepted_count"] = len(accepted)
    s["observer_exit_count"] = len(exits)
    s["normal_exit_count"] = len(normal)
    s["recovery_forced_close_count"] = len(rec)
    s["active_positions"] = 0
    s["pm_recovery_exit_price_patched"] = True
    s["pm_recovery_exit_price_patched_at"] = datetime.now(JST).isoformat()
    summary_path.write_text(json.dumps(s, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "patched_exit_price": patched,
                "accepted": len(accepted),
                "exits": len(exits),
                "recovery": len(rec),
                "backup": str(bak),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
