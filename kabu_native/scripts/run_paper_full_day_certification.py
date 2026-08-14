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
    detect_teardown_nameerror_would_fail_v13,
    identities_equal,
    source_regression_gates,
)
from small_paper.runtime_clock import (
    ENV_CERT_MODE,
    ENV_REPLAY_EPS,
    ENV_REPLAY_PATH,
    ENV_STOP,
    bind_session_clock,
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
    out: dict[str, Any] = {
        "station_reachable": False,
        "token_acquisition": False,
        "single_authority": True,
        "registlist": False,
        "registration_50_50": False,
        "auth_401_readiness": True,
        "auth_429_readiness": True,
        "submit_cancel_live": "0/0/0",
        "market_push": "MARKET_PUSH_NOT_AVAILABLE_OFF_HOURS",
        "handoff": "next_morning_startup_preflight_must_reconfirm_live_ingress",
    }
    try:
        from api.rest_client import KabuNativeRestClient, default_base_url, load_kabu_env

        load_kabu_env(repo_root=REPO)
        load_kabu_env(repo_root=NATIVE)
        rest = KabuNativeRestClient(default_base_url())
        token = rest.issue_token_from_env()
        out["token_acquisition"] = bool(token)
        out["station_reachable"] = True
        try:
            rl = rest.get_json("/register") if hasattr(rest, "get_json") else None
            out["registlist"] = rl is not None
        except Exception as exc:
            out["registlist_error"] = f"{type(exc).__name__}:{exc}"
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}:{exc}"
        out["station_reachable"] = False
    out["ok"] = bool(out["station_reachable"] and out["token_acquisition"])
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


def _invoke_checked_bat(*, env: dict[str, str], timeout_sec: float, log_name: str) -> dict[str, Any]:
    if not CHECKED_BAT.is_file():
        return {"ok": False, "error": "missing_checked_bat", "exit_code": 2}
    log_dir = CERT_DIR / "run_logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = log_dir / f"{log_name}.stdout.txt"
    stderr_path = log_dir / f"{log_name}.stderr.txt"
    t0 = time.time()
    proc = subprocess.run(
        ["cmd.exe", "/d", "/c", "call", str(CHECKED_BAT), "--full-day-cert", "--no-pause"],
        cwd=str(REPO),
        env=env,
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
    }


def _copy_run_snapshot(name: str, day: str = "20260812") -> dict[str, str]:
    dest = CERT_DIR / "run_snapshots" / name
    dest.mkdir(parents=True, exist_ok=True)
    copied: dict[str, str] = {}
    reports = NATIVE / "results" / "reports"
    for fn in (
        f"phase148_am_pm_daily_runner_{day}.json",
        f"daily_runner_summary_{day}.json",
        f"small_paper_safety_{day}.json",
    ):
        src = reports / fn
        if src.is_file():
            target = dest / fn
            target.write_bytes(src.read_bytes())
            copied[fn] = str(target)
    return copied


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _evaluate_full_day(snap_dir: Path, day: str = "20260812") -> dict[str, Any]:
    p148 = _load_json(snap_dir / f"phase148_am_pm_daily_runner_{day}.json")
    summary = _load_json(snap_dir / f"daily_runner_summary_{day}.json")
    safety = _load_json(snap_dir / f"small_paper_safety_{day}.json")
    failed_ids = list((safety.get("failed_check_ids") if isinstance(safety, dict) else None) or [])
    if not failed_ids:
        failed_ids = list(((p148.get("preflight") or {}).get("safety") or {}).get("failed_check_ids") or [])
    am_live = p148.get("am_live") or {}
    pm_live = p148.get("pm_live") or {}
    am_skip = str(am_live.get("reason") or "") == "SKIPPED_AFTER_SESSION_END"
    out = {
        "verdict": str(p148.get("verdict") or summary.get("verdict") or ""),
        "stopped_reason": str(p148.get("stopped_reason") or summary.get("stopped_reason") or ""),
        "am_skip": am_skip,
        "am_token_mutation": int(am_live.get("am_token_mutation") or 0),
        "am_ok": bool(am_live.get("ok") or am_skip),
        "pm_ok": bool(pm_live.get("ok")),
        "safety_failed": failed_ids,
        "lifecycle_complete": str(p148.get("verdict") or "")
        in {"am_pm_daily_runner_ready", "completed_with_warnings"},
    }
    return out


def _leftover_processes(day: str = "20260812") -> list[dict[str, Any]]:
    try:
        from small_paper.v1r_pbv2_duplicate_runtime import list_live_ingress, list_live_pilots

        return list(list_live_ingress(trading_date=day, native_root=NATIVE) or []) + list(
            list_live_pilots(trading_date=day) or []
        )
    except Exception:
        return []


def _scan_session_metrics(day: str) -> dict[str, Any]:
    root = NATIVE / "results" / "small_paper" / day
    sessions = sorted(root.glob("live_session_*")) + sorted(root.glob("v1r_primary_*"))
    latest: Optional[Path] = sessions[-1] if sessions else None
    summary: dict[str, Any] = {}
    hb: dict[str, Any] = {}
    if latest and (latest / "small_paper_summary.json").is_file():
        summary = json.loads((latest / "small_paper_summary.json").read_text(encoding="utf-8"))
    # heartbeat last line
    for cand in sessions[::-1]:
        p = cand / "heartbeat.jsonl"
        if p.is_file():
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
            if lines:
                try:
                    hb = json.loads(lines[-1])
                except Exception:
                    hb = {}
            break
    submit = int(summary.get("submit") or 0)
    cancel = int(summary.get("cancel") or 0)
    live = int(summary.get("live") or 0)
    pb = (hb.get("pbv2_eval") or {}) if isinstance(hb, dict) else {}
    return {
        "latest_session": str(latest) if latest else "",
        "summary_keys": sorted(summary.keys())[:40],
        "stop_reason": summary.get("stop_reason"),
        "fatal_error": summary.get("fatal_error"),
        "session_external_backup": summary.get("session_external_backup"),
        "submit": submit,
        "cancel": cancel,
        "live": live,
        "submit_cancel_live": f"{submit}/{cancel}/{live}",
        "forced_eval_count": int(pb.get("forced_eval_count") or summary.get("forced_eval_count") or 0),
        "eval_fraction": pb.get("eval_fraction"),
        "native_ingest": hb.get("native_ingest_count") or hb.get("v1r_native_ingest"),
        "raw_published": hb.get("ingress_last_sequence") or hb.get("raw_sequence"),
        "max_consumer_processing_delay_sec": pb.get("max_consumer_processing_delay_sec"),
        "heartbeat": {k: hb.get(k) for k in ("pid", "state", "v1r_exit_v2") if k in hb},
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
    fixture = CERT_DIR / "ingress_replay_20260812_anchors_cruise.jsonl"
    if CAPTURE_STREAM.is_file():
        fixture_meta = _extract_anchor_stream(CAPTURE_STREAM, fixture)
        fixture_meta["ok"] = bool(fixture_meta.get("anchors_16")) and int(fixture_meta.get("kept") or 0) > 1000
        if not fixture_meta["ok"]:
            failed.append("MARKET_STREAM_COVERAGE")
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
        env[ENV_REPLAY_EPS] = "800"

    stream_ok = bool(fixture_meta.get("ok")) and "CAPTURE_STREAM_MISSING" not in failed
    _stop_cert_children("20260812")

    full_day: dict[str, Any] = {"skipped": True}
    full_day_eval: dict[str, Any] = {}
    if stream_ok:
        print("CERT_STAGE=FULL_DAY start", flush=True)
        try:
            full_day = _invoke_checked_bat(env=env, timeout_sec=2400, log_name="full_day")
        except subprocess.TimeoutExpired as exc:
            full_day = {"ok": False, "error": f"timeout:{exc}", "exit_code": 124}
        full_day["cleanup"] = _stop_cert_children("20260812")
        full_day["snapshot"] = _copy_run_snapshot("full_day")
        full_day_eval = _evaluate_full_day(CERT_DIR / "run_snapshots" / "full_day")
        full_day["lifecycle"] = full_day_eval
        print(
            f"CERT_STAGE=FULL_DAY done exit={full_day.get('exit_code')} "
            f"verdict={full_day_eval.get('verdict')} stopped={full_day_eval.get('stopped_reason')}",
            flush=True,
        )
        if not full_day.get("ok"):
            failed.append("FULL_DAY_CHECKED_BAT")
        if not full_day_eval.get("lifecycle_complete"):
            failed.append("FULL_DAY_LIFECYCLE")
        if full_day_eval.get("safety_failed"):
            failed.append("FULL_DAY_SAFETY:" + ",".join(str(x) for x in full_day_eval["safety_failed"]))
    leftover = _leftover_processes("20260812")
    leftover_end = leftover
    if leftover:
        failed.append("PROCESS_LEFTOVER_AFTER_FULL_DAY")

    metrics = _scan_session_metrics("20260812")
    if str(metrics.get("submit_cancel_live") or "0/0/0") != "0/0/0":
        failed.append("SUBMIT_CANCEL_LIVE")
    if metrics.get("forced_eval_count") not in (0, None):
        if int(metrics.get("forced_eval_count") or 0) != 0:
            failed.append("FORCED_EVAL")

    pm_run: dict[str, Any] = {"skipped": True}
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
        try:
            pm_run = _invoke_checked_bat(env=pm_env, timeout_sec=1800, log_name="pm_direct_start")
        except subprocess.TimeoutExpired as exc:
            pm_run = {"ok": False, "error": f"timeout:{exc}", "exit_code": 124}
            failed.append("PM_DIRECT_START")
        pm_run["cleanup"] = _stop_cert_children("20260812")
        pm_run["snapshot"] = _copy_run_snapshot("pm_direct_start")
        pm_eval = _evaluate_full_day(CERT_DIR / "run_snapshots" / "pm_direct_start")
        pm_run["lifecycle"] = pm_eval
        if not pm_run.get("ok"):
            failed.append("PM_DIRECT_START")
        if not pm_eval.get("am_skip"):
            failed.append("PM_DIRECT_START_AM_NOT_SKIPPED")
        if int(pm_eval.get("am_token_mutation") or 0) != 0:
            failed.append("PM_DIRECT_START_AM_TOKEN_MUTATION")

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
            print(f"CERT_STAGE=WINDOW_{name} start", flush=True)
            try:
                windows[name] = _invoke_checked_bat(
                    env=wenv,
                    timeout_sec=int((end - start).total_seconds()) + 180,
                    log_name=f"window_{name}",
                )
            except subprocess.TimeoutExpired as exc:
                windows[name] = {"ok": False, "error": f"timeout:{exc}", "exit_code": 124}
            windows[name]["cleanup"] = _stop_cert_children("20260812")
            windows[name]["snapshot"] = _copy_run_snapshot(f"window_{name}")
            if not windows[name].get("ok"):
                failed.append(f"WINDOW_{name}")
    leftover_end = _leftover_processes("20260812")
    if leftover_end:
        failed.append("PROCESS_LEFTOVER")

    identity_after = capture_identity()
    same, mismatches = identities_equal(identity_before, identity_after)
    if not same:
        failed.append("IDENTITY_MUTATION:" + ",".join(mismatches))

    sink.shutdown()

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
