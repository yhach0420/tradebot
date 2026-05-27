"""
Phase 153a: Quantify fade vs low-price / high-tick-ratio risk (review only).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.small_paper_performance_review import _profit_factor

SESSION_DATE = "20260525"
BASELINE_PF = 0.482
BASELINE_AVG = -0.1591

PRICE_BUCKETS: tuple[tuple[str, Optional[float], Optional[float]], ...] = (
    ("price_lt_50", None, 50.0),
    ("price_50_100", 50.0, 100.0),
    ("price_100_300", 100.0, 300.0),
    ("price_300_1000", 300.0, 1000.0),
    ("price_ge_1000", 1000.0, None),
)

TICK_RATIO_BUCKETS: tuple[tuple[str, Optional[float], Optional[float]], ...] = (
    ("tick_ratio_gt_5pct", 5.0, None),
    ("tick_ratio_3_5pct", 3.0, 5.0),
    ("tick_ratio_2_3pct", 2.0, 3.0),
    ("tick_ratio_1_2pct", 1.0, 2.0),
    ("tick_ratio_lt_1pct", None, 1.0),
)

PHASE150_FADE_CANDIDATES: tuple[tuple[str, str, str], ...] = (
    ("P150_E", "disable_momentum_fade_exit", "phase150"),
    ("P150_F", "disable_quality_decay_exit", "phase150"),
    ("P150_H", "momentum_fade_breakdown_confirmed", "phase150"),
    ("P150_I", "take_observer_as_exit", "phase150"),
)

PHASE152_GAP_REJECT = ("P152_E", "reject_gap_entry_gt3pct", "phase152")

FILTER_WHATIF: tuple[tuple[str, str, str], ...] = (
    ("A", "combined_current_all", "baseline"),
    ("B", "entry_price_ge_30", "price_ge_30"),
    ("C", "entry_price_ge_50", "price_ge_50"),
    ("D", "entry_price_ge_100", "price_ge_100"),
    ("E", "tick_ratio_le_5pct", "tick_ratio_le_5"),
    ("F", "tick_ratio_le_3pct", "tick_ratio_le_3"),
    ("G", "tick_ratio_le_2pct", "tick_ratio_le_2"),
)


def jpx_tick_size_yen(price: float, *, narrow_topix500: bool = False) -> float:
    """JPX quotation unit (Other Issues table; default for non-TOPIX500 small caps)."""
    p = float(price)
    if p <= 0:
        return 1.0
    if narrow_topix500:
        if p <= 1000:
            return 0.1
        if p <= 10000:
            return 1.0
        if p <= 100000:
            return 10.0
        if p <= 300000:
            return 50.0
        if p <= 1000000:
            return 100.0
        if p <= 3000000:
            return 500.0
        if p <= 10000000:
            return 1000.0
        if p <= 30000000:
            return 5000.0
        return 10000.0
    if p <= 3000:
        return 1.0
    if p <= 5000:
        return 5.0
    if p <= 30000:
        return 10.0
    if p <= 50000:
        return 50.0
    if p <= 300000:
        return 100.0
    if p <= 500000:
        return 500.0
    if p <= 3000000:
        return 1000.0
    if p <= 5000000:
        return 5000.0
    if p <= 30000000:
        return 10000.0
    return 100000.0


def tick_ratio_pct(entry_price: float, *, narrow_topix500: bool = False) -> float:
    if entry_price <= 0:
        return 0.0
    tick = jpx_tick_size_yen(entry_price, narrow_topix500=narrow_topix500)
    return round(tick / entry_price * 100.0, 4)


def _enrich_trade(
    trade: Mapping[str, Any],
    *,
    stop_meta: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    sym = str(trade.get("symbol") or "")
    ent = str(trade.get("entry_time") or "")
    entry_px = float(trade.get("entry_price") or 0)
    tr = tick_ratio_pct(entry_px)
    meta = stop_meta.get((sym, ent), {})
    return {
        **dict(trade),
        "entry_price": entry_px,
        "tick_size_yen": jpx_tick_size_yen(entry_px),
        "tick_ratio_pct": tr,
        "realized_pnl_pct": float(trade.get("realized_pnl_pct") or 0),
        "hold_duration_sec": float(trade.get("hold_duration_sec") or 0),
        "close_reason": str(trade.get("close_reason") or ""),
        "gap_through_stop": meta.get("gap_through_stop", ""),
        "entry_jump_vs_prior_median_pct": meta.get("entry_jump_vs_prior_median_pct", ""),
    }


def _in_bucket(
    value: float,
    low: Optional[float],
    high: Optional[float],
    *,
    low_inclusive: bool = True,
    high_inclusive: bool = False,
) -> bool:
    if low is not None:
        if low_inclusive and value < low:
            return False
        if not low_inclusive and value <= low:
            return False
    if high is not None:
        if high_inclusive and value > high:
            return False
        if not high_inclusive and value >= high:
            return False
    return True


def _bucket_metrics(trades: Sequence[Mapping[str, Any]], bucket_id: str) -> dict[str, Any]:
    pnls = [float(t.get("realized_pnl_pct") or 0) for t in trades]
    n = len(trades)
    stops = [t for t in trades if str(t.get("close_reason")) == "stop_hit"]
    holds = [float(t.get("hold_duration_sec") or 0) for t in trades]
    pf = _profit_factor(pnls)
    return {
        "bucket_id": bucket_id,
        "trade_count": n,
        "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4) if n else None,
        "structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
        "stop_hit_count": len(stops),
        "stop_hit_rate": round(len(stops) / n, 4) if n else None,
        "stop_loss_sum_pct": round(sum(float(t.get("realized_pnl_pct") or 0) for t in stops), 4),
        "avg_hold_sec": round(statistics.mean(holds), 1) if holds else None,
        "max_loss_pct": round(min(pnls), 4) if pnls else None,
        "max_gain_pct": round(max(pnls), 4) if pnls else None,
    }


def build_price_bucket_summary(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bid, lo, hi in PRICE_BUCKETS:
        subset = [
            t
            for t in trades
            if _in_bucket(float(t.get("entry_price") or 0), lo, hi, low_inclusive=True, high_inclusive=False)
        ]
        rows.append(_bucket_metrics(subset, bid))
    return rows


def build_tick_ratio_summary(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for bid, lo, hi in TICK_RATIO_BUCKETS:
        subset = [
            t
            for t in trades
            if _in_bucket(
                float(t.get("tick_ratio_pct") or 0),
                lo,
                hi,
                low_inclusive=False if lo is not None else True,
                high_inclusive=False,
            )
        ]
        rows.append(_bucket_metrics(subset, bid))
    return rows


def build_stop_loss_contribution(
    trades: Sequence[Mapping[str, Any]],
    *,
    price_buckets: Sequence[Mapping[str, Any]],
    tick_buckets: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stops = [t for t in trades if str(t.get("close_reason")) == "stop_hit"]
    for t in stops:
        rows.append(
            {
                "row_type": "stop_trade",
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "entry_price": t.get("entry_price"),
                "tick_size_yen": t.get("tick_size_yen"),
                "tick_ratio_pct": t.get("tick_ratio_pct"),
                "gap_through_stop": t.get("gap_through_stop"),
                "pnl_pct": t.get("realized_pnl_pct"),
                "price_bucket": _price_bucket_label(float(t.get("entry_price") or 0)),
                "tick_ratio_bucket": _tick_ratio_label(float(t.get("tick_ratio_pct") or 0)),
            }
        )
    for label, src, key in (
        ("price_bucket_agg", price_buckets, "bucket_id"),
        ("tick_ratio_bucket_agg", tick_buckets, "bucket_id"),
    ):
        for b in src:
            if int(b.get("stop_hit_count") or 0) <= 0:
                continue
            rows.append(
                {
                    "row_type": label,
                    "bucket": b.get(key),
                    "stop_hit_count": b.get("stop_hit_count"),
                    "stop_loss_sum_pct": b.get("stop_loss_sum_pct"),
                    "trade_count": b.get("trade_count"),
                    "structural_pf": b.get("structural_pf"),
                }
            )
    return rows


def _price_bucket_label(px: float) -> str:
    for bid, lo, hi in PRICE_BUCKETS:
        if _in_bucket(px, lo, hi):
            return bid
    return "unknown"


def _tick_ratio_label(tr: float) -> str:
    for bid, lo, hi in TICK_RATIO_BUCKETS:
        if _in_bucket(tr, lo, hi, low_inclusive=False if lo else True, high_inclusive=False):
            return bid
    return "unknown"


def _trade_passes_filter(t: Mapping[str, Any], filter_key: str) -> bool:
    px = float(t.get("entry_price") or 0)
    tr = float(t.get("tick_ratio_pct") or 0)
    if filter_key == "baseline":
        return True
    if filter_key == "price_ge_30":
        return px >= 30
    if filter_key == "price_ge_50":
        return px >= 50
    if filter_key == "price_ge_100":
        return px >= 100
    if filter_key == "tick_ratio_le_5":
        return tr <= 5.0
    if filter_key == "tick_ratio_le_3":
        return tr <= 3.0
    if filter_key == "tick_ratio_le_2":
        return tr <= 2.0
    return True


def build_filter_whatif(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    baseline_pnls = [float(t.get("realized_pnl_pct") or 0) for t in trades]
    baseline_pf = _profit_factor(baseline_pnls)
    baseline_avg = statistics.mean(baseline_pnls) if baseline_pnls else 0.0
    baseline_max_loss = min(baseline_pnls) if baseline_pnls else 0.0

    rows: list[dict[str, Any]] = []
    for sid, label, fkey in FILTER_WHATIF:
        kept = [t for t in trades if _trade_passes_filter(t, fkey)]
        excluded = [t for t in trades if not _trade_passes_filter(t, fkey)]
        pnls = [float(t.get("realized_pnl_pct") or 0) for t in kept]
        stops = [t for t in kept if str(t.get("close_reason")) == "stop_hit"]
        pf = _profit_factor(pnls)
        max_loss = min(pnls) if pnls else None
        missed_good = sum(1 for t in excluded if float(t.get("realized_pnl_pct") or 0) > 0)
        rows.append(
            {
                "scenario_id": sid,
                "scenario": label,
                "filter_key": fkey,
                "trade_count": len(kept),
                "excluded_count": len(excluded),
                "structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
                "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
                "max_loss_pct": round(max_loss, 4) if max_loss is not None else None,
                "stop_hit_count": len(stops),
                "stop_loss_sum_pct": round(
                    sum(float(t.get("realized_pnl_pct") or 0) for t in stops), 4
                ),
                "missed_good_trade_count": missed_good,
                "delta_pf_vs_baseline": round(float(pf or 0) - float(baseline_pf or 0), 4)
                if pf is not None and baseline_pf is not None
                else None,
                "delta_avg_pnl_vs_baseline": round(statistics.mean(pnls) - baseline_avg, 4)
                if pnls
                else None,
                "delta_max_loss_vs_baseline": round(float(max_loss or 0) - baseline_max_loss, 4)
                if max_loss is not None
                else None,
            }
        )
    return rows


def _load_phase150_scenarios(path: Path) -> dict[str, dict[str, Any]]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return {str(r.get("scenario_id")): r for r in rows}


def _load_phase152_gap_row(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f):
            if str(r.get("scenario_id")) == "E":
                return dict(r)
    return {}


def build_root_cause_comparison(
    filter_whatif: Sequence[Mapping[str, Any]],
    *,
    phase150_path: Path,
    phase152_path: Path,
    baseline_max_loss: float,
) -> list[dict[str, Any]]:
    p150 = _load_phase150_scenarios(phase150_path)
    p152_e = _load_phase152_gap_row(phase152_path)
    by_id = {str(r["scenario_id"]): r for r in filter_whatif}
    baseline = by_id.get("A", {})

    rows: list[dict[str, Any]] = []
    candidates: list[tuple[str, str, str, Mapping[str, Any]]] = []

    for sid, label, fkey in (
        ("153a_C", "entry_price_ge_50", "C"),
        ("153a_E", "tick_ratio_le_5pct", "E"),
        ("153a_D", "entry_price_ge_100", "D"),
    ):
        r = by_id.get(fkey)
        if r:
            candidates.append((sid, label, "phase153a", r))

    for sid, label, src in PHASE150_FADE_CANDIDATES:
        r = p150.get(sid.replace("P150_", ""))
        if r:
            candidates.append((sid, label, src, r))

    if p152_e:
        candidates.append((PHASE152_GAP_REJECT[0], PHASE152_GAP_REJECT[1], "phase152", p152_e))

    for cid, label, src, r in candidates:
        pf = float(r.get("structural_pf") or 0)
        avg = float(r.get("avg_pnl_pct") or 0)
        max_loss = float(r.get("max_loss_pct") or baseline_max_loss)
        rows.append(
            {
                "candidate_id": cid,
                "candidate": label,
                "source": src,
                "trade_count": r.get("trade_count"),
                "structural_pf": pf,
                "avg_pnl_pct": avg,
                "max_loss_pct": max_loss,
                "delta_pf_vs_combined": round(pf - BASELINE_PF, 4),
                "delta_avg_pnl_vs_combined": round(avg - BASELINE_AVG, 4),
                "delta_max_loss_vs_combined": round(max_loss - baseline_max_loss, 4),
                "stop_hit_count": r.get("stop_hit_count"),
                "missed_good_trade_count": r.get("missed_good_trade_count", r.get("rejected_entry_count", "")),
            }
        )
    return rows


def determine_phase153a_verdict(
    *,
    price_buckets: Sequence[Mapping[str, Any]],
    tick_buckets: Sequence[Mapping[str, Any]],
    filter_whatif: Sequence[Mapping[str, Any]],
    root_cause: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, Any]],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    by_f = {str(r["scenario_id"]): r for r in filter_whatif}
    lt50 = next((b for b in price_buckets if b["bucket_id"] == "price_lt_50"), {})
    gt5 = next((b for b in tick_buckets if b["bucket_id"] == "tick_ratio_gt_5pct"), {})

    under50_n = int(lt50.get("trade_count") or 0)
    under50_symbols = sorted({str(t.get("symbol")) for t in trades if float(t.get("entry_price") or 0) < 50})
    notes.append(f"price_lt_50: {under50_n} trades symbols={under50_symbols}")
    notes.append(
        f"price_lt_50 PF={lt50.get('structural_pf')} stop_loss_sum={lt50.get('stop_loss_sum_pct')}%"
    )
    notes.append(
        f"tick_ratio_gt_5pct: n={gt5.get('trade_count')} PF={gt5.get('structural_pf')} "
        f"stop_sum={gt5.get('stop_loss_sum_pct')}%"
    )

    pf_c = float(by_f.get("C", {}).get("structural_pf") or 0)
    pf_e = float(by_f.get("E", {}).get("structural_pf") or 0)
    pf_a = float(by_f.get("A", {}).get("structural_pf") or 0)
    missed_c = int(by_f.get("C", {}).get("missed_good_trade_count") or 0)

    best_price = max(
        (r for r in root_cause if "price" in str(r.get("candidate", "")) or "tick_ratio" in str(r.get("candidate", ""))),
        key=lambda r: float(r.get("delta_pf_vs_combined") or 0),
        default=None,
    )
    best_fade = max(
        (r for r in root_cause if str(r.get("source")) == "phase150"),
        key=lambda r: float(r.get("delta_pf_vs_combined") or 0),
        default=None,
    )

    if under50_n <= 3 and under50_symbols == ["5856.T"] and pf_c >= 1.0 and missed_c == 0:
        if best_fade and float(best_fade.get("delta_pf_vs_combined") or 0) < float(best_price.get("delta_pf_vs_combined") or 0) - 0.3:
            return "5856_outlier_only", notes + [
                "All sub-50yen trades are 5856.T; excluding price>=50 matches gap-reject PF lift.",
                f"Best fade candidate delta_pf={best_fade.get('delta_pf_vs_combined')} "
                f"vs price>=50 delta_pf={best_price.get('delta_pf_vs_combined')}.",
            ]

    if pf_c > pf_a + 0.5 and pf_e > pf_a + 0.5 and missed_c <= 1:
        if best_fade and float(best_fade.get("delta_pf_vs_combined") or 0) > 0.15:
            return "multiple_root_causes", notes + [
                "Low-price filter fixes PF/max_loss; fade-disable still material on path replay.",
            ]
        return "low_price_filter_promising", notes + [
            f"price>=50 PF={pf_c:.4f} missed_good={missed_c}."
        ]

    if pf_e > pf_a + 0.5:
        return "tick_ratio_filter_promising", notes + [f"tick_ratio<=5% PF={pf_e:.4f}."]

    fade_delta = float(best_fade.get("delta_pf_vs_combined") or 0) if best_fade else 0
    price_delta = float(best_price.get("delta_pf_vs_combined") or 0) if best_price else 0
    if fade_delta >= price_delta + 0.05:
        return "fade_problem_still_dominant", notes + [
            f"Fade what-if delta_pf={fade_delta} >= price-filter delta_pf={price_delta} on replay metrics."
        ]

    return "multiple_root_causes", notes + ["Mixed signals; see bucket tables and root_cause_comparison."]


def _load_stop_meta(reports_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    path = reports_dir / "phase152_stop_hit_trades.csv"
    if not path.is_file():
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            key = (str(row.get("symbol")), str(row.get("entry_time")))
            out[key] = row
    return out


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def run_phase153a_low_price_risk_review(
    session_dir: Path,
    *,
    reports_dir: Path,
) -> dict[str, Any]:
    session_dir = session_dir.resolve()
    reports_dir.mkdir(parents=True, exist_ok=True)

    trades_path = session_dir / "structural_trades.csv"
    with trades_path.open(encoding="utf-8", newline="") as f:
        raw = list(csv.DictReader(f))

    stop_meta = _load_stop_meta(reports_dir)
    trades = [_enrich_trade(t, stop_meta=stop_meta) for t in raw]

    price_buckets = build_price_bucket_summary(trades)
    tick_buckets = build_tick_ratio_summary(trades)
    stop_contrib = build_stop_loss_contribution(
        trades, price_buckets=price_buckets, tick_buckets=tick_buckets
    )
    filter_whatif = build_filter_whatif(trades)
    baseline_pnls = [float(t.get("realized_pnl_pct") or 0) for t in trades]
    baseline_max_loss = min(baseline_pnls) if baseline_pnls else 0.0

    root_cause = build_root_cause_comparison(
        filter_whatif,
        phase150_path=reports_dir / "phase150_exit_whatif_scenarios.csv",
        phase152_path=reports_dir / "phase152_stop_hit_whatif_scenarios.csv",
        baseline_max_loss=baseline_max_loss,
    )
    verdict, verdict_notes = determine_phase153a_verdict(
        price_buckets=price_buckets,
        tick_buckets=tick_buckets,
        filter_whatif=filter_whatif,
        root_cause=root_cause,
        trades=trades,
    )

    low_price_share = sum(1 for t in trades if float(t.get("entry_price") or 0) < 50)
    high_tick_share = sum(1 for t in trades if float(t.get("tick_ratio_pct") or 0) > 5.0)

    report: dict[str, Any] = {
        "phase": "153a",
        "mode": "low_price_tick_ratio_risk_review",
        "what_if_only": True,
        "session_dir": str(session_dir),
        "session_date": SESSION_DATE,
        "baseline_combined_pf": BASELINE_PF,
        "baseline_avg_pnl_pct": BASELINE_AVG,
        "trade_count": len(trades),
        "low_price_lt_50_count": low_price_share,
        "high_tick_ratio_gt_5pct_count": high_tick_share,
        "verdict": verdict,
        "verdict_options": {
            "A": "low_price_filter_promising",
            "B": "tick_ratio_filter_promising",
            "C": "fade_problem_still_dominant",
            "D": "5856_outlier_only",
            "E": "multiple_root_causes",
        },
        "verdict_notes": verdict_notes,
        "price_bucket_summary": price_buckets,
        "tick_ratio_summary": tick_buckets,
        "filter_whatif": filter_whatif,
        "root_cause_comparison": root_cause,
        "constraints": [
            "no_production_yaml_change",
            "no_entry_exit_universe_change",
            "review_whatif_only",
            "take_exit_shadow_not_in_scope",
        ],
    }

    _write_csv(reports_dir / "phase153a_price_bucket_summary.csv", price_buckets)
    _write_csv(reports_dir / "phase153a_tick_ratio_summary.csv", tick_buckets)
    _write_csv(reports_dir / "phase153a_stop_loss_contribution.csv", stop_contrib)
    _write_csv(reports_dir / "phase153a_low_price_filter_whatif.csv", filter_whatif)
    _write_csv(reports_dir / "phase153a_root_cause_comparison.csv", root_cause)
    (reports_dir / "phase153a_low_price_risk_review.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    report["output_files"] = {
        "json": str(reports_dir / "phase153a_low_price_risk_review.json"),
        "price_bucket_csv": str(reports_dir / "phase153a_price_bucket_summary.csv"),
        "tick_ratio_csv": str(reports_dir / "phase153a_tick_ratio_summary.csv"),
        "stop_contrib_csv": str(reports_dir / "phase153a_stop_loss_contribution.csv"),
        "filter_whatif_csv": str(reports_dir / "phase153a_low_price_filter_whatif.csv"),
        "root_cause_csv": str(reports_dir / "phase153a_root_cause_comparison.csv"),
    }
    return report
