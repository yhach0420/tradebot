#!/usr/bin/env python
"""Write UNCERTIFIED immutable V26-G12 Candidate-12 snapshot from current working tree.

Does not mutate Formal V25 or Candidate-6/7/8/9/10/11. Does not Formal-freeze V26.
Does not enable Formal Paper. OPVAL current-trading-day identity is not rewritten.
Candidate-10 DualLane bytes and Candidate-11 snapshot must remain identical.
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
CANDIDATE_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G12_12"
SELECTOR_CANDIDATE = OUT / "active_v1r_candidate_v26g12_12.json"
V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
C6_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G6_6"
C6_SHA = "3ac5cf4b1788f52d38aeb0b7ea059f847f89cf4e026c844ec64d96713fa3563d"
C7_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G7_7"
C7_SHA = "bc0b47e01f6bce592fa374bc555d3e9f26dbd353848356a890bdb73452602960"
C8_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G8_8"
C9_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G9_9"
C9_SHA = "364754cd444bdce80e9f0e8157cfde8f426eb4d7e8bd78ccd5a7cd04004e6945"
C10_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G10_10"
C10_SHA = "b89c39881b2ba48c2d1b051c28acf0221e7f361b46e55f0a1a3b99abafc6c20e"
C10_DUALLANE_SHA = "2cdb61f2e5f39a8f4ef782fa3d0059797b70c015887df5d94aa0520ba04b66f6"
C11_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G11_11"
C11_SHA = "d1ada73cd2434abda895db3fd7977d16d17de550dbbf5038c5ae76b1fee4d9c1"
C10_SELECTOR = OUT / "active_v1r_candidate_v26g10_10.json"
C11_SELECTOR = OUT / "active_v1r_candidate_v26g11_11.json"
C9_SELECTOR = OUT / "active_v1r_candidate_v26g9_9.json"
C8_SELECTOR = OUT / "active_v1r_candidate_v26g8_8.json"
C7_SELECTOR = OUT / "active_v1r_candidate_v26g7_7.json"
ALLOWED_RUNTIME_DRIFT = frozenset(
    {
        "src/small_paper/v1r_activation_binding.py",
        "src/small_paper/local_market_bus.py",
        "src/small_paper/capture_sequence_reader.py",
    }
)
PRIOR = (
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G2_1",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_2",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_3",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_4",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G4_5",
    C6_ID,
    C7_ID,
    C8_ID,
    C9_ID,
    C10_ID,
    C11_ID,
)


def _sha(path: Path) -> str:
    return file_sha256(path) if path.is_file() else ""


def main() -> int:
    dest = OUT / f"{CANDIDATE_ID}.json"
    if dest.is_file() or SELECTOR_CANDIDATE.is_file():
        print("REFUSE_OVERWRITE: candidate-12 snapshot already exists", dest, SELECTOR_CANDIDATE)
        return 2

    prior_shas: dict[str, str] = {}
    for cid in PRIOR:
        p = OUT / f"{cid}.json"
        if not p.is_file():
            print("REFUSE: missing", cid)
            return 2
        body = json.loads(p.read_text(encoding="utf-8"))
        sha = str(body.get("sha256") or "")
        got_id = str(body.get("candidate_id") or body.get("manifest_id") or "")
        if got_id != cid or not sha:
            print("REFUSE: identity mutated", cid)
            return 2
        prior_shas[cid] = sha
    if prior_shas[C6_ID] != C6_SHA:
        print("REFUSE: Candidate-6 manifest mutated")
        return 2
    if prior_shas[C7_ID] != C7_SHA:
        print("REFUSE: Candidate-7 manifest mutated")
        return 2
    if prior_shas[C9_ID] != C9_SHA:
        print("REFUSE: Candidate-9 manifest mutated")
        return 2
    if prior_shas[C10_ID] != C10_SHA:
        print("REFUSE: Candidate-10 manifest mutated")
        return 2
    if prior_shas[C11_ID] != C11_SHA:
        print("REFUSE: Candidate-11 manifest mutated")
        return 2
    if C7_SELECTOR.is_file():
        c7s = json.loads(C7_SELECTOR.read_text(encoding="utf-8"))
        if c7s.get("activation_id") != C7_ID or c7s.get("activation_sha") != C7_SHA:
            print("REFUSE: Candidate-7 identity selector mutated")
            return 2
    if C9_SELECTOR.is_file():
        c9s = json.loads(C9_SELECTOR.read_text(encoding="utf-8"))
        if c9s.get("activation_id") != C9_ID or c9s.get("activation_sha") != C9_SHA:
            print("REFUSE: Candidate-9 identity selector mutated")
            return 2
    if C10_SELECTOR.is_file():
        c10s = json.loads(C10_SELECTOR.read_text(encoding="utf-8"))
        if c10s.get("activation_id") != C10_ID or c10s.get("activation_sha") != C10_SHA:
            print("REFUSE: Candidate-10 identity selector mutated")
            return 2
    if C11_SELECTOR.is_file():
        c11s = json.loads(C11_SELECTOR.read_text(encoding="utf-8"))
        if c11s.get("activation_id") != C11_ID or c11s.get("activation_sha") != C11_SHA:
            print("REFUSE: Candidate-11 identity selector mutated")
            return 2

    c6 = json.loads((OUT / f"{C6_ID}.json").read_text(encoding="utf-8"))
    c7 = json.loads((OUT / f"{C7_ID}.json").read_text(encoding="utf-8"))
    c8 = json.loads((OUT / f"{C8_ID}.json").read_text(encoding="utf-8"))
    c9 = json.loads((OUT / f"{C9_ID}.json").read_text(encoding="utf-8"))
    c10 = json.loads((OUT / f"{C10_ID}.json").read_text(encoding="utf-8"))
    c11 = json.loads((OUT / f"{C11_ID}.json").read_text(encoding="utf-8"))
    C8_SHA = str(c8.get("sha256") or "")
    if not C8_SHA:
        print("REFUSE: Candidate-8 missing sha")
        return 2
    if C8_SELECTOR.is_file():
        c8s = json.loads(C8_SELECTOR.read_text(encoding="utf-8"))
        if c8s.get("activation_id") != C8_ID or c8s.get("activation_sha") != C8_SHA:
            print("REFUSE: Candidate-8 identity selector mutated")
            return 2

    v25_sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    v25 = json.loads((OUT / f"{V25_ACTIVATION_ID}.json").read_text(encoding="utf-8"))
    if v25_sel.get("activation_id") != V25_ACTIVATION_ID or v25.get("sha256") != V25_SHA:
        print("REFUSE: V25 selector/manifest mutated")
        return 2
    if manifest_content_sha(v25) != V25_SHA:
        print("REFUSE: V25 manifest self-sha drift")
        return 2

    strategy_ok = (
        str(v25.get("strategy_sha") or "") == str(c11.get("strategy_sha") or "") == str(c10.get("strategy_sha") or "")
        and str(v25.get("entry_sha") or "") == str(c11.get("entry_sha") or "")
        and str(v25.get("exit_v2_candidate_sha") or "") == str(c11.get("exit_v2_candidate_sha") or "")
        and str(v25.get("anchor_sha") or "") == str(c11.get("anchor_sha") or "")
    )
    if not strategy_ok:
        print("REFUSE: strategy identity drift vs Candidate-11/10 pins")
        return 2

    cov = audit_runtime_inventory_coverage(native_root=NATIVE)
    if not cov.get("ok") or cov.get("runtime_critical_uncovered_files"):
        print("V1R_V26G12_INVENTORY_COVERAGE_FAIL", json.dumps(cov, indent=2, default=str))
        return 2

    inv = collect_runtime_inventory(native_root=NATIVE)
    if len(inv) != len(RUNTIME_DEPENDENCY_RELS):
        print("REFUSE: inventory length != generator", len(inv), len(RUNTIME_DEPENDENCY_RELS))
        return 2
    dual_rel = "src/small_paper/v1r_live_dual_lane.py"
    if str(inv.get(dual_rel) or "") != C10_DUALLANE_SHA:
        print("REFUSE: Candidate-10 DualLane mutated", inv.get(dual_rel), C10_DUALLANE_SHA)
        return 2
    c11_inv = c11.get("runtime_file_sha256") or {}
    drifted = [
        rel
        for rel in RUNTIME_DEPENDENCY_RELS
        if rel not in ALLOWED_RUNTIME_DRIFT
        and rel in c11_inv
        and str(c11_inv.get(rel) or "") != str(inv.get(rel) or "")
    ]
    if drifted:
        print("REFUSE: unexpected inventory drift vs Candidate-11 G=", len(drifted), drifted[:20])
        return 2
    new_keys = [rel for rel in RUNTIME_DEPENDENCY_RELS if rel not in c11_inv]
    if any(rel not in ALLOWED_RUNTIME_DRIFT for rel in new_keys):
        print("REFUSE: unexpected new inventory keys vs Candidate-11", new_keys)
        return 2
    runtime_changed = sorted(
        {
            rel
            for rel in RUNTIME_DEPENDENCY_RELS
            if rel in ALLOWED_RUNTIME_DRIFT
            and str(c11_inv.get(rel) or "") != str(inv.get(rel) or "")
        }
    )
    if "src/small_paper/local_market_bus.py" not in runtime_changed:
        print("REFUSE: expected local_market_bus physical fanout repair vs Candidate-11")
        return 2
    if "src/small_paper/capture_sequence_reader.py" not in runtime_changed:
        print("REFUSE: expected capture_sequence_reader pin vs Candidate-11")
        return 2
    for key in ("entry_sha", "anchor_sha", "exit_v2_candidate_sha", "strategy_sha"):
        if str(c11.get(key) or "") != str(c10.get(key) or "") or str(c11.get(key) or "") != str(v25.get(key) or ""):
            print("REFUSE: Candidate-11 strategy identity drifted vs V25/C10", key)
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
            "operational_validation_only": True,
            "not_formal": True,
            "invalid_for_strategy_evaluation": True,
            "classification": [
                "UNCERTIFIED",
                "OPERATIONAL_VALIDATION_ONLY",
                "NOT_FORMAL",
                "INVALID_FOR_STRATEGY_EVALUATION",
            ],
            "parent_activation_id": V25_ACTIVATION_ID,
            "parent_activation_sha": V25_SHA,
            "parent_v25_activation_id": V25_ACTIVATION_ID,
            "parent_v25_activation_sha": V25_SHA,
            "parent_activation_status": "IMMUTABLE_FORMAL_PARENT",
            "parent_candidate6_id": C6_ID,
            "parent_candidate6_sha": C6_SHA,
            "parent_candidate7_id": C7_ID,
            "parent_candidate7_sha": C7_SHA,
            "parent_candidate8_id": C8_ID,
            "parent_candidate8_sha": C8_SHA,
            "parent_candidate9_id": C9_ID,
            "parent_candidate9_sha": C9_SHA,
            "parent_candidate10_id": C10_ID,
            "parent_candidate10_sha": C10_SHA,
            "parent_candidate11_id": C11_ID,
            "parent_candidate11_sha": C11_SHA,
            "supersede_reason": "V26G12_POST_RESYNC_PHYSICAL_FANOUT_READER_RING_HANDOFF_REPAIR",
            "notification_only_change": False,
            "submit_cancel_live": "0/0/0",
            "strategy_affecting_diff_g": 0,
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
            "parent_runtime_candidate_id": C11_ID,
            "parent_runtime_candidate_sha": C11_SHA,
            "parent_runtime_candidate_status": "UNCERTIFIED_IMMUTABLE_LEFT_IN_PLACE",
            "allowed_runtime_drift_vs_candidate11": sorted(ALLOWED_RUNTIME_DRIFT),
            "runtime_changed_rels": runtime_changed,
            "candidate10_duallane_unchanged": True,
            "candidate10_duallane_sha": C10_DUALLANE_SHA,
            "candidate11_unchanged": True,
        }
    )
    for key in ("entry_sha", "anchor_sha", "exit_v2_candidate_sha", "strategy_sha"):
        if str(body.get(key) or "") != str(c11.get(key) or ""):
            print("REFUSE: strategy SHA != Candidate-11", key, body.get(key), c11.get(key))
            return 2
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
        "note": (
            "Identity-only UNCERTIFIED candidate-12 selector; not the active Formal selector. "
            "Not Candidate-11. OPVAL current-trading-day identity is not rewritten."
        ),
    }
    SELECTOR_CANDIDATE.write_text(json.dumps(selector, indent=2) + "\n", encoding="utf-8")

    v25_after = json.loads((OUT / f"{V25_ACTIVATION_ID}.json").read_text(encoding="utf-8"))
    sel_after = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    c10_after = json.loads((OUT / f"{C10_ID}.json").read_text(encoding="utf-8"))
    c11_after = json.loads((OUT / f"{C11_ID}.json").read_text(encoding="utf-8"))
    if v25_after.get("sha256") != V25_SHA or sel_after.get("activation_id") != V25_ACTIVATION_ID:
        print("REFUSE: V25 mutated during candidate write")
        dest.unlink(missing_ok=True)
        SELECTOR_CANDIDATE.unlink(missing_ok=True)
        return 2
    if c10_after.get("sha256") != C10_SHA:
        print("REFUSE: Candidate-10 mutated during candidate write")
        dest.unlink(missing_ok=True)
        SELECTOR_CANDIDATE.unlink(missing_ok=True)
        return 2
    if c11_after.get("sha256") != C11_SHA:
        print("REFUSE: Candidate-11 mutated during candidate write")
        dest.unlink(missing_ok=True)
        SELECTOR_CANDIDATE.unlink(missing_ok=True)
        return 2
    for cid, sha in prior_shas.items():
        got_sha = json.loads((OUT / f"{cid}.json").read_text(encoding="utf-8")).get("sha256")
        if got_sha != sha:
            print("REFUSE: prior candidate mutated", cid)
            dest.unlink(missing_ok=True)
            SELECTOR_CANDIDATE.unlink(missing_ok=True)
            return 2

    print(f"CANDIDATE_ID={CANDIDATE_ID}")
    print(f"CANDIDATE_SHA={body['sha256']}")
    print(f"RUNTIME_INVENTORY_N={len(inv)}")
    print(f"RUNTIME_INVENTORY_DIGEST={body['runtime_inventory_digest']}")
    print(f"CANDIDATE_SOURCE_DIGEST={body['candidate_source_digest']}")
    print(f"CONFIG_SHA={body['config_sha256']}")
    print(f"STRATEGY_SHA={body.get('strategy_sha')}")
    print(f"ENTRY_SHA={body.get('entry_sha')}")
    print(f"EXIT_SHA={body.get('exit_v2_candidate_sha')}")
    print(f"ANCHOR_SHA={body.get('anchor_sha')}")
    print(f"SELECTOR={SELECTOR_CANDIDATE}")
    print(f"MANIFEST={dest}")
    print("V25_UNCHANGED=true")
    print("CANDIDATE10_UNCHANGED=true")
    print("CANDIDATE11_UNCHANGED=true")
    print("CANDIDATE10_DUALLANE_UNCHANGED=true")
    print("RUNTIME_CHANGED=true")
    print(f"RUNTIME_CHANGED_RELS={runtime_changed}")
    print("STRATEGY_AFFECTING_DIFF_G=0")
    print("PRIOR_CANDIDATES_UNCHANGED=true")
    print("UNCERTIFIED=true")
    print("OPERATIONAL_VALIDATION_ONLY=true")
    print("NOT_FORMAL=true")
    print("INVALID_FOR_STRATEGY_EVALUATION=true")
    print("FORMAL_PAPER_ALLOWED=false")
    print("IMMUTABLE=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
