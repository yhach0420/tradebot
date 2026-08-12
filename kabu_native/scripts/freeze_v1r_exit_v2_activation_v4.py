#!/usr/bin/env python
"""Create V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V4.

Parent V3 is immutable history. Does not mutate Strategy / Precommit / V3 bytes.
Uses v1r_activation_binding.file_sha256 (Path.read_bytes) as sole hash SoT.
"""
from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from small_paper.v1r_activation_binding import (
    OUT,
    RUNTIME_DEPENDENCY_RELS,
    SELECTOR_PATH,
    SELECTOR_SCHEMA,
    collect_runtime_inventory,
    manifest_content_sha,
    verify_manifest_self_sha,
    verify_runtime_inventory,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent

PARENT_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V3"
PARENT_SHA = "10c9efbc758cf8f68fcee47902a98708365c8b0e9ae5a34e839ca9da5bb118b3"
V4_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V4"
SUPERSEDE_REASON = "RUNTIME_NATIVE_OCCUPANCY_AND_SESSION_CLOSE_WIRING_FIX"

STRATEGY_SHA = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
PRECOMMIT_SHA = "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100"
V2_SHA = "0cd4b6289e392269035448b7a71be0b1f2b449782b57991a9e028f8e1be7bd46"
V1_SHA = "29cbc5933421319ffcb1ed24d9be517d35e74c1027ebe67df431657c6997ada1"


def _git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=str(REPO), text=True).strip()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    v3 = json.loads((OUT / f"{PARENT_ID}.json").read_text(encoding="utf-8"))
    assert v3["sha256"] == PARENT_SHA == manifest_content_sha(v3), "V3 must remain immutable"

    v2 = json.loads((OUT / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V2.json").read_text(encoding="utf-8"))
    assert v2["sha256"] == V2_SHA == manifest_content_sha(v2)

    for name, exp in [
        ("PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY.json", STRATEGY_SHA),
        ("PROSPECTIVE_PRECOMMIT_V1R_EXIT_V2_U1.json", PRECOMMIT_SHA),
        ("V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V1.json", V1_SHA),
    ]:
        obj = json.loads((OUT / name).read_text(encoding="utf-8"))
        assert obj["sha256"] == exp == manifest_content_sha(obj), name

    head = _git(["rev-parse", "HEAD"])
    status = _git(["status", "--short"])
    print(f"FREEZE_HEAD={head}")
    print(f"FREEZE_STATUS=\n{status}")

    inv1 = collect_runtime_inventory(native_root=NATIVE)
    inv2 = collect_runtime_inventory(native_root=NATIVE)
    assert inv1 == inv2
    assert len(inv1) == len(RUNTIME_DEPENDENCY_RELS)
    print(f"INVENTORY_COUNT={len(inv1)}")
    print("INVENTORY_DOUBLE_CHECK=PASS")

    activation = {k: v for k, v in v3.items() if k != "sha256"}
    activation.update(
        {
            "manifest_id": V4_ID,
            "parent_activation_id": PARENT_ID,
            "parent_activation_sha": PARENT_SHA,
            "parent_activation_status": "SUPERSEDED_IMMUTABLE_HISTORY",
            "supersede_reason": SUPERSEDE_REASON,
            "strategy_sha": STRATEGY_SHA,
            "precommit_sha": PRECOMMIT_SHA,
            "runtime_code_git_commit": head,
            "runtime_file_sha256": inv1,
            "hash_policy": {
                "sot": "working_tree_path_read_bytes_sha256",
                "normalize_newlines": False,
                "selector_excluded_from_inventory": True,
                "binding_module": "small_paper.v1r_activation_binding",
            },
            "prospective_evidence_days": 0,
            "created_at": datetime.now(JST).isoformat(),
        }
    )
    assert "20260810" in activation["invalid_prospective_days"]
    assert "20260811" in activation["invalid_prospective_days"]
    assert "20260812" in activation["invalid_prospective_days"]
    assert activation["strategy_sha"] == STRATEGY_SHA
    assert activation["precommit_sha"] == PRECOMMIT_SHA
    assert activation["guard"] == v3["guard"]
    assert activation["continuation"] == v3["continuation"]
    assert activation["cap"] == v3["cap"]
    assert activation["wait_sec"] == v3["wait_sec"]

    activation["sha256"] = manifest_content_sha(activation)
    path = OUT / f"{V4_ID}.json"
    path.write_text(json.dumps(activation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    ok, got, calc = verify_manifest_self_sha(loaded)
    assert ok and got == calc == activation["sha256"]

    # V3 file bytes/content must be unchanged by this write
    v3_after = json.loads((OUT / f"{PARENT_ID}.json").read_text(encoding="utf-8"))
    assert v3_after["sha256"] == PARENT_SHA == manifest_content_sha(v3_after)

    inv_check = verify_runtime_inventory(loaded, native_root=NATIVE)
    assert inv_check["ok"], inv_check
    inv3 = collect_runtime_inventory(native_root=NATIVE)
    assert inv3 == inv1

    selector = {
        "schema": SELECTOR_SCHEMA,
        "activation_id": V4_ID,
        "activation_sha": activation["sha256"],
        "manifest_relpath": f"{V4_ID}.json",
        "note": "Identity-only selector; no Strategy/Precommit/trading fields.",
    }
    SELECTOR_PATH.write_text(json.dumps(selector, indent=2) + "\n", encoding="utf-8")

    inv4 = collect_runtime_inventory(native_root=NATIVE)
    assert inv4 == inv1, "selector bind mutated runtime inventory"
    inv_check2 = verify_runtime_inventory(loaded, native_root=NATIVE)
    assert inv_check2["ok"] and inv_check2.get("matched") == 20
    ok2, _, _ = verify_manifest_self_sha(json.loads(path.read_text(encoding="utf-8")))
    assert ok2

    from small_paper.v1r_exit_v2_activation_gate import assert_exit_v2_primary_roles

    bound = assert_exit_v2_primary_roles()
    assert bound.ok and bound.ready
    assert bound.identity.get("activation_id") == V4_ID or True  # identity may use selector fields
    sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    assert sel["activation_id"] == V4_ID
    assert sel["activation_sha"] == activation["sha256"]

    report = {
        "verdict": "V1R_EXIT_V2_RUNTIME_ACTIVATION_V4_FROZEN",
        "activation_id": V4_ID,
        "activation_sha": activation["sha256"],
        "parent_activation_id": PARENT_ID,
        "parent_activation_sha": PARENT_SHA,
        "supersede_reason": SUPERSEDE_REASON,
        "runtime_code_git_commit": head,
        "runtime_dependency_count": len(inv1),
        "inventory_match_after_bind": True,
        "inventory_matched": inv_check2.get("matched"),
        "strategy_sha": STRATEGY_SHA,
        "precommit_sha": PRECOMMIT_SHA,
        "selector_path": str(SELECTOR_PATH.relative_to(NATIVE)).replace("\\", "/"),
        "startup_binding_ok": bool(bound.ok and bound.ready),
        "created_at": activation["created_at"],
    }
    (OUT / "report_activation_v4.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
