#!/usr/bin/env python
"""V26-G5: Candidate-5 exact identity gate. Does not rewrite the candidate."""
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
    RUNTIME_DEPENDENCY_RELS,
    SELECTOR_PATH,
    V25_ACTIVATION_ID,
    audit_runtime_inventory_coverage,
    candidate_source_digest,
    collect_runtime_inventory,
    file_sha256,
    inventory_digest,
    load_activation_manifest,
    load_active_selector,
    manifest_content_sha,
    resolve_manifest_path,
    verify_generator_inventory_coverage,
    verify_manifest_self_sha,
    verify_runtime_inventory,
)

C5_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G4_5"
C5_SHA = "eb952288c4be2dcb586db877f09166d9197564b6658fb72525a66f038e41e8e6"
C5_SELECTOR = OUT / "active_v1r_candidate_v26g4_5.json"
V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
STRATEGY_SHA = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
PRECOMMIT_SHA = "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100"
ENTRY_SHA = "f2887bb2be539cc173aee438a43ee8afb8cfa2b8c31380937ecd843e90dd9b29"
EXIT_V2_SHA = "6cc3b8aade76e323682ec39dfd06878aab0ff1a99dd42922744b0054a7ea3255"
UNIVERSE = "DAY_FIXED_AM_RUNTIME_UNIVERSE_V1"


def check() -> dict:
    fail: list[str] = []
    sel = load_active_selector(path=C5_SELECTOR)
    if sel.get("activation_id") != C5_ID:
        fail.append(f"selector_id={sel.get('activation_id')}")
    if sel.get("activation_sha") != C5_SHA:
        fail.append("selector_sha_mismatch")
    man = load_activation_manifest(selector=sel, out_dir=OUT)
    ok_sha, got, calc = verify_manifest_self_sha(man)
    if not (ok_sha and got == C5_SHA and calc == C5_SHA):
        fail.append(f"self_sha got={got} calc={calc}")
    if man.get("candidate_id") != C5_ID or man.get("sha256") != C5_SHA:
        fail.append("manifest_id_or_sha")
    if man.get("candidate_status") != CANDIDATE_STATUS_UNCERTIFIED:
        fail.append("status")
    if man.get("immutable") is not True:
        fail.append("immutable")
    if man.get("formal_paper_allowed") is not False:
        fail.append("formal_paper_allowed")

    expected_n = len(RUNTIME_DEPENDENCY_RELS)
    inv_now = collect_runtime_inventory(native_root=NATIVE)
    man_inv = {str(k).replace("\\", "/"): str(v) for k, v in (man.get("runtime_file_sha256") or {}).items()}
    matched = sum(1 for k, v in man_inv.items() if inv_now.get(k) == v)
    mismatch = sorted(k for k, v in man_inv.items() if inv_now.get(k) != v)
    extra = sorted(set(inv_now) - set(man_inv))
    missing = sorted(set(man_inv) - set(inv_now))
    if len(man_inv) != expected_n:
        fail.append(f"inventory_expected={expected_n} manifest={len(man_inv)}")
    if matched != expected_n or mismatch or extra or missing:
        fail.append(f"inventory_matched={matched} mismatch={mismatch[:8]} extra={extra[:4]} missing={missing[:4]}")

    cov = audit_runtime_inventory_coverage(native_root=NATIVE)
    if cov.get("runtime_critical_uncovered_files"):
        fail.append(f"uncovered={cov.get('runtime_critical_uncovered_files')}")
    gen = verify_generator_inventory_coverage(man)
    if not gen.get("ok"):
        fail.append(f"generator_set={gen}")
    inv_check = verify_runtime_inventory(man, native_root=NATIVE)
    if not inv_check.get("ok"):
        fail.append(f"verify_runtime_inventory={inv_check.get('reason')}")

    src_now = candidate_source_digest(inv_now, native_root=NATIVE)
    if src_now != str(man.get("candidate_source_digest") or ""):
        fail.append("source_digest_mismatch")
    if inventory_digest(inv_now) != str(man.get("runtime_inventory_digest") or ""):
        fail.append("inventory_digest_mismatch")

    cfg_rel = str(man.get("config_path") or "configs/small_paper_pilot.yaml")
    cfg = NATIVE / cfg_rel
    cfg_sha = file_sha256(cfg) if cfg.is_file() else ""
    if cfg_sha != str(man.get("config_sha256") or ""):
        fail.append("config_hash_mismatch")

    if str(man.get("strategy_sha") or "") != STRATEGY_SHA:
        fail.append("strategy_sha")
    if str(man.get("precommit_sha") or "") != PRECOMMIT_SHA:
        fail.append("precommit_sha")
    if str(man.get("entry_sha") or "") != ENTRY_SHA:
        fail.append("entry_sha")
    if str(man.get("exit_v2_candidate_sha") or "") != EXIT_V2_SHA:
        fail.append("exit_sha")
    if str(man.get("universe_contract") or "") != UNIVERSE:
        fail.append("universe")
    roles = man.get("runtime_roles") or {}
    if str(roles.get("primary") or "") != "PAPER_PRIMARY":
        fail.append("primary_role")

    v25_sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    if v25_sel.get("activation_id") != V25_ACTIVATION_ID or v25_sel.get("activation_sha") != V25_SHA:
        fail.append("V25_FORMAL_SELECTOR_MUTATED")

    out = {
        "ok": not fail,
        "verdict": "CANDIDATE5_IDENTITY_OK" if not fail else "V1R_V26G5_CANDIDATE5_IDENTITY_DRIFT",
        "fail": fail,
        "selected_activation": sel.get("activation_id"),
        "candidate_sha": man.get("sha256"),
        "inventory_expected": expected_n,
        "inventory_matched": matched,
        "mismatch": mismatch,
        "runtime_critical_uncovered_files": cov.get("runtime_critical_uncovered_files") or [],
        "candidate_source_digest": src_now,
        "runtime_inventory_digest": inventory_digest(inv_now),
        "config_sha256": cfg_sha,
        "strategy_sha": man.get("strategy_sha"),
        "precommit_sha": man.get("precommit_sha"),
        "entry_sha": man.get("entry_sha"),
        "exit_v2_candidate_sha": man.get("exit_v2_candidate_sha"),
        "universe_contract": man.get("universe_contract"),
        "v25_formal_unchanged": v25_sel.get("activation_id") == V25_ACTIVATION_ID,
        "selector_path": str(C5_SELECTOR),
        "manifest_path": str(resolve_manifest_path(sel, out_dir=OUT)),
    }
    return out


def main() -> int:
    body = check()
    print(json.dumps(body, indent=2, ensure_ascii=False, default=str))
    return 0 if body.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
