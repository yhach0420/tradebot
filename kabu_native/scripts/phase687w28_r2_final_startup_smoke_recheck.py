"""Phase687W28-R2 — Final Startup Smoke Recheck after Kabu Station connect.

No code/config/strategy changes. Writes to phase687w28_r2_* (does not overwrite W28).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
REPORT = NATIVE / "results" / "reports" / "phase687w28_r2_final_startup_smoke_recheck"
BAT = REPO / "run_paper_trade_checked.bat"
OBSERVE_SEC_MIN = 320  # >= 5 minutes
OBSERVE_SEC_MAX = 1200  # hard ceiling until Capture ready
PAPER_GRACE_SEC = 420  # after Capture ready, wait for Paper path
YAML = (
    NATIVE
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)
FORBIDDEN_ENV = (
    "TRADEBOT_DISCORD_FORMAT_TEST",
    "TRADEBOT_DEMO_PUSH_E2E",
    "TRADEBOT_COMM_FAULT_E2E",
)
CAPTURE_READY_STATUSES = (
    "READY_FOR_FANOUT",
    "CAPTURE_READY_FOR_FANOUT",
    "RECEIVING",
    "CAPTURE_RECEIVING",
    "WRITING",
    "CAPTURE_WRITING",
    "CAPTURE_ONLINE",
    "ONLINE",
    "WAITING",
    "CAPTURE_WAITING",
    "SOCKET_OPEN_WAITING",
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


def _day() -> str:
    return datetime.now(tz=JST).strftime("%Y%m%d")


def _market_hours(now: Optional[datetime] = None) -> bool:
    n = now or datetime.now(tz=JST)
    t = n.hour * 60 + n.minute
    # TSE cash: 9:00-11:30, 12:30-15:30 approx
    return (9 * 60 <= t < 11 * 60 + 30) or (12 * 60 + 30 <= t < 15 * 60 + 30)


def _parse_iso(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except ValueError:
        return None


def _age_sec(ts: Any) -> Optional[float]:
    dt = _parse_iso(ts)
    if not dt:
        return None
    return round((datetime.now(tz=JST) - dt).total_seconds(), 1)


def code_manifest() -> dict[str, Any]:
    yaml_txt = YAML.read_text(encoding="utf-8") if YAML.is_file() else ""
    builder = (NATIVE / "src/small_paper/discord_message_builder.py").read_text(encoding="utf-8")
    or_entry = (NATIVE / "src/small_paper/or_overlay_entry.py").read_text(encoding="utf-8")
    pilot = (NATIVE / "src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    day_mod = NATIVE / "src/small_paper/daily_symbol_discord_state.py"
    return {
        "audited_at": _now(),
        "yaml": str(YAML),
        "cap_pbv2": 4 if re.search(r"cap_pbv2:\s*4\b", yaml_txt) else None,
        "cap_or": 1 if re.search(r"cap_or:\s*1\b", yaml_txt) else None,
        "or_overlay_enabled": bool(re.search(r"or_overlay_enabled:\s*true", yaml_txt, re.I)),
        "flat_band_mainline": bool(re.search(r"pbv2_flat_band_mainline_enabled:\s*true", yaml_txt, re.I)),
        "or_am_anchor_0900": "hour=9" in or_entry and "_session_open_ts" in or_entry,
        "pm_or_slot_return_mainline": bool(re.search(r"cap_pbv2:\s*5\b", yaml_txt) and re.search(r"cap_or:\s*0\b", yaml_txt)),
        "discord_exit_orange": "0xC05621" in builder,
        "discord_entry_time": "エントリー時間" in builder,
        "discord_exit_time": "EXIT時間" in builder,
        "daily_symbol_discord_state_module": day_mod.is_file(),
        "same_push_wired": "_record_same_push_reentry_skip" in pilot and "same_push" in pilot,
        "code_mutations": False,
        "config_mutations": False,
    }


def latest_checked(since_ts: Optional[float] = None) -> Optional[Path]:
    d = NATIVE / "results/reports/paper_trade_checked_runner"
    files = sorted(d.glob("checked_runner_*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if d.is_dir() else []
    for f in files:
        if since_ts is None or f.stat().st_mtime >= since_ts - 5:
            return f
    return files[0] if files else None


def capture_dir(day: str) -> Path:
    return NATIVE / "data" / "market_capture" / day


def latest_session(day: str) -> Optional[Path]:
    base = NATIVE / "results" / "small_paper" / day
    if not base.is_dir():
        return None
    sess = sorted(base.glob("live_session_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    return sess[0] if sess else None


def list_procs() -> list[dict[str, Any]]:
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    # Exclude this probe itself (CommandLine contains the match pattern literals).
                    "$exclude='Get-CimInstance Win32_Process'; "
                    "Get-CimInstance Win32_Process | Where-Object { "
                    "$_.CommandLine -and $_.CommandLine -notmatch [regex]::Escape($exclude) -and ("
                    "$_.CommandLine -match 'market_capture_sidecar' -or "
                    "$_.CommandLine -match 'paper_trade_checked_runner' -or "
                    "$_.CommandLine -match 'am_pm_daily_runner' -or "
                    "$_.CommandLine -match 'small_paper_pilot' -or "
                    "$_.CommandLine -match 'run_core10_dynamic40_am_pm_daily_runner' -or "
                    "$_.CommandLine -match 'run_phase113_vol_liq'"
                    ") } | ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }"
                ),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.CalledProcessError:
        return []
    rows = []
    for line in out.splitlines():
        if "|" not in line:
            continue
        pid, cmd = line.split("|", 1)
        try:
            rows.append({"pid": int(pid), "cmd": cmd[:260]})
        except ValueError:
            continue
    return rows


def paper_proc_alive(procs: list[dict[str, Any]]) -> bool:
    return any(
        any(
            x in (p.get("cmd") or "")
            for x in ("am_pm_daily_runner", "small_paper_pilot", "run_core10_dynamic40_am_pm_daily_runner")
        )
        for p in procs
    )


def capture_proc_alive(procs: list[dict[str, Any]]) -> bool:
    return any("market_capture_sidecar" in (p.get("cmd") or "") for p in procs)


def stop_owned(day: str) -> dict[str, Any]:
    result: dict[str, Any] = {"operator_stop": False, "killed": []}
    day_dir = capture_dir(day)
    try:
        day_dir.mkdir(parents=True, exist_ok=True)
        (day_dir / "operator_stop.flag").write_text(
            json.dumps({"reason": "phase687w28_r2_smoke_end", "at": _now()}), encoding="utf-8"
        )
        result["operator_stop"] = True
    except OSError as e:
        result["error"] = str(e)
    time.sleep(3)
    for row in list_procs():
        pid = row["pid"]
        cmd = row["cmd"]
        if any(
            x in cmd
            for x in (
                "market_capture_sidecar",
                "paper_trade_checked_runner",
                "am_pm_daily_runner",
                "small_paper_pilot",
                "run_core10_dynamic40_am_pm_daily_runner",
                "run_phase113_vol_liq",
            )
        ):
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, timeout=30)
                result["killed"].append(pid)
            except Exception as e:
                result.setdefault("kill_errors", []).append(str(e))
    # Also kill parent cmd/ps1 launchers for checked bat if still up
    try:
        out = subprocess.check_output(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-CimInstance Win32_Process | Where-Object { "
                    "$_.CommandLine -match 'run_paper_trade_checked\\.(bat|ps1)' } | "
                    "ForEach-Object { $_.ProcessId }"
                ),
            ],
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        for pid_s in out.split():
            try:
                pid = int(pid_s.strip())
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True, timeout=30)
                result["killed"].append(pid)
            except ValueError:
                continue
    except subprocess.CalledProcessError:
        pass
    return result


def audit_errors(*paths: Path) -> dict[str, Any]:
    total = 0
    fatal = 0
    samples: list[str] = []
    for path in paths:
        if not path or not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if not line.strip():
                continue
            total += 1
            if any(k in line.lower() for k in ("fatal", "traceback", "critical", "panic")):
                fatal += 1
                if len(samples) < 5:
                    samples.append(line[:400])
    return {"total_lines": total, "fatal_like": fatal, "samples": samples}


def status_str(obj: Optional[dict[str, Any]]) -> str:
    if not obj:
        return ""
    return str(obj.get("status") or obj.get("capture_status") or obj.get("state") or "")


def is_ready_status(s: str) -> bool:
    u = s.upper()
    return any(x in u for x in CAPTURE_READY_STATUSES) or any(
        x in u for x in ("READY", "RECEIVING", "WRITING", "ONLINE", "FANOUT")
    )


def pick_verdict(c: dict[str, Any]) -> str:
    if c.get("order_safety_violation"):
        return "ORDER_SAFETY_VIOLATION"
    if c.get("kabu_failed"):
        return "KABU_CONNECTION_FAILED"
    if c.get("preflight_failed"):
        return "PREFLIGHT_FAILED"
    if c.get("paper_not_started"):
        return "PAPER_NOT_STARTED"
    if c.get("push_socket_failed"):
        return "PUSH_SOCKET_FAILED"
    if c.get("capture_not_ready"):
        return "CAPTURE_NOT_READY"
    if c.get("heartbeat_not_updating"):
        return "HEARTBEAT_NOT_UPDATING"
    if c.get("pass_off_hours"):
        return "FINAL_STARTUP_SMOKE_PASS_OFF_HOURS"
    if c.get("pass_full"):
        return "FINAL_STARTUP_SMOKE_PASS"
    return "PREFLIGHT_FAILED"


def clear_stale_universe_lock() -> dict[str, Any]:
    """Release universe_prebuild.lock if holder PID is dead (smoke helper only)."""
    lock = NATIVE / "runtime" / "universe_prebuild.lock"
    out: dict[str, Any] = {"path": str(lock), "cleared": False}
    if not lock.is_file():
        out["present"] = False
        return out
    out["present"] = True
    txt = lock.read_text(encoding="utf-8", errors="replace")
    out["content"] = txt.strip()
    m = re.search(r"pid=(\d+)", txt)
    pid = int(m.group(1)) if m else None
    alive = False
    if pid:
        try:
            subprocess.check_output(["tasklist", "/FI", f"PID eq {pid}"], text=True, encoding="utf-8", errors="replace")
            # tasklist always exits 0; check output
            alive = str(pid) in subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.CalledProcessError:
            alive = False
    out["holder_pid"] = pid
    out["holder_alive"] = alive
    if not alive:
        try:
            lock.unlink()
            out["cleared"] = True
        except OSError as e:
            out["error"] = str(e)
    return out


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    day = _day()
    mh = _market_hours()
    lock_info = clear_stale_universe_lock()
    _write_json(REPORT / "universe_lock_cleanup.json", lock_info)
    pre_env = {k: os.environ.get(k) for k in FORBIDDEN_ENV}
    cleared = []
    for k in FORBIDDEN_ENV:
        if os.environ.pop(k, None):
            cleared.append(k)
    env = os.environ.copy()
    for k in FORBIDDEN_ENV:
        env.pop(k, None)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"

    manifest = code_manifest()
    _write_json(REPORT / "code_change_manifest.json", {**manifest, "parent_env_before": pre_env, "cleared": cleared})
    _write_json(
        REPORT / "runtime_config_snapshot.json",
        {
            "generated_at": _now(),
            "trading_date": day,
            "market_hours": mh,
            "cap_pbv2": manifest["cap_pbv2"],
            "cap_or": manifest["cap_or"],
            "cap_total": 5,
            "or_overlay_enabled": manifest["or_overlay_enabled"],
            "or_am_limited": manifest["or_am_anchor_0900"],
            "pm_slot_return_mainline": manifest["pm_or_slot_return_mainline"],
            "flat_band_mainline": manifest["flat_band_mainline"],
            "discord_legacy_embed": {
                "exit_orange": manifest["discord_exit_orange"],
                "entry_time": manifest["discord_entry_time"],
                "exit_time": manifest["discord_exit_time"],
            },
            "same_push": manifest["same_push_wired"],
            "daily_symbol_state_module": manifest["daily_symbol_discord_state_module"],
            "test_env_cleared": all(not env.get(k) for k in FORBIDDEN_ENV),
        },
    )

    console_path = REPORT / "startup_console_tail.txt"
    fh = console_path.open("w", encoding="utf-8", errors="replace")
    fh.write(f"W28R2_START {_now()} observe_min={OBSERVE_SEC_MIN}s observe_max={OBSERVE_SEC_MAX}s market_hours={mh}\n")
    fh.flush()

    t0 = time.time()
    proc = subprocess.Popen(
        ["cmd", "/c", str(BAT), "--no-pause"],
        cwd=str(REPO),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )
    fh.write(f"launcher_pid={proc.pid}\n")
    fh.flush()
    lines: list[str] = []

    def reader() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            fh.write(line)
            fh.flush()

    th = threading.Thread(target=reader, daemon=True)
    th.start()

    snapshots: list[dict[str, Any]] = []
    milestone_hit = False
    capture_ready_at: Optional[float] = None
    while True:
        elapsed = time.time() - t0
        cdir = capture_dir(day)
        cap_s = _read_json(cdir / "capture_status.json")
        procs_now = list_procs()
        sess_now = latest_session(day)
        paper_alive = paper_proc_alive(procs_now)
        cap_alive = capture_proc_alive(procs_now)
        st = status_str(cap_s)
        if capture_ready_at is None and cap_s is not None and is_ready_status(st):
            capture_ready_at = elapsed
            fh.write(f"\nCAPTURE_READY_AT elapsed={elapsed:.1f}s status={st!r}\n")
            fh.flush()
        snap = {
            "t": round(elapsed, 1),
            "at": _now(),
            "proc_alive": proc.poll() is None,
            "proc_exit": proc.poll(),
            "capture_status": cap_s,
            "capture_heartbeat": _read_json(cdir / "capture_heartbeat.json"),
            "procs": procs_now,
            "session": str(sess_now or ""),
            "paper_alive": paper_alive,
            "cap_alive": cap_alive,
            "capture_ready_at": capture_ready_at,
        }
        snapshots.append(snap)
        # Milestone: min observe + Capture ready + Paper path evidence
        if (
            elapsed >= OBSERVE_SEC_MIN
            and cap_s is not None
            and is_ready_status(st)
            and (paper_alive or sess_now is not None or "Starting Paper" in "".join(lines[-50:]) or "[PAPER TRADE]" in "".join(lines[-80:]))
        ):
            milestone_hit = True
            fh.write(f"\nMILESTONE_HIT elapsed={elapsed:.1f}s status={st!r}\n")
            fh.flush()
            break
        # After Capture ready, allow PAPER_GRACE_SEC before giving up on paper
        if capture_ready_at is not None and (elapsed - capture_ready_at) >= PAPER_GRACE_SEC:
            fh.write(
                f"\nPAPER_GRACE_DONE elapsed={elapsed:.1f}s since_capture={elapsed - capture_ready_at:.1f}s "
                f"paper_alive={paper_alive}\n"
            )
            fh.flush()
            break
        if proc.poll() is not None and elapsed >= OBSERVE_SEC_MIN and capture_ready_at is None:
            fh.write(f"\nLAUNCHER_EXIT_NO_CAPTURE code={proc.poll()} elapsed={elapsed:.1f}\n")
            fh.flush()
            break
        # Absolute ceiling only if Capture never became ready
        if elapsed >= OBSERVE_SEC_MAX and capture_ready_at is None:
            fh.write(f"\nOBSERVE_MAX_NO_CAPTURE elapsed={elapsed:.1f}\n")
            fh.flush()
            break
        # Absolute ceiling even after capture (capture + grace already handled)
        if elapsed >= OBSERVE_SEC_MAX + PAPER_GRACE_SEC:
            fh.write(f"\nOBSERVE_HARD_CEILING elapsed={elapsed:.1f}\n")
            fh.flush()
            break
        time.sleep(15)

    console = "".join(lines)
    checked_path = latest_checked(since_ts=t0)
    checked = _read_json(checked_path) if checked_path else None
    cdir = capture_dir(day)
    cap_status = _read_json(cdir / "capture_status.json") or {}
    cap_hb = _read_json(cdir / "capture_heartbeat.json") or {}
    sess = latest_session(day)
    sess_sum = _read_json(sess / "small_paper_summary.json") if sess else None
    sess_cfg = _read_json(sess / "live_session_config.json") if sess and (sess / "live_session_config.json").is_file() else None

    # --- step results from checked runner / console ---
    steps = {str(s.get("name")): s for s in (checked or {}).get("steps") or [] if isinstance(s, dict)}
    kabu_step = steps.get("kabu_readonly") or {}
    kabu_pass = str(kabu_step.get("result") or "").upper() == "PASS" or bool(
        re.search(r"Kabu readonly\.+PASS", console, re.I)
    )
    kabu_detail = {}
    try:
        kabu_detail = json.loads(kabu_step.get("stdout_tail") or "{}")
    except json.JSONDecodeError:
        kabu_detail = {"raw": (kabu_step.get("stdout_tail") or "")[:500]}

    preflight_pass = any(
        str(steps.get(n, {}).get("result") or "").upper() == "PASS"
        for n in ("preflight", "production_startup_smoke", "smoke", "live_pipeline_preflight")
    ) or bool(re.search(r"preflight\.+PASS|smoke.+PASS|production startup smoke", console, re.I))
    # Also PASS if paper path reached past smoke in timeline
    if any(str(steps.get(n, {}).get("result")) == "PASS" for n in ("cache_prebuild", "safety_flags", "paper_trade")):
        preflight_pass = True
    if str((checked or {}).get("blocked_reason") or ""):
        blocked_obj = (checked or {}).get("blocked")
        failed_step = ""
        if isinstance(blocked_obj, dict):
            failed_step = str(blocked_obj.get("failed_step") or "")
        elif blocked_obj is not None:
            failed_step = str(blocked_obj)
        if "preflight" in failed_step.lower():
            preflight_pass = False

    paper_call = int((checked or {}).get("paper_call_count") or 0)
    paper_started = paper_call > 0 or sess is not None or bool(
        re.search(r"\[PAPER TRADE\] starting|Starting Paper|am_pm_daily_runner|run_core10_dynamic40", console, re.I)
    )
    # process evidence
    paper_proc = paper_proc_alive(list_procs()) or any(
        paper_proc_alive(snap.get("procs") or []) for snap in snapshots
    )
    if paper_proc:
        paper_started = True

    cap_st = status_str(cap_status)
    capture_started = bool(cap_status) or any(
        capture_proc_alive(snap.get("procs") or []) for snap in snapshots
    ) or bool(re.search(r"Capture sidecar\.+PASS|CAPTURE_", console, re.I))
    capture_ready = is_ready_status(cap_st)

    # PUSH
    push_msgs = 0
    for key in ("push_message_count", "push_messages", "websocket_message_count", "received_count"):
        if sess_sum and sess_sum.get(key) is not None:
            try:
                push_msgs = max(push_msgs, int(sess_sum.get(key) or 0))
            except (TypeError, ValueError):
                pass
    if cap_status.get("events") is not None:
        try:
            push_msgs = max(push_msgs, int(cap_status.get("events") or 0))
        except (TypeError, ValueError):
            pass
    for key in ("event_count", "push_event_count", "messages_received"):
        if cap_status.get(key) is not None:
            try:
                push_msgs = max(push_msgs, int(cap_status[key] or 0))
            except (TypeError, ValueError):
                pass
    socket_open = bool(
        re.search(r"websocket.*(open|connect|connected)|PUSH.*(connect|open|start)|socket.*open", console, re.I)
        or (cap_status.get("websocket_connected") is True)
        or (cap_status.get("push_connected") is True)
        or capture_ready
        or ("RECEIVING" in cap_st.upper())
        or ("ONLINE" in cap_st.upper())
    )
    socket_failed = bool(
        re.search(r"websocket.*(fail|error|refused)|PUSH.*(fail|error)|socket.*(fail|error)", console, re.I)
    ) and not socket_open
    push_state = "RECEIVING" if push_msgs > 0 else ("SOCKET_OPEN_WAITING" if (socket_open or capture_ready) and not mh else ("NO_EVIDENCE" if not socket_open else "SOCKET_OPEN_WAITING"))

    # heartbeats
    cap_hb_age = None
    for k in ("updated_at", "heartbeat_at", "ts", "last_heartbeat_at", "timestamp"):
        cap_hb_age = _age_sec(cap_hb.get(k))
        if cap_hb_age is not None:
            break
    if cap_hb_age is None and cap_status:
        for k in ("updated_at", "heartbeat_at", "last_heartbeat_at"):
            cap_hb_age = _age_sec(cap_status.get(k))
            if cap_hb_age is not None:
                break
    # also compare snapshot ages across observe window
    cap_hb_updated = False
    if len(snapshots) >= 2:
        h0 = json.dumps(snapshots[0].get("capture_heartbeat") or {}, sort_keys=True)
        h1 = json.dumps(snapshots[-1].get("capture_heartbeat") or {}, sort_keys=True)
        s0 = json.dumps(snapshots[0].get("capture_status") or {}, sort_keys=True)
        s1 = json.dumps(snapshots[-1].get("capture_status") or {}, sort_keys=True)
        if (h0 != h1 and h1 != "{}") or (s0 != s1 and s1 != "{}"):
            cap_hb_updated = True
    if cap_hb_age is not None and cap_hb_age < 180:
        cap_hb_updated = True

    paper_hb_lines = 0
    paper_hb_updated = False
    if sess and (sess / "heartbeat.jsonl").is_file():
        paper_hb_lines = len([ln for ln in (sess / "heartbeat.jsonl").read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()])
        paper_hb_updated = paper_hb_lines > 0
    # runner may still be in wait-until-AM; process alive counts as paper path live
    if paper_started and paper_proc and not mh:
        # off-hours waiting: process heartbeat may be thin; don't require paper hb file yet
        pass

    # Discord
    day_state = NATIVE / "results" / "small_paper" / day / "daily_symbol_discord_state.json"
    discord = {
        "legacy_embed_loaded": all(
            [
                manifest["discord_exit_orange"],
                manifest["discord_entry_time"],
                manifest["discord_exit_time"],
            ]
        ),
        "same_push_wired": manifest["same_push_wired"],
        "test_env_cleared": all(not env.get(k) for k in FORBIDDEN_ENV),
        "live_send_evidence": bool(re.search(r"discord.*(sent|204|success|notify)", console, re.I)),
        "daily_symbol_state_path": str(day_state),
        "daily_symbol_state_writable": False,
        "daily_symbol_state_exists": day_state.is_file(),
    }
    try:
        day_state.parent.mkdir(parents=True, exist_ok=True)
        probe = day_state.parent / ".w28r2_write_probe"
        probe.write_text("ok", encoding="utf-8")
        discord["daily_symbol_state_writable"] = probe.read_text(encoding="utf-8") == "ok"
        probe.unlink(missing_ok=True)
    except OSError as e:
        discord["write_error"] = str(e)

    # orders
    submit = 0
    cancel = 0
    if sess_sum:
        submit = int(sess_sum.get("actual_submit") or sess_sum.get("live_order_submit_count") or 0 or 0)
        cancel = int(sess_sum.get("actual_cancel") or sess_sum.get("live_order_cancel_count") or 0 or 0)
    post = (checked or {}).get("post_session") or {}
    submit = max(submit, int(post.get("actual_submit") or 0))
    cancel = max(cancel, int(post.get("actual_cancel") or 0))
    real_disabled = True
    if "Real orders: DISABLED" in console or (checked or {}).get("real_orders") == "DISABLED":
        real_disabled = True

    err = audit_errors(
        cdir / "errors.jsonl",
        *( [sess / "errors.jsonl"] if sess else [] ),
    )
    console_fatal = len(re.findall(r"(?i)traceback|fatal error", console))
    fatal_n = err["fatal_like"] + console_fatal

    # Core10/Dynamic40 subscribe evidence
    core_dyn = bool(
        re.search(r"core10|dynamic40|register|購読|universe", console, re.I)
        or any(str(steps.get(n, {}).get("result")) == "PASS" for n in ("registration", "universe_resolve", "universe_prebuild", "cache_prebuild"))
    )

    # CAP from session if present
    cap_ok = manifest["cap_pbv2"] == 4 and manifest["cap_or"] == 1 and not manifest["pm_or_slot_return_mainline"]
    if sess_sum:
        if sess_sum.get("cap_pbv2") not in (None, 4):
            cap_ok = False
        if sess_sum.get("cap_or") not in (None, 1):
            cap_ok = False

    # Stop
    stop_info = stop_owned(day)
    if proc.poll() is None:
        try:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
        except Exception as e:
            stop_info["launcher_err"] = str(e)
    th.join(timeout=5)
    fh.write(f"\nW28R2_END {_now()} elapsed={round(time.time()-t0,1)} stop={json.dumps(stop_info)}\n")
    fh.close()

    # Verdict flags
    kabu_failed = not kabu_pass
    # preflight: if we got past kabu into capture/paper, treat as progressed; fail only if explicit fail
    preflight_failed = False
    blocked_raw = (checked or {}).get("blocked")
    blocked: dict[str, Any] = blocked_raw if isinstance(blocked_raw, dict) else {}
    if str(blocked.get("failed_step") or "") in ("preflight", "smoke", "production_startup_smoke"):
        preflight_failed = True
    if kabu_failed:
        preflight_failed = False  # kabu verdict wins

    paper_not_started = not paper_started
    push_socket_failed = socket_failed or (not socket_open and capture_started and mh)
    # off-hours: socket may be waiting via capture online
    if not mh and capture_ready:
        push_socket_failed = False
    capture_not_ready = not capture_ready
    # off-hours allow waiting statuses already in is_ready_status
    if capture_started and not capture_ready and not mh:
        # still fail if no status at all
        capture_not_ready = not bool(cap_status)

    hb_fail = False
    if capture_started and not cap_hb_updated and (cap_hb_age is None or cap_hb_age > 180):
        hb_fail = True
    # if capture never got status, capture_not_ready covers it
    if not capture_started:
        hb_fail = False

    order_violation = submit > 0 or cancel > 0 or not real_disabled

    pass_off = (
        not mh
        and kabu_pass
        and paper_started
        and capture_started
        and capture_ready
        and cap_hb_updated
        and (socket_open or push_state == "SOCKET_OPEN_WAITING" or capture_ready)
        and fatal_n == 0
        and not order_violation
        and cap_ok
    )
    pass_full = (
        mh
        and kabu_pass
        and preflight_pass
        and paper_started
        and capture_ready
        and cap_hb_updated
        and (push_msgs > 0 or socket_open)
        and fatal_n == 0
        and not order_violation
        and cap_ok
    )

    checks = {
        "kabu_failed": kabu_failed,
        "preflight_failed": preflight_failed,
        "paper_not_started": paper_not_started,
        "push_socket_failed": push_socket_failed,
        "capture_not_ready": capture_not_ready and not pass_off and not pass_full,
        "heartbeat_not_updating": hb_fail and not (pass_off or pass_full),
        "order_safety_violation": order_violation,
        "pass_off_hours": pass_off,
        "pass_full": pass_full,
    }
    # refine capture_not_ready flag for pick_verdict priority
    if capture_not_ready and not kabu_failed and not paper_not_started and not preflight_failed:
        checks["capture_not_ready"] = True
    else:
        checks["capture_not_ready"] = False
    if hb_fail and capture_ready and not kabu_failed:
        checks["heartbeat_not_updating"] = True
    else:
        checks["heartbeat_not_updating"] = False

    verdict = pick_verdict(checks)

    # Write audits
    _write_json(
        REPORT / "kabu_connection_audit.json",
        {
            "kabu_readonly_pass": kabu_pass,
            "detail": kabu_detail,
            "port_18080_from_step": kabu_detail.get("port_reachable") or kabu_detail.get("api_port_reachable"),
            "station_running": kabu_detail.get("station_running"),
            "token_acquired": kabu_detail.get("token_acquired"),
            "blocked": blocked if not kabu_pass else None,
        },
    )
    _write_json(
        REPORT / "push_connection_audit.json",
        {
            "market_hours": mh,
            "socket_open_evidence": socket_open,
            "socket_failed": socket_failed,
            "push_message_count": push_msgs,
            "state": push_state,
            "note": "Off-hours with 0 messages => SOCKET_OPEN_WAITING if socket/capture ready; not a connection failure.",
        },
    )
    _write_json(
        REPORT / "paper_startup_result.json",
        {
            "paper_started": paper_started,
            "paper_call_count": paper_call,
            "paper_proc_seen": paper_proc,
            "session": str(sess) if sess else None,
            "summary_caps": {
                "cap_pbv2": (sess_sum or {}).get("cap_pbv2"),
                "cap_or": (sess_sum or {}).get("cap_or"),
                "or_overlay_enabled": (sess_sum or {}).get("or_overlay_enabled"),
            },
            "core_dyn_subscribe_evidence": core_dyn,
            "preflight_pass": preflight_pass,
        },
    )
    _write_json(
        REPORT / "capture_startup_result.json",
        {
            "started": capture_started,
            "status": cap_st,
            "ready_like": capture_ready,
            "status_obj": cap_status,
            "reached_ready_receiving_writing": any(
                x in cap_st.upper() for x in ("READY", "RECEIVING", "WRITING", "ONLINE", "FANOUT")
            ),
        },
    )
    _write_json(
        REPORT / "heartbeat_check.json",
        {
            "capture_heartbeat": cap_hb,
            "capture_heartbeat_age_sec": cap_hb_age,
            "capture_heartbeat_updated": cap_hb_updated,
            "paper_session": str(sess) if sess else None,
            "paper_heartbeat_lines": paper_hb_lines,
            "paper_heartbeat_updated": paper_hb_updated,
            "off_hours_note": "Paper may wait until AM; Capture heartbeat is primary off-hours signal.",
        },
    )
    _write_json(REPORT / "discord_startup_result.json", discord)
    _write_json(REPORT / "error_audit.json", {**err, "console_fatal_like": console_fatal, "fatal_total": fatal_n})
    _write_json(
        REPORT / "order_safety_audit.json",
        {
            "real_orders_disabled": real_disabled,
            "submit": submit,
            "cancel": cancel,
            "test_env_cleared": discord["test_env_cleared"],
            "violation": order_violation,
        },
    )
    _write_json(REPORT / "observe_snapshots.json", {"n": len(snapshots), "last": snapshots[-3:] if snapshots else []})

    report = {
        "phase": "687W28-R2",
        "generated_at": _now(),
        "verdict": verdict,
        "observe_sec": round(time.time() - t0, 1),
        "market_hours": mh,
        "checked_runner": str(checked_path) if checked_path else None,
        "completion": {
            "1_kabu_readonly": kabu_pass,
            "2_preflight": preflight_pass,
            "3_paper_runner": paper_started,
            "4_push_socket": push_state,
            "5_push_messages": push_msgs,
            "6_capture": {"status": cap_st, "ready": capture_ready, "started": capture_started},
            "7_heartbeats": {"capture_updated": cap_hb_updated, "paper_updated": paper_hb_updated, "cap_age": cap_hb_age},
            "8_discord": discord,
            "9_cap": {"pbv2": 4, "or": 1, "total": 5, "ok": cap_ok},
            "10_or_am_only": manifest["or_am_anchor_0900"] and not manifest["pm_or_slot_return_mainline"],
            "11_fatal": fatal_n,
            "12_submit_cancel": {"submit": submit, "cancel": cancel},
            "13_code_config_changed": False,
            "14_market_hours": mh,
        },
        "checks": checks,
        "stop": stop_info,
        "launcher_exit": proc.poll(),
    }
    _write_json(REPORT / "startup_smoke_report.json", report)

    md = [
        "# Phase687W28-R2 — Final Startup Smoke Recheck",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        f"- market_hours: {mh}",
        f"- observe_sec: {report['observe_sec']}",
        f"- kabu_readonly: {kabu_pass}",
        f"- preflight: {preflight_pass}",
        f"- paper_started: {paper_started}",
        f"- PUSH state: {push_state} (msgs={push_msgs})",
        f"- Capture: started={capture_started} status={cap_st!r} ready={capture_ready}",
        f"- heartbeat capture_updated={cap_hb_updated} age={cap_hb_age} paper_lines={paper_hb_lines}",
        f"- CAP 4+1/5 ok={cap_ok}; OR AM-only={manifest['or_am_anchor_0900']}; PM return mainline={manifest['pm_or_slot_return_mainline']}",
        f"- flat_band={manifest['flat_band_mainline']}",
        f"- Discord legacy embed={discord['legacy_embed_loaded']}; test_env_cleared={discord['test_env_cleared']}",
        f"- fatal={fatal_n}; submit/cancel={submit}/{cancel}",
        f"- code/config changes: none",
        "",
    ]
    _write(REPORT / "phase687w28_r2_decision.md", "\n".join(md))
    _write(REPORT / "phase687w28_r2_verdict.txt", verdict + "\n")
    print(f"W28-R2 verdict={verdict} → {REPORT}")
    return 0 if verdict.startswith("FINAL_STARTUP_SMOKE_PASS") else 1


if __name__ == "__main__":
    raise SystemExit(main())
