#!/usr/bin/env python3
"""
Phase176b: Live shadow audit to confirm intraday refresh failure does NOT stop the runner.

Reads live session outputs (small_paper_summary.json, errors.jsonl, heartbeat.jsonl)
and writes:
 - kabu_native/results/reports/phase176b_intraday_refresh_live_shadow_audit.json
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo
from dataclasses import asdict


JST = ZoneInfo("Asia/Tokyo")
OUT = Path("kabu_native/results/reports/phase176b_intraday_refresh_live_shadow_audit.json")


@dataclass
class SessionAudit:
    session_dir: str
    ok: bool
    verdict: str
    refresh_time: Optional[str]
    refresh_failed: bool
    ended_at: Optional[str]
    ended_after_refresh_min: Optional[float]
    has_degraded_action_log: bool
    stop_reason: Optional[str]
    heartbeat_after_refresh_count: int
    push_messages_before: Optional[int]
    push_messages_after: Optional[int]
    notes: list[str]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                out.append({"_raw": line, "_error": "json_decode_error"})
    return out


def _parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


def _parse_refresh_dt(generated_at: Optional[str], refresh_hhmm: Optional[str]) -> Optional[datetime]:
    g = _parse_iso(generated_at)
    if not g or not refresh_hhmm:
        return None
    try:
        hh, mm = int(refresh_hhmm[:2]), int(refresh_hhmm[3:5])
    except Exception:
        return None
    return g.astimezone(JST).replace(hour=hh, minute=mm, second=0, microsecond=0)


def _intraday_refresh_events(errors: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in errors if str(e.get("error_type") or "") == "intraday_refresh"]


def _has_degraded_action(errors: Iterable[dict[str, Any]]) -> bool:
    for e in _intraday_refresh_events(errors):
        if e.get("event") != "failed":
            continue
        if e.get("action") == "continue_keep_previous_subscription" and e.get("will_stop") is False:
            return True
    return False


def _looks_post_patch(errors: Iterable[dict[str, Any]]) -> bool:
    """Heuristic: post-patch refresh failure includes action/will_stop fields."""
    for e in _intraday_refresh_events(errors):
        if e.get("event") != "failed":
            continue
        if "action" in e or "will_stop" in e:
            return True
    return False


def _hb_after_refresh(heartbeats: list[dict[str, Any]], refresh_dt: datetime) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for hb in heartbeats:
        t = _parse_iso(str(hb.get("event_time") or ""))
        if not t:
            continue
        if t.astimezone(JST) >= refresh_dt:
            out.append(hb)
    return out


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    base = Path("kabu_native/results/small_paper")
    if not base.is_dir():
        OUT.write_text(json.dumps({"phase": "176b", "ok": False, "error": "missing_small_paper_results_dir"}, indent=2), encoding="utf-8")
        return 0

    # Audit most recent day’s AM/PM sessions if present; otherwise scan all.
    candidates: list[Path] = []
    for p in sorted(base.rglob("small_paper_summary.json"), key=lambda x: str(x))[-20:]:
        candidates.append(p.parent)

    audits: list[dict[str, Any]] = []
    overall_ok = True
    post_patch_found = 0

    for sdir in candidates:
        summ = _read_json(sdir / "small_paper_summary.json")
        if not summ:
            continue
        if not bool(summ.get("intraday_refresh_enabled")):
            continue

        errors = _read_jsonl(sdir / "errors.jsonl")
        heartbeats = _read_jsonl(sdir / "heartbeat.jsonl")
        if not _looks_post_patch(errors):
            # Pre-patch sessions will fail the new requirements; skip them and require new run.
            continue
        post_patch_found += 1
        refresh_dt = _parse_refresh_dt(summ.get("generated_at"), summ.get("intraday_refresh_time"))
        ended = _parse_iso(summ.get("ended_at"))

        notes: list[str] = []
        refresh_events = _intraday_refresh_events(errors)
        refresh_failed = any(e.get("event") == "failed" for e in refresh_events)
        degraded_log_ok = _has_degraded_action(errors)

        ended_after_min = None
        if refresh_dt and ended:
            ended_after_min = (ended.astimezone(JST) - refresh_dt).total_seconds() / 60.0

        hb_after = []
        if refresh_dt:
            hb_after = _hb_after_refresh(heartbeats, refresh_dt)

        # push_messages progression: use heartbeat.jsonl to compare nearest before/after
        push_before = None
        push_after = None
        if refresh_dt:
            before = []
            after = []
            for hb in heartbeats:
                t = _parse_iso(str(hb.get("event_time") or ""))
                if not t:
                    continue
                if t.astimezone(JST) < refresh_dt:
                    before.append(hb)
                else:
                    after.append(hb)
            if before:
                push_before = int(before[-1].get("push_messages") or 0)
            if after:
                push_after = int(after[-1].get("push_messages") or 0)

        # Assertions / verdict logic
        ok = True
        verdict = "ok"

        stop_reason = summ.get("stop_reason")
        if stop_reason == "open_symbols_exceed_cap":
            ok = False
            verdict = "stopped_with_open_symbols_exceed_cap"
            notes.append("stop_reason=open_symbols_exceed_cap indicates fatal stop (should not happen post-patch)")

        if refresh_failed and not degraded_log_ok:
            ok = False
            verdict = "missing_degraded_failure_log"
            notes.append("refresh failed but no action=continue_keep_previous_subscription, will_stop=false log found")

        # Require: not ended at refresh time (15+ minutes after refresh), OR at least heartbeat continues 15+ minutes.
        if refresh_failed:
            if ended_after_min is not None and ended_after_min < 1.0:
                ok = False
                verdict = "ended_at_refresh_time"
                notes.append("ended_at is essentially at refresh time")
            # Stronger requirement: >= 15 minutes continuation
            if ended_after_min is not None and ended_after_min < 15.0:
                notes.append("ended_after_refresh < 15min (require >=15min or session_end)")
                ok = False
                verdict = "did_not_continue_15min_after_refresh"
            if refresh_dt and len(hb_after) == 0:
                ok = False
                verdict = "no_heartbeat_after_refresh"
                notes.append("no heartbeat.jsonl entries after refresh time")
            if push_before is not None and push_after is not None and push_after <= push_before:
                ok = False
                verdict = "push_messages_not_increasing_after_refresh"
                notes.append("push_messages did not increase after refresh")

        overall_ok = overall_ok and ok

        audits.append(
            asdict(
                SessionAudit(
                    session_dir=str(sdir).replace("\\", "/"),
                    ok=ok,
                    verdict=verdict,
                    refresh_time=str(summ.get("intraday_refresh_time") or ""),
                    refresh_failed=refresh_failed,
                    ended_at=summ.get("ended_at"),
                    ended_after_refresh_min=round(ended_after_min, 2) if ended_after_min is not None else None,
                    has_degraded_action_log=degraded_log_ok,
                    stop_reason=stop_reason,
                    heartbeat_after_refresh_count=len(hb_after),
                    push_messages_before=push_before,
                    push_messages_after=push_after,
                    notes=notes,
                )
            )
        )

    out = {
        "phase": "176b",
        "ok": overall_ok if post_patch_found > 0 else None,
        "verdict": (
            "pass"
            if (post_patch_found > 0 and overall_ok)
            else ("fail" if post_patch_found > 0 else "insufficient_data_post_patch_run_needed")
        ),
        "audited_session_count": len(audits),
        "post_patch_session_count": post_patch_found,
        "audits": audits,
        "requirements": {
            "ended_at_not_stuck_at_refresh": True,
            "push_messages_increase_after_refresh": True,
            "stop_reason_not_open_symbols_exceed_cap": True,
            "errors_has_degraded_action_fields": True,
            "heartbeat_continues_15min_after_refresh": True,
        },
        "notes": [
            "This audit reads heartbeat.jsonl (time series). Ensure incremental writer is enabled in live shadow runs.",
            "If no refresh failure occurred in a session, the strict post-failure checks are not applied.",
            "This audit intentionally skips pre-patch sessions (no action/will_stop fields in intraday_refresh failed events).",
        ],
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

