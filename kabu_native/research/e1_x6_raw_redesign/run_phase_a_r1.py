"""Phase A-R1 orchestrator: P1 contract repair. NO economics of any kind.

Verdicts: E1_X6_RAW_REDESIGN_P1_R1_READY / E1_X6_RAW_REDESIGN_P1_R1_BLOCKED /
E1_X6_RESEARCH_PAUSED_FOR_PAPER. Stops after atomic publish.

Usage (from kabu_native/, PYTHONPATH=research;src):
  python -m e1_x6_raw_redesign.run_phase_a_r1 [--run-id X]
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
from .asof_coverage import canonical_day_bundle, scan_day
from .base_binding import build_base_binding
from .history import SUPERSEDED_RUNS
from .p1 import build_p1_lock
from .protected_manifest import build_protected_manifest, manifests_equal
from .raw_inventory import inventory_raw_day, known_excluded_windows
from .registry import build_candidate_registry
from .replay_order import REPLAY_ORDER_CONTRACT
from .report import atomic_publish
from .source_manifest import DAYS, build_source_manifest
from .store import load_checkpoint, run_root, save_checkpoint, sha256_obj, write_json
from .tick_resolver import classify_from_increments, runtime_resolver_sha256
from .windows import build_analysis_mask_r1

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
PLAN_DOC = NATIVE_ROOT / "kabu_native" / "docs" / "e1_x6_plan_v3_raw_feature_redesign.md"
OLD_RUN_ID = "e1x6r3_20260802_233645_144c3aab"
COVERAGE_MIN = 0.90

USABILITY_RULE_R1 = (
    "pre-registered: field group USABLE only if its AS-OF GRID coverage >= 0.90 "
    "in EVERY mask-included AM/PM session; never derived from event-row missing "
    "rates; unusable groups excluded from mandatory conditions (no pseudo-data). "
    "volume/board stay DIAGNOSTIC-ONLY for the current 24 candidates even if USABLE."
)


def _pause(run_id: str, guard_res: dict[str, Any], done: dict[str, Any]) -> None:
    write_json(run_root(run_id) / "paused.json", {
        "verdict": "E1_X6_RESEARCH_PAUSED_FOR_PAPER",
        "guard": guard_res, "progress": done,
        "paused_at": datetime.now().astimezone().isoformat(),
    })
    print(f"verdict: E1_X6_RESEARCH_PAUSED_FOR_PAPER run_id={run_id}")
    print(f"reasons: {guard_res['reasons']}")
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
    for k in sorted(nk - ok):
        diff["added"].append(k)
    for k in sorted(ok - nk):
        diff["removed"].append(k)
    for k in sorted(ok & nk):
        if sha256_obj(old_p1[k]) != sha256_obj(new_p1[k]):
            diff["changed"].append(k)
    return diff


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    guard_mod.apply_thread_caps()
    prio_ok = guard_mod.set_below_normal_priority()
    run_id = args.run_id or (
        f"e1x6r3r1_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
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

    sm_fp = root / "source_manifest.json"
    if sm_fp.is_file():
        sm = json.loads(sm_fp.read_text(encoding="utf-8"))
    else:
        print("hashing raw + canonical inputs (read-only)...")
        sm = build_source_manifest(NATIVE_ROOT)
        write_json(sm_fp, sm)
    binding = {"source_manifest_sha256": sm["source_manifest_sha256"], "run_id": run_id}
    print(f"source_manifest sha={sm['source_manifest_sha256'][:16]}")

    # ---- per-day chunks: canonical bundle + as-of coverage + raw inventory ----
    inv_days: dict[str, Any] = {}
    cov_days: dict[str, Any] = {}
    regr_days: dict[str, Any] = {}
    for day in DAYS:
        ck = load_checkpoint(run_id, f"r1day_{day}", binding=binding)
        if ck is not None:
            inv_days[day] = ck["inventory"]
            cov_days[day] = ck["coverage"]
            regr_days[day] = ck["regressions"]
            print(f"[R1] {day}: resumed")
            continue
        _guard_or_pause(run_id, {"stage": f"day_{day}", "done": sorted(cov_days)})
        t0 = datetime.now()
        cb = canonical_day_bundle(NATIVE_ROOT, day)
        cov = scan_day(NATIVE_ROOT, day, cb["universe"])
        raw = inventory_raw_day(NATIVE_ROOT, day)
        row = {
            "inventory": {**raw, "canonical": cb["canonical"]},
            "coverage": cov,
            "regressions": cb["regression_audit"],
        }
        save_checkpoint(run_id, f"r1day_{day}", row, binding=binding)
        inv_days[day] = row["inventory"]
        cov_days[day] = cov
        regr_days[day] = cb["regression_audit"]
        dt = (datetime.now() - t0).total_seconds()
        qc = {sk: cov["windows"][sk]["quality_class"] for sk in ("AM", "PM")}
        print(f"[R1] {day}: universe={cov['universe_n']} windows={qc} "
              f"regr={cb['regression_audit']['canonical_ts_regressions_stored_order']} ({dt:.0f}s)")

    excl = known_excluded_windows()
    mask = build_analysis_mask_r1(cov_days, excl)
    included = [wid for wid, row in mask["windows"].items() if row["included"]]
    print(f"analysis_mask_id={mask['analysis_mask_id']} included_windows={len(included)}")

    # ---- field usability (as-of grid method, included sessions only) ----
    def _min_asof(grp: str) -> float:
        vals = []
        for day, cov in cov_days.items():
            for sk in ("AM", "PM"):
                if not mask["windows"][f"{day}_{sk}"]["included"]:
                    continue
                c = cov["sessions"][sk][grp]["coverage"]
                if c is not None:
                    vals.append(c)
        return min(vals) if vals else 0.0

    def _min_eventrow(grp_key: str, is_rate: bool) -> float:
        vals = []
        for day, d in inv_days.items():
            for sk in ("AM", "PM"):
                if not mask["windows"][f"{day}_{sk}"]["included"]:
                    continue
                s = d["sessions"][sk]
                if s["raw_events"] == 0:
                    continue
                v = s[grp_key] if not is_rate else (
                    None if s["field_missing_rate"].get(grp_key) is None
                    else 1.0 - s["field_missing_rate"][grp_key]
                )
                if v is not None:
                    vals.append(v)
        return min(vals) if vals else 0.0

    new_cov = {g: _min_asof(g) for g in ("quote", "volume", "vwap", "board10")}
    old_cov = {
        "quote": _min_eventrow("quote_coverage", False),
        "volume": _min_eventrow("TradingVolume", True),
        "vwap": _min_eventrow("VWAP", True),
        "board10": _min_eventrow("board_full10_coverage", False),
    }
    usable = [g for g, v in new_cov.items() if v >= COVERAGE_MIN]
    unusable = [g for g, v in new_cov.items() if v < COVERAGE_MIN]
    coverage_diff = {
        g: {
            "old_min_coverage": round(old_cov[g], 6),
            "new_min_coverage": round(new_cov[g], 6),
            "usable_old": g in ("quote", "board10"),  # published A decision
            "usable_new": g in usable,
        } for g in new_cov
    }
    field_usability = {
        "rule": USABILITY_RULE_R1,
        "coverage_min_required": COVERAGE_MIN,
        "min_included_session_asof_coverage": {g: round(v, 6) for g, v in new_cov.items()},
        "usable": usable,
        "unusable": unusable,
        "diagnostic_only": [g for g in ("volume", "board10") if g in usable],
        "vwap_usage_if_usable": "PULL pre-registered support condition only (mid>=vwap_asof at SETUP and TRIGGER)",
    }
    print(f"as-of usability: {field_usability['min_included_session_asof_coverage']} usable={usable}")
    core_ok = new_cov["quote"] >= COVERAGE_MIN

    # ---- tick classification (all symbols in any day universe must resolve) ----
    tick_syms: dict[str, dict[str, Any]] = {}
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
    unresolved = []
    for sym in all_universe:
        bins = agg.get(sym, {})
        res = classify_from_increments(
            {k: v[0] for k, v in bins.items()},
            {k: v[1] for k, v in bins.items()},
        )
        tick_syms[sym] = {"class": res["class"], "reason": res["reason"],
                          "observations": int(sum(v[1] for v in bins.values()))}
        if res["class"] is None:
            unresolved.append(sym)
    tick_resolver_info = {
        "runtime_resolver_sha256": runtime_resolver_sha256(NATIVE_ROOT),
        "rule": (
            "same band tables as runtime jpx_tick_size_yen (read-only reference); "
            "class per symbol proven from 9-day observed price increments; "
            "no 0.1-yen fallback: unresolved class => P1_R1_BLOCKED"
        ),
        "symbol_classes": tick_syms,
        "unresolved": unresolved,
    }
    print(f"tick classes: {len(all_universe)} symbols, unresolved={len(unresolved)}")

    # ---- E1_X5 base binding ----
    base = build_base_binding(included)
    print(f"base comparable={base.get('comparable')} {base.get('reason','')[:120]}")

    # ---- tests ----
    _guard_or_pause(run_id, {"stage": "tests"})
    tests = _run_tests()
    write_json(root / "tests_result.json", tests)
    print(f"tests: {tests['passed']}/{tests['total']} passed (exit={tests['exit_code']})")

    # ---- registry + P1_R1 ----
    registry = build_candidate_registry(
        core_feature_coverage_ok=core_ok,
        market_feature_coverage_ok=core_ok,
        vwap_available="vwap" in usable,
        volume_available="volume" in usable,
        board_available="board10" in usable,
    )
    inventory_summary = {
        "days_n": len(inv_days),
        "raw_total_lines": int(sum(d["raw_total_lines"] for d in inv_days.values())),
        "canonical_total": int(sum(d["canonical"]["canonical_events"] for d in inv_days.values())),
        "known_excluded_windows": excl,
        "analysis_mask_id": mask["analysis_mask_id"],
        "per_day_session_sha256": sha256_obj({d: inv_days[d]["sessions"] for d in inv_days}),
    }
    r1_payload = {
        "superseded_previous": SUPERSEDED_RUNS[OLD_RUN_ID] | {"run_id": OLD_RUN_ID},
        "coverage_diff": coverage_diff,
        "field_usability_r1": field_usability,
        "canonical_regressions": regr_days,
        "analysis_mask": mask,
        "tick_resolver": tick_resolver_info,
        "base_binding": base,
    }
    p1 = build_p1_lock(
        run_id=run_id, plan_doc_path=PLAN_DOC,
        source_manifest_sha256=sm["source_manifest_sha256"],
        protected_manifest_sha256=pm_before["manifest_sha256"],
        inventory_summary=inventory_summary,
        field_usability=field_usability,
        registry=registry,
        r1=r1_payload,
    )
    write_json(root / "p1_lock.json", p1)
    print(f"P1_R1 frozen: sha={p1['p1_sha256'][:16]} registry_n={p1['candidate_registry_n']}")

    # ---- old vs new P1 diff (old run preserved untouched) ----
    old_p1_fp = (run_root(OLD_RUN_ID) / "p1_lock.json")
    p1_diff = {"added": ["(old p1 not found)"], "removed": [], "changed": []}
    if old_p1_fp.is_file():
        old_p1 = json.loads(old_p1_fp.read_text(encoding="utf-8"))
        p1_diff = _p1_diff(old_p1, p1)
    write_json(root / "p1_diff_vs_old.json", {
        "old_run_id": OLD_RUN_ID,
        "old_p1_sha256": SUPERSEDED_RUNS[OLD_RUN_ID]["p1_sha256"],
        "new_p1_sha256": p1["p1_sha256"],
        "diff": p1_diff,
    })

    pm_after = build_protected_manifest(REPO_ROOT)
    write_json(root / "paper_protected_manifest_after.json", pm_after)
    pm_match, pm_diffs = manifests_equal(pm_before, pm_after)
    if not pm_match:
        print(f"FAIL: paper protected manifest changed: {pm_diffs}")
        sys.exit(2)

    tests_ok = tests["exit_code"] == 0 and tests["failed"] == 0 and tests["total"] > 0
    blocked_reasons = []
    if not core_ok:
        blocked_reasons.append("CORE_QUOTE_ASOF_COVERAGE_BELOW_0.90")
    if unresolved:
        blocked_reasons.append(f"TICK_CLASS_UNRESOLVED:{unresolved}")
    if not base.get("comparable"):
        blocked_reasons.append(base.get("reason", "NOT_COMPARABLE_BASE"))
    if not tests_ok:
        blocked_reasons.append("TESTS_FAILED")
    verdict = ("E1_X6_RAW_REDESIGN_P1_R1_READY" if not blocked_reasons
               else "E1_X6_RAW_REDESIGN_P1_R1_BLOCKED")

    report = {
        "plan_id": "E1_X6_PLAN_V3_RAW_FEATURE_REDESIGN",
        "run_id": run_id,
        "phase": "PHASE_A_R1",
        "verdict": verdict,
        "blocked_reasons": blocked_reasons,
        "verdict_basis": {
            "core_quote_asof_coverage_ok": core_ok,
            "tick_all_resolved": not unresolved,
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
            "superseded_run_id": OLD_RUN_ID,
            "coverage_days": cov_days,
            "coverage_diff": coverage_diff,
            "replay_order_contract": REPLAY_ORDER_CONTRACT,
            "canonical_regressions": regr_days,
            "analysis_mask": mask,
            "tick_resolver": tick_resolver_info,
            "base_binding": base,
            "p1_diff": p1_diff,
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
        "not_executed": ["9-day PnL replay", "candidate selection", "Shadow start"],
    }
    shas = atomic_publish(run_id, report)
    print("published:")
    for name, sha in shas.items():
        print(f"  {root / 'published' / name}  sha256={sha}")
    print(f"verdict: {verdict}")
    if blocked_reasons:
        print(f"blocked_reasons: {blocked_reasons}")


if __name__ == "__main__":
    main()
