"""Phase A-R3 orchestrator: structural coverage + official tick evidence.

NO X6 economics. Verdicts: E1_X6_RAW_REDESIGN_P1_R3_READY /
E1_X6_RAW_REDESIGN_P1_R3_BLOCKED / E1_X6_RESEARCH_PAUSED_FOR_PAPER.
"""
from __future__ import annotations

import os

for _k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_k] = "1"

import argparse
import json
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import guard as guard_mod
from .asof_coverage import canonical_day_bundle
from .decision_coverage_r3 import AUDIT_EXPECT, scan_day_r3
from .history import SUPERSEDED_RUNS
from .p1 import build_p1_lock
from .protected_manifest import build_protected_manifest, manifests_equal
from .raw_inventory import inventory_raw_day, known_excluded_windows
from .registry import build_candidate_registry
from .replay_order import REPLAY_ORDER_CONTRACT
from .report import atomic_publish
from .source_manifest import DAYS, build_source_manifest
from .store import load_checkpoint, run_root, save_checkpoint, sha256_file, sha256_obj, write_json
from .tick_official_r3 import classify_universe_r3
from .windows import build_analysis_mask_r1

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
PLAN_DOC = NATIVE_ROOT / "kabu_native" / "docs" / "e1_x6_plan_v3_raw_feature_redesign.md"
R2_RUN_ID = "e1x6r3r2_20260803_040009_4d87ffa4"
R2_REPORT = (Path.home() / "e1x6_research_store" / "raw_feature_redesign" / R2_RUN_ID
             / "published" / "report.json")
R2_REPORT_SHA = "18c8b7497ddcb43395b51a2a5eaac788c5d8491b040591c74bafd228a23ec654"
R2_BASE_RECUT_SHA = "138f74676a3ffd3f303f2bfdeb529c9bd4369a0f13f59bb805e65690aefa909f"
R2_BASE_RECUT = (Path.home() / "e1x6_research_store" / "raw_feature_redesign" / R2_RUN_ID
                 / "e1x5_base_recut.json")
GATE_MIN = 0.90
AUDIT_TOL = 1e-5


def _pause(run_id: str, guard_res: dict[str, Any], done: dict[str, Any]) -> None:
    write_json(run_root(run_id) / "paused.json", {
        "verdict": "E1_X6_RESEARCH_PAUSED_FOR_PAPER",
        "guard": guard_res, "progress": done,
        "paused_at": datetime.now().astimezone().isoformat(),
    })
    print(f"verdict: E1_X6_RESEARCH_PAUSED_FOR_PAPER run_id={run_id}")
    sys.exit(0)


def _guard_or_pause(run_id: str, done: dict[str, Any]) -> None:
    res = guard_mod.paper_guard_check(NATIVE_ROOT, run_root(run_id))
    if not res["ok"]:
        _pause(run_id, res, done)


def _run_tests() -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{NATIVE_ROOT / 'research'};{NATIVE_ROOT / 'src'}"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "--tb=short",
         "-p", "no:cacheprovider",
         str(NATIVE_ROOT / "tests" / "research" / "e1_x6_raw_redesign")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(NATIVE_ROOT), env=env, timeout=3600,
    )
    rows = []
    for line in (proc.stdout or "").splitlines():
        ls = line.strip()
        for status in ("PASSED", "FAILED", "ERROR"):
            if ls.startswith(status + " "):
                rows.append({"test": ls.split(" ", 1)[1].split(" - ")[0], "outcome": status})
    passed = sum(1 for r in rows if r["outcome"] == "PASSED")
    return {"exit_code": proc.returncode, "total": len(rows), "passed": passed,
            "failed": len(rows) - passed, "rows": rows, "tail": (proc.stdout or "")[-2000:]}


def _p1_diff(old_p1: dict[str, Any], new_p1: dict[str, Any]) -> dict[str, list[str]]:
    ok, nk = set(old_p1), set(new_p1)
    return {
        "added": sorted(nk - ok),
        "removed": sorted(ok - nk),
        "changed": [k for k in sorted(ok & nk)
                    if sha256_obj(old_p1[k]) != sha256_obj(new_p1[k])],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    guard_mod.apply_thread_caps()
    prio_ok = guard_mod.set_below_normal_priority()
    run_id = args.run_id or (
        f"e1x6r3r3_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
    )
    root = run_root(run_id)
    print(f"run_id={run_id} store={root} below_normal={prio_ok}")
    _guard_or_pause(run_id, {"stage": "start"})

    pm_before_fp = root / "paper_protected_manifest_before.json"
    if pm_before_fp.is_file():
        pm_before = json.loads(pm_before_fp.read_text(encoding="utf-8"))
    else:
        pm_before = build_protected_manifest(REPO_ROOT)
        write_json(pm_before_fp, pm_before)
    print(f"protected_manifest before: files={pm_before['files_n']} "
          f"sha={pm_before['manifest_sha256'][:16]}")

    if not R2_REPORT.is_file() or sha256_file(R2_REPORT) != R2_REPORT_SHA:
        print("FAIL: R2 block-evidence report missing or SHA mismatch")
        sys.exit(2)
    r2_report = json.loads(R2_REPORT.read_text(encoding="utf-8"))

    if not R2_BASE_RECUT.is_file():
        print("FAIL: R2 base recut artifact missing")
        sys.exit(2)
    base_art = json.loads(R2_BASE_RECUT.read_text(encoding="utf-8"))
    if base_art.get("artifact_sha256") != R2_BASE_RECUT_SHA:
        print(f"FAIL: R2 base recut artifact_sha256 mismatch "
              f"{base_art.get('artifact_sha256')}")
        sys.exit(2)
    print(f"base recut binding OK sha={R2_BASE_RECUT_SHA[:16]} "
          f"n={base_art['recut_metrics']['completed_trades']}")

    sm_fp = root / "source_manifest.json"
    if sm_fp.is_file():
        sm = json.loads(sm_fp.read_text(encoding="utf-8"))
    else:
        print("hashing raw + canonical inputs (read-only)...")
        sm = build_source_manifest(NATIVE_ROOT)
        write_json(sm_fp, sm)
    binding = {"source_manifest_sha256": sm["source_manifest_sha256"], "run_id": run_id}
    print(f"source_manifest sha={sm['source_manifest_sha256'][:16]}")

    inv_days: dict[str, Any] = {}
    cov_days: dict[str, Any] = {}
    regr_days: dict[str, Any] = {}
    for day in DAYS:
        ck = load_checkpoint(run_id, f"r3day_{day}", binding=binding)
        if ck is not None:
            inv_days[day] = ck["inventory"]
            cov_days[day] = ck["coverage"]
            regr_days[day] = ck["regressions"]
            print(f"[R3] {day}: resumed")
            continue
        _guard_or_pause(run_id, {"stage": f"day_{day}", "done": sorted(cov_days)})
        t0 = datetime.now()
        cb = canonical_day_bundle(NATIVE_ROOT, day)
        cov = scan_day_r3(NATIVE_ROOT, day, cb["universe"])
        raw = inventory_raw_day(NATIVE_ROOT, day)
        row = {"inventory": {**raw, "canonical": cb["canonical"]},
               "coverage": cov, "regressions": cb["regression_audit"]}
        save_checkpoint(run_id, f"r3day_{day}", row, binding=binding)
        inv_days[day] = row["inventory"]
        cov_days[day] = cov
        regr_days[day] = cb["regression_audit"]
        dt = (datetime.now() - t0).total_seconds()
        line = []
        for sk in ("AM", "PM"):
            s = cov["sessions"][sk]
            if "due_symbol_grid_n" in s:
                line.append(
                    f"{sk}: struct={s['structural_decision_quote_coverage']} "
                    f"spread={s['spread_healthy_rate']} "
                    f"mc={s['market_context_coverage']} due={s['due_symbol_grid_n']}"
                )
        print(f"[R3] {day}: {'; '.join(line)} ({dt:.0f}s)")

    excl = known_excluded_windows()
    mask = build_analysis_mask_r1(cov_days, excl)
    included = [wid for wid, row in mask["windows"].items() if row["included"]]
    print(f"analysis_mask_id={mask['analysis_mask_id']} included_windows={len(included)}")
    if mask["analysis_mask_id"] != "MASK_R1_ea8f67eb1b559218":
        print(f"FAIL: analysis_mask_id changed from R1/R2 freeze: {mask['analysis_mask_id']}")
        sys.exit(2)

    # ---- structural + market gates ----
    per_window: dict[str, Any] = {}
    for wid in included:
        day, sk = wid.split("_")
        s = cov_days[day]["sessions"][sk]
        sc = s["structural_decision_quote_coverage"]
        mc = s["market_context_coverage"]
        per_window[wid] = {
            "structural_decision_quote_coverage": sc,
            "gate_structural": sc is not None and sc >= GATE_MIN,
            "market_context_coverage": mc,
            "gate_market": mc is not None and mc >= GATE_MIN,
            "spread_healthy_rate": s["spread_healthy_rate"],
            "due_symbol_grid_n": s["due_symbol_grid_n"],
            "structural_n": s["structural_decision_quote_available_n"],
            "spread_healthy_n": s["spread_healthy_n"],
            "spread_unhealthy_n": s["spread_unhealthy_n"],
            "r2_mixed_decision_quote_coverage": (
                r2_report["r2"]["coverage_days"][day]["sessions"][sk]
                .get("decision_quote_coverage")
            ),
        }
    fail_struct = [w for w, r in per_window.items() if not r["gate_structural"]]
    fail_mkt = [w for w, r in per_window.items() if not r["gate_market"]]
    min_sc = min((r["structural_decision_quote_coverage"] for r in per_window.values()
                  if r["structural_decision_quote_coverage"] is not None), default=None)
    min_mc = min((r["market_context_coverage"] for r in per_window.values()
                  if r["market_context_coverage"] is not None), default=None)
    # weighted structural
    tot_due = sum(r["due_symbol_grid_n"] for r in per_window.values())
    tot_struct = sum(r["structural_n"] for r in per_window.values())
    weighted = round(tot_struct / tot_due, 6) if tot_due else None

    # audit expectation cross-check (not hard-coded READY — discrepancy => BLOCK)
    audit_diffs = []
    for wid, exp in AUDIT_EXPECT.items():
        if wid in ("min_included_approx", "weighted_included_approx"):
            continue
        if wid not in per_window:
            continue
        got = per_window[wid]
        if abs(got["structural_n"] - exp["structural_n"]) > 0 or abs(
                got["due_symbol_grid_n"] - exp["due_n"]) > 0:
            audit_diffs.append({
                "window": wid, "expected": exp,
                "got_structural_n": got["structural_n"],
                "got_due_n": got["due_symbol_grid_n"],
                "got_coverage": got["structural_decision_quote_coverage"],
            })
        elif abs((got["structural_decision_quote_coverage"] or 0) - exp["coverage"]) > AUDIT_TOL:
            audit_diffs.append({
                "window": wid, "expected": exp,
                "got_coverage": got["structural_decision_quote_coverage"],
            })
    if min_sc is not None and abs(min_sc - AUDIT_EXPECT["min_included_approx"]) > 5e-4:
        audit_diffs.append({"kind": "min_included", "expected": AUDIT_EXPECT["min_included_approx"],
                            "got": min_sc})
    if weighted is not None and abs(weighted - AUDIT_EXPECT["weighted_included_approx"]) > 5e-4:
        audit_diffs.append({"kind": "weighted", "expected": AUDIT_EXPECT["weighted_included_approx"],
                            "got": weighted})

    gates = {
        "per_window": per_window,
        "fail_structural": fail_struct,
        "fail_market": fail_mkt,
        "min_structural_decision_quote_coverage": min_sc,
        "min_market_context_coverage": min_mc,
        "weighted_structural_decision_quote_coverage": weighted,
        "audit_expectation_diffs": audit_diffs,
        "threshold": GATE_MIN,
        "all_pass": not fail_struct and not fail_mkt and not audit_diffs,
    }
    print(f"gates: struct_min={min_sc} weighted={weighted} mc_min={min_mc} "
          f"fail_s={fail_struct} fail_m={fail_mkt} audit_diffs={len(audit_diffs)}")

    # ---- official tick ----
    agg: dict[str, dict[str, list]] = {}
    for cov in cov_days.values():
        for sym, bins in cov["tick_evidence"].items():
            a = agg.setdefault(sym, {})
            for rep, (minc, cnt) in bins.items():
                cur = a.get(rep)
                if cur is None:
                    a[rep] = [minc, cnt]
                else:
                    cur[0] = min(cur[0], minc)
                    cur[1] += cnt
    all_universe = sorted({s for cov in cov_days.values() for s in cov["universe"]})
    tick = classify_universe_r3(REPO_ROOT, all_universe, agg, DAYS)
    print(f"tick: unresolved={tick['unresolved']} integrity={tick['evidence_integrity_failures']}")

    _guard_or_pause(run_id, {"stage": "tests"})
    tests = _run_tests()
    write_json(root / "tests_result.json", tests)
    print(f"tests: {tests['passed']}/{tests['total']} passed (exit={tests['exit_code']})")

    all_pass = gates["all_pass"] and not tick["unresolved"] and not tick["evidence_integrity_failures"]
    registry = build_candidate_registry(
        core_feature_coverage_ok=all_pass,
        market_feature_coverage_ok=all_pass,
        vwap_available=False, volume_available=False, board_available=False,
    )
    if not all_pass:
        for r in registry:
            r["disable_reason"] = "R3_GATE_FAIL"

    quote_ok = not fail_struct and not audit_diffs
    mkt_ok = not fail_mkt
    field_usability = {
        "quote": ("USABLE_STRUCTURAL_COVERAGE_GATE_PASS" if quote_ok
                  else "USABLE_STRUCTURAL_COVERAGE_GATE_FAIL"),
        "spread": "STRATEGY_FILTER_NOT_DATA_COVERAGE",
        "market_context": "USABLE" if mkt_ok else "FAIL",
        "volume": "DIAGNOSTIC_ONLY_NOT_CANDIDATE_AXIS",
        "vwap": "DIAGNOSTIC_ONLY_NOT_CANDIDATE_AXIS",
        "board10": "DIAGNOSTIC_ONLY_NOT_CANDIDATE_AXIS",
        "r2_mixed_decision_quote_preserved": True,
        "r2_fail_windows": ["20260724_AM", "20260729_AM"],
        "r1_quote_min_preserved": 0.752485,
    }

    inventory_summary = {
        "days_n": len(inv_days),
        "raw_total_lines": int(sum(d["raw_total_lines"] for d in inv_days.values())),
        "canonical_total": int(sum(d["canonical"]["canonical_events"] for d in inv_days.values())),
        "known_excluded_windows": excl,
        "analysis_mask_id": mask["analysis_mask_id"],
        "per_day_session_sha256": sha256_obj({d: inv_days[d]["sessions"] for d in inv_days}),
    }
    r2_payload = {
        "r1_block_evidence": SUPERSEDED_RUNS.get("e1x6r3r1_20260803_031244_a7d98591"),
        "timestamp_policy": r2_report["p1"].get("r2", {}).get("timestamp_policy"),
        "source_semantics": {
            day: {k: v.get("semantics") for k, v in cov_days[day]["source_semantics"].items()
                  if isinstance(v, dict) and "semantics" in v}
            for day in cov_days
        },
        "decision_gates": r2_report["r2"]["gates"],
        "tick_official": {"superseded_by": "r3.tick_official"},
        "base_binding_r2": {
            "comparable": True,
            "artifact_sha256": R2_BASE_RECUT_SHA,
            "analysis_mask_id": "MASK_R1_ea8f67eb1b559218",
            "recut_metrics": base_art["recut_metrics"],
            "note": "R3 reconfirmed binding; no re-cut",
        },
    }
    r3_payload = {
        "r2_block_evidence": SUPERSEDED_RUNS[R2_RUN_ID] | {"run_id": R2_RUN_ID},
        "structural_vs_spread": {
            "structural_gate": "structural_decision_quote_coverage >= 0.90",
            "spread_role": "STRATEGY_FILTER_NOT_DATA_COVERAGE (50bps unchanged)",
            "r2_mixed_preserved_from": R2_REPORT_SHA,
        },
        "decision_gates": gates,
        "tick_official": {
            "master_path": tick["master_path"],
            "master_sha256": tick["master_sha256"],
            "evidence_manifest_sha256": tick["evidence_manifest_sha256"],
            "evidence_fetched_at_jst": tick["evidence_fetched_at_jst"],
            "rule": tick["rule"],
            "unresolved": tick["unresolved"],
            "supplemental_codes": tick["supplemental_codes"],
            "evidence_integrity_failures": tick["evidence_integrity_failures"],
        },
        "base_binding_r3": {
            "comparable": True,
            "artifact_path": str(R2_BASE_RECUT),
            "artifact_sha256": R2_BASE_RECUT_SHA,
            "analysis_mask_id": "MASK_R1_ea8f67eb1b559218",
            "recut_metrics": base_art["recut_metrics"],
            "unchanged_from_r2": True,
        },
        "field_usability": field_usability,
    }
    p1 = build_p1_lock(
        run_id=run_id, plan_doc_path=PLAN_DOC,
        source_manifest_sha256=sm["source_manifest_sha256"],
        protected_manifest_sha256=pm_before["manifest_sha256"],
        inventory_summary=inventory_summary,
        field_usability=field_usability,
        registry=registry,
        r1={"superseded_previous": SUPERSEDED_RUNS.get("e1x6r3_20260802_233645_144c3aab"),
            "coverage_diff": r2_report["r1"]["coverage_diff"],
            "field_usability_r1": r2_report.get("field_usability"),
            "canonical_regressions": regr_days,
            "analysis_mask": mask,
            "tick_resolver": {"superseded_by": "r3"},
            "base_binding": {"superseded_by": "r3"}},
        r2=r2_payload,
        r3=r3_payload,
    )
    write_json(root / "p1_lock.json", p1)
    print(f"P1_R3 frozen: sha={p1['p1_sha256'][:16]} registry_n={p1['candidate_registry_n']}")

    old_p1_fp = run_root(R2_RUN_ID) / "p1_lock.json"
    p1_diff = {"added": ["(R2 p1 not found)"], "removed": [], "changed": []}
    if old_p1_fp.is_file():
        p1_diff = _p1_diff(json.loads(old_p1_fp.read_text(encoding="utf-8")), p1)
    write_json(root / "p1_diff_r2_r3.json", {
        "r2_run_id": R2_RUN_ID,
        "r2_p1_sha256": SUPERSEDED_RUNS[R2_RUN_ID]["p1_sha256"],
        "r3_p1_sha256": p1["p1_sha256"],
        "diff": p1_diff,
    })

    pm_after = build_protected_manifest(REPO_ROOT)
    write_json(root / "paper_protected_manifest_after.json", pm_after)
    pm_match, pm_diffs = manifests_equal(pm_before, pm_after)
    if not pm_match:
        print(f"FAIL: paper protected manifest changed: {pm_diffs}")
        sys.exit(2)

    tests_ok = tests["exit_code"] == 0 and tests["failed"] == 0 and tests["total"] > 0
    blocked = []
    if fail_struct:
        blocked.append(f"STRUCTURAL_DECISION_QUOTE_COVERAGE_GATE_FAIL:{fail_struct}")
    if fail_mkt:
        blocked.append(f"MARKET_CONTEXT_COVERAGE_GATE_FAIL:{fail_mkt}")
    if audit_diffs:
        blocked.append(f"AUDIT_EXPECTATION_MISMATCH:{audit_diffs}")
    if tick["unresolved"]:
        blocked.append(f"TICK_OFFICIAL_CLASS_UNRESOLVED:{tick['unresolved']}")
    if tick["evidence_integrity_failures"]:
        blocked.append(f"TICK_EVIDENCE_INTEGRITY:{tick['evidence_integrity_failures']}")
    if not tests_ok:
        blocked.append("TESTS_FAILED")
    verdict = ("E1_X6_RAW_REDESIGN_P1_R3_READY" if not blocked
               else "E1_X6_RAW_REDESIGN_P1_R3_BLOCKED")

    tick_counts: dict[str, int] = {}
    for row in tick["symbol_classes"].values():
        tick_counts[str(row["class"])] = tick_counts.get(str(row["class"]), 0) + 1

    report = {
        "plan_id": "E1_X6_PLAN_V3_RAW_FEATURE_REDESIGN",
        "run_id": run_id,
        "phase": "PHASE_A_R3",
        "verdict": verdict,
        "blocked_reasons": blocked,
        "verdict_basis": {
            "structural_gates_all_pass": not fail_struct and not audit_diffs,
            "market_gates_all_pass": not fail_mkt,
            "tick_all_resolved": not tick["unresolved"],
            "base_comparable": True,
            "tests_ok": tests_ok,
            "paper_protected_manifest_match": pm_match,
        },
        "scope_note": p1["scope_note"],
        "published_at_jst": datetime.now().astimezone().isoformat(),
        "source_manifest_sha256": sm["source_manifest_sha256"],
        "source_manifest_days": {
            d: {k: v for k, v in sm["days"][d].items() if k != "raw_files"} for d in sm["days"]
        },
        "inventory": {
            "days": inv_days,
            "raw_total_lines": inventory_summary["raw_total_lines"],
            "canonical_total": inventory_summary["canonical_total"],
            "known_excluded_windows": excl,
        },
        "field_usability": field_usability,
        "candidate_registry": registry,
        "p1": p1,
        "r1": {
            "superseded_run_id": "e1x6r3r1_20260803_031244_a7d98591",
            "coverage_days": {},
            "coverage_diff": r2_report["r1"]["coverage_diff"],
            "replay_order_contract": REPLAY_ORDER_CONTRACT,
            "canonical_regressions": regr_days,
            "analysis_mask": mask,
            "tick_resolver": {"runtime_resolver_sha256": None, "rule": "see r3",
                              "symbol_classes": {}},
            "base_binding": {"superseded_by": "r3"},
            "p1_diff": p1_diff,
        },
        "r2": {
            "block_evidence_run_id": R2_RUN_ID,
            "block_evidence_report_sha256": R2_REPORT_SHA,
            "gates": r2_report["r2"]["gates"],
            "coverage_days_preserved": True,
            "tick_official_summary": r2_report["r2"].get("tick_official_summary"),
            "base_binding_r2": r2_payload["base_binding_r2"],
            "p1_diff_r1_r2": {},
        },
        "r3": {
            "coverage_days": cov_days,
            "gates": gates,
            "tick_official": tick,
            "tick_official_summary": tick_counts,
            "base_binding_r3": r3_payload["base_binding_r3"],
            "p1_diff_r2_r3": p1_diff,
            "field_usability": field_usability,
        },
        "tests": tests,
        "paper_protected_manifest": {
            "match": pm_match,
            "before_sha256": pm_before["manifest_sha256"],
            "after_sha256": pm_after["manifest_sha256"],
            "files_n": pm_before["files_n"],
            "before_files": pm_before["files"],
        },
        "paper_guard": {"triggered": False, "checks": "start + per-day + pre-test"},
        "safety_counters": {"submit": 0, "cancel": 0, "live": 0},
        "not_executed": ["9-day X6 PnL replay", "candidate ranking/selection", "Shadow start"],
    }
    shas = atomic_publish(run_id, report)
    print("published:")
    for name, sha in shas.items():
        print(f"  {root / 'published' / name}  sha256={sha}")
    print(f"verdict: {verdict}")
    if blocked:
        print(f"blocked_reasons: {blocked}")


if __name__ == "__main__":
    main()
