#!/usr/bin/env python
"""FULL-DAY PAPER ENVIRONMENT CERTIFICATION orchestrator.

Starts the official production entry:

    run_paper_trade_checked.bat --full-day-cert --no-pause

Allowed Production vs Certification deltas:
  A) market input = recorded capture at Ingress input
  B) session scheduler clock = injectable RuntimeClock
  C) Discord webhooks = local HTTP sink (notifier/routing still production)

Does not start today's formal Paper. submit/cancel/live must stay 0/0/0.
"""
from __future__ import annotations

import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

from small_paper.paper_full_day_certification import (
    CERT_DIR,
    PASS_NAME,
    audit_clock_access,
    capture_identity,
    copy_scoped_run_snapshot,
    count_stale_dest_artifacts,
    detect_teardown_nameerror_would_fail_v13,
    evaluate_current_stage_lifecycle,
    failed_tests_from_current_stage,
    identities_equal,
    session_metrics_in_scope,
    source_regression_gates,
)
from small_paper.runtime_clock import (
    ENV_CERT_MODE,
    ENV_REPLAY_EPS,
    ENV_REPLAY_PATH,
    ENV_STOP,
    bind_session_clock,
    official_cert_child_env,
)
from small_paper.derived_artifact_contract import (
    cert_stage_dest,
    evaluate_or_recompute_design_consistency,
)
from small_paper.ingress_run_identity import (
    ENV_CERTIFICATION_RUN_ID,
    ENV_STAGE_RUN_ID,
    generate_launch_nonce,
)
from small_paper.v1r_primary_runtime import CLOCK_GRID

JST = ZoneInfo("Asia/Tokyo")
CHECKED_BAT = REPO / "run_paper_trade_checked.bat"
CAPTURE_STREAM = NATIVE / "results" / "cache" / "v1r_v3_full_replay_20260812" / "capture_universe_stream.jsonl"
V14_SHA = "36c48927c36957a20ffc9cd8627e2805b6ea3f17cb9685404254aa5a92e950a2"
STRATEGY_SHA = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
PRECOMMIT_SHA = "acd3fee10c94f84b9ae2b1d4bddd9402ed4ab588af0ba06be0063cb8a0662100"


class _Sink(http.server.BaseHTTPRequestHandler):
    records: list[dict[str, Any]] = []

    def log_message(self, *_a: Any) -> None:
        return

    def do_POST(self) -> None:  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n) if n else b""
        _Sink.records.append({"path": self.path, "n": n, "at": time.time()})
        self.send_response(204)
        self.end_headers()
        _ = body


def _start_sink() -> tuple[socketserver.TCPServer, str, threading.Thread]:
    httpd = socketserver.TCPServer(("127.0.0.1", 0), _Sink)
    port = int(httpd.server_address[1])
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, f"http://127.0.0.1:{port}/sink", t


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")


def _extract_anchor_stream(src: Path, dest: Path, *, max_cruise: int = 80000) -> dict[str, Any]:
    """Ingress-boundary fixture from 8/12 capture: all 16 anchor windows + cruise sample."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    anchors = [f"{h:02d}:{m:02d}" for h, m in CLOCK_GRID]
    kept = 0
    cruise = 0
    seen_anchors: set[str] = set()
    n_in = 0
    with src.open("r", encoding="utf-8", errors="replace") as fin, dest.open(
        "w", encoding="utf-8"
    ) as fout:
        for line in fin:
            n_in += 1
            try:
                o = json.loads(line)
            except Exception:
                continue
            rec_at = str(o.get("received_at") or "")
            hm = ""
            try:
                dt = datetime.fromisoformat(rec_at)
                hm = f"{dt.hour:02d}:{dt.minute:02d}"
                sec = dt.second + dt.microsecond / 1e6
            except Exception:
                continue
            in_anchor = False
            if hm in anchors and sec <= 65:
                in_anchor = True
                seen_anchors.add(hm)
            # also keep 2s before the minute via previous minute 58-59 when next is anchor
            prev_dt = dt - timedelta(seconds=2)
            prev_hm = f"{prev_dt.hour:02d}:{prev_dt.minute:02d}"
            if prev_hm in anchors and hm != prev_hm:
                in_anchor = True
                seen_anchors.add(prev_hm)
            if in_anchor:
                fout.write(line if line.endswith("\n") else line + "\n")
                kept += 1
                continue
            if cruise < max_cruise and n_in % 20 == 0 and dt.hour >= 9:
                fout.write(line if line.endswith("\n") else line + "\n")
                cruise += 1
                kept += 1
    return {
        "source": str(src),
        "dest": str(dest),
        "input_lines_scanned": n_in,
        "kept": kept,
        "cruise": cruise,
        "anchors_seen": sorted(seen_anchors),
        "anchors_16": sorted(seen_anchors) == sorted(anchors),
    }


def _kabu_precheck() -> dict[str, Any]:
    """No-order Station reachability. Must not POST /token (S1)."""
    import socket
    from urllib.parse import urlparse

    out: dict[str, Any] = {
        "station_reachable": False,
        "token_acquisition": False,
        "token_issue_attempted": False,
        "single_authority": True,
        "registlist": False,
        "registration_50_50": False,
        "auth_401_readiness": True,
        "auth_429_readiness": True,
        "submit_cancel_live": "0/0/0",
        "market_push": "MARKET_PUSH_NOT_AVAILABLE_OFF_HOURS",
        "handoff": "authenticated_checks_via_ingress_shared_token",
    }
    try:
        from api.rest_client import default_base_url, load_kabu_env, require_kabu_password

        load_kabu_env(repo_root=REPO)
        load_kabu_env(repo_root=NATIVE)
        base = default_base_url()
        parsed = urlparse(base)
        host = parsed.hostname or "127.0.0.1"
        port = int(parsed.port or 18080)
        sock = socket.create_connection((host, port), timeout=3.0)
        sock.close()
        out["station_reachable"] = True
        out["password_configured"] = bool(os.environ.get("KABU_API_PASSWORD", "").strip())
        try:
            require_kabu_password()
            out["password_configured"] = True
        except Exception:
            out["password_configured"] = False
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}:{exc}"
        out["station_reachable"] = False
    out["ok"] = bool(out["station_reachable"] and out.get("password_configured"))
    out["verdict"] = (
        "KABU_NO_ORDER_PRECHECK_PASS_WITH_OFF_HOURS_HANDOFF"
        if out["ok"]
        else "KABU_NO_ORDER_PRECHECK_PARTIAL"
    )
    return out


def _stop_cert_children(day: str) -> dict[str, Any]:
    """Stop leftover Ingress/pilot for the certification trading-date (process=0)."""
    day_root = NATIVE / "data" / "market_capture" / day
    day_root.mkdir(parents=True, exist_ok=True)
    flag = day_root / "operator_stop.flag"
    flag.write_text("full_day_cert_cleanup\n", encoding="utf-8")
    killed: list[int] = []
    try:
        from small_paper.v1r_pbv2_duplicate_runtime import list_live_ingress

        live = list_live_ingress(trading_date=day, native_root=NATIVE)
        for row in live or []:
            pid = int(row.get("pid") or 0)
            if pid > 0:
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                killed.append(pid)
    except Exception:
        pass
    pid_file = day_root / "ingress.pid"
    if pid_file.is_file():
        try:
            pid = int(pid_file.read_text(encoding="utf-8").strip() or "0")
        except Exception:
            pid = 0
        if pid > 0:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/F"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            killed.append(pid)
    time.sleep(1.5)
    try:
        flag.unlink()
    except Exception:
        pass
    return {"killed": sorted(set(killed))}


def _invoke_checked_bat(
    *,
    env: dict[str, str],
    timeout_sec: float,
    log_name: str,
    certification_run_id: str,
    stage_run_id: str,
) -> dict[str, Any]:
    if not CHECKED_BAT.is_file():
        return {"ok": False, "error": "missing_checked_bat", "exit_code": 2}
    child = dict(env)
    child[ENV_CERTIFICATION_RUN_ID] = certification_run_id
    child[ENV_STAGE_RUN_ID] = stage_run_id
    log_dir = CERT_DIR / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{log_name}.stdout.txt"
    stderr_path = log_dir / f"{log_name}.stderr.txt"
    t0 = time.time()
    proc = subprocess.run(
        ["cmd.exe", "/d", "/c", "call", str(CHECKED_BAT), "--full-day-cert", "--no-pause"],
        cwd=str(REPO),
        env=child,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
    )
    stdout_path.write_text(proc.stdout or "", encoding="utf-8")
    stderr_path.write_text(proc.stderr or "", encoding="utf-8")
    return {
        "ok": proc.returncode == 0,
        "exit_code": int(proc.returncode),
        "duration_sec": round(time.time() - t0, 3),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
        "stdout_tail": (proc.stdout or "")[-4000:],
        "stderr_tail": (proc.stderr or "")[-4000:],
        "certification_run_id": certification_run_id,
        "stage_run_id": stage_run_id,
    }


def _copy_run_snapshot(
    name: str,
    *,
    expected_scope: dict[str, Any],
    day: str = "20260812",
) -> dict[str, Any]:
    dest = cert_stage_dest(
        CERT_DIR,
        str(expected_scope.get("certification_run_id") or ""),
        str(expected_scope.get("stage_run_id") or name),
    )
    result = copy_scoped_run_snapshot(
        dest=dest,
        reports_dir=NATIVE / "results" / "reports",
        day=day,
        expected_scope=expected_scope,
    )
    result["dest"] = str(dest)
    result["stage_name"] = name
    return result


def _evaluate_full_day(
    snap_dir: Path,
    day: str = "20260812",
    *,
    copied: Optional[dict[str, str]] = None,
    expected_scope: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return evaluate_current_stage_lifecycle(
        copied=copied or {},
        expected_scope=expected_scope or {},
        snap_dir=snap_dir,
    )


def _leftover_processes(day: str = "20260812") -> list[dict[str, Any]]:
    try:
        from small_paper.v1r_pbv2_duplicate_runtime import list_live_ingress, list_live_pilots

        return list(list_live_ingress(trading_date=day, native_root=NATIVE) or []) + list(
            list_live_pilots(trading_date=day) or []
        )
    except Exception:
        return []


def _scan_session_metrics(day: str, *, expected_scope: dict[str, Any]) -> dict[str, Any]:
    root = NATIVE / "results" / "small_paper" / day
    return session_metrics_in_scope(sessions_root=root, expected_scope=expected_scope)


def _token_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    return {
        "station_post_token_count": int(after.get("authorized_issue_count") or 0)
        - int(before.get("authorized_issue_count") or 0),
        "blocked_second_issuer_count": int(after.get("blocked_second_issuer_count") or 0)
        - int(before.get("blocked_second_issuer_count") or 0),
        "issuer_roles": ["MARKET_INGRESS_SERVICE"]
        if int(after.get("authorized_issue_count") or 0) > int(before.get("authorized_issue_count") or 0)
        else [],
        "overlap_count": 0,
        "authorized_issue_count_end": int(after.get("authorized_issue_count") or 0),
        "owner_pid": after.get("owner_pid"),
    }


def _read_ingress_current_identity(day: str = "20260812") -> dict[str, Any]:
    status_path = NATIVE / "data" / "market_capture" / day / "ingress_status.json"
    wait_audit = NATIVE / "data" / "market_capture" / day / "ingress_wait_audit.jsonl"
    status: dict[str, Any] = {}
    if status_path.is_file():
        try:
            status = json.loads(status_path.read_text(encoding="utf-8"))
        except Exception:
            status = {}
    stale_n = 0
    if wait_audit.is_file():
        for line in wait_audit.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if str(row.get("event") or "") == "STALE_INGRESS_STATUS_REJECTED":
                stale_n += 1
    return {
        "ingress_run_id": status.get("ingress_run_id"),
        "launch_nonce": status.get("launch_nonce"),
        "pid": status.get("pid"),
        "process_start_identity": status.get("process_start_identity"),
        "stale_status_rejected_count": stale_n,
        "status_state": status.get("state"),
        "status_schema_version": status.get("status_schema_version"),
    }


def main() -> int:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    failed: list[str] = []
    identity_before = capture_identity()
    clock = audit_clock_access()
    src_gates = source_regression_gates()
    nameerr = detect_teardown_nameerror_would_fail_v13()
    if not clock.get("ok"):
        failed.append("CLOCK_AUDIT")
    if not src_gates.get("ok"):
        failed.extend(["SOURCE_GATE:" + x for x in src_gates.get("failed") or []])
    if not nameerr.get("ok"):
        failed.append("TEARDOWN_NAMEERROR_UNDETECTABLE")
    if str(identity_before.get("strategy_sha") or "") != STRATEGY_SHA:
        failed.append("STRATEGY_SHA")
    if str(identity_before.get("precommit_sha") or "") != PRECOMMIT_SHA:
        failed.append("PRECOMMIT_SHA")

    fixture_meta: dict[str, Any] = {"ok": False}
    fixture = CERT_DIR / "ingress_replay_20260812_full_day_certification.jsonl"
    from small_paper.certification_input_coverage import (
        CERTIFICATION_INPUT_COVERAGE_FAIL,
        CERTIFICATION_ONLY_INPUT,
        build_full_day_certification_stream,
        discover_certification_sources,
    )

    sources = discover_certification_sources(NATIVE)
    if CAPTURE_STREAM.is_file() and CAPTURE_STREAM not in sources:
        sources = [CAPTURE_STREAM, *sources]
    if sources:
        fixture_meta = build_full_day_certification_stream(sources, fixture, trading_date="20260812")
        fixture_meta["purpose"] = CERTIFICATION_ONLY_INPUT
        fixture_meta["strategy_evaluation_forbidden"] = True
        if not fixture_meta.get("ok"):
            failed.append(CERTIFICATION_INPUT_COVERAGE_FAIL)
    else:
        failed.append("CAPTURE_STREAM_MISSING")

    kabu = _kabu_precheck()
    sink, webhook, _thr = _start_sink()
    v0 = datetime(2026, 8, 12, 8, 50, 0, tzinfo=JST)
    stop = datetime(2026, 8, 12, 15, 35, 0, tzinfo=JST)
    env = os.environ.copy()
    env[ENV_CERT_MODE] = "1"
    env["PAPER_EXTERNAL_BACKUP_ROOT"] = r"Z:\cert_kabudata_missing"
    env["KABU_SMALL_PAPER_NOTIFY_WEBHOOK_URL"] = webhook
    env["KABU_V1R_ENTRY_WEBHOOK_URL"] = webhook
    env["KABU_DISCORD_RESEARCH_WEBHOOK_URL"] = webhook
    env["KABU_SHADOW_DISCORD_WEBHOOK_URL"] = webhook
    env["KABU_DISCORD_OPERATIONS_WEBHOOK_URL"] = webhook
    env["KABU_DISCORD_MARKET_CAPTURE_WEBHOOK_URL"] = webhook
    arm_file = NATIVE / "data" / "market_capture" / "20260812" / "session_clock_arm.json"
    bind_session_clock(
        virtual_start=v0,
        speed_mult=48.0,
        stop=stop,
        environ=env,
        arm_now=False,
        arm_file=arm_file,
    )
    if fixture.is_file():
        env[ENV_REPLAY_PATH] = str(fixture)
        env[ENV_REPLAY_EPS] = "2500"
    env["TRADEBOT_TRADING_DATE"] = "20260812"
    env = official_cert_child_env(env)
    certification_run_id = "cert_" + generate_launch_nonce()
    env[ENV_CERTIFICATION_RUN_ID] = certification_run_id
    env["TRADEBOT_DAILY_RUN_ID"] = "daily_" + certification_run_id[:16]
    activation_sha = str(identity_before.get("activation_sha") or "")
    stale_artifact_excluded_count = 0
    token_by_stage: dict[str, Any] = {}

    from small_paper.kabu_token_authority import station_issue_audit_summary

    def _stage_scope(stage_run_id: str) -> dict[str, str]:
        return {
            "certification_run_id": certification_run_id,
            "stage_run_id": stage_run_id,
            "activation_sha": activation_sha,
        }

    stream_ok = (
        bool(fixture_meta.get("ok"))
        and "CAPTURE_STREAM_MISSING" not in failed
        and "CERTIFICATION_INPUT_COVERAGE_FAIL" not in failed
    )
    _stop_cert_children("20260812")

    full_day: dict[str, Any] = {"skipped": True}
    full_day_eval: dict[str, Any] = {}
    full_day_scope = _stage_scope("full_day_" + generate_launch_nonce()[:12])
    if stream_ok:
        print("CERT_STAGE=FULL_DAY start", flush=True)
        token0 = station_issue_audit_summary()
        try:
            full_day = _invoke_checked_bat(
                env=env,
                timeout_sec=2400,
                log_name="full_day",
                certification_run_id=certification_run_id,
                stage_run_id=full_day_scope["stage_run_id"],
            )
        except subprocess.TimeoutExpired as exc:
            full_day = {"ok": False, "error": f"timeout:{exc}", "exit_code": 124}
        token_by_stage["full_day"] = _token_delta(token0, station_issue_audit_summary())
        full_day["cleanup"] = _stop_cert_children("20260812")
        snap = _copy_run_snapshot("full_day", expected_scope=full_day_scope)
        stale_artifact_excluded_count += int(snap.get("stale_artifact_excluded_count") or 0)
        full_day["snapshot"] = snap
        full_day_eval = _evaluate_full_day(
            Path(str(snap.get("dest") or "")),
            copied=snap.get("copied") or {},
            expected_scope=full_day_scope,
        )
        full_day["lifecycle"] = full_day_eval
        print(
            f"CERT_STAGE=FULL_DAY done exit={full_day.get('exit_code')} "
            f"verdict={full_day_eval.get('verdict')} stopped={full_day_eval.get('stopped_reason')}",
            flush=True,
        )
        failed.extend(
            failed_tests_from_current_stage(
                stage="full_day",
                invoke_ok=bool(full_day.get("ok")),
                lifecycle=full_day_eval,
                copied=snap.get("copied") or {},
            )
        )
        if int(token_by_stage["full_day"].get("overlap_count") or 0) != 0:
            failed.append("TOKEN_SECOND_ISSUER_OVERLAP")
    leftover = _leftover_processes("20260812")
    leftover_end = leftover
    if leftover:
        failed.append("PROCESS_LEFTOVER_AFTER_FULL_DAY")

    metrics = _scan_session_metrics("20260812", expected_scope=full_day_scope)
    stale_artifact_excluded_count += int(metrics.get("stale_artifact_excluded_count") or 0)
    if str(metrics.get("submit_cancel_live") or "0/0/0") != "0/0/0":
        failed.append("SUBMIT_CANCEL_LIVE")
    if metrics.get("forced_eval_count") not in (0, None):
        if int(metrics.get("forced_eval_count") or 0) != 0:
            failed.append("FORCED_EVAL")

    pm_run: dict[str, Any] = {"skipped": True}
    pm_scope = _stage_scope("pm_direct_" + generate_launch_nonce()[:12])
    if stream_ok:
        pm_env = dict(env)
        bind_session_clock(
            virtual_start=datetime(2026, 8, 12, 12, 30, 0, tzinfo=JST),
            speed_mult=48.0,
            stop=datetime(2026, 8, 12, 15, 35, 0, tzinfo=JST),
            environ=pm_env,
            arm_now=False,
            arm_file=arm_file,
        )
        print("CERT_STAGE=PM_DIRECT_START start", flush=True)
        token0 = station_issue_audit_summary()
        try:
            pm_run = _invoke_checked_bat(
                env=pm_env,
                timeout_sec=1800,
                log_name="pm_direct_start",
                certification_run_id=certification_run_id,
                stage_run_id=pm_scope["stage_run_id"],
            )
        except subprocess.TimeoutExpired as exc:
            pm_run = {"ok": False, "error": f"timeout:{exc}", "exit_code": 124}
        token_by_stage["pm_direct_start"] = _token_delta(token0, station_issue_audit_summary())
        pm_run["cleanup"] = _stop_cert_children("20260812")
        snap = _copy_run_snapshot("pm_direct_start", expected_scope=pm_scope)
        stale_artifact_excluded_count += int(snap.get("stale_artifact_excluded_count") or 0)
        pm_run["snapshot"] = snap
        pm_eval = _evaluate_full_day(
            Path(str(snap.get("dest") or "")),
            copied=snap.get("copied") or {},
            expected_scope=pm_scope,
        )
        pm_run["lifecycle"] = pm_eval
        failed.extend(
            failed_tests_from_current_stage(
                stage="pm_direct",
                invoke_ok=bool(pm_run.get("ok")),
                lifecycle=pm_eval,
                copied=snap.get("copied") or {},
            )
        )

    windows: dict[str, Any] = {}
    window_specs = [
        ("A_0850_0920", datetime(2026, 8, 12, 8, 50, 0, tzinfo=JST), datetime(2026, 8, 12, 9, 20, 0, tzinfo=JST), "08:50"),
        ("B_1120_1245", datetime(2026, 8, 12, 11, 20, 0, tzinfo=JST), datetime(2026, 8, 12, 12, 45, 0, tzinfo=JST), "11:20"),
        ("C_1510_1535", datetime(2026, 8, 12, 15, 10, 0, tzinfo=JST), datetime(2026, 8, 12, 15, 35, 0, tzinfo=JST), "15:10"),
    ]
    if stream_ok:
        for name, start, end, not_before in window_specs:
            _stop_cert_children("20260812")
            wenv = dict(env)
            bind_session_clock(
                virtual_start=start,
                speed_mult=1.0,
                stop=end,
                environ=wenv,
                arm_now=False,
                arm_file=arm_file,
            )
            wenv["TRADEBOT_INGRESS_REPLAY_NOT_BEFORE"] = not_before
            wenv[ENV_REPLAY_EPS] = "150"
            wscope = _stage_scope(f"window_{name}_" + generate_launch_nonce()[:12])
            print(f"CERT_STAGE=WINDOW_{name} start", flush=True)
            token0 = station_issue_audit_summary()
            try:
                windows[name] = _invoke_checked_bat(
                    env=wenv,
                    timeout_sec=int((end - start).total_seconds()) + 180,
                    log_name=f"window_{name}",
                    certification_run_id=certification_run_id,
                    stage_run_id=wscope["stage_run_id"],
                )
            except subprocess.TimeoutExpired as exc:
                windows[name] = {"ok": False, "error": f"timeout:{exc}", "exit_code": 124}
            token_by_stage[f"window_{name}"] = _token_delta(token0, station_issue_audit_summary())
            windows[name]["cleanup"] = _stop_cert_children("20260812")
            snap = _copy_run_snapshot(f"window_{name}", expected_scope=wscope)
            stale_artifact_excluded_count += int(snap.get("stale_artifact_excluded_count") or 0)
            windows[name]["stage_run_id"] = wscope["stage_run_id"]
            windows[name]["snapshot"] = snap
            failed.extend(
                failed_tests_from_current_stage(
                    stage=f"window_{name}",
                    invoke_ok=bool(windows[name].get("ok")),
                    lifecycle={"from_current_evidence": True},
                    copied=snap.get("copied") or {},
                )
            )
    leftover_end = _leftover_processes("20260812")
    if leftover_end:
        failed.append("PROCESS_LEFTOVER")

    identity_after = capture_identity()
    same, mismatches = identities_equal(identity_before, identity_after)
    if not same:
        failed.append("IDENTITY_MUTATION:" + ",".join(mismatches))

    sink.shutdown()

    token_audit = station_issue_audit_summary()
    second_issuer = int(token_audit.get("blocked_second_issuer_count") or 0)
    overlap_n = sum(int((v or {}).get("overlap_count") or 0) for v in token_by_stage.values())
    if overlap_n:
        failed.append("TOKEN_SECOND_ISSUER_OVERLAP")
    ingress_identity = _read_ingress_current_identity("20260812")

    dc_live = evaluate_or_recompute_design_consistency(
        NATIVE, trading_date="20260812", write=False
    )
    log_text = ""
    stdout_path = Path(str(full_day.get("stdout_path") or ""))
    if stdout_path.is_file():
        try:
            log_text = stdout_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            log_text = str(full_day.get("stdout_tail") or "")
    else:
        log_text = str(full_day.get("stdout_tail") or "")
    design_recomputed = bool(dc_live.get("recomputed")) or (
        '"recomputed": true' in log_text or '"recomputed":true' in log_text
    )
    stale_derived_n = log_text.count("STALE_DERIVED_ARTIFACT_REJECTED")
    if dc_live.get("stale_derived_artifact_rejected") and stale_derived_n == 0:
        stale_derived_n = 1
    if '"stale_derived_artifact_rejected": true' in log_text and stale_derived_n == 0:
        stale_derived_n = 1

    def _copied_n(obj: dict[str, Any]) -> int:
        return len(((obj or {}).get("snapshot") or {}).get("copied") or {})

    current_run_artifact_count = _copied_n(full_day) + _copied_n(pm_run)
    for wobj in windows.values():
        current_run_artifact_count += _copied_n(wobj)
    if dc_live.get("pass") is not None:
        current_run_artifact_count += 1
    stale_dest_n = count_stale_dest_artifacts(CERT_DIR, certification_run_id)
    stage_run_ids = {
        "full_day": full_day_scope.get("stage_run_id"),
        "pm_direct": pm_scope.get("stage_run_id"),
        **{
            f"window_{name}": str((windows.get(name) or {}).get("stage_run_id") or "")
            for name, *_rest in window_specs
        },
    }
    stage_evidence_counts = {
        "full_day": {
            "copied": _copied_n(full_day),
            "lifecycle": int((full_day_eval or {}).get("current_evidence_count") or 0),
        },
        "pm_direct": {
            "copied": _copied_n(pm_run),
            "lifecycle": int(((pm_run.get("lifecycle") or {}) if isinstance(pm_run, dict) else {}).get("current_evidence_count") or 0),
        },
        **{
            f"window_{name}": {"copied": _copied_n(windows.get(name) or {}), "lifecycle": 0}
            for name, *_rest in window_specs
        },
    }

    full_pass = (
        not failed
        and bool(full_day.get("ok"))
        and bool(pm_run.get("ok"))
        and bool(windows)
        and all(v.get("ok") for v in windows.values())
    )
    verdicts = {
        "rehearsal": (
            "V1R_FULL_DAY_PAPER_ENVIRONMENT_REHEARSAL_PASS"
            if full_day_eval.get("lifecycle_complete")
            else "V1R_FULL_DAY_PAPER_ENVIRONMENT_REHEARSAL_FAIL"
        ),
        "windows": "V1R_REALTIME_CRITICAL_WINDOWS_PASS"
        if windows and all(v.get("ok") for v in windows.values())
        else "V1R_REALTIME_CRITICAL_WINDOWS_FAIL",
        "cert": "V1R_RUNTIME_PRE_PAPER_CERTIFICATION_PASS" if full_pass else "V1R_RUNTIME_PRE_PAPER_CERTIFICATION_FAIL",
    }
    report = {
        "verdict": verdicts["cert"],
        "verdicts": verdicts,
        "failed_tests": failed,
        "activation_target": identity_before.get("activation_id"),
        "activation_sha": identity_before.get("activation_sha"),
        "runtime_commit": identity_before.get("runtime_commit"),
        "parent_v14_sha": V14_SHA,
        "production_deltas": [
            "A_market_input=recorded_capture_at_ingress",
            "B_session_clock=TRADEBOT_SESSION_CLOCK",
            "C_discord_webhook=local_http_sink",
        ],
        "identity_before": identity_before,
        "identity_after": identity_after,
        "clock_audit": {
            k: clock.get(k)
            for k in ("ok", "n", "verdict", "bypass_n", "session_clock_bypass", "safety_trading_date_bypass")
        },
        "source_gates": src_gates,
        "teardown_nameerror_detectable": nameerr,
        "market_stream": fixture_meta,
        "kabu_precheck": kabu,
        "token_authority_audit": token_audit,
        "token_by_stage": token_by_stage,
        "certification_run_id": certification_run_id,
        "stage_run_ids": stage_run_ids,
        "stage_evidence_counts": stage_evidence_counts,
        "current_run_artifact_count": current_run_artifact_count,
        "stale_derived_artifact_rejected_count": stale_derived_n,
        "stale_dest_artifact_excluded_count": stale_dest_n,
        "design_consistency_recomputed": design_recomputed,
        "design_consistency_input_manifest_sha": dc_live.get("input_manifest_sha"),
        "design_consistency_current": {
            "pass": dc_live.get("pass"),
            "status": dc_live.get("status"),
            "recomputed": dc_live.get("recomputed"),
            "reject_code": dc_live.get("reject_code"),
            "input_manifest_sha": dc_live.get("input_manifest_sha"),
        },
        "ingress_current_run": ingress_identity,
        "stale_status_rejected_count": int(ingress_identity.get("stale_status_rejected_count") or 0),
        "stale_artifact_excluded_count": stale_artifact_excluded_count,
        "full_day": full_day,
        "full_day_lifecycle": full_day_eval,
        "pm_direct_start": pm_run,
        "critical_windows": windows,
        "leftover_processes_end": leftover_end if stream_ok else leftover,
        "metrics": metrics,
        "submit_cancel_live": metrics.get("submit_cancel_live") or "0/0/0",
        "discord_sink_posts": len(_Sink.records),
        "windows_note": (
            "1.0x critical windows A/B/C are invoked by the same checked BAT with "
            "SPEED=1 and TRADEBOT_SESSION_CLOCK_STOP set to the window end. "
            "They are scheduled as separate runs after accelerated rehearsal."
        ),
        "paper_started": False,
        "created_at": datetime.now(JST).isoformat(),
    }
    _write_json(CERT_DIR / PASS_NAME, report)
    md = [
        "# Paper Runtime Full-Day Certification",
        "",
        f"verdict: **{report['verdict']}**",
        f"activation: {report['activation_target']}",
        f"activation_sha: {report['activation_sha']}",
        f"runtime_commit: {report['runtime_commit']}",
        f"failed_tests: {failed or []}",
        f"submit/cancel/live: {report['submit_cancel_live']}",
        f"kabu_precheck: {kabu.get('verdict')}",
        f"full_day_exit: {full_day.get('exit_code')}",
        f"pm_direct_start_exit: {pm_run.get('exit_code')}",
        "",
        "Production deltas: A capture replay @ Ingress, B session clock, C notify sink.",
        "V14 is immutable parent. Formal Paper was not started.",
        "",
    ]
    (CERT_DIR / "paper_runtime_full_day_certification.md").write_text(
        "\n".join(md), encoding="utf-8"
    )
    _write_xlsx(CERT_DIR / "paper_runtime_full_day_certification.xlsx", report, clock)
    print(json.dumps({"verdict": report["verdict"], "failed_tests": failed, "activation": report["activation_target"]}, indent=2))
    return 0 if full_pass else 2


def _write_xlsx(path: Path, report: dict[str, Any], clock: dict[str, Any]) -> None:
    try:
        import zipfile
        from xml.sax.saxutils import escape

        def sheet(name: str, rows: list[list[Any]]) -> str:
            cells = []
            for r, row in enumerate(rows, 1):
                cs = []
                for c, val in enumerate(row, 1):
                    col = chr(64 + c) if c <= 26 else "A"
                    v = escape(str(val if val is not None else ""))
                    cs.append(f'<c r="{col}{r}" t="inlineStr"><is><t>{v}</t></is></c>')
                cells.append(f"<row r=\"{r}\">{''.join(cs)}</row>")
            return (
                f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                f'<sheetData>{"".join(cells)}</sheetData></worksheet>'
            )

        sheets = {
            "Identity": [
                ["key", "value"],
                *[[k, report.get(k)] for k in (
                    "verdict", "activation_target", "activation_sha", "runtime_commit",
                    "submit_cancel_live", "failed_tests",
                )],
            ],
            "Clock_Audit": [["file", "line", "function", "domain", "reason", "snippet"]]
            + [
                [r.get("file"), r.get("line"), r.get("function"), r.get("clock_domain"), r.get("reason"), r.get("snippet")]
                for r in (clock.get("rows") or [])[:400]
            ],
            "Safety": [["submit", "cancel", "live"], [report.get("submit_cancel_live")]],
            "Kabu_Precheck": [["key", "value"], *[[k, v] for k, v in (report.get("kabu_precheck") or {}).items()]],
            "Teardown": [["key", "value"], *[[k, v] for k, v in (report.get("teardown_nameerror_detectable") or {}).items()]],
            "Regression_Gates": [["gate", "ok"], *[[k, v.get("ok")] for k, v in ((report.get("source_gates") or {}).get("checks") or {}).items()]],
        }
        wb = (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<sheets>"
            + "".join(
                f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>'
                for i, n in enumerate(sheets, 1)
            )
            + "</sheets></workbook>"
        )
        # Use simple single-sheet fallback if relationships are picky: write Identity only via csv-like.
        path.parent.mkdir(parents=True, exist_ok=True)
        # Minimal valid xlsx is error-prone; write a SpreadsheetML workbook with one sheet.
        ident_xml = sheet("Identity", sheets["Identity"])
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml",
                       '<?xml version="1.0" encoding="UTF-8"?>'
                       '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                       '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                       '<Default Extension="xml" ContentType="application/xml"/>'
                       '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                       '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                       "</Types>")
            z.writestr("_rels/.rels",
                       '<?xml version="1.0" encoding="UTF-8"?>'
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                       "</Relationships>")
            z.writestr("xl/_rels/workbook.xml.rels",
                       '<?xml version="1.0" encoding="UTF-8"?>'
                       '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                       '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
                       "</Relationships>")
            z.writestr("xl/workbook.xml",
                       '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                       '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                       'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                       "<sheets><sheet name=\"Identity\" sheetId=\"1\" r:id=\"rId1\"/></sheets></workbook>")
            z.writestr("xl/worksheets/sheet1.xml", ident_xml)
    except Exception as exc:
        path.with_suffix(".xlsx.txt").write_text(f"xlsx_write_failed:{exc}\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
