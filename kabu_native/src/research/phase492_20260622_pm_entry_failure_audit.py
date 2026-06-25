"""
Phase492 — 20260622 PM Entry Failure Audit (replay / live events only).

Analyzes stop_hit + no_progress_exit vs trailing_mfe winners for PM session.
No Runtime / YAML / Entry / Exit / Gate changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from replay.pnl_yen import enrich_trade_pnl_yen

JST = ZoneInfo("Asia/Tokyo")

DAY = "20260622"
PM_SESSION_DIR = f"live_session_122529"
FOCUS_SYMBOLS = ("6522", "6976", "6981")
FAILURE_REASONS = frozenset({"stop_hit", "no_progress_exit"})
WINNER_REASON = "trailing_mfe_exit"
PHASE483_TRAP_R10_ELEVATED_PCT = 40.0
PHASE483_TRAP_VWAP_TRAP_PCT = 40.0

AUDIT_FIELDS = [
    "symbol",
    "symbol_short",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "exit_reason",
    "pnl_yen_100",
    "pnl_pct",
    "mfe_pct",
    "mae_pct",
    "hold_minutes",
    "r5",
    "r10",
    "r15",
    "r30",
    "r60_proxy",
    "vwap_dev_pct",
    "vwap_structure_score",
    "momentum_continuation_score",
    "day_high_distance_pct",
    "board_tier",
    "board_imbalance",
    "r30_minus_r5",
    "vwap_extension_rate",
    "phase483_trap_match",
    "pre_rally_5m",
    "pre_rally_10m",
    "pre_rally_15m",
    "pre_rally_30m",
    "pre_rally_60m",
    "late_chase_cluster",
]

SYMBOL_REVIEW_FIELDS = [
    "symbol",
    "trade_count_pm",
    "stop_np_count",
    "total_pnl_yen_100",
    "entry_assessment",
    "five_min_narrative",
    "sample_entries",
]

COUNTERFACTUAL_FIELDS = [
    "scenario",
    "threshold_pct",
    "metric",
    "blocked_total",
    "blocked_winners",
    "blocked_losers",
    "blocked_flat",
    "blocked_pnl_yen_100",
    "remaining_pnl_yen_100",
    "delta_pnl_yen_100",
    "remaining_pf",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _resolve_reports(repo_root: Path) -> Path:
    kabu = repo_root / "kabu_native"
    if (kabu / "results").is_dir():
        return kabu / "results" / "reports"
    return repo_root / "results" / "reports"


def _resolve_kabu(repo_root: Path) -> Path:
    kabu = repo_root / "kabu_native"
    return kabu if kabu.is_dir() else repo_root


def _float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_ts(s: str) -> Optional[datetime]:
    s = str(s or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _symbol_short(sym: str) -> str:
    return str(sym or "").replace(".T", "").upper()


def _exit_reason(row: Mapping[str, Any]) -> str:
    return str(row.get("structural_exit_reason") or row.get("exit_reason") or "").strip()


def _board_tier(row: Mapping[str, Any]) -> str:
    tier = str(row.get("board_dynamic_trailing_tier") or "").strip()
    if tier:
        return tier
    pct = _float(row.get("entry_imbalance_percentile"))
    if pct is not None:
        return "board_high" if pct >= 47.62 else "board_low"
    return ""


def _vwap_structure_score(row: Mapping[str, Any]) -> Optional[float]:
    dev = _float(row.get("entry_vwap_dev_pct"))
    mom = _float(row.get("entry_momentum_continuation_score") or row.get("momentum_continuation_score"))
    if dev is None and mom is None:
        return None
    return round((dev or 0.0) * 0.5 + (mom or 0.0) * 50.0, 4)


def _vwap_extension(row: Mapping[str, Any]) -> Optional[float]:
    ext = _float(row.get("vwap_acceleration"))
    if ext is not None:
        return ext
    dev = _float(row.get("entry_vwap_dev_pct"))
    age = _float(row.get("minutes_since_day_high_update"))
    if dev is not None and age is not None and age > 0:
        return round(dev / age, 6)
    return dev


def _phase483_trap(row: Mapping[str, Any], *, pool_r10_median: float, pool_vwap_part_median: float) -> bool:
    r10 = _float(row.get("entry_rise_10min_pct"))
    vwap_above = _float(row.get("entry_vwap_dev_pct"))
    vwap_part = _float(row.get("entry_momentum_continuation_score") or row.get("momentum_continuation_score"))
    elevated_r10 = r10 is not None and r10 > pool_r10_median
    vwap_trap = (
        vwap_above is not None
        and vwap_above >= 0.7
        and vwap_part is not None
        and vwap_part < pool_vwap_part_median
    )
    vwap_ext = _vwap_extension(row)
    r30m5 = _r30_minus_r5(row)
    decay_chase = r30m5 is not None and r30m5 > 0.5 and (vwap_ext or 0) > 0.3
    return bool(elevated_r10 or vwap_trap or decay_chase)


def _r30_minus_r5(row: Mapping[str, Any]) -> Optional[float]:
    r5 = _float(row.get("entry_rise_5min_pct"))
    r30 = _float(row.get("entry_rise_30min_pct"))
    r10 = _float(row.get("entry_rise_10min_pct"))
    if r30 is not None and r5 is not None:
        return round(r30 - r5, 4)
    if r10 is not None and r5 is not None:
        return round(r10 - r5, 4)
    return None


def _entry_assessment(row: Mapping[str, Any]) -> str:
    r5 = _float(row.get("entry_rise_5min_pct"))
    r10 = _float(row.get("entry_rise_10min_pct"))
    r15 = _float(row.get("entry_rise_15min_pct"))
    r30 = _float(row.get("entry_rise_30min_pct"))
    dhd = _float(row.get("day_high_distance_pct"))
    long_r = r30 if r30 is not None else r10
    if long_r is not None and r5 is not None and long_r >= 1.5 and r5 >= 0.3 and (dhd is None or dhd < 1.2):
        return "late_chase_after_rally (戻り売り圏)"
    if long_r is not None and long_r < -0.5 and r5 is not None and r5 > 0:
        return "downtrend_bounce (下降トレンド中の戻り)"
    if r5 is not None and r5 < -0.5:
        return "falling_knife_entry (直前下落中エントリー)"
    if r10 is not None and r5 is not None and r10 > 0 and r5 >= 0:
        return "pullback_in_uptrend (押し目)"
    return "mixed / unclear"


def _late_chase_cluster(row: Mapping[str, Any]) -> bool:
    r5 = _float(row.get("entry_rise_5min_pct"))
    r30 = _float(row.get("entry_rise_30min_pct"))
    r10 = _float(row.get("entry_rise_10min_pct"))
    long_r = r30 if r30 is not None else r10
    dhd = _float(row.get("day_high_distance_pct"))
    if r5 is None or long_r is None:
        return False
    return long_r >= 1.2 and r5 >= 0.3 and (dhd is None or dhd < 1.5)


def _five_min_narrative(
    symbol: str,
    entries: Sequence[Mapping[str, Any]],
    price_snaps: Sequence[tuple[datetime, float]],
) -> str:
    lines: list[str] = []
    for e in entries[:3]:
        ets = _parse_ts(str(e.get("entry_time") or ""))
        ep = _float(e.get("entry_price"))
        if ets is None or ep is None:
            continue
        window = [
            (t, p)
            for t, p in price_snaps
            if ets - timedelta(minutes=60) <= t <= ets + timedelta(minutes=15)
        ]
        if len(window) < 3:
            lines.append(
                f"{e.get('entry_time')}: entry@{ep:.0f} — insufficient tick density for 5m chart"
            )
            continue
        pre = [p for t, p in window if t < ets]
        post = [p for t, p in window if t >= ets]
        pre_high = max(pre) if pre else ep
        pre_low = min(pre) if pre else ep
        rise_from_low = 100.0 * (ep - pre_low) / pre_low if pre_low else 0
        dist_from_high = 100.0 * (pre_high - ep) / pre_high if pre_high else 0
        lines.append(
            f"{e.get('entry_time')}: entry@{ep:.0f} | 60m low={pre_low:.0f} high={pre_high:.0f} "
            f"| rise_from_low={rise_from_low:.2f}% pullback_from_high={dist_from_high:.2f}% "
            f"| assessment={_entry_assessment(e)}"
        )
    return " // ".join(lines) if lines else "no entries"


def _load_pm_events(kabu_root: Path) -> tuple[list[dict[str, Any]], Path]:
    events_path = (
        kabu_root / "results" / "small_paper" / DAY / PM_SESSION_DIR / "small_paper_events.csv"
    )
    if not events_path.is_file():
        raise FileNotFoundError(events_path)
    rows: list[dict[str, Any]] = []
    with events_path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            rows.append(dict(row))
    return rows, events_path


def _collect_exits(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        if str(row.get("event_type") or "") != "observer_exit":
            continue
        enriched = enrich_trade_pnl_yen(dict(row))
        if enriched.get("pnl_yen_100") is None:
            continue
        out.append(enriched)
    return out


def _price_snaps_by_symbol(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[tuple[datetime, float]]]:
    snaps: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    for row in rows:
        sym = str(row.get("symbol") or "")
        px = _float(row.get("current_price"))
        ts = _parse_ts(str(row.get("event_time") or ""))
        if sym and px is not None and ts is not None:
            snaps[sym].append((ts, px))
    for sym in snaps:
        snaps[sym].sort(key=lambda x: x[0])
    return dict(snaps)


def _trade_row(
    row: Mapping[str, Any],
    *,
    pool_r10_median: float,
    pool_vwap_part_median: float,
    cohort: str,
) -> dict[str, Any]:
    hold_sec = _float(row.get("hold_sec")) or 0.0
    mfe = _float(row.get("peak_mfe_pct") or row.get("mfe_pct"))
    mae = _float(row.get("rolling_mae_pct") or row.get("mae_pct"))
    r5 = _float(row.get("entry_rise_5min_pct"))
    r10 = _float(row.get("entry_rise_10min_pct"))
    r15 = _float(row.get("entry_rise_15min_pct"))
    r30 = _float(row.get("entry_rise_30min_pct"))
    r60 = _float(row.get("r60_sec"))
    r30m5 = _r30_minus_r5(row)
    return {
        "cohort": cohort,
        "symbol": row.get("symbol"),
        "symbol_short": _symbol_short(str(row.get("symbol") or "")),
        "entry_time": row.get("entry_time"),
        "exit_time": row.get("exit_time") or row.get("event_time"),
        "entry_price": _float(row.get("entry_price")),
        "exit_price": _float(row.get("exit_price") or row.get("current_price")),
        "exit_reason": _exit_reason(row),
        "pnl_yen_100": round(float(row["pnl_yen_100"]), 2),
        "pnl_pct": _float(row.get("pnl_pct")),
        "mfe_pct": mfe,
        "mae_pct": mae,
        "hold_minutes": round(hold_sec / 60.0, 1),
        "r5": r5,
        "r10": r10,
        "r15": r15,
        "r30": r30,
        "r60_proxy": r60,
        "vwap_dev_pct": _float(row.get("entry_vwap_dev_pct")),
        "vwap_structure_score": _vwap_structure_score(row),
        "momentum_continuation_score": _float(
            row.get("entry_momentum_continuation_score") or row.get("momentum_continuation_score")
        ),
        "day_high_distance_pct": _float(row.get("day_high_distance_pct")),
        "board_tier": _board_tier(row),
        "board_imbalance": _float(row.get("entry_order_book_imbalance")),
        "r30_minus_r5": r30m5,
        "vwap_extension_rate": _vwap_extension(row),
        "phase483_trap_match": _phase483_trap(
            row, pool_r10_median=pool_r10_median, pool_vwap_part_median=pool_vwap_part_median
        ),
        "pre_rally_5m": r5,
        "pre_rally_10m": r10,
        "pre_rally_15m": r15,
        "pre_rally_30m": r30,
        "pre_rally_60m": r60,
        "late_chase_cluster": _late_chase_cluster(row),
    }


def _mean_row(rows: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> dict[str, Optional[float]]:
    out: dict[str, Optional[float]] = {}
    for k in keys:
        vals = [_float(r.get(k)) for r in rows]
        vals = [v for v in vals if v is not None]
        out[k] = round(statistics.mean(vals), 4) if vals else None
    return out


def _pf(rows: Sequence[Mapping[str, Any]]) -> Optional[float]:
    gp = sum(max(float(r.get("pnl_yen_100") or 0), 0) for r in rows)
    gl = sum(abs(min(float(r.get("pnl_yen_100") or 0), 0)) for r in rows)
    if gl > 0:
        return round(gp / gl, 4)
    if gp > 0:
        return float("inf")
    return None


def _top_pct_threshold(values: Sequence[float], pct: float = 80.0) -> float:
    if not values:
        return 0.0
    ranked = sorted(values)
    idx = min(len(ranked) - 1, int(round((pct / 100.0) * (len(ranked) - 1))))
    return ranked[idx]


def _counterfactual(
    trades: Sequence[Mapping[str, Any]],
    *,
    metric_key: str,
    scenario: str,
) -> dict[str, Any]:
    vals = [(_float(t.get(metric_key)), t) for t in trades]
    nums = [v for v, _ in vals if v is not None]
    thr = _top_pct_threshold(nums, 80.0)
    blocked = [t for v, t in vals if v is not None and v >= thr]
    remain = [t for v, t in vals if v is None or v < thr]
    blocked_pnl = sum(float(t.get("pnl_yen_100") or 0) for t in blocked)
    remain_pnl = sum(float(t.get("pnl_yen_100") or 0) for t in remain)
    total_pnl = sum(float(t.get("pnl_yen_100") or 0) for t in trades)
    bw = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) > 0)
    bl = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) < 0)
    bf = sum(1 for t in blocked if float(t.get("pnl_yen_100") or 0) == 0)
    return {
        "scenario": scenario,
        "threshold_pct": 80,
        "metric": metric_key,
        "threshold_value": round(thr, 6),
        "blocked_total": len(blocked),
        "blocked_winners": bw,
        "blocked_losers": bl,
        "blocked_flat": bf,
        "blocked_pnl_yen_100": round(blocked_pnl, 2),
        "remaining_pnl_yen_100": round(remain_pnl, 2),
        "delta_pnl_yen_100": round(remain_pnl - total_pnl, 2),
        "remaining_pf": _pf(remain),
        "baseline_pnl_yen_100": round(total_pnl, 2),
        "baseline_pf": _pf(trades),
    }


def _counterfactual_ab(
    trades: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    a_thr = _top_pct_threshold(
        [v for v in (_float(t.get("r30_minus_r5")) for t in trades) if v is not None], 80.0
    )
    b_thr = _top_pct_threshold(
        [v for v in (_float(t.get("vwap_extension_rate")) for t in trades) if v is not None], 80.0
    )

    def blocked(t: Mapping[str, Any]) -> bool:
        a = _float(t.get("r30_minus_r5"))
        b = _float(t.get("vwap_extension_rate"))
        return (a is not None and a >= a_thr) and (b is not None and b >= b_thr)

    blocked_rows = [t for t in trades if blocked(t)]
    remain = [t for t in trades if not blocked(t)]
    blocked_pnl = sum(float(t.get("pnl_yen_100") or 0) for t in blocked_rows)
    remain_pnl = sum(float(t.get("pnl_yen_100") or 0) for t in remain)
    total_pnl = sum(float(t.get("pnl_yen_100") or 0) for t in trades)
    return {
        "scenario": "C_A_plus_B_top20",
        "threshold_pct": 80,
        "metric": "r30_minus_r5 AND vwap_extension_rate",
        "threshold_value_a": round(a_thr, 6),
        "threshold_value_b": round(b_thr, 6),
        "blocked_total": len(blocked_rows),
        "blocked_winners": sum(1 for t in blocked_rows if float(t.get("pnl_yen_100") or 0) > 0),
        "blocked_losers": sum(1 for t in blocked_rows if float(t.get("pnl_yen_100") or 0) < 0),
        "blocked_flat": sum(1 for t in blocked_rows if float(t.get("pnl_yen_100") or 0) == 0),
        "blocked_pnl_yen_100": round(blocked_pnl, 2),
        "remaining_pnl_yen_100": round(remain_pnl, 2),
        "delta_pnl_yen_100": round(remain_pnl - total_pnl, 2),
        "remaining_pf": _pf(remain),
        "baseline_pnl_yen_100": round(total_pnl, 2),
        "baseline_pf": _pf(trades),
    }


def _verdict(
    failures: Sequence[Mapping[str, Any]],
    *,
    trap_match_rate: float,
    late_chase_cluster_rate: float,
    counterfactual_c: Mapping[str, Any],
) -> str:
    if trap_match_rate >= 0.5 and late_chase_cluster_rate >= 0.4:
        return "entry_quality_problem"
    if counterfactual_c.get("remaining_pf") and float(counterfactual_c["remaining_pf"] or 0) > 1.2:
        return "entry_quality_problem"
    if trap_match_rate < 0.25:
        return "market_regime_problem"
    return "entry_quality_problem"


def _write_csv(path: Path, fields: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow(dict(row))


def run_phase492(*, repo_root: Path) -> dict[str, Any]:
    kabu = _resolve_kabu(repo_root)
    rows, events_path = _load_pm_events(kabu)
    exits = _collect_exits(rows)
    price_snaps = _price_snaps_by_symbol(rows)

    all_r10 = [_float(r.get("entry_rise_10min_pct")) for r in exits]
    all_r10 = [v for v in all_r10 if v is not None]
    pool_r10_med = statistics.median(all_r10) if all_r10 else 0.0
    all_mom = [
        _float(r.get("entry_momentum_continuation_score") or r.get("momentum_continuation_score"))
        for r in exits
    ]
    all_mom = [v for v in all_mom if v is not None]
    pool_vwap_part_med = statistics.median(all_mom) if all_mom else 0.0

    failures = [r for r in exits if _exit_reason(r) in FAILURE_REASONS]
    winners = [r for r in exits if _exit_reason(r) == WINNER_REASON]

    audit_rows = [
        _trade_row(r, pool_r10_median=pool_r10_med, pool_vwap_part_median=pool_vwap_part_med, cohort="failure")
        for r in failures
    ]
    winner_rows = [
        _trade_row(r, pool_r10_median=pool_r10_med, pool_vwap_part_median=pool_vwap_part_med, cohort="winner")
        for r in winners
    ]

    feat_keys = [
        "r5", "r10", "r15", "r30", "vwap_dev_pct", "momentum_continuation_score",
        "day_high_distance_pct", "r30_minus_r5", "vwap_extension_rate",
        "pre_rally_5m", "pre_rally_30m",
    ]
    fail_mean = _mean_row(audit_rows, feat_keys)
    win_mean = _mean_row(winner_rows, feat_keys)
    diff = {
        k: round((fail_mean.get(k) or 0) - (win_mean.get(k) or 0), 4)
        if fail_mean.get(k) is not None and win_mean.get(k) is not None
        else None
        for k in feat_keys
    }

    trap_matches = sum(1 for r in audit_rows if r.get("phase483_trap_match"))
    trap_rate = trap_matches / len(audit_rows) if audit_rows else 0.0
    late_cluster = sum(1 for r in audit_rows if r.get("late_chase_cluster"))
    late_cluster_rate = late_cluster / len(audit_rows) if audit_rows else 0.0

    all_enriched = [
        _trade_row(r, pool_r10_median=pool_r10_med, pool_vwap_part_median=pool_vwap_part_med, cohort="all")
        for r in exits
    ]
    cf_a = _counterfactual(all_enriched, metric_key="r30_minus_r5", scenario="A_r30_minus_r5_top20")
    cf_b = _counterfactual(all_enriched, metric_key="vwap_extension_rate", scenario="B_vwap_extension_top20")
    cf_c = _counterfactual_ab(all_enriched)

    symbol_review: list[dict[str, Any]] = []
    for sym_short in FOCUS_SYMBOLS:
        sym = f"{sym_short}.T"
        sym_exits = [r for r in exits if _symbol_short(str(r.get("symbol") or "")) == sym_short]
        sym_fail = [r for r in sym_exits if _exit_reason(r) in FAILURE_REASONS]
        sym_entries = [
            _trade_row(r, pool_r10_median=pool_r10_med, pool_vwap_part_median=pool_vwap_part_med, cohort="failure")
            for r in sym_fail
        ]
        assessments = [_entry_assessment(r) for r in sym_fail] if sym_fail else []
        symbol_review.append(
            {
                "symbol": sym_short,
                "trade_count_pm": len(sym_exits),
                "stop_np_count": len(sym_fail),
                "total_pnl_yen_100": round(sum(float(r["pnl_yen_100"]) for r in sym_exits), 2),
                "entry_assessment": Counter(assessments).most_common(1)[0][0] if assessments else "n/a",
                "five_min_narrative": _five_min_narrative(sym, sym_fail, price_snaps.get(sym, [])),
                "sample_entries": "; ".join(
                    f"{r.get('entry_time')} {_exit_reason(r)} {r.get('pnl_yen_100')}"
                    for r in sym_fail[:5]
                ),
            }
        )

    summary_path_data = kabu / "results" / "small_paper" / DAY / PM_SESSION_DIR / "small_paper_summary.json"
    summary_json: dict[str, Any] = {}
    if summary_path_data.is_file():
        summary_json = json.loads(summary_path_data.read_text(encoding="utf-8"))
    canon = summary_json.get("canonical_summary") or {}

    verdict = _verdict(
        audit_rows,
        trap_match_rate=trap_rate,
        late_chase_cluster_rate=late_cluster_rate,
        counterfactual_c=cf_c,
    )

    fail_pnl = sum(float(r["pnl_yen_100"]) for r in failures)
    win_pnl = sum(float(r["pnl_yen_100"]) for r in winners)
    stop_pnl = sum(float(r["pnl_yen_100"]) for r in failures if _exit_reason(r) == "stop_hit")
    np_pnl = sum(float(r["pnl_yen_100"]) for r in failures if _exit_reason(r) == "no_progress_exit")

    mandatory = {
        "pm_collapse_primary_cause": (
            f"stop_hit({len([r for r in failures if _exit_reason(r)=='stop_hit'])}) + "
            f"no_progress({len([r for r in failures if _exit_reason(r)=='no_progress_exit'])}) "
            f"= {len(failures)} trades / {fail_pnl:,.0f} yen; PF={canon.get('profit_factor_yen_100')}"
        ),
        "6522_6976_6981_eval": {r["symbol"]: r["entry_assessment"] for r in symbol_review},
        "late_chase_hypothesis": (
            f"{'CONFIRMED' if trap_rate >= 0.4 or late_cluster_rate >= 0.4 else 'PARTIAL' if trap_rate >= 0.25 else 'REJECTED'} "
            f"(phase483_trap_match={trap_rate:.0%}, late_chase_cluster={late_cluster_rate:.0%})"
        ),
        "implementation_value": (
            "Moderate — counterfactual C improves PF but blocks winners; guard tuning replay required"
            if cf_c.get("remaining_pf") and float(cf_c["remaining_pf"]) > float(canon.get("profit_factor_yen_100") or 0)
            else "Low — filters do not cleanly separate PM failures from winners"
        ),
        "overfit_risk": (
            "High if deployed on single-day PM slice; Phase483 pattern is population-level, "
            "6522 concentration (5 stops) is symbol-specific"
        ),
        "verdict": verdict,
        "failure_pnl_yen_100": round(fail_pnl, 2),
        "winner_pnl_yen_100": round(win_pnl, 2),
        "stop_hit_pnl_yen_100": round(stop_pnl, 2),
        "no_progress_pnl_yen_100": round(np_pnl, 2),
        "failure_vs_winner_feature_diff": diff,
        "counterfactual": {"A": cf_a, "B": cf_b, "C": cf_c},
    }

    return {
        "generated_at": _now_iso(),
        "day": DAY,
        "session": PM_SESSION_DIR,
        "events_path": str(events_path),
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "failure_count": len(failures),
        "winner_count": len(winners),
        "phase483_trap_match_rate": round(trap_rate, 4),
        "late_chase_cluster_rate": round(late_cluster_rate, 4),
        "canonical_pf": canon.get("profit_factor_yen_100"),
        "_audit_rows": audit_rows,
        "_winner_rows": winner_rows,
        "_symbol_review": symbol_review,
        "_counterfactual": [cf_a, cf_b, cf_c],
        "_fail_mean": fail_mean,
        "_win_mean": win_mean,
    }


@dataclass
class Phase492Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase492(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = _resolve_reports(self.repo_root)
        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        paths = {
            "audit": reports / "phase492_20260622_pm_entry_failure_audit.csv",
            "counterfactual": reports / "phase492_20260622_pm_counterfactual.csv",
            "symbol_review": reports / "phase492_20260622_pm_symbol_review.csv",
            "summary": reports / "phase492_summary.json",
            "report": doc_root / "docs" / "operations" / "phase492_20260622_pm_entry_failure_audit.md",
        }
        _write_csv(paths["audit"], AUDIT_FIELDS, list(result.get("_audit_rows") or []))
        _write_csv(paths["counterfactual"], COUNTERFACTUAL_FIELDS, list(result.get("_counterfactual") or []))
        _write_csv(paths["symbol_review"], SYMBOL_REVIEW_FIELDS, list(result.get("_symbol_review") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        self._write_report(paths["report"], result)
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        lines = [
            "# Phase492 — 20260622 PM Entry Failure Audit",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
            "## Session",
            "",
            f"- Day: {result.get('day')} | Session: `{result.get('session')}`",
            f"- Canonical PF: **{result.get('canonical_pf')}**",
            f"- Failures (stop+NP): **{result.get('failure_count')}** | Winners (trailing): **{result.get('winner_count')}**",
            "",
            "## 必須回答",
            "",
            f"1. **PM崩壊の主因:** {m.get('pm_collapse_primary_cause')}",
            f"2. **6522/6976/6981:** {m.get('6522_6976_6981_eval')}",
            f"3. **late_chase仮説:** {m.get('late_chase_hypothesis')}",
            f"4. **実装価値:** {m.get('implementation_value')}",
            f"5. **過学習リスク:** {m.get('overfit_risk')}",
            "",
            "## Feature diff (failures vs trailing winners)",
            "",
            f"```json\n{json.dumps(m.get('failure_vs_winner_feature_diff'), indent=2, ensure_ascii=False)}\n```",
            "",
            "## Counterfactual",
            "",
            f"```json\n{json.dumps(m.get('counterfactual'), indent=2, ensure_ascii=False, default=str)}\n```",
            "",
            "## Outputs",
            "",
            "- `phase492_20260622_pm_entry_failure_audit.csv`",
            "- `phase492_20260622_pm_counterfactual.csv`",
            "- `phase492_20260622_pm_symbol_review.csv`",
            "- `phase492_summary.json`",
            "",
        ]
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
