"""Assert 20260817 OPVAL identity. Does not mutate V25 or Candidate-5."""
from __future__ import annotations

import json
import sys
from pathlib import Path

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
    SELECTOR_PATH,
    V25_ACTIVATION_ID,
    collect_runtime_inventory,
    load_activation_manifest,
    load_active_selector,
    verify_generator_inventory_coverage,
    verify_manifest_self_sha,
    verify_runtime_inventory,
)

V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
C5_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G4_5"
C5_SHA = "eb952288c4be2dcb586db877f09166d9197564b6658fb72525a66f038e41e8e6"
C5_SELECTOR = OUT / "active_v1r_candidate_v26g4_5.json"
OPVAL_SELECTOR = NATIVE / "results" / "research" / "v26g6_targeted_rca" / "active_v1r_opval_20260817.json"


def check() -> dict:
    fail: list[str] = []
    v25_sel = json.loads(SELECTOR_PATH.read_text(encoding="utf-8"))
    v25 = json.loads((OUT / f"{V25_ACTIVATION_ID}.json").read_text(encoding="utf-8"))
    if v25_sel.get("activation_id") != V25_ACTIVATION_ID or v25.get("sha256") != V25_SHA:
        fail.append("V25_MUTATED")
    ok25, got25, calc25 = verify_manifest_self_sha(v25)
    if not (ok25 and got25 == V25_SHA and calc25 == V25_SHA):
        fail.append("V25_SELF_SHA")

    c5_sel = load_active_selector(path=C5_SELECTOR)
    if c5_sel.get("activation_id") != C5_ID or c5_sel.get("activation_sha") != C5_SHA:
        fail.append("C5_SELECTOR_MUTATED")
    c5 = load_activation_manifest(selector=c5_sel, out_dir=OUT)
    ok5, got5, calc5 = verify_manifest_self_sha(c5)
    if not (ok5 and got5 == C5_SHA and calc5 == C5_SHA):
        fail.append("C5_SELF_SHA")
    if c5.get("candidate_id") != C5_ID or c5.get("sha256") != C5_SHA:
        fail.append("C5_MANIFEST_MUTATED")

    if not OPVAL_SELECTOR.is_file():
        fail.append("OPVAL_SELECTOR_MISSING")
        return {"ok": False, "fail": fail, "opval_id": OPVAL_ACTIVATION_ID}

    osel = json.loads(OPVAL_SELECTOR.read_text(encoding="utf-8"))
    if osel.get("activation_id") != OPVAL_ACTIVATION_ID:
        fail.append(f"opval_id={osel.get('activation_id')}")
    if osel.get("formal_paper_allowed") is True:
        fail.append("OPVAL_SELECTOR_FORMAL_PAPER")
    oman = load_activation_manifest(selector=osel, out_dir=OUT)
    ook, ogot, ocalc = verify_manifest_self_sha(oman)
    if not (ook and ogot == ocalc == osel.get("activation_sha")):
        fail.append("OPVAL_SELF_SHA")
    if oman.get("candidate_id") != OPVAL_ACTIVATION_ID:
        fail.append("OPVAL_MANIFEST_ID")
    if oman.get("candidate_status") != CANDIDATE_STATUS_OPVAL:
        fail.append("OPVAL_STATUS")
    if oman.get("formal_paper_allowed") is not False:
        fail.append("OPVAL_FORMAL_PAPER")
    if oman.get("prospective_allowed") is not False:
        fail.append("OPVAL_PROSPECTIVE")
    if oman.get("strategy_evaluation_allowed") is not False:
        fail.append("OPVAL_STRATEGY_EVALUATION")
    if oman.get("paper_only") is not True:
        fail.append("OPVAL_PAPER_ONLY")
    if oman.get("order_enabled") is not False:
        fail.append("OPVAL_ORDER_ENABLED")
    if oman.get("live_trading_enabled") is not False:
        fail.append("OPVAL_LIVE_TRADING")
    if str(oman.get("submit_cancel_live") or "") != "0/0/0":
        fail.append("OPVAL_SCL")
    if oman.get("not_candidate_6") is not True:
        fail.append("OPVAL_MUST_NOT_BE_CANDIDATE_6")
    inv = verify_runtime_inventory(oman, native_root=NATIVE)
    gen = verify_generator_inventory_coverage(oman)
    if not inv.get("ok"):
        fail.append(f"OPVAL_INVENTORY {inv}")
    if not gen.get("ok"):
        fail.append(f"OPVAL_GENERATOR {gen}")
    now = collect_runtime_inventory(native_root=NATIVE)
    man_inv = {str(k).replace("\\", "/"): str(v) for k, v in (oman.get("runtime_file_sha256") or {}).items()}
    mismatch = sorted(k for k, v in man_inv.items() if now.get(k) != v)
    if mismatch:
        fail.append(f"OPVAL_STALE_REWRITE_NEEDED mismatch={mismatch[:8]}")
    if str(oman.get("runtime_code_git_commit") or "") != current_git_head():
        fail.append("OPVAL_GIT_HEAD")
    if str(oman.get("config_sha256") or "") != current_config_sha():
        fail.append("OPVAL_CONFIG_SHA")
    return {
        "ok": not fail,
        "fail": fail,
        "opval_id": OPVAL_ACTIVATION_ID,
        "opval_sha": osel.get("activation_sha"),
        "status": CANDIDATE_STATUS_OPVAL,
        "UNCERTIFIED": True,
        "formal_paper_allowed": False,
        "prospective_allowed": False,
        "strategy_evaluation_allowed": False,
        "INVALID_FOR_STRATEGY_EVALUATION": True,
        "NOT_PROSPECTIVE_DAY1": True,
        "v25_unchanged": "V25_MUTATED" not in fail,
        "c5_unchanged": "C5_SELECTOR_MUTATED" not in fail and "C5_MANIFEST_MUTATED" not in fail,
        "selector": str(OPVAL_SELECTOR),
        "runtime_inventory_n": len(man_inv),
        "runtime_inventory_digest": oman.get("runtime_inventory_digest"),
        "candidate_source_digest": oman.get("candidate_source_digest"),
        "config_sha256": oman.get("config_sha256"),
        "runtime_code_git_commit": oman.get("runtime_code_git_commit"),
        "strategy_sha": oman.get("strategy_sha"),
        "entry_sha": oman.get("entry_sha"),
        "exit_v2_candidate_sha": oman.get("exit_v2_candidate_sha"),
        "universe_binding_sha": oman.get("universe_binding_sha"),
    }


def main() -> int:
    body = check()
    print(json.dumps(body, indent=2, default=str))
    return 0 if body.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
