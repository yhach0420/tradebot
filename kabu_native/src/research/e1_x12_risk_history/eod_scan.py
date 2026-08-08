"""E1_X12 EOD quality scan for a RISK_INFRASTRUCTURE_ONLY day — real files only."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import parse_ts

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]

AM_START, AM_END = 9 * 3600, 11 * 3600 + 30 * 60  # seconds from midnight JST
PM_START, PM_END = 12 * 3600 + 30 * 60, 15 * 3600 + 30 * 60


def _day_dash(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _sec_of_day(ts: datetime) -> float:
    local = ts.astimezone(JST)
    return local.hour * 3600 + local.minute * 60 + local.second + local.microsecond / 1e6


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_day_push_jsonl(day: str = "20260805") -> dict[str, Any]:
    """Stream push_jsonl/{YYYY-MM-DD}/*.jsonl and compute EOD quality metrics."""
    rd = NATIVE / "data" / "push_jsonl" / _day_dash(day)
    if not rd.is_dir():
        return {"day": day, "error": "PUSH_DIR_MISSING", "path": str(rd)}

    files = sorted(rd.glob("*.jsonl"))
    file_meta = []
    # aggregate counters
    n_events = 0
    n_bid = n_ask = n_bid_qty = n_ask_qty = 0
    n_bid_time = n_ask_time = n_cur_px = n_cur_time = n_prev = 0
    n_am = n_pm = 0
    n_am_bid = n_pm_bid = n_am_ask = n_pm_ask = 0
    first_ts = None
    last_ts = None
    prev_t_by_sym: dict[str, float] = {}
    inversions = 0
    dup_same_ts = 0
    gaps: list[float] = []
    symbols = set()
    ref_syms = set()

    for fp in files:
        sym = fp.stem.replace(".T", "").replace(".t", "")
        symbols.add(sym)
        sha = _sha256_file(fp)
        file_meta.append({
            "path": str(fp),
            "size": fp.stat().st_size,
            "sha256": sha,
            "symbol": sym,
        })
        last_line_t = None
        with fp.open("rb") as f:
            for lineb in f:
                try:
                    d = json.loads(lineb)
                except Exception:
                    continue
                recv = parse_ts(d.get("recorded_at"))
                if recv is None:
                    continue
                if recv.tzinfo is None:
                    recv = recv.replace(tzinfo=JST)
                t = recv.timestamp()
                if first_ts is None or recv < first_ts:
                    first_ts = recv
                if last_ts is None or recv > last_ts:
                    last_ts = recv
                n_events += 1
                sod = _sec_of_day(recv)
                in_am = AM_START <= sod <= AM_END
                in_pm = PM_START <= sod <= PM_END
                if in_am:
                    n_am += 1
                if in_pm:
                    n_pm += 1

                p = d.get("payload") or {}
                buy1 = p.get("Buy1") or {}
                sell1 = p.get("Sell1") or {}
                bid = _f(buy1.get("Price"))
                ask = _f(sell1.get("Price"))
                bq = _f(buy1.get("Qty"))
                aq = _f(sell1.get("Qty"))
                if bid is not None:
                    n_bid += 1
                    if in_am:
                        n_am_bid += 1
                    if in_pm:
                        n_pm_bid += 1
                if ask is not None:
                    n_ask += 1
                    if in_am:
                        n_am_ask += 1
                    if in_pm:
                        n_pm_ask += 1
                if bq is not None:
                    n_bid_qty += 1
                if aq is not None:
                    n_ask_qty += 1
                if p.get("BidTime"):
                    n_bid_time += 1
                if p.get("AskTime"):
                    n_ask_time += 1
                if _f(p.get("CurrentPrice")) is not None:
                    n_cur_px += 1
                if p.get("CurrentPriceTime"):
                    n_cur_time += 1
                if _f(p.get("PreviousClose")) is not None:
                    n_prev += 1
                    ref_syms.add(sym)

                # sequence / gap — only within same session continuum (exclude lunch)
                prev = prev_t_by_sym.get(sym)
                if prev is not None:
                    dt = t - prev
                    if dt < 0:
                        inversions += 1
                    elif dt == 0:
                        dup_same_ts += 1
                    else:
                        # skip lunch-crossing gaps (AM→PM)
                        prev_recv = datetime.fromtimestamp(prev, tz=JST)
                        prev_sod = _sec_of_day(prev_recv)
                        crosses_lunch = prev_sod <= AM_END and sod >= PM_START
                        if not crosses_lunch:
                            gaps.append(dt)
                prev_t_by_sym[sym] = t
                last_line_t = t

    def cov(num: int) -> float:
        return (num / n_events) if n_events else 0.0

    am_present = n_am > 0
    pm_present = n_pm > 0
    board_time_cov = cov(min(n_bid_time, n_ask_time))  # conservative joint proxy
    # better: both BidTime and AskTime present rate — approximate with mean
    board_time_cov = ((n_bid_time + n_ask_time) / 2 / n_events) if n_events else 0.0
    ref_cov = (len(ref_syms) / len(symbols)) if symbols else 0.0

    longest_gap = max(gaps) if gaps else None
    # "重大なcapture gap" — no single symbol gap > 30 min during session (heuristic)
    major_gap = bool(longest_gap is not None and longest_gap > 1800)

    reasons = []
    if not am_present:
        reasons.append("AM_MISSING")
    if not pm_present:
        reasons.append("PM_MISSING")
    if cov(n_bid) < 0.95:
        reasons.append("BID_COV_LT_095")
    if cov(n_ask) < 0.95:
        reasons.append("ASK_COV_LT_095")
    if cov(n_bid_qty) < 0.95:
        reasons.append("BID_QTY_COV_LT_095")
    if cov(n_ask_qty) < 0.95:
        reasons.append("ASK_QTY_COV_LT_095")
    if board_time_cov < 0.90:
        reasons.append("BOARD_TIME_COV_LT_090")
    if ref_cov < 0.90:
        reasons.append("REF_COV_LT_090")
    if not file_meta:
        reasons.append("NO_RAW_SHA")
    if major_gap:
        reasons.append("MAJOR_CAPTURE_GAP")

    quality = "RISK_HISTORY_DAY_VALID" if not reasons else "RISK_HISTORY_DAY_INVALID"

    # combined raw sha of paths+sizes+per-file sha list
    catalog_sha = hashlib.sha256(
        json.dumps([(m["path"], m["size"], m["sha256"]) for m in file_meta], sort_keys=False).encode()
    ).hexdigest()

    return {
        "day": day,
        "source": "push_jsonl",
        "path": str(rd),
        "am_present": am_present,
        "pm_present": pm_present,
        "n_am_events": n_am,
        "n_pm_events": n_pm,
        "capture_start": first_ts.isoformat() if first_ts else None,
        "capture_end": last_ts.isoformat() if last_ts else None,
        "first_event_timestamp": first_ts.isoformat() if first_ts else None,
        "last_event_timestamp": last_ts.isoformat() if last_ts else None,
        "symbols_n": len(symbols),
        "events_n": n_events,
        "raw_files_n": len(file_meta),
        "raw_total_bytes": sum(m["size"] for m in file_meta),
        "raw_catalog_sha256": catalog_sha,
        "raw_files": file_meta,
        "coverage": {
            "best_bid": cov(n_bid),
            "best_ask": cov(n_ask),
            "best_bid_qty": cov(n_bid_qty),
            "best_ask_qty": cov(n_ask_qty),
            "BidTime": cov(n_bid_time),
            "AskTime": cov(n_ask_time),
            "board_time": board_time_cov,
            "CurrentPrice": cov(n_cur_px),
            "CurrentPriceTime": cov(n_cur_time),
            "PreviousClose": cov(n_prev),
            "reference_price_symbol": ref_cov,
            "am_best_bid": (n_am_bid / n_am) if n_am else None,
            "am_best_ask": (n_am_ask / n_am) if n_am else None,
            "pm_best_bid": (n_pm_bid / n_pm) if n_pm else None,
            "pm_best_ask": (n_pm_ask / n_pm) if n_pm else None,
        },
        "duplicate_event_rate": (dup_same_ts / n_events) if n_events else 0.0,
        "timestamp_inversion_n": inversions,
        "sequence_inversion_n": inversions,
        "longest_capture_gap_sec": longest_gap,
        "major_capture_gap": major_gap,
        "quality_status": quality,
        "quality_reasons": reasons,
    }
