"""Capture-day quality inventory. Paper realtime-invalid is not an exclusion by itself."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from research.anchor_timing_robustness import X32_FORBIDDEN_FROM, X32_HISTORICAL_END
from research.anchor_vs_event_driven.run_comparison import _load_json, find_capture_dir

NATIVE = Path(__file__).resolve().parents[3]
CAP_ROOT = NATIVE / "data" / "market_capture"
KNOWN_HOLIDAYS = {"20260811"}


def _peek_seq(capture: Path) -> dict[str, Any]:
    """First/last sequence only. Never counts every capture line."""
    parts = sorted(p for p in capture.glob("push_part_*.jsonl") if p.stat().st_size > 0)
    if not parts:
        return {
            "line_count": 0,
            "first_seq": None,
            "last_seq": None,
            "contiguous_hint": False,
            "size_bytes": 0,
            "n_parts": 0,
        }
    size = sum(p.stat().st_size for p in parts)
    first_seq = None
    first_t = ""
    with parts[0].open("rb") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except Exception:
                break
            try:
                first_seq = int(rec.get("sequence") or 0) or None
            except (TypeError, ValueError):
                first_seq = None
            first_t = str(rec.get("received_at") or rec.get("event_time") or rec.get("persisted_at") or "")
            break
    last_seq = None
    last_t = ""
    with parts[-1].open("rb") as fh:
        fh.seek(0, 2)
        pos = fh.tell()
        fh.seek(max(0, pos - 262144))
        chunk = fh.read().decode("utf-8", errors="replace")
    for line in reversed(chunk.splitlines()):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except Exception:
            continue
        try:
            last_seq = int(rec.get("sequence") or 0) or last_seq
        except (TypeError, ValueError):
            pass
        last_t = str(rec.get("received_at") or rec.get("event_time") or rec.get("persisted_at") or last_t)
        break
    return {
        "line_count": int(last_seq or 0),
        "first_seq": first_seq,
        "last_seq": last_seq,
        "contiguous_hint": first_seq == 1,
        "first_event": first_t,
        "last_event": last_t,
        "size_bytes": size,
        "n_parts": len(parts),
    }


def _period_split(day: str) -> str:
    if day <= X32_HISTORICAL_END:
        return "DEVELOPMENT"
    if day >= X32_FORBIDDEN_FROM:
        return "POST_FREEZE_HOLDOUT"
    return "GAP_FREEZE_WINDOW"


def build_inventory() -> list[dict[str, Any]]:
    import sys

    scripts = NATIVE / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    from _p1_inventory import classify, resolve_universe

    days: list[str] = []
    if CAP_ROOT.is_dir():
        days = sorted(
            p.name for p in CAP_ROOT.iterdir() if p.is_dir() and p.name.isdigit() and len(p.name) == 8
        )
    # also include expected JPX weekdays with missing capture so exclusion is explicit
    extra = set()
    if days:
        a = datetime.strptime(days[0], "%Y%m%d")
        b = datetime.strptime(days[-1], "%Y%m%d")
        from datetime import timedelta

        d = a
        while d <= b:
            extra.add(d.strftime("%Y%m%d"))
            d += timedelta(days=1)
    all_days = sorted(set(days) | extra)
    rows: list[dict[str, Any]] = []
    for day in all_days:
        cap = find_capture_dir(day)
        uni = resolve_universe(day, cap)
        seq: dict[str, Any] = {}
        if cap is not None:
            # Prefer completeness/summary; full line scan only when summary missing.
            summary = _load_json(cap / "capture_summary.json") or _load_json(
                cap.parent / "capture_summary.json"
            )
            comp = _load_json(cap / "capture_completeness.json") or _load_json(
                cap.parent / "capture_completeness.json"
            )
            n_ev = int(summary.get("total_events") or summary.get("writer", {}).get("written") or 0)
            peek = _peek_seq(cap)
            seq = {
                "line_count": n_ev or peek.get("line_count") or 0,
                "first_seq": summary.get("first_sequence") if summary.get("first_sequence") is not None else peek.get("first_seq"),
                "last_seq": summary.get("last_sequence") if summary.get("last_sequence") is not None else peek.get("last_seq"),
                "contiguous_hint": (
                    int((summary.get("first_sequence") if summary.get("first_sequence") is not None else peek.get("first_seq")) or 0) == 1
                    and int(summary.get("dropped_count") or comp.get("dropped_event_count") or 0) == 0
                ),
                "first_event": summary.get("first_event_at") or summary.get("first_event_time") or peek.get("first_event"),
                "last_event": summary.get("last_event_at") or summary.get("last_event_time") or peek.get("last_event"),
                "size_bytes": peek.get("size_bytes") or 0,
            }
        klass = classify(day, cap, uni, seq)
        wd = datetime(int(day[:4]), int(day[4:6]), int(day[6:8])).weekday()
        jpx = wd < 5 and day not in KNOWN_HOLIDAYS
        reasons: list[str] = []
        if not jpx:
            reasons.append("NOT_JPX_TRADING_DAY" if day in KNOWN_HOLIDAYS else "WEEKEND")
        if cap is None:
            reasons.append("NO_CAPTURE")
        if not uni.get("resolved"):
            reasons.append(str(uni.get("reason") or "UNIVERSE_BINDING_UNRESOLVED"))
        if uni.get("resolved") and int(uni.get("universe_n") or 0) != 50:
            reasons.append(f"UNIVERSE_NOT_50 n={uni.get('universe_n')}")
        notes = uni.get("notes") or []
        if "registration_differs_from_frozen" in notes:
            reasons.append("REGISTRATION_MISMATCH_FROZEN")
        dropped = int(klass.get("dropped_event_count") or 0)
        if dropped > 0:
            reasons.append(f"dropped_event_count={dropped}")
        if klass.get("capture_class") == "INVALID":
            reasons.append(str(klass.get("exclusion_reason") or "INVALID_CAPTURE"))
        if klass.get("capture_class") == "MISSING":
            reasons.append("CAPTURE_MISSING")
        am, pm = bool(klass.get("am_coverage")), bool(klass.get("pm_coverage"))
        if cap is not None and not (am and pm):
            reasons.append("CAPTURE_WINDOW_INCOMPLETE")
        seq_fail = False
        if cap is not None and seq:
            first_seq = seq.get("first_seq")
            last_seq = seq.get("last_seq")
            n = int(seq.get("line_count") or 0)
            try:
                fs = int(first_seq) if first_seq is not None else None
            except (TypeError, ValueError):
                fs = None
            if fs is not None and fs != 1:
                seq_fail = True
                reasons.append(f"SEQUENCE_HOLE first_seq={first_seq}")
            if (
                last_seq is not None
                and n > 0
                and int(last_seq) != n
                and dropped > 0
            ):
                seq_fail = True
                reasons.append("SEQUENCE_HOLE SEQ_COUNT_NE_LAST_WITH_DROPS")
        # 50/50 + gap=0 + AM/PM: usable even if Paper was realtime-invalid
        fifty = bool(uni.get("resolved") and int(uni.get("universe_n") or 0) == 50)
        gap0 = dropped == 0 and not seq_fail
        causal_ok = bool(
            jpx
            and cap is not None
            and fifty
            and gap0
            and am
            and pm
            and klass.get("capture_class") in {"FULL", "PARTIAL", "DEGRADED"}
            and "REGISTRATION_MISMATCH_FROZEN" not in reasons
            and "INVALID_CAPTURE" not in "".join(reasons)
        )
        if klass.get("capture_class") == "INVALID":
            causal_ok = False
        row = {
            "date": day,
            "period": _period_split(day),
            "capture_path": str(cap) if cap else "",
            "capture_class": klass.get("capture_class"),
            "jpx_trading_day": jpx,
            "am_coverage": am,
            "pm_coverage": pm,
            "dropped_event_count": dropped,
            "universe_resolved": uni.get("resolved"),
            "universe_source": uni.get("source"),
            "universe_n": uni.get("universe_n"),
            "universe_symbols": uni.get("symbols") or [],
            "universe_notes": notes,
            "event_count": seq.get("line_count") or klass.get("event_hint") or 0,
            "first_seq": seq.get("first_seq"),
            "last_seq": seq.get("last_seq"),
            "sequence_continuity_hint": seq.get("contiguous_hint"),
            "first_event": klass.get("first_event") or seq.get("first_event"),
            "last_event": klass.get("last_event") or seq.get("last_event"),
            "exclusion_reason": ";".join(reasons) if reasons else str(klass.get("exclusion_reason") or ""),
            "fifty_fifty": fifty,
            "gap0": gap0,
            "replay_eligible": causal_ok,
        }
        rows.append(row)
    return rows
