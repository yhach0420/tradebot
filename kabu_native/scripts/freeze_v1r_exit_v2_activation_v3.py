#!/usr/bin/env python
"""Create V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V3 (binding-architecture supersede).

Parent V2 is immutable. Does not mutate Strategy / Precommit.
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
    file_sha256,
    manifest_content_sha,
    verify_manifest_self_sha,
    verify_runtime_inventory,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent

PARENT_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V2"
PARENT_SHA = "0cd4b6289e392269035448b7a71be0b1f2b449782b57991a9e028f8e1be7bd46"
V3_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V3"

STRATEGY_SHA = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
PRECOMMIT_SHA = "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100"
V1_SHA = "29cbc5933421319ffcb1ed24d9be517d35e74c1027ebe67df431657c6997ada1"


def _git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=str(REPO), text=True).strip()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    # Parent V2 immutable
    v2 = json.loads((OUT / f"{PARENT_ID}.json").read_text(encoding="utf-8"))
    assert v2["sha256"] == PARENT_SHA == manifest_content_sha(v2)

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
    print(f"INVENTORY_DOUBLE_CHECK=PASS")

    # Carry economic + evidence fields from V2; replace binding identity + inventory.
    activation = {k: v for k, v in v2.items() if k != "sha256"}
    activation.update(
        {
            "manifest_id": V3_ID,
            "parent_activation_id": PARENT_ID,
            "parent_activation_sha": PARENT_SHA,
            "parent_activation_status": (
                "SUPERSEDED_FREEZE_INVENTORY_BINDING_MISMATCH_BEFORE_PROSPECTIVE_START"
            ),
            "supersede_reason": (
                "Not a Strategy change. Activation V2 freeze inventory was taken from "
                "68a915ad working-tree bytes while startup gate was later bound to V2 "
                "in b8c5040, creating a self-reference cycle "
                "(gate hard-coded ACTIVATION_SHA also listed in runtime_file_sha256). "
                "V3 separates active selector binding from Strategy Runtime inventory."
            ),
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
    # Ensure invalid day labels retained from V2
    assert "20260810" in activation["invalid_prospective_days"]
    assert "20260811" in activation["invalid_prospective_days"]
    assert "20260812" in activation["invalid_prospective_days"]

    activation["sha256"] = manifest_content_sha(activation)
    path = OUT / f"{V3_ID}.json"
    path.write_text(json.dumps(activation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    ok, got, calc = verify_manifest_self_sha(loaded)
    assert ok and got == calc == activation["sha256"]

    inv_check = verify_runtime_inventory(loaded, native_root=NATIVE)
    assert inv_check["ok"], inv_check
    # Recompute inventory after write — must be unchanged (manifest not in inventory)
    inv3 = collect_runtime_inventory(native_root=NATIVE)
    assert inv3 == inv1

    # Bind selector AFTER manifest SHA is final (selector not in inventory)
    selector = {
        "schema": SELECTOR_SCHEMA,
        "activation_id": V3_ID,
        "activation_sha": activation["sha256"],
        "manifest_relpath": f"{V3_ID}.json",
        "note": "Identity-only selector; no Strategy/Precommit/trading fields.",
    }
    SELECTOR_PATH.write_text(json.dumps(selector, indent=2) + "\n", encoding="utf-8")

    # Post-bind inventory must be identical
    inv4 = collect_runtime_inventory(native_root=NATIVE)
    assert inv4 == inv1, "selector bind mutated runtime inventory"
    inv_check2 = verify_runtime_inventory(loaded, native_root=NATIVE)
    assert inv_check2["ok"]
    ok2, _, _ = verify_manifest_self_sha(
        json.loads(path.read_text(encoding="utf-8"))
    )
    assert ok2

    report = {
        "verdict": "V1R_EXIT_V2_RUNTIME_ACTIVATION_V3_FROZEN",
        "activation_id": V3_ID,
        "activation_sha": activation["sha256"],
        "parent_activation_id": PARENT_ID,
        "parent_activation_sha": PARENT_SHA,
        "runtime_code_git_commit": head,
        "runtime_dependency_count": len(inv1),
        "inventory_match_after_bind": True,
        "strategy_sha": STRATEGY_SHA,
        "precommit_sha": PRECOMMIT_SHA,
        "selector_path": str(SELECTOR_PATH.relative_to(NATIVE)).replace("\\", "/"),
        "created_at": activation["created_at"],
    }
    (OUT / "report_activation_v3.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
