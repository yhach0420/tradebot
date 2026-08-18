#!/usr/bin/env python
"""Assert UNCERTIFIED Candidate-6 identity. Does not mutate V25 or Candidate-5."""
from __future__ import annotations

import json
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
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

C6_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G6_6"
C6_SELECTOR = OUT / "active_v1r_candidate_v26g6_6.json"
V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
C5_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G4_5"
C5_SHA = "eb952288c4be2dcb586db877f09166d9197564b6658fb72525a66f038e41e8e6"
C5_SELECTOR = OUT / "active_v1r_candidate_v26g4_5.json"


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

    if not C6_SELECTOR.is_file():
        fail.append("C6_SELECTOR_MISSING")
        return {"ok": False, "fail": fail, "candidate_id": C6_ID}

    osel = json.loads(C6_SELECTOR.read_text(encoding="utf-8"))
    if osel.get("activation_id") != C6_ID:
        fail.append(f"c6_id={osel.get('activation_id')}")
    oman = load_activation_manifest(selector=osel, out_dir=OUT)
    ook, ogot, ocalc = verify_manifest_self_sha(oman)
    want = osel.get("activation_sha")
    if not (ook and ogot == ocalc == want == oman.get("sha256")):
        fail.append("C6_SELF_SHA")
    if oman.get("candidate_id") != C6_ID:
        fail.append("C6_MANIFEST_ID")
    if oman.get("candidate_status") != CANDIDATE_STATUS_UNCERTIFIED:
        fail.append("C6_STATUS")
    if oman.get("formal_paper_allowed") is not False:
        fail.append("C6_FORMAL_PAPER")
    if oman.get("immutable") is not True:
        fail.append("C6_IMMUTABLE")
    inv = verify_runtime_inventory(oman, native_root=NATIVE)
    gen = verify_generator_inventory_coverage(oman)
    if not inv.get("ok"):
        fail.append(f"C6_INVENTORY {inv}")
    if not gen.get("ok"):
        fail.append(f"C6_GENERATOR {gen}")
    now = collect_runtime_inventory(native_root=NATIVE)
    man_inv = {str(k).replace("\\", "/"): str(v) for k, v in (oman.get("runtime_file_sha256") or {}).items()}
    mismatch = sorted(k for k, v in man_inv.items() if now.get(k) != v)
    if mismatch:
        fail.append(f"C6_STALE mismatch={mismatch[:8]}")
    formal = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    if formal.get("activation_id") != V25_ACTIVATION_ID:
        fail.append("FORMAL_SELECTOR_MUTATED")
    return {
        "ok": not fail,
        "fail": fail,
        "candidate_id": C6_ID,
        "opval_sha": osel.get("activation_sha"),
        "activation_sha": osel.get("activation_sha"),
        "candidate_source_digest": oman.get("candidate_source_digest"),
        "runtime_inventory_n": len(man_inv),
        "runtime_inventory_digest": oman.get("runtime_inventory_digest"),
        "runtime_code_git_commit": oman.get("runtime_code_git_commit"),
        "config_sha256": oman.get("config_sha256"),
        "strategy_sha": oman.get("strategy_sha"),
        "entry_sha": oman.get("entry_sha"),
        "exit_v2_candidate_sha": oman.get("exit_v2_candidate_sha"),
        "universe_binding_sha": oman.get("universe_binding_sha"),
        "v25_unchanged": "V25_MUTATED" not in fail,
        "c5_unchanged": "C5_SELECTOR_MUTATED" not in fail and "C5_SELF_SHA" not in fail,
        "formal_paper_allowed": False,
        "immutable": True,
        "UNCERTIFIED": True,
    }


def main() -> int:
    body = check()
    print(json.dumps(body, indent=2, default=str))
    return 0 if body.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
