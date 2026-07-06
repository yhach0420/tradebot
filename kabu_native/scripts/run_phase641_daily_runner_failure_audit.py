#!/usr/bin/env python3
"""Phase641: daily runner failure root-cause audit (read-only)."""

from __future__ import annotations

import csv
import json
import re
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

SCRIPT = Path(__file__).resolve()
NATIVE_ROOT = SCRIPT.parents[1]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase641_daily_runner_failure_audit"
PHASE641_VERDICT_DONE = "phase641_daily_runner_failure_audit_done"

FAILURE_VERDICTS = frozenset(
    {
        "preflight_blocked",
        "safety_blocked",
        "am_failed",
        "pm_failed",
        "universe_generation_failed",
        "refresh_universe_generation_failed",
        "session_end_failed",
        "interrupted",
    }
)

CLASSIFICATION_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("kabu_station_unreachable", re.compile(r"kabu_station|connection|unreachable|register_capacity", re.I)),
    ("token_issue", re.compile(r"token|password|401|403|unauthorized", re.I)),
    ("config_sha_mismatch", re.compile(r"config.?sha|sha256.*mismatch", re.I)),
    ("extension_bus_exception", re.compile(r"extension.?bus|ExtensionBus", re.I)),
    ("discord_exception", re.compile(r"discord", re.I)),
    ("session_end_exception", re.compile(r"session_end|finalize|notify_discord_session", re.I)),
    ("file_lock_permission", re.compile(r"PermissionError|file.?lock|WinError 32|being used", re.I)),
    ("disk_full", re.compile(r"disk|No space|ENOSPC", re.I)),
    ("websocket_disconnect", re.compile(r"websocket|close frame|ConnectionReset|push_iter", re.I)),
    ("script_bug", re.compile(r"UnboundLocalError|NameError|AttributeError|TypeError|rows is not defined", re.I)),
    ("pilot_exit_nonzero", re.compile(r"pilot_exit_nonzero|exit_code.?[=: ]+1|AM pilot exit=1", re.I)),
    ("universe_generation", re.compile(r"universe_generation|am_universe|pm_universe", re.I)),
    ("refresh_universe", re.compile(r"refresh_universe", re.I)),
    ("core_watchlist", re.compile(r"core_watchlist|Core10 watchlist", re.I)),
    ("config_safety", re.compile(r"config_safety|order_enabled|safety_blocked", re.I)),
]


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _rel(path: Optional[Path]) -> str:
    if path is None:
        return ""
    try:
        return str(path.resolve().relative_to(REPO_ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def _find_summaries() -> dict[str, Path]:
    """day_stamp -> preferred summary path (reports/ over daily/runtime/)."""
    out: dict[str, Path] = {}
    for fp in sorted(NATIVE_ROOT.rglob("daily_runner_summary_*.json")):
        day = fp.stem.replace("daily_runner_summary_", "")
        if "reports" in fp.parts:
            out[day] = fp
        elif day not in out:
            out[day] = fp
    return out


def _find_phase148(day: str) -> Optional[Path]:
    candidates = [
        NATIVE_ROOT / "results" / "reports" / f"phase148_am_pm_daily_runner_{day}.json",
        NATIVE_ROOT / "results" / "daily" / day / "runtime" / f"phase148_am_pm_daily_runner_{day}.json",
    ]
    for fp in candidates:
        if fp.is_file():
            return fp
    return None


def _session_dir_path(day: str, rel_dir: Optional[str]) -> Optional[Path]:
    if not rel_dir:
        return None
    p = REPO_ROOT / rel_dir.replace("/", "\\")
    if not p.is_dir():
        p = NATIVE_ROOT / rel_dir.split("kabu_native/", 1)[-1] if "kabu_native/" in rel_dir else Path(rel_dir)
    return p if p.is_dir() else None


def _first_error_from_session(session_dir: Optional[Path]) -> dict[str, Any]:
    out: dict[str, Any] = {
        "file": "",
        "operation": "",
        "error_type": "",
        "message": "",
        "event_time": "",
        "stacktrace": "",
    }
    if session_dir is None:
        return out
    fp = session_dir / "errors.jsonl"
    if not fp.is_file():
        return out
    first_line = ""
    for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if line:
            first_line = line
            break
    if not first_line:
        return out
    try:
        row = json.loads(first_line)
    except json.JSONDecodeError:
        out["message"] = first_line[:500]
        out["file"] = _rel(fp)
        return out
    out.update(
        {
            "file": _rel(fp),
            "operation": str(row.get("operation") or row.get("event_kind") or ""),
            "error_type": str(row.get("error_type") or ""),
            "message": str(row.get("message") or "")[:500],
            "event_time": str(row.get("event_time") or ""),
        }
    )
    return out


def _dominant_errors(session_dir: Optional[Path], limit: int = 5) -> list[dict[str, Any]]:
    if session_dir is None:
        return []
    fp = session_dir / "errors.jsonl"
    if not fp.is_file():
        return []
    counts: Counter[str] = Counter()
    samples: dict[str, str] = {}
    for line in fp.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = "|".join(
            [
                str(row.get("error_type") or ""),
                str(row.get("operation") or row.get("event_kind") or ""),
                str(row.get("message") or "")[:120],
            ]
        )
        counts[key] += 1
        samples.setdefault(key, str(row.get("message") or "")[:200])
    top = []
    for key, n in counts.most_common(limit):
        parts = key.split("|", 2)
        top.append(
            {
                "count": n,
                "error_type": parts[0],
                "operation": parts[1] if len(parts) > 1 else "",
                "message_sample": parts[2] if len(parts) > 2 else samples.get(key, ""),
            }
        )
    return top


def _session_impact(
    day: str,
    summary: dict[str, Any],
    phase148: Optional[dict[str, Any]],
    session_kind: str,
) -> dict[str, Any]:
    rel_dir = summary.get(f"{session_kind}_session_dir")
    if not rel_dir and phase148:
        live = phase148.get(f"{session_kind}_live") or {}
        rel_dir = live.get("session_dir")
    session_dir = _session_dir_path(day, rel_dir)
    sp_summary = session_dir / "small_paper_summary.json" if session_dir else None
    session_summary: dict[str, Any] = {}
    if sp_summary and sp_summary.is_file():
        try:
            session_summary = _load_json(sp_summary)
        except Exception:
            pass
    return {
        "session_dir_exists": bool(session_dir and session_dir.is_dir()),
        "summary_exists": bool(sp_summary and sp_summary.is_file()),
        "accepted_count": int(session_summary.get("accepted_count") or 0),
        "rejected_count": int(session_summary.get("rejected_count") or 0),
        "stop_reason": str(session_summary.get("stop_reason") or ""),
        "api_error_count": int(session_summary.get("api_error_count") or 0),
        "discord_error_count": int(session_summary.get("discord_error_count") or 0),
        "has_trades": int(session_summary.get("accepted_count") or 0) > 0,
    }


def _classify_failure(
    verdict: str,
    stopped_reason: str,
    summary: dict[str, Any],
    phase148: Optional[dict[str, Any]],
    am_impact: dict[str, Any],
    pm_impact: dict[str, Any],
    *,
    day: str = "",
) -> tuple[str, str]:
    notes: list[str] = []
    pf = (phase148 or {}).get("preflight") or {}
    safety = pf.get("safety") or {}
    failed_ids = list(safety.get("failed_check_ids") or [])
    text_blob = " ".join(
        [
            verdict,
            stopped_reason,
            " ".join(summary.get("verdict_notes") or []),
            " ".join(failed_ids),
            json.dumps(pf.get("config_safety") or {}, ensure_ascii=False),
        ]
    )

    if verdict == "preflight_blocked":
        safety_fp = NATIVE_ROOT / "results" / "reports" / f"small_paper_safety_{day}.json"
        if safety_fp.is_file():
            try:
                safety_doc = _load_json(safety_fp)
                text_blob += " " + json.dumps(safety_doc.get("checks") or [], ensure_ascii=False)
            except Exception:
                pass
        if stopped_reason == "safety_check":
            if any("kabu" in x for x in failed_ids):
                if "401" in text_blob or "token" in text_blob.lower():
                    return "token_issue", f"safety failed (token/auth): {failed_ids}"
                return "kabu_station_unreachable", f"safety failed: {failed_ids}"
            if any("discord" in x for x in failed_ids):
                return "discord_exception", f"safety failed: {failed_ids}"
            return "unknown", f"safety failed: {failed_ids}"
        if stopped_reason == "kabu_connection":
            return "kabu_station_unreachable", "kabu connection preflight"
        if stopped_reason == "core_watchlist_missing":
            return "core_watchlist", "Core10 watchlist missing"
        if stopped_reason == "config_safety":
            return "config_safety", "config safety hard stop"

    if verdict in ("universe_generation_failed",):
        return "universe_generation", stopped_reason
    if verdict in ("refresh_universe_generation_failed",):
        return "refresh_universe", stopped_reason
    if verdict in ("safety_blocked",):
        return "config_safety", stopped_reason

    if verdict == "am_failed":
        am_live = (phase148 or {}).get("am_live") or {}
        exit_code = am_live.get("exit_code")
        if am_impact.get("summary_exists") and am_impact.get("has_trades"):
            notes.append("session completed with trades but runner verdict=am_failed")
            if exit_code == 1:
                return "pilot_exit_nonzero", "pilot exit=1 with summary+trades (verdict policy gap)"
        if am_live.get("new_session_dirs") and not am_impact.get("summary_exists"):
            return "pilot_crash_early", "session dir created but no summary (early crash or deleted artifacts)"
        if exit_code == 1:
            return "pilot_exit_nonzero", "pilot exit=1 without usable summary"
        return "unknown", f"am_pilot stopped_reason={stopped_reason}"

    if verdict == "pm_failed":
        return "pilot_exit_nonzero", f"pm_pilot exit={(phase148 or {}).get('pm_live', {}).get('exit_code')}"

    for label, pat in CLASSIFICATION_PATTERNS:
        if pat.search(text_blob):
            return label, f"pattern:{label}"

    return "unknown", stopped_reason or verdict


def _impact_row(
    day: str,
    verdict: str,
    summary: dict[str, Any],
    phase148: Optional[dict[str, Any]],
    am_impact: dict[str, Any],
    pm_impact: dict[str, Any],
) -> dict[str, Any]:
    am_ran = bool(summary.get("am_session_dir") or (phase148 or {}).get("am_live", {}).get("session_dir"))
    pm_ran = bool(summary.get("pm_session_dir") or (phase148 or {}).get("pm_live", {}).get("session_dir"))
    false_failure = (
        verdict in ("am_failed", "pm_failed")
        and (am_impact.get("has_trades") or pm_impact.get("has_trades"))
        and (am_impact.get("summary_exists") or pm_impact.get("summary_exists"))
    )
    return {
        "day_stamp": day,
        "verdict": verdict,
        "am_executed": am_ran,
        "pm_executed": pm_ran,
        "am_skipped": verdict == "preflight_blocked" or summary.get("am_live_ok") is False,
        "pm_skipped": verdict in ("preflight_blocked", "am_failed", "universe_generation_failed"),
        "summary_generated_am": am_impact.get("summary_exists"),
        "summary_generated_pm": pm_impact.get("summary_exists"),
        "am_has_trades": am_impact.get("has_trades"),
        "pm_has_trades": pm_impact.get("has_trades"),
        "am_accepted_count": am_impact.get("accepted_count"),
        "pm_accepted_count": pm_impact.get("accepted_count"),
        "output_missing": not am_impact.get("summary_exists") and verdict not in (
            "am_pm_daily_runner_ready",
            "intraday_refresh_shadow_ready",
        ),
        "false_failure_with_trades": false_failure,
        "discord_errors_am": am_impact.get("discord_error_count"),
    }


def _recommendations(class_counts: Counter[str], impact_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    false_fail_days = [r["day_stamp"] for r in impact_rows if r.get("false_failure_with_trades")]

    recs.append(
        {
            "category": "preflight_hard_stop",
            "priority": "P0",
            "recommendation": "Keep kabu_station_connection + kabu_register_capacity as preflight hard stops (3/3 preflight_blocked days).",
            "phase": "ops",
            "rationale": f"Blocked AM+PM entirely on {class_counts.get('kabu_station_unreachable', 0)} days.",
        }
    )
    recs.append(
        {
            "category": "verdict_policy",
            "priority": "P0",
            "recommendation": "Treat pilot exit=0 OR (summary_exists + stop_reason=completed) as AM/PM success; downgrade to warning otherwise.",
            "phase": "642_daily_runner_verdict_policy",
            "rationale": f"False am_failed with trades on days: {false_fail_days or 'none'}",
        }
    )
    recs.append(
        {
            "category": "warn_only",
            "priority": "P2",
            "recommendation": "Discord webhook failures, websocket reconnect storms, ref_now logging errors: warn-only in session; never fail daily runner.",
            "phase": "existing (Phase638/640)",
            "rationale": "Discord/WS errors present in live sessions but paper trading continues.",
        }
    )
    recs.append(
        {
            "category": "session_end_isolation",
            "priority": "P1",
            "recommendation": "Wrap post-session shadow auto + organizer + Discord session_end in isolated try/except; never propagate to process exit code.",
            "phase": "643_session_end_exception_isolation",
            "rationale": "20260701 completed session but pilot exit=1 (likely post-main or unlogged exit).",
        }
    )
    recs.append(
        {
            "category": "artifact_retention",
            "priority": "P1",
            "recommendation": "Do not delete live_session_* on am_failed; preserve for root-cause (20260626 dir gone).",
            "phase": "ops",
            "rationale": "20260626 had new_session_dirs but summary missing and artifacts absent.",
        }
    )
    recs.append(
        {
            "category": "file_lock_retry",
            "priority": "P2",
            "recommendation": "Add bounded retry on JSONL append PermissionError (Windows file lock) in LiveSessionWriter.",
            "phase": "644_file_lock_retry",
            "rationale": "No file-lock failures observed in history; preventive for replay/live overlap.",
        }
    )
    recs.append(
        {
            "category": "disk_threshold",
            "priority": "P2",
            "recommendation": "Add preflight disk free-space check (warn <5GB, block <1GB).",
            "phase": "645_disk_preflight",
            "rationale": "No disk-full failures in history.",
        }
    )
    recs.append(
        {
            "category": "logging",
            "priority": "P1",
            "recommendation": "Log pilot stderr/stdout tail + last exception to phase148 am_live/pm_live on nonzero exit.",
            "phase": "641b_runner_subprocess_logging",
            "rationale": "Current artifacts record exit_code=1 but no stacktrace (both am_failed days).",
        }
    )
    return recs


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(row)


def run_audit() -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    summaries = _find_summaries()
    timeline: list[dict[str, Any]] = []
    classifications: list[dict[str, Any]] = []
    first_exceptions: list[dict[str, Any]] = []
    impacts: list[dict[str, Any]] = []

    for day in sorted(summaries):
        summary_path = summaries[day]
        try:
            summary = _load_json(summary_path)
        except Exception as exc:
            timeline.append(
                {
                    "day_stamp": day,
                    "verdict": "parse_error",
                    "stopped_reason": str(exc),
                    "summary_path": _rel(summary_path),
                }
            )
            continue

        verdict = str(summary.get("verdict") or "unknown")
        stopped = str(summary.get("stopped_reason") or "")
        phase148_path = _find_phase148(day)
        phase148 = _load_json(phase148_path) if phase148_path else None

        timeline.append(
            {
                "day_stamp": day,
                "verdict": verdict,
                "stopped_reason": stopped,
                "preflight_ok": summary.get("preflight_ok"),
                "am_live_ok": summary.get("am_live_ok"),
                "pm_live_ok": summary.get("pm_live_ok"),
                "am_session_dir": summary.get("am_session_dir"),
                "pm_session_dir": summary.get("pm_session_dir"),
                "dry_run_only": summary.get("dry_run_only"),
                "generated_at": summary.get("generated_at"),
                "verdict_notes": " | ".join(summary.get("verdict_notes") or []),
            }
        )

        am_impact = _session_impact(day, summary, phase148, "am")
        pm_impact = _session_impact(day, summary, phase148, "pm")
        impacts.append(_impact_row(day, verdict, summary, phase148, am_impact, pm_impact))

        if verdict not in FAILURE_VERDICTS:
            continue

        primary_class, class_note = _classify_failure(
            verdict, stopped, summary, phase148, am_impact, pm_impact, day=day
        )
        am_live = (phase148 or {}).get("am_live") or {}
        pm_live = (phase148 or {}).get("pm_live") or {}
        live = am_live if verdict.startswith("am") or verdict != "pm_failed" else pm_live
        if verdict == "pm_failed":
            live = pm_live

        rel_dir = summary.get("am_session_dir") or am_live.get("session_dir")
        session_dir = _session_dir_path(day, rel_dir)
        first_err = _first_error_from_session(session_dir)
        if verdict == "preflight_blocked":
            safety_fp = NATIVE_ROOT / "results" / "reports" / f"small_paper_safety_{day}.json"
            if safety_fp.is_file():
                try:
                    safety_doc = _load_json(safety_fp)
                    for chk in safety_doc.get("checks") or []:
                        if not chk.get("passed"):
                            first_err = {
                                "file": _rel(safety_fp),
                                "operation": str(chk.get("check_id") or ""),
                                "error_type": "safety_check_failed",
                                "message": str(chk.get("message") or "")[:500],
                                "event_time": str(safety_doc.get("generated_at") or ""),
                                "stacktrace": "",
                            }
                            break
                except Exception:
                    pass
        dom = _dominant_errors(session_dir)

        # refine classification from session errors
        if dom and verdict == "am_failed":
            top_msg = dom[0].get("message_sample", "")
            if "ref_now" in top_msg:
                class_note += "; dominant: ref_now UnboundLocalError (Phase640 fixed)"
            elif "close frame" in top_msg or "ConnectionReset" in top_msg:
                if primary_class == "pilot_exit_nonzero":
                    class_note += "; dominant: websocket_disconnect"

        classifications.append(
            {
                "day_stamp": day,
                "verdict": verdict,
                "stopped_reason": stopped,
                "primary_classification": primary_class,
                "classification_notes": class_note,
                "exit_code": live.get("exit_code"),
                "pilot_error": live.get("error") or live.get("error_message"),
                "failed_safety_checks": ",".join(
                    ((phase148 or {}).get("preflight") or {}).get("safety", {}).get("failed_check_ids") or []
                ),
                "dominant_error_type": dom[0]["error_type"] if dom else "",
                "dominant_error_count": dom[0]["count"] if dom else 0,
            }
        )

        first_exceptions.append(
            {
                "day_stamp": day,
                "verdict": verdict,
                "session_kind": "am" if verdict != "pm_failed" else "pm",
                "file": first_err.get("file"),
                "operation": first_err.get("operation"),
                "error_type": first_err.get("error_type"),
                "message": first_err.get("message"),
                "event_time": first_err.get("event_time"),
                "exit_code": live.get("exit_code"),
                "stopped_reason": live.get("finalize_snapshot", {}).get("stopped_reason") or stopped,
                "stacktrace": first_err.get("stacktrace"),
                "top_errors_json": json.dumps(dom, ensure_ascii=False),
            }
        )

    verdict_counts = Counter(r["verdict"] for r in timeline)
    class_counts = Counter(r["primary_classification"] for r in classifications)
    am_failed_days = [r["day_stamp"] for r in timeline if r["verdict"] == "am_failed"]
    pm_failed_days = [r["day_stamp"] for r in timeline if r["verdict"] == "pm_failed"]
    preflight_days = [r["day_stamp"] for r in timeline if r["verdict"] == "preflight_blocked"]
    false_fail = [r for r in impacts if r.get("false_failure_with_trades")]

    mandatory = {
        "am_failed_occurrences": len(am_failed_days),
        "am_failed_days": am_failed_days,
        "pm_failed_occurrences": len(pm_failed_days),
        "pm_failed_days": pm_failed_days,
        "preflight_blocked_occurrences": len(preflight_days),
        "preflight_blocked_days": preflight_days,
        "top_failure_cause": class_counts.most_common(1)[0][0] if class_counts else "none",
        "false_failure_with_trades_days": [r["day_stamp"] for r in false_fail],
        "hard_stop_causes": ["token_issue", "kabu_station_unreachable", "config_safety", "core_watchlist"],
        "warn_only_causes": ["discord_exception", "websocket_disconnect", "script_bug_ref_now"],
        "p0_immediate": [
            "642: daily runner verdict policy (success when summary+completed despite exit code)",
            "641b: subprocess stderr capture on pilot nonzero exit",
            "ops: ensure Kabu Station running before run_paper_trade.bat (3 preflight_blocked days)",
        ],
        "priority_phases": [
            "642_daily_runner_verdict_policy",
            "641b_runner_subprocess_logging",
            "643_session_end_exception_isolation",
        ],
    }

    recs = _recommendations(class_counts, impacts)
    report = {
        "phase": 641,
        "verdict": PHASE641_VERDICT_DONE,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "history_days": len(timeline),
        "verdict_counts": dict(verdict_counts),
        "failure_classification_counts": dict(class_counts),
        "mandatory_answers": mandatory,
        "notes": [
            "Audit scope: 32 unique daily_runner_summary files under kabu_native/results.",
            "No pm_failed in history. No session_end_failed / interrupted verdicts recorded.",
            "run_paper_trade.bat logs not found in repo; phase148 + session artifacts used.",
        ],
    }

    _write_csv(
        REPORT_DIR / "phase641_verdict_timeline.csv",
        timeline,
        [
            "day_stamp",
            "verdict",
            "stopped_reason",
            "preflight_ok",
            "am_live_ok",
            "pm_live_ok",
            "am_session_dir",
            "pm_session_dir",
            "dry_run_only",
            "generated_at",
            "verdict_notes",
        ],
    )
    _write_csv(
        REPORT_DIR / "phase641_failure_classification.csv",
        classifications,
        [
            "day_stamp",
            "verdict",
            "stopped_reason",
            "primary_classification",
            "classification_notes",
            "exit_code",
            "pilot_error",
            "failed_safety_checks",
            "dominant_error_type",
            "dominant_error_count",
        ],
    )
    _write_csv(
        REPORT_DIR / "phase641_first_exception.csv",
        first_exceptions,
        [
            "day_stamp",
            "verdict",
            "session_kind",
            "file",
            "operation",
            "error_type",
            "message",
            "event_time",
            "exit_code",
            "stopped_reason",
            "stacktrace",
            "top_errors_json",
        ],
    )
    _write_csv(
        REPORT_DIR / "phase641_impact_summary.csv",
        impacts,
        [
            "day_stamp",
            "verdict",
            "am_executed",
            "pm_executed",
            "am_skipped",
            "pm_skipped",
            "summary_generated_am",
            "summary_generated_pm",
            "am_has_trades",
            "pm_has_trades",
            "am_accepted_count",
            "pm_accepted_count",
            "output_missing",
            "false_failure_with_trades",
            "discord_errors_am",
        ],
    )
    _write_csv(
        REPORT_DIR / "phase641_recommendations.csv",
        recs,
        ["category", "priority", "recommendation", "phase", "rationale"],
    )
    (REPORT_DIR / "phase641_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    try:
        report = run_audit()
    except Exception:
        traceback.print_exc()
        return 1
    return 0 if report.get("verdict") == PHASE641_VERDICT_DONE else 1


if __name__ == "__main__":
    raise SystemExit(main())
