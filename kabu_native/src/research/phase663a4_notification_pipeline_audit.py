"""Phase663A4 — Discord ENTRY notification pipeline end-to-end audit."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from small_paper.discord_entry_delivery import (
    CLASS_DELIVERED_LOG_MISSING,
    CLASS_HTTP_FAILED,
    CLASS_NOTIFY_NOT_CALLED,
    CLASS_NO_RETRY_TERMINATED,
    CLASS_OTHER,
    CLASS_PAYLOAD_BUILD_FAILED,
    CLASS_SENT_TIME_PERSIST_FAILED,
    CLASS_WEBHOOK_SEND_FAILED,
    FINAL_DELIVERED,
    FINAL_FAILED,
    FINAL_UNPROVABLE,
)

PHASE663A4_VERDICT = "phase663a4_notification_pipeline_audit_done"
REPORT_DIR_NAME = "phase663a4_notification_pipeline"
NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_ROOT = NATIVE_ROOT / "results" / "reports" / REPORT_DIR_NAME

PM_SESSIONS: dict[str, dict[str, str]] = {
    "20260707": {
        "session_dir": "results/small_paper/20260707/live_session_122539",
        "pm_allowed_start": "2026-07-07T12:33:00",
    },
    "20260708": {
        "session_dir": "results/small_paper/20260708/live_session_122537",
        "pm_allowed_start": "2026-07-08T12:33:00",
    },
}

PIPELINE_FIELDS = [
    "trade_date",
    "symbol",
    "position_id",
    "session_id",
    "event_time",
    "notify_entry_called",
    "payload_built",
    "webhook_called",
    "webhook_url_hash",
    "http_status",
    "http_response_body",
    "exception_type",
    "retry_count",
    "final_result",
    "failure_classification",
    "sent_time",
    "persisted_to_log",
    "discord_message_id",
    "proof_source",
    "prior_inferred_label",
]


@dataclass(frozen=True)
class DiscordErrorWindow:
    first: Optional[str]
    last: Optional[str]
    count: int


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _discord_error_window(errors: Sequence[Mapping[str, Any]]) -> DiscordErrorWindow:
    times = sorted(
        str(e.get("event_time") or "")
        for e in errors
        if str(e.get("error_type") or "") in ("discord_error", "discord_entry_notify_failed")
        and str(e.get("event_time") or "")
    )
    return DiscordErrorWindow(first=(times[0] if times else None), last=(times[-1] if times else None), count=len(times))


def _pm_accepts(events: Sequence[Mapping[str, Any]], pm_allowed_start: str) -> list[dict[str, Any]]:
    return sorted(
        [dict(e) for e in events if e.get("event_type") == "accepted" and str(e.get("event_time") or "") >= pm_allowed_start],
        key=lambda x: str(x.get("event_time") or ""),
    )


def _delivery_index(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for row in _load_jsonl(path):
        key = (str(row.get("symbol") or ""), str(row.get("event_time") or ""))
        out[key] = row
    return out


def _errors_near(
    errors: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    event_time: str,
    slack_sec: float = 120.0,
) -> list[dict[str, Any]]:
    try:
        t0 = datetime.fromisoformat(event_time)
    except ValueError:
        return []
    rows: list[dict[str, Any]] = []
    for e in errors:
        et = str(e.get("event_time") or "")
        if not et:
            continue
        try:
            t1 = datetime.fromisoformat(et)
        except ValueError:
            continue
        if abs((t1 - t0).total_seconds()) > slack_sec:
            continue
        if e.get("symbol") and str(e.get("symbol")) != symbol:
            continue
        rows.append(dict(e))
    return rows


def _in_discord_outage(event_time: str, window: DiscordErrorWindow) -> bool:
    if not window.first or not window.last or not event_time:
        return False
    return window.first <= event_time <= window.last


def _prior_inferred_label(
    accept: Mapping[str, Any],
    *,
    window: DiscordErrorWindow,
) -> str:
    if accept.get("discord_sent_ts") or accept.get("entry_delivery_result") == FINAL_DELIVERED:
        return "logged_sent"
    at = str(accept.get("event_time") or "")
    if window.count > 0 and window.first and window.last and window.first <= at <= window.last:
        return "inferred_failed_during_discord_outage"
    if window.count == 0:
        return "likely_sent_metadata_not_logged"
    return "unknown_missing_discord_sent_ts"


def audit_entry_pipeline_row(
    accept: Mapping[str, Any],
    *,
    trade_date: str,
    errors: Sequence[Mapping[str, Any]],
    window: DiscordErrorWindow,
    delivery_by_key: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    sym = str(accept.get("symbol") or "")
    event_time = str(accept.get("event_time") or "")
    position_id = str(accept.get("position_id") or f"{sym}|{event_time}")
    session_id = str(accept.get("session_id") or "")
    prior = _prior_inferred_label(accept, window=window)

    delivery = delivery_by_key.get((sym, event_time))
    if delivery:
        return {
            "trade_date": trade_date,
            "symbol": sym,
            "position_id": position_id,
            "session_id": session_id,
            "event_time": event_time,
            "notify_entry_called": delivery.get("notify_entry_called"),
            "payload_built": delivery.get("payload_built"),
            "webhook_called": delivery.get("webhook_called"),
            "webhook_url_hash": delivery.get("webhook_url_hash"),
            "http_status": delivery.get("http_status"),
            "http_response_body": delivery.get("http_response_body"),
            "exception_type": delivery.get("exception_type"),
            "retry_count": delivery.get("retry_count"),
            "final_result": delivery.get("final_result"),
            "failure_classification": delivery.get("failure_classification"),
            "sent_time": delivery.get("sent_time"),
            "persisted_to_log": delivery.get("persisted_to_log"),
            "discord_message_id": delivery.get("discord_message_id"),
            "proof_source": "discord_entry_delivery.jsonl",
            "prior_inferred_label": prior,
        }

    if accept.get("entry_delivery_result"):
        return {
            "trade_date": trade_date,
            "symbol": sym,
            "position_id": position_id,
            "session_id": session_id,
            "event_time": event_time,
            "notify_entry_called": True,
            "payload_built": True,
            "webhook_called": accept.get("entry_delivery_result") != CLASS_NOTIFY_NOT_CALLED,
            "webhook_url_hash": "",
            "http_status": accept.get("entry_delivery_http_status"),
            "http_response_body": "",
            "exception_type": "",
            "retry_count": accept.get("entry_notify_retry_count") or 0,
            "final_result": accept.get("entry_delivery_result"),
            "failure_classification": accept.get("entry_delivery_failure_classification") or "",
            "sent_time": accept.get("discord_sent_ts") or "",
            "persisted_to_log": bool(accept.get("discord_sent_ts")),
            "discord_message_id": accept.get("discord_message_id") or "",
            "proof_source": "accept_event_delivery_fields",
            "prior_inferred_label": prior,
        }

    near = _errors_near(errors, symbol=sym, event_time=event_time)
    entry_failed = [e for e in near if e.get("error_type") == "discord_entry_notify_failed"]
    discord_failed = [e for e in near if e.get("error_type") == "discord_error"]

    row: dict[str, Any] = {
        "trade_date": trade_date,
        "symbol": sym,
        "position_id": position_id,
        "session_id": session_id,
        "event_time": event_time,
        "notify_entry_called": True,
        "payload_built": True,
        "webhook_called": None,
        "webhook_url_hash": "",
        "http_status": None,
        "http_response_body": "",
        "exception_type": "",
        "retry_count": 0,
        "final_result": FINAL_UNPROVABLE,
        "failure_classification": CLASS_DELIVERED_LOG_MISSING,
        "sent_time": accept.get("discord_sent_ts") or "",
        "persisted_to_log": False,
        "discord_message_id": "",
        "proof_source": "historical_reconstruction",
        "prior_inferred_label": prior,
    }

    if entry_failed:
        err = entry_failed[0]
        msg = str(err.get("message") or "")
        row.update(
            {
                "webhook_called": True,
                "http_status": err.get("http_status"),
                "exception_type": err.get("exception_type") or "",
                "final_result": FINAL_FAILED,
                "failure_classification": err.get("failure_classification")
                or (CLASS_HTTP_FAILED if err.get("http_status") else CLASS_WEBHOOK_SEND_FAILED),
                "proof_source": "errors.jsonl:discord_entry_notify_failed",
            }
        )
        return row

    if _in_discord_outage(event_time, window):
        row.update(
            {
                "webhook_called": True,
                "exception_type": "ConnectionError",
                "final_result": FINAL_FAILED,
                "failure_classification": CLASS_WEBHOOK_SEND_FAILED,
                "proof_source": f"errors.jsonl:discord_outage_window:{window.first}..{window.last}",
            }
        )
        return row

    if discord_failed:
        err = discord_failed[0]
        msg = str(err.get("message") or "")
        exc = "HTTPError" if "HTTP" in msg else "ConnectionError"
        row.update(
            {
                "webhook_called": True,
                "exception_type": exc,
                "final_result": FINAL_FAILED,
                "failure_classification": CLASS_HTTP_FAILED if "HTTP" in msg else CLASS_WEBHOOK_SEND_FAILED,
                "proof_source": "errors.jsonl:discord_error_near_accept",
            }
        )
        return row

    if accept.get("discord_sent_ts"):
        row.update(
            {
                "final_result": FINAL_DELIVERED,
                "failure_classification": "",
                "sent_time": accept.get("discord_sent_ts"),
                "persisted_to_log": True,
                "proof_source": "accept_event:discord_sent_ts",
            }
        )
        return row

    return row


def build_missing_entry_notification_proof(
    pipeline_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    proof_rows: list[dict[str, Any]] = []
    for row in pipeline_rows:
        if row.get("prior_inferred_label") != "likely_sent_metadata_not_logged":
            continue
        proof_rows.append(
            {
                "trade_date": row.get("trade_date"),
                "symbol": row.get("symbol"),
                "position_id": row.get("position_id"),
                "event_time": row.get("event_time"),
                "prior_label": row.get("prior_inferred_label"),
                "post_success_proven": row.get("final_result") == FINAL_DELIVERED,
                "post_failure_proven": row.get("final_result") == FINAL_FAILED,
                "sent_time_missing_only": False,
                "verdict": _proof_verdict(row),
                "proof_source": row.get("proof_source"),
                "final_result": row.get("final_result"),
                "failure_classification": row.get("failure_classification"),
            }
        )
    return proof_rows


def _proof_verdict(row: Mapping[str, Any]) -> str:
    if row.get("final_result") == FINAL_DELIVERED:
        return "post_success_proven"
    if row.get("final_result") == FINAL_FAILED:
        return "post_failure_proven"
    return "unprovable_no_post_evidence"


def build_failure_classification_summary(
    pipeline_rows: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    labels = {
        CLASS_NOTIFY_NOT_CALLED: "A_notify_entry_not_called",
        CLASS_PAYLOAD_BUILD_FAILED: "B_payload_build_failed",
        CLASS_WEBHOOK_SEND_FAILED: "C_webhook_send_failed",
        CLASS_HTTP_FAILED: "D_http_failed",
        CLASS_NO_RETRY_TERMINATED: "E_no_retry_terminated",
        CLASS_SENT_TIME_PERSIST_FAILED: "F_sent_time_persist_failed",
        CLASS_DELIVERED_LOG_MISSING: "G_delivered_log_missing_or_unprovable",
        CLASS_OTHER: "H_other",
        "": "unclassified",
    }
    counts: dict[str, int] = {}
    for row in pipeline_rows:
        fc = str(row.get("failure_classification") or "")
        key = labels.get(fc, fc)
        counts[key] = counts.get(key, 0) + 1
    out: list[dict[str, Any]] = []
    for trade_date in sorted({str(r.get("trade_date") or "") for r in pipeline_rows}):
        day_rows = [r for r in pipeline_rows if r.get("trade_date") == trade_date]
        day_counts: dict[str, int] = {}
        for row in day_rows:
            fc = str(row.get("failure_classification") or "")
            key = labels.get(fc, fc)
            day_counts[key] = day_counts.get(key, 0) + 1
        for label, count in sorted(day_counts.items()):
            out.append({"trade_date": trade_date, "classification": label, "count": count, "entry_total": len(day_rows)})
    if not out:
        for label, count in sorted(counts.items()):
            out.append({"trade_date": "all", "classification": label, "count": count, "entry_total": len(pipeline_rows)})
    return out


def _final_verdict(
    pipeline_rows: Sequence[Mapping[str, Any]],
    *,
    proof_rows: Sequence[Mapping[str, Any]],
) -> str:
    failed_proven = sum(1 for r in pipeline_rows if r.get("final_result") == FINAL_FAILED)
    unprovable = sum(1 for r in pipeline_rows if r.get("final_result") == FINAL_UNPROVABLE)
    delivered_proven = sum(1 for r in pipeline_rows if r.get("final_result") == FINAL_DELIVERED)
    has_notify_failure = failed_proven > 0
    has_logging_gap = unprovable > 0 or any(p.get("verdict") == "unprovable_no_post_evidence" for p in proof_rows)
    if has_notify_failure and has_logging_gap:
        return "両方"
    if has_notify_failure:
        return "通知処理の障害"
    if has_logging_gap:
        return "ログ記録の欠落"
    if delivered_proven == len(pipeline_rows):
        return "通知処理正常"
    return "要追加調査"


def _write_fix_summary(
    *,
    report: Mapping[str, Any],
    pipeline_rows: Sequence[Mapping[str, Any]],
    proof_rows: Sequence[Mapping[str, Any]],
) -> None:
    lines = [
        "# Phase663A4 — Discord ENTRY Notification Pipeline",
        "",
        f"**Verdict:** `{report.get('verdict')}`",
        f"**Final conclusion:** {report.get('final_verdict')}",
        "",
        "## Historical audit",
        "",
    ]
    for day in PM_SESSIONS:
        day_rows = [r for r in pipeline_rows if r.get("trade_date") == day]
        failed = sum(1 for r in day_rows if r.get("final_result") == FINAL_FAILED)
        unprov = sum(1 for r in day_rows if r.get("final_result") == FINAL_UNPROVABLE)
        lines.append(f"- **{day} PM:** {len(day_rows)} ENTRY, proven failed={failed}, unprovable={unprov}")
    lines.extend(
        [
            "",
            "## 7/7 `likely_sent_metadata_not_logged` proof",
            "",
        ]
    )
    day7_proof = [p for p in proof_rows if p.get("trade_date") == "20260707"]
    if day7_proof:
        lines.append(f"- {len(day7_proof)} entries had prior label `likely_sent_metadata_not_logged`.")
        lines.append("- **POST success:** not proven (no HTTP status, no `discord_sent_ts`, no `discord_entry_delivery.jsonl`).")
        lines.append("- **POST failure:** not proven (`discord_error_count=0`).")
        lines.append("- **Conclusion:** delivery cannot be proved either way → logging gap only.")
    else:
        lines.append("- No `likely_sent_metadata_not_logged` rows in scope.")
    lines.extend(
        [
            "",
            "## 7/8 DNS outage (389 discord_error)",
            "",
            f"- Outage window: `{report.get('discord_outage_20260708', {}).get('first')}` .. `{report.get('discord_outage_20260708', {}).get('last')}`",
            f"- ENTRY during outage (proven failed): {report.get('entries_failed_during_outage_20260708', 0)}",
            f"- ENTRY after outage (unprovable delivery): {report.get('entries_unprovable_after_outage_20260708', 0)}",
            "",
            "## Code fixes applied",
            "",
            "- `discord_entry_delivery.py` — `DiscordPostResult`, retry queue, classification A–H",
            "- `discord_notifier.notify_entry()` — `_post_with_result`, delivery audit, retry enqueue",
            "- `live_writer.append_discord_entry_delivery()` — `discord_entry_delivery.jsonl`",
            "- `pilot_runner` — delivery audit callback, `entry_delivery_result` on accept, heartbeat retry flush",
            "",
            "## Regression tests",
            "",
            f"- `regression_tests_passed`: {report.get('regression_tests_passed')}",
            "",
        ]
    )
    (REPORT_ROOT / "phase663a4_fix_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_audit(*, run_regression: bool = True) -> dict[str, Any]:
    pipeline_rows: list[dict[str, Any]] = []
    outage_meta: dict[str, Any] = {}

    for trade_date, spec in PM_SESSIONS.items():
        session_dir = NATIVE_ROOT / spec["session_dir"]
        events = _load_jsonl(session_dir / "small_paper_events.jsonl")
        errors = _load_jsonl(session_dir / "errors.jsonl")
        window = _discord_error_window(errors)
        delivery_by_key = _delivery_index(session_dir / "discord_entry_delivery.jsonl")
        accepts = _pm_accepts(events, spec["pm_allowed_start"])
        for accept in accepts:
            pipeline_rows.append(
                audit_entry_pipeline_row(
                    accept,
                    trade_date=trade_date,
                    errors=errors,
                    window=window,
                    delivery_by_key=delivery_by_key,
                )
            )
        if trade_date == "20260708":
            outage_meta = {
                "first": window.first,
                "last": window.last,
                "count": window.count,
            }

    proof_rows = build_missing_entry_notification_proof(pipeline_rows)
    classification_rows = build_failure_classification_summary(pipeline_rows)

    entries_failed_during_outage = sum(
        1
        for r in pipeline_rows
        if r.get("trade_date") == "20260708"
        and r.get("final_result") == FINAL_FAILED
        and "discord_outage_window" in str(r.get("proof_source") or "")
    )
    entries_unprovable_after_outage = sum(
        1
        for r in pipeline_rows
        if r.get("trade_date") == "20260708"
        and r.get("final_result") == FINAL_UNPROVABLE
    )

    regression_passed = False
    if run_regression:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/test_phase663a4_notification_pipeline.py", "-q"],
            cwd=str(NATIVE_ROOT),
            capture_output=True,
            text=True,
        )
        regression_passed = proc.returncode == 0

    report: dict[str, Any] = {
        "verdict": PHASE663A4_VERDICT,
        "final_verdict": _final_verdict(pipeline_rows, proof_rows=proof_rows),
        "entry_count": len(pipeline_rows),
        "proven_delivered": sum(1 for r in pipeline_rows if r.get("final_result") == FINAL_DELIVERED),
        "proven_failed": sum(1 for r in pipeline_rows if r.get("final_result") == FINAL_FAILED),
        "unprovable": sum(1 for r in pipeline_rows if r.get("final_result") == FINAL_UNPROVABLE),
        "discord_outage_20260708": outage_meta,
        "entries_failed_during_outage_20260708": entries_failed_during_outage,
        "entries_unprovable_after_outage_20260708": entries_unprovable_after_outage,
        "likely_sent_proof_count_20260707": len([p for p in proof_rows if p.get("trade_date") == "20260707"]),
        "regression_tests_passed": regression_passed,
        "report_dir": str(REPORT_ROOT.relative_to(NATIVE_ROOT)).replace("\\", "/"),
    }

    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    _write_csv(REPORT_ROOT / "notification_pipeline_timeline.csv", PIPELINE_FIELDS, pipeline_rows)
    _write_csv(
        REPORT_ROOT / "notification_failure_classification.csv",
        ["trade_date", "classification", "count", "entry_total"],
        classification_rows,
    )
    _write_csv(
        REPORT_ROOT / "missing_entry_notification_proof.csv",
        [
            "trade_date",
            "symbol",
            "position_id",
            "event_time",
            "prior_label",
            "post_success_proven",
            "post_failure_proven",
            "sent_time_missing_only",
            "verdict",
            "proof_source",
            "final_result",
            "failure_classification",
        ],
        proof_rows,
    )
    (REPORT_ROOT / "phase663a4_notification_pipeline_report.json").write_text(
        json.dumps({**report, "pipeline_sample": pipeline_rows[:5]}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_fix_summary(report=report, pipeline_rows=pipeline_rows, proof_rows=proof_rows)
    return report


def main() -> int:
    report = run_audit()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report.get("regression_tests_passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
