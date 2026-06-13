"""
Phase345: Board failure exit forensic review (mfe_lt_0p2_confirm5).
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase337_exit_candidate_evaluation import _float, _memory_mb
from small_paper.board_failure_forensic_pack import VARIANT_ID

JST = ZoneInfo("Asia/Tokyo")
FOCUS_DAY = "20260528"
NOISE_DELTA_YEN = 300.0

FORENSIC_FIELDS = [
    "session_id",
    "day_key",
    "symbol",
    "position_id",
    "variant_id",
    "entry_time",
    "shadow_exit_time",
    "actual_exit_time",
    "shadow_exit_reason",
    "actual_exit_reason",
    "shadow_exit_price",
    "actual_exit_price",
    "shadow_pnl_yen_100",
    "actual_pnl_yen_100",
    "pnl_difference_yen_100",
    "peak_mfe_pct",
    "mae_pct",
    "post_shadow_max_up_pct",
    "post_shadow_max_down_pct",
    "entry_board_imbalance",
    "entry_imbalance_percentile",
    "shadow_board_imbalance",
    "shadow_board_imbalance_delta",
    "shadow_bid_qty",
    "shadow_ask_qty",
    "actual_board_imbalance",
    "actual_board_imbalance_delta",
    "shadow_triggered",
    "forensic_class",
]


def _dist(values: Sequence[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "median": None, "max": None, "mean": None}
    return {
        "count": len(values),
        "min": round(min(values), 4),
        "median": round(median(values), 4),
        "max": round(max(values), 4),
        "mean": round(mean(values), 4),
    }


def is_profit_take_miss(row: Mapping[str, Any]) -> bool:
    actual = float(_float(row.get("actual_pnl_yen_100")) or 0.0)
    delta = float(_float(row.get("pnl_difference_yen_100")) or 0.0)
    return actual > 0 and delta < -NOISE_DELTA_YEN and bool(row.get("shadow_triggered"))


def is_stop_hit_saved(row: Mapping[str, Any]) -> bool:
    actual = float(_float(row.get("actual_pnl_yen_100")) or 0.0)
    delta = float(_float(row.get("pnl_difference_yen_100")) or 0.0)
    return (
        str(row.get("actual_exit_reason") or "") == "stop_hit"
        and bool(row.get("shadow_triggered"))
        and delta > NOISE_DELTA_YEN
    )


def _symbol_contribution(rows: Sequence[Mapping[str, Any]], *, key_fn) -> list[dict[str, Any]]:
    by_sym: dict[str, float] = defaultdict(float)
    for row in rows:
        sym = str(row.get("symbol") or "")
        if sym:
            by_sym[sym] += float(key_fn(row))
    return [
        {"symbol": sym, "total_yen": round(v, 2)}
        for sym, v in sorted(by_sym.items(), key=lambda x: abs(x[1]), reverse=True)
    ]


def _build_conclusions(
    *,
    all_rows: Sequence[Mapping[str, Any]],
    triggered: Sequence[Mapping[str, Any]],
    profit_miss: Sequence[Mapping[str, Any]],
    class_counts: Counter[str],
) -> dict[str, Any]:
    false_pos = [r for r in triggered if r.get("forensic_class") == "B_false_positive"]
    correct = [r for r in triggered if r.get("forensic_class") == "A_correct_cut"]

    miss_by_exit = Counter(str(r.get("actual_exit_reason") or "") for r in profit_miss)
    miss_rebound = sum(
        1 for r in profit_miss if float(_float(r.get("post_shadow_max_up_pct")) or 0) >= 0.3
    )
    miss_board_recover = sum(
        1
        for r in profit_miss
        if float(_float(r.get("shadow_board_imbalance_delta")) or -1) > -0.08
        and float(_float(r.get("actual_board_imbalance_delta")) or -1) > -0.08
    )

    died_correctly = len(correct)
    false_rate = round(len(false_pos) / len(triggered), 4) if triggered else None
    correct_rate = round(len(correct) / len(triggered), 4) if triggered else None

    next_condition_hint = "board_recovery_filter"
    if miss_rebound > miss_board_recover:
        next_condition_hint = "price_recovery_or_time_filter"
    elif all(
        float(_float(r.get("peak_mfe_pct")) or 0) < 0.2 for r in false_pos[:5]
    ) if false_pos else False:
        next_condition_hint = "tighter_mfe_or_confirm_ticks"

    continue_research = bool(correct) and (correct_rate or 0) >= 0.35
    if false_rate is not None and false_rate > 0.55:
        continue_research = False

    return {
        "q1_did_exit_cut_dying_names": {
            "answer": died_correctly > 0 and (correct_rate or 0) >= 0.3,
            "correct_cut_count": died_correctly,
            "correct_cut_rate": correct_rate,
            "detail": (
                f"{died_correctly}/{len(triggered)} shadow exits classified as correct early cuts "
                f"(post-shadow continued down or delta positive)."
            ),
        },
        "q2_profit_take_miss_patterns": {
            "answer": miss_by_exit,
            "trailing_mfe_exit_count": miss_by_exit.get("trailing_mfe_exit", 0),
            "profit_take_like_count": miss_by_exit.get("profit_take", 0) + miss_by_exit.get("take_profit", 0),
            "post_shadow_rebound_count": miss_rebound,
            "board_recovery_at_actual_count": miss_board_recover,
            "detail": (
                "Profit miss concentrates on actual winners cut early by shadow; "
                "often trailing_mfe_exit would have captured more upside."
            ),
        },
        "q3_next_condition_priority": {
            "recommendation": next_condition_hint,
            "options": {
                "mfe": "Further tighten MFE ceiling or require lower peak MFE at arm",
                "board_recovery": "Require board_imbalance_delta still <= -0.08 at trigger AND no recovery streak",
                "price_recovery": "Abort shadow exit if price reclaims session low within N ticks",
            },
        },
        "q4_continue_board_failure_research": {
            "worth_continuing": continue_research,
            "rationale": (
                "Stop-hit compression works but false positive rate on winners is high on extended sessions."
                if not continue_research
                else "Correct cuts exist; filter false positives before adoption."
            ),
            "false_positive_rate": false_rate,
        },
    }


@dataclass
class Phase345ForensicReview:
    reports_dir: Path
    forensic_rows: list[dict[str, Any]] = field(default_factory=list)
    peak_memory_mb: float = 0.0
    wall_runtime_sec: float = 0.0
    parallel_enabled: bool = False
    parallel_max_workers: int = 1

    def note_memory(self) -> None:
        self.peak_memory_mb = max(self.peak_memory_mb, _memory_mb())

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase345_forensic_review_summary.json",
            "profit_take_miss_top20": self.reports_dir / "phase345_profit_take_miss_top20.csv",
            "worst_delta": self.reports_dir / "phase345_worst_delta_trades.csv",
            "stop_hit_saved": self.reports_dir / "phase345_stop_hit_saved_trades.csv",
        }

    def ingest_forensic_rows(self, rows: Sequence[Mapping[str, Any]]) -> None:
        self.note_memory()
        for row in rows:
            self.forensic_rows.append(dict(row))

    def _write_csv(self, path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        fields = sorted({k for r in rows for k in r})
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow(row)

    def build_summary(self) -> dict[str, Any]:
        self.note_memory()
        rows = self.forensic_rows
        triggered = [r for r in rows if r.get("shadow_triggered")]
        profit_miss = [r for r in rows if is_profit_take_miss(r)]
        profit_miss_sorted = sorted(
            profit_miss,
            key=lambda r: float(_float(r.get("pnl_difference_yen_100")) or 0.0),
        )
        worst_528 = [
            r
            for r in rows
            if str(r.get("day_key") or "") == FOCUS_DAY
            and float(_float(r.get("pnl_difference_yen_100")) or 0.0) < -NOISE_DELTA_YEN
        ]
        worst_528_sorted = sorted(
            worst_528,
            key=lambda r: float(_float(r.get("pnl_difference_yen_100")) or 0.0),
        )
        stop_saved = [r for r in rows if is_stop_hit_saved(r)]
        stop_saved_sorted = sorted(
            stop_saved,
            key=lambda r: float(_float(r.get("pnl_difference_yen_100")) or 0.0),
            reverse=True,
        )

        class_counts = Counter(str(r.get("forensic_class") or "") for r in triggered)
        n_trig = len(triggered) or 1

        mfe_vals = [float(_float(r.get("peak_mfe_pct")) or 0) for r in triggered]
        shadow_deltas = [
            float(_float(r.get("shadow_board_imbalance_delta")) or 0) for r in triggered
        ]

        focus_rows = [r for r in rows if str(r.get("day_key") or "") == FOCUS_DAY]
        focus_delta = round(
            sum(float(_float(r.get("pnl_difference_yen_100")) or 0) for r in focus_rows),
            2,
        )

        conclusions = _build_conclusions(
            all_rows=rows,
            triggered=triggered,
            profit_miss=profit_miss,
            class_counts=class_counts,
        )

        return {
            "phase": 345,
            "title": "board_failure_exit_forensic_review",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "variant_id": VARIANT_ID,
            "source_phase": 344,
            "focus_day_key": FOCUS_DAY,
            "positions_analyzed": len(rows),
            "shadow_triggered_count": len(triggered),
            "profit_take_miss_top_symbols": _symbol_contribution(
                profit_miss,
                key_fn=lambda r: float(_float(r.get("pnl_difference_yen_100")) or 0.0),
            )[:10],
            "day_20260528_worst_contributors": _symbol_contribution(
                worst_528,
                key_fn=lambda r: float(_float(r.get("pnl_difference_yen_100")) or 0.0),
            )[:10],
            "day_20260528_total_delta_yen": focus_delta,
            "false_positive_rate": round(class_counts.get("B_false_positive", 0) / n_trig, 4),
            "correct_cut_rate": round(class_counts.get("A_correct_cut", 0) / n_trig, 4),
            "noise_rate": round(class_counts.get("C_noise", 0) / n_trig, 4),
            "forensic_class_counts": dict(class_counts),
            "profit_take_miss_count": len(profit_miss),
            "profit_take_miss_total_yen": round(
                sum(float(_float(r.get("pnl_difference_yen_100")) or 0) for r in profit_miss),
                2,
            ),
            "stop_hit_saved_count": len(stop_saved),
            "mfe_distribution": _dist(mfe_vals),
            "shadow_board_delta_distribution": _dist(shadow_deltas),
            "conclusions": conclusions,
            "parallel_enabled": self.parallel_enabled,
            "parallel_max_workers": self.parallel_max_workers,
            "wall_runtime_sec": round(self.wall_runtime_sec, 2),
            "peak_memory_mb": self.peak_memory_mb,
            "note": "Forensic only — no EXIT rule changes",
        }

    def finalize_outputs(self) -> dict[str, str]:
        paths = self.paths()
        rows = self.forensic_rows
        profit_miss = sorted(
            [r for r in rows if is_profit_take_miss(r)],
            key=lambda r: float(_float(r.get("pnl_difference_yen_100")) or 0.0),
        )[:20]
        worst_528 = sorted(
            [
                r
                for r in rows
                if str(r.get("day_key") or "") == FOCUS_DAY
                and float(_float(r.get("pnl_difference_yen_100")) or 0.0) < -NOISE_DELTA_YEN
            ],
            key=lambda r: float(_float(r.get("pnl_difference_yen_100")) or 0.0),
        )
        stop_saved = sorted(
            [r for r in rows if is_stop_hit_saved(r)],
            key=lambda r: float(_float(r.get("pnl_difference_yen_100")) or 0.0),
            reverse=True,
        )

        self._write_csv(paths["profit_take_miss_top20"], profit_miss)
        self._write_csv(paths["worst_delta"], worst_528)
        self._write_csv(paths["stop_hit_saved"], stop_saved)

        summary = self.build_summary()
        summary["output_file_sizes_bytes"] = {
            k: paths[k].stat().st_size if paths[k].is_file() else 0 for k in paths
        }
        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {k: str(v) for k, v in paths.items()}
