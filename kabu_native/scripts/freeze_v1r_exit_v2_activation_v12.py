#!/usr/bin/env python
"""Create V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V12.

Parent V11 is immutable history. Does not mutate Strategy / Precommit / V11–V1 bytes.
Uses v1r_activation_binding.file_sha256 (Path.read_bytes) as sole hash SoT.
Refuses freeze if runtime inventory files are uncommitted.
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

PARENT_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V11"
PARENT_SHA = "19a8974dbd453e26664c4f0124c97c32e70c1097f2e01ebd3b497fb483a2673b"
V12_ID = "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V12"
SUPERSEDE_REASON = "RUNTIME_STALE_RECOVERY_THROTTLE_AND_CONSUMER_BACKPRESSURE_FIX"

STRATEGY_SHA = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
PRECOMMIT_SHA = "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100"
V11_SHA = PARENT_SHA
V10_SHA = "84d76713fd8492a8e518b64d2818f724657572588adbb371b6364439ebd7c0b2"
V9_SHA = "858f21fa785ac508b92b8d54c10a7c6ec7cb3483157b80936caf167e9684f7f1"
V8_SHA = "1d3e7c3d9644b7db60f4cc2524ba2ce4065f78fe4cff3cec86971ff97e25954f"
V7_SHA = "3074c3bf8028819e2f708550c5cdbff874fb4a7f3e7274e7327a48dcf2fda087"
V6_SHA = "f113dd0d77138417a0b32c2666edb99b2d257163a43805dad4a2c032e6d4c86f"
V5_SHA = "885e2ba246ecfdc448274adafaaf1789c7ccb874a3290cc497346cc48aee5e3a"
V4_SHA = "73e9397ba7e2cee05f32044c3cb5ecb80459f45d191fdb50496a1c95f4a86dc2"
V3_SHA = "10c9efbc758cf8f68fcee47902a98708365c8b0e9ae5a34e839ca9da5bb118b3"
V2_SHA = "0cd4b6289e392269035448b7a71be0b1f2b449782b57991a9e028f8e1be7bd46"
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
            "REFUSE_V12_FREEZE: runtime inventory uncommitted: " + ", ".join(dirty)
        )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    v11 = json.loads((OUT / f"{PARENT_ID}.json").read_text(encoding="utf-8"))
    assert v11["sha256"] == PARENT_SHA == manifest_content_sha(v11), "V11 must remain immutable"

    v10 = json.loads((OUT / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V10.json").read_text(encoding="utf-8"))
    assert v10["sha256"] == V10_SHA == manifest_content_sha(v10)

    v9 = json.loads((OUT / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V9.json").read_text(encoding="utf-8"))
    assert v9["sha256"] == V9_SHA == manifest_content_sha(v9)

    v8 = json.loads((OUT / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V8.json").read_text(encoding="utf-8"))
    assert v8["sha256"] == V8_SHA == manifest_content_sha(v8)

    v7 = json.loads((OUT / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V7.json").read_text(encoding="utf-8"))
    assert v7["sha256"] == V7_SHA == manifest_content_sha(v7)

    v6 = json.loads((OUT / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V6.json").read_text(encoding="utf-8"))
    assert v6["sha256"] == V6_SHA == manifest_content_sha(v6)

    v5 = json.loads((OUT / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V5.json").read_text(encoding="utf-8"))
    assert v5["sha256"] == V5_SHA == manifest_content_sha(v5)

    v4 = json.loads((OUT / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V4.json").read_text(encoding="utf-8"))
    assert v4["sha256"] == V4_SHA == manifest_content_sha(v4)

    v3 = json.loads((OUT / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V3.json").read_text(encoding="utf-8"))
    assert v3["sha256"] == V3_SHA == manifest_content_sha(v3)

    v2 = json.loads((OUT / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V2.json").read_text(encoding="utf-8"))
    assert v2["sha256"] == V2_SHA == manifest_content_sha(v2)

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

    activation = {k: v for k, v in v11.items() if k != "sha256"}
    invalid = dict(activation.get("invalid_prospective_days") or {})
    invalid["20260814"] = {
        "status": "V1R_20260814_PROSPECTIVE_DAY1_INVALID_RUNTIME_FAIL",
        "reason": (
            "CONSUMER_LAG_PERSISTENT_INCREASE / STALE_RECOVERY_FORCE_EVAL_DEATH_SPIRAL. "
            "PnL excluded from Strategy evaluation. V12 is the runtime fix; not Prospective."
        ),
    }
    activation.update(
        {
            "manifest_id": V12_ID,
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
    assert "20260811" in activation["invalid_prospective_days"]
    assert "20260812" in activation["invalid_prospective_days"]
    assert "20260813" in activation["invalid_prospective_days"]
    assert "20260814" in activation["invalid_prospective_days"]
    assert activation["strategy_sha"] == STRATEGY_SHA
    assert activation["precommit_sha"] == PRECOMMIT_SHA
    assert activation["guard"] == v11["guard"]
    assert activation["continuation"] == v11["continuation"]
    assert activation["cap"] == v11["cap"]
    assert activation["wait_sec"] == v11["wait_sec"]

    activation["sha256"] = manifest_content_sha(activation)
    path = OUT / f"{V12_ID}.json"
    path.write_text(json.dumps(activation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    loaded = json.loads(path.read_text(encoding="utf-8"))
    ok, got, calc = verify_manifest_self_sha(loaded)
    assert ok and got == calc == activation["sha256"]

    v11_after = json.loads((OUT / f"{PARENT_ID}.json").read_text(encoding="utf-8"))
    assert v11_after["sha256"] == PARENT_SHA == manifest_content_sha(v11_after)

    inv_check = verify_runtime_inventory(loaded, native_root=NATIVE)
    assert inv_check["ok"], inv_check
    inv3 = collect_runtime_inventory(native_root=NATIVE)
    assert inv3 == inv1

    selector = {
        "schema": SELECTOR_SCHEMA,
        "activation_id": V12_ID,
        "activation_sha": activation["sha256"],
        "manifest_relpath": f"{V12_ID}.json",
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
    assert sel["activation_id"] == V12_ID
    assert sel["activation_sha"] == activation["sha256"]

    report = {
        "verdict": "V1R_EXIT_V2_RUNTIME_ACTIVATION_V12_FROZEN",
        "activation_id": V12_ID,
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
    (OUT / "report_activation_v12.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
