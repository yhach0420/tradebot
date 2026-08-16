"""V26-C/D/E real Station lifecycle probe. Paper only. Not Full Certification.

Records ENVIRONMENT_AUTH_BLOCKED when KabuS returns HTTP 401 / 4001007.
Does not rewrite API password. Does not report stub as real PASS.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

from api.rest_client import load_kabu_env
from small_paper.auth_lifecycle import PHASE_PRE_INGRESS, set_auth_phase
from small_paper.capture_child_cleanup import cleanup_owned_capture, record_owned_from_spawn
from small_paper.env_loader import ensure_repo_dotenv
from small_paper.ingress_run_identity import ENV_CERTIFICATION_RUN_ID, ENV_STAGE_RUN_ID, generate_launch_nonce
from small_paper.kabu_token_authority import load_station_bundle, load_station_owner, station_issue_audit_summary
from small_paper.market_ingress_spawn import spawn_ingress_process, wait_ingress_online
from small_paper.runtime_clock import ENV_CERT_MODE, ENV_KABU_AUTH_MODE, ENV_REPLAY_PATH, official_cert_child_env
from small_paper.runtime_lifecycle import finish_teardown, real_kabus_auth_ready, reconcile_startup


def _one_run(*, day: str, stage: str, cert: str) -> dict:
    os.environ[ENV_CERTIFICATION_RUN_ID] = cert
    os.environ[ENV_STAGE_RUN_ID] = stage
    child = official_cert_child_env(dict(os.environ))
    for k, v in child.items():
        os.environ[k] = str(v)
    set_auth_phase(PHASE_PRE_INGRESS)
    rec = reconcile_startup(native_root=NATIVE, trading_date=day)
    spawn = spawn_ingress_process(
        native_root=NATIVE,
        trading_date=day,
        python_exe=sys.executable,
        synthetic=False,
        code_root=NATIVE,
        allow_duplicate=True,
    )
    pid = int(spawn.get("pid") or 0)
    owned = record_owned_from_spawn(spawn, native_root=NATIVE) if pid > 0 else None
    wait = {}
    if pid > 0:
        wait = wait_ingress_online(
            NATIVE,
            day,
            timeout_sec=20.0,
            expected_launch_nonce=str(spawn.get("launch_nonce") or ""),
            expected_ingress_run_id=str(spawn.get("ingress_run_id") or ""),
            expected_activation_id=str(spawn.get("activation_id") or ""),
            expected_activation_sha=str(spawn.get("activation_sha") or ""),
            expected_pid=pid,
            expected_process_start_identity=str(spawn.get("process_start_identity") or ""),
            expected_bus_identity=str(spawn.get("bus_identity") or ""),
        )
    status = {}
    sp = NATIVE / "data" / "market_capture" / day / "ingress_status.json"
    if sp.is_file():
        try:
            status = json.loads(sp.read_text(encoding="utf-8"))
        except Exception:
            status = {}
    gate = real_kabus_auth_ready(status=status or wait.get("snapshot") or {})
    if owned is not None:
        cleanup_owned_capture(
            owned,
            reason="test_teardown",
            skip_capture_wait=True,
            graceful_timeout_sec=4.0,
            terminate_timeout_sec=3.0,
        )
    torn = finish_teardown(native_root=NATIVE, trading_date=day, owned_pid=pid)
    return {
        "spawn": {k: spawn.get(k) for k in ("ok", "pid", "launch_nonce", "ingress_run_id", "rejected", "reason", "ingress_stderr_log")},
        "wait": {
            "ok": wait.get("ok"),
            "reason": wait.get("reason"),
            "status": wait.get("status"),
            "http_status": wait.get("http_status"),
            "kabu_code": wait.get("kabu_code"),
            "REAL_KABUS_AUTH_READY": wait.get("REAL_KABUS_AUTH_READY"),
        },
        "reconcile": {"ok": rec.get("ok"), "ownership_class": rec.get("ownership_class"), "wrong_process_kill": rec.get("wrong_process_kill")},
        "status_auth": {
            "state": status.get("state"),
            "auth_failure_code": status.get("auth_failure_code"),
            "auth_failure_http_status": status.get("auth_failure_http_status"),
            "auth_failure_message_sanitized": status.get("auth_failure_message_sanitized"),
            "password_present": status.get("password_present"),
            "pid": status.get("pid"),
        },
        "gate": gate,
        "teardown": {
            "ok": (torn.get("residuals") or {}).get("ok"),
            "history_deleted": torn.get("history_deleted"),
            "wrong_process_kill": torn.get("wrong_process_kill"),
            "residuals": (torn.get("residuals") or {}).get("residuals"),
        },
        "audit": station_issue_audit_summary(),
        "bundle_generation": int((load_station_bundle() or {}).get("generation") or 0),
        "station_owner_pid": int((load_station_owner() or {}).get("pid") or 0),
    }


def main() -> int:
    ensure_repo_dotenv(repo_root=REPO)
    load_kabu_env(repo_root=REPO)
    load_kabu_env(repo_root=NATIVE)
    day = "20260812"
    os.environ[ENV_CERT_MODE] = "1"
    os.environ[ENV_KABU_AUTH_MODE] = "LIVE"
    os.environ["TRADEBOT_TRADING_DATE"] = day
    fixture = (
        NATIVE
        / "results"
        / "research"
        / "paper_runtime_full_day_certification"
        / "ingress_replay_20260812_full_day_certification.jsonl"
    )
    if fixture.is_file():
        os.environ[ENV_REPLAY_PATH] = str(fixture)
    nonce = generate_launch_nonce()[:12]
    cert_a = "cert_v26cde_real_a_" + nonce
    stage_a = "window_C_v26cde_real_a_" + nonce
    run_a = _one_run(day=day, stage=stage_a, cert=cert_a)
    time.sleep(1.0)
    cert_b = "cert_v26cde_real_b_" + nonce
    stage_b = "window_C_v26cde_real_b_" + nonce
    run_b = _one_run(day=day, stage=stage_b, cert=cert_b)
    out = {
        "at": datetime.now(JST).isoformat(timespec="seconds"),
        "paper_only": True,
        "submit_cancel_live": "0/0/0",
        "full_certification_run": False,
        "stub_reported_as_real_pass": False,
        "run_a": run_a,
        "run_b_restart": run_b,
        "REAL_KABUS_AUTH_READY": bool((run_a.get("gate") or {}).get("REAL_KABUS_AUTH_READY")),
        "ENVIRONMENT_AUTH_BLOCKED": bool((run_a.get("gate") or {}).get("ENVIRONMENT_AUTH_BLOCKED")),
        "http_status": (run_a.get("gate") or {}).get("http_status") or (run_a.get("wait") or {}).get("http_status"),
        "kabu_code": (run_a.get("gate") or {}).get("kabu_code") or (run_a.get("wait") or {}).get("kabu_code"),
    }
    dest = NATIVE / "results" / "research" / "v26cde_lifecycle_consolidation" / "real_probe.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
