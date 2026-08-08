"""Phase A-R2 orchestrator: decision-coverage contract repair. NO X6 economics.

Verdicts: E1_X6_RAW_REDESIGN_P1_R2_READY / E1_X6_RAW_REDESIGN_P1_R2_BLOCKED /
E1_X6_RESEARCH_PAUSED_FOR_PAPER. Stops after atomic publish.
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
from .base_recut import recut_base
from .decision_coverage import scan_day_r2
from .history import SUPERSEDED_RUNS
from .p1 import build_p1_lock
from .protected_manifest import build_protected_manifest, manifests_equal
from .raw_inventory import inventory_raw_day, known_excluded_windows
from .registry import build_candidate_registry
from .replay_order import REPLAY_ORDER_CONTRACT
from .report import atomic_publish
from .source_manifest import DAYS, build_source_manifest
from .store import load_checkpoint, run_root, save_checkpoint, sha256_file, sha256_obj, write_json
from .tick_official import classify_universe_official
from .windows import build_analysis_mask_r1

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
PLAN_DOC = NATIVE_ROOT / "kabu_native" / "docs" / "e1_x6_plan_v3_raw_feature_redesign.md"
R1_RUN_ID = "e1x6r3r1_20260803_031244_a7d98591"
R1_REPORT = (Path.home() / "e1x6_research_store" / "raw_feature_redesign" / R1_RUN_ID
             / "published" / "report.json")
R1_REPORT_SHA = "99c8cb5a590d26d21c6f32ecfc38d9ff2f5c339c209c88800889d48ccb025f02"
GATE_MIN = 0.90


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
    diff = {"added": [], "removed": [], "changed": []}
    ok, nk = set(old_p1), set(new_p1)
    diff["added"] = sorted(nk - ok)
    diff["removed"] = sorted(ok - nk)
    diff["changed"] = [k for k in sorted(ok & nk)
                       if sha256_obj(old_p1[k]) != sha256_obj(new_p1[k])]
    return diff


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    guard_mod.apply_thread_caps()
    prio_ok = guard_mod.set_below_normal_priority()
    run_id = args.run_id or (
        f"e1x6r3r2_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
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
    print(f"protected_manifest before: files={pm_before['files_n']} sha={pm_before['manifest_sha256'][:16]}")

    # R1 published report is read-only evidence input
    if not R1_REPORT.is_file() or sha256_file(R1_REPORT) != R1_REPORT_SHA:
        print("FAIL: R1 block-evidence report missing or SHA mismatch")
        sys.exit(2)
    r1_report = json.loads(R1_REPORT.read_text(encoding="utf-8"))

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
        ck = load_checkpoint(run_id, f"r2day_{day}", binding=binding)
        if ck is not None:
            inv_days[day] = ck["inventory"]
            cov_days[day] = ck["coverage"]
            regr_days[day] = ck["regressions"]
            print(f"[R2] {day}: resumed")
            continue
        _guard_or_pause(run_id, {"stage": f"day_{day}", "done": sorted(cov_days)})
        t0 = datetime.now()
        cb = canonical_day_bundle(NATIVE_ROOT, day)
        cov = scan_day_r2(NATIVE_ROOT, day, cb["universe"])
        raw = inventory_raw_day(NATIVE_ROOT, day)
        row = {"inventory": {**raw, "canonical": cb["canonical"]},
               "coverage": cov, "regressions": cb["regression_audit"]}
        save_checkpoint(run_id, f"r2day_{day}", row, binding=binding)
        inv_days[day] = row["inventory"]
        cov_days[day] = cov
        regr_days[day] = cb["regression_audit"]
        dt = (datetime.now() - t0).total_seconds()
        line = []
        for sk in ("AM", "PM"):
            s = cov["sessions"][sk]
            if "due_symbol_grid_n" in s:
                line.append(f"{sk}: dq={s['decision_quote_coverage']} "
                            f"mc={s['market_context_coverage']} due={s['due_symbol_grid_n']}")
        print(f"[R2] {day}: {'; '.join(line)} ({dt:.0f}s)")

    excl = known_excluded_windows()
    mask = build_analysis_mask_r1(cov_days, excl)
    included = [wid for wid, row in mask["windows"].items() if row["included"]]
    print(f"analysis_mask_id={mask['analysis_mask_id']} included_windows={len(included)}")

    # cross-check scan-local horizon vs mask
    for wid in included:
        day, sk = wid.split("_")
        s = cov_days[day]["sessions"][sk]
        mrow = mask["windows"][wid]
        if abs((s["entry_evaluable_until_epoch"] or 0)
               - (mrow["entry_evaluable_until_epoch"] or 0)) > 1e-6:
            print(f"FAIL: horizon mismatch {wid}")
            sys.exit(2)

    # ---- decision gates (B and C) over included windows ----
    per_window: dict[str, Any] = {}
    for wid in included:
        day, sk = wid.split("_")
        s = cov_days[day]["sessions"][sk]
        dq = s["decision_quote_coverage"]
        mc = s["market_context_coverage"]
        per_window[wid] = {
            "decision_quote_coverage": dq,
            "gate_b": dq is not None and dq >= GATE_MIN,
            "market_context_coverage": mc,
            "gate_c": mc is not None and mc >= GATE_MIN,
        }
    gate_b_fail = [w for w, r in per_window.items() if not r["gate_b"]]
    gate_c_fail = [w for w, r in per_window.items() if not r["gate_c"]]
    all_pass = not gate_b_fail and not gate_c_fail
    min_dq = min((r["decision_quote_coverage"] for r in per_window.values()
                  if r["decision_quote_coverage"] is not None), default=None)
    min_mc = min((r["market_context_coverage"] for r in per_window.values()
                  if r["market_context_coverage"] is not None), default=None)
    gates = {"per_window": per_window, "all_pass": all_pass,
             "gate_b_fail_windows": gate_b_fail, "gate_c_fail_windows": gate_c_fail,
             "min_decision_quote_coverage": min_dq,
             "min_market_context_coverage": min_mc,
             "threshold": GATE_MIN}
    print(f"gates: all_pass={all_pass} min_dq={min_dq} min_mc={min_mc} "
          f"fail_b={gate_b_fail} fail_c={gate_c_fail}")

    # ---- official tick classification (empirical = cross-check only) ----
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
    tick_official = classify_universe_official(REPO_ROOT, all_universe, agg)
    print(f"tick official: {len(all_universe)} symbols, "
          f"unresolved={len(tick_official['unresolved'])} {tick_official['unresolved'][:8]}")

    # ---- E1_X5 base recut onto the X6 mask ----
    base = recut_base(mask)
    if base.get("comparable"):
        rm = base["artifact"]["recut_metrics"]
        write_json(root / "e1x5_base_recut.json", base["artifact"])
        print(f"base recut: kept={rm['completed_trades']}/1058 pnl={rm['pnl']} "
              f"dd={rm['max_dd']} stop={rm['stop_loss_total']}")
    else:
        print(f"base recut NOT comparable: {base.get('reason')}")

    _guard_or_pause(run_id, {"stage": "tests"})
    tests = _run_tests()
    write_json(root / "tests_result.json", tests)
    print(f"tests: {tests['passed']}/{tests['total']} passed (exit={tests['exit_code']})")

    registry = build_candidate_registry(
        core_feature_coverage_ok=all_pass,
        market_feature_coverage_ok=all_pass,
        vwap_available=False, volume_available=False, board_available=False,
    )
    if not all_pass:
        for r in registry:
            r["disable_reason"] = "DECISION_COVERAGE_GATE_FAIL"

    inventory_summary = {
        "days_n": len(inv_days),
        "raw_total_lines": int(sum(d["raw_total_lines"] for d in inv_days.values())),
        "canonical_total": int(sum(d["canonical"]["canonical_events"] for d in inv_days.values())),
        "known_excluded_windows": excl,
        "analysis_mask_id": mask["analysis_mask_id"],
        "per_day_session_sha256": sha256_obj({d: inv_days[d]["sessions"] for d in inv_days}),
    }
    r1r = r1_report["r1"]
    field_usability = {
        "rule": ("R2: core gating is decision-based (gates B and C); full-grid "
                 "as-of coverage is diagnostic only; volume/VWAP/board10 remain "
                 "out of candidate axes (unchanged from R1)"),
        "usable": [], "unusable": [],
        "r1_full_grid_min_asof": r1r["coverage_diff"],
        "r1_quote_min_preserved": 0.752485,
        "decision_gates": {"min_decision_quote_coverage": min_dq,
                           "min_market_context_coverage": min_mc,
                           "all_pass": all_pass},
    }
    sem_agg = {day: cov_days[day]["source_semantics"] for day in cov_days}
    r1_payload = {
        "superseded_previous": SUPERSEDED_RUNS["e1x6r3_20260802_233645_144c3aab"]
        | {"run_id": "e1x6r3_20260802_233645_144c3aab"},
        "coverage_diff": r1r["coverage_diff"],
        "field_usability_r1": r1_report["field_usability"],
        "canonical_regressions": regr_days,
        "analysis_mask": mask,
        "tick_resolver": {"superseded_by": "r2.tick_official",
                          "r1_empirical_note": "empirical-only classification retired per R2 §8"},
        "base_binding": {"superseded_by": "r2.base_binding_r2",
                         "r1_binding_sha": r1r["base_binding"].get("artifact_sha256")},
    }
    r2_payload = {
        "r1_block_evidence": SUPERSEDED_RUNS[R1_RUN_ID] | {"run_id": R1_RUN_ID},
        "timestamp_policy": {
            "availability_ts": "ingress only (causal order + availability)",
            "snapshot_age_sec": "grid t - ingress of last symbol snapshot",
            "field_source_age_sec": "grid t - field-specific source time (diagnostic)",
            "source_to_ingress_delta_sec": "saved per day",
            "max_merge_abolished": True,
        },
        "source_semantics": {
            day: {k: v.get("semantics") for k, v in sem.items()
                  if isinstance(v, dict) and "semantics" in v}
            for day, sem in sem_agg.items()
        },
        "decision_gates": gates,
        "tick_official": {k: tick_official[k] for k in
                          ("master_path", "master_sha256", "rule", "unresolved")},
        "base_binding_r2": (
            {"comparable": True,
             "artifact_path": str(root / "e1x5_base_recut.json"),
             "artifact_sha256": base["artifact"]["artifact_sha256"],
             "analysis_mask_id": mask["analysis_mask_id"],
             "recut_metrics": base["artifact"]["recut_metrics"],
             "original_base_n": 1058}
            if base.get("comparable") else
            {"comparable": False, "reason": base.get("reason")}
        ),
    }
    p1 = build_p1_lock(
        run_id=run_id, plan_doc_path=PLAN_DOC,
        source_manifest_sha256=sm["source_manifest_sha256"],
        protected_manifest_sha256=pm_before["manifest_sha256"],
        inventory_summary=inventory_summary,
        field_usability=field_usability,
        registry=registry,
        r1=r1_payload,
        r2=r2_payload,
    )
    write_json(root / "p1_lock.json", p1)
    print(f"P1_R2 frozen: sha={p1['p1_sha256'][:16]} registry_n={p1['candidate_registry_n']}")

    old_p1_fp = run_root(R1_RUN_ID) / "p1_lock.json"
    p1_diff = {"added": ["(R1 p1 not found)"], "removed": [], "changed": []}
    if old_p1_fp.is_file():
        p1_diff = _p1_diff(json.loads(old_p1_fp.read_text(encoding="utf-8")), p1)
    write_json(root / "p1_diff_r1_r2.json", {
        "r1_run_id": R1_RUN_ID,
        "r1_p1_sha256": SUPERSEDED_RUNS[R1_RUN_ID]["p1_sha256"],
        "r2_p1_sha256": p1["p1_sha256"],
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
    if gate_b_fail:
        blocked.append(f"DECISION_QUOTE_COVERAGE_GATE_FAIL:{gate_b_fail}")
    if gate_c_fail:
        blocked.append(f"MARKET_CONTEXT_COVERAGE_GATE_FAIL:{gate_c_fail}")
    if tick_official["unresolved"]:
        blocked.append(f"TICK_OFFICIAL_CLASS_UNRESOLVED:{tick_official['unresolved']}")
    if not base.get("comparable"):
        blocked.append(base.get("reason", "NOT_COMPARABLE_BASE"))
    if not tests_ok:
        blocked.append("TESTS_FAILED")
    verdict = ("E1_X6_RAW_REDESIGN_P1_R2_READY" if not blocked
               else "E1_X6_RAW_REDESIGN_P1_R2_BLOCKED")

    tick_counts: dict[str, int] = {}
    for row in tick_official["symbol_classes"].values():
        tick_counts[str(row["class"])] = tick_counts.get(str(row["class"]), 0) + 1

    report = {
        "plan_id": "E1_X6_PLAN_V3_RAW_FEATURE_REDESIGN",
        "run_id": run_id,
        "phase": "PHASE_A_R2",
        "verdict": verdict,
        "blocked_reasons": blocked,
        "verdict_basis": {
            "decision_gates_all_pass": all_pass,
            "tick_all_resolved": not tick_official["unresolved"],
            "base_comparable": bool(base.get("comparable")),
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
            "superseded_run_id": R1_RUN_ID,
            # preserve R1 full-grid asof sessions verbatim (read-only evidence)
            "coverage_days": r1_report.get("r1", {}).get("coverage_days") or {},
            "coverage_diff": r1r["coverage_diff"],
            "replay_order_contract": REPLAY_ORDER_CONTRACT,
            "canonical_regressions": regr_days,
            "analysis_mask": mask,
            "tick_resolver": r1_report.get("r1", {}).get("tick_resolver") or {
                "runtime_resolver_sha256": None,
                "rule": "superseded by r2 tick_official",
                "symbol_classes": {},
            },
            "base_binding": r1_report.get("r1", {}).get("base_binding")
            or r1_payload["base_binding"],
            "p1_diff": p1_diff,
        },
        "r2": {
            "coverage_days": cov_days,
            "gates": gates,
            "tick_official": tick_official,
            "tick_official_summary": tick_counts,
            "base_binding_r2": r2_payload["base_binding_r2"],
            "p1_diff_r1_r2": p1_diff,
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
