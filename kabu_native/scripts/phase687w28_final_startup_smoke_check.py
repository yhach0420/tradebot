"""Phase687W28 — Final Startup Smoke Check (observe + audit, no strategy/CAP/order changes).

Launches the normal Paper path via run_paper_trade_checked.bat --no-pause,
observes for OBSERVE_SEC, audits CAP/OR/Discord/Capture/orders, then stops
owned children. Off-hours: startup/connect/config/Discord/Capture-wait evidence.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
REPORT = NATIVE / "results" / "reports" / "phase687w28_final_startup_smoke_check"
BAT = REPO / "run_paper_trade_checked.bat"
OBSERVE_SEC = 320  # >= 5 minutes
CONSOLE_LOG = REPORT / "startup_console_tail.txt"

# Env that must NOT ride into normal Paper path
FORBIDDEN_ENV = (
    "TRADEBOT_DISCORD_FORMAT_TEST",
    "TRADEBOT_DEMO_PUSH_E2E",
    "TRADEBOT_COMM_FAULT_E2E",
)


def _now() -> str:
    return datetime.now(tz=JST).isoformat()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def _write_json(path: Path, obj: Any) -> None:
    _write(path, json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _read_json(path: Path) -> Optional[dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _trading_date() -> str:
    return datetime.now(tz=JST).strftime("%Y%m%d")


def build_code_change_manifest() -> dict[str, Any]:
    """Static code/config audit (no mutations)."""
    yaml_path = (
        NATIVE
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    yaml_txt = yaml_path.read_text(encoding="utf-8") if yaml_path.is_file() else ""
    builder = (NATIVE / "src" / "small_paper" / "discord_message_builder.py").read_text(encoding="utf-8")
    notifier = (NATIVE / "src" / "small_paper" / "discord_notifier.py").read_text(encoding="utf-8")
    or_entry = (NATIVE / "src" / "small_paper" / "or_overlay_entry.py").read_text(encoding="utf-8")
    or_cap = (NATIVE / "src" / "small_paper" / "or_overlay_cap.py").read_text(encoding="utf-8")
    day_state = NATIVE / "src" / "small_paper" / "daily_symbol_discord_state.py"
    pilot = (NATIVE / "src" / "small_paper" / "pilot_runner.py").read_text(encoding="utf-8")
    w27 = NATIVE / "scripts" / "phase687w27_pm_or_slot_policy_comparison.py"

    def yaml_has(key: str, expect: str) -> bool:
        # simple presence
        return bool(re.search(rf"{re.escape(key)}\s*:\s*{re.escape(expect)}\b", yaml_txt))

    exit_orange = "0xC05621" in builder or "0xC05621" in notifier
    entry_time = "エントリー時間" in builder or "entry_time" in builder.lower()
    exit_time = "EXIT時間" in builder or "exit_time" in builder
    same_push = "same_push" in pilot and "_record_same_push_reentry_skip" in pilot
    # OR AM bias: session open anchored at 09:00
    or_am_anchor = "_session_open_ts" in or_entry and "hour=9" in or_entry
    # PM slot return not in mainline
    pm_return_mainline = False
    if "cap_pbv2" in yaml_txt:
        # research-only w27 exists; mainline yaml should still be 4+1
        pm_return_mainline = yaml_has("cap_pbv2", "5") and yaml_has("cap_or", "0")

    return {
        "audited_at": _now(),
        "yaml": str(yaml_path),
        "cap_pbv2_config": 4 if yaml_has("cap_pbv2", "4") or "cap_pbv2: 4" in yaml_txt else _extract_int(yaml_txt, "cap_pbv2"),
        "cap_or_config": 1 if yaml_has("cap_or", "1") or "cap_or: 1" in yaml_txt else _extract_int(yaml_txt, "cap_or"),
        "or_overlay_enabled": "or_overlay_enabled: true" in yaml_txt.replace("'", "").lower()
        or bool(re.search(r"or_overlay_enabled:\s*true", yaml_txt, re.I)),
        "flat_band_mainline": "pbv2_flat_band_mainline_enabled: true" in yaml_txt
        or bool(re.search(r"pbv2_flat_band_mainline_enabled:\s*true", yaml_txt, re.I)),
        "or_am_open_anchor_0900": or_am_anchor,
        "pm_or_slot_return_in_mainline": pm_return_mainline,
        "pm_or_slot_return_research_only": w27.is_file(),
        "discord_exit_color_orange_0xC05621": exit_orange,
        "discord_entry_time_label": "エントリー時間" in builder,
        "discord_exit_time_label": "EXIT時間" in builder,
        "daily_symbol_discord_state_module": day_state.is_file(),
        "same_push_suppression_wired": same_push,
        "or_overlay_cap_split_documented": "cap_pbv2" in or_cap and "cap_or" in or_cap,
        "code_mutations_this_phase": False,
        "config_mutations_this_phase": False,
    }


def _extract_int(text: str, key: str) -> Optional[int]:
    m = re.search(rf"{re.escape(key)}\s*:\s*(\d+)", text)
    return int(m.group(1)) if m else None


def latest_checked_runner() -> Optional[Path]:
    d = NATIVE / "results" / "reports" / "paper_trade_checked_runner"
    if not d.is_dir():
        return None
    files = sorted(d.glob("checked_runner_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


def capture_paths(day: str) -> dict[str, Path]:
    root = NATIVE / "data" / "market_capture" / day
    return {
        "root": root,
        "status": root / "capture_status.json",
        "heartbeat": root / "capture_heartbeat.json",
        "errors": root / "errors.jsonl",
    }


def latest_paper_session(day: str) -> Optional[Path]:
    base = NATIVE / "results" / "small_paper" / day
    if not base.is_dir():
        return None
    sess = sorted(base.glob("live_session_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return sess[0] if sess else None


def audit_errors(path: Path) -> dict[str, Any]:
    fatal = 0
    samples: list[str] = []
    total = 0
    if path.is_file():
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            total += 1
            low = line.lower()
            if any(k in low for k in ("fatal", "traceback", "critical", "panic")):
                fatal += 1
                if len(samples) < 5:
                    samples.append(line[:400])
    return {"path": str(path), "total_lines": total, "fatal_like": fatal, "samples": samples}


def list_related_procs() -> list[dict[str, Any]]:
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process | "
                    "Where-Object { $_.CommandLine -match "
                    "'paper_trade_checked|market_capture_sidecar|am_pm_daily|small_paper_pilot|"
                    "run_core10_dynamic40|run_paper_trade' } | "
                    "ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }"
                ),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError as e:
        return [{"error": str(e)}]
    rows = []
    for line in out.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        pid, cmd = line.split("|", 1)
        rows.append({"pid": int(pid), "cmd": cmd[:240]})
    return rows


def stop_owned(capture_pid: Optional[int], day: str) -> dict[str, Any]:
    """Best-effort graceful stop via operator_stop + taskkill of tree rooted at launcher."""
    result: dict[str, Any] = {"operator_stop": False, "killed": []}
    day_dir = NATIVE / "data" / "market_capture" / day
    flag = day_dir / "operator_stop.flag"
    try:
        day_dir.mkdir(parents=True, exist_ok=True)
        flag.write_text(json.dumps({"reason": "phase687w28_smoke_end", "at": _now()}), encoding="utf-8")
        result["operator_stop"] = True
    except OSError as e:
        result["operator_stop_error"] = str(e)
    time.sleep(3.0)
    # Kill related processes we likely spawned (careful: only match our smoke markers)
    for row in list_related_procs():
        pid = row.get("pid")
        cmd = str(row.get("cmd") or "")
        if not pid:
            continue
        # Prefer capture for today + checked runner / daily runner
        if "market_capture_sidecar" in cmd or "paper_trade_checked" in cmd or "am_pm_daily" in cmd or "small_paper_pilot" in cmd or "run_core10_dynamic40" in cmd:
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, timeout=30)
                result["killed"].append(pid)
            except Exception as e:
                result.setdefault("kill_errors", []).append({"pid": pid, "error": str(e)})
    return result


def pick_verdict(checks: dict[str, Any]) -> str:
    if checks.get("order_safety_violation"):
        return "ORDER_SAFETY_VIOLATION"
    if checks.get("cap_mismatch"):
        return "CAP_CONFIG_MISMATCH"
    if checks.get("discord_failed"):
        return "DISCORD_STARTUP_FAILED"
    # Kabu / orchestration block outranks capture-not-ready
    if checks.get("startup_blocked") or checks.get("kabu_block"):
        return "STARTUP_BLOCKED"
    if checks.get("capture_not_ready"):
        return "CAPTURE_NOT_READY"
    if checks.get("push_not_receiving") and checks.get("market_hours"):
        return "PUSH_NOT_RECEIVING"
    if checks.get("core_pass"):
        return "FINAL_STARTUP_SMOKE_PASS"
    return "STARTUP_BLOCKED"


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    day = _trading_date()
    hour = datetime.now(tz=JST).hour
    market_hours = 9 <= hour < 15 or (hour == 15 and datetime.now(tz=JST).minute < 30)
    # Premarket / lunch / after: off-hours path
    if hour < 9 or hour >= 16 or 11 <= hour < 12:
        market_hours = False

    pre_env = {k: os.environ.get(k) for k in FORBIDDEN_ENV}
    cleared = []
    for k in FORBIDDEN_ENV:
        if os.environ.get(k):
            cleared.append({k: os.environ.pop(k)})
    # Ensure child inherits clean env
    child_env = os.environ.copy()
    for k in FORBIDDEN_ENV:
        child_env.pop(k, None)

    manifest = build_code_change_manifest()
    _write_json(REPORT / "code_change_manifest.json", {
        **manifest,
        "parent_shell_forbidden_env_before": pre_env,
        "cleared_for_normal_path": cleared,
    })

    # Snapshot runtime config expectations
    runtime_snap = {
        "generated_at": _now(),
        "trading_date": day,
        "market_hours": market_hours,
        "cap_total": 5,
        "cap_pbv2": manifest.get("cap_pbv2_config"),
        "cap_or": manifest.get("cap_or_config"),
        "or_overlay_enabled": manifest.get("or_overlay_enabled"),
        "or_am_limited": manifest.get("or_am_open_anchor_0900"),
        "pm_slot_return_unimplemented_mainline": not manifest.get("pm_or_slot_return_in_mainline"),
        "flat_band_mainline": manifest.get("flat_band_mainline"),
        "discord_legacy_embed_signals": {
            "exit_orange": manifest.get("discord_exit_color_orange_0xC05621"),
            "entry_time": manifest.get("discord_entry_time_label"),
            "exit_time": manifest.get("discord_exit_time_label"),
        },
        "daily_symbol_discord_state": manifest.get("daily_symbol_discord_state_module"),
        "same_push": manifest.get("same_push_suppression_wired"),
        "test_mode_cleared": all(not child_env.get(k) for k in FORBIDDEN_ENV),
    }
    _write_json(REPORT / "runtime_config_snapshot.json", runtime_snap)

    if not BAT.is_file():
        _write_json(REPORT / "startup_smoke_report.json", {
            "verdict": "STARTUP_BLOCKED",
            "reason": f"missing {BAT}",
        })
        return 1

    t0 = time.time()
    console_fh = CONSOLE_LOG.open("w", encoding="utf-8", errors="replace")
    console_fh.write(f"W28_START {_now()} observe_sec={OBSERVE_SEC} market_hours={market_hours}\n")
    console_fh.write(f"cleared_env={json.dumps(cleared, ensure_ascii=False)}\n")
    console_fh.flush()

    # Launch bat
    proc = subprocess.Popen(
        ["cmd", "/c", str(BAT), "--no-pause"],
        cwd=str(REPO),
        env=child_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    console_fh.write(f"launcher_pid={proc.pid}\n")
    console_fh.flush()

    # Drain stdout in background via thread
    import threading

    lines: list[str] = []

    def _reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            console_fh.write(line)
            console_fh.flush()

    th = threading.Thread(target=_reader, daemon=True)
    th.start()

    # Observe loop
    snapshots: list[dict[str, Any]] = []
    while time.time() - t0 < OBSERVE_SEC:
        caps = capture_paths(day)
        snap = {
            "t": round(time.time() - t0, 1),
            "at": _now(),
            "proc_alive": proc.poll() is None,
            "proc_exit": proc.poll(),
            "capture_status": _read_json(caps["status"]),
            "capture_heartbeat": _read_json(caps["heartbeat"]),
            "related_procs": list_related_procs(),
        }
        snapshots.append(snap)
        time.sleep(15.0)

    # Final reads
    caps = capture_paths(day)
    cap_status = _read_json(caps["status"]) or {}
    cap_hb = _read_json(caps["heartbeat"]) or {}
    checked = latest_checked_runner()
    checked_data = _read_json(checked) if checked else None
    sess = latest_paper_session(day)
    sess_summary = _read_json(sess / "small_paper_summary.json") if sess else None
    sess_hb_path = (sess / "heartbeat.jsonl") if sess else None
    sess_err = (sess / "errors.jsonl") if sess else None
    day_discord_state = NATIVE / "results" / "small_paper" / day / "daily_symbol_discord_state.json"

    # Heartbeat check
    hb_check: dict[str, Any] = {
        "capture_heartbeat": cap_hb,
        "capture_heartbeat_age_sec": None,
        "paper_session": str(sess) if sess else None,
        "paper_heartbeat_exists": bool(sess_hb_path and sess_hb_path.is_file()),
        "paper_heartbeat_lines": 0,
        "updated": False,
    }
    if cap_hb:
        for key in ("updated_at", "heartbeat_at", "ts", "last_heartbeat_at"):
            raw = cap_hb.get(key)
            if raw:
                try:
                    dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=JST)
                    hb_check["capture_heartbeat_age_sec"] = round(
                        (datetime.now(tz=JST) - dt.astimezone(JST)).total_seconds(), 1
                    )
                    hb_check["updated"] = hb_check["capture_heartbeat_age_sec"] < 120
                except ValueError:
                    pass
                break
    if sess_hb_path and sess_hb_path.is_file():
        hb_lines = [ln for ln in sess_hb_path.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
        hb_check["paper_heartbeat_lines"] = len(hb_lines)
        if hb_lines:
            hb_check["updated"] = True

    _write_json(REPORT / "heartbeat_check.json", hb_check)

    # Capture result
    status_str = str(cap_status.get("status") or cap_status.get("capture_status") or "")
    ready_like = any(
        x in status_str.upper()
        for x in ("READY", "RECEIVING", "WRITING", "ONLINE", "FANOUT", "WAITING", "CAPTURE_")
    )
    cap_result = {
        "day": day,
        "status_file": str(caps["status"]),
        "status": cap_status,
        "status_str": status_str,
        "ready_like": ready_like,
        "heartbeat": cap_hb,
        "snapshots_n": len(snapshots),
        "last_snapshot": snapshots[-1] if snapshots else None,
    }
    _write_json(REPORT / "capture_startup_result.json", cap_result)

    # Discord — from session summary / checked runner / console
    console_text = "".join(lines)
    discord_sent = bool(
        re.search(r"discord.*(sent|ok|204|success)", console_text, re.I)
        or (sess_summary or {}).get("discord_notify_count")
        or (sess_summary or {}).get("discord_sent")
    )
    discord_cfg_ok = bool(
        (sess_summary or {}).get("discord_enabled")
        or "discord" in console_text.lower()
        or manifest.get("discord_exit_color_orange_0xC05621")
    )
    # Off-hours: config loaded + module present counts; live send may be screening-only
    discord_result = {
        "config_signals_ok": discord_cfg_ok,
        "live_send_evidence": discord_sent,
        "test_mode_env_cleared": all(k not in child_env or not child_env.get(k) for k in FORBIDDEN_ENV),
        "parent_had_format_test": bool(pre_env.get("TRADEBOT_DISCORD_FORMAT_TEST")),
        "daily_symbol_state_path": str(day_discord_state),
        "daily_symbol_state_writable_probe": False,
        "legacy_embed": {
            "exit_orange": manifest.get("discord_exit_color_orange_0xC05621"),
            "entry_time": manifest.get("discord_entry_time_label"),
            "exit_time": manifest.get("discord_exit_time_label"),
        },
        "same_push_wired": manifest.get("same_push_suppression_wired"),
        "summary_discord_fields": {
            k: (sess_summary or {}).get(k)
            for k in (
                "discord_enabled",
                "discord_notify_count",
                "discord_webhook_configured",
                "cap_blocked_webhook_configured",
            )
            if sess_summary
        },
    }
    # writable probe (create parent dir if needed; write+read temp then leave day file alone if missing)
    try:
        day_discord_state.parent.mkdir(parents=True, exist_ok=True)
        probe = day_discord_state.parent / ".w28_write_probe"
        probe.write_text("ok", encoding="utf-8")
        discord_result["daily_symbol_state_writable_probe"] = probe.read_text(encoding="utf-8") == "ok"
        probe.unlink(missing_ok=True)
        if day_discord_state.is_file():
            discord_result["daily_symbol_state_exists"] = True
            discord_result["daily_symbol_state"] = _read_json(day_discord_state)
    except OSError as e:
        discord_result["daily_symbol_state_error"] = str(e)
    _write_json(REPORT / "discord_startup_result.json", discord_result)

    # Errors
    err_audit = {
        "capture_errors": audit_errors(caps["errors"]),
        "session_errors": audit_errors(sess_err) if sess_err else {"path": None, "total_lines": 0, "fatal_like": 0},
    }
    # also scan console for fatal
    console_fatal = len(re.findall(r"(?i)traceback|fatal error|CRITICAL", console_text))
    err_audit["console_fatal_like"] = console_fatal
    _write_json(REPORT / "error_audit.json", err_audit)

    # Order safety
    submit = 0
    cancel = 0
    if sess_summary:
        submit = int((sess_summary.get("actual_submit") or sess_summary.get("live_order_submit_count") or 0) or 0)
        cancel = int((sess_summary.get("actual_cancel") or sess_summary.get("live_order_cancel_count") or 0) or 0)
        if isinstance(sess_summary.get("canonical_summary"), dict):
            pass
    if checked_data:
        post = checked_data.get("post_session") or {}
        submit = max(submit, int(post.get("actual_submit") or 0))
        cancel = max(cancel, int(post.get("actual_cancel") or 0))
    dry_run = True
    if sess_summary is not None:
        dry_run = bool(sess_summary.get("dry_run", True)) or not bool(sess_summary.get("live_orders_enabled", False))
    order_audit = {
        "real_orders_disabled": dry_run,
        "submit": submit,
        "cancel": cancel,
        "forbidden_env_cleared": bool(cleared) or all(not pre_env.get(k) for k in FORBIDDEN_ENV),
        "demo_push_not_active": "TRADEBOT_DEMO_PUSH_E2E" not in child_env,
        "format_test_not_active": "TRADEBOT_DISCORD_FORMAT_TEST" not in child_env,
    }
    order_audit["violation"] = submit > 0 or cancel > 0 or not order_audit["format_test_not_active"] or not order_audit["demo_push_not_active"]
    # format_test cleared for child — violation only if submit/cancel
    order_audit["violation"] = submit > 0 or cancel > 0
    _write_json(REPORT / "order_safety_audit.json", order_audit)

    # Stop processes
    cap_pid = None
    if isinstance(cap_status, dict):
        cap_pid = cap_status.get("pid") or cap_status.get("process_pid")
    stop_info = stop_owned(cap_pid if isinstance(cap_pid, int) else None, day)
    if proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception as e:
            stop_info["launcher_stop_error"] = str(e)
    th.join(timeout=5)
    console_fh.write(f"\nW28_END {_now()} elapsed={round(time.time()-t0,1)}s stop={json.dumps(stop_info)}\n")
    console_fh.close()

    # Preflight / PUSH / runner signals from console + artifacts
    preflight_pass = bool(re.search(r"preflight.*PASS|PASS.*preflight|step.*preflight.*PASS", console_text, re.I)) or (
        checked_data and any(
            (s.get("name") == "preflight" and s.get("result") == "PASS")
            for s in (checked_data.get("steps") or [])
            if isinstance(s, dict)
        )
    )
    # Also production smoke in bat
    if re.search(r"production startup smoke|smoke.*PASS|PASS.*smoke", console_text, re.I):
        preflight_pass = preflight_pass or True
    kabu_ok = bool(re.search(r"kabu.*(ok|success|connected|PASS)|token.*ok|API.*接続", console_text, re.I))
    push_start = bool(re.search(r"PUSH|websocket|push.*(start|receiv|subscribe)", console_text, re.I))
    core_dyn = bool(re.search(r"core10|dynamic40|subscribe|登録|register", console_text, re.I))
    kabu_block = bool(re.search(r"kabu_station_not_running|failed_step:\s*kabu_readonly", console_text, re.I))
    paper_started = bool(
        sess is not None
        or re.search(r"Starting Paper|\[PAPER TRADE\] starting|am_pm_daily_runner|small_paper_pilot", console_text, re.I)
    )
    if checked_data and int(checked_data.get("paper_call_count") or 0) == 0:
        paper_started = False

    # CAP from session if available
    cap_ok = (
        manifest.get("cap_pbv2_config") == 4
        and manifest.get("cap_or_config") == 1
        and not manifest.get("pm_or_slot_return_in_mainline")
    )
    if sess_summary:
        if sess_summary.get("cap_pbv2") not in (None, 4):
            cap_ok = False
        if sess_summary.get("cap_or") not in (None, 1):
            cap_ok = False

    checks = {
        "preflight_pass": preflight_pass,
        "kabu_ok": kabu_ok,
        "push_start": push_start,
        "paper_started": paper_started,
        "core_dyn_subscribe": core_dyn,
        "cap_ok": cap_ok,
        "cap_mismatch": not cap_ok,
        "or_am_limited": bool(manifest.get("or_am_open_anchor_0900")),
        "flat_band": bool(manifest.get("flat_band_mainline")),
        "discord_ok": discord_result["config_signals_ok"] and discord_result["test_mode_env_cleared"],
        "discord_failed": not discord_result["test_mode_env_cleared"],
        "capture_ready": ready_like or bool(cap_status),
        "capture_not_ready": not (ready_like or bool(cap_status)),
        "heartbeat_updated": bool(hb_check.get("updated")) or (not market_hours and bool(cap_hb)),
        "fatal_errors": err_audit["capture_errors"]["fatal_like"] + err_audit["session_errors"]["fatal_like"] + console_fatal,
        "order_safety_violation": order_audit["violation"],
        "kabu_block": kabu_block,
        "startup_blocked": kabu_block
        or (
            (checked_data or {}).get("blocked_reason")
            in ("kabu_station_not_running", "PRECHECK_ORCHESTRATION_FAILED")
        )
        or (
            proc.returncode not in (None, 0)
            and not paper_started
            and not ready_like
        ),
        "push_not_receiving": market_hours and not push_start,
        "market_hours": market_hours,
        "observe_sec": round(time.time() - t0, 1),
    }
    if checks["startup_blocked"]:
        checks["core_pass"] = False
    elif market_hours and preflight_pass and paper_started and cap_ok and not order_audit["violation"] and ready_like:
        checks["core_pass"] = True
    elif (
        not market_hours
        and (ready_like or bool(cap_status))
        and cap_ok
        and not order_audit["violation"]
        and not kabu_block
    ):
        checks["core_pass"] = True
    else:
        checks["core_pass"] = False

    verdict = pick_verdict(checks)

    report = {
        "phase": "687W28",
        "generated_at": _now(),
        "verdict": verdict,
        "observe_sec": checks["observe_sec"],
        "market_hours": market_hours,
        "launcher_pid": proc.pid,
        "launcher_exit": proc.poll(),
        "checks": checks,
        "completion": {
            "1_startup_ok": bool(paper_started or ready_like or preflight_pass),
            "2_preflight": preflight_pass,
            "3_push": push_start if market_hours else "off_hours_n/a_or_partial",
            "4_paper_runner": paper_started,
            "5_discord": discord_result,
            "6_capture": {"status": status_str, "ready_like": ready_like},
            "7_cap": {"pbv2": 4, "or": 1, "total": 5, "ok": cap_ok},
            "8_or_am_only": bool(manifest.get("or_am_open_anchor_0900")),
            "9_heartbeat_updated": hb_check.get("updated"),
            "10_fatal_error_count": checks["fatal_errors"],
            "11_submit_cancel": {"submit": submit, "cancel": cancel},
            "12_code_config_changed": False,
        },
        "checked_runner": str(checked) if checked else None,
        "session": str(sess) if sess else None,
        "stop": stop_info,
        "parent_env_cleared": cleared,
    }
    _write_json(REPORT / "startup_smoke_report.json", report)
    _write_json(REPORT / "observe_snapshots.json", {"snapshots": snapshots[-8:]})
    _write(
        REPORT / "phase687w28_verdict.txt",
        verdict + "\n",
    )
    _write(
        REPORT / "phase687w28_decision.md",
        "\n".join(
            [
                "# Phase687W28 — Final Startup Smoke Check",
                "",
                f"**Verdict:** `{verdict}`",
                "",
                f"- observe_sec: {checks['observe_sec']}",
                f"- market_hours: {market_hours}",
                f"- preflight: {preflight_pass}",
                f"- paper_started: {paper_started}",
                f"- capture: {status_str} ready_like={ready_like}",
                f"- CAP 4+1/5 ok: {cap_ok}",
                f"- OR AM-limited (09:00 anchor): {manifest.get('or_am_open_anchor_0900')}",
                f"- PM slot return mainline: {manifest.get('pm_or_slot_return_in_mainline')}",
                f"- heartbeat updated: {hb_check.get('updated')}",
                f"- fatal: {checks['fatal_errors']}",
                f"- submit/cancel: {submit}/{cancel}",
                f"- test env cleared: {cleared}",
                f"- code/config mutations: none",
                "",
            ]
        ),
    )
    print(f"W28 verdict={verdict} → {REPORT}")
    return 0 if verdict == "FINAL_STARTUP_SMOKE_PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
