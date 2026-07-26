#!/usr/bin/env python3
"""PM start helpers for 20260722 — protect AM SHA + status dump."""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
AM = NATIVE / "results" / "small_paper" / "20260722" / "live_session_075904"
OUT = NATIVE / "results" / "reports" / "pm_start_20260722"


def sha256_file(p: Path) -> dict:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return {"sha256": h.hexdigest(), "size": p.stat().st_size}


def protect_am() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    s = json.loads((AM / "small_paper_summary.json").read_text(encoding="utf-8"))
    seal = json.loads((AM / "session_seal.json").read_text(encoding="utf-8"))
    acc = []
    ex = []
    for line in (AM / "small_paper_events.jsonl").open(encoding="utf-8"):
        e = json.loads(line)
        et = e.get("event_type")
        if et == "accepted":
            acc.append(e)
        elif et == "observer_exit":
            ex.append(e)
    ex_pids = {str(x.get("position_id") or "") for x in ex if x.get("position_id")}
    ex_keys = {(x.get("symbol"), x.get("entry_time")) for x in ex}
    orph = [
        a
        for a in acc
        if str(a.get("position_id") or "") not in ex_pids
        and (a.get("symbol"), a.get("entry_time")) not in ex_keys
    ]
    sha = {}
    for name in (
        "small_paper_summary.json",
        "small_paper_events.jsonl",
        "session_seal.json",
        "small_paper_summary_am.json",
        "small_paper_positions.csv",
    ):
        p = AM / name
        if p.is_file():
            sha[name] = sha256_file(p)
    report = {
        "protected_at": datetime.now(JST).isoformat(timespec="seconds"),
        "session": str(AM),
        "session_seal_status": seal.get("session_seal_status"),
        "ended_at": s.get("ended_at"),
        "stop_reason": s.get("stop_reason"),
        "accepted_count": s.get("accepted_count"),
        "observer_exit_count": s.get("observer_exit_count") or len(ex),
        "total_pnl_yen_100": s.get("total_pnl_yen_100"),
        "open_slots_end": s.get("open_slots_end"),
        "orphan_open": len(orph),
        "accepted_n": len(acc),
        "exit_n": len(ex),
        "sha256": sha,
        "note": "AM protected — do not re-finalize or re-send AM Summary",
    }
    (OUT / "am_protected_sha.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    print(json.dumps(protect_am(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
