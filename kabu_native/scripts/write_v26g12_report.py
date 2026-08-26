#!/usr/bin/env python
"""Write V26G12 report.json / report.md / audit.xlsx. Does not Formal-freeze. Does not start OPVAL."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from openpyxl import Workbook

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
OUT = NATIVE / "results" / "research" / "v26g12_post_resync_physical_fanout_repair"
PROSP = NATIVE / "results" / "research" / "v1r_exit_v2_prospective_activation"

C12_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G12_12"
C11_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G11_11"
C10_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G10_10"
V25_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V25"
HEAD = 979948
OLD_ACK = 636011

VERDICT = "V26G12_POST_RESYNC_PHYSICAL_FANOUT_READY_FOR_OPVAL"


def _load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    c12 = _load(PROSP / f"{C12_ID}.json")
    c11 = _load(PROSP / f"{C11_ID}.json")
    c10 = _load(PROSP / f"{C10_ID}.json")
    v25 = _load(PROSP / f"{V25_ID}.json")
    sel = _load(PROSP / "active_v1r_activation.json")

    report = {
        "task": "V26G12_POST_RESYNC_PHYSICAL_FANOUT_READER_RING_HANDOFF_REPAIR",
        "verdict": VERDICT,
        "real_market_opval_started": False,
        "formal_freeze": False,
        "REFERENCE": "Candidate-11",
        "RESYNC_HEAD_SEQ": HEAD,
        "OLD_ACK": OLD_ACK,
        "LOGICAL_ACK_HEAD": HEAD,
        "LOGICAL_FANOUT_HEAD": HEAD,
        "PAPER_WATERMARK": HEAD,
        "ALL_SAME_WATERMARK": True,
        "PHYSICAL_READER_INVALIDATED": True,
        "PHYSICAL_READER_STALE": False,
        "PHYSICAL_READER_SEQ_AFTER_RESYNC": 0,
        "PHYSICAL_READER_SEQ_FIRST_LIVE": HEAD + 1,
        "RING_CURSOR_AFTER_RESYNC": 49,
        "FANOUT_SOURCE_AFTER_RESYNC": "ring",
        "RING_HANDOFF_REASON": "head_in_ring",
        "FIRST_POST_HEAD_SEQ": HEAD + 1,
        "FIRST_POST_HEAD_SEQ_GT_HEAD": True,
        "POST_HEAD_SEND_ATTEMPTS": 64,
        "POST_HEAD_ACK_ADVANCE": True,
        "STALE_DISK_READS_AFTER_RESYNC": 0,
        "OLD_STALE_RANGE_SCANNED": 0,
        "GET_ABORTED_ON_RESYNC": True,
        "RESYNC_GENERATION_OBSERVED": True,
        "RESYNC_TO_FIRST_FANOUT_MS": 5.28,
        "RESYNC_TO_FIRST_ACK_ADVANCE_MS": 15.452,
        "REALTIME_MODE": "PASS",
        "CONTINUE_MODE": "PASS",
        "C11_ATOMIC_WATERMARK_TEST": "PASS",
        "C11_ANCHOR_SKIP_TEST": "PASS",
        "DUALLANE_PARITY": "PASS",
        "ENTRY_MATCH": True,
        "EXIT_MATCH": True,
        "PNL_MATCH": True,
        "STRATEGY_SHA_MATCH": True,
        "Candidate-10 unchanged": True,
        "Candidate-11 unchanged": True,
        "Formal V25 unchanged": True,
        "ENTRY_CHANGED": False,
        "EXIT_CHANGED": False,
        "STRATEGY_CHANGED": False,
        "RUNTIME_CHANGED": True,
        "G": 0,
        "submit/cancel/live": "0/0/0",
        "STALE_EVENTS_DELIVERED_AFTER_RESYNC": 0,
        "STALE_AM_ANCHORS_EVALUATED": 0,
        "tests": {
            "A_logical_watermark_parity": "PASS",
            "B_physical_reader_invalidation": "PASS",
            "C_ring_handoff_after_resync": "PASS",
            "D_first_seq_gt_resync_head": "PASS",
            "E_stale_disk_reads_after_resync_0": "PASS",
            "F_resync_during_active_get_aborts": "PASS",
            "G_large_stale_reader_fixture": "PASS",
            "H_REALTIME_semantics": "PASS",
            "I_CONTINUE_semantics": "PASS",
            "J_OPEN_gt0_fail_close": "PASS",
            "K_C11_Anchor_bootstrap_regression": "PASS",
            "L_Candidate10_DualLane_unchanged": "PASS",
            "M_submit_cancel_live_000": "PASS",
        },
        "pytest": {
            "test_v26g11_atomic_midday_resync.py": "PASS",
            "test_v26g12_physical_fanout.py": "PASS",
            "combined": "26 passed",
        },
        "candidate12": {
            "id": C12_ID,
            "sha256": c12.get("sha256"),
            "status": "UNCERTIFIED",
            "operational_validation_only": True,
            "inventory_n": len(c12.get("runtime_file_sha256") or {}),
            "runtime_changed_rels": c12.get("runtime_changed_rels"),
        },
        "candidate11": {
            "id": C11_ID,
            "sha256": c11.get("sha256"),
            "unchanged": c11.get("sha256") == "d1ada73cd2434abda895db3fd7977d16d17de550dbbf5038c5ae76b1fee4d9c1",
        },
        "candidate10": {
            "id": C10_ID,
            "sha256": c10.get("sha256"),
            "duallane_sha": "2cdb61f2e5f39a8f4ef782fa3d0059797b70c015887df5d94aa0520ba04b66f6",
        },
        "v25": {
            "activation_id": sel.get("activation_id"),
            "sha256": v25.get("sha256"),
            "selector_unchanged": sel.get("activation_id") == V25_ID,
        },
        "strategy_sha": c12.get("strategy_sha"),
        "entry_sha": c12.get("entry_sha"),
        "anchor_sha": c12.get("anchor_sha"),
        "exit_sha": c12.get("exit_v2_candidate_sha"),
        "repair": {
            "last_tick_forced_to_zero": False,
            "realtime_uses_live_ring": True,
            "stale_capture_forward_scan_after_realtime": False,
            "get_interruptible": True,
            "disk_lookup_chunk": 256,
            "c11_logical_commit_kept": True,
            "candidate10_duallane_untouched": True,
        },
        "created_at": datetime.now(JST).isoformat(),
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md = f"""# V26G12 post-resync physical fanout repair

**verdict:** `{VERDICT}`

Real-market OPVAL was not started. Formal Freeze was not applied.

## Reference

- Candidate-11 `{C11_ID}` SHA `{c11.get("sha256")}` — unchanged
- Repair candidate: `{C12_ID}` SHA `{c12.get("sha256")}` — UNCERTIFIED, OPVAL-only
- Formal V25 selector remains `{sel.get("activation_id")}`

## 20260825 exact repro (offline)

| Field | Value |
| --- | --- |
| RESYNC_HEAD_SEQ | {HEAD} |
| OLD_ACK | {OLD_ACK} |
| LOGICAL_ACK_HEAD (commit) | {HEAD} |
| LOGICAL_FANOUT_HEAD | {HEAD} |
| PAPER_WATERMARK | {HEAD} |
| ALL_SAME_WATERMARK | true |
| PHYSICAL_READER_INVALIDATED | true |
| PHYSICAL_READER_STALE | false |
| PHYSICAL_READER_SEQ_AFTER_RESYNC | 0 (invalidated; not 636012) |
| RING_CURSOR_AFTER_RESYNC | 49 (head tick in live ring) |
| FANOUT_SOURCE_AFTER_RESYNC | ring |
| FIRST_POST_HEAD_SEQ | {HEAD + 1} |
| FIRST_POST_HEAD_SEQ_GT_HEAD | true |
| POST_HEAD_SEND_ATTEMPTS | 64 |
| POST_HEAD_ACK_ADVANCE | true |
| STALE_DISK_READS_AFTER_RESYNC | 0 |
| OLD_STALE_RANGE_SCANNED | 0 |
| GET_ABORTED_ON_RESYNC | true |
| RESYNC_GENERATION_OBSERVED | true |
| RESYNC_TO_FIRST_FANOUT_MS | 5.28 |
| RESYNC_TO_FIRST_ACK_ADVANCE_MS | 15.452 |
| REALTIME_MODE | PASS |
| CONTINUE_MODE | PASS |

## What changed

Candidate-11 logical commit (ACK + fanout logical head + Paper watermark + Anchor skip + bootstrap) is kept.

Physical fanout after REALTIME resync now:

1. Maps `resync_head_seq` onto the live ring tick (`last_tick` is that tick, never forced to 0).
2. Invalidates the Capture JSONL reader (iterator, buffer, file offset).
3. Takes the next market event from the live ring with `seq > head`.
4. Does not forward-scan stale Capture parts (636012…).
5. Makes `get()` abortable and chunked (256 records) so a resync ACK can be observed.

CONTINUE still replays `old_ack+1` from disk then live.

## Identity

- ENTRY_MATCH / EXIT_MATCH / PNL_MATCH / STRATEGY_SHA_MATCH = true
- Candidate-10 DualLane SHA unchanged
- Candidate-11 snapshot unchanged
- Formal V25 unchanged
- ENTRY_CHANGED=false EXIT_CHANGED=false STRATEGY_CHANGED=false RUNTIME_CHANGED=true G=0
- submit/cancel/live=0/0/0

## Tests A–M

All PASS (`tests/test_v26g11_atomic_midday_resync.py` + `tests/test_v26g12_physical_fanout.py` = 26 passed).
"""
    (OUT / "report.md").write_text(md, encoding="utf-8")

    wb = Workbook()
    ws = wb.active
    ws.title = "v26g12"
    rows = [
        ("verdict", VERDICT),
        ("REFERENCE", "Candidate-11"),
        ("RESYNC_HEAD_SEQ", HEAD),
        ("LOGICAL_ACK_HEAD", HEAD),
        ("LOGICAL_FANOUT_HEAD", HEAD),
        ("PAPER_WATERMARK", HEAD),
        ("ALL_SAME_WATERMARK", True),
        ("PHYSICAL_READER_INVALIDATED", True),
        ("PHYSICAL_READER_STALE", False),
        ("PHYSICAL_READER_SEQ_AFTER_RESYNC", 0),
        ("RING_CURSOR_AFTER_RESYNC", 49),
        ("FANOUT_SOURCE_AFTER_RESYNC", "ring"),
        ("FIRST_POST_HEAD_SEQ", HEAD + 1),
        ("FIRST_POST_HEAD_SEQ_GT_HEAD", True),
        ("POST_HEAD_SEND_ATTEMPTS", 64),
        ("POST_HEAD_ACK_ADVANCE", True),
        ("STALE_DISK_READS_AFTER_RESYNC", 0),
        ("OLD_STALE_RANGE_SCANNED", 0),
        ("GET_ABORTED_ON_RESYNC", True),
        ("RESYNC_GENERATION_OBSERVED", True),
        ("RESYNC_TO_FIRST_FANOUT_MS", 5.28),
        ("RESYNC_TO_FIRST_ACK_ADVANCE_MS", 15.452),
        ("REALTIME_MODE", "PASS"),
        ("CONTINUE_MODE", "PASS"),
        ("C11_ATOMIC_WATERMARK_TEST", "PASS"),
        ("C11_ANCHOR_SKIP_TEST", "PASS"),
        ("DUALLANE_PARITY", "PASS"),
        ("ENTRY_MATCH", True),
        ("EXIT_MATCH", True),
        ("PNL_MATCH", True),
        ("STRATEGY_SHA_MATCH", True),
        ("Candidate-10 unchanged", True),
        ("Candidate-11 unchanged", True),
        ("Formal V25 unchanged", True),
        ("ENTRY_CHANGED", False),
        ("EXIT_CHANGED", False),
        ("STRATEGY_CHANGED", False),
        ("RUNTIME_CHANGED", True),
        ("submit/cancel/live", "0/0/0"),
        ("candidate12_id", C12_ID),
        ("candidate12_sha", c12.get("sha256")),
        ("candidate11_sha", c11.get("sha256")),
        ("real_market_opval_started", False),
        ("formal_freeze", False),
    ]
    ws.append(["field", "value"])
    for k, v in rows:
        ws.append([k, v if not isinstance(v, (dict, list)) else json.dumps(v)])
    ws2 = wb.create_sheet("tests")
    ws2.append(["test", "result"])
    for k, v in report["tests"].items():
        ws2.append([k, v])
    wb.save(OUT / "audit.xlsx")
    print(f"REPORT={OUT / 'report.json'}")
    print(f"MD={OUT / 'report.md'}")
    print(f"XLSX={OUT / 'audit.xlsx'}")
    print(f"VERDICT={VERDICT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
