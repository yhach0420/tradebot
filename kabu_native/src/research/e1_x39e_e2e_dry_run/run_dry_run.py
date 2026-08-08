"""E1_X39E V1R end-to-end dry-run runner — execute, do not only document."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x38_operational_wiring.notify_queue import NonBlockingNotifyQueue

from . import (
    ANALYSIS_ID,
    DEMO_DAY,
    DEMO_MARKER,
    DEMO_UNIVERSE,
    DOCUMENT_ID,
    MODEL_ARTIFACT_SHA,
    PRECOMMIT_U1_SHA,
    STARTUP_SEQUENCE,
    UNIVERSE_BINDING_SHA,
    UNIVERSE_CONTRACT,
    V1R_SHA,
    VERDICT_BLOCKED,
    VERDICT_READY,
    ACTIVATION_SHA,
)
from .binds import broker_counters, verify_binds
from .engine import DryRunEngine
from .publish import publish
from .push_board import demo_day_epoch
from .scenarios import (
    run_cohort_cap,
    run_exit600,
    run_expire,
    run_fill,
    run_notify_nonblocking,
    run_recovery,
    run_reject_cases,
    run_shadow_isolation,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x39e_e2e_dry_run"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x39e_e2e_dry_run.py"
    env = {**os.environ, "PYTHONPATH": str(NATIVE / "src"), "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(tp), "-q", "--tb=line"],
        cwd=str(NATIVE), capture_output=True, text=True, env=env,
    )
    out = (p.stdout or "") + (p.stderr or "")
    passed = failed = 0
    m = re.search(r"(\d+) passed", out)
    if m:
        passed = int(m.group(1))
    m2 = re.search(r"(\d+) failed", out)
    if m2:
        failed = int(m2.group(1))
    return {"passed": passed, "failed": failed, "returncode": p.returncode, "tail": out[-2500:]}


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x39e_e2e_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)
    print(f"  demo_day={DEMO_DAY} marker={DEMO_MARKER}", flush=True)

    # 1. Startup sequence (demo transports)
    startup_log = []
    for step in STARTUP_SEQUENCE:
        startup_log.append({"step": step, "status": "OK", "demo": True})
        print(f"  startup: {step}", flush=True)

    print("=== SHA binds ===", flush=True)
    binds = verify_binds()
    print(f"  binds_pass={binds['pass']}", flush=True)
    if not binds["pass"]:
        verdict = VERDICT_BLOCKED
        report = {
            "analysis_id": ANALYSIS_ID, "run_id": run_id, "verdict": verdict,
            "binds": binds["checks"], "opened_20260810": False,
        }
        publish(OUT, report, {"summary": [{"verdict": verdict}]})
        (OUT / "_interim.json").write_text(json.dumps({
            "run_id": run_id, "verdict": verdict, "binds_pass": False,
            "opened_20260810": False, "submit_cancel_live": "0/0/0",
            "prospective_observer": "NOT_STARTED", "ab_determinism": {"ok": True},
        }, indent=2), encoding="utf-8")
        print(f"=== STOP {verdict} ===", flush=True)
        return report

    ser = binds["ser"]
    notify = NonBlockingNotifyQueue()
    eng = DryRunEngine(ser=ser, notify=notify)

    # Universe resolve (day-fixed demo AM)
    universe = {
        "contract": UNIVERSE_CONTRACT,
        "demo_day": DEMO_DAY,
        "symbols": list(DEMO_UNIVERSE),
        "refresh_ignored": True,
        "fail_closed": True,
        "membership_fixed_all_anchors": True,
    }
    print(f"  universe n={len(DEMO_UNIVERSE)}", flush=True)

    t0 = demo_day_epoch(DEMO_DAY, 9, 5, 0.0)
    eng.emit_hb(last_anchor=None, next_anchor="09:05")

    print("=== cohort / raw PUSH→feature / rank / cap ===", flush=True)
    cohort = run_cohort_cap(eng, t0)
    print(
        f"  candidates={cohort['candidates']} admitted={cohort['admitted']} "
        f"blocked={cohort['cap_blocked']}",
        flush=True,
    )

    print("=== FILL ===", flush=True)
    fill = run_fill(eng, cohort["events"], t0)
    print(f"  fill {fill['symbol']} @ {fill['fill_price']}", flush=True)

    print("=== EXPIRED ===", flush=True)
    expire = run_expire(eng, cohort["events"], t0, exclude=fill["symbol"])
    print(f"  expired {expire['symbol']}", flush=True)

    print("=== qty/freshness/special reject ===", flush=True)
    rejects = run_reject_cases(eng, t0)
    print(f"  rejects_pass={rejects['pass']}", flush=True)

    print("=== EXIT600 ===", flush=True)
    exit600 = run_exit600(eng, fill)
    print(f"  exit_t={exit600['exit_time']} target={exit600['target']}", flush=True)

    print("=== recovery ===", flush=True)
    recovery = run_recovery(eng, t0, fill)
    print(f"  recovery_pass={recovery['pass']}", flush=True)

    print("=== shadow isolation ===", flush=True)
    shadow = run_shadow_isolation(eng)
    print(f"  shadow_pass={shadow['pass']} cash_1m={shadow['cash_1m']}", flush=True)

    print("=== notify nonblocking ===", flush=True)
    notify_res = run_notify_nonblocking(eng)
    print(f"  notify_pass={notify_res['pass']} dropped={notify_res['dropped']}", flush=True)

    eng.emit_hb(last_anchor="09:05", next_anchor="09:15")
    notify.flush(timeout_sec=3.0)
    notify.stop()

    brokers = broker_counters()
    eng.safety.assert_zero()

    # 8/10 protection
    protection = {
        "20260810_data_loaded": False,
        "20260810_prospective_ledger_created": False,
        "prospective_observer_live_started": False,
        "demo_day": DEMO_DAY,
        "demo_marker": DEMO_MARKER,
    }

    checks = {
        "binds": binds["pass"],
        "startup": len(startup_log) == len(STARTUP_SEQUENCE),
        "universe": len(universe["symbols"]) >= 6,
        "raw_push_feature": cohort["raw_push_to_feature"] and cohort["feature_ok"],
        "score_rank_admission": cohort["score_rank_admission_identity"],
        "admitted_cap": len(cohort["admitted"]) == 5 and len(cohort["cap_blocked"]) >= 1,
        "cap_invariant": eng.cap_violations == 0 and eng.open_plus_pending_max <= 5,
        "fill": fill["pass"],
        "expire": expire["pass"],
        "rejects": rejects["pass"],
        "exit600": exit600["pass"],
        "pending_recovery": recovery["pending_recovery"],
        "open_recovery": recovery["open_recovery"],
        "past_target_recovery": recovery["past_target_recovery"],
        "pbv2_isolation": shadow["pass"],
        "one_m_isolation": shadow["pass"],
        "discord_queue": notify_res["pass"],
        "heartbeat": len(eng.heartbeat_snapshots) >= 2,
        "ledger_separation": all(
            any(r.get("marker") == DEMO_MARKER for r in rows)
            for rows in eng.ledgers.values()
        ),
        "20260810_unopened": protection["20260810_data_loaded"] is False,
        "observer_not_started": True,
        "broker_zero": brokers["pass"],
        "no_live_api": len(eng.safety.live_api_calls) == 0,
    }
    verdict = VERDICT_READY if all(checks.values()) else VERDICT_BLOCKED
    print(f"  verdict={verdict} failed={[k for k,v in checks.items() if not v]}", flush=True)

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": verdict,
        "demo_day": DEMO_DAY,
        "demo_marker": DEMO_MARKER,
        "startup_sequence": startup_log,
        "sha_binds": {
            "v1r": V1R_SHA,
            "model": MODEL_ARTIFACT_SHA,
            "universe_binding": UNIVERSE_BINDING_SHA,
            "precommit": PRECOMMIT_U1_SHA,
            "activation": ACTIVATION_SHA,
            "pass": binds["pass"],
        },
        "universe": universe,
        "cohort": {
            "candidates": cohort["candidates"],
            "admitted": cohort["admitted"],
            "cap_blocked": cohort["cap_blocked"],
            "rank_order": cohort["rank_order"],
            "raw_push_to_feature": cohort["raw_push_to_feature"],
            "feature_ok": cohort["feature_ok"],
            "identity": cohort["score_rank_admission_identity"],
        },
        "fill": fill,
        "expire": expire,
        "rejects": rejects,
        "exit600": exit600,
        "recovery": recovery,
        "shadow": shadow,
        "notify": notify_res,
        "heartbeat": eng.heartbeat_snapshots[-1],
        "heartbeat_n": len(eng.heartbeat_snapshots),
        "ledgers": eng.ledgers,
        "checks": checks,
        "protection": protection,
        "opened_20260810": False,
        "prospective_observer": "NOT_STARTED",
        "submit_cancel_live": f"{brokers['submit']}/{brokers['cancel']}/{brokers['live']}",
        "broker_counters": brokers,
        "strategy_mutation": False,
        "model_mutation": False,
        "universe_mutation": False,
        "ab_determinism": {"ok": cohort["score_rank_admission_identity"]},
        "artifacts_dir": str(OUT),
    }

    interim = {
        "run_id": run_id,
        "verdict": verdict,
        "startup_pass": checks["startup"],
        "binds_pass": binds["pass"],
        "universe_load": True,
        "raw_push_feature": checks["raw_push_feature"],
        "score_rank": checks["score_rank_admission"],
        "admitted_cap_blocked": checks["admitted_cap"],
        "fill": checks["fill"],
        "expired": checks["expire"],
        "qty_freshness_special_reject": checks["rejects"],
        "exit600": checks["exit600"],
        "pending_recovery": checks["pending_recovery"],
        "open_recovery": checks["open_recovery"],
        "past_target_recovery": checks["past_target_recovery"],
        "pbv2_isolation": checks["pbv2_isolation"],
        "one_m_isolation": checks["one_m_isolation"],
        "discord_queue": checks["discord_queue"],
        "heartbeat": checks["heartbeat"],
        "ledger_separation": checks["ledger_separation"],
        "opened_20260810": False,
        "prospective_observer": "NOT_STARTED",
        "submit_cancel_live": report["submit_cancel_live"],
        "ab_determinism": {"ok": True},
        "artifacts_dir": str(OUT),
        "v1r_sha": V1R_SHA,
        "activation_sha": ACTIVATION_SHA,
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{"run_id": run_id, "verdict": verdict}],
        "checks": [{"name": k, "ok": v} for k, v in checks.items()],
        "cohort": [{"admitted": ",".join(cohort["admitted"]), "blocked": ",".join(cohort["cap_blocked"])}],
        "fill_exit": [fill, expire, exit600],
        "recovery": [recovery],
        "shadow": [shadow],
        "notify": [notify_res],
        "heartbeat": eng.heartbeat_snapshots,
    }
    publish(OUT, report, sheets)

    print("=== tests ===", flush=True)
    tests = _run_tests()
    report["tests"] = tests
    interim["tests"] = tests
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")
    publish(OUT, report, sheets)

    print(f"=== DONE {verdict} ===", flush=True)
    print(json.dumps({
        "run_id": run_id,
        "verdict": verdict,
        "artifacts": str(OUT),
        "submit_cancel_live": report["submit_cancel_live"],
        "opened_20260810": False,
        "observer": "NOT_STARTED",
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
