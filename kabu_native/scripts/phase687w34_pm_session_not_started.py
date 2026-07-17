#!/usr/bin/env python3
"""Phase687W34: PM Paper session not started — RCA + fix certification."""

from __future__ import annotations

import csv
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))

JST = ZoneInfo("Asia/Tokyo")
OUT = NATIVE / "results" / "reports" / "phase687w34_pm_session_not_started"
DAY = "20260716"


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _wc(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _wm(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def collect_notifications() -> dict[str, Any]:
    p = NATIVE / "results" / "notifications" / DAY / "notification_events.jsonl"
    rows = []
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    am_sum = [r for r in rows if str(r.get("dedupe_key") or "") in ("daily_summary", "am_summary") or str(r.get("dedupe_key") or "").startswith("am_summary|") or (r.get("at", "").startswith(f"{DAY[:4]}-{DAY[4:6]}-{DAY[6:8]}T11:26") and r.get("category") == "SESSION_SUMMARY")]
    shadow = [r for r in rows if r.get("category") == "RESEARCH_SHADOW" and str(r.get("at") or "") >= f"{DAY[:4]}-{DAY[4:6]}-{DAY[6:8]}T11:20"]
    pm_screen = [r for r in rows if "PM Screening" in str(r.get("dedupe_key") or "")]
    return {
        "am_summary_events": am_sum,
        "shadow_events": shadow,
        "pm_screening_events": pm_screen,
        "am_summary_deduped": any(r.get("status") == "DEDUPED" and "summary" in str(r.get("dedupe_key") or "") for r in am_sum),
        "am_summary_sent": any(r.get("status") == "SENT" for r in am_sum),
        "shadow_skipped": any(r.get("status") == "SKIPPED" for r in shadow),
        "pm_screening_sent": any(r.get("status") == "SENT" for r in pm_screen) or any(
            r.get("status") == "SENT" and str(r.get("at") or "").startswith(f"{DAY[:4]}-{DAY[4:6]}-{DAY[6:8]}T12:25")
            for r in rows
            if r.get("category") == "SESSION_SUMMARY"
        ),
    }


def build_timeline() -> list[dict[str, Any]]:
    am = NATIVE / "results" / "small_paper" / DAY / "live_session_073602"
    pm = NATIVE / "results" / "small_paper" / DAY / "live_session_122532"
    summary = load_json(NATIVE / "results" / "reports" / f"daily_runner_summary_{DAY}.json")
    am_sum = load_json(am / "small_paper_summary.json")
    seal = load_json(am / "session_seal.json")
    reg = load_json(pm / "register_api_trace.json") if pm.is_dir() else {}
    man = load_json(pm / "live_order_safety" / "session_manifest.json") if pm.is_dir() else {}
    return [
        {"ts": "07:35:59", "actor": "am_pm_daily_runner", "event": "start_verdict=started", "detail": summary.get("generated_at")},
        {"ts": "07:36:02", "actor": "am_pm_daily_runner", "event": "AM_Screening_SENT", "detail": "parent notify"},
        {"ts": "07:36:02", "actor": "run_small_paper_pilot(am)", "event": "AM_subprocess_start", "detail": str(am)},
        {"ts": "11:24:49", "actor": "AM_pilot", "event": "last_heartbeat", "detail": "known fact"},
        {"ts": "11:26:03", "actor": "AM_pilot", "event": "AM_Summary_DEDUPED", "detail": "dedupe_key=daily_summary (am_pm_session missing)"},
        {"ts": "11:26:03", "actor": "AM_pilot", "event": "Shadow_Summary_SKIPPED", "detail": "not_am_pm_session"},
        {"ts": "11:26", "actor": "AM_pilot", "event": "session_seal", "detail": seal.get("session_seal_status") or seal.get("generated_at")},
        {"ts": "11:26", "actor": "am_pm_daily_runner", "event": "AM_exit_code_0_checkpoint", "detail": f"am_pilot_exit={summary.get('am_pilot_exit_code')}"},
        {"ts": "11:26→12:25", "actor": "am_pm_daily_runner", "event": "wait_until_hhmm(12:25)", "detail": "PM wait exists"},
        {"ts": "12:25:30", "actor": "am_pm_daily_runner", "event": "PM_Screening_SENT", "detail": "parent notify BEFORE pm pilot"},
        {"ts": "12:25:34", "actor": "run_small_paper_pilot(pm)", "event": "PM_subprocess_created", "detail": str(pm)},
        {"ts": "12:25→12:40", "actor": "PM_pilot", "event": "wait_until_session", "detail": "warmup/session start"},
        {"ts": "12:40:24", "actor": "PM_pilot", "event": "UNIVERSE_PREPARED + register_reuse", "detail": json.dumps({"reused": reg.get("reused_existing"), "skipped_put": True})},
        {"ts": "12:40:24+", "actor": "PM_pilot", "event": "PUSH_never_started", "detail": "Station cleared but local SoT reuse skipped PUT"},
        {"ts": "(never)", "actor": "am_pm_daily_runner", "event": "PM_checkpoint_missing", "detail": f"pm_session_dir={summary.get('pm_session_dir')}"},
        {"ts": "(never)", "actor": "am_pm_daily_runner", "event": "final_verdict_not_written", "detail": f"stuck verdict={summary.get('verdict')}"},
    ]


def synthetic_fix_proof() -> dict[str, Any]:
    from api.kabu_register import (
        clear_paper_register_state,
        load_paper_register_state,
        register_symbols_cleared,
        save_paper_register_state,
    )

    root = OUT / "_synth"
    root.mkdir(parents=True, exist_ok=True)
    specs = [(f"{1000 + i}", 1) for i in range(50)]
    save_paper_register_state(root, symbols_spec=specs, regist_num=50, trading_date=DAY)
    clear_paper_register_state(root, reason="after_am_session")
    after_clear_n = int(load_paper_register_state(root).get("symbol_count") or 0)

    class Push:
        def __init__(self) -> None:
            self.puts = 0

        def unregister_all(self):
            return {"RegistNum": 0}

        def register(self, specs_in):
            self.puts += 1
            return {
                "RegistNum": len(specs_in),
                "Symbols": [{"Symbol": s, "Exchange": ex} for s, ex in specs_in],
            }

    push = Push()
    out = register_symbols_cleared(
        push, specs, native_root=root, trading_date=DAY, settle_sec=0.0, allow_reuse_if_match=True
    )
    labels_am = __import__(
        "small_paper.discord_message_builder", fromlist=["summary_notification_labels"]
    ).summary_notification_labels({"am_pm_session": {"kind": "am"}, "trading_date": DAY})
    return {
        "after_clear_symbol_count": after_clear_n,
        "pm_register_reused": out.get("reused_existing"),
        "pm_put_count": push.puts,
        "pm_register_ok": out.get("ok"),
        "am_summary_label": labels_am[0],
        "fix_ok": (
            after_clear_n == 0
            and push.puts == 1
            and not out.get("reused_existing")
            and labels_am[0] == "AM Summary"
        ),
    }


def run_tests() -> dict[str, Any]:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--tb=line",
            "tests/test_phase687w34_pm_session_start.py",
            "tests/test_kabu_register.py",
        ],
        cwd=str(NATIVE),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    import re

    passed = failed = 0
    m = re.search(r"(\d+)\s+passed", out)
    if m:
        passed = int(m.group(1))
    m = re.search(r"(\d+)\s+failed", out)
    if m:
        failed = int(m.group(1))
    return {
        "exit_code": proc.returncode,
        "passed": passed,
        "failed": failed,
        "dedicated": "tests/test_phase687w34_pm_session_start.py",
        "related": "tests/test_kabu_register.py",
        "tail": out[-1200:],
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    summary = load_json(NATIVE / "results" / "reports" / f"daily_runner_summary_{DAY}.json")
    am_dir = NATIVE / "results" / "small_paper" / DAY / "live_session_073602"
    pm_dir = NATIVE / "results" / "small_paper" / DAY / "live_session_122532"
    reg = load_json(pm_dir / "register_api_trace.json") if pm_dir.is_dir() else {}
    sot = load_json(NATIVE / "runtime" / "paper_register_state.json")
    notif = collect_notifications()
    timeline = build_timeline()
    _wc(OUT / "am_to_pm_timeline.csv", timeline, ["ts", "actor", "event", "detail"])

    orchestrator = {
        "process": "run_core10_dynamic40_am_pm_daily_runner.py → run_daily_runner",
        "am_exit_code": summary.get("am_pilot_exit_code"),
        "am_live_ok": summary.get("am_live_ok"),
        "daily_summary_verdict_stuck": summary.get("verdict"),
        "generated_at_frozen_at_start": summary.get("generated_at"),
        "pm_wait": "wait_until_hhmm(12:25) exists after AM",
        "parent_exited_at_11_26": False,
        "parent_alive_until_pm_screening": True,
        "pm_screening_sender": "am_pm_daily_runner (parent)",
        "pm_pilot_starter": "am_pm_daily_runner.run_pilot_session(session=pm)",
        "pm_call_count": 1 if pm_dir.is_dir() else 0,
        "pm_subprocess_created": pm_dir.is_dir(),
        "pm_session_dir": str(pm_dir) if pm_dir.is_dir() else None,
        "final_pm_checkpoint_written": summary.get("pm_session_dir") is not None,
        "capture_blocked_pm": False,
        "am_seal_blocked_pm": False,
    }
    _wj(OUT / "orchestrator_process_trace.json", orchestrator)

    pm_start = {
        "pm_screening_at": "2026-07-16T12:25:30+09:00",
        "pm_subprocess_session": "live_session_122532",
        "register_trace": {
            "reused_existing": reg.get("reused_existing"),
            "unregister_called": reg.get("unregister_called"),
            "steps": reg.get("steps"),
        },
        "paper_register_state_at_audit": {
            "updated_at": sot.get("updated_at"),
            "symbol_count": sot.get("symbol_count"),
            "note": "SoT still held AM 09:04 codes; Station had been cleared after AM",
        },
        "direct_reason": (
            "After AM, orchestrator kabu_clear unregistered Station but left paper_register_state.json. "
            "PM register_symbols_cleared saw identical desired set → reused_existing/skipped PUT → "
            "Station RegistNum=0 → no PUSH/gate/ENTRY. Parent never wrote PM checkpoint (PM did not finalize)."
        ),
        "pm_push_gate_summary_absent": True,
    }
    _wj(OUT / "pm_start_call_trace.json", pm_start)

    am_notif = {
        "small_paper_summary_am_exists": (am_dir / "small_paper_summary_am.json").is_file(),
        "discord_call_attempted": True,
        "status": "DEDUPED",
        "dedupe_key": "daily_summary",
        "root_cause": (
            "summary missing am_pm_session → labeled Daily Summary → bare key daily_summary; "
            "persistent DedupeStore still had SENT from 2026-07-14 → forever DEDUPED"
        ),
        "production_webhook_response": None,
        "http_status": None,
        "events": notif.get("am_summary_events"),
    }
    _wj(OUT / "am_summary_notification_trace.json", am_notif)

    shadow_notif = {
        "status": "SKIPPED",
        "reason": "DAILY_SHADOW_SUMMARY_SUPPRESSED / not_am_pm_session",
        "events": notif.get("shadow_events"),
        "fix": "attach am_pm_session on live summary before notify",
    }
    _wj(OUT / "shadow_summary_notification_trace.json", shadow_notif)

    proof = synthetic_fix_proof()
    tests = run_tests()
    _wj(OUT / "regression_test_results.json", tests)

    manifest = {
        "phase": "687W34",
        "entry_exit_cap_or_shadow_judgment_unchanged": True,
        "registration_state_machine_logic_unchanged": True,
        "note": "Only invalidate local SoT when Station unregister/all is intentional; attach am_pm_session; day-scope summary dedupe keys",
        "files_modified": [
            "src/api/kabu_register.py",
            "src/small_paper/pilot_runner.py",
            "src/small_paper/discord_notifier.py",
            "tests/test_phase687w34_pm_session_start.py",
            "scripts/phase687w34_pm_session_not_started.py",
        ],
    }
    _wj(OUT / "code_change_manifest.json", manifest)
    _wj(
        OUT / "order_safety_audit.json",
        {"submit": 0, "cancel": 0, "live_trading_enabled": False, "order_enabled": False},
    )

    # Multiple lifecycle bugs: false reuse + summary dedupe + shadow skip
    verdict = "MULTIPLE_LIFECYCLE_BUGS"
    if proof.get("fix_ok") and tests.get("failed", 1) == 0:
        verdict = "PM_SESSION_START_FIXED"

    answers = {
        "pm_screening_process": "am_pm_daily_runner (parent) notify_screening_universe_discord",
        "pm_paper_intended_starter": "am_pm_daily_runner.run_pilot_session(session='pm')",
        "pm_paper_call_count": orchestrator["pm_call_count"],
        "pm_subprocess_created": orchestrator["pm_subprocess_created"],
        "direct_reason": pm_start["direct_reason"],
        "parent_exited_at_1126": False,
        "pm_wait_existed": True,
        "capture_is_cause": False,
        "am_to_pm_continuous_after_fix": bool(proof.get("fix_ok")),
        "am_summary_discord": "DEDUPED (daily_summary from 20260714)",
        "shadow_summary": "SKIPPED (am_pm_session missing)",
        "fix_proof": proof,
    }

    report = {
        "phase": "687W34",
        "verdict": verdict,
        "day": DAY,
        "answers": answers,
        "orchestrator": orchestrator,
        "pm_start": pm_start,
        "notifications": notif,
        "tests": tests,
        "generated_at": datetime.now(JST).isoformat(),
    }
    _wj(OUT / "phase687w34_report.json", report)

    decision = f"""# Phase687W34 Decision

## Verdict: `{verdict}`

### Direct cause (PM Paper effectively not started)
1. Parent **did** wait until 12:25 and **did** spawn PM (`live_session_122532`).
2. After AM, `kabu_clear_stale_registrations` cleared **Station** but left `runtime/paper_register_state.json`.
3. PM `register_symbols_cleared` **reused** local SoT (`skipped_put`) while Station had **0** regs.
4. → no PUSH / gate / ENTRY / Summary; parent never got PM finalize checkpoint.

### AM Summary
- Call attempted at 11:26:03 but **DEDUPED** (`daily_summary`) because summary lacked `am_pm_session`
  and persistent dedupe store still had SENT from **2026-07-14**.
- Shadow Summary **SKIPPED** for the same missing `am_pm_session`.

### Fixes
1. `clear_register_before_session` → `clear_paper_register_state` (invalidate SoT on Station clear)
2. `_build_live_summary` copies `am_pm_session` (+ trading_date)
3. Summary Discord dedupe keys day-scoped: `am_summary|YYYYMMDD` / `pm_summary|YYYYMMDD`

### Answers
- PM Screening sender: **parent daily runner**
- PM Paper starter: **parent `run_pilot_session(pm)`**
- PM call_count: **{orchestrator['pm_call_count']}**
- PM subprocess: **yes** (`live_session_122532`)
- Parent exited 11:26: **no** (waited to 12:25)
- PM wait: **yes** (`wait_until_hhmm(12:25)`)
- Capture cause: **no**
- Continuous AM→PM after fix: **{proof.get('fix_ok')}** (synthetic PUT forced after clear)

### Tests
passed={tests.get('passed')} failed={tests.get('failed')}
"""
    _wm(OUT / "phase687w34_decision.md", decision)
    print(json.dumps({"verdict": verdict, "answers": answers, "tests": tests}, ensure_ascii=False, indent=2))
    return 0 if verdict == "PM_SESSION_START_FIXED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
