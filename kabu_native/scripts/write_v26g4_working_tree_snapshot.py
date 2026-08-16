#!/usr/bin/env python
"""Write a working-tree UNCERTIFIED snapshot for V26-G4 targeted RCA runs.

Not Candidate-5. Not Formal. Does not live in the freeze candidate ID series.
Allowed to overwrite itself as source changes. Does not mutate V25 or G2/G3 candidates.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

from small_paper.v1r_activation_binding import (
    CANDIDATE_STATUS_UNCERTIFIED,
    OUT,
    RUNTIME_DEPENDENCY_RELS,
    SELECTOR_PATH,
    SELECTOR_SCHEMA,
    V25_ACTIVATION_ID,
    audit_runtime_inventory_coverage,
    candidate_source_digest,
    collect_runtime_inventory,
    file_sha256,
    inventory_digest,
    manifest_content_sha,
    verify_generator_inventory_coverage,
    verify_manifest_self_sha,
    verify_runtime_inventory,
)

JST = ZoneInfo("Asia/Tokyo")
G4_DIR = NATIVE / "results" / "research" / "v26g4_targeted_runtime_fix"
WORKING_ID = "V1R_EXIT_V2_PAPER_PRIMARY_WORKING_V26G4"
V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
PRIOR = (
    ("V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G2_1", "d7e100dfd62bdb4da7fe055aa23f26c51c379348e8e8c9800052b1c54495cd62"),
    ("V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_2", "7238266f815458ef4a769be0c23c096922b06c77200c47f7adbf703fd45c286f"),
    ("V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_3", "f3c5100494cc7c4db53f65b6163626b213acf02e08025ced625097a2fd3e5648"),
    ("V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_4", "fa8811c5df4c4a0647a8a5311c5b1197b2c7a0a6a5d35f396383388f5ec88686"),
)


def _sha(path: Path) -> str:
    return file_sha256(path) if path.is_file() else ""


def main() -> int:
    G4_DIR.mkdir(parents=True, exist_ok=True)
    dest = G4_DIR / f"{WORKING_ID}.json"
    selector_path = G4_DIR / "active_v1r_working_v26g4.json"
    v25_sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    v25 = json.loads((OUT / f"{V25_ACTIVATION_ID}.json").read_text(encoding="utf-8"))
    if v25_sel.get("activation_id") != V25_ACTIVATION_ID or v25.get("sha256") != V25_SHA:
        print("REFUSE: V25 selector/manifest mutated")
        return 2
    for cid, sha in PRIOR:
        p = OUT / f"{cid}.json"
        if not p.is_file():
            print("REFUSE: missing", cid)
            return 2
        body = json.loads(p.read_text(encoding="utf-8"))
        if body.get("sha256") != sha or body.get("candidate_id") != cid:
            print("REFUSE: identity mutated", cid)
            return 2

    cov = audit_runtime_inventory_coverage(native_root=NATIVE)
    if not cov.get("ok") or cov.get("runtime_critical_uncovered_files"):
        print("V1R_V26G4_INVENTORY_COVERAGE_FAIL", json.dumps(cov, indent=2, default=str))
        return 2

    inv = collect_runtime_inventory(native_root=NATIVE)
    if len(inv) != len(RUNTIME_DEPENDENCY_RELS):
        print("REFUSE: inventory length != generator", len(inv), len(RUNTIME_DEPENDENCY_RELS))
        return 2

    body = {k: v for k, v in v25.items() if k != "sha256"}
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(REPO), text=True).strip()
    cfg = NATIVE / "configs" / "small_paper_pilot.yaml"
    if not cfg.is_file():
        cfg = NATIVE / "config" / "small_paper_pilot.yaml"
    body.update(
        {
            "manifest_id": WORKING_ID,
            "candidate_id": WORKING_ID,
            "candidate_status": CANDIDATE_STATUS_UNCERTIFIED,
            "formal_paper_allowed": False,
            "immutable": False,
            "working_tree_only": True,
            "not_a_freeze_candidate": True,
            "parent_activation_id": V25_ACTIVATION_ID,
            "parent_activation_sha": V25_SHA,
            "supersede_reason": "V26G4_TARGETED_WORKING_TREE_NOT_CANDIDATE_5",
            "runtime_code_git_commit": head,
            "runtime_file_sha256": inv,
            "runtime_inventory_digest": inventory_digest(inv),
            "candidate_source_digest": candidate_source_digest(inv, native_root=NATIVE),
            "config_sha256": _sha(cfg) if cfg.is_file() else "",
            "created_at": datetime.now(JST).isoformat(),
        }
    )
    body["sha256"] = manifest_content_sha(body)
    dest.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    ok, got, calc = verify_manifest_self_sha(loaded)
    if not (ok and got == calc == body["sha256"]):
        dest.unlink(missing_ok=True)
        print("REFUSE: working self-sha")
        return 2
    inv_check = verify_runtime_inventory(loaded, native_root=NATIVE)
    gen = verify_generator_inventory_coverage(loaded)
    if not inv_check.get("ok") or not gen.get("ok"):
        dest.unlink(missing_ok=True)
        print("REFUSE: working inventory", inv_check, gen)
        return 2

    selector = {
        "schema": SELECTOR_SCHEMA,
        "activation_id": WORKING_ID,
        "activation_sha": body["sha256"],
        "manifest_relpath": str(dest.resolve()),
        "note": "Working-tree selector for V26-G4 targeted RCA only. Not Candidate-5. Not Formal.",
    }
    selector_path.write_text(json.dumps(selector, indent=2) + "\n", encoding="utf-8")
    print(f"WORKING_ID={WORKING_ID}")
    print(f"WORKING_SHA={body['sha256']}")
    print(f"RUNTIME_INVENTORY_N={len(inv)}")
    print(f"RUNTIME_INVENTORY_DIGEST={body['runtime_inventory_digest']}")
    print(f"SELECTOR={selector_path}")
    print(f"MANIFEST={dest}")
    print("V25_UNCHANGED=true")
    print("NOT_CANDIDATE_5=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
