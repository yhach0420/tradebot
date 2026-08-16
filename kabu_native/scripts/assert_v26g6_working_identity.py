"""V26-G6 working-tree identity. Does not require Candidate-5 inventory to match this tree.

Candidate-5 remains immutable failed-preflight evidence. This tree is G6 work.
"""
from __future__ import annotations

import json
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
import sys

sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

from small_paper.v1r_activation_binding import (
    CANDIDATE_STATUS_UNCERTIFIED,
    OUT,
    SELECTOR_PATH,
    V25_ACTIVATION_ID,
    collect_runtime_inventory,
    load_activation_manifest,
    load_active_selector,
    verify_generator_inventory_coverage,
    verify_manifest_self_sha,
    verify_runtime_inventory,
)

V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
C5_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G4_5"
C5_SHA = "eb952288c4be2dcb586db877f09166d9197564b6658fb72525a66f038e41e8e6"
C5_SELECTOR = OUT / "active_v1r_candidate_v26g4_5.json"
WORKING_ID = "V1R_EXIT_V2_PAPER_PRIMARY_WORKING_V26G6"
WORKING_SELECTOR = NATIVE / "results" / "research" / "v26g6_targeted_rca" / "active_v1r_working_v26g6.json"


def check() -> dict:
    fail: list[str] = []
    v25_sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    v25 = json.loads((OUT / f"{V25_ACTIVATION_ID}.json").read_text(encoding="utf-8"))
    if v25_sel.get("activation_id") != V25_ACTIVATION_ID or v25.get("sha256") != V25_SHA:
        fail.append("V25_MUTATED")
    ok25, got25, calc25 = verify_manifest_self_sha(v25)
    if not (ok25 and got25 == V25_SHA and calc25 == V25_SHA):
        fail.append("V25_SELF_SHA")

    c5_sel = load_active_selector(path=C5_SELECTOR)
    if c5_sel.get("activation_id") != C5_ID or c5_sel.get("activation_sha") != C5_SHA:
        fail.append("C5_SELECTOR_MUTATED")
    c5 = load_activation_manifest(selector=c5_sel, out_dir=OUT)
    ok5, got5, calc5 = verify_manifest_self_sha(c5)
    if not (ok5 and got5 == C5_SHA and calc5 == C5_SHA):
        fail.append("C5_SELF_SHA")
    if c5.get("candidate_id") != C5_ID or c5.get("sha256") != C5_SHA:
        fail.append("C5_MANIFEST_MUTATED")
    if c5.get("immutable") is not True:
        fail.append("C5_IMMUTABLE")

    if not WORKING_SELECTOR.is_file():
        fail.append("WORKING_SELECTOR_MISSING")
        return {"ok": False, "fail": fail, "working_id": WORKING_ID}

    wsel = json.loads(WORKING_SELECTOR.read_text(encoding="utf-8"))
    if wsel.get("activation_id") != WORKING_ID:
        fail.append(f"working_id={wsel.get('activation_id')}")
    wman = load_activation_manifest(selector=wsel, out_dir=OUT)
    wok, wgot, wcalc = verify_manifest_self_sha(wman)
    if not (wok and wgot == wcalc == wsel.get("activation_sha")):
        fail.append("WORKING_SELF_SHA")
    if wman.get("candidate_id") != WORKING_ID:
        fail.append("WORKING_MANIFEST_ID")
    if wman.get("candidate_status") != CANDIDATE_STATUS_UNCERTIFIED:
        fail.append("WORKING_STATUS")
    if wman.get("immutable") is True:
        fail.append("WORKING_MUST_NOT_BE_IMMUTABLE")
    if wman.get("formal_paper_allowed") is not False:
        fail.append("WORKING_FORMAL_PAPER")
    if wman.get("not_a_freeze_candidate") is not True:
        fail.append("WORKING_NOT_A_FREEZE_CANDIDATE")
    inv = verify_runtime_inventory(wman, native_root=NATIVE)
    gen = verify_generator_inventory_coverage(wman)
    if not inv.get("ok"):
        fail.append(f"WORKING_INVENTORY {inv}")
    if not gen.get("ok"):
        fail.append(f"WORKING_GENERATOR {gen}")
    now = collect_runtime_inventory(native_root=NATIVE)
    man_inv = {str(k).replace("\\", "/"): str(v) for k, v in (wman.get("runtime_file_sha256") or {}).items()}
    mismatch = sorted(k for k, v in man_inv.items() if now.get(k) != v)
    if mismatch:
        fail.append(f"WORKING_STALE_REWRITE_NEEDED mismatch={mismatch[:8]}")
    return {
        "ok": not fail,
        "fail": fail,
        "working_id": WORKING_ID,
        "working_sha": wsel.get("activation_sha"),
        "v25_unchanged": "V25_MUTATED" not in fail,
        "c5_unchanged": "C5_SELECTOR_MUTATED" not in fail and "C5_MANIFEST_MUTATED" not in fail,
        "selector": str(WORKING_SELECTOR),
    }


def main() -> int:
    body = check()
    print(json.dumps(body, indent=2, default=str))
    return 0 if body.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
