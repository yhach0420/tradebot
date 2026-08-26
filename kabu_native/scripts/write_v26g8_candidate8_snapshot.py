#!/usr/bin/env python
"""Write UNCERTIFIED immutable V26-G8 Candidate-8 snapshot from current working tree.

Does not mutate Formal V25, Candidate-6, or Candidate-7. Does not Formal-freeze V26.
Does not enable Formal Paper. OPVAL current-trading-day identity is not rewritten.
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
CANDIDATE_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G8_8"
SELECTOR_CANDIDATE = OUT / "active_v1r_candidate_v26g8_8.json"
V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
C6_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G6_6"
C6_SHA = "3ac5cf4b1788f52d38aeb0b7ea059f847f89cf4e026c844ec64d96713fa3563d"
C7_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G7_7"
C7_SHA = "bc0b47e01f6bce592fa374bc555d3e9f26dbd353848356a890bdb73452602960"
C7_SELECTOR = OUT / "active_v1r_candidate_v26g7_7.json"
PRIOR = (
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G2_1",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_2",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_3",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G3_4",
    "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G4_5",
    C6_ID,
    C7_ID,
)


def _sha(path: Path) -> str:
    return file_sha256(path) if path.is_file() else ""


def main() -> int:
    dest = OUT / f"{CANDIDATE_ID}.json"
    if dest.is_file() or SELECTOR_CANDIDATE.is_file():
        print("REFUSE_OVERWRITE: candidate-8 snapshot already exists", dest, SELECTOR_CANDIDATE)
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
    if C7_SELECTOR.is_file():
        c7s = json.loads(C7_SELECTOR.read_text(encoding="utf-8"))
        if c7s.get("activation_id") != C7_ID or c7s.get("activation_sha") != C7_SHA:
            print("REFUSE: Candidate-7 identity selector mutated")
            return 2
    c6 = json.loads((OUT / f"{C6_ID}.json").read_text(encoding="utf-8"))
    c7 = json.loads((OUT / f"{C7_ID}.json").read_text(encoding="utf-8"))

    v25_sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    v25 = json.loads((OUT / f"{V25_ACTIVATION_ID}.json").read_text(encoding="utf-8"))
    if v25_sel.get("activation_id") != V25_ACTIVATION_ID or v25.get("sha256") != V25_SHA:
        print("REFUSE: V25 selector/manifest mutated")
        return 2
    if manifest_content_sha(v25) != V25_SHA:
        print("REFUSE: V25 manifest self-sha drift")
        return 2

    strategy_ok = (
        str(v25.get("strategy_sha") or "") == str(c7.get("strategy_sha") or "") == str(c6.get("strategy_sha") or "")
        and str(v25.get("entry_sha") or "") == str(c6.get("entry_sha") or "")
        and str(v25.get("exit_v2_candidate_sha") or "") == str(c6.get("exit_v2_candidate_sha") or "")
        and str(v25.get("anchor_sha") or "") == str(c6.get("anchor_sha") or "")
    )
    if not strategy_ok:
        print("REFUSE: strategy identity drift vs Candidate-6 pins")
        print("strategy", v25.get("strategy_sha"))
        print("entry", v25.get("entry_sha"))
        print("exit", v25.get("exit_v2_candidate_sha"))
        print("anchor", v25.get("anchor_sha"))
        return 2

    cov = audit_runtime_inventory_coverage(native_root=NATIVE)
    if not cov.get("ok") or cov.get("runtime_critical_uncovered_files"):
        print("V1R_V26G8_INVENTORY_COVERAGE_FAIL", json.dumps(cov, indent=2, default=str))
        return 2

    inv = collect_runtime_inventory(native_root=NATIVE)
    if len(inv) != len(RUNTIME_DEPENDENCY_RELS):
        print("REFUSE: inventory length != generator", len(inv), len(RUNTIME_DEPENDENCY_RELS))
        return 2
    c7_inv = c7.get("runtime_file_sha256") or {}
    drifted = [rel for rel in RUNTIME_DEPENDENCY_RELS if "v1r_live_dual_lane.py" not in rel and str(c7_inv.get(rel) or "") != str(inv.get(rel) or "")]
    if drifted:
        print("REFUSE: strategy-affecting inventory drift G=", len(drifted), drifted[:20])
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
            "supersede_reason": "V26G8_DUALLANE_THROUGHPUT_REPAIR_RUNTIME_ONLY",
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
            "parent_runtime_candidate_id": C7_ID,
            "parent_runtime_candidate_sha": C7_SHA,
            "parent_runtime_candidate_status": "UNCERTIFIED_IMMUTABLE_LEFT_IN_PLACE",
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
        "note": (
            "Identity-only UNCERTIFIED candidate-8 selector; not the active Formal selector. "
            "Not Candidate-7. OPVAL current-trading-day identity is not rewritten."
        ),
    }
    SELECTOR_CANDIDATE.write_text(json.dumps(selector, indent=2) + "\n", encoding="utf-8")

    v25_after = json.loads((OUT / f"{V25_ACTIVATION_ID}.json").read_text(encoding="utf-8"))
    sel_after = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    c6_after = json.loads((OUT / f"{C6_ID}.json").read_text(encoding="utf-8"))
    c7_after = json.loads((OUT / f"{C7_ID}.json").read_text(encoding="utf-8"))
    if v25_after.get("sha256") != V25_SHA or sel_after.get("activation_id") != V25_ACTIVATION_ID:
        print("REFUSE: V25 mutated during candidate write")
        return 2
    if c6_after.get("sha256") != C6_SHA:
        print("REFUSE: Candidate-6 mutated during candidate write")
        dest.unlink(missing_ok=True)
        SELECTOR_CANDIDATE.unlink(missing_ok=True)
        return 2
    if c7_after.get("sha256") != C7_SHA:
        print("REFUSE: Candidate-7 mutated during candidate write")
        dest.unlink(missing_ok=True)
        SELECTOR_CANDIDATE.unlink(missing_ok=True)
        return 2
    for cid, sha in prior_shas.items():
        got_sha = json.loads((OUT / f"{cid}.json").read_text(encoding="utf-8")).get("sha256")
        if got_sha != sha:
            print("REFUSE: prior candidate mutated", cid)
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
    print("CANDIDATE6_UNCHANGED=true")
    print("CANDIDATE7_UNCHANGED=true")
    print("RUNTIME_CHANGED=true")
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
