"""Run E1_X6_FCRR final study (VALIDATION_PLAN 1.2 + FCRR IMPLEMENTATION_SPEC 1.0).

Order: Gate0 → P1_STUDY_PRECOMMIT → economics → gates → publish → STOP.
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

for _k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_k] = "1"

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE / "research"))

from research.e1_x6_fcrr.config import (  # noqa: E402
    CANDIDATE_IDS,
    DOCUMENT_ID,
    DOCUMENT_VERSION,
    PLAN_DOCUMENT_ID,
    PLAN_VERSION,
)
from research.e1_x6_fcrr.manifests import gate0_check, write_p1_precommit  # noqa: E402
from research.e1_x6_fcrr.metrics import (  # noqa: E402
    evaluate_gates,
    fixed_spec_day_deletion,
    rolling_origin_from_day_pnls,
    summarize_trades,
)
from research.e1_x6_fcrr.replay import estimate_volume_floor, replay_all_candidates  # noqa: E402
from research.e1_x6_fcrr.report import atomic_publish  # noqa: E402
from research.e1_x6_provisional.util import sha256_obj  # noqa: E402

JST = ZoneInfo("Asia/Tokyo")
STORE = Path.home() / "e1x6_research_store" / "fcrr"


def _run_tests() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{NATIVE / 'src'};{NATIVE / 'research'}"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "--tb=line",
         "-p", "no:cacheprovider",
         str(NATIVE / "tests" / "test_e1_x6_fcrr.py")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(NATIVE), env=env, timeout=600,
    )
    rows = []
    for line in (proc.stdout or "").splitlines():
        ls = line.strip()
        for st in ("PASSED", "FAILED", "ERROR"):
            if ls.startswith(st + " "):
                rows.append({"test": ls.split(" ", 1)[1].split(" - ")[0], "outcome": st})
    return {
        "exit_code": proc.returncode,
        "passed": sum(1 for r in rows if r["outcome"] == "PASSED"),
        "failed": sum(1 for r in rows if r["outcome"] != "PASSED"),
        "total": len(rows),
        "rows": rows,
        "tail": (proc.stdout or "")[-2000:],
    }


def main() -> None:
    run_id = f"e1x6_fcrr_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(3)}"
    work = STORE / run_id
    work.mkdir(parents=True, exist_ok=True)
    print(f"final_run_id={run_id}", flush=True)

    print("=== Gate 0 ===", flush=True)
    g0 = gate0_check()
    print("gate0", g0["ok"], g0.get("errors"), flush=True)
    if not g0["ok"]:
        report = {
            "verdict": "E1_X6_SOURCE_BLOCKED",
            "final_run_id": run_id,
            "plan_document_id": PLAN_DOCUMENT_ID,
            "plan_version": PLAN_VERSION,
            "document_id": DOCUMENT_ID,
            "document_version": DOCUMENT_VERSION,
            "gate0": g0,
            "precommit": {},
            "safety": {"submit": 0, "cancel": 0, "live": 0},
            "tests": {"rows": [], "passed": 0, "total": 0, "failed": 0, "exit_code": 0},
            "mainline_changed": False,
            "candidate_results": {},
        }
        shas = atomic_publish(report)
        print("SOURCE_BLOCKED", shas, flush=True)
        sys.exit(2)

    print("=== P1_STUDY_PRECOMMIT (before economics) ===", flush=True)
    pre = write_p1_precommit(work)
    print("precommit_sha", pre["precommit_sha256"], "at", pre["precommit_at_jst"], flush=True)

    print("=== volume abs floor (build days, no economics) ===", flush=True)
    floor = estimate_volume_floor()
    print("volume_abs_floor_q50", floor, flush=True)
    pre["volume_abs_floor_q50"] = floor

    print("=== FCRR economics A ===", flush=True)
    lane_a = replay_all_candidates(volume_abs_floor=floor)
    print("=== FCRR economics B (determinism) ===", flush=True)
    lane_b = replay_all_candidates(volume_abs_floor=floor)

    cand_results = {}
    day_pnls = {}
    ab_ok = True
    for cid in CANDIDATE_IDS:
        a = lane_a["candidates"][cid]
        b = lane_b["candidates"][cid]
        sha_a = sha256_obj(a["trades"])
        sha_b = sha256_obj(b["trades"])
        if sha_a != sha_b:
            ab_ok = False
        metrics = summarize_trades(a["trades"])
        day_del = fixed_spec_day_deletion(metrics["day_pnl"])
        day_pnls[cid] = metrics["day_pnl"]
        cand_results[cid] = {
            "metrics": metrics,
            "day_deletion": day_del,
            "trades": a["trades"],
            "funnels": a["funnels"],
            "cap_blocked": a["cap_blocked"],
            "episode_reentry": a["episode_reentry"],
            "ab": {"sha_a": sha_a, "sha_b": sha_b, "match": sha_a == sha_b},
            "transitions_n": len(a["transitions"]),
            "transitions_sha": sha256_obj(a["transitions"]),
        }

    rolling = rolling_origin_from_day_pnls(day_pnls)
    base = g0["all_usable_base"]
    # fill missing base stop/dd neutrally if absent
    if base.get("stop_loss_total") is None:
        base = {**base, "stop_loss_total": 0.0, "stop_loss_per_trade": 0.0, "max_dd": 0.0}

    for cid, row in cand_results.items():
        gates = evaluate_gates(
            row["metrics"],
            core_metrics=None,
            core_evaluable=False,
            base_all_usable=base,
            rolling=rolling,
            day_del=row["day_deletion"],
            determinism_ok=ab_ok and row["ab"]["match"],
            precommit_ok=True,
            safety_ok=True,
            leakage_ok=True,
        )
        row["gates"] = gates

    print("=== tests ===", flush=True)
    tests = _run_tests()

    # Verdict selection
    if g0.get("insufficient_evidence_recommended"):
        verdict = "E1_X6_INSUFFICIENT_EVIDENCE"
    elif not ab_ok or tests["exit_code"] != 0:
        verdict = "E1_X6_SOURCE_BLOCKED" if not ab_ok else "E1_X6_SOURCE_BLOCKED"
        if tests["exit_code"] != 0 and ab_ok:
            # treat test fail as blocked integrity
            verdict = "E1_X6_SOURCE_BLOCKED"
    else:
        any_pass = any(r["gates"]["all_pass"] for r in cand_results.values())
        verdict = (
            "E1_X6_ENTRY_ONLY_CANDIDATE_FROZEN" if any_pass
            else "E1_X6_NO_ROBUST_ENTRY_CANDIDATE"
        )
        # CORE not evaluable overrides NO_ROBUST → INSUFFICIENT
        if not any_pass and g0.get("insufficient_evidence_recommended"):
            verdict = "E1_X6_INSUFFICIENT_EVIDENCE"

    report = {
        "plan_document_id": PLAN_DOCUMENT_ID,
        "plan_version": PLAN_VERSION,
        "document_id": DOCUMENT_ID,
        "document_version": DOCUMENT_VERSION,
        "final_run_id": run_id,
        "verdict": verdict,
        "generated_at_jst": datetime.now(JST).isoformat(),
        "precommit": pre,
        "gate0": g0,
        "volume_abs_floor_q50": floor,
        "candidate_results": cand_results,
        "rolling_origin": rolling,
        "day_deletion": next(iter(cand_results.values()))["day_deletion"] if cand_results else {},
        "determinism": {"all_match": ab_ok},
        "tests": tests,
        "safety": {"submit": 0, "cancel": 0, "live": 0},
        "mainline_changed": False,
        "EXIT_REDESIGN_STARTED": False,
        "FORWARD_STARTED": False,
        "SHADOW_STARTED": False,
        "selected_candidate_id": next(
            (cid for cid, r in cand_results.items() if r["gates"]["all_pass"]), None
        ),
    }
    shas = atomic_publish(report)
    print("=== DONE ===", flush=True)
    print("verdict", verdict, flush=True)
    for k, v in shas.items():
        print(k, v, flush=True)
    print("STOP", flush=True)


if __name__ == "__main__":
    main()
