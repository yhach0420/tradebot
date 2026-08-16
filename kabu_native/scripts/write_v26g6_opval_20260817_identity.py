#!/usr/bin/env python
"""Write OPERATIONAL_VALIDATION_ONLY identity for 20260817 live Paper OPVAL.

Not Formal. Not Candidate-6. Does not mutate V25 or Candidate-5.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

from small_paper.operational_validation import (
    OPVAL_ACTIVATION_ID,
    current_config_sha,
    current_git_head,
)
from small_paper.v1r_activation_binding import (
    CANDIDATE_STATUS_OPVAL,
    OUT,
    RUNTIME_DEPENDENCY_RELS,
    SELECTOR_PATH,
    SELECTOR_SCHEMA,
    V25_ACTIVATION_ID,
    audit_runtime_inventory_coverage,
    candidate_source_digest,
    collect_runtime_inventory,
    inventory_digest,
    manifest_content_sha,
    verify_generator_inventory_coverage,
    verify_manifest_self_sha,
    verify_runtime_inventory,
)

JST = ZoneInfo("Asia/Tokyo")
G6_DIR = NATIVE / "results" / "research" / "v26g6_targeted_rca"
V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
C5_SHA = "eb952288c4be2dcb586db877f09166d9197564b6658fb72525a66f038e41e8e6"
PRIOR = (
    ("V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G2_1", "d7e100dfd62bdb4da7fe055aa23f26c51c379348e8e8c9800052b1c54495cd62"),
    ("V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_2", "7238266f815458ef4a769be0c23c096922b06c77200c47f7adbf703fd45c286f"),
    ("V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_3", "f3c5100494cc7c4db53f65b6163626b213acf02e08025ced625097a2fd3e5648"),
    ("V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_4", "fa8811c5df4c4a0647a8a5311c5b1197b2c7a0a6a5d35f396383388f5ec88686"),
    ("V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G4_5", C5_SHA),
)


def main() -> int:
    G6_DIR.mkdir(parents=True, exist_ok=True)
    dest = G6_DIR / f"{OPVAL_ACTIVATION_ID}.json"
    selector_path = G6_DIR / "active_v1r_opval_20260817.json"
    if dest.resolve() == (OUT / f"{V25_ACTIVATION_ID}.json").resolve():
        print("REFUSE: would overwrite V25")
        return 2
    if selector_path.resolve() == SELECTOR_PATH.resolve():
        print("REFUSE: would overwrite Formal selector")
        return 2

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
        print("V1R_OPVAL_INVENTORY_COVERAGE_FAIL", json.dumps(cov, indent=2, default=str))
        return 2

    inv = collect_runtime_inventory(native_root=NATIVE)
    if len(inv) != len(RUNTIME_DEPENDENCY_RELS):
        print("REFUSE: inventory length != generator", len(inv), len(RUNTIME_DEPENDENCY_RELS))
        return 2

    body = {k: v for k, v in v25.items() if k != "sha256"}
    body.update(
        {
            "manifest_id": OPVAL_ACTIVATION_ID,
            "candidate_id": OPVAL_ACTIVATION_ID,
            "candidate_status": CANDIDATE_STATUS_OPVAL,
            "formal_paper_allowed": False,
            "prospective_allowed": False,
            "strategy_evaluation_allowed": False,
            "immutable": True,
            "paper_only": True,
            "order_enabled": False,
            "live_trading_enabled": False,
            "submit_cancel_live": "0/0/0",
            "submit": 0,
            "cancel": 0,
            "live": 0,
            "not_a_freeze_candidate": True,
            "not_candidate_6": True,
            "INVALID_FOR_STRATEGY_EVALUATION": True,
            "NOT_PROSPECTIVE_DAY1": True,
            "parent_activation_id": V25_ACTIVATION_ID,
            "parent_activation_sha": V25_SHA,
            "supersede_reason": "OPERATIONAL_VALIDATION_ONLY_20260817_NOT_FORMAL_NOT_CANDIDATE_6",
            "runtime_code_git_commit": current_git_head(),
            "runtime_file_sha256": inv,
            "runtime_inventory_digest": inventory_digest(inv),
            "candidate_source_digest": candidate_source_digest(inv, native_root=NATIVE),
            "config_sha256": current_config_sha(),
            "created_at": datetime.now(JST).isoformat(),
        }
    )
    body["sha256"] = manifest_content_sha(body)
    dest.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    loaded = json.loads(dest.read_text(encoding="utf-8"))
    ok, got, calc = verify_manifest_self_sha(loaded)
    if not (ok and got == calc == body["sha256"]):
        dest.unlink(missing_ok=True)
        print("REFUSE: opval self-sha")
        return 2
    inv_check = verify_runtime_inventory(loaded, native_root=NATIVE)
    gen = verify_generator_inventory_coverage(loaded)
    if not inv_check.get("ok") or not gen.get("ok"):
        dest.unlink(missing_ok=True)
        print("REFUSE: opval inventory", inv_check, gen)
        return 2

    after_formal = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    if after_formal.get("activation_id") != V25_ACTIVATION_ID or after_formal.get("activation_sha") != V25_SHA:
        dest.unlink(missing_ok=True)
        print("REFUSE: Formal selector mutated during write")
        return 2

    selector = {
        "schema": SELECTOR_SCHEMA,
        "activation_id": OPVAL_ACTIVATION_ID,
        "activation_sha": body["sha256"],
        "manifest_relpath": str(dest.resolve()).replace("\\", "/"),
        "note": (
            "OPERATIONAL_VALIDATION_ONLY selector for 20260817 live Paper. "
            "Not Formal. Not Candidate-6. INVALID_FOR_STRATEGY_EVALUATION. NOT_PROSPECTIVE_DAY1."
        ),
        "paper_mode": CANDIDATE_STATUS_OPVAL,
        "formal_paper_allowed": False,
        "prospective_allowed": False,
        "strategy_evaluation_allowed": False,
    }
    selector_path.write_text(json.dumps(selector, indent=2) + "\n", encoding="utf-8")
    print(f"OPVAL_ID={OPVAL_ACTIVATION_ID}")
    print(f"OPVAL_SHA={body['sha256']}")
    print(f"RUNTIME_INVENTORY_N={len(inv)}")
    print(f"RUNTIME_INVENTORY_DIGEST={body['runtime_inventory_digest']}")
    print(f"SOURCE_DIGEST={body['candidate_source_digest']}")
    print(f"CONFIG_SHA={body['config_sha256']}")
    print(f"GIT_HEAD={body['runtime_code_git_commit']}")
    print(f"STRATEGY_SHA={body.get('strategy_sha')}")
    print(f"ENTRY_SHA={body.get('entry_sha')}")
    print(f"EXIT_SHA={body.get('exit_v2_candidate_sha')}")
    print(f"UNIVERSE_BINDING_SHA={body.get('universe_binding_sha')}")
    print(f"SELECTOR={selector_path}")
    print(f"MANIFEST={dest}")
    print("STATUS=OPERATIONAL_VALIDATION_ONLY")
    print("UNCERTIFIED=true")
    print("formal_paper_allowed=false")
    print("prospective_allowed=false")
    print("strategy_evaluation_allowed=false")
    print("V25_UNCHANGED=true")
    print("NOT_CANDIDATE_5=true")
    print("NOT_CANDIDATE_6=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
