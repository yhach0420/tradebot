#!/usr/bin/env python
"""Write UNCERTIFIED immutable V26-G3 Candidate-4 snapshot (atomic arm-file T0).

Does not mutate V25 / Candidate-1 / V26G3_2 / V26G3_3.
Does not Formal-freeze V26.
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
CANDIDATE_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_4"
SELECTOR_CANDIDATE = OUT / "active_v1r_candidate_v26g3_4.json"
CANDIDATE1_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G2_1"
CANDIDATE1_SHA = "d7e100dfd62bdb4da7fe055aa23f26c51c379348e8e8c9800052b1c54495cd62"
CANDIDATE2_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_2"
CANDIDATE2_SHA = "7238266f815458ef4a769be0c23c096922b06c77200c47f7adbf703fd45c286f"
CANDIDATE3_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_3"
CANDIDATE3_SHA = "f3c5100494cc7c4db53f65b6163626b213acf02e08025ced625097a2fd3e5648"
V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"


def _sha(path: Path) -> str:
    return file_sha256(path) if path.is_file() else ""


def main() -> int:
    dest = OUT / f"{CANDIDATE_ID}.json"
    if dest.is_file() or SELECTOR_CANDIDATE.is_file():
        print("REFUSE_OVERWRITE: candidate-4 snapshot already exists", dest, SELECTOR_CANDIDATE)
        return 2
    pins = [
        (CANDIDATE1_ID, CANDIDATE1_SHA),
        (CANDIDATE2_ID, CANDIDATE2_SHA),
        (CANDIDATE3_ID, CANDIDATE3_SHA),
    ]
    for cid, sha in pins:
        p = OUT / f"{cid}.json"
        if not p.is_file():
            print("REFUSE: missing", cid)
            return 2
        body = json.loads(p.read_text(encoding="utf-8"))
        if body.get("sha256") != sha or body.get("candidate_id") != cid:
            print("REFUSE: identity mutated", cid)
            return 2

    v25_sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    v25 = json.loads((OUT / f"{V25_ACTIVATION_ID}.json").read_text(encoding="utf-8"))
    if v25_sel.get("activation_id") != V25_ACTIVATION_ID or v25.get("sha256") != V25_SHA:
        print("REFUSE: V25 selector/manifest mutated")
        return 2
    if manifest_content_sha(v25) != V25_SHA:
        print("REFUSE: V25 manifest self-sha drift")
        return 2

    cov = audit_runtime_inventory_coverage(native_root=NATIVE)
    if not cov.get("ok") or cov.get("runtime_critical_uncovered_files"):
        print("V1R_V26G3_INVENTORY_COVERAGE_FAIL", json.dumps(cov, indent=2, default=str))
        return 2

    inv = collect_runtime_inventory(native_root=NATIVE)
    if len(inv) != len(RUNTIME_DEPENDENCY_RELS):
        print("REFUSE: inventory length != generator", len(inv), len(RUNTIME_DEPENDENCY_RELS))
        return 2

    body = {k: v for k, v in v25.items() if k != "sha256"}
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=str(REPO), text=True).strip()
    launch = {
        "run_paper_trade_checked.bat": _sha(REPO / "run_paper_trade_checked.bat"),
        "run_paper_trade_checked.ps1": _sha(NATIVE / "scripts" / "run_paper_trade_checked.ps1"),
        "run_paper_trade.bat": _sha(REPO / "run_paper_trade.bat"),
        "run_paper_full_day_certification.py": _sha(NATIVE / "scripts" / "run_paper_full_day_certification.py"),
    }
    cfg = NATIVE / "configs" / "small_paper_pilot.yaml"
    if not cfg.is_file():
        cfg = NATIVE / "config" / "small_paper_pilot.yaml"
    body.update(
        {
            "manifest_id": CANDIDATE_ID,
            "candidate_id": CANDIDATE_ID,
            "candidate_status": CANDIDATE_STATUS_UNCERTIFIED,
            "formal_paper_allowed": False,
            "immutable": True,
            "parent_activation_id": V25_ACTIVATION_ID,
            "parent_activation_sha": V25_SHA,
            "parent_v25_activation_id": V25_ACTIVATION_ID,
            "parent_v25_activation_sha": V25_SHA,
            "parent_activation_status": "IMMUTABLE_FORMAL_PARENT",
            "supersede_reason": "V26G3_UNCERTIFIED_RUNTIME_FIX_CANDIDATE4_ATOMIC_ARM_T0",
            "runtime_code_git_commit": head,
            "runtime_file_sha256": inv,
            "runtime_inventory_digest": inventory_digest(inv),
            "candidate_source_digest": candidate_source_digest(inv, native_root=NATIVE),
            "launch_surface_sha256": launch,
            "config_sha256": _sha(cfg) if cfg.is_file() else "",
            "config_path": str(cfg.relative_to(NATIVE)).replace("\\", "/") if cfg.is_file() else "",
            "created_at": datetime.now(JST).isoformat(),
            "hash_policy": {
                "sot": "working_tree_path_read_bytes_sha256",
                "normalize_newlines": False,
                "selector_excluded_from_inventory": True,
                "binding_module": "small_paper.v1r_activation_binding",
            },
            "parent_failed_candidate_id": CANDIDATE1_ID,
            "parent_failed_candidate_sha": CANDIDATE1_SHA,
            "parent_failed_candidate_status": "FAILED_AUDIT_ONLY",
            "parent_premature_candidate_id": CANDIDATE2_ID,
            "parent_premature_candidate_sha": CANDIDATE2_SHA,
            "parent_aborted_candidate_id": CANDIDATE3_ID,
            "parent_aborted_candidate_sha": CANDIDATE3_SHA,
            "parent_aborted_candidate_status": "UNCERTIFIED_PREFLIGHT_ABORTED_ARM_FILE_TEAR",
        }
    )
    body["sha256"] = manifest_content_sha(body)
    dest.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    ok, got, calc = verify_manifest_self_sha(loaded)
    if not (ok and got == calc == body["sha256"]):
        dest.unlink(missing_ok=True)
        print("REFUSE: candidate self-sha")
        return 2
    inv_check = verify_runtime_inventory(loaded, native_root=NATIVE)
    gen = verify_generator_inventory_coverage(loaded)
    if not inv_check.get("ok") or not gen.get("ok"):
        dest.unlink(missing_ok=True)
        print("REFUSE: candidate inventory", inv_check, gen)
        return 2

    selector = {
        "schema": SELECTOR_SCHEMA,
        "activation_id": CANDIDATE_ID,
        "activation_sha": body["sha256"],
        "manifest_relpath": f"{CANDIDATE_ID}.json",
        "note": "Identity-only UNCERTIFIED candidate-4 selector; not the active Formal selector.",
    }
    SELECTOR_CANDIDATE.write_text(json.dumps(selector, indent=2) + "\n", encoding="utf-8")

    v25_after = json.loads((OUT / f"{V25_ACTIVATION_ID}.json").read_text(encoding="utf-8"))
    sel_after = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    if v25_after.get("sha256") != V25_SHA or sel_after.get("activation_id") != V25_ACTIVATION_ID:
        print("REFUSE: V25 mutated during candidate write")
        return 2
    for cid, sha in pins:
        got = json.loads((OUT / f"{cid}.json").read_text(encoding="utf-8")).get("sha256")
        if got != sha:
            print("REFUSE: prior candidate mutated", cid)
            return 2

    print(f"CANDIDATE_ID={CANDIDATE_ID}")
    print(f"CANDIDATE_SHA={body['sha256']}")
    print(f"RUNTIME_INVENTORY_N={len(inv)}")
    print(f"RUNTIME_INVENTORY_DIGEST={body['runtime_inventory_digest']}")
    print(f"CANDIDATE_SOURCE_DIGEST={body['candidate_source_digest']}")
    print(f"SELECTOR={SELECTOR_CANDIDATE}")
    print(f"MANIFEST={dest}")
    print("V25_UNCHANGED=true")
    print("PRIOR_CANDIDATES_UNCHANGED=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
