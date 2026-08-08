"""Phase A orchestrator: inventory -> definitions -> tests -> P1 freeze -> publish.

Stops at Candidate Registry P1 freeze. NO 9-day PnL replay, NO candidate
selection, NO Shadow. Verdicts:
  E1_X6_RAW_REDESIGN_P1_READY / E1_X6_RAW_REDESIGN_P1_BLOCKED /
  E1_X6_RESEARCH_PAUSED_FOR_PAPER

Usage (from kabu_native/):
  python -m e1_x6_raw_redesign.run_phase_a            # new run
  python -m e1_x6_raw_redesign.run_phase_a --run-id X # resume (SHA-verified)
with PYTHONPATH=research;src
"""
from __future__ import annotations

import os

for _k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_k] = "1"  # must precede numpy import

import argparse
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import guard as guard_mod
from .p1 import build_p1_lock
from .protected_manifest import build_protected_manifest, manifests_equal
from .raw_inventory import inventory_canonical_day, inventory_raw_day, known_excluded_windows
from .registry import build_candidate_registry
from .report import atomic_publish
from .source_manifest import DAYS, build_source_manifest
from .store import load_checkpoint, run_root, save_checkpoint, sha256_obj, write_json

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
PLAN_DOC = NATIVE_ROOT / "kabu_native" / "docs" / "e1_x6_plan_v3_raw_feature_redesign.md"

FIELD_USABILITY_RULE = (
    "pre-registered: a field group is USABLE only if its session-hours as-of "
    "coverage >= 0.90 in EVERY included AM/PM session of all 9 days; groups "
    "below the bar are excluded from mandatory conditions (never pseudo-generated)"
)
COVERAGE_MIN = 0.90


def _pause(run_id: str, guard_res: dict[str, Any], done: dict[str, Any]) -> None:
    write_json(run_root(run_id) / "paused.json", {
        "verdict": "E1_X6_RESEARCH_PAUSED_FOR_PAPER",
        "guard": guard_res,
        "progress": done,
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
    """Run the mandatory contract tests as a child process (1 worker, -p no:cacheprovider)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{NATIVE_ROOT / 'research'};{NATIVE_ROOT / 'src'}"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "--tb=short",
         "-p", "no:cacheprovider",
         str(NATIVE_ROOT / "tests" / "research" / "e1_x6_raw_redesign")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(NATIVE_ROOT), env=env, timeout=1800,
    )
    rows: list[dict[str, str]] = []
    for line in (proc.stdout or "").splitlines():
        ls = line.strip()
        for status in ("PASSED", "FAILED", "ERROR"):
            if ls.startswith(status + " "):
                rows.append({"test": ls.split(" ", 1)[1].split(" - ")[0], "outcome": status})
    passed = sum(1 for r in rows if r["outcome"] == "PASSED")
    return {
        "exit_code": proc.returncode,
        "total": len(rows),
        "passed": passed,
        "failed": len(rows) - passed,
        "rows": rows,
        "tail": (proc.stdout or "")[-2000:],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    guard_mod.apply_thread_caps()
    prio_ok = guard_mod.set_below_normal_priority()

    run_id = args.run_id or (
        f"e1x6r3_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
    )
    root = run_root(run_id)
    print(f"run_id={run_id} store={root} below_normal={prio_ok}")

    _guard_or_pause(run_id, {"stage": "start"})

    # protected manifest BEFORE
    pm_before_fp = root / "paper_protected_manifest_before.json"
    if pm_before_fp.is_file():
        import json as _json

        pm_before = _json.loads(pm_before_fp.read_text(encoding="utf-8"))
    else:
        pm_before = build_protected_manifest(REPO_ROOT)
        write_json(pm_before_fp, pm_before)
    print(f"protected_manifest before: files={pm_before['files_n']} sha={pm_before['manifest_sha256'][:16]}")

    # P0 source manifest (raw SHAs are expensive; checkpointed)
    sm_fp = root / "source_manifest.json"
    if sm_fp.is_file():
        import json as _json

        sm = _json.loads(sm_fp.read_text(encoding="utf-8"))
    else:
        print("hashing raw + canonical inputs (read-only)...")
        sm = build_source_manifest(NATIVE_ROOT)
        write_json(sm_fp, sm)
    binding = {"source_manifest_sha256": sm["source_manifest_sha256"], "run_id": run_id}
    print(f"source_manifest sha={sm['source_manifest_sha256'][:16]} days={len(sm['days'])}")

    # ---- Phase A-1: per-day inventory (chunked, guard between days) ----
    inv_days: dict[str, Any] = {}
    for day in DAYS:
        ck = load_checkpoint(run_id, f"inventory_{day}", binding=binding)
        if ck is not None:
            inv_days[day] = ck
            print(f"[A-1] {day}: resumed from checkpoint")
            continue
        _guard_or_pause(run_id, {"stage": f"inventory_{day}", "done": sorted(inv_days)})
        t0 = datetime.now()
        raw = inventory_raw_day(NATIVE_ROOT, day)
        can = inventory_canonical_day(NATIVE_ROOT, day)
        row = {**raw, "canonical": can}
        save_checkpoint(run_id, f"inventory_{day}", row, binding=binding)
        inv_days[day] = row
        dt = (datetime.now() - t0).total_seconds()
        print(f"[A-1] {day}: raw={raw['raw_total_lines']:,} canonical={can['canonical_events']:,} ({dt:.0f}s)")

    excl = known_excluded_windows()

    # ---- field usability decision (pre-registered rule; no economics) ----
    def _min_cov(key: str) -> float:
        vals = []
        for day, d in inv_days.items():
            for sk in ("AM", "PM"):
                s = d["sessions"][sk]
                if s["raw_events"] == 0:
                    continue
                if key in ("quote_coverage", "board_top1_coverage", "board_full10_coverage"):
                    v = s[key]
                else:
                    mr = s["field_missing_rate"].get(key)
                    v = None if mr is None else 1.0 - mr
                if v is not None:
                    vals.append(v)
        return min(vals) if vals else 0.0

    cov = {
        "quote": _min_cov("quote_coverage"),
        "volume": _min_cov("TradingVolume"),
        "vwap": _min_cov("VWAP"),
        "board_full10": _min_cov("board_full10_coverage"),
    }
    usable = [k for k, v in cov.items() if v >= COVERAGE_MIN]
    unusable = [k for k, v in cov.items() if v < COVERAGE_MIN]
    field_usability = {
        "rule": FIELD_USABILITY_RULE,
        "coverage_min_required": COVERAGE_MIN,
        "min_session_coverage": {k: round(v, 6) for k, v in cov.items()},
        "usable": usable,
        "unusable": unusable,
        "note": "unusable groups are dropped from mandatory conditions; never pseudo-generated",
    }
    print(f"field usability: {field_usability['min_session_coverage']} usable={usable}")

    core_ok = cov["quote"] >= COVERAGE_MIN

    # ---- mandatory tests ----
    _guard_or_pause(run_id, {"stage": "tests"})
    tests = _run_tests()
    write_json(root / "tests_result.json", tests)
    print(f"tests: {tests['passed']}/{tests['total']} passed (exit={tests['exit_code']})")

    # ---- Candidate Registry + P1 freeze ----
    registry = build_candidate_registry(
        core_feature_coverage_ok=core_ok,
        market_feature_coverage_ok=core_ok,
        vwap_available="vwap" in usable,
        volume_available="volume" in usable,
        board_available="board_full10" in usable,
    )
    inventory_summary = {
        "days_n": len(inv_days),
        "raw_total_lines": int(sum(d["raw_total_lines"] for d in inv_days.values())),
        "canonical_total": int(sum(d["canonical"]["canonical_events"] for d in inv_days.values())),
        "known_excluded_windows": excl,
        "per_day_session_sha256": sha256_obj({d: inv_days[d]["sessions"] for d in inv_days}),
    }
    p1 = build_p1_lock(
        run_id=run_id,
        plan_doc_path=PLAN_DOC,
        source_manifest_sha256=sm["source_manifest_sha256"],
        protected_manifest_sha256=pm_before["manifest_sha256"],
        inventory_summary=inventory_summary,
        field_usability=field_usability,
        registry=registry,
    )
    write_json(root / "p1_lock.json", p1)
    print(f"P1 frozen: sha={p1['p1_sha256'][:16]} registry_n={p1['candidate_registry_n']}")

    # ---- protected manifest AFTER (mandatory gate) ----
    pm_after = build_protected_manifest(REPO_ROOT)
    write_json(root / "paper_protected_manifest_after.json", pm_after)
    pm_match, pm_diffs = manifests_equal(pm_before, pm_after)
    if not pm_match:
        print(f"FAIL: paper protected manifest changed: {pm_diffs}")
        sys.exit(2)

    tests_ok = tests["exit_code"] == 0 and tests["failed"] == 0 and tests["total"] > 0
    if core_ok and tests_ok:
        verdict = "E1_X6_RAW_REDESIGN_P1_READY"
    else:
        verdict = "E1_X6_RAW_REDESIGN_P1_BLOCKED"

    report = {
        "plan_id": "E1_X6_PLAN_V3_RAW_FEATURE_REDESIGN",
        "run_id": run_id,
        "verdict": verdict,
        "verdict_basis": {
            "core_quote_coverage_ok": core_ok,
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


if __name__ == "__main__":
    main()
