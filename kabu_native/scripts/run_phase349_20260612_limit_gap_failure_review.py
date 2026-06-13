#!/usr/bin/env python3
"""
Phase349: 2026/06/12 AM limit-up / gap-up failure review (analysis only).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, time
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SESSION_DIR = REPO / "kabu_native/results/small_paper/20260612/live_session_080806"
UNIVERSE_CSV = (
    REPO
    / "kabu_native/results/reports/universe_core10_dynamic40_price_risk_am_refresh1000_20260612.csv"
)
OUT_DIR = REPO / "kabu_native/results/reports"
JST = ZoneInfo("Asia/Tokyo")

NEAR_LIMIT_PCT = 0.5
NEAR_DAY_HIGH_PCT = 1.5
GU_RUN_FROM_PREV_MIN_PCT = 2.0
OPEN_END = time(10, 0)


def _bootstrap() -> None:
    import sys

    src = REPO / "kabu_native" / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))


def _float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _bool(v: Any) -> bool:
    return str(v or "").lower() in ("true", "1", "yes")


def _parse_dt(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(JST)
    except (TypeError, ValueError):
        return None


def _entry_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))


def _load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _pnl_yen_100(row: dict[str, Any]) -> Optional[float]:
    ep, xp = _float(row.get("entry_price")), _float(row.get("exit_price"))
    if ep is None or xp is None:
        return None
    return round((xp - ep) * 100.0, 2)


def _pf(yens: list[float]) -> Optional[float]:
    gp = sum(max(y, 0) for y in yens)
    gl = abs(sum(min(y, 0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _cohort(rows: list[dict[str, Any]]) -> dict[str, Any]:
    yens = [float(r["pnl_yen_100"]) for r in rows if r.get("pnl_yen_100") is not None]
    stops = sum(1 for r in rows if r.get("is_stop_hit"))
    return {
        "count": len(rows),
        "total_pnl_yen_100": round(sum(yens), 2) if yens else 0.0,
        "profit_factor_yen_100": _pf(yens),
        "stop_hit_count": stops,
        "stop_rate": round(stops / len(rows), 4) if rows else 0.0,
    }


def _classify(row: dict[str, Any]) -> str:
    near_lim = _bool(row.get("near_limit_up")) or _bool(row.get("is_limit_up"))
    dist_up = _float(row.get("distance_to_limit_up_pct"))
    near_high = _float(row.get("entry_near_day_high_pct"))
    rise5 = _float(row.get("entry_rise_5min_pct"))
    vwap_dev = _float(row.get("entry_vwap_dev_pct"))
    run_prev = _float(row.get("run_from_prev_close_pct"))

    close_to_high = near_high is not None and near_high <= NEAR_DAY_HIGH_PCT
    limit_prox = near_lim or (dist_up is not None and dist_up <= NEAR_LIMIT_PCT)
    limit_prox_wide = dist_up is not None and dist_up <= 2.0
    peeling = rise5 is not None and rise5 < 0
    gu_run = run_prev is not None and run_prev >= GU_RUN_FROM_PREV_MIN_PCT
    vwap_below = vwap_dev is not None and vwap_dev < 0

    if (limit_prox or (close_to_high and limit_prox_wide)) and peeling:
        return "A"
    if gu_run and close_to_high and peeling:
        return "B"
    if vwap_below and peeling:
        return "C"
    return "D"


def _enrich_trade(
    acc: dict[str, str],
    ex: dict[str, str],
    *,
    universe: dict[str, dict[str, str]],
) -> dict[str, Any]:
    from universe.am_pm_universe import estimate_daily_limit_prices, limit_status_from_prices

    sym = str(ex.get("symbol") or "")
    u = universe.get(sym, {})
    prev_close = _float(u.get("close_price"))
    entry_px = _float(ex.get("entry_price")) or _float(acc.get("current_price"))
    near_high = _float(acc.get("entry_near_day_high_pct") or ex.get("entry_near_day_high_pct"))
    rise5 = _float(acc.get("entry_rise_5min_pct") or ex.get("entry_rise_5min_pct"))
    vwap_dev = _float(acc.get("entry_vwap_dev_pct") or ex.get("entry_vwap_dev_pct"))
    entry_dt = _parse_dt(str(ex.get("entry_time") or ""))

    implied_day_high: Optional[float] = None
    if entry_px and near_high is not None and near_high < 100:
        implied_day_high = round(entry_px / (1.0 - near_high / 100.0), 2)

    lim_up, lim_down, lim_src = estimate_daily_limit_prices(prev_close)
    lim = limit_status_from_prices(
        current=entry_px,
        limit_up=lim_up,
        limit_down=lim_down,
        bid_qty=_float(acc.get("bid_qty")),
        ask_qty=_float(acc.get("ask_qty")),
    )

    run_prev: Optional[float] = None
    if prev_close and entry_px and prev_close > 0:
        run_prev = round((entry_px - prev_close) / prev_close * 100.0, 4)

    run_day_high_prev: Optional[float] = None
    if prev_close and implied_day_high and prev_close > 0:
        run_day_high_prev = round((implied_day_high - prev_close) / prev_close * 100.0, 4)

    dist_high_to_limit: Optional[float] = None
    if implied_day_high and lim_up and lim_up > 0:
        dist_high_to_limit = round((lim_up - implied_day_high) / lim_up * 100.0, 4)

    yen = _pnl_yen_100(ex)
    reason = str(ex.get("structural_exit_reason") or ex.get("exit_reason") or "")
    row: dict[str, Any] = {
        "symbol": sym,
        "entry_time": ex.get("entry_time"),
        "exit_time": ex.get("exit_time"),
        "entry_price": entry_px,
        "exit_price": _float(ex.get("exit_price")),
        "pnl_yen_100": yen,
        "pnl_pct": _float(ex.get("pnl_pct")),
        "exit_reason": reason,
        "is_stop_hit": reason == "stop_hit",
        "prev_close": prev_close,
        "implied_day_high_at_entry": implied_day_high,
        "daily_limit_up_price": lim_up,
        "daily_limit_down_price": lim_down,
        "limit_price_source": lim_src,
        "distance_to_limit_up_pct": lim.get("distance_to_limit_up_pct"),
        "distance_to_limit_down_pct": lim.get("distance_to_limit_down_pct"),
        "is_limit_up": lim.get("is_limit_up"),
        "is_limit_down": lim.get("is_limit_down"),
        "near_limit_up": lim.get("near_limit_up"),
        "near_limit_down": lim.get("near_limit_down"),
        "day_high_near_limit_up": (
            dist_high_to_limit is not None and dist_high_to_limit <= NEAR_LIMIT_PCT
        ),
        "entry_near_day_high_pct": near_high,
        "entry_high_break_recent": _bool(
            acc.get("entry_high_break_recent") or ex.get("entry_high_break_recent")
        ),
        "entry_vwap_dev_pct": vwap_dev,
        "entry_rise_5min_pct": rise5,
        "run_from_prev_close_pct": run_prev,
        "run_day_high_from_prev_close_pct": run_day_high_prev,
        "open_to_entry_proxy_pct": run_prev if entry_dt and entry_dt.time() < OPEN_END else None,
        "early_open_window_entry": bool(entry_dt and entry_dt.time() < OPEN_END),
        "universe_slot": u.get("universe_slot", ""),
        "source_bucket": u.get("source_bucket", ""),
        "continuation_quality_score": _float(acc.get("continuation_quality_score")),
        "entry_expectancy_score_v2": _int(acc.get("entry_expectancy_score_v2")),
    }
    row["pattern_class"] = _classify(row)
    return row


def _int(v: Any) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def main() -> int:
    _bootstrap()
    events_path = SESSION_DIR / "small_paper_events.csv"
    if not events_path.is_file():
        raise SystemExit(f"missing {events_path}")

    events = _load_csv(events_path)
    accepted = { _entry_key(a): a for a in events if a.get("event_type") == "accepted"}
    universe = {str(r.get("symbol") or ""): r for r in _load_csv(UNIVERSE_CSV)} if UNIVERSE_CSV.is_file() else {}

    trades: list[dict[str, Any]] = []
    for ex in events:
        if ex.get("event_type") != "observer_exit" or ex.get("pnl_pct") in (None, ""):
            continue
        acc = accepted.get(_entry_key(ex), {})
        trades.append(_enrich_trade(acc, ex, universe=universe))

    trades.sort(key=lambda r: float(r.get("pnl_yen_100") or 0))
    worst = trades[:20]
    stops = [t for t in trades if t.get("is_stop_hit")]
    dynamic = [t for t in trades if t.get("universe_slot") == "dynamic"]

    by_class = defaultdict(list)
    for t in trades:
        by_class[str(t.get("pattern_class"))].append(t)

    limit_prox = [t for t in trades if t.get("near_limit_up") or t.get("is_limit_up")]
    gap_pullback = [t for t in trades if t.get("pattern_class") in ("A", "B")]
    vwap_rise_neg = [t for t in trades if (_float(t.get("entry_vwap_dev_pct")) or 0) < 0 and (_float(t.get("entry_rise_5min_pct")) or 0) < 0]

    sym_dyn: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in dynamic:
        sym_dyn[str(t["symbol"])].append(t)
    dyn_rows = []
    for sym, rows in sorted(sym_dyn.items(), key=lambda kv: sum(float(r["pnl_yen_100"]) for r in kv[1])):
        yens = [float(r["pnl_yen_100"]) for r in rows if r.get("pnl_yen_100") is not None]
        classes = Counter(str(r.get("pattern_class")) for r in rows)
        dyn_rows.append(
            {
                "symbol": sym,
                "trade_count": len(rows),
                "total_pnl_yen_100": round(sum(yens), 2),
                "stop_hit_count": sum(1 for r in rows if r.get("is_stop_hit")),
                "pattern_A": classes.get("A", 0),
                "pattern_B": classes.get("B", 0),
                "pattern_C": classes.get("C", 0),
                "pattern_D": classes.get("D", 0),
                "avg_distance_to_limit_up_pct": _mean(
                    [_float(r.get("distance_to_limit_up_pct")) for r in rows]
                ),
                "avg_near_day_high_pct": _mean(
                    [_float(r.get("entry_near_day_high_pct")) for r in rows]
                ),
                "near_limit_up_entries": sum(1 for r in rows if r.get("near_limit_up")),
                "day_high_near_limit_entries": sum(1 for r in rows if r.get("day_high_near_limit_up")),
            }
        )

    class_yen = {
        k: round(sum(float(r["pnl_yen_100"]) for r in v if r.get("pnl_yen_100") is not None), 2)
        for k, v in by_class.items()
    }
    total_yen = sum(float(t["pnl_yen_100"]) for t in trades if t.get("pnl_yen_100") is not None)
    stop_yen = sum(float(t["pnl_yen_100"]) for t in stops if t.get("pnl_yen_100") is not None)

    need_limit_filter = len(limit_prox) >= 3 or _cohort(limit_prox)["total_pnl_yen_100"] < -50000
    need_gu_filter = len(gap_pullback) >= 10 and class_yen.get("A", 0) + class_yen.get("B", 0) < -100000
    rise5_neg_stop = sum(1 for t in stops if (_float(t.get("entry_rise_5min_pct")) or 0) < 0)
    rise5_neg_all = sum(1 for t in trades if (_float(t.get("entry_rise_5min_pct")) or 0) < 0)
    dynamic_bad = _cohort(dynamic)["total_pnl_yen_100"] < -200000

    shadow_improvements: list[str] = []
    if need_limit_filter:
        shadow_improvements.append(
            "near_limit_up / day_high_near_limit 除外 Shadow（distance_to_limit_up <= 0.5%）"
        )
    if need_gu_filter:
        shadow_improvements.append(
            "GU+寄り天崩れ Shadow: run_from_prev_close>=2% かつ near_day_high<=1.5% かつ rise_5min<0 で ENTRY 抑制"
        )
    if rise5_neg_stop >= len(stops) * 0.5:
        shadow_improvements.append(
            "rise_5min<0 かつ vwap_dev<0 の複合 ENTRY 禁止 Shadow（押し目誤認 C 群）"
        )
    if dynamic_bad and len(shadow_improvements) < 3:
        shadow_improvements.append(
            "Dynamic40 AM: 値幅制限接近銘柄の rank ペナルティ / core10 優先 Shadow"
        )
    shadow_improvements = shadow_improvements[:3]

    summary = {
        "phase": 349,
        "title": "20260612 Limit-Up / Gap-Up Failure Review (AM)",
        "session_id": "20260612/live_session_080806",
        "method_note": (
            "prev_close from universe refresh CSV; limit bands via JPX tier proxy; "
            "day_high from board HighPrice at entry (entry_near_day_high_pct); "
            "no live open price — open_to_entry uses run_from_prev_close before 10:00."
        ),
        "thresholds": {
            "NEAR_LIMIT_PCT": NEAR_LIMIT_PCT,
            "NEAR_DAY_HIGH_PCT": NEAR_DAY_HIGH_PCT,
            "GU_RUN_FROM_PREV_MIN_PCT": GU_RUN_FROM_PREV_MIN_PCT,
        },
        "headline": {
            "trade_count": len(trades),
            "total_pnl_yen_100": round(total_yen, 2),
            "stop_hit_count": len(stops),
            "stop_hit_pnl_yen_100": round(stop_yen, 2),
        },
        "checklist": {
            "1_limit_proximity": {
                "near_limit_up_count": sum(1 for t in trades if t.get("near_limit_up")),
                "is_limit_up_count": sum(1 for t in trades if t.get("is_limit_up")),
                "day_high_near_limit_count": sum(1 for t in trades if t.get("day_high_near_limit_up")),
                "avg_distance_to_limit_up_pct": _mean(
                    [_float(t.get("distance_to_limit_up_pct")) for t in trades]
                ),
                "cohort_near_limit_up": _cohort(limit_prox),
            },
            "2_day_high_before_entry": {
                "avg_entry_near_day_high_pct": _mean(
                    [_float(t.get("entry_near_day_high_pct")) for t in trades]
                ),
                "pct_within_1p5_of_day_high": round(
                    sum(
                        1
                        for t in trades
                        if (_float(t.get("entry_near_day_high_pct")) or 99) <= NEAR_DAY_HIGH_PCT
                    )
                    / len(trades),
                    4,
                )
                if trades
                else 0.0,
                "high_break_recent_count": sum(1 for t in trades if t.get("entry_high_break_recent")),
            },
            "3_gap_open_to_entry_proxy": {
                "early_open_window_entries": sum(1 for t in trades if t.get("early_open_window_entry")),
                "avg_run_from_prev_close_pct": _mean(
                    [_float(t.get("run_from_prev_close_pct")) for t in trades]
                ),
                "avg_open_to_entry_proxy_pct": _mean(
                    [_float(t.get("open_to_entry_proxy_pct")) for t in trades if t.get("open_to_entry_proxy_pct") is not None]
                ),
            },
            "4_rise_5min_negative": {
                "count": rise5_neg_all,
                "rate": round(rise5_neg_all / len(trades), 4) if trades else 0.0,
                "stop_hit_with_rise5_neg": rise5_neg_stop,
                "cohort": _cohort(
                    [t for t in trades if (_float(t.get("entry_rise_5min_pct")) or 0) < 0]
                ),
            },
            "5_vwap_below": {
                "count": sum(1 for t in trades if (_float(t.get("entry_vwap_dev_pct")) or 0) < 0),
                "cohort": _cohort(
                    [t for t in trades if (_float(t.get("entry_vwap_dev_pct")) or 0) < 0]
                ),
            },
            "6_gap_up_pullback": {
                "pattern_A_B_count": len(gap_pullback),
                "cohort": _cohort(gap_pullback),
            },
            "7_dynamic40_failure": _cohort(dynamic),
        },
        "pattern_classification": {
            "counts": {k: len(v) for k, v in sorted(by_class.items())},
            "pnl_yen_100_by_class": class_yen,
            "cohort_by_class": {k: _cohort(v) for k, v in sorted(by_class.items())},
        },
        "conclusions": {
            "need_limit_up_down_filter": need_limit_filter,
            "limit_filter_note": (
                f"near/is_limit_up {len(limit_prox)}件、損益 {_cohort(limit_prox)['total_pnl_yen_100']}円。"
                "当日高値が制限接近のケースも多い — ストップ高圏の剥がりフィルタを Shadow 検証推奨。"
                if need_limit_filter
                else "制限接近は全体の少数派。"
            ),
            "need_gap_up_collapse_filter": need_gu_filter,
            "gap_filter_note": (
                f"A+B 分類 {len(gap_pullback)}件 / 損益 {round(sum(float(t['pnl_yen_100']) for t in gap_pullback), 0)}円。"
                "GU後寄り天崩れパターンが損失に寄与。"
                if need_gu_filter
                else "GU寄り天パターンは副次要因。"
            ),
            "ban_rise_5min_negative": rise5_neg_stop >= len(stops) * 0.5,
            "rise_5min_note": (
                f"stop_hit {rise5_neg_stop}/{len(stops)} が rise_5min<0。"
                "単独禁止は誤検知も多いが、VWAP下との複合禁止は Shadow 向き。"
                if rise5_neg_stop >= len(stops) * 0.5
                else "rise_5min 単独では stop 説明力が不足。"
            ),
            "revise_dynamic40": dynamic_bad,
            "dynamic40_note": (
                f"Dynamic40 {_cohort(dynamic)['count']}件 / {_cohort(dynamic)['total_pnl_yen_100']}円。"
                "値幅制限接近・寄り天パターンが損失上位銘柄に集中。"
                if dynamic_bad
                else "Dynamic40 は今回の主因ではない。"
            ),
            "primary_pattern_driver": min(class_yen.items(), key=lambda kv: kv[1])[0]
            if class_yen
            else "D",
            "shadow_improvements_max3": shadow_improvements,
        },
    }

    trade_fields = sorted({k for t in trades for k in t})
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "phase349_20260612_limit_gap_failure_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(OUT_DIR / "phase349_20260612_limit_proximity_trades.csv", trades, trade_fields)
    _write_csv(
        OUT_DIR / "phase349_20260612_gap_up_pullback_trades.csv",
        gap_pullback,
        trade_fields,
    )
    _write_csv(
        OUT_DIR / "phase349_20260612_dynamic40_failure_patterns.csv",
        dyn_rows,
        list(dyn_rows[0].keys()) if dyn_rows else [],
    )

    print(json.dumps(summary["conclusions"], ensure_ascii=True, indent=2))
    print(f"wrote phase349 outputs under {OUT_DIR}")
    return 0


def _mean(xs: list[Optional[float]]) -> Optional[float]:
    vals = [float(x) for x in xs if x is not None]
    return round(statistics.mean(vals), 4) if vals else None


if __name__ == "__main__":
    raise SystemExit(main())
