#!/usr/bin/env python
"""Write OPERATIONAL_VALIDATION_ONLY current-trading-day working identity.

Not Candidate-7. Not Formal V26. Does not overwrite Candidate-6 or Formal V25.
Run after OPVAL date-binding source changes.
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

from small_paper.operational_validation import OPVAL_ACTIVATION_ID, current_config_sha, current_git_head
from small_paper.v1r_activation_binding import (
    CANDIDATE_STATUS_OPVAL,
    OUT,
    SELECTOR_PATH,
    SELECTOR_SCHEMA,
    V25_ACTIVATION_ID,
    candidate_source_digest,
    collect_runtime_inventory,
    inventory_digest,
    manifest_content_sha,
    verify_manifest_self_sha,
    verify_runtime_inventory,
)

JST = ZoneInfo("Asia/Tokyo")
V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
C6_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G6_6"
C6_SHA = "3ac5cf4b1788f52d38aeb0b7ea059f847f89cf4e026c844ec64d96713fa3563d"
DEST_DIR = NATIVE / "results" / "research" / "v26g6_opval_launcher"
SELECTOR_DEST = DEST_DIR / "active_v1r_opval_current_trading_day.json"
MANIFEST_DEST = DEST_DIR / f"{OPVAL_ACTIVATION_ID}.json"


def main() -> int:
    v25_sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    v25 = json.loads((OUT / f"{V25_ACTIVATION_ID}.json").read_text(encoding="utf-8"))
    c6 = json.loads((OUT / f"{C6_ID}.json").read_text(encoding="utf-8"))
    if v25_sel.get("activation_id") != V25_ACTIVATION_ID or v25.get("sha256") != V25_SHA:
        print("REFUSE: V25 selector/manifest mutated")
        return 2
    if c6.get("sha256") != C6_SHA:
        print("REFUSE: Candidate-6 manifest mutated")
        return 2

    inv = collect_runtime_inventory(native_root=NATIVE)
    body = {k: v for k, v in v25.items() if k != "sha256"}
    body.update(
        {
            "manifest_id": OPVAL_ACTIVATION_ID,
            "candidate_id": OPVAL_ACTIVATION_ID,
            "candidate_status": CANDIDATE_STATUS_OPVAL,
            "formal_paper_allowed": False,
            "prospective_allowed": False,
            "strategy_evaluation_allowed": False,
            "immutable": False,
            "paper_only": True,
            "order_enabled": False,
            "live_trading_enabled": False,
            "submit_cancel_live": "0/0/0",
            "submit": 0,
            "cancel": 0,
            "live": 0,
            "INVALID_FOR_STRATEGY_EVALUATION": True,
            "NOT_PROSPECTIVE_DAY1": True,
            "not_a_freeze_candidate": True,
            "not_candidate_6": True,
            "not_candidate_7": True,
            "date_policy": "per_run_canonical_trading_session",
            "legacy_opval_pinned_date": "20260817",
            "parent_activation_id": V25_ACTIVATION_ID,
            "parent_activation_sha": V25_SHA,
            "parent_candidate6_id": C6_ID,
            "parent_candidate6_sha": C6_SHA,
            "runtime_code_git_commit": current_git_head(),
            "config_sha256": current_config_sha(),
            "runtime_file_sha256": inv,
            "runtime_inventory_digest": inventory_digest(inv),
            "candidate_source_digest": candidate_source_digest(inv, native_root=NATIVE),
            "created_at": datetime.now(JST).isoformat(timespec="milliseconds"),
            "hash_policy": {
                "sot": "working_tree_path_read_bytes_sha256",
                "normalize_newlines": False,
                "selector_excluded_from_inventory": True,
                "binding_module": "small_paper.v1r_activation_binding",
            },
        }
    )
    body["sha256"] = manifest_content_sha(body)
    DEST_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST_DEST.write_text(json.dumps(body, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    loaded = json.loads(MANIFEST_DEST.read_text(encoding="utf-8"))
    ok, got, calc = verify_manifest_self_sha(loaded)
    if not (ok and got == calc == body["sha256"]):
        MANIFEST_DEST.unlink(missing_ok=True)
        print("REFUSE: OPVAL self-sha")
        return 2
    inv_check = verify_runtime_inventory(loaded, native_root=NATIVE)
    if not inv_check.get("ok"):
        MANIFEST_DEST.unlink(missing_ok=True)
        print("REFUSE: OPVAL inventory", inv_check)
        return 2

    selector = {
        "schema": SELECTOR_SCHEMA,
        "activation_id": OPVAL_ACTIVATION_ID,
        "activation_sha": body["sha256"],
        "manifest_relpath": str(MANIFEST_DEST.resolve()).replace("\\", "/"),
        "note": "OPERATIONAL_VALIDATION_ONLY current-trading-day working identity. Not Formal. Not Candidate-6. Not Candidate-7.",
        "paper_mode": CANDIDATE_STATUS_OPVAL,
        "formal_paper_allowed": False,
        "prospective_allowed": False,
        "strategy_evaluation_allowed": False,
    }
    SELECTOR_DEST.write_text(json.dumps(selector, indent=2) + "\n", encoding="utf-8")

    v25_after = json.loads((OUT / f"{V25_ACTIVATION_ID}.json").read_text(encoding="utf-8"))
    sel_after = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    c6_after = json.loads((OUT / f"{C6_ID}.json").read_text(encoding="utf-8"))
    if v25_after.get("sha256") != V25_SHA or sel_after.get("activation_id") != V25_ACTIVATION_ID:
        print("REFUSE: V25 mutated during OPVAL write")
        return 2
    if c6_after.get("sha256") != C6_SHA:
        print("REFUSE: Candidate-6 mutated during OPVAL write")
        return 2

    print(f"OPVAL_ID={OPVAL_ACTIVATION_ID}")
    print(f"OPVAL_SHA={body['sha256']}")
    print(f"RUNTIME_INVENTORY_N={len(inv)}")
    print(f"RUNTIME_INVENTORY_DIGEST={body['runtime_inventory_digest']}")
    print(f"SOURCE_DIGEST={body['candidate_source_digest']}")
    print(f"GIT_HEAD={body['runtime_code_git_commit']}")
    print(f"CONFIG_SHA={body['config_sha256']}")
    print(f"STRATEGY_SHA={body.get('strategy_sha')}")
    print(f"ENTRY_SHA={body.get('entry_sha')}")
    print(f"EXIT_SHA={body.get('exit_v2_candidate_sha')}")
    print(f"UNIVERSE_BINDING_SHA={body.get('universe_binding_sha')}")
    print(f"SELECTOR={SELECTOR_DEST}")
    print(f"MANIFEST={MANIFEST_DEST}")
    print("V25_UNCHANGED=true")
    print("CANDIDATE6_UNCHANGED=true")
    print("NOT_CANDIDATE_7=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
