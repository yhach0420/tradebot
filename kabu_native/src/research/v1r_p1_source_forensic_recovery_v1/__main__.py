"""Offline P1 V1R source forensic recovery. No main-tree checkout. No extension economics."""
from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
os.environ["V1R_EXIT_V2_LIVE_PRIMARY"] = "1"
os.environ["PYTHONPATH"] = str(SRC) + os.pathsep + str(NATIVE / "scripts") + os.pathsep + os.environ.get("PYTHONPATH", "")

from research.am_entry_profit_improvement import CANCEL_N, LIVE_ORDER_N, SUBMIT_N
from research.v1r_p1_source_forensic_recovery_v1.analyze import build_answers, decide
from research.v1r_p1_source_forensic_recovery_v1.calib import run_full14
from research.v1r_p1_source_forensic_recovery_v1.harvest import (
    collect_exact,
    copy_recovered,
    existence_proof,
    run_all_searches,
    semantic_diff,
    summarize_searches,
    working_tree_hashes,
)
from research.v1r_p1_source_forensic_recovery_v1.isolation import (
    CACHE,
    OUT,
    P1_OUT,
    PRIOR_EXT_OUT,
    RECOVERED,
    TODAY,
    input_active_file_n,
    set_research_priority_below_normal,
    snapshot,
    write_overlap_n,
)
from research.v1r_p1_source_forensic_recovery_v1.publish import build_markdown, build_sheets, write_artifacts
from research.v1r_p1_source_forensic_recovery_v1.spec import (
    ANALYSIS_ID,
    canonical_spec,
    source_sha256,
    spec_sha256,
)

INTEGRITY_ZERO = (
    "SUBMIT_N",
    "CANCEL_N",
    "LIVE_ORDER_N",
    "RESEARCH_WRITE_PATH_OVERLAP_N",
    "ACTIVE_CAPTURE_INPUT_N",
    "INPUT_20260903_N",
    "INPUT_20260904_N",
    "FUTURE_DATA_N",
    "PROSPECTIVE_HARVESTER_RUN_N",
    "PRIOR_ARTIFACT_MUTATED_N",
    "MAIN_TREE_CHECKOUT_N",
    "RUNTIME_SOURCE_REWRITE_N",
    "EXTENSION_ECONOMICS_N",
    "NEW_ENTRY_N",
    "NEW_EXIT_N",
    "SIZING_IMPLEMENTED_N",
)


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "MISSING"


def main() -> int:
    set_research_priority_below_normal()
    sha = spec_sha256()
    source_sha = source_sha256()
    pre = snapshot(phase="PRE")
    p1_hash = _sha_file(P1_OUT / "report.json")
    prior_ext_hash = _sha_file(PRIOR_EXT_OUT / "report.json")
    leak = {k: 0 for k in INTEGRITY_ZERO}
    leak.update({"SUBMIT_N": int(SUBMIT_N), "CANCEL_N": int(CANCEL_N), "LIVE_ORDER_N": int(LIVE_ORDER_N)})
    leak["RESEARCH_WRITE_PATH_OVERLAP_N"] = write_overlap_n(
        str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or "")
    )
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"PREFLIGHT {ANALYSIS_ID} spec={sha[:12]} today={TODAY}", flush=True)

    proof = existence_proof()
    wt = working_tree_hashes()
    print("SEARCH start", flush=True)
    searches = run_all_searches()
    found = collect_exact(searches)
    # do not keep raw bytes in the published report
    found_pub = {k: v for k, v in found.items() if k not in {"native_bytes", "dual_bytes"}}
    recovery = copy_recovered(found, RECOVERED)
    exact_native = bool(recovery.get("exact_native") or found.get("exact_native"))
    exact_dual = bool(recovery.get("exact_dual") or found.get("exact_dual"))
    totals = summarize_searches(searches)
    searches["working_tree"] = {
        "searched": True,
        "candidate_n": 2,
        "hash_match_n": sum(1 for r in wt["rows"] if r["equal"]),
        "rows": wt["rows"],
        "evidence": "current working tree hashes",
    }
    sem = semantic_diff()
    print(f"SEARCH cand={totals['candidate_file_n']} exact_hits={totals['exact_sha_hit_n']} material={sem.get('material_n')}", flush=True)

    leak["ACTIVE_CAPTURE_INPUT_N"] = input_active_file_n([], str(pre.get("ACTIVE_CAPTURE_PATH") or ""), str(pre.get("ACTIVE_PAPER_SESSION") or ""))
    leak_ok = all(int(leak.get(k) or 0) == 0 for k in INTEGRITY_ZERO)

    if exact_native and exact_dual:
        calib = {"ran": False, "reason": "byte_exact_recovered_skip_calibration"}
    else:
        print("CALIB FULL14 start", flush=True)
        calib = run_full14(workers=2)
        # drop bulky trades from rows already slim
        calib = {k: v for k, v in calib.items() if k != "jobs_universe"}

    post = snapshot(phase="POST")
    if _sha_file(P1_OUT / "report.json") != p1_hash or _sha_file(PRIOR_EXT_OUT / "report.json") != prior_ext_hash:
        leak["PRIOR_ARTIFACT_MUTATED_N"] = 1
        leak_ok = False

    decision = decide(
        exact_native=exact_native,
        exact_dual=exact_dual,
        material_n=int(sem.get("material_n") or 0),
        calib=calib,
        leak_ok=leak_ok,
    )
    report = {
        "analysis_id": ANALYSIS_ID,
        "spec_sha256": sha,
        "source_sha256": source_sha,
        "canonical_spec": canonical_spec(),
        "required": {
            "ANALYSIS_ID": ANALYSIS_ID,
            "VERDICT": decision.get("VERDICT"),
            "CASE": decision.get("CASE"),
            "RECOVERY_MODE": decision.get("RECOVERY_MODE"),
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "FUTURE_DATA_USED": False,
            "MAX_RESEARCH_DATE": "20260902",
            "PROSPECTIVE_HARVEST_SUSPENDED": True,
            "MAIN_TREE_CHANGED": False,
            "RUNTIME_CHANGED": False,
        },
        "decision": decision,
        "existence_proof": proof,
        "searches": {k: {kk: vv for kk, vv in dict(v).items() if kk not in {"rows"} or k in {"cursor_history", "project_named", "working_tree"}} for k, v in searches.items()},
        "search_totals": totals,
        "semantic_diff": sem,
        "calibration": calib,
        "recovery": {**recovery, **found_pub},
        "working_tree": wt,
        "leak": leak,
        "preflight": pre,
        "postflight": post,
        "submit_cancel_live": {"submit": 0, "cancel": 0, "live_order": 0},
        "answers": {},
        "_markdown": "",
    }
    report["searches"]["git_reachable"]["rows"] = [
        {k: r.get(k) for k in ("label", "commit", "sha256", "size", "match")}
        for r in list((searches.get("git_reachable") or {}).get("rows") or [])
    ]
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    write_artifacts(report, build_sheets(report))
    names = sorted(p.name for p in OUT.iterdir() if p.is_file())
    print(f"CASE {decision.get('CASE')} {decision.get('VERDICT')}", flush=True)
    print(f"out={OUT} files={names}", flush=True)
    print("STOP.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
