"""Session coverage for exact V1 capture replay. No prices are aggregated into returns."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.anchor_vs_event_driven.run_comparison import find_capture_dir
from research.symbol_setup_baseline_minimal_completion import DEVELOPMENT_CUTOFF, PROSPECTIVE_FROM
from research.symbol_setup_baseline_minimal_completion.isolation import NATIVE

ROOT = NATIVE / "data" / "market_capture"


def _hour(value: Any) -> float | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.hour + dt.minute / 60.0


def _summary(cap) -> dict[str, Any]:
    for path in (cap / "capture_summary.json", cap.parent / "capture_summary.json"):
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _parts(cap) -> list:
    return [p for p in sorted(cap.glob("push_part_*.jsonl")) if p.stat().st_size > 0]


def _first_event(cap) -> dict[str, Any]:
    for part in _parts(cap):
        with part.open(encoding="utf-8") as fh:
            for raw in fh:
                if raw.strip():
                    return json.loads(raw)
    return {}


def _last_event(cap) -> dict[str, Any]:
    parts = _parts(cap)
    if not parts:
        return {}
    path = parts[-1]
    with path.open("rb") as fh:
        fh.seek(0, 2)
        size = fh.tell()
        fh.seek(max(0, size - 65536))
        blob = fh.read().decode("utf-8", errors="ignore")
    for raw in reversed(blob.splitlines()):
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            continue
    return {}


def _probe(cap) -> dict[str, Any]:
    body = _first_event(cap)
    if not body:
        return {"probed": False, "reason": "NO_EVENT"}
    payload = body.get("payload") if isinstance(body.get("payload"), dict) else None
    if not isinstance(payload, dict):
        payload = body.get("original_payload") if isinstance(body.get("original_payload"), dict) else body
    keys = set(payload)
    needed = {
        "buy": "Buy1" in keys or "BidPrice" in keys,
        "ask": "Sell1" in keys or "AskPrice" in keys,
        "price": "CurrentPrice" in keys,
        "volume": "TradingVolume" in keys,
    }
    return {"probed": True, "fields_present": needed, "ok": all(needed.values())}


def coverage() -> dict[str, Any]:
    names = sorted(p.name for p in ROOT.iterdir() if p.is_dir() and len(p.name) == 8 and p.name.isdigit())
    rows = []
    for day in names:
        if day >= PROSPECTIVE_FROM:
            rows.append({"date": day, "status": "PROSPECTIVE_NOT_OPENED"})
            continue
        if day > DEVELOPMENT_CUTOFF:
            rows.append({"date": day, "status": "AFTER_DEVELOPMENT_CUTOFF_NOT_OPENED"})
            continue
        cap = find_capture_dir(day)
        if cap is None:
            rows.append({"date": day, "status": "INVALID_NO_PUSH"})
            continue
        summary = _summary(cap)
        first = summary.get("first_event_at") or summary.get("first_event_time") or summary.get("first_time")
        last = summary.get("last_event_at") or summary.get("last_event_time") or summary.get("last_time")
        if not first or not last:
            first = (_first_event(cap) or {}).get("received_at")
            last = (_last_event(cap) or {}).get("received_at")
        h0, h1 = _hour(first), _hour(last)
        am = h0 is not None and h1 is not None and h0 <= 11.5 and h1 >= 9.0
        status = "EXACT_CAPTURE_AM" if am else "PARTIAL_NO_AM_WINDOW"
        rows.append(
            {
                "date": day,
                "status": status,
                "capture": str(cap.relative_to(NATIVE)),
                "summary_present": bool(summary),
                "am_window": bool(am),
            }
        )
    eligible = [r["date"] for r in rows if r["status"] == "EXACT_CAPTURE_AM"]
    partial = [r["date"] for r in rows if str(r["status"]).startswith("PARTIAL")]
    invalid = [r["date"] for r in rows if r["status"] == "INVALID_NO_PUSH"]
    surface = list(ELIGIBLE_DAYS)
    missing_surface = [d for d in surface if d not in eligible]
    probe = {"probed": False}
    if surface and surface[0] in eligible:
        probe = _probe(find_capture_dir(surface[0]))
        probe["date"] = surface[0]
    ready = not missing_surface and bool(probe.get("ok"))
    return {
        "development_cutoff": DEVELOPMENT_CUTOFF,
        "capture_dir_n": len(names),
        "eligible_session_n": len(eligible),
        "eligible_dates": eligible,
        "eligible_range": [eligible[0], eligible[-1]] if eligible else [],
        "partial_dates": partial,
        "invalid_dates": invalid,
        "after_cutoff_not_opened": [r["date"] for r in rows if r["status"] == "AFTER_DEVELOPMENT_CUTOFF_NOT_OPENED"],
        "prospective_not_opened": [r["date"] for r in rows if r["status"] == "PROSPECTIVE_NOT_OPENED"],
        "v1_research_days": surface,
        "v1_research_days_missing": missing_surface,
        "board_coverage": "sealed push capture Buy1/Sell1 or Bid/Ask on the probed development session",
        "quote_coverage": "same push stream; AM overlap taken from capture_summary, not from a minute-bar store",
        "fill_window_coverage": "5-second ask-cross requires the same quote stream. No OHLC fill is inferred.",
        "exit_path_coverage": "post-fill quotes exist only inside the same sealed capture through the AM summary end",
        "full_calendar_20240917_20260911_usable": False,
        "reason_calendar_unusable": "Capture on disk begins in 202607. Earlier minute history has no sealed board stream.",
        "schema_probe": probe,
        "EXACT_V1_REPLAY_DATA_READY": ready,
        "replay_universe": "V1 ELIGIBLE_DAYS whose sealed capture covers the AM window",
        "rows": rows,
    }
