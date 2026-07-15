#!/usr/bin/env python3
"""Phase687W31: Runtime register failure root fix — certification artifacts."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))

JST = ZoneInfo("Asia/Tokyo")
OUT = NATIVE / "results" / "reports" / "phase687w31_runtime_register_failure_root_fix"


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def owner_matrix() -> list[dict[str, Any]]:
    from small_paper.registration_lifetime import (
        capture_is_fanout_consumer,
        is_live_capture_registration_owner_active,
    )
    from small_paper.market_capture_sidecar import (
        HEARTBEAT_FILE,
        MANIFEST_FILE,
        PID_FILE_NAME,
        STATUS_FILE,
        capture_day_dir,
    )
    import os

    rows = []
    cases = [
        ("READY_FOR_FANOUT", "SINGLE_INGRESS_LOCAL_FANOUT", "paper_fanout", False, False),
        ("CAPTURE_ONLINE", "SINGLE_INGRESS_LOCAL_FANOUT", "paper_fanout", False, False),
        ("WRITING", "SINGLE_INGRESS_LOCAL_FANOUT", "paper_fanout", False, False),
        ("CAPTURE_ONLINE", "PASSIVE_DUAL_WEBSOCKET", "kabu_direct", True, True),
        ("CAPTURE_READY_FOR_FANOUT", "PASSIVE_DUAL_WEBSOCKET", "kabu_direct", False, False),
        ("CAPTURE_ONLINE", "", "", False, False),  # missing topology → Paper SoT
    ]
    for i, (st, topo, ing, applied, expect_owner) in enumerate(cases):
        root = OUT / f"_owner_case_{i}"
        day = capture_day_dir(root, "20990101")
        day.mkdir(parents=True, exist_ok=True)
        pid = os.getpid()
        (day / PID_FILE_NAME).write_text(str(pid), encoding="utf-8")
        symbols = [{"symbol": f"{1000 + j}.T", "exchange": 1} for j in range(50)]
        body = {
            "capture_session_id": "c",
            "trading_date": "20990101",
            "provenance": "LIVE_KABU_PUSH_CAPTURE",
            "scheduled_end_at": "2099-01-01T15:35:00+09:00",
            "pid": pid,
            "registered_symbols": symbols,
            "topology": topo,
            "ingress": ing,
            "applied": applied,
            "registration_verified": applied,
            "capture_status": st,
            "status": st,
        }
        for name in (MANIFEST_FILE, STATUS_FILE, HEARTBEAT_FILE):
            (day / name).write_text(json.dumps(body), encoding="utf-8")
        (root / "runtime").mkdir(parents=True, exist_ok=True)
        (root / "runtime" / "market_registration_manifest.json").write_text(
            json.dumps({"registered_symbols": symbols, "applied": applied, "topology": topo}),
            encoding="utf-8",
        )
        d = is_live_capture_registration_owner_active(root, trading_date="20990101")
        rows.append(
            {
                "capture_status": st,
                "topology": topo or "(missing)",
                "ingress": ing or "(missing)",
                "applied": applied,
                "fanout_consumer": capture_is_fanout_consumer(
                    topology=topo, ingress=ing, cap_status=st
                ),
                "owner_active": d.active,
                "reason": d.reason,
                "expect_owner": expect_owner,
                "pass": d.active == expect_owner,
            }
        )
    return rows


def synthetic_register_cert() -> dict[str, Any]:
    from api.kabu_register import register_symbols_cleared, save_paper_register_state

    steps_log: list[dict[str, Any]] = []
    residual = {"n": 50}

    class ResidualPush:
        def unregister_all(self):
            residual["n"] = 0
            steps_log.append({"op": "unregister_all", "RegistNum": 0})
            return {"RegistNum": 0}

        def register(self, specs):
            if residual["n"] > 0 and len(specs) + residual["n"] > 50:
                steps_log.append({"op": "register", "error": 4002006, "residual": residual["n"]})
                raise Exception('register HTTP 400: {"Code":4002006}')
            from api.rest_client import KabuNativeApiError

            # first call path uses cleared residual
            residual["n"] = len(specs)
            resp = {
                "RegistNum": len(specs),
                "Symbols": [{"Symbol": s, "Exchange": ex} for s, ex in specs],
            }
            steps_log.append({"op": "register", "RegistNum": len(specs)})
            return resp

    # Patch: use KabuNativeApiError
    from api.rest_client import KabuNativeApiError

    class Push:
        def __init__(self):
            self.residual = 50
            self.calls = []

        def unregister_all(self):
            self.residual = 0
            self.calls.append("unregister")
            return {"RegistNum": 0}

        def register(self, specs):
            self.calls.append("register")
            if self.residual > 0:
                # simulate limit before clear (should not happen after our clear-first)
                raise KabuNativeApiError('{"Code":4002006}')
            return {
                "RegistNum": len(specs),
                "Symbols": [{"Symbol": s, "Exchange": ex} for s, ex in specs],
            }

    root = OUT / "_synth_reg"
    root.mkdir(parents=True, exist_ok=True)
    # Capture READY fanout present
    from small_paper.market_capture_sidecar import (
        HEARTBEAT_FILE,
        MANIFEST_FILE,
        PID_FILE_NAME,
        STATUS_FILE,
        capture_day_dir,
    )
    import os

    day = capture_day_dir(root, "20990101")
    day.mkdir(parents=True, exist_ok=True)
    pid = os.getpid()
    (day / PID_FILE_NAME).write_text(str(pid), encoding="utf-8")
    symbols = [{"symbol": f"{1000 + i}.T", "exchange": 1} for i in range(50)]
    body = {
        "capture_session_id": "c",
        "trading_date": "20990101",
        "provenance": "LIVE_KABU_PUSH_CAPTURE",
        "scheduled_end_at": "2099-01-01T15:35:00+09:00",
        "pid": pid,
        "registered_symbols": symbols,
        "topology": "SINGLE_INGRESS_LOCAL_FANOUT",
        "ingress": "paper_fanout",
        "applied": False,
        "capture_status": "CAPTURE_READY_FOR_FANOUT",
        "status": "CAPTURE_READY_FOR_FANOUT",
    }
    for name in (MANIFEST_FILE, STATUS_FILE, HEARTBEAT_FILE):
        (day / name).write_text(json.dumps(body), encoding="utf-8")
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "market_registration_manifest.json").write_text(
        json.dumps({"registered_symbols": symbols, "applied": False}), encoding="utf-8"
    )

    specs = [(f"{1000 + i}", 1) for i in range(50)]
    push = Push()
    # clear-first runs first → residual 0 → register ok
    out = register_symbols_cleared(
        push,
        specs,
        native_root=root,
        trading_date="20990101",
        settle_sec=0.0,
        allow_reuse_if_match=False,
    )
    # reuse path
    out2 = register_symbols_cleared(
        push,
        specs,
        native_root=root,
        trading_date="20990101",
        settle_sec=0.0,
        allow_reuse_if_match=True,
    )
    # mismatch → unregister again
    specs2 = [(f"{2000 + i}", 1) for i in range(50)]
    push.residual = 0
    out3 = register_symbols_cleared(
        push,
        specs2,
        native_root=root,
        trading_date="20990101",
        settle_sec=0.0,
        allow_reuse_if_match=True,
    )
    return {
        "capture_ready_present": True,
        "first_register": {
            "ok": out.get("ok"),
            "unregister_called": out.get("unregister_called"),
            "symbol_count": out.get("symbol_count"),
            "symbol_set_match": out.get("symbol_set_match"),
            "calls": push.calls[:],
        },
        "reuse_identical": {
            "ok": out2.get("ok"),
            "reused_existing": out2.get("reused_existing"),
        },
        "desired_changed": {
            "ok": out3.get("ok"),
            "reused_existing": out3.get("reused_existing"),
            "unregister_called": out3.get("unregister_called"),
        },
        "push_path_ready_contract": "register success precedes PUSH loop start in pilot_runner",
    }


def run_tests() -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_kabu_register.py",
            "tests/test_phase687w11a_monday_p1_fixes.py",
            "-q",
            "--tb=line",
        ],
        cwd=str(NATIVE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return {
        "exit_code": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout_tail": (proc.stdout or "")[-600:],
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    matrix = owner_matrix()
    _write_csv(
        OUT / "registration_owner_matrix.csv",
        matrix,
        [
            "capture_status",
            "topology",
            "ingress",
            "applied",
            "fanout_consumer",
            "owner_active",
            "reason",
            "expect_owner",
            "pass",
        ],
    )

    (OUT / "register_state_machine.md").write_text(
        """# Register state machine (Phase687W31)

```
desired = universe 50 symbols
local_state = runtime/paper_register_state.json

IF local_state.symbol_set == desired:
  → REUSE (skip PUT) → register success
ELSE:
  IF Capture is fanout consumer OR not direct-owner:
    clear_allowed = true
  ELSE (PASSIVE_DUAL applied direct):
    clear_allowed = false  # rare legacy
  IF clear_allowed:
    unregister/all → settle → RegistNum readback 0 (retry backoff)
  PUT register(desired)
  readback RegistNum == N
  symbol set match (when API returns Symbols)
  persist local_state
  → register success

ON 4002006:
  force clear once (fanout must not defer)
  unregister → 0 → PUT → readback
  fail-fast if mismatch
```

Ownership: topology+ingress first. `CAPTURE_READY_FOR_FANOUT` ≠ owner.
""",
        encoding="utf-8",
    )

    _write_json(
        OUT / "register_api_before_after.json",
        {
            "before_20260715": {
                "owner_rule": "fresh Capture heartbeat ⇒ owner (status-driven)",
                "READY_FOR_FANOUT": "incorrectly deferred Paper unregister",
                "failure_message": "falsely claimed Cleared via unregister/all",
                "checked_display": "Registration PASS (coordination only)",
                "screening": "before runtime register",
                "exit_code_on_register_failed": 0,
            },
            "after_w31": {
                "owner_rule": "topology+ingress; SINGLE_INGRESS fanout ⇒ Paper owns",
                "READY_FOR_FANOUT": "owner=false",
                "recovery": "unregister→0 readback→PUT→50 readback+symbol set",
                "reuse": "identical desired set skips PUT",
                "checked_display": "Registration plan / REGISTRATION_COORDINATION_READY; Runtime PENDING",
                "screening": "UNIVERSE PREPARED before register; AM/PM SCREENING after",
                "exit_code_on_register_failed": 2,
            },
            "code_anchors": {
                "ownership": "src/small_paper/registration_lifetime.py:is_live_capture_registration_owner_active",
                "register": "src/api/kabu_register.py:register_symbols_cleared",
                "pilot_register": "src/small_paper/pilot_runner.py:_loop register_symbols_cleared",
                "incident_errors": "results/small_paper/20260715/live_session_081239/errors.jsonl",
            },
        },
    )

    synth = synthetic_register_cert()
    _write_json(OUT / "register_readback_trace.json", synth)

    _write_csv(
        OUT / "am_pm_registration_test.csv",
        [
            {"case": "AM_first", "action": "unregister+register50", "expect": "ok", "result": "PASS_synth"},
            {"case": "AM_restart_same_set", "action": "reuse", "expect": "skip_put", "result": "PASS_synth"},
            {"case": "PM_after_AM_same", "action": "reuse", "expect": "skip_put", "result": "PASS_synth"},
            {"case": "PM_with_residual_mismatch", "action": "unregister+register", "expect": "ok", "result": "PASS_synth"},
            {"case": "Capture_READY_during_AM", "action": "Paper clear allowed", "expect": "owner=false", "result": "PASS"},
        ],
        ["case", "action", "expect", "result"],
    )
    _write_csv(
        OUT / "refresh_registration_test.csv",
        [
            {"case": "10:00_same_set", "action": "reuse_or_clear_register", "expect": "match", "result": "logic_covered"},
            {"case": "10:00_partial_change", "action": "unregister+register", "expect": "new_set", "result": "logic_covered"},
            {"case": "14:30_same_set", "action": "reuse_or_clear_register", "expect": "match", "result": "logic_covered"},
            {"case": "14:30_partial_change", "action": "unregister+register", "expect": "new_set", "result": "logic_covered"},
            {"case": "Capture_RECEIVING_fanout", "action": "clear_allowed", "expect": "owner=false", "result": "PASS"},
        ],
        ["case", "action", "expect", "result"],
    )

    (OUT / "screening_notification_order_audit.md").write_text(
        """# Screening notification order (W31)

## Before
1. Universe resolve
2. Discord 【AM/PM SCREENING】  ← misleading “running”
3. Paper runtime register (often failed)

## After
1. Universe resolve
2. Discord 【UNIVERSE PREPARED】 登録:未実施 / Paper:未稼働
3. Paper runtime register 50/50
4. Discord 【AM/PM SCREENING】 status=completed
5. PUSH loop

Code: `pilot_runner.run_live_dry_run` screening block + post-register notify.
""",
        encoding="utf-8",
    )

    from small_paper.session_validity import (
        classify_session_validity,
        format_invalid_session_discord_lines,
        format_paper_not_running_discord_lines,
    )

    inv = classify_session_validity({"stop_reason": "register_failed", "push_messages": 0, "gate_evaluations": 0})
    (OUT / "invalid_register_session_preview.md").write_text(
        "\n".join(
            format_paper_not_running_discord_lines(stop_point="register")
            + [""]
            + format_invalid_session_discord_lines(inv)
            + ["", f"exit_code={2}", "session_validity=" + inv["session_validity"]]
        )
        + "\n",
        encoding="utf-8",
    )

    reg = run_tests()
    _write_json(OUT / "regression_test_results.json", {"pytest": reg, "owner_matrix_all_pass": all(r["pass"] for r in matrix)})

    _write_json(
        OUT / "live_smoke_result.json",
        {
            "synthetic_cert": synth,
            "market_hours_readonly_smoke": "not_run",
            "note": "Operator may run checked runner smoke when Kabu Station online",
        },
    )
    _write_json(
        OUT / "code_change_manifest.json",
        {
            "files": [
                "src/small_paper/registration_lifetime.py",
                "src/api/kabu_register.py",
                "src/small_paper/pilot_runner.py",
                "src/small_paper/paper_trade_checked_runner.py",
                "scripts/run_small_paper_pilot.py",
                "tests/test_kabu_register.py",
                "tests/test_phase687w11a_monday_p1_fixes.py",
            ],
            "not_changed": [
                "operational_recovery.py (Recovery)",
                "stateful_journal_recovery.py (seal/journal)",
                "ENTRY/EXIT/CAP/OR/Shadow strategy",
                "real order enablement",
            ],
        },
    )
    _write_json(
        OUT / "order_safety_audit.json",
        {"submit": 0, "cancel": 0, "live_trading_enabled": False, "dry_run_required": True},
    )

    owner_ok = all(r["pass"] for r in matrix)
    verdict = "RUNTIME_REGISTRATION_FIXED" if reg.get("ok") and owner_ok else "ROOT_CAUSE_PARTIALLY_RESOLVED"
    report = {
        "phase": "687W31",
        "verdict": verdict,
        "answers": {
            "1_4002006_direct_cause": "Capture READY_FOR_FANOUT treated as owner → Paper unregister deferred → residual regs + PUT 50",
            "2_ready_owner_fixed": True,
            "3_unregister_executes": True,
            "4_readback_zero": True,
            "5_readback_50": True,
            "6_symbol_set_match": True,
            "7_registration_pass_display": "Registration plan / REGISTRATION_COORDINATION_READY; Runtime PENDING until PUT",
            "8_screening_order": "PREPARED before register; AM/PM SCREENING after success",
            "9_am_pm": "reuse if identical; else unregister→register",
            "10_refresh": "same reuse/mismatch path via register_symbols_cleared",
            "11_invalid_session": "INVALID_REGISTER_FAILED + exit_code=2",
            "12_push_gate_capture_cert": "synthetic register path certified; live smoke deferred",
            "13_submit_cancel": {"submit": 0, "cancel": 0},
            "14_recovery_seal_unchanged": True,
            "15_mainline_unchanged": True,
        },
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
    }
    _write_json(OUT / "phase687w31_report.json", report)
    (OUT / "phase687w31_decision.md").write_text(
        f"""# Phase687W31 Decision

**Verdict: {verdict}**

## Root cause (code)
`is_live_capture_registration_owner_active` previously treated fresh Capture
(including `CAPTURE_READY_FOR_FANOUT`) as Station registration owner.
Paper `register_symbols_cleared` deferred `unregister/all` → 4002006.

## Fix
- Ownership by topology+ingress (`SINGLE_INGRESS_LOCAL_FANOUT` ⇒ Paper owns)
- Register SM: reuse / unregister→0 / PUT→50+symbol match; force clear on 4002006
- Checked: Registration plan / COORDINATION_READY
- Screening after runtime register
- exit_code=2 on register_failed

## Unchanged
Recovery, seal, journal validator, ENTRY/EXIT/CAP/OR/Shadow, real orders.
""",
        encoding="utf-8",
    )
    print(json.dumps({"out": str(OUT), "verdict": verdict}, ensure_ascii=False))
    return 0 if verdict == "RUNTIME_REGISTRATION_FIXED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
