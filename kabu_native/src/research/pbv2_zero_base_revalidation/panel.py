"""Build Watch50 candidate panel + price paths (causal joins only)."""
from __future__ import annotations

import csv
import json
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from research.pbv2_zero_base_revalidation.constants import (
    EVAL_BUCKET_SEC,
    EVENT_FEATURE_MAP,
    LANE_C_REQUIRED,
    NATIVE,
    NP_STEMS,
    NP_WINDOWS,
    PRICE_PATH_BUCKET_SEC,
    SUSPECT_BOARD_DAYS,
)
from research.pbv2_zero_base_revalidation.util import day_from_ts, fnum, parse_ts


@dataclass
class PricePoint:
    t: datetime
    px: float


@dataclass
class CandidateRow:
    day: str
    session: str
    symbol: str
    evaluation_time: datetime
    evaluation_event_id: str
    universe_source: str
    current_price: float
    current_price_time: Optional[datetime]
    board_time: Optional[datetime]
    board_age_sec: Optional[float]
    price_age_sec: Optional[float]
    pbv2_candidate: bool
    pbv2_score: Optional[float]
    pbv2_decision: bool
    reject_reason: str
    accept: bool
    cap_blocked: bool
    features: dict[str, Optional[float]] = field(default_factory=dict)
    board_quality: str = "UNKNOWN"
    board_source: str = "event"
    lane_c_complete: bool = False
    lane_c_any: bool = False
    evaluability: str = "COVERAGE_ONLY"
    session_bucket: str = "OTHER"  # AM | PM
    forward_return_evaluable: bool = False
    mfe_mae_evaluable: bool = False
    large_rise_evaluable: bool = False
    counterfactual_exit_evaluable: bool = False
    pnl_evaluable: bool = False
    # labels filled later
    forward: dict[str, Optional[float]] = field(default_factory=dict)
    cohort: str = "Normal"
    is_stop: bool = False
    is_np: bool = False
    is_winner: bool = False
    is_large_rise: bool = False
    cf_pnl: Optional[float] = None
    cf_pnl_5bps: Optional[float] = None
    cf_exit_reason: str = ""
    cf_hold_sec: Optional[float] = None
    actual_pnl: Optional[float] = None
    actual_pnl_5bps: Optional[float] = None
    actual_exit_reason: str = ""


def list_sessions(native: Path = NATIVE, *, max_per_day: int = 1) -> list[tuple[str, Path]]:
    """Backward-compatible wrapper → canonical AM+PM selection."""
    from research.pbv2_zero_base_revalidation.session_select import select_canonical_sessions

    sel = select_canonical_sessions(native)
    return [(d, p) for d, p, _b in sel["selected"]]


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in ("1", "true", "yes", "y")


def _extract_features(row: Mapping[str, Any]) -> dict[str, Optional[float]]:
    feats: dict[str, Optional[float]] = {}
    for fk, col in EVENT_FEATURE_MAP.items():
        feats[fk] = fnum(row.get(col))
    # late_chase_flag is boolean-ish; map to chase score proxy
    if feats.get("f_chase") is None and row.get("late_chase_flag") not in (None, ""):
        feats["f_chase"] = 1.0 if _truthy(row.get("late_chase_flag")) else 0.0
    return feats


def _board_quality(feats: Mapping[str, Optional[float]], day: str, row: Mapping[str, Any]) -> str:
    """Never promote TOP_ONLY / PARTIAL_L2 to FULL_L2 without true L2 depth fields."""
    imb = feats.get("f_imb")
    age = feats.get("f_board_age")
    if imb is None:
        return "MISSING"
    if abs(imb - 0.5) < 1e-12:
        return "FALLBACK_0_5"
    if age is not None and age > 5.0:
        return "STALE"
    has_depth_hint = any(
        str(row.get(k) or "").strip()
        for k in ("Buy1", "Sell1", "bid_qty", "ask_qty", "BidQty", "AskQty", "buy1", "sell1")
    )
    # NP complete alone is dynamic-board evidence, not FULL_L2 static depth.
    if has_depth_hint:
        if day in SUSPECT_BOARD_DAYS:
            return "PARTIAL_L2"
        return "FULL_L2"
    if day in SUSPECT_BOARD_DAYS:
        return "PARTIAL_L2"
    return "TOP_ONLY"


def _load_np_index(session: Path) -> dict[str, list[tuple[datetime, dict[str, Optional[float]]]]]:
    path = session / "np_pre_entry_features.jsonl"
    out: dict[str, list[tuple[datetime, dict[str, Optional[float]]]]] = defaultdict(list)
    if not path.exists():
        return out
    with path.open(encoding="utf-8", errors="replace") as f:
        for ln in f:
            ln = ln.strip()
            if not ln:
                continue
            try:
                o = json.loads(ln)
            except json.JSONDecodeError:
                continue
            sym = str(o.get("symbol") or "")
            ts = parse_ts(o.get("np_accepted_at") or o.get("accepted_at") or o.get("event_time"))
            if not sym or ts is None:
                continue
            feats: dict[str, Optional[float]] = {}
            for w in NP_WINDOWS:
                for stem in NP_STEMS:
                    key = f"{stem}_{w}s"
                    feats[f"f_{stem}_{w}"] = fnum(o.get(key))
            feats["np_feature_complete"] = 1.0 if _truthy(o.get("np_feature_complete")) else 0.0
            # causal: max_source_ts must be <= accepted_at if present
            max_src = parse_ts(o.get("np_max_source_ts"))
            if max_src is not None and max_src > ts:
                continue  # leakage row discarded
            out[sym].append((ts, feats))
    for sym in out:
        out[sym].sort(key=lambda x: x[0])
    return out


def _np_asof(
    index: Mapping[str, list[tuple[datetime, dict[str, Optional[float]]]]],
    symbol: str,
    eval_t: datetime,
) -> dict[str, Optional[float]]:
    arr = index.get(symbol) or []
    if not arr:
        return {}
    times = [t for t, _ in arr]
    i = bisect_right(times, eval_t) - 1
    if i < 0:
        return {}
    # only join if NP timestamp within 120s before eval (same state)
    if (eval_t - times[i]).total_seconds() > 120:
        return {}
    return dict(arr[i][1])


def _is_pbv2_candidate(row: Mapping[str, Any], score: Optional[float]) -> bool:
    """True only if the row was a PBv2-generated candidate (not merely Watch50-evaluated)."""
    if _truthy(row.get("gate_accept")):
        return True
    if score is not None and score >= 5:
        return True
    reason = str(
        row.get("final_reject_reason")
        or row.get("gate_reject_reason")
        or row.get("reject_reason")
        or ""
    ).lower()
    # Accepted by score/overlay path then blocked by portfolio / overlap / mainline band.
    if any(
        x in reason
        for x in (
            "max_entries_per_scan",
            "same_symbol_open",
            "flat_band_mainline",
            "reject_same_symbol",
            "cap",
        )
    ):
        return True
    internal = str(row.get("pbv2_internal_reason") or "").lower()
    # Score gate failed → not a PBv2 candidate
    if "entry_score_v2_below_threshold" in internal or "entry_score_v2_below_threshold" in reason:
        return False
    if "or_overlay_not_candidate" in reason:
        return False
    return False


def build_price_paths_and_panel(
    native: Path = NATIVE,
    *,
    bucket_sec: int = EVAL_BUCKET_SEC,
) -> tuple[list[CandidateRow], dict[tuple[str, str], list[PricePoint]], dict[str, Any]]:
    """Stream events → price paths + thinned candidate panel."""
    from research.pbv2_zero_base_revalidation.session_select import select_canonical_sessions

    sess_meta = select_canonical_sessions(native)
    sessions = [(d, p, b) for d, p, b in sess_meta["selected"]]
    price_paths: dict[tuple[str, str], list[PricePoint]] = defaultdict(list)
    # bucket key -> best row material (include session bucket to avoid AM/PM collapse)
    buckets: dict[tuple[str, str, str, int], dict[str, Any]] = {}
    accepts: dict[tuple[str, str, str], dict[str, Any]] = {}  # day,symbol,entry_time
    exits: dict[tuple[str, str, str], dict[str, Any]] = {}
    coverage: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "sessions": 0,
            "selected_sessions": "",
            "candidate_events": 0,
            "panel_rows": 0,
            "accepts": 0,
            "has_np": False,
            "has_market_capture": False,
            "pbv2_candidates": 0,
            "non_pbv2": 0,
            "am_panel_rows": 0,
            "pm_panel_rows": 0,
            "am_pbv2_candidates": 0,
            "pm_pbv2_candidates": 0,
            "am_accepts": 0,
            "pm_accepts": 0,
            "has_am": False,
            "has_pm": False,
        }
    )

    print(f"[pbv2_zb] sessions={len(sessions)} canonical_rule={sess_meta.get('canonical_rule')}", flush=True)
    price_bucket: dict[tuple[str, str, int], PricePoint] = {}
    for si, (day, sess, sess_bucket) in enumerate(sessions):
        coverage[day]["sessions"] += 1
        coverage[day]["selected_sessions"] = (
            (coverage[day]["selected_sessions"] + "," if coverage[day]["selected_sessions"] else "")
            + f"{sess.name}:{sess_bucket}"
        )
        if sess_bucket == "AM":
            coverage[day]["has_am"] = True
        if sess_bucket == "PM":
            coverage[day]["has_pm"] = True
        mc = native / "data" / "market_capture" / day
        if mc.exists():
            coverage[day]["has_market_capture"] = True
        np_index = _load_np_index(sess)
        if np_index:
            coverage[day]["has_np"] = True

        events_path = sess / "small_paper_events.csv"
        if not events_path.exists():
            continue
        print(f"[pbv2_zb] load {si+1}/{len(sessions)} {day} {sess_bucket} {sess.name}", flush=True)
        with events_path.open(encoding="utf-8", errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                et = str(row.get("event_type") or "")
                sym = str(row.get("symbol") or "")
                if not sym:
                    continue
                ts = parse_ts(row.get("event_time") or row.get("entry_time"))
                px = fnum(row.get("current_price") or row.get("entry_price"))
                if ts is not None and px is not None and px > 0:
                    pb = int(ts.timestamp() // PRICE_PATH_BUCKET_SEC)
                    price_bucket[(day, sym, pb)] = PricePoint(ts, px)

                if et == "accepted":
                    key = (day, sym, str(row.get("entry_time") or row.get("event_time") or ""))
                    accepts[key] = {**row, "_session_bucket": sess_bucket, "_session": sess.name}
                    coverage[day]["accepts"] += 1
                    if sess_bucket == "AM":
                        coverage[day]["am_accepts"] += 1
                    elif sess_bucket == "PM":
                        coverage[day]["pm_accepts"] += 1
                    continue
                if et == "observer_exit":
                    key = (day, sym, str(row.get("entry_time") or ""))
                    exits[key] = row
                    continue
                if et != "candidate":
                    continue

                coverage[day]["candidate_events"] += 1
                if ts is None or px is None or px <= 0:
                    continue

                feats = _extract_features(row)
                np_feats = _np_asof(np_index, sym, ts)
                for k, v in np_feats.items():
                    if k.startswith("f_np_") and v is not None:
                        feats[k] = v
                score = feats.get("f_pbv2")
                pbv2_cand = _is_pbv2_candidate(row, score)
                reject = str(
                    row.get("final_reject_reason")
                    or row.get("gate_reject_reason")
                    or row.get("reject_reason")
                    or ""
                )
                cap_blocked = "max_entries" in reject or "cap" in reject.lower()
                bq = _board_quality(feats, day, row)
                lane_c_any = any(feats.get(k) is not None for k in LANE_C_REQUIRED)
                lane_c_complete = all(feats.get(k) is not None for k in LANE_C_REQUIRED)
                # Prefer denser / higher-score state inside bucket (per AM/PM session)
                bucket = int(ts.timestamp() // bucket_sec)
                bkey = (day, sym, sess_bucket, bucket)
                priority = (
                    1 if _truthy(row.get("gate_accept")) else 0,
                    score if score is not None else -1.0,
                    1 if lane_c_complete else 0,
                    1 if pbv2_cand else 0,
                )
                prev = buckets.get(bkey)
                if prev is not None and prev["priority"] >= priority:
                    continue
                buckets[bkey] = {
                    "priority": priority,
                    "day": day,
                    "session": sess.name,
                    "session_bucket": sess_bucket,
                    "symbol": sym,
                    "evaluation_time": ts,
                    "evaluation_event_id": f"{day}:{sess.name}:{sym}:{ts.isoformat()}",
                    "universe_source": str(row.get("universe_bucket") or row.get("source_bucket") or "watch50"),
                    "current_price": px,
                    "current_price_time": parse_ts(row.get("event_time")),
                    "board_time": None,
                    "board_age_sec": feats.get("f_board_age"),
                    "price_age_sec": feats.get("f_price_age"),
                    "pbv2_candidate": pbv2_cand,
                    "pbv2_score": score,
                    "pbv2_decision": bool(score is not None and score >= 5) or _truthy(row.get("gate_accept")),
                    "reject_reason": reject,
                    "accept": False,
                    "cap_blocked": cap_blocked,
                    "features": feats,
                    "board_quality": bq,
                    "board_source": "np" if lane_c_any else "event",
                    "lane_c_complete": lane_c_complete,
                    "lane_c_any": lane_c_any,
                    "entry_time_key": str(row.get("entry_time") or ""),
                }

    # mark accepts onto nearest bucket rows within same day/symbol
    for (day, sym, et), row in accepts.items():
        ts = parse_ts(et) or parse_ts(row.get("event_time"))
        if ts is None:
            continue
        sess_bucket = str(row.get("_session_bucket") or "OTHER")
        bucket = int(ts.timestamp() // bucket_sec)
        bkey = (day, sym, sess_bucket, bucket)
        if bkey in buckets:
            buckets[bkey]["accept"] = True
            buckets[bkey]["pbv2_decision"] = True
            buckets[bkey]["pbv2_candidate"] = True
            ex = exits.get((day, sym, et))
            if ex:
                buckets[bkey]["actual_exit"] = ex
        else:
            px = fnum(row.get("current_price") or row.get("entry_price")) or 0.0
            feats = _extract_features(row)
            buckets[bkey] = {
                "priority": (2, 99.0, 1, 1),
                "day": day,
                "session": str(row.get("_session") or "accept_only"),
                "session_bucket": sess_bucket,
                "symbol": sym,
                "evaluation_time": ts,
                "evaluation_event_id": f"{day}:accept:{sym}:{ts.isoformat()}",
                "universe_source": str(row.get("universe_bucket") or "watch50"),
                "current_price": px,
                "current_price_time": ts,
                "board_time": None,
                "board_age_sec": feats.get("f_board_age"),
                "price_age_sec": feats.get("f_price_age"),
                "pbv2_candidate": True,
                "pbv2_score": feats.get("f_pbv2"),
                "pbv2_decision": True,
                "reject_reason": "",
                "accept": True,
                "cap_blocked": False,
                "features": feats,
                "board_quality": _board_quality(feats, day, row),
                "board_source": "event",
                "lane_c_complete": all(feats.get(k) is not None for k in LANE_C_REQUIRED),
                "lane_c_any": any(feats.get(k) is not None for k in LANE_C_REQUIRED),
                "entry_time_key": et,
                "actual_exit": exits.get((day, sym, et)),
            }

    # build downsampled price paths
    tmp: dict[tuple[str, str], list[PricePoint]] = defaultdict(list)
    for (day, sym, _pb), pt in price_bucket.items():
        tmp[(day, sym)].append(pt)
    price_paths = {}
    for k, pts in tmp.items():
        pts.sort(key=lambda p: p.t)
        price_paths[k] = pts
    print(f"[pbv2_zb] price_symbols={len(price_paths)} panel_buckets={len(buckets)}", flush=True)

    panel: list[CandidateRow] = []
    for bkey, m in buckets.items():
        feats = m["features"]
        dense_ok = any(feats.get(k) is not None for k in ("f_rise5", "f_mom", "f_near_high", "f_vwap", "f_pbv2"))
        evaluability = "FEATURE_EVALUABLE" if dense_ok else "COVERAGE_ONLY"
        sb = str(m.get("session_bucket") or "OTHER")
        row = CandidateRow(
            day=m["day"],
            session=m["session"],
            symbol=m["symbol"],
            evaluation_time=m["evaluation_time"],
            evaluation_event_id=m["evaluation_event_id"],
            universe_source=m["universe_source"],
            current_price=float(m["current_price"]),
            current_price_time=m.get("current_price_time"),
            board_time=m.get("board_time"),
            board_age_sec=m.get("board_age_sec"),
            price_age_sec=m.get("price_age_sec"),
            pbv2_candidate=bool(m["pbv2_candidate"]),
            pbv2_score=m.get("pbv2_score"),
            pbv2_decision=bool(m["pbv2_decision"]),
            reject_reason=str(m.get("reject_reason") or ""),
            accept=bool(m["accept"]),
            cap_blocked=bool(m["cap_blocked"]),
            features=feats,
            board_quality=str(m.get("board_quality") or "UNKNOWN"),
            board_source=str(m.get("board_source") or "event"),
            lane_c_complete=bool(m.get("lane_c_complete")),
            lane_c_any=bool(m.get("lane_c_any")),
            evaluability=evaluability,
            session_bucket=sb,
        )
        ex = m.get("actual_exit")
        if ex:
            ep = fnum(ex.get("entry_price")) or row.current_price
            xp = fnum(ex.get("exit_price") or ex.get("current_price"))
            if ep and xp:
                from research.pbv2_zero_base_revalidation.util import pnl_5bps, yen100

                row.actual_pnl = yen100(ep, xp)
                row.actual_pnl_5bps = pnl_5bps(ep, xp)
                row.actual_exit_reason = str(ex.get("exit_reason") or "")
        panel.append(row)
        coverage[row.day]["panel_rows"] += 1
        if sb == "AM":
            coverage[row.day]["am_panel_rows"] += 1
        elif sb == "PM":
            coverage[row.day]["pm_panel_rows"] += 1
        if row.pbv2_candidate:
            coverage[row.day]["pbv2_candidates"] += 1
            if sb == "AM":
                coverage[row.day]["am_pbv2_candidates"] += 1
            elif sb == "PM":
                coverage[row.day]["pm_pbv2_candidates"] += 1
        else:
            coverage[row.day]["non_pbv2"] += 1

    # Merge session audit into coverage rows
    audit_by_day = {r["day"]: r for r in sess_meta.get("audit_rows") or []}
    for day, cov in coverage.items():
        a = audit_by_day.get(day) or {}
        cov["live_session_count"] = a.get("live_session_count")
        cov["excluded_sessions"] = a.get("excluded_sessions")
        cov["selected_am"] = a.get("selected_am")
        cov["selected_pm"] = a.get("selected_pm")
        cov["coverage_ok_day"] = a.get("coverage_ok_day")

    panel.sort(key=lambda r: (r.day, r.evaluation_time, r.symbol))
    meta = {
        "n_sessions": len(sessions),
        "n_panel": len(panel),
        "n_price_symbols": len(price_paths),
        "n_accept_events": len(accepts),
        "coverage_by_day": dict(coverage),
        "bucket_sec": bucket_sec,
        "days": sorted(coverage.keys()),
        "session_select": {
            "canonical_rule": sess_meta.get("canonical_rule"),
            "session_coverage_pass": sess_meta.get("session_coverage_pass"),
            "coverage_blocked_days": sess_meta.get("coverage_blocked_days"),
            "audit_rows": sess_meta.get("audit_rows"),
            "n_selected": sess_meta.get("n_selected"),
        },
    }
    return panel, dict(price_paths), meta


def price_asof(path: list[PricePoint], t: datetime) -> Optional[float]:
    if not path:
        return None
    times = [p.t for p in path]
    i = bisect_right(times, t) - 1
    if i < 0:
        return None
    return path[i].px


def price_window(
    path: list[PricePoint],
    start: datetime,
    horizon_sec: float,
) -> list[PricePoint]:
    if not path:
        return []
    end = start + timedelta(seconds=horizon_sec)
    times = [p.t for p in path]
    i0 = bisect_right(times, start) - 1
    if i0 < 0:
        i0 = 0
        if path[0].t > end:
            return []
    out: list[PricePoint] = []
    for j in range(i0, len(path)):
        if path[j].t < start:
            # include last price at/before start as path origin
            continue
        if path[j].t > end:
            break
        out.append(path[j])
    # ensure origin point
    origin_px = price_asof(path, start)
    if origin_px is not None:
        if not out or out[0].t > start:
            out = [PricePoint(start, origin_px)] + out
    return out
