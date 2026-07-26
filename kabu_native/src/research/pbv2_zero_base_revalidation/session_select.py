"""Canonical AM/PM session selection with explicit exclusion reasons."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from research.pbv2_zero_base_revalidation.constants import NATIVE

# Empty / boot / overnight sessions are not trading coverage.
MIN_EVENTS_BYTES = 1_000_000
AM_HOUR_START = 7
AM_HOUR_END = 12  # [7, 12)
PM_HOUR_START = 12
PM_HOUR_END = 16  # [12, 16)


@dataclass(frozen=True)
class SessionInfo:
    day: str
    path: Path
    name: str
    hhmmss: str
    hour: int
    bucket: str  # AM | PM | NIGHT | OTHER
    events_bytes: int
    usable: bool
    exclude_reason: str


def _parse_hhmmss(name: str) -> tuple[str, int]:
    parts = name.split("_")
    token = parts[-1] if parts else ""
    if token.isdigit() and len(token) == 6:
        return token, int(token[:2])
    return "", -1


def classify_bucket(hour: int) -> str:
    if AM_HOUR_START <= hour < AM_HOUR_END:
        return "AM"
    if PM_HOUR_START <= hour < PM_HOUR_END:
        return "PM"
    if hour < 0:
        return "OTHER"
    return "NIGHT"


def inventory_day(day_dir: Path) -> list[SessionInfo]:
    out: list[SessionInfo] = []
    day = day_dir.name
    for sess in sorted(day_dir.glob("live_session_*")):
        if "demo" in sess.name.lower():
            out.append(
                SessionInfo(day, sess, sess.name, "", -1, "OTHER", 0, False, "demo_session")
            )
            continue
        hhmmss, hour = _parse_hhmmss(sess.name)
        bucket = classify_bucket(hour)
        ev = sess / "small_paper_events.csv"
        sz = ev.stat().st_size if ev.exists() else 0
        if not ev.exists():
            out.append(SessionInfo(day, sess, sess.name, hhmmss, hour, bucket, 0, False, "missing_events_csv"))
            continue
        if sz < MIN_EVENTS_BYTES:
            out.append(
                SessionInfo(day, sess, sess.name, hhmmss, hour, bucket, sz, False, "empty_or_tiny_events_file")
            )
            continue
        if bucket == "NIGHT":
            out.append(
                SessionInfo(day, sess, sess.name, hhmmss, hour, bucket, sz, False, "outside_trading_session_window")
            )
            continue
        if bucket not in ("AM", "PM"):
            out.append(SessionInfo(day, sess, sess.name, hhmmss, hour, bucket, sz, False, "unclassified_session"))
            continue
        out.append(SessionInfo(day, sess, sess.name, hhmmss, hour, bucket, sz, True, ""))
    return out


def select_canonical_sessions(native: Path = NATIVE) -> dict[str, Any]:
    """
    Canonical rule:
    - Inventory all live_session_* per day
    - Drop demo / missing / tiny / night
    - Keep richest usable AM and richest usable PM independently
    - Superseded same-bucket sessions are excluded with reason
    """
    root = native / "results" / "small_paper"
    selected: list[tuple[str, Path, str]] = []  # day, path, bucket
    audit_rows: list[dict[str, Any]] = []
    coverage_blocked_days: list[str] = []

    if not root.exists():
        return {
            "selected": selected,
            "audit_rows": audit_rows,
            "session_coverage_pass": False,
            "canonical_rule": "no_small_paper_root",
            "coverage_blocked_days": [],
        }

    for day_dir in sorted(root.iterdir()):
        if not day_dir.is_dir() or not day_dir.name.isdigit() or day_dir.name.startswith("2099"):
            continue
        infos = inventory_day(day_dir)
        usable_am = [i for i in infos if i.usable and i.bucket == "AM"]
        usable_pm = [i for i in infos if i.usable and i.bucket == "PM"]
        best_am = max(usable_am, key=lambda x: x.events_bytes) if usable_am else None
        best_pm = max(usable_pm, key=lambda x: x.events_bytes) if usable_pm else None

        chosen_names = set()
        if best_am:
            selected.append((day_dir.name, best_am.path, "AM"))
            chosen_names.add(best_am.name)
        if best_pm:
            selected.append((day_dir.name, best_pm.path, "PM"))
            chosen_names.add(best_pm.name)

        # Coverage integrity: if both AM and PM usable exist, both must be selected
        if usable_am and usable_pm and not (best_am and best_pm):
            coverage_blocked_days.append(day_dir.name)
        if (usable_am and best_am is None) or (usable_pm and best_pm is None):
            coverage_blocked_days.append(day_dir.name)

        excluded = []
        for i in infos:
            if i.name in chosen_names:
                continue
            reason = i.exclude_reason
            if i.usable and i.bucket == "AM" and best_am and i.name != best_am.name:
                reason = "superseded_by_richer_am"
            elif i.usable and i.bucket == "PM" and best_pm and i.name != best_pm.name:
                reason = "superseded_by_richer_pm"
            elif i.usable and not reason:
                reason = "not_selected"
            excluded.append({"session": i.name, "bucket": i.bucket, "reason": reason, "events_bytes": i.events_bytes})

        audit_rows.append(
            {
                "day": day_dir.name,
                "live_session_count": len([i for i in infos if "demo" not in i.name.lower()]),
                "usable_am_count": len(usable_am),
                "usable_pm_count": len(usable_pm),
                "selected_am": best_am.name if best_am else "",
                "selected_pm": best_pm.name if best_pm else "",
                "selected_sessions": ",".join(sorted(chosen_names)),
                "excluded_sessions": ";".join(f"{e['session']}:{e['reason']}" for e in excluded),
                "has_am": bool(best_am),
                "has_pm": bool(best_pm),
                "am_pm_both_required": bool(usable_am and usable_pm),
                "am_pm_both_selected": bool(best_am and best_pm) if (usable_am and usable_pm) else None,
                "coverage_ok_day": (bool(best_am and best_pm) if (usable_am and usable_pm) else True),
            }
        )

    # Unique blocked days
    coverage_blocked_days = sorted(set(coverage_blocked_days))
    # Also block if any day with both usable buckets did not select both
    for row in audit_rows:
        if row.get("am_pm_both_required") and not row.get("am_pm_both_selected"):
            coverage_blocked_days.append(row["day"])
    coverage_blocked_days = sorted(set(coverage_blocked_days))

    session_coverage_pass = len(coverage_blocked_days) == 0 and any(r.get("has_am") or r.get("has_pm") for r in audit_rows)

    return {
        "selected": selected,
        "audit_rows": audit_rows,
        "session_coverage_pass": session_coverage_pass,
        "coverage_blocked_days": coverage_blocked_days,
        "canonical_rule": (
            "Per day: richest usable AM (07-12) + richest usable PM (12-16); "
            f"exclude events<{MIN_EVENTS_BYTES}B, night/demo/missing; "
            "supersede duplicate same-bucket sessions."
        ),
        "n_selected": len(selected),
    }


def session_bucket_from_name(session_name: str) -> str:
    _, hour = _parse_hhmmss(session_name)
    return classify_bucket(hour)
