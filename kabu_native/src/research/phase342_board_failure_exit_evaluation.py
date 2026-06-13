"""
Phase342: Board failure EXIT shadow evaluation with MFE stratification.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase337_exit_candidate_evaluation import (
    CONCENTRATION_THRESHOLD,
    _float,
    _memory_mb,
    _top_share,
)
from small_paper.board_failure_exit_shadow import (
    BOARD_FAILURE_EXIT_ID,
    MFE_BUCKET_THRESHOLDS,
    trade_in_mfe_cohort,
)

JST = ZoneInfo("Asia/Tokyo")

MFE_COHORTS: tuple[str, ...] = ("all", "mfe_lt_0p3", "mfe_lt_0p5", "mfe_lt_1p0")

TRADE_FIELDS = [
    "session_id",
    "day_key",
    "symbol",
    "position_id",
    "candidate_id",
    "peak_mfe_pct",
    "mfe_bucket",
    "shadow_pnl_yen_100",
    "actual_exit_reason",
    "actual_pnl_yen_100",
    "candidate_vs_actual_delta_yen",
    "no_candidate_trigger",
]


@dataclass
class _CohortAccum:
    positions: int = 0
    actual_total_pnl_yen_100: float = 0.0
    shadow_total_pnl_yen_100: float = 0.0
    actual_gross_profit_yen_100: float = 0.0
    actual_gross_loss_yen_100: float = 0.0
    shadow_gross_profit_yen_100: float = 0.0
    shadow_gross_loss_yen_100: float = 0.0
    trigger_count: int = 0
    stop_hit_reduction_count: int = 0
    profit_take_miss_yen_100: float = 0.0
    improved_trade_count: int = 0
    worsened_trade_count: int = 0
    symbol_delta_yen: dict[str, float] = field(default_factory=dict)

    def actual_pf(self) -> Optional[float]:
        if self.actual_gross_loss_yen_100 <= 0:
            return None if self.actual_gross_profit_yen_100 <= 0 else float("inf")
        return round(self.actual_gross_profit_yen_100 / self.actual_gross_loss_yen_100, 4)

    def shadow_pf(self) -> Optional[float]:
        if self.shadow_gross_loss_yen_100 <= 0:
            return None if self.shadow_gross_profit_yen_100 <= 0 else float("inf")
        return round(self.shadow_gross_profit_yen_100 / self.shadow_gross_loss_yen_100, 4)

    def ingest_trade(self, row: Mapping[str, Any]) -> None:
        shadow = float(_float(row.get("shadow_pnl_yen_100")) or 0.0)
        actual = float(_float(row.get("actual_pnl_yen_100")) or 0.0)
        delta = float(_float(row.get("candidate_vs_actual_delta_yen")) or 0.0)
        sym = str(row.get("symbol") or "")
        self.positions += 1
        self.actual_total_pnl_yen_100 += actual
        self.shadow_total_pnl_yen_100 += shadow
        if actual > 0:
            self.actual_gross_profit_yen_100 += actual
        elif actual < 0:
            self.actual_gross_loss_yen_100 += abs(actual)
        if shadow > 0:
            self.shadow_gross_profit_yen_100 += shadow
        elif shadow < 0:
            self.shadow_gross_loss_yen_100 += abs(shadow)
        if not row.get("no_candidate_trigger"):
            self.trigger_count += 1
        if delta > 0:
            self.improved_trade_count += 1
        elif delta < 0:
            self.worsened_trade_count += 1
        if (
            row.get("actual_exit_reason") == "stop_hit"
            and not row.get("no_candidate_trigger")
            and delta > 0
        ):
            self.stop_hit_reduction_count += 1
        if actual > 0 and delta < 0:
            self.profit_take_miss_yen_100 += delta
        if sym:
            self.symbol_delta_yen[sym] = round(self.symbol_delta_yen.get(sym, 0.0) + delta, 2)

    def metrics(self, cohort: str) -> dict[str, Any]:
        delta = round(self.shadow_total_pnl_yen_100 - self.actual_total_pnl_yen_100, 2)
        sym_abs = {s: abs(v) for s, v in self.symbol_delta_yen.items() if s}
        top_share = _top_share(sym_abs, delta)
        return {
            "cohort": cohort,
            "positions": self.positions,
            "actual_total_pnl_yen_100": round(self.actual_total_pnl_yen_100, 2),
            "shadow_total_pnl_yen_100": round(self.shadow_total_pnl_yen_100, 2),
            "delta_yen": delta,
            "actual_pf": self.actual_pf(),
            "shadow_pf": self.shadow_pf(),
            "trigger_count": self.trigger_count,
            "stop_hit_reduction_count": self.stop_hit_reduction_count,
            "profit_take_miss_yen_100": round(self.profit_take_miss_yen_100, 2),
            "improved_trade_count": self.improved_trade_count,
            "worsened_trade_count": self.worsened_trade_count,
            "top_symbol_delta_share": top_share,
            "symbol_concentration": (
                top_share is not None and top_share >= CONCENTRATION_THRESHOLD
            ),
        }


@dataclass
class Phase342BoardFailureAggregator:
    reports_dir: Path
    sessions_evaluated: int = 0
    sessions_failed: int = 0
    positions_evaluated: int = 0
    cohort_acc: dict[str, _CohortAccum] = field(default_factory=dict)
    bucket_counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    failed_sessions: list[dict[str, Any]] = field(default_factory=list)
    peak_memory_mb: float = 0.0

    def __post_init__(self) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        for cohort in MFE_COHORTS:
            self.cohort_acc.setdefault(cohort, _CohortAccum())

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase342_board_failure_exit_summary.json",
            "sessions": self.reports_dir / "phase342_board_failure_exit_sessions.csv",
            "trades": self.reports_dir / "phase342_board_failure_exit_trades.csv",
            "by_mfe_cohort": self.reports_dir / "phase342_board_failure_exit_by_mfe_cohort.csv",
            "by_mfe_bucket": self.reports_dir / "phase342_board_failure_exit_by_mfe_bucket.csv",
        }

    def note_memory(self) -> None:
        self.peak_memory_mb = max(self.peak_memory_mb, _memory_mb())

    def _append_csv(
        self,
        path: Path,
        fieldnames: Sequence[str],
        rows: Sequence[Mapping[str, Any]],
    ) -> None:
        if not rows:
            return
        write_header = not path.is_file()
        with path.open("a", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(fieldnames), extrasaction="ignore")
            if write_header:
                w.writeheader()
            for row in rows:
                w.writerow(row)

    def ingest_session(
        self,
        *,
        session_meta: Mapping[str, Any],
        trade_rows: Sequence[Mapping[str, Any]],
        push_rows: int,
        runtime_sec: float,
        error: str = "",
    ) -> None:
        self.note_memory()
        if error:
            self.sessions_failed += 1
            self.failed_sessions.append({**dict(session_meta), "error": error})
            return

        sid = str(session_meta.get("session_id") or "")
        day_key = str(session_meta.get("day_key") or "")
        seen_positions: set[str] = set()
        session_actual = 0.0
        session_shadow = 0.0
        enriched: list[dict[str, Any]] = []

        for row in trade_rows:
            pid = str(row.get("position_id") or "")
            if pid and pid not in seen_positions:
                seen_positions.add(pid)
                self.positions_evaluated += 1
                actual = float(_float(row.get("actual_pnl_yen_100")) or 0.0)
                session_actual += actual
            shadow = float(_float(row.get("shadow_pnl_yen_100")) or 0.0)
            session_shadow += shadow
            bucket = str(row.get("mfe_bucket") or "")
            if bucket:
                self.bucket_counts[bucket] += 1
            for cohort in MFE_COHORTS:
                if trade_in_mfe_cohort(row, cohort):
                    self.cohort_acc[cohort].ingest_trade(row)
            enriched.append({**dict(row), "session_id": sid, "day_key": day_key})

        session_row = {
            **dict(session_meta),
            "positions": len(seen_positions),
            "push_rows": push_rows,
            "runtime_sec": round(runtime_sec, 1),
            "actual_total_pnl_yen_100": round(session_actual, 2),
            "shadow_total_pnl_yen_100": round(session_shadow, 2),
            "session_delta_yen": round(session_shadow - session_actual, 2),
            "trigger_count": sum(
                1 for r in trade_rows if not r.get("no_candidate_trigger")
            ),
        }
        self._append_csv(self.paths()["sessions"], sorted(session_row.keys()), [session_row])
        self._append_csv(self.paths()["trades"], TRADE_FIELDS, enriched)
        self.sessions_evaluated += 1

    def _small_mfe_effective(self, metrics_by_cohort: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
        """True when improvement concentrates in low-MFE names."""
        all_m = metrics_by_cohort.get("all") or {}
        lt03 = metrics_by_cohort.get("mfe_lt_0p3") or {}
        lt05 = metrics_by_cohort.get("mfe_lt_0p5") or {}
        return {
            "delta_positive_in_mfe_lt_0p3": float(lt03.get("delta_yen") or 0) > 0,
            "delta_positive_in_mfe_lt_0p5": float(lt05.get("delta_yen") or 0) > 0,
            "stop_reduction_in_mfe_lt_0p3": int(lt03.get("stop_hit_reduction_count") or 0) > 0,
            "profit_miss_low_in_mfe_lt_0p3": float(lt03.get("profit_take_miss_yen_100") or 0) >= -500.0,
            "all_cohort_delta_yen": all_m.get("delta_yen"),
            "mfe_lt_0p3_delta_yen": lt03.get("delta_yen"),
            "mfe_lt_0p5_delta_yen": lt05.get("delta_yen"),
            "mfe_lt_1p0_delta_yen": (metrics_by_cohort.get("mfe_lt_1p0") or {}).get("delta_yen"),
        }

    def build_summary(self) -> dict[str, Any]:
        self.note_memory()
        metrics_by_cohort = {c: self.cohort_acc[c].metrics(c) for c in MFE_COHORTS}
        all_m = metrics_by_cohort["all"]
        small_mfe = self._small_mfe_effective(metrics_by_cohort)
        adopt_hints = {
            "total_pnl_improved": float(all_m.get("delta_yen") or 0) > 0,
            "shadow_pf_better": (
                all_m.get("shadow_pf") is not None
                and all_m.get("actual_pf") is not None
                and float(all_m["shadow_pf"]) > float(all_m["actual_pf"])
            ),
            "stop_hit_reduction": int(all_m.get("stop_hit_reduction_count") or 0) > 0,
            "low_profit_take_miss": float(all_m.get("profit_take_miss_yen_100") or 0) >= -500.0,
            "small_mfe_hypothesis_supported": (
                small_mfe["delta_positive_in_mfe_lt_0p3"]
                and small_mfe["stop_reduction_in_mfe_lt_0p3"]
                and small_mfe["profit_miss_low_in_mfe_lt_0p3"]
            ),
        }
        return {
            "phase": 342,
            "title": "board_failure_exit_research",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "candidate_id": BOARD_FAILURE_EXIT_ID,
            "conditions": {
                "current_pnl_pct_lt": 0,
                "board_imbalance_delta_lte": -0.08,
                "recent_low_update_arm": True,
                "confirm_ticks": 3,
                "uses_vwap": False,
            },
            "sessions_evaluated": self.sessions_evaluated,
            "sessions_failed": self.sessions_failed,
            "positions_evaluated": self.positions_evaluated,
            "mfe_cohort_metrics": metrics_by_cohort,
            "mfe_bucket_counts": dict(self.bucket_counts),
            "mfe_bucket_thresholds_pct": [t for _, t in MFE_BUCKET_THRESHOLDS],
            "small_mfe_effectiveness": small_mfe,
            "adopt_hints": adopt_hints,
            "peak_memory_mb": self.peak_memory_mb,
            "failed_sessions": self.failed_sessions,
            "note": "Shadow only — production EXIT unchanged; no VWAP",
        }

    def finalize_outputs(self) -> dict[str, str]:
        paths = self.paths()
        summary = self.build_summary()
        cohort_rows = list(summary["mfe_cohort_metrics"].values())
        if cohort_rows:
            with paths["by_mfe_cohort"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(cohort_rows[0].keys()), extrasaction="ignore")
                w.writeheader()
                for row in cohort_rows:
                    w.writerow(row)
        bucket_rows = [
            {"mfe_bucket": k, "trade_count": v}
            for k, v in sorted(self.bucket_counts.items())
        ]
        if bucket_rows:
            with paths["by_mfe_bucket"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["mfe_bucket", "trade_count"])
                w.writeheader()
                for row in bucket_rows:
                    w.writerow(row)
        sizes = {k: paths[k].stat().st_size if paths[k].is_file() else 0 for k in paths}
        summary["output_file_sizes_bytes"] = sizes
        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {k: str(v) for k, v in paths.items()}
