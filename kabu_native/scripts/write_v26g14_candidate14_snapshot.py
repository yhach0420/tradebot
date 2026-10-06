#!/usr/bin/env python
"""Write UNCERTIFIED immutable V26-G14 Candidate-14 snapshot (corrected Passive Fill).

Runtime-only execution-semantics correction. Does not overwrite Candidate-13.
Does not mutate Formal V25 or Candidates 6–13. Does not Formal-freeze V26.
Does not enable Formal Paper. Does not start Paper/OPVAL.
Does rewrite the identity-only OPVAL runtime pointer so the next Paper cannot
start on Candidate-13. OPVAL_CURRENT_TRADING_DAY identity is not rewritten.
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

from small_paper.opval_runtime_candidate import (  # noqa: E402
    OPVAL_RUNTIME_CANDIDATE_SELECTOR_NAME,
    opval_runtime_candidate_selector_path,
)
from small_paper.v1r_activation_binding import (  # noqa: E402
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
from small_paper.v1r_native_entry_live import FEATURE_ORDER  # noqa: E402
from small_paper.v1r_primary_runtime import CLOCK_GRID, WAIT_SEC  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
CANDIDATE_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G14_14"
SELECTOR_CANDIDATE = OUT / "active_v1r_candidate_v26g14_14.json"
OPVAL_SELECTOR = OUT / OPVAL_RUNTIME_CANDIDATE_SELECTOR_NAME
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
C12_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G12_12"
C12_SHA = "7769527e34e6b2df323a36c0b65162d603a5bf55b2f62120b5d3e42fd7abff95"
C13_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G13_13"
C13_SHA = "6810374b3cb929ebda08a070937e4beab8c5fa07179af1887df2d025aff6c8c4"
C13_SELECTOR = OUT / "active_v1r_candidate_v26g13_13.json"
C12_SELECTOR = OUT / "active_v1r_candidate_v26g12_12.json"
C11_SELECTOR = OUT / "active_v1r_candidate_v26g11_11.json"
C10_SELECTOR = OUT / "active_v1r_candidate_v26g10_10.json"
ALLOWED_RUNTIME_DRIFT = frozenset(
    {
        "src/small_paper/v1r_activation_binding.py",
        "src/small_paper/v1r_native_entry_live.py",
        "src/research/e1_x34a_execution_policy/arms.py",
        "src/research/e1_x34a_execution_policy/executable_board.py",
    }
)
REQUIRED_CHANGED = frozenset(
    {
        "src/small_paper/v1r_native_entry_live.py",
        "src/research/e1_x34a_execution_policy/arms.py",
    }
)
REQUIRED_NEW = frozenset({"src/research/e1_x34a_execution_policy/executable_board.py"})
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
    C12_ID,
    C13_ID,
)


def _sha(path: Path) -> str:
    return file_sha256(path) if path.is_file() else ""


def main() -> int:
    dest = OUT / f"{CANDIDATE_ID}.json"
    if dest.is_file() or SELECTOR_CANDIDATE.is_file():
        print("REFUSE_OVERWRITE: candidate-14 snapshot already exists", dest, SELECTOR_CANDIDATE)
        return 2
    c13_path = OUT / f"{C13_ID}.json"
    if not c13_path.is_file():
        print("REFUSE: Candidate-13 snapshot missing")
        return 2

    if WAIT_SEC != 1.0:
        print("REFUSE: WAIT_SEC mutated", WAIT_SEC)
        return 2
    if FEATURE_ORDER != (
        "spread_bps",
        "imbalance",
        "mid_ret_60s",
        "mid_ret_180s",
        "event_rate_60s",
        "log_bid_qty",
    ):
        print("REFUSE: FEATURE_ORDER mutated")
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
    if prior_shas[C13_ID] != C13_SHA:
        print("REFUSE: Candidate-13 manifest mutated")
        return 2
    if prior_shas[C12_ID] != C12_SHA:
        print("REFUSE: Candidate-12 manifest mutated")
        return 2
    if prior_shas[C11_ID] != C11_SHA:
        print("REFUSE: Candidate-11 manifest mutated")
        return 2
    if prior_shas[C10_ID] != C10_SHA:
        print("REFUSE: Candidate-10 manifest mutated")
        return 2
    if prior_shas[C9_ID] != C9_SHA:
        print("REFUSE: Candidate-9 manifest mutated")
        return 2
    if prior_shas[C7_ID] != C7_SHA:
        print("REFUSE: Candidate-7 manifest mutated")
        return 2
    if prior_shas[C6_ID] != C6_SHA:
        print("REFUSE: Candidate-6 manifest mutated")
        return 2
    if C13_SELECTOR.is_file():
        c13s = json.loads(C13_SELECTOR.read_text(encoding="utf-8"))
        if c13s.get("activation_id") != C13_ID or c13s.get("activation_sha") != C13_SHA:
            print("REFUSE: Candidate-13 identity selector mutated")
            return 2
    if C12_SELECTOR.is_file():
        c12s = json.loads(C12_SELECTOR.read_text(encoding="utf-8"))
        if c12s.get("activation_id") != C12_ID or c12s.get("activation_sha") != C12_SHA:
            print("REFUSE: Candidate-12 identity selector mutated")
            return 2
    if C11_SELECTOR.is_file():
        c11s = json.loads(C11_SELECTOR.read_text(encoding="utf-8"))
        if c11s.get("activation_id") != C11_ID or c11s.get("activation_sha") != C11_SHA:
            print("REFUSE: Candidate-11 identity selector mutated")
            return 2
    if C10_SELECTOR.is_file():
        c10s = json.loads(C10_SELECTOR.read_text(encoding="utf-8"))
        if c10s.get("activation_id") != C10_ID or c10s.get("activation_sha") != C10_SHA:
            print("REFUSE: Candidate-10 identity selector mutated")
            return 2

    c13 = json.loads(c13_path.read_text(encoding="utf-8"))
    c12 = json.loads((OUT / f"{C12_ID}.json").read_text(encoding="utf-8"))
    c10 = json.loads((OUT / f"{C10_ID}.json").read_text(encoding="utf-8"))
    c8 = json.loads((OUT / f"{C8_ID}.json").read_text(encoding="utf-8"))
    C8_SHA = str(c8.get("sha256") or "")

    v25_sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    v25 = json.loads((OUT / f"{V25_ACTIVATION_ID}.json").read_text(encoding="utf-8"))
    if v25_sel.get("activation_id") != V25_ACTIVATION_ID or v25.get("sha256") != V25_SHA:
        print("REFUSE: V25 selector/manifest mutated")
        return 2
    if manifest_content_sha(v25) != V25_SHA:
        print("REFUSE: V25 manifest self-sha drift")
        return 2

    for key in ("entry_sha", "anchor_sha", "exit_v2_candidate_sha", "strategy_sha"):
        if str(c13.get(key) or "") != str(v25.get(key) or "") or str(c13.get(key) or "") != str(c10.get(key) or ""):
            print("REFUSE: strategy identity drift vs V25/C13/C10", key)
            return 2
        if str(c13.get(key) or "") != str(c12.get(key) or ""):
            print("REFUSE: strategy identity drift vs C12", key)
            return 2

    cov = audit_runtime_inventory_coverage(native_root=NATIVE)
    if not cov.get("ok") or cov.get("runtime_critical_uncovered_files"):
        print("V1R_V26G14_INVENTORY_COVERAGE_FAIL", json.dumps(cov, indent=2, default=str))
        return 2

    inv = collect_runtime_inventory(native_root=NATIVE)
    if len(inv) != len(RUNTIME_DEPENDENCY_RELS):
        print("REFUSE: inventory length != generator", len(inv), len(RUNTIME_DEPENDENCY_RELS))
        return 2
    dual_rel = "src/small_paper/v1r_live_dual_lane.py"
    if str(inv.get(dual_rel) or "") != C10_DUALLANE_SHA:
        print("REFUSE: Candidate-10 DualLane mutated", inv.get(dual_rel), C10_DUALLANE_SHA)
        return 2
    c13_inv = c13.get("runtime_file_sha256") or {}
    drifted = [
        rel
        for rel in RUNTIME_DEPENDENCY_RELS
        if rel not in ALLOWED_RUNTIME_DRIFT
        and rel in c13_inv
        and str(c13_inv.get(rel) or "") != str(inv.get(rel) or "")
    ]
    if drifted:
        print("REFUSE: unexpected inventory drift vs Candidate-13 G=", len(drifted), drifted[:20])
        return 2
    new_keys = [rel for rel in RUNTIME_DEPENDENCY_RELS if rel not in c13_inv]
    if any(rel not in ALLOWED_RUNTIME_DRIFT for rel in new_keys):
        print("REFUSE: unexpected new inventory keys vs Candidate-13", new_keys)
        return 2
    if any(rel not in new_keys for rel in REQUIRED_NEW):
        print("REFUSE: expected new inventory key missing", REQUIRED_NEW, new_keys)
        return 2
    runtime_changed = sorted(
        {
            rel
            for rel in RUNTIME_DEPENDENCY_RELS
            if rel in ALLOWED_RUNTIME_DRIFT and str(c13_inv.get(rel) or "") != str(inv.get(rel) or "")
        }
    )
    for rel in REQUIRED_CHANGED:
        if rel not in runtime_changed:
            print("REFUSE: expected fill-gate drift missing", rel)
            return 2
    if "src/research/e1_x34a_execution_policy/executable_board.py" not in runtime_changed:
        print("REFUSE: expected executable_board inventory addition")
        return 2

    prev_opval = {}
    opval_path = opval_runtime_candidate_selector_path(native_root=NATIVE)
    if opval_path.is_file():
        prev_opval = json.loads(opval_path.read_text(encoding="utf-8"))
        if prev_opval.get("activation_id") not in {C13_ID, CANDIDATE_ID}:
            print("REFUSE: unexpected current OPVAL pointer", prev_opval.get("activation_id"))
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
    native_live = "src/small_paper/v1r_native_entry_live.py"
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
            "parent_candidate12_id": C12_ID,
            "parent_candidate12_sha": C12_SHA,
            "parent_candidate13_id": C13_ID,
            "parent_candidate13_sha": C13_SHA,
            "supersede_reason": "V26G14_CORRECTED_PASSIVE_FILL_EXECUTABLE_CONTINUOUS_BOARD",
            "notification_only_change": False,
            "submit_cancel_live": "0/0/0",
            "strategy_affecting_diff_g": 0,
            "STRATEGY_CHANGED": False,
            "CLOCK_GRID_CHANGED": False,
            "ENTRY_CHANGED": False,
            "EXIT_CHANGED": False,
            "STRATEGY_RETUNED": False,
            "EXECUTION_SEMANTICS_DEFECT_FIXED": True,
            "EXECUTION_SEMANTICS_CHANGED": "CORRECTED_DEFECT",
            "WAIT_SEC": WAIT_SEC,
            "fill_price_rule": "limit_price",
            "fill_eligibility_sot": "is_executable_continuous_board",
            "CLOCK_GRID": [[int(h), int(m)] for h, m in CLOCK_GRID],
            "runtime_code_git_commit": head,
            "runtime_code_sha": str(inv.get(native_live) or ""),
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
            "parent_runtime_candidate_id": C13_ID,
            "parent_runtime_candidate_sha": C13_SHA,
            "parent_runtime_candidate_status": "UNCERTIFIED_IMMUTABLE_LEFT_IN_PLACE",
            "allowed_runtime_drift_vs_candidate13": sorted(ALLOWED_RUNTIME_DRIFT),
            "runtime_changed_rels": runtime_changed,
            "candidate10_duallane_unchanged": True,
            "candidate10_duallane_sha": C10_DUALLANE_SHA,
            "candidate13_unchanged": True,
            "previous_opval_runtime_candidate_selector": prev_opval,
            "paper_not_started": True,
            "opval_launcher_not_started": True,
        }
    )
    for key in ("entry_sha", "anchor_sha", "exit_v2_candidate_sha", "strategy_sha"):
        if str(body.get(key) or "") != str(c13.get(key) or ""):
            print("REFUSE: strategy SHA != Candidate-13", key, body.get(key), c13.get(key))
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
            "Identity-only UNCERTIFIED candidate-14 selector; not the active Formal selector. "
            "Corrected Passive Fill. Not Candidate-13. OPVAL current-trading-day identity is not rewritten."
        ),
    }
    SELECTOR_CANDIDATE.write_text(json.dumps(selector, indent=2) + "\n", encoding="utf-8")

    opval_selector = {
        "schema": SELECTOR_SCHEMA,
        "activation_id": CANDIDATE_ID,
        "activation_sha": body["sha256"],
        "manifest_relpath": f"{CANDIDATE_ID}.json",
        "note": (
            "Identity-only pointer to the current immutable OPVAL runtime candidate. "
            "Candidate-14 corrected Passive Fill. Not Formal. Not a freeze. "
            "Updating this pointer does not mutate Candidate-6/7/8/9/10/11/12/13 or Formal V25. "
            "OPVAL_CURRENT_TRADING_DAY is rewritten from the resolved candidate."
        ),
    }
    OPVAL_SELECTOR.write_text(json.dumps(opval_selector, indent=2) + "\n", encoding="utf-8")

    v25_after = json.loads((OUT / f"{V25_ACTIVATION_ID}.json").read_text(encoding="utf-8"))
    sel_after = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    c13_after = json.loads(c13_path.read_text(encoding="utf-8"))
    if v25_after.get("sha256") != V25_SHA or sel_after.get("activation_id") != V25_ACTIVATION_ID:
        print("REFUSE: V25 mutated during candidate write")
        dest.unlink(missing_ok=True)
        SELECTOR_CANDIDATE.unlink(missing_ok=True)
        return 2
    if c13_after.get("sha256") != C13_SHA:
        print("REFUSE: Candidate-13 mutated during candidate write")
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
    print(f"RUNTIME_CODE_SHA={body['runtime_code_sha']}")
    print(f"RUNTIME_CODE_GIT_COMMIT={head}")
    print(f"RUNTIME_INVENTORY_N={len(inv)}")
    print(f"RUNTIME_INVENTORY_DIGEST={body['runtime_inventory_digest']}")
    print(f"CANDIDATE_SOURCE_DIGEST={body['candidate_source_digest']}")
    print(f"CONFIG_SHA={body['config_sha256']}")
    print(f"STRATEGY_SHA={body.get('strategy_sha')}")
    print(f"ENTRY_SHA={body.get('entry_sha')}")
    print(f"EXIT_SHA={body.get('exit_v2_candidate_sha')}")
    print(f"ANCHOR_SHA={body.get('anchor_sha')}")
    print(f"SELECTOR={SELECTOR_CANDIDATE}")
    print(f"OPVAL_SELECTOR={OPVAL_SELECTOR}")
    print(f"MANIFEST={dest}")
    print("STRATEGY_CHANGED=false")
    print("CLOCK_GRID_CHANGED=false")
    print("ENTRY_CHANGED=false")
    print("EXIT_CHANGED=false")
    print("EXECUTION_SEMANTICS_CHANGED=CORRECTED_DEFECT")
    print("V25_UNCHANGED=true")
    print("CANDIDATE13_UNCHANGED=true")
    print("CANDIDATE10_DUALLANE_UNCHANGED=true")
    print(f"RUNTIME_CHANGED_RELS={runtime_changed}")
    print("STRATEGY_AFFECTING_DIFF_G=0")
    print("PRIOR_CANDIDATES_UNCHANGED=true")
    print("UNCERTIFIED=true")
    print("OPERATIONAL_VALIDATION_ONLY=true")
    print("NOT_FORMAL=true")
    print("INVALID_FOR_STRATEGY_EVALUATION=true")
    print("FORMAL_PAPER_ALLOWED=false")
    print("IMMUTABLE=true")
    print("PAPER_NOT_STARTED=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
