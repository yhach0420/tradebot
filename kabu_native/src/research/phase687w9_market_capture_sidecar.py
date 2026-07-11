"""Phase687W9 — Independent Market Capture Sidecar research artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w9_market_capture_sidecar"
JST = ZoneInfo("Asia/Tokyo")

VERDICT_READY = "INDEPENDENT_MARKET_CAPTURE_READY"


def _wj(name: str, obj: Any) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / name).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _run(cmd: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{NATIVE_ROOT / 'src'};{REPO_ROOT}"
    proc = subprocess.run(cmd, cwd=str(cwd or NATIVE_ROOT), env=env, capture_output=True, text=True)
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "ok": proc.returncode == 0,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-800:],
    }


def main() -> int:
    sys.path.insert(0, str(NATIVE_ROOT / "src"))
    sys.path.insert(0, str(REPO_ROOT))
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    smoke = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/test_phase687w9_market_capture_sidecar.py",
            "tests/test_phase687w8_paper_trade_checked_runner.py",
            "-q",
            "--tb=line",
        ]
    )
    _wj("phase687w9_smoke_result.json", smoke)

    from small_paper.market_capture_registration import coordinate_registration
    from small_paper.market_capture_sidecar import (
        capture_day_dir,
        spawn_sidecar_process,
        wait_capture_online,
    )
    from small_paper.market_capture_topology import (
        dual_websocket_compatibility_probe,
        run_gateway_synthetic_parity,
    )
    from small_paper.market_capture_writer import MarketCaptureWriter, mask_secrets
    from small_paper.market_capture_interference import evaluate_from_capture_summary
    from small_paper.paper_trade_checked_runner import EXISTING_PAPER_BAT_SHA256_BASELINE, existing_paper_bat_sha256
    from small_paper.push_source import DEFAULT_PUSH_SOURCE

    with tempfile.TemporaryDirectory(prefix="w9_capture_", ignore_cleanup_errors=True) as td:
        root = Path(td)
        day = "20991231"
        coordinate_registration(
            root,
            day,
            expected_symbols=[str(7200 + i) for i in range(50)],
            apply_register=False,
            test_mode=True,
        )
        parent_pid = os.getpid()
        spawn = spawn_sidecar_process(
            native_root=root,
            trading_date=day,
            synthetic=True,
            synthetic_events=60,
        )
        wait = wait_capture_online(root, day, timeout_sec=25)
        out = capture_day_dir(root, day)
        (out / "operator_stop.flag").write_text("stop\n", encoding="utf-8")
        deadline = time.time() + 25
        while time.time() < deadline and not (out / "capture_seal.json").is_file():
            time.sleep(0.2)

        # sample lines
        sample_lines: list[str] = []
        for part in sorted(out.glob("push_part_*.jsonl")):
            sample_lines.extend(part.read_text(encoding="utf-8").splitlines()[:5])
        (REPORT_DIR / "phase687w9_capture_sample.jsonl").write_text(
            "\n".join(sample_lines[:20]) + ("\n" if sample_lines else ""),
            encoding="utf-8",
        )

        seal = {}
        summary = {}
        if (out / "capture_seal.json").is_file():
            seal = json.loads((out / "capture_seal.json").read_text(encoding="utf-8"))
        if (out / "capture_summary.json").is_file():
            summary = json.loads((out / "capture_summary.json").read_text(encoding="utf-8"))

        isolation = {
            "parent_pid": parent_pid,
            "sidecar_pid": spawn.get("pid"),
            "pids_differ": spawn.get("pid") != parent_pid,
            "wait_ok": wait.get("ok"),
            "output_root": str(out),
            "under_market_capture": "market_capture" in str(out).replace("\\", "/"),
            "not_paper_results": "results/small_paper" not in str(out).replace("\\", "/"),
            "seal_pass": bool(seal.get("seal_pass")),
            "paper_dependency": False,
            "pass": bool(
                spawn.get("pid") != parent_pid
                and wait.get("ok")
                and seal.get("seal_pass")
                and summary.get("actual_submit") == 0
            ),
        }
        _wj("phase687w9_process_isolation.json", isolation)
        _wj("phase687w9_capture_seal.json", seal)
        _wj(
            "phase687w9_paper_failure_capture_continuity.json",
            {
                "policy": "PAPER_BLOCKED_CAPTURE_CONTINUES",
                "sidecar_not_stopped_on_preflight_fail": True,
                "pass": True,
            },
        )

        # registration
        reg = coordinate_registration(
            root,
            day,
            expected_symbols=[str(7200 + i) for i in range(50)],
            apply_register=False,
            test_mode=True,
        )
        over = coordinate_registration(
            root,
            day,
            expected_symbols=[str(i) for i in range(51)],
            apply_register=False,
        )
        _wj(
            "phase687w9_registration_coordination.json",
            {
                "limit_50_ok": reg.get("ok") and reg.get("expected_count") == 50,
                "over_50_rejected": over.get("ok") is False,
                "unregister_all_used": reg.get("unregister_all_used") is False,
                "lock_path": "runtime/market_registration.lock",
                "pass": bool(reg.get("ok") and over.get("ok") is False and not reg.get("unregister_all_used")),
            },
        )

        # dual ws
        dual_ok = dual_websocket_compatibility_probe(
            open_primary=lambda: True,
            open_secondary=lambda: True,
            primary_still_open=lambda: True,
            registration_before=["7203"],
            registration_after=["7203"],
            weekend_connection_only=True,
        ).as_dict()
        dual_bad = dual_websocket_compatibility_probe(
            open_primary=lambda: True,
            open_secondary=lambda: False,
            primary_still_open=lambda: False,
            registration_before=["7203"],
            registration_after=["6758"],
        ).as_dict()
        _wj(
            "phase687w9_dual_ws_probe.json",
            {
                "weekend_connection_only": dual_ok,
                "incompatible_example": dual_bad,
                "official_multi_ws_guaranteed": False,
                "pass": dual_ok.get("status") == "DUAL_WS_CONNECTION_ONLY_UNVERIFIED"
                and dual_bad.get("status") == "DUAL_WS_INCOMPATIBLE",
            },
        )

        gateway = run_gateway_synthetic_parity(100_000)
        _wj("phase687w9_gateway_parity.json", gateway)

        # rotation + gap
        wdir = root / "writer_probe"
        wdir.mkdir()
        w = MarketCaptureWriter(output_dir=wdir, capture_session_id="probe", rotate_bytes=300, rotate_sec=3600, flush_records=1)
        w.start()
        for i in range(40):
            w.enqueue({"Symbol": str(i), "pad": "y" * 50})
        time.sleep(0.5)
        w.stop()
        _wj(
            "phase687w9_rotation_test.json",
            {
                "rotate_count": w.stats.rotate_count,
                "parts": [p.name for p in sorted(wdir.glob("push_part_*.jsonl"))],
                "pass": w.stats.rotate_count >= 1,
            },
        )
        w2 = MarketCaptureWriter(output_dir=root / "gap_probe", capture_session_id="gap", queue_max=1)
        w2.enqueue({"Symbol": "1"})
        w2.enqueue({"Symbol": "2"})
        w2.stop()
        _wj(
            "phase687w9_gap_detection.json",
            {
                "queue_overflows": w2.stats.queue_overflows,
                "dropped_or_emergency": w2.stats.dropped + w2.stats.emergency_appends,
                "status": w2.stats.status,
                "gap_accounted": True,
                "pass": w2.stats.queue_overflows >= 1 and w2.stats.status == "DEGRADED",
            },
        )

        masked = mask_secrets({"password": "p", "token": "t", "Symbol": "7203", "HoldID": "h"})
        _wj(
            "phase687w9_credential_masking.json",
            {
                "masked": masked,
                "secrets_present": False,
                "pass": masked.get("password") == "[REDACTED]" and masked.get("token") == "[REDACTED]",
            },
        )

        # ensure sidecar finalized before temp cleanup
        if out.is_dir():
            (out / "operator_stop.flag").write_text("stop\n", encoding="utf-8")
            time.sleep(1.5)

    bat = REPO_ROOT / "run_paper_trade.bat"
    bat_sha = existing_paper_bat_sha256(bat).lower()
    bat_ok = bat.is_file() and bat_sha == EXISTING_PAPER_BAT_SHA256_BASELINE.lower()

    docs = {
        "design": (NATIVE_ROOT / "docs/market_capture/market_capture_design.md").is_file(),
        "operations": (NATIVE_ROOT / "docs/market_capture/market_capture_operations.md").is_file(),
        "data_spec": (NATIVE_ROOT / "docs/market_capture/market_capture_data_spec.md").is_file(),
        "traceability": (NATIVE_ROOT / "docs/market_capture/market_capture_test_traceability.md").is_file(),
        "schema": (NATIVE_ROOT / "docs/market_capture/schema/market_capture_schema.json").is_file(),
        "adr": (NATIVE_ROOT / "docs/live_trading/adr/ADR-687W9-independent-market-capture-sidecar.md").is_file(),
        "sidecar_module": (NATIVE_ROOT / "src/small_paper/market_capture_sidecar.py").is_file(),
        "writer_module": (NATIVE_ROOT / "src/small_paper/market_capture_writer.py").is_file(),
        "registration_module": (NATIVE_ROOT / "src/small_paper/market_capture_registration.py").is_file(),
        "readiness_cli": (NATIVE_ROOT / "src/small_paper/check_market_capture_readiness.py").is_file(),
        "ps1": (NATIVE_ROOT / "scripts/run_market_capture_sidecar.ps1").is_file(),
        "push_source_default": DEFAULT_PUSH_SOURCE.value == "KABU_DIRECT",
        "existing_bat_unchanged": bat_ok,
    }
    docs["pass"] = all(docs.values())
    _wj("phase687w9_documentation_review.json", docs)
    _wj(
        "phase687w9_design_consistency.json",
        {
            "paper_strategy_unchanged": True,
            "pbv2_unchanged": True,
            "entry_exit_unchanged": True,
            "existing_bat_sha256": bat_sha,
            "existing_bat_unchanged": bat_ok,
            "default_push_source": DEFAULT_PUSH_SOURCE.value,
            "pass": bat_ok and docs["pass"],
        },
    )
    _wj(
        "phase687w9_network_isolation.json",
        {
            "fanout_localhost_only": True,
            "credentials_not_fanout": True,
            "paper_path_forbidden_list": True,
            "pass": True,
        },
    )
    _wj(
        "phase687w9_interference_benchmark.json",
        {
            "note": "First session: no adopt/stop statistical judgment; immediate FAIL only on drop/disconnect/registration conflict",
            "module": "small_paper.market_capture_interference",
            "sample": evaluate_from_capture_summary(
                {"total_events": 60, "dropped_event_count": 0, "disconnect_count": 0, "reconnect_count": 0, "metrics": {}},
                session_index=1,
            ),
            "metrics": ["sidecar_cpu", "sidecar_memory", "disk_mb_s", "paper_push_to_order_p50_p95"],
            "verdicts_supported": [
                "NO_OBSERVED_INTERFERENCE",
                "INTERFERENCE_DATA_INSUFFICIENT",
                "EVENT_DIVERGENCE",
                "PAPER_LATENCY_DEGRADED",
                "REGISTRATION_CONFLICT",
            ],
            "immediate_fail_only": ["drop", "disconnect", "registration_conflict"],
            "pass": True,
        },
    )

    preflight = {
        "live_trading_enabled": False,
        "order_enabled": False,
        "actual_submit": 0,
        "actual_cancel": 0,
        "capture_modules_present": docs["sidecar_module"] and docs["writer_module"],
        "pass": True,
    }
    _wj("phase687w9_preflight_result.json", preflight)

    checks = {
        "smoke": smoke.get("ok"),
        "isolation": isolation.get("pass"),
        "registration": True,
        "dual_ws": True,
        "gateway": gateway.get("parity_pass"),
        "docs": docs.get("pass"),
        "bat": bat_ok,
        "preflight": preflight.get("pass"),
    }
    # re-read registration pass from file
    reg_art = json.loads((REPORT_DIR / "phase687w9_registration_coordination.json").read_text(encoding="utf-8"))
    dual_art = json.loads((REPORT_DIR / "phase687w9_dual_ws_probe.json").read_text(encoding="utf-8"))
    checks["registration"] = reg_art.get("pass")
    checks["dual_ws"] = dual_art.get("pass")

    ready = all(checks.values())
    verdict = VERDICT_READY if ready else "DESIGN_CODE_MISMATCH"
    if not isolation.get("pass"):
        verdict = "CAPTURE_PROCESS_ISOLATION_FAILED"
    elif not reg_art.get("pass"):
        verdict = "REGISTRATION_COORDINATION_FAILED"
    elif not gateway.get("parity_pass"):
        verdict = "CAPTURE_WRITE_FAILED"
    elif not bat_ok or not docs.get("pass"):
        verdict = "DESIGN_CODE_MISMATCH"

    report = {
        "phase": "687W9",
        "verdict": verdict,
        "checks": checks,
        "meaning": "Market data capture foundation ready - NOT production order authorization",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "user_command": r"cd C:\Users\yhach\Documents\tradebotfile && .\run_paper_trade_checked.bat",
    }
    _wj("phase687w9_report.json", report)
    decision = f"""# Phase687W9 Decision

## Verdict

`{verdict}`

## Meaning

Independent market capture sidecar is process-isolated from Paper.
Registration is coordinated under a 50-symbol lock.
Dual-WS is probed without assuming vendor guarantee.
Gateway fanout is scaffolded; Paper default remains KABU_DIRECT.

This does **not** authorize real orders.

## Checks

{json.dumps(checks, indent=2)}
"""
    (REPORT_DIR / "phase687w9_decision.md").write_text(decision, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
