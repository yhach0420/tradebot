"""
Phase379: Period-B low-MFE stop_hit deep review (Stack C, 20260528-20260612).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase367_low_mfe_residual_forensic import (
    _is_board_low,
    _is_weak_momentum,
    enrich_residual_trade,
    load_session_residual_forensic,
)
from research.phase377_daily_regime_breakdown import PERIOD_B_END, PERIOD_B_START, PRIMARY_STACK
from research.phase379_380_period_b_eval import (
    FOCUS_EXCLUDE_DAY,
    cohens_d,
    cohort_label,
    evaluate_variant_shadow,
    in_period_b,
    is_low_mfe_stop,
    production_candidate_pass,
)
from research.phase366_stophit_reclassification import production_kept_trades
from small_paper.near_day_high_low_mom_entry_guard_shadow import would_block_near_day_high_low_mom_guard
from small_paper.pullback_misread_entry_guard_shadow import would_block_pullback_dynamic40_shadow

JST = ZoneInfo("Asia/Tokyo")

NUMERIC_FEATURES = (
    "entry_momentum_score",
    "entry_rise_1min_pct",
    "entry_rise_5min_pct",
    "entry_rise_10min_pct",
    "entry_vwap_dev_pct",
    "day_high_distance_pct",
    "price_range_position",
    "entry_imbalance_percentile",
    "trading_value",
    "turnover_proxy",
    "hold_sec",
    "peak_mfe_pct",
    "peak_mae_pct",
)

FEATURES_CSV_FIELDS = [
    "feature",
    "cohort",
    "compare_to",
    "count",
    "mean",
    "median",
    "delta_vs_compare",
    "effect_size",
    "rank",
]

TRADES_CSV_FIELDS = [
    "day_key",
    "symbol",
    "entry_time",
    "exit_time",
    "pnl_yen_100",
    "pnl_pct",
    "exit_reason_canonical",
    "peak_mfe_pct",
    "peak_mae_pct",
    "hold_sec",
    "universe_group",
    "session_kind",
    "entry_momentum_score",
    "entry_vwap_dev_pct",
    "entry_rise_5min_pct",
    "entry_rise_10min_pct",
    "day_high_distance_pct",
    "price_range_position",
    "entry_imbalance_percentile",
    "board_dynamic_tier",
    "entry_time_bucket",
    "cohort",
]

BY_VARIANT_FIELDS = [
    "variant_id",
    "label",
    "scope",
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
    "excluded_overlap_355_364",
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
    "low_mfe_stop_count",
    "total_loss_yen_100",
    "avg_momentum",
    "avg_imbalance_pctile",
    "board_low_count",
    "dynamic40_count",
]


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _session_day_key(session_result: Mapping[str, Any]) -> str:
    day = str(session_result.get("day_key") or "")
    if day:
        return day
    meta = session_result.get("session_meta") or {}
    return str(meta.get("day_key") or meta.get("day") or "")


def load_session_period_b_trades(
    session_meta: Mapping[str, Any], *, reports_dir: Path
) -> dict[str, Any]:
    day = str(session_meta.get("day_key") or session_meta.get("day") or "")
    if not in_period_b(day):
        return {"error": "outside_period_b", "trades": [], "all_trades": []}

    result = load_session_residual_forensic(session_meta, reports_dir=reports_dir)
    if result.get("error"):
        return {**result, "trades": [], "all_trades": []}

    all_trades = [t for t in result.get("all_production_enriched") or [] if in_period_b(str(t.get("day_key") or day))]
    low_mfe = [t for t in all_trades if is_low_mfe_stop(t)]
    return {
        **result,
        "day_key": day,
        "all_trades": all_trades,
        "low_mfe_stop_trades": low_mfe,
        "trade_count": len(all_trades),
        "low_mfe_stop_count": len(low_mfe),
        "error": "",
    }


def _feature_value(trade: Mapping[str, Any], feature: str) -> Optional[float]:
    if feature == "hold_sec":
        return _float(trade.get("hold_sec")) or _float(trade.get("hold_duration_sec"))
    if feature == "entry_rise_1min_pct":
        return _float(trade.get("entry_rise_1min_pct"))
    return _float(trade.get(feature))


def feature_distribution_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    cohort: str,
    compare_trades: Sequence[Mapping[str, Any]],
    compare_label: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for feat in NUMERIC_FEATURES:
        vals = [v for t in trades if (v := _feature_value(t, feat)) is not None]
        cmp_vals = [v for t in compare_trades if (v := _feature_value(t, feat)) is not None]
        if not vals:
            continue
        effect = cohens_d(vals, cmp_vals) if cmp_vals else None
        mean_v = round(statistics.mean(vals), 4)
        cmp_mean = round(statistics.mean(cmp_vals), 4) if cmp_vals else None
        rows.append(
            {
                "feature": feat,
                "cohort": cohort,
                "compare_to": compare_label,
                "count": len(vals),
                "mean": mean_v,
                "median": round(statistics.median(vals), 4),
                "delta_vs_compare": round(mean_v - cmp_mean, 4) if cmp_mean is not None else None,
                "effect_size": effect,
                "rank": 0,
            }
        )
    rows.sort(key=lambda r: abs(_float(r.get("effect_size")) or 0.0), reverse=True)
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


def _board_tier(trade: Mapping[str, Any]) -> str:
    return str(trade.get("board_dynamic_tier") or trade.get("board_dynamic_trailing_tier") or "unknown")


def _overlap_fraction(
    trades: Sequence[Mapping[str, Any]],
    block_fn: Callable[[Mapping[str, Any]], bool],
    guard_fn: Callable[[Mapping[str, Any]], bool],
) -> float:
    blocked = [t for t in trades if block_fn(t)]
    if not blocked:
        return 0.0
    overlap = sum(1 for t in blocked if guard_fn(t))
    return overlap / len(blocked)


def _is_dynamic40(trade: Mapping[str, Any]) -> bool:
    return str(trade.get("universe_group") or "") == "dynamic40"


def _variant_specs() -> list[dict[str, Any]]:
    return [
        {
            "variant_id": "A",
            "label": "board_low + momentum < 0.25",
            "scope": "all",
            "block": lambda t: _is_board_low(t) and _is_weak_momentum(t, 0.25),
        },
        {
            "variant_id": "B",
            "label": "board_low + momentum < 0.30",
            "scope": "all",
            "block": lambda t: _is_board_low(t) and _is_weak_momentum(t, 0.30),
        },
        {
            "variant_id": "C",
            "label": "imbalance_pctile < 20 + momentum < 0.30",
            "scope": "all",
            "block": lambda t: (_float(t.get("entry_imbalance_percentile")) or 100.0) < 20
            and _is_weak_momentum(t, 0.30),
        },
        {
            "variant_id": "D",
            "label": "vwap_dev < 0 + rise_5min >= 0 (non-Phase355)",
            "scope": "all",
            "block": lambda t: (_float(t.get("entry_vwap_dev_pct")) or 0.0) < 0
            and (_float(t.get("entry_rise_5min_pct")) or 0.0) >= 0,
        },
        {
            "variant_id": "E",
            "label": "price_range_position >= 0.85 + momentum < 0.30",
            "scope": "all",
            "block": lambda t: (_float(t.get("price_range_position")) or 0.0) >= 0.85
            and _is_weak_momentum(t, 0.30),
        },
        {
            "variant_id": "F",
            "label": "board_low + momentum < 0.25 (Dynamic40 only)",
            "scope": "dynamic40",
            "block": lambda t: _is_dynamic40(t) and _is_board_low(t) and _is_weak_momentum(t, 0.25),
        },
        {
            "variant_id": "G",
            "label": "board_low + momentum < 0.25 (AM only)",
            "scope": "am",
            "block": lambda t: str(t.get("session_kind") or "").lower() == "am"
            and _is_board_low(t)
            and _is_weak_momentum(t, 0.25),
        },
    ]


def _guard_block_fields(trade: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "entry_rise_5min_pct": trade.get("entry_rise_5min_pct"),
        "entry_vwap_dev_pct": trade.get("entry_vwap_dev_pct"),
        "universe_slot": trade.get("universe_slot"),
        "source_bucket": trade.get("source_bucket"),
        "universe_bucket": trade.get("universe_bucket"),
        "day_high_distance_pct": trade.get("day_high_distance_pct"),
        "entry_near_day_high_pct": trade.get("day_high_distance_pct"),
        "entry_momentum_score": trade.get("entry_momentum_score"),
    }


def variant_excluded_overlap(trade: Mapping[str, Any], block_fn: Callable[[Mapping[str, Any]], bool]) -> bool:
    if not block_fn(trade):
        return False
    fields = _guard_block_fields(trade)
    p355 = would_block_pullback_dynamic40_shadow(fields)
    p364 = would_block_near_day_high_low_mom_guard(fields) and _is_dynamic40(trade)
    return p355 or p364


def evaluate_variants(trades: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    variant_rows: list[dict[str, Any]] = []
    day_rows: list[dict[str, Any]] = []
    for spec in _variant_specs():
        block_fn = spec["block"]
        overlap_frac = max(
            _overlap_fraction(trades, block_fn, lambda t: would_block_pullback_dynamic40_shadow(_guard_block_fields(t))),
            _overlap_fraction(
                trades,
                block_fn,
                lambda t: would_block_near_day_high_low_mom_guard(_guard_block_fields(t)) and _is_dynamic40(t),
            ),
        )
        excluded = overlap_frac >= 0.95

        def _effective_block(t: Mapping[str, Any], fn=block_fn) -> bool:
            if excluded:
                return False
            return fn(t) and not variant_excluded_overlap(t, fn)

        metrics = evaluate_variant_shadow(
            trades,
            variant_id=str(spec["variant_id"]),
            would_block=_effective_block,
        )
        row = {
            "variant_id": spec["variant_id"],
            "label": spec["label"],
            "scope": spec["scope"],
            "excluded_overlap_355_364": excluded,
            **{k: metrics.get(k) for k in BY_VARIANT_FIELDS if k not in ("variant_id", "label", "scope", "excluded_overlap_355_364")},
        }
        variant_rows.append(row)
        baseline_by_day: dict[str, float] = defaultdict(float)
        for t in trades:
            baseline_by_day[str(t.get("day_key") or "")] += float(_float(t.get("pnl_yen_100")) or 0.0)
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


def by_symbol_low_mfe(low_mfe: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    acc: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "pnl": 0.0, "mom": [], "imb": [], "board_low": 0, "d40": 0}
    )
    for t in low_mfe:
        sym = str(t.get("symbol") or "")
        acc[sym]["count"] += 1
        acc[sym]["pnl"] += float(_float(t.get("pnl_yen_100")) or 0.0)
        m = _float(t.get("entry_momentum_score"))
        if m is not None:
            acc[sym]["mom"].append(m)
        im = _float(t.get("entry_imbalance_percentile"))
        if im is not None:
            acc[sym]["imb"].append(im)
        if _is_board_low(t):
            acc[sym]["board_low"] += 1
        if _is_dynamic40(t):
            acc[sym]["d40"] += 1
    rows = []
    for sym, v in sorted(acc.items(), key=lambda x: x[1]["pnl"]):
        rows.append(
            {
                "symbol": sym,
                "low_mfe_stop_count": v["count"],
                "total_loss_yen_100": round(v["pnl"], 2),
                "avg_momentum": round(statistics.mean(v["mom"]), 4) if v["mom"] else None,
                "avg_imbalance_pctile": round(statistics.mean(v["imb"]), 4) if v["imb"] else None,
                "board_low_count": v["board_low"],
                "dynamic40_count": v["d40"],
            }
        )
    return rows


def build_report(summary: Mapping[str, Any]) -> str:
    lines = [
        "# Phase379 Period-B Low-MFE StopHit Deep Review",
        "",
        f"**期間:** {PERIOD_B_START}–{PERIOD_B_END} | **Stack:** {PRIMARY_STACK}",
        "",
        "## 結論",
        "",
        f"- low-MFE stop_hit件数: {summary.get('low_mfe_stop_count')}",
        f"- 最大特徴差分: {summary.get('top_feature_delta')}",
        f"- 有望variant: {summary.get('promising_variants')}",
        f"- production candidate: {summary.get('production_candidates')}",
        f"- 過学習チェック: top_day_share / top_symbol_share / 6/12除外 を全variantで評価",
        "",
        "## 特徴量ランキング（low-MFE stop vs winning）",
        "",
    ]
    for row in (summary.get("top_features") or [])[:10]:
        lines.append(
            f"- {row.get('feature')}: effect={row.get('effect_size')} "
            f"delta={row.get('delta_vs_compare')}"
        )
    lines.extend(["", "## Variant shadow 結果", ""])
    for row in summary.get("variant_results") or []:
        lines.append(
            f"- {row.get('variant_id')} {row.get('label')}: delta={row.get('delta_yen')} "
            f"pf_delta={row.get('delta_pf')} candidate={row.get('production_candidate')}"
        )
    cons = summary.get("consistency_checks") or {}
    lines.extend(
        [
            "",
            "## Phase377/378整合",
            "",
            f"- trade_count_matches: {cons.get('trade_count_matches')}",
            f"- total_pnl_matches: {cons.get('total_pnl_matches')}",
            f"- low_mfe_count: {summary.get('low_mfe_stop_count')}",
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
class Phase379LowMfeStophitDeepReview:
    reports_dir: Path
    all_trades: list[dict[str, Any]] = field(default_factory=list)

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase379_low_mfe_stophit_deep_review_summary.json",
            "features": self.reports_dir / "phase379_low_mfe_stophit_features.csv",
            "by_variant": self.reports_dir / "phase379_low_mfe_stophit_by_variant.csv",
            "by_day": self.reports_dir / "phase379_low_mfe_stophit_by_day.csv",
            "by_symbol": self.reports_dir / "phase379_low_mfe_stophit_by_symbol.csv",
            "trades": self.reports_dir / "phase379_low_mfe_stophit_trades.csv",
            "report": self.reports_dir / "phase379_low_mfe_stophit_report.md",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.all_trades.extend(result.get("all_trades") or [])

    def analyze(self) -> dict[str, Any]:
        low_mfe = [t for t in self.all_trades if is_low_mfe_stop(t)]
        winning = [t for t in self.all_trades if cohort_label(t) == "winning"]
        high_mfe_stop = [t for t in self.all_trades if cohort_label(t) == "stop_hit_high_mfe"]
        non_stop_loss = [t for t in self.all_trades if cohort_label(t) == "non_stop_losing"]

        feature_rows: list[dict[str, Any]] = []
        feature_rows.extend(
            feature_distribution_rows(low_mfe, cohort="low_mfe_stop_hit", compare_trades=winning, compare_label="winning")
        )
        feature_rows.extend(
            feature_distribution_rows(
                low_mfe, cohort="low_mfe_stop_hit", compare_trades=high_mfe_stop, compare_label="stop_hit_high_mfe"
            )
        )
        for ug in ("dynamic40", "core10"):
            sub = [t for t in low_mfe if str(t.get("universe_group") or "") == ug]
            sub_win = [t for t in winning if str(t.get("universe_group") or "") == ug]
            if sub:
                feature_rows.extend(
                    feature_distribution_rows(sub, cohort=f"low_mfe_{ug}", compare_trades=sub_win, compare_label=f"winning_{ug}")
                )
        for sk in ("am", "pm"):
            sub = [t for t in low_mfe if str(t.get("session_kind") or "").lower() == sk]
            sub_win = [t for t in winning if str(t.get("session_kind") or "").lower() == sk]
            if sub:
                feature_rows.extend(
                    feature_distribution_rows(sub, cohort=f"low_mfe_{sk}", compare_trades=sub_win, compare_label=f"winning_{sk}")
                )

        top_vs_win = [
            r
            for r in feature_rows
            if r.get("cohort") == "low_mfe_stop_hit" and r.get("compare_to") == "winning"
        ]
        top_feature = top_vs_win[0] if top_vs_win else None

        variant_rows, day_rows = evaluate_variants(self.all_trades)
        promising = [r["variant_id"] for r in variant_rows if r.get("production_candidate") and not r.get("excluded_overlap_355_364")]
        candidates = [r["variant_id"] for r in variant_rows if r.get("production_candidate")]

        total_pnl = round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in self.all_trades), 2)
        phase377_ref = {}
        p377_path = self.reports_dir / "phase377_daily_regime_breakdown_summary.json"
        if p377_path.is_file():
            p377 = json.loads(p377_path.read_text(encoding="utf-8"))
            phase377_ref = (p377.get("period_metrics") or {}).get("period_b_20260528_20260612", {}).get(PRIMARY_STACK, {})

        trade_rows = []
        for t in self.all_trades:
            trade_rows.append(
                {
                    "day_key": t.get("day_key"),
                    "symbol": t.get("symbol"),
                    "entry_time": t.get("entry_time"),
                    "exit_time": t.get("exit_time"),
                    "pnl_yen_100": _float(t.get("pnl_yen_100")),
                    "pnl_pct": _float(t.get("pnl_pct")),
                    "exit_reason_canonical": t.get("exit_reason_canonical"),
                    "peak_mfe_pct": _float(t.get("peak_mfe_pct")),
                    "peak_mae_pct": _float(t.get("peak_mae_pct")),
                    "hold_sec": _float(t.get("hold_sec")) or _float(t.get("hold_duration_sec")),
                    "universe_group": t.get("universe_group"),
                    "session_kind": t.get("session_kind"),
                    "entry_momentum_score": _float(t.get("entry_momentum_score")),
                    "entry_vwap_dev_pct": _float(t.get("entry_vwap_dev_pct")),
                    "entry_rise_5min_pct": _float(t.get("entry_rise_5min_pct")),
                    "entry_rise_10min_pct": _float(t.get("entry_rise_10min_pct")),
                    "day_high_distance_pct": _float(t.get("day_high_distance_pct")),
                    "price_range_position": _float(t.get("price_range_position")),
                    "entry_imbalance_percentile": _float(t.get("entry_imbalance_percentile")),
                    "board_dynamic_tier": _board_tier(t),
                    "entry_time_bucket": t.get("entry_time_bucket"),
                    "cohort": cohort_label(t),
                }
            )

        return {
            "phase": 379,
            "title": "Period-B low-MFE stop_hit deep review",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "period": {"start": PERIOD_B_START, "end": PERIOD_B_END},
            "stack_id": PRIMARY_STACK,
            "trade_count": len(self.all_trades),
            "low_mfe_stop_count": len(low_mfe),
            "winning_count": len(winning),
            "total_pnl_yen_100": total_pnl,
            "top_feature_delta": (
                {
                    "feature": top_feature.get("feature"),
                    "effect_size": top_feature.get("effect_size"),
                    "delta_vs_winning": top_feature.get("delta_vs_compare"),
                }
                if top_feature
                else None
            ),
            "top_features": top_vs_win[:15],
            "promising_variants": promising,
            "production_candidates": candidates,
            "variant_results": variant_rows,
            "consistency_checks": {
                "phase377_period_b_trade_count": phase377_ref.get("trade_count"),
                "phase377_period_b_total_pnl": phase377_ref.get("total_pnl_yen_100"),
                "trade_count_matches": len(self.all_trades) == _int(phase377_ref.get("trade_count")),
                "total_pnl_matches": total_pnl == _float(phase377_ref.get("total_pnl_yen_100")),
                "phase378_low_mfe_stop_reference": None,
            },
            "overfitting_rules_applied": True,
            "_feature_rows": feature_rows,
            "_variant_rows": variant_rows,
            "_day_rows": day_rows,
            "_symbol_rows": by_symbol_low_mfe(low_mfe),
            "_trade_rows": trade_rows,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        _write_csv(paths["features"], list(result["_feature_rows"]), FEATURES_CSV_FIELDS)
        _write_csv(paths["by_variant"], list(result["_variant_rows"]), BY_VARIANT_FIELDS)
        _write_csv(paths["by_day"], list(result["_day_rows"]), BY_DAY_FIELDS)
        _write_csv(paths["by_symbol"], list(result["_symbol_rows"]), BY_SYMBOL_FIELDS)
        _write_csv(paths["trades"], list(result["_trade_rows"]), TRADES_CSV_FIELDS)
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["report"].write_text(build_report(payload), encoding="utf-8")
        return paths


def _int(val: Any) -> int:
    try:
        return int(float(val or 0))
    except (TypeError, ValueError):
        return 0
