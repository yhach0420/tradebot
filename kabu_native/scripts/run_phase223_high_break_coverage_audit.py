#!/usr/bin/env python3
"""
Phase223: High-break coverage audit (review only).

Investigate why high_break_count fires on only ~30/2503 trades.
"""

from __future__ import annotations

import importlib.util
import json
import math
import statistics
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase223_high_break_coverage_audit.json"

LOOKBACK_SEC = 600.0


def _load_module(name: str, rel_path: str) -> Any:
    path = REPO / rel_path
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    sys.path[:0] = [str(REPO), str(REPO / "kabu_native" / "src")]
    spec.loader.exec_module(mod)
    return mod


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _boolish(val: Any) -> bool:
    return str(val or "").lower() in ("true", "1", "yes")


def _pearson(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx <= 0 or dy <= 0:
        return None
    return round(num / (dx * dy), 4)


def _find_push_jsonl(push_dir: Path, symbol: str) -> Optional[Path]:
    sym = symbol.replace(".T", "")
    for name in (f"{symbol}.jsonl", f"{sym}.jsonl"):
        p = push_dir / name
        if p.is_file():
            return p
    return None


def _high_break_count(window: list[tuple[float, float]]) -> int:
    if len(window) < 2:
        return 0
    running = window[0][1]
    count = 0
    for _, px in window[1:]:
        if px > running * 1.0001:
            count += 1
            running = px
    return count


def _high_break_recent(ring: list[tuple[float, float]], entry_ts: float, entry_px: float) -> bool:
    cur = [(t, px) for t, px in ring if entry_ts - 300 <= t <= entry_ts]
    prev = [(t, px) for t, px in ring if entry_ts - 600 <= t < entry_ts - 300]
    if len(cur) < 2 or len(prev) < 2 or entry_px <= 0:
        return False
    m5 = max(px for _, px in cur)
    m5_prev = max(px for _, px in prev)
    if m5 <= m5_prev * 1.0001:
        return False
    if entry_px < m5 * 0.998:
        return False
    last_high_ts = max(t for t, px in cur if px >= m5 * 0.998)
    return (entry_ts - last_high_ts) <= 60.0


def _load_ring_phase221_style_from_ticks(ticks: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Replicate Phase221 _get_price_ring: stream ticks, trailing 660s retained."""
    from small_paper.extended_entry_shadow import append_price_tick

    ring: list[tuple[float, float]] = []
    for ts, px in ticks:
        append_price_tick(ring, ts=ts, px=float(px))
    return ring


def _load_ring_phase221_style(path: Path, mod: Any) -> list[tuple[float, float]]:
    return _load_ring_phase221_style_from_ticks(_load_ticks_file(path, mod))


def _high_break_count_ring(ring: list[tuple[float, float]], entry_ts: float, lookback: float = LOOKBACK_SEC) -> int:
    window = [(t, px) for t, px in ring if entry_ts - lookback <= t <= entry_ts]
    return _high_break_count(window)


def _load_ticks_file(path: Path, mod: Any) -> list[tuple[float, float]]:
    ticks: list[tuple[float, float]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = mod._parse_ts(str(rec.get("recorded_at") or ""))
            px = _float((rec.get("payload") or {}).get("CurrentPrice"))
            if px and px > 0:
                ticks.append((ts, float(px)))
    return ticks


def _audit_trade(
    mod: Any,
    row: dict[str, Any],
    acc: dict[str, Any],
    tick_cache: dict[str, list[tuple[float, float]]],
    ring_cache: dict[str, list[tuple[float, float]]],
) -> dict[str, Any]:
    sym = str(row.get("symbol") or "")
    entry_time = str(row.get("entry_time") or "")
    entry_ts = mod._parse_ts(entry_time)
    entry_day = str(row.get("day_stamp") or "")
    session_rel = str(row.get("session_id") or "")
    entry_px = _float(row.get("current_price")) or _float(acc.get("current_price"))

    push_dir = mod._push_dir_for_day(entry_day) or mod._push_dir(session_rel)
    push_dir_exists = bool(push_dir and push_dir.is_dir())

    out: dict[str, Any] = {
        "push_dir": str(push_dir) if push_dir else None,
        "push_dir_exists": push_dir_exists,
        "entry_px_present": entry_px is not None and entry_px > 0,
        "jsonl_path": None,
        "jsonl_exists": False,
        "ticks_total_file": 0,
        "ticks_before_entry": 0,
        "ticks_in_600s_window": 0,
        "high_break_count": 0,
        "high_break_count_full_jsonl": 0,
        "high_break_count_phase221_ring": 0,
        "phase221_ring_ticks_before_entry": 0,
        "phase221_ring_ticks_in_600s": 0,
        "hb_zero_reason": None,
        "high_break_recent_recomputed": None,
        "new_high_ratio": None,
    }

    if not push_dir_exists:
        out["hb_zero_reason"] = "no_push_dir"
        return out
    if not entry_px or entry_px <= 0:
        out["hb_zero_reason"] = "missing_entry_price"
        return out

    jpath = _find_push_jsonl(push_dir, sym)
    if not jpath:
        out["hb_zero_reason"] = "no_symbol_jsonl"
        return out

    out["jsonl_path"] = str(jpath)
    out["jsonl_exists"] = True

    cache_key = str(jpath)
    if cache_key not in tick_cache:
        tick_cache[cache_key] = _load_ticks_file(jpath, mod)
    ticks_all = tick_cache[cache_key]

    out["ticks_total_file"] = len(ticks_all)
    before = [(t, px) for t, px in ticks_all if t <= entry_ts]
    out["ticks_before_entry"] = len(before)
    window = [(t, px) for t, px in before if entry_ts - LOOKBACK_SEC <= t <= entry_ts]
    out["ticks_in_600s_window"] = len(window)

    if len(before) == 0:
        out["hb_zero_reason"] = "jsonl_no_ticks_before_entry"
        return out
    if len(window) < 2:
        out["hb_zero_reason"] = "insufficient_window_ticks"
        hb = 0
    else:
        hb = _high_break_count(window)
        out["high_break_count"] = hb
        out["high_break_count_full_jsonl"] = hb
        out["hb_zero_reason"] = "computed_zero_new_highs" if hb == 0 else None
        out["new_high_ratio"] = round(hb / max(1, len(window) - 1), 4)
        ring_slice = before
        out["high_break_recent_recomputed"] = _high_break_recent(ring_slice, entry_ts, float(entry_px))

    if cache_key not in ring_cache:
        ring_cache[cache_key] = _load_ring_phase221_style_from_ticks(ticks_all)
    ring_p221 = ring_cache[cache_key]
    ring_at_entry = ring_p221[: bisect_right([t for t, _ in ring_p221], entry_ts)]
    out["phase221_ring_ticks_before_entry"] = len(ring_at_entry)
    p221_window = [(t, px) for t, px in ring_at_entry if entry_ts - LOOKBACK_SEC <= t <= entry_ts]
    out["phase221_ring_ticks_in_600s"] = len(p221_window)
    if len(p221_window) >= 2:
        out["high_break_count_phase221_ring"] = _high_break_count(p221_window)
    elif len(before) > 0:
        out["high_break_count_phase221_ring"] = 0
        if out.get("hb_zero_reason") is None and hb == 0:
            pass
        if hb >= 1 and out["high_break_count_phase221_ring"] == 0:
            out["phase221_ring_gap"] = "full_jsonl_has_hb_ring_empty_or_flat"

    if len(window) >= 2 and out.get("hb_zero_reason") is None:
        out["high_break_count"] = hb
    elif len(window) >= 2:
        out["high_break_count"] = hb

    return out


def _breakout_distance_from_accept(acc: dict[str, Any]) -> Optional[float]:
    v = _float(acc.get("entry_near_day_high_pct"))
    if v is not None:
        return v
    return None


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p217 = _load_module("phase217_p223", "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py")
    mod = p217._load_phase213c_module()

    print("loading trades...", flush=True)
    rows = p217._build_all(mod)

    accept_cache: dict[str, dict[tuple[str, str], dict[str, Any]]] = {}
    tick_cache: dict[str, list[tuple[float, float]]] = {}
    ring_cache: dict[str, list[tuple[float, float]]] = {}
    enriched: list[dict[str, Any]] = []

    for i, r in enumerate(rows):
        session_rel = str(r.get("session_id") or "")
        if session_rel and session_rel not in accept_cache:
            sdir = mod.BASE / session_rel
            acc_map: dict[tuple[str, str], dict[str, Any]] = {}
            for ev in mod._load_events(sdir):
                if ev.get("event_type") == "accepted":
                    acc_map[(str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))] = ev
            accept_cache[session_rel] = acc_map

        key = (str(r.get("symbol") or ""), str(r.get("entry_time") or ""))
        acc = accept_cache.get(session_rel, {}).get(key, {})
        audit = _audit_trade(mod, r, acc, tick_cache, ring_cache)

        row = {
            **r,
            **audit,
            "entry_high_break_recent_logged": acc.get("entry_high_break_recent"),
            "entry_high_break_recent_log": _boolish(acc.get("entry_high_break_recent"))
            if acc.get("entry_high_break_recent") is not None
            else None,
            "entry_near_day_high_pct": _float(acc.get("entry_near_day_high_pct")),
            "breakout_distance_pct": _breakout_distance_from_accept(acc),
            "entry_rise_5min_logged": _float(acc.get("entry_rise_5min_pct")) or _float(r.get("entry_rise_5min_pct")),
            "max_continuation_duration": _float(r.get("max_continuation_duration"))
            or _float(acc.get("max_continuation_duration")),
        }
        enriched.append(row)
        if (i + 1) % 500 == 0:
            print(f"  audited {i+1}/{len(rows)}", flush=True)

    hb_counts = Counter(int(r.get("high_break_count_full_jsonl") or r.get("high_break_count") or 0) for r in enriched)
    p221_hb_counts = Counter(int(r.get("high_break_count_phase221_ring") or 0) for r in enriched)
    zero_reasons = Counter(
        str(r.get("hb_zero_reason") or "hb_positive")
        for r in enriched
        if int(r.get("high_break_count_full_jsonl") or r.get("high_break_count") or 0) == 0
    )

    # hb=0 split: data vs true zero (full jsonl method)
    hb0 = [r for r in enriched if int(r.get("high_break_count_full_jsonl") or r.get("high_break_count") or 0) == 0]
    data_missing_reasons = {
        "no_push_dir",
        "no_symbol_jsonl",
        "missing_entry_price",
        "jsonl_no_ticks_before_entry",
        "insufficient_window_ticks",
    }
    hb0_data = [r for r in hb0 if r.get("hb_zero_reason") in data_missing_reasons]
    hb0_true = [r for r in hb0 if r.get("hb_zero_reason") == "computed_zero_new_highs"]

    ring_ok = [r for r in enriched if int(r.get("ticks_in_600s_window") or 0) >= 2]
    p221_ring_ok = [r for r in enriched if int(r.get("phase221_ring_ticks_in_600s") or 0) >= 2]
    p221_hb0 = [r for r in enriched if int(r.get("high_break_count_phase221_ring") or 0) == 0]
    p221_hb0_ring_empty = [r for r in p221_hb0 if int(r.get("phase221_ring_ticks_in_600s") or 0) < 2]
    p221_hb0_true = [r for r in p221_hb0 if int(r.get("phase221_ring_ticks_in_600s") or 0) >= 2]
    jsonl_hb_pos_p221_zero = [
        r
        for r in enriched
        if int(r.get("high_break_count_full_jsonl") or 0) >= 1
        and int(r.get("high_break_count_phase221_ring") or 0) == 0
    ]

    # Correlations on ring-ok subset
    corr_pairs: list[tuple[float, float, float]] = []
    for r in ring_ok:
        hb = float(r.get("high_break_count_full_jsonl") or r.get("high_break_count") or 0)
        r5 = _float(r.get("entry_rise_5min_logged"))
        dur = _float(r.get("max_continuation_duration"))
        if r5 is not None:
            corr_pairs.append((hb, r5, dur or float("nan")))

    hb_r5 = [(a, b) for a, b, _ in corr_pairs if not math.isnan(b)]
    hb_dur = [(a, c) for a, b, c in corr_pairs if c is not None and not math.isnan(c)]

    # Alternative indicators coverage
    log_hb_recent = [r for r in enriched if r.get("entry_high_break_recent_logged") is not None]
    log_near_high = [r for r in enriched if r.get("entry_near_day_high_pct") is not None]
    recomp_recent = [r for r in enriched if r.get("high_break_recent_recomputed") is not None]

    hb_ge1_rows = [r for r in enriched if int(r.get("high_break_count_full_jsonl") or r.get("high_break_count") or 0) >= 1]
    p221_hb_ge1_rows = [r for r in enriched if int(r.get("high_break_count_phase221_ring") or 0) >= 1]

    def _alt_capture(rows_sub: list[dict], pred) -> dict[str, Any]:
        n = len(rows_sub)
        if not n:
            return {"count": 0, "rate": None}
        c = sum(1 for r in rows_sub if pred(r))
        return {"count": c, "rate": round(c / n, 4)}

    alt = {
        "entry_high_break_recent_logged": {
            "coverage_n": len(log_hb_recent),
            "coverage_pct": round(100.0 * len(log_hb_recent) / len(enriched), 2),
            "true_count_all": sum(1 for r in enriched if r.get("entry_high_break_recent_log")),
            "overlap_hb_ge_1": sum(
                1 for r in hb_ge1_rows if r.get("entry_high_break_recent_log")
            ),
            "capture_hb_ge_1_full_jsonl": _alt_capture(
                hb_ge1_rows, lambda r: bool(r.get("entry_high_break_recent_log"))
            ),
            "capture_phase221_hb_ge_1": _alt_capture(
                p221_hb_ge1_rows, lambda r: bool(r.get("entry_high_break_recent_log"))
            ),
        },
        "high_break_recent_recomputed": {
            "coverage_n": len(recomp_recent),
            "coverage_pct": round(100.0 * len(recomp_recent) / len(enriched), 2),
            "true_count_all": sum(1 for r in enriched if r.get("high_break_recent_recomputed")),
            "overlap_hb_ge_1": sum(
                1 for r in hb_ge1_rows if r.get("high_break_recent_recomputed")
            ),
        },
        "entry_near_day_high_pct_breakout_distance": {
            "coverage_n": len(log_near_high),
            "coverage_pct": round(100.0 * len(log_near_high) / len(enriched), 2),
            "median_all": round(statistics.median([float(r["entry_near_day_high_pct"]) for r in log_near_high]), 4)
            if log_near_high
            else None,
            "hb_ge_1_median": round(
                statistics.median(
                    [float(r["entry_near_day_high_pct"]) for r in hb_ge1_rows if r.get("entry_near_day_high_pct") is not None]
                ),
                4,
            )
            if any(r.get("entry_near_day_high_pct") is not None for r in hb_ge1_rows)
            else None,
        },
        "new_high_ratio": {
            "coverage_n": len(ring_ok),
            "hb_ge_1_mean": round(
                statistics.mean([float(r["new_high_ratio"]) for r in hb_ge1_rows if r.get("new_high_ratio") is not None]),
                4,
            )
            if hb_ge1_rows
            else None,
        },
    }

    # Session-level push coverage
    by_session: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in enriched:
        sid = str(r.get("session_id") or "")
        by_session[sid]["trades"] += 1
        if r.get("jsonl_exists"):
            by_session[sid]["jsonl_hit"] += 1
        if int(r.get("ticks_in_600s_window") or 0) >= 2:
            by_session[sid]["ring_ok"] += 1
        if int(r.get("high_break_count_full_jsonl") or r.get("high_break_count") or 0) >= 1:
            by_session[sid]["hb_ge_1"] += 1
        if int(r.get("high_break_count_phase221_ring") or 0) >= 1:
            by_session[sid]["p221_hb_ge_1"] += 1

    session_summary = {
        k: {
            **dict(v),
            "jsonl_hit_pct": round(100.0 * v["jsonl_hit"] / max(1, v["trades"]), 2),
            "ring_ok_pct": round(100.0 * v["ring_ok"] / max(1, v["trades"]), 2),
        }
        for k, v in sorted(by_session.items())
    }

    report = {
        "phase": 223,
        "mode": "high_break_coverage_audit",
        "constraints": {
            "review_only": True,
            "hard_reject_forbidden": True,
            "entry_change_forbidden": True,
        },
        "population": {"total_trades": len(enriched)},
        "0_phase222_context": {
            "phase222_hb_source": "Phase221 _get_price_ring (full jsonl scan, trailing 660s ring only)",
            "phase222_trades_with_hb_ge_1": 30,
            "note": "high_break_count is not logged at accept time; Phase221/222 compute it offline.",
        },
        "1_high_break_count_coverage": {
            "full_jsonl_recompute": {
                "distribution": dict(sorted(hb_counts.items())),
                "hb_ge_1": sum(
                    1
                    for r in enriched
                    if int(r.get("high_break_count_full_jsonl") or r.get("high_break_count") or 0) >= 1
                ),
                "hb_ge_1_pct": round(
                    100.0
                    * sum(
                        1
                        for r in enriched
                        if int(r.get("high_break_count_full_jsonl") or r.get("high_break_count") or 0) >= 1
                    )
                    / len(enriched),
                    2,
                ),
            },
            "phase221_ring_method_matches_phase222": {
                "distribution": dict(sorted(p221_hb_counts.items())),
                "hb_ge_1": len(p221_hb_ge1_rows),
                "hb_ge_1_pct": round(100.0 * len(p221_hb_ge1_rows) / len(enriched), 2),
            },
        },
        "2_push_ring_coverage": {
            "full_jsonl_method": {
                "push_dir_exists": sum(1 for r in enriched if r.get("push_dir_exists")),
                "symbol_jsonl_exists": sum(1 for r in enriched if r.get("jsonl_exists")),
                "ticks_before_entry_gt_0": sum(1 for r in enriched if int(r.get("ticks_before_entry") or 0) > 0),
                "ring_ok_ticks_in_600s_ge_2": len(ring_ok),
                "coverage_pct": {
                    "push_dir": round(100.0 * sum(1 for r in enriched if r.get("push_dir_exists")) / len(enriched), 2),
                    "jsonl": round(100.0 * sum(1 for r in enriched if r.get("jsonl_exists")) / len(enriched), 2),
                    "ring_ok": round(100.0 * len(ring_ok) / len(enriched), 2),
                },
            },
            "phase221_ring_method": {
                "ring_ticks_before_entry_gt_0": sum(
                    1 for r in enriched if int(r.get("phase221_ring_ticks_before_entry") or 0) > 0
                ),
                "ring_ok_ticks_in_600s_ge_2": len(p221_ring_ok),
                "coverage_pct": {
                    "any_ticks_before_entry": round(
                        100.0
                        * sum(1 for r in enriched if int(r.get("phase221_ring_ticks_before_entry") or 0) > 0)
                        / len(enriched),
                        2,
                    ),
                    "ring_ok": round(100.0 * len(p221_ring_ok) / len(enriched), 2),
                },
            },
        },
        "3_hb_zero_breakdown": {
            "full_jsonl_method": {
                "total_hb_zero": len(hb0),
                "data_missing": len(hb0_data),
                "data_missing_pct_of_hb_zero": round(100.0 * len(hb0_data) / max(1, len(hb0)), 2),
                "true_zero_new_highs": len(hb0_true),
                "true_zero_pct_of_hb_zero": round(100.0 * len(hb0_true) / max(1, len(hb0)), 2),
                "reason_counts": dict(zero_reasons.most_common()),
                "data_missing_reason_counts": dict(
                    Counter(str(r.get("hb_zero_reason")) for r in hb0_data).most_common()
                ),
            },
            "phase221_ring_method": {
                "total_hb_zero": len(p221_hb0),
                "ring_empty_or_insufficient_ticks": len(p221_hb0_ring_empty),
                "ring_empty_pct_of_hb_zero": round(100.0 * len(p221_hb0_ring_empty) / max(1, len(p221_hb0)), 2),
                "true_zero_with_ring_ok": len(p221_hb0_true),
                "true_zero_pct_of_hb_zero": round(100.0 * len(p221_hb0_true) / max(1, len(p221_hb0)), 2),
            },
            "method_gap": {
                "full_jsonl_hb_ge_1_but_phase221_zero": len(jsonl_hb_pos_p221_zero),
                "pct_of_all_trades": round(100.0 * len(jsonl_hb_pos_p221_zero) / len(enriched), 2),
            },
        },
        "4_correlations_ring_ok_subset": {
            "full_jsonl_ring_ok_n": len(ring_ok),
            "phase221_ring_ok_n": len(p221_ring_ok),
            "high_break_full_jsonl_vs_rise_5min_logged": {
                "n": len(hb_r5),
                "pearson_r": _pearson([a for a, _ in hb_r5], [b for _, b in hb_r5]),
            },
            "high_break_full_jsonl_vs_continuation_duration": {
                "n": len(hb_dur),
                "pearson_r": _pearson([a for a, _ in hb_dur], [c for _, c in hb_dur]),
            },
            "high_break_phase221_vs_rise_5min_logged": {
                "n": len(
                    [
                        1
                        for r in p221_ring_ok
                        if _float(r.get("entry_rise_5min_logged")) is not None
                    ]
                ),
                "pearson_r": _pearson(
                    [float(r.get("high_break_count_phase221_ring") or 0) for r in p221_ring_ok if _float(r.get("entry_rise_5min_logged")) is not None],
                    [_float(r.get("entry_rise_5min_logged")) for r in p221_ring_ok if _float(r.get("entry_rise_5min_logged")) is not None],
                ),
            },
            "rise_5min_logged_coverage_in_full_jsonl_ring_ok": round(
                100.0 * len(hb_r5) / max(1, len(ring_ok)), 2
            ),
        },
        "5_alternative_indicators": alt,
        "session_push_coverage": session_summary,
        "diagnosis": [],
        "notes": [
            "Phase222 hb=30 uses Phase221 _get_price_ring: scans entire jsonl but append_price_tick keeps only trailing 660s of file.",
            "Early-session entries lose pre-entry history after full-file scan; full-jsonl recompute is entry-aligned.",
            "hb_zero data_missing (full jsonl) = no push dir, no jsonl, or insufficient ticks in 600s window.",
            "hb_zero computed_zero_new_highs (full jsonl) = ring ok but no new-high updates detected.",
            "entry_high_break_recent logged only in sessions with extended_entry_shadow (~8.7% coverage).",
            "breakout_distance uses entry_near_day_high_pct (smaller = closer to day high).",
        ],
    }

    diag: list[str] = []
    p221_ge1 = len(p221_hb_ge1_rows)
    if abs(p221_ge1 - 30) <= 5:
        diag.append(
            f"Phase221 ring method reproduces Phase222 hb>=1 count ({p221_ge1} vs 30): "
            "low fire rate is a ring-loader artifact, not missing push data."
        )
    if len(jsonl_hb_pos_p221_zero) > 100:
        diag.append(
            f"{len(jsonl_hb_pos_p221_zero)} trades ({round(100*len(jsonl_hb_pos_p221_zero)/len(enriched),1)}%) "
            "have hb>=1 on full jsonl but hb=0 on Phase221 ring — entries before ring tail window."
        )
    jsonl_pct = report["2_push_ring_coverage"]["full_jsonl_method"]["coverage_pct"]["jsonl"]
    ring_pct = report["2_push_ring_coverage"]["full_jsonl_method"]["coverage_pct"]["ring_ok"]
    p221_ring_pct = report["2_push_ring_coverage"]["phase221_ring_method"]["coverage_pct"]["ring_ok"]
    if jsonl_pct >= 99 and p221_ring_pct < 10:
        diag.append(
            f"Push jsonl exists for {jsonl_pct}% of trades but Phase221 ring_ok only {p221_ring_pct}%: "
            "offline ring rebuild does not preserve entry-time history."
        )
    if len(hb0_data) > len(hb0_true):
        diag.append(
            f"Full-jsonl hb=0: data_missing ({len(hb0_data)}) vs true_zero ({len(hb0_true)})."
        )
    elif len(hb0_true) == len(hb0) and len(hb0) < len(enriched) * 0.1:
        diag.append(
            f"Full-jsonl hb=0 ({len(hb0)}) is 100% true_zero (no new highs), not data gap."
        )
    if alt["entry_high_break_recent_logged"]["coverage_n"] < len(enriched) * 0.1:
        diag.append("entry_high_break_recent log coverage sparse — limited live shadow alternative.")
    if p221_hb_ge1_rows and alt["high_break_recent_recomputed"]["overlap_hb_ge_1"] >= len(p221_hb_ge1_rows) * 0.9:
        diag.append("high_break_recent_recomputed captures most Phase221 hb>=1 trades on full jsonl paths.")
    report["diagnosis"] = diag

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"wrote {OUT} full_jsonl hb>=1={report['1_high_break_count_coverage']['full_jsonl_recompute']['hb_ge_1']} "
        f"phase221 hb>=1={p221_ge1}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
