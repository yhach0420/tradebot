"""Phase A-1: raw + canonical data / feature-source inventory (read-only).

Per day and per session (AM/PM) collects: raw/canonical counts, symbols,
start/end, timestamp inversions, duplicates, gaps, stale rate, per-field
missing rates, source-vs-ingress lag stats, known excluded windows and the
usable field set. Missing values are never filled (no future values, no daily
close, no interpolation).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, time as dtime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .source_manifest import raw_day_dir

JST = ZoneInfo("Asia/Tokyo")

AM_START, AM_END = dtime(9, 0), dtime(11, 30)
PM_START, PM_END = dtime(12, 30), dtime(15, 30)
STALE_LAG_SEC = 5.0

# Raw payload fields whose session-hours coverage is measured.
TRACKED_FIELDS = (
    "CurrentPrice", "CurrentPriceTime", "TradingVolume", "TradingValue", "VWAP",
    "HighPrice", "LowPrice", "OpeningPrice",
    "MarketOrderBuyQty", "MarketOrderSellQty", "OverSellQty", "UnderBuyQty",
)
BOARD_SIDES = ("Buy", "Sell")
BOARD_LEVELS = 10


def _session_of(ts: datetime) -> Optional[str]:
    t = ts.timetz().replace(tzinfo=None)
    if AM_START <= t <= AM_END:
        return "AM"
    if PM_START <= t <= PM_END:
        return "PM"
    return None


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _new_session_acc() -> dict[str, Any]:
    return {
        "raw_events": 0,
        "symbols": set(),
        "first_ts": None,
        "last_ts": None,
        "ts_inversions": 0,
        "duplicates": 0,
        "stale_events": 0,
        "lag_samples": [],
        "field_nonnull": {f: 0 for f in TRACKED_FIELDS},
        "board_full_nonnull": 0,   # all 10 levels x both sides have Price&Qty
        "board_top1_nonnull": 0,   # Buy1 & Sell1 have Price&Qty
        "quote_nonnull": 0,        # canonical bid(Buy1)/ask(Sell1) both > 0
    }


def _finalize_session(acc: dict[str, Any]) -> dict[str, Any]:
    n = acc["raw_events"]
    lags = sorted(acc["lag_samples"])

    def _pct(q: float) -> Optional[float]:
        if not lags:
            return None
        i = min(len(lags) - 1, max(0, int(q * (len(lags) - 1))))
        return round(lags[i], 3)

    out = {
        "raw_events": n,
        "symbols_n": len(acc["symbols"]),
        "first_ts": acc["first_ts"].isoformat() if acc["first_ts"] else None,
        "last_ts": acc["last_ts"].isoformat() if acc["last_ts"] else None,
        "ts_inversions": acc["ts_inversions"],
        "duplicates": acc["duplicates"],
        "stale_events": acc["stale_events"],
        "stale_rate": round(acc["stale_events"] / n, 6) if n else None,
        "source_ingress_lag_sec": {"p50": _pct(0.50), "p90": _pct(0.90), "p99": _pct(0.99)},
        "field_missing_rate": {
            f: round(1.0 - acc["field_nonnull"][f] / n, 6) if n else None for f in TRACKED_FIELDS
        },
        "quote_coverage": round(acc["quote_nonnull"] / n, 6) if n else None,
        "board_top1_coverage": round(acc["board_top1_nonnull"] / n, 6) if n else None,
        "board_full10_coverage": round(acc["board_full_nonnull"] / n, 6) if n else None,
    }
    return out


def inventory_raw_day(native_root: Path, day: str) -> dict[str, Any]:
    """Stream all raw symbol files of one day; per-session quality stats."""
    rd = raw_day_dir(native_root, day)
    sessions = {"AM": _new_session_acc(), "PM": _new_session_acc(), "OFF": _new_session_acc()}
    per_symbol_counts: dict[str, int] = {}
    parse_errors = 0

    for fp in sorted(rd.glob("*.jsonl")):
        sym = fp.stem
        prev_ts: Optional[datetime] = None
        prev_line_hash: Optional[str] = None
        cnt = 0
        with fp.open("rb") as f:
            for lineb in f:
                cnt += 1
                try:
                    d = json.loads(lineb)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    parse_errors += 1
                    continue
                rec = _parse_iso(d.get("recorded_at"))
                if rec is None:
                    parse_errors += 1
                    continue
                sk = _session_of(rec) or "OFF"
                acc = sessions[sk]
                acc["raw_events"] += 1
                acc["symbols"].add(sym)
                if acc["first_ts"] is None or rec < acc["first_ts"]:
                    acc["first_ts"] = rec
                if acc["last_ts"] is None or rec > acc["last_ts"]:
                    acc["last_ts"] = rec
                if prev_ts is not None and rec < prev_ts:
                    acc["ts_inversions"] += 1
                prev_ts = rec
                lh = hashlib.sha256(lineb).hexdigest()
                if lh == prev_line_hash:
                    acc["duplicates"] += 1
                prev_line_hash = lh

                p = d.get("payload") or {}
                for fld in TRACKED_FIELDS:
                    if p.get(fld) is not None:
                        acc["field_nonnull"][fld] += 1
                b1 = p.get("Buy1") or {}
                s1 = p.get("Sell1") or {}
                b1ok = b1.get("Price") is not None and b1.get("Qty") is not None
                s1ok = s1.get("Price") is not None and s1.get("Qty") is not None
                if b1ok and s1ok:
                    acc["board_top1_nonnull"] += 1
                    bb, sa = float(b1["Price"] or 0), float(s1["Price"] or 0)
                    if bb > 0 and sa > 0:
                        acc["quote_nonnull"] += 1
                full = True
                for side in BOARD_SIDES:
                    for lv in range(1, BOARD_LEVELS + 1):
                        lvd = p.get(f"{side}{lv}") or {}
                        if lvd.get("Price") is None or lvd.get("Qty") is None:
                            full = False
                            break
                    if not full:
                        break
                if full:
                    acc["board_full_nonnull"] += 1

                # source timestamp vs ingress: freshest of Bid/Ask/CurrentPrice times
                src_ts = None
                for key in ("BidTime", "AskTime", "CurrentPriceTime"):
                    t = _parse_iso(p.get(key))
                    if t is not None and (src_ts is None or t > src_ts):
                        src_ts = t
                if src_ts is not None and sk in ("AM", "PM"):
                    lag = (rec - src_ts).total_seconds()
                    acc["lag_samples"].append(lag)
                    if lag > STALE_LAG_SEC:
                        acc["stale_events"] += 1
        per_symbol_counts[sym] = cnt

    return {
        "day": day,
        "raw_symbols_n": len(per_symbol_counts),
        "raw_total_lines": int(sum(per_symbol_counts.values())),
        "parse_errors": parse_errors,
        "per_symbol_lines": per_symbol_counts,
        "sessions": {k: _finalize_session(v) for k, v in sessions.items()},
    }


def inventory_canonical_day(native_root: Path, day: str) -> dict[str, Any]:
    """Canonical stats via existing read-only cache (no re-normalization writes)."""
    import small_paper.e1_x5_canonical_replay as cr

    from .source_manifest import canonical_cache_dir

    cd = canonical_cache_dir()
    for suffix in ("events_slim_v3.pkl.gz", "gap_map.json", "normalize_report.json"):
        if not (cd / f"{day}_{suffix}").is_file():
            raise SystemExit(f"FAIL: canonical cache missing {day}_{suffix} (read-only input)")
    events, report = cr.normalize_day(native_root, day, cache_dir=cd, use_cache=True)
    per_session = {"AM": 0, "PM": 0, "OFF": 0}
    syms: dict[str, set] = {"AM": set(), "PM": set()}
    for e in events:
        sk = _session_of(e.ts) or "OFF"
        per_session[sk] += 1
        if sk in syms:
            syms[sk].add(str(e.symbol))
    gaps = getattr(report, "gaps", []) or []
    out = {
        "canonical_events": len(events),
        "canonical_by_session": per_session,
        "canonical_symbols": {k: len(v) for k, v in syms.items()},
        "normalize_sessions": list(getattr(report, "sessions", []) or []),
        "gap_n": len(gaps),
        "gap_max_sec": max((float(g.get("gap_sec") or 0) for g in gaps), default=0.0),
        "canonical_duplicate_keys": int(getattr(report, "duplicate_keys", 0) or 0),
        "canonical_ts_regressions": int(
            getattr(report, "timestamp_regressions_in_file_order", 0) or 0
        ),
    }
    del events
    return out


def known_excluded_windows() -> dict[str, Any]:
    """Historical (read-only) analysis-mask exclusions from the stage-1 run."""
    fp = (
        Path.home()
        / "e1x6_research_store" / "plan21_work"
        / "e1x6_p21_20260802_204337_49eabae8" / "capture_metas.json"
    )
    if not fp.is_file():
        return {"available": False}
    metas = json.loads(fp.read_text(encoding="utf-8"))
    included = {(m["day"], m["am_pm"]): m for m in metas}
    out = {}
    for day in sorted({m["day"] for m in metas}):
        row = {}
        for ap in ("AM", "PM"):
            m = included.get((day, ap))
            row[ap] = (
                {"included": True, "window_id": m["window_id"], "quality_class": m["quality_class"]}
                if m
                else {"included": False, "reason": "EXCLUDED_BY_ANALYSIS_MASK_STAGE1"}
            )
        out[day] = row
    return {"available": True, "windows": out, "source": str(fp)}
