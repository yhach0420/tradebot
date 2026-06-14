"""
Phase380: Board quality entry signal review (Stack C, 20260528-20260612).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase367_low_mfe_residual_forensic import _is_board_low, _is_weak_momentum
from research.phase377_daily_regime_breakdown import PERIOD_B_END, PERIOD_B_START, PRIMARY_STACK
from research.phase379_380_period_b_eval import (
    evaluate_variant_shadow,
    in_period_b,
    is_low_mfe_stop,
)
from research.phase379_low_mfe_stophit_deep_review import load_session_period_b_trades

JST = ZoneInfo("Asia/Tokyo")

BUCKET_FIELDS = [
    "bucket_type",
    "bucket",
    "trade_count",
    "total_pnl_yen_100",
    "profit_factor",
    "win_rate",
    "expectancy_yen_100",
    "avg_pnl_yen_100",
    "avg_mfe_pct",
    "avg_mae_pct",
    "stop_hit_count",
    "stop_hit_rate",
    "low_mfe_stop_hit_count",
    "low_mfe_stop_hit_rate",
    "trailing_mfe_exit_count",
    "avg_hold_seconds",
    "dynamic40_count",
    "core10_count",
    "am_count",
    "pm_count",
]

BY_VARIANT_FIELDS = [
    "variant_id",
    "label",
    "removed_trade_count",
    "skipped_pnl_actual",
    "delta_yen",
    "delta_pf",
    "baseline_pf",
    "variant_pf",
    "stop_hit_reduction_count",
    "low_mfe_stop_hit_reduction_count",
    "improved_days",
    "worsened_days",
    "median_day_delta",
    "top_day_share",
    "top_symbol_share",
    "delta_excluding_20260612",
    "dynamic40_delta_yen",
    "core10_delta_yen",
    "am_delta_yen",
    "pm_delta_yen",
    "production_candidate",
]

BY_DAY_FIELDS = [
    "day_key",
    "variant_id",
    "baseline_pnl_yen_100",
    "variant_pnl_yen_100",
    "day_delta_yen",
]

BY_SYMBOL_FIELDS = [
    "symbol",
    "trade_count",
    "total_pnl_yen_100",
    "board_low_count",
    "board_mid_count",
    "board_high_count",
    "avg_imbalance_pctile",
]


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _pf(yens: Sequence[float]) -> Optional[float]:
    gp = sum(max(y, 0.0) for y in yens)
    gl = abs(sum(min(y, 0.0) for y in yens))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _board_tier(trade: Mapping[str, Any]) -> str:
    tier = str(trade.get("board_dynamic_tier") or trade.get("board_dynamic_trailing_tier") or "")
    if tier in ("board_high", "board_mid", "board_low"):
        return tier
    return "board_unknown"


def _imbalance_bucket(trade: Mapping[str, Any]) -> str:
    p = _float(trade.get("entry_imbalance_percentile"))
    if p is None:
        return "unknown"
    if p >= 70:
        return "imbalance_ge_70"
    if p >= 50:
        return "imbalance_50_70"
    if p >= 30:
        return "imbalance_30_50"
    return "imbalance_lt_30"


def bucket_metrics(trades: Sequence[Mapping[str, Any]], *, bucket_type: str, bucket: str) -> dict[str, Any]:
    yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in trades]
    mfes = [_float(t.get("peak_mfe_pct")) for t in trades]
    maes = [_float(t.get("peak_mae_pct")) for t in trades]
    holds = [_float(t.get("hold_sec")) or _float(t.get("hold_duration_sec")) for t in trades]
    stops = sum(1 for t in trades if str(t.get("exit_reason_canonical") or "") == "stop_hit")
    low_stops = sum(1 for t in trades if is_low_mfe_stop(t))
    trails = sum(1 for t in trades if str(t.get("exit_reason_canonical") or "") == "trailing_mfe_exit")
    n = len(trades)
    wins = sum(1 for y in yens if y > 0)
    mfe_valid = [float(m) for m in mfes if m is not None]
    mae_valid = [float(m) for m in maes if m is not None]
    hold_valid = [float(h) for h in holds if h is not None]
    return {
        "bucket_type": bucket_type,
        "bucket": bucket,
        "trade_count": n,
        "total_pnl_yen_100": round(sum(yens), 2) if yens else None,
        "profit_factor": _pf(yens),
        "win_rate": round(wins / n, 4) if n else None,
        "expectancy_yen_100": round(sum(yens) / n, 2) if n else None,
        "avg_pnl_yen_100": round(statistics.mean(yens), 2) if yens else None,
        "avg_mfe_pct": round(statistics.mean(mfe_valid), 4) if mfe_valid else None,
        "avg_mae_pct": round(statistics.mean(mae_valid), 4) if mae_valid else None,
        "stop_hit_count": stops,
        "stop_hit_rate": round(stops / n, 4) if n else None,
        "low_mfe_stop_hit_count": low_stops,
        "low_mfe_stop_hit_rate": round(low_stops / n, 4) if n else None,
        "trailing_mfe_exit_count": trails,
        "avg_hold_seconds": round(statistics.mean(hold_valid), 2) if hold_valid else None,
        "dynamic40_count": sum(1 for t in trades if str(t.get("universe_group") or "") == "dynamic40"),
        "core10_count": sum(1 for t in trades if str(t.get("universe_group") or "") == "core10"),
        "am_count": sum(1 for t in trades if str(t.get("session_kind") or "").lower() == "am"),
        "pm_count": sum(1 for t in trades if str(t.get("session_kind") or "").lower() == "pm"),
    }


def build_bucket_rows(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    board_groups: dict[str, list] = defaultdict(list)
    imb_groups: dict[str, list] = defaultdict(list)
    for t in trades:
        board_groups[_board_tier(t)].append(t)
        imb_groups[_imbalance_bucket(t)].append(t)
    for bucket in ("board_high", "board_mid", "board_low", "board_unknown"):
        rows.append(bucket_metrics(board_groups.get(bucket, []), bucket_type="board_tier", bucket=bucket))
    for bucket in ("imbalance_ge_70", "imbalance_50_70", "imbalance_30_50", "imbalance_lt_30", "unknown"):
        rows.append(bucket_metrics(imb_groups.get(bucket, []), bucket_type="imbalance_pctile", bucket=bucket))
    return rows


def _variant_specs() -> list[dict[str, Any]]:
    return [
        {"variant_id": "A", "label": "board_mid reject (simulate mid boost removal)", "block": lambda t: _board_tier(t) == "board_mid"},
        {"variant_id": "B", "label": "board_low reject", "block": lambda t: _board_tier(t) == "board_low"},
        {
            "variant_id": "C",
            "label": "board_low + weak_momentum reject",
            "block": lambda t: _board_tier(t) == "board_low" and _is_weak_momentum(t, 0.30),
        },
        {
            "variant_id": "D",
            "label": "imbalance_pctile < 20 reject",
            "block": lambda t: (_float(t.get("entry_imbalance_percentile")) or 100.0) < 20,
        },
        {
            "variant_id": "E",
            "label": "imbalance_pctile < 30 + momentum < 0.30 reject",
            "block": lambda t: (_float(t.get("entry_imbalance_percentile")) or 100.0) < 30
            and _is_weak_momentum(t, 0.30),
        },
        {
            "variant_id": "F",
            "label": "board_high only (reject non-high)",
            "block": lambda t: _board_tier(t) != "board_high",
        },
    ]


def evaluate_variants(trades: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    variant_rows: list[dict[str, Any]] = []
    day_rows: list[dict[str, Any]] = []
    baseline_by_day: dict[str, float] = defaultdict(float)
    for t in trades:
        baseline_by_day[str(t.get("day_key") or "")] += float(_float(t.get("pnl_yen_100")) or 0.0)

    for spec in _variant_specs():
        metrics = evaluate_variant_shadow(trades, variant_id=spec["variant_id"], would_block=spec["block"])
        variant_rows.append(
            {
                "variant_id": spec["variant_id"],
                "label": spec["label"],
                **{k: metrics.get(k) for k in BY_VARIANT_FIELDS if k not in ("variant_id", "label")},
            }
        )
        for day, delta in (metrics.get("day_deltas") or {}).items():
            day_rows.append(
                {
                    "day_key": day,
                    "variant_id": spec["variant_id"],
                    "baseline_pnl_yen_100": round(baseline_by_day.get(day, 0.0), 2),
                    "variant_pnl_yen_100": round(baseline_by_day.get(day, 0.0) + delta, 2),
                    "day_delta_yen": delta,
                }
            )
    return variant_rows, day_rows


def by_symbol_board(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "pnl": 0.0, "low": 0, "mid": 0, "high": 0, "imb": []}
    )
    for t in trades:
        sym = str(t.get("symbol") or "")
        acc[sym]["count"] += 1
        acc[sym]["pnl"] += float(_float(t.get("pnl_yen_100")) or 0.0)
        tier = _board_tier(t)
        if tier == "board_low":
            acc[sym]["low"] += 1
        elif tier == "board_mid":
            acc[sym]["mid"] += 1
        elif tier == "board_high":
            acc[sym]["high"] += 1
        im = _float(t.get("entry_imbalance_percentile"))
        if im is not None:
            acc[sym]["imb"].append(im)
    rows = []
    for sym, v in sorted(acc.items(), key=lambda x: x[1]["pnl"]):
        rows.append(
            {
                "symbol": sym,
                "trade_count": v["count"],
                "total_pnl_yen_100": round(v["pnl"], 2),
                "board_low_count": v["low"],
                "board_mid_count": v["mid"],
                "board_high_count": v["high"],
                "avg_imbalance_pctile": round(statistics.mean(v["imb"]), 4) if v["imb"] else None,
            }
        )
    return rows


def board_judgment(buckets: Sequence[Mapping[str, Any]], variants: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_tier = {r["bucket"]: r for r in buckets if r.get("bucket_type") == "board_tier"}
    by_imb = {r["bucket"]: r for r in buckets if r.get("bucket_type") == "imbalance_pctile"}
    mid = by_tier.get("board_mid", {})
    low = by_tier.get("board_low", {})
    high = by_tier.get("board_high", {})

    imb_order = ["imbalance_lt_30", "imbalance_30_50", "imbalance_50_70", "imbalance_ge_70"]
    imb_pnls = [_float(by_imb.get(k, {}).get("total_pnl_yen_100")) for k in imb_order if k in by_imb]
    monotonic = None
    if len(imb_pnls) == 4 and all(x is not None for x in imb_pnls):
        monotonic = imb_pnls[0] <= imb_pnls[1] <= imb_pnls[2] <= imb_pnls[3]

    promising = [v["variant_id"] for v in variants if v.get("production_candidate")]
    return {
        "board_mid_profitable": (_float(mid.get("total_pnl_yen_100")) or 0.0) > 0,
        "board_mid_pf": mid.get("profit_factor"),
        "board_low_loss_source": (_float(low.get("total_pnl_yen_100")) or 0.0) < 0,
        "board_high_profit_source": (_float(high.get("total_pnl_yen_100")) or 0.0) > 0,
        "imbalance_monotonic_pnl": monotonic,
        "board_mid_entry_boost_meaningful": (_float(mid.get("expectancy_yen_100")) or 0.0) > 0,
        "should_deprecate_board_mid": (_float(mid.get("total_pnl_yen_100")) or 0.0) < (_float(high.get("total_pnl_yen_100")) or 0.0),
        "board_needs_combo_with_momentum": True,
        "promising_variants": promising,
        "production_candidates": promising,
    }


def build_report(summary: Mapping[str, Any]) -> str:
    j = summary.get("board_judgment") or {}
    lines = [
        "# Phase380 Board Quality Entry Signal Review",
        "",
        f"**期間:** {PERIOD_B_START}–{PERIOD_B_END} | **Stack:** {PRIMARY_STACK}",
        "",
        "## 結論",
        "",
        f"- board_mid有効か: {j.get('board_mid_profitable')} (PF={j.get('board_mid_pf')})",
        f"- board_low損失源か: {j.get('board_low_loss_source')}",
        f"- board_high利益源か: {j.get('board_high_profit_source')}",
        f"- imbalance単調性: {j.get('imbalance_monotonic_pnl')}",
        f"- board_mid廃止検討: {j.get('should_deprecate_board_mid')}",
        f"- 有望variant: {j.get('promising_variants')}",
        f"- production candidate: {j.get('production_candidates')}",
        "",
        "## Board tier buckets",
        "",
    ]
    for row in summary.get("bucket_rows") or []:
        if row.get("bucket_type") == "board_tier":
            lines.append(
                f"- {row.get('bucket')}: trades={row.get('trade_count')} pnl={row.get('total_pnl_yen_100')} "
                f"pf={row.get('profit_factor')} stop_rate={row.get('stop_hit_rate')}"
            )
    lines.extend(["", "## Variant shadow", ""])
    for row in summary.get("variant_results") or []:
        lines.append(
            f"- {row.get('variant_id')}: delta={row.get('delta_yen')} candidate={row.get('production_candidate')}"
        )
    cons = summary.get("consistency_checks") or {}
    lines.extend(
        [
            "",
            "## Phase377整合",
            f"- trade_count_matches: {cons.get('trade_count_matches')}",
            f"- total_pnl_matches: {cons.get('total_pnl_matches')}",
            "",
        ]
    )
    return "\n".join(lines) + "\n"


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


@dataclass
class Phase380BoardQualityEntrySignalReview:
    reports_dir: Path
    all_trades: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase380_board_quality_entry_signal_summary.json",
            "buckets": self.reports_dir / "phase380_board_quality_buckets.csv",
            "by_variant": self.reports_dir / "phase380_board_quality_by_variant.csv",
            "by_day": self.reports_dir / "phase380_board_quality_by_day.csv",
            "by_symbol": self.reports_dir / "phase380_board_quality_by_symbol.csv",
            "report": self.reports_dir / "phase380_board_quality_report.md",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.all_trades.extend(result.get("all_trades") or [])

    def analyze(self) -> dict[str, Any]:
        bucket_rows = build_bucket_rows(self.all_trades)
        variant_rows, day_rows = evaluate_variants(self.all_trades)
        judgment = board_judgment(bucket_rows, variant_rows)
        total_pnl = round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in self.all_trades), 2)

        phase377_ref = {}
        p377_path = self.reports_dir / "phase377_daily_regime_breakdown_summary.json"
        if p377_path.is_file():
            p377 = json.loads(p377_path.read_text(encoding="utf-8"))
            phase377_ref = (p377.get("period_metrics") or {}).get("period_b_20260528_20260612", {}).get(PRIMARY_STACK, {})

        return {
            "phase": 380,
            "title": "Board quality entry signal review",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "period": {"start": PERIOD_B_START, "end": PERIOD_B_END},
            "stack_id": PRIMARY_STACK,
            "trade_count": len(self.all_trades),
            "total_pnl_yen_100": total_pnl,
            "bucket_rows": bucket_rows,
            "board_judgment": judgment,
            "variant_results": variant_rows,
            "consistency_checks": {
                "phase377_period_b_trade_count": phase377_ref.get("trade_count"),
                "phase377_period_b_total_pnl": phase377_ref.get("total_pnl_yen_100"),
                "trade_count_matches": len(self.all_trades) == int(phase377_ref.get("trade_count") or -1),
                "total_pnl_matches": total_pnl == _float(phase377_ref.get("total_pnl_yen_100")),
            },
            "overfitting_rules_applied": True,
            "_bucket_rows": bucket_rows,
            "_variant_rows": variant_rows,
            "_day_rows": day_rows,
            "_symbol_rows": by_symbol_board(self.all_trades),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        _write_csv(paths["buckets"], list(result["_bucket_rows"]), BUCKET_FIELDS)
        _write_csv(paths["by_variant"], list(result["_variant_rows"]), BY_VARIANT_FIELDS)
        _write_csv(paths["by_day"], list(result["_day_rows"]), BY_DAY_FIELDS)
        _write_csv(paths["by_symbol"], list(result["_symbol_rows"]), BY_SYMBOL_FIELDS)
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["report"].write_text(build_report(payload), encoding="utf-8")
        return paths
