#!/usr/bin/env python
"""Create V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V16.

Parent V15 is immutable history (V14 remains immutable grandparent).
Does not mutate Strategy / Precommit / V15–V1 bytes.
Uses v1r_activation_binding.file_sha256 (Path.read_bytes) as sole hash SoT.
Refuses freeze if runtime inventory files are uncommitted.
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

V14_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V14"
V14_SHA = "36c48927c36957a20ffc9cd8627e2805b6ea3f17cb9685404254aa5a92e950a2"
PARENT_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V15"
PARENT_SHA = "d92426594c6c72d83d72d6e461f8edce3571a14ae3a4ea33187a8427968c2abc"
V16_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V16"
SUPERSEDE_REASON = "RUNTIME_FULL_DAY_CERT_DOMAIN_B_SAFETY_TRADING_DATE"
V13_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V13"
V13_SHA = "36fcee66a59fa708699c33e1671470153bd78c275d2cd5428876dfd179f31c13"
V12_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V12"
V12_SHA = "934c082e402020b9ac2d4b3c7d240a06bc67ebf9cd11d8d1d9b04214f5f11982"

STRATEGY_SHA = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
PRECOMMIT_SHA = "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100"
V1_SHA = "29cbc5933421319ffcb1ed24d9be517d35e74c1027ebe67df431657c6997ada1"


def _git(args: list[str]) -> str:
    return subprocess.check_output(["git", *args], cwd=str(REPO), text=True).strip()


def _assert_inventory_committed() -> None:
    porcelain = _git(["status", "--porcelain"])
    dirty: list[str] = []
    for line in porcelain.splitlines():
        path = line[3:].strip().replace("\\", "/")
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        rel = path
        if rel.startswith("kabu_native/"):
            rel = rel[len("kabu_native/") :]
        if rel in RUNTIME_DEPENDENCY_RELS:
            dirty.append(rel)
    if dirty:
        raise SystemExit(
            "REFUSE_V16_FREEZE: runtime inventory uncommitted: " + ", ".join(dirty)
        )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    v15 = json.loads((OUT / f"{PARENT_ID}.json").read_text(encoding="utf-8"))
    assert v15["sha256"] == PARENT_SHA == manifest_content_sha(v15), "V15 must remain immutable"

    v14 = json.loads((OUT / f"{V14_ID}.json").read_text(encoding="utf-8"))
    assert v14["sha256"] == V14_SHA == manifest_content_sha(v14), "V14 must remain immutable"

    v13 = json.loads((OUT / f"{V13_ID}.json").read_text(encoding="utf-8"))
    assert v13["sha256"] == V13_SHA == manifest_content_sha(v13), "V13 must remain immutable"

    v12 = json.loads((OUT / f"{V12_ID}.json").read_text(encoding="utf-8"))
    assert v12["sha256"] == V12_SHA == manifest_content_sha(v12), "V12 must remain immutable"

    for name, exp in [
        ("PASSIVE_ASYMMETRIC_EXIT_V2_FULL_STRATEGY.json", STRATEGY_SHA),
        ("PROSPECTIVE_PRECOMMIT_V1R_EXIT_V2_U1.json", PRECOMMIT_SHA),
        ("V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V1.json", V1_SHA),
    ]:
        obj = json.loads((OUT / name).read_text(encoding="utf-8"))
        assert obj["sha256"] == exp == manifest_content_sha(obj), name

    _assert_inventory_committed()
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

    activation = {k: v for k, v in v15.items() if k != "sha256"}
    invalid = dict(activation.get("invalid_prospective_days") or {})
    day14 = dict(invalid.get("20260814") or {})
    day14["status"] = "V1R_20260814_PROSPECTIVE_DAY1_INVALID_RUNTIME_FAIL"
    day14["v13_opval"] = "V1R_20260814_V13_PM_OPERATIONAL_VALIDATION_PASS"
    day14["note"] = (
        "V13 PM Operational Validation PASS is unchanged. 2026-08-14 remains "
        "INVALID_FOR_STRATEGY_EVALUATION / NOT_PROSPECTIVE_DAY1. V16 wires "
        "safety probe trading date to RuntimeClock (Domain B). V15 is immutable parent."
    )
    invalid["20260814"] = day14
    activation.update(
        {
            "manifest_id": V16_ID,
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
            "invalid_prospective_days": invalid,
            "prospective_evidence_days": 0,
            "created_at": datetime.now(JST).isoformat(),
        }
    )
    assert "20260810" in activation["invalid_prospective_days"]
    assert "20260814" in activation["invalid_prospective_days"]
    assert activation["strategy_sha"] == STRATEGY_SHA
    assert activation["precommit_sha"] == PRECOMMIT_SHA
    assert activation["guard"] == v15["guard"]
    assert activation["continuation"] == v15["continuation"]
    assert activation["cap"] == v15["cap"]
    assert activation["wait_sec"] == v15["wait_sec"]

    activation["sha256"] = manifest_content_sha(activation)
    path = OUT / f"{V16_ID}.json"
    path.write_text(json.dumps(activation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    ok, got, calc = verify_manifest_self_sha(loaded)
    assert ok and got == calc == activation["sha256"]

    v15_after = json.loads((OUT / f"{PARENT_ID}.json").read_text(encoding="utf-8"))
    assert v15_after["sha256"] == PARENT_SHA == manifest_content_sha(v15_after)
    v14_after = json.loads((OUT / f"{V14_ID}.json").read_text(encoding="utf-8"))
    assert v14_after["sha256"] == V14_SHA == manifest_content_sha(v14_after)
    v13_after = json.loads((OUT / f"{V13_ID}.json").read_text(encoding="utf-8"))
    assert v13_after["sha256"] == V13_SHA == manifest_content_sha(v13_after)

    inv_check = verify_runtime_inventory(loaded, native_root=NATIVE)
    assert inv_check["ok"], inv_check
    inv3 = collect_runtime_inventory(native_root=NATIVE)
    assert inv3 == inv1

    selector = {
        "schema": SELECTOR_SCHEMA,
        "activation_id": V16_ID,
        "activation_sha": activation["sha256"],
        "manifest_relpath": f"{V16_ID}.json",
        "note": "Identity-only selector; no Strategy/Precommit/trading fields.",
    }
    SELECTOR_PATH.write_text(json.dumps(selector, indent=2) + "\n", encoding="utf-8")

    inv4 = collect_runtime_inventory(native_root=NATIVE)
    assert inv4 == inv1, "selector bind mutated runtime inventory"
    inv_check2 = verify_runtime_inventory(loaded, native_root=NATIVE)
    assert inv_check2["ok"] and inv_check2.get("matched") == len(RUNTIME_DEPENDENCY_RELS)
    ok2, _, _ = verify_manifest_self_sha(json.loads(path.read_text(encoding="utf-8")))
    assert ok2

    from small_paper.v1r_exit_v2_activation_gate import assert_exit_v2_primary_roles

    bound = assert_exit_v2_primary_roles()
    assert bound.ok and bound.ready
    sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    assert sel["activation_id"] == V16_ID
    assert sel["activation_sha"] == activation["sha256"]

    report = {
        "verdict": "V1R_EXIT_V2_RUNTIME_ACTIVATION_V16_FROZEN",
        "activation_id": V16_ID,
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
        "v13_opval_unchanged": "V1R_20260814_V13_PM_OPERATIONAL_VALIDATION_PASS",
        "v14_immutable": V14_SHA,
        "v15_immutable": PARENT_SHA,
        "day_20260814": ["INVALID_FOR_STRATEGY_EVALUATION", "NOT_PROSPECTIVE_DAY1"],
        "paper_started": False,
    }
    (OUT / "report_activation_v16.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
