"""V26-B step 4: one-stage production-path AUTH issue reproduction.

Does not delete gen34 / pid14372 residue. Does not start Full Certification.
Stops only the Ingress this script spawns.
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
from small_paper.capture_child_cleanup import request_graceful_stop
from small_paper.env_loader import ensure_repo_dotenv
from small_paper.ingress_run_identity import ENV_CERTIFICATION_RUN_ID, ENV_STAGE_RUN_ID, generate_launch_nonce
from small_paper.kabu_token_authority import load_station_bundle, load_station_owner, station_issue_audit_summary
from small_paper.market_ingress_spawn import spawn_ingress_process
from small_paper.runtime_clock import ENV_CERT_MODE, ENV_KABU_AUTH_MODE, ENV_REPLAY_PATH, official_cert_child_env


def main() -> int:
    ensure_repo_dotenv(repo_root=REPO)
    load_kabu_env(repo_root=REPO)
    load_kabu_env(repo_root=NATIVE)
    day = "20260812"
    os.environ[ENV_CERT_MODE] = "1"
    os.environ[ENV_KABU_AUTH_MODE] = "LIVE"
    os.environ["TRADEBOT_TRADING_DATE"] = day
    fixture = NATIVE / "results" / "research" / "paper_runtime_full_day_certification" / "ingress_replay_20260812_full_day_certification.jsonl"
    if fixture.is_file():
        os.environ[ENV_REPLAY_PATH] = str(fixture)
    cert_id = "cert_v26b_obs_" + generate_launch_nonce()[:12]
    stage_id = "window_C_v26b_obs_" + generate_launch_nonce()[:12]
    os.environ[ENV_CERTIFICATION_RUN_ID] = cert_id
    os.environ[ENV_STAGE_RUN_ID] = stage_id
    child = official_cert_child_env(dict(os.environ))
    for k, v in child.items():
        os.environ[k] = str(v)
    set_auth_phase(PHASE_PRE_INGRESS)
    from api.kabu_register import clear_register_before_session

    preclear = clear_register_before_session(REPO)
    before = {
        "bundle_pid": int((load_station_bundle() or {}).get("pid") or 0),
        "generation": int((load_station_bundle() or {}).get("generation") or 0),
        "station_owner_pid": int((load_station_owner() or {}).get("pid") or 0),
        "audit": station_issue_audit_summary(),
        "preclear": preclear,
    }
    spawn = spawn_ingress_process(
        native_root=NATIVE,
        trading_date=day,
        python_exe=sys.executable,
        synthetic=False,
        code_root=NATIVE,
        allow_duplicate=True,
    )
    pid = int(spawn.get("pid") or 0)
    time.sleep(12.0)
    day_dir = NATIVE / "data" / "market_capture" / day
    status = {}
    sp = day_dir / "ingress_status.json"
    if sp.is_file():
        status = json.loads(sp.read_text(encoding="utf-8"))
    traces = []
    tp = day_dir / "auth_issue_trace.jsonl"
    if tp.is_file():
        for line in tp.read_text(encoding="utf-8").splitlines()[-80:]:
            if line.strip():
                traces.append(json.loads(line))
    fail = {}
    fp = day_dir / "auth_issue_last_failure.json"
    if fp.is_file():
        fail = json.loads(fp.read_text(encoding="utf-8"))
    sess_status = {}
    if pid:
        for d in day_dir.glob(f"session_ing_{day}_{pid}_*"):
            ss = d / "status.json"
            if ss.is_file():
                sess_status = json.loads(ss.read_text(encoding="utf-8"))
                break
    if pid:
        try:
            request_graceful_stop(day_dir, pid=pid, reason="v26b_obs_repro_stop")
            time.sleep(2.0)
        except Exception:
            pass
        try:
            import subprocess

            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            pass
    out = {
        "at": datetime.now(JST).isoformat(timespec="seconds"),
        "certification_run_id": cert_id,
        "stage_run_id": stage_id,
        "spawn": {k: spawn.get(k) for k in ("ok", "pid", "launch_nonce", "ingress_run_id", "rejected", "reason")},
        "before": before,
        "after_audit": station_issue_audit_summary(),
        "status_auth": {
            "state": status.get("state"),
            "entry_block_reason": status.get("entry_block_reason"),
            "last_error": status.get("last_error"),
            "last_error_type": status.get("last_error_type"),
            "auth_failure_step": status.get("auth_failure_step"),
            "auth_failure_type": status.get("auth_failure_type"),
            "auth_failure_code": status.get("auth_failure_code"),
            "auth_failure_message_sanitized": status.get("auth_failure_message_sanitized"),
            "auth_failure_at": status.get("auth_failure_at"),
            "auth_failure_http_status": status.get("auth_failure_http_status"),
            "password_present": status.get("password_present"),
            "pid": status.get("pid"),
        },
        "session_status_auth": {
            "state": sess_status.get("state"),
            "last_error": sess_status.get("last_error"),
            "last_error_type": sess_status.get("last_error_type"),
            "auth_failure_step": sess_status.get("auth_failure_step"),
            "auth_failure_type": sess_status.get("auth_failure_type"),
            "auth_failure_message_sanitized": sess_status.get("auth_failure_message_sanitized"),
            "auth_failure_code": sess_status.get("auth_failure_code"),
        },
        "last_failure_file": fail,
        "trace_events": [t.get("event") for t in traces],
        "trace_tail": traces[-20:],
        "bundle_generation_after": int((load_station_bundle() or {}).get("generation") or 0),
        "submit_cancel_live": "0/0/0",
    }
    dest = NATIVE / "results" / "research" / "v26b_auth_ownership" / "obs_repro.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
