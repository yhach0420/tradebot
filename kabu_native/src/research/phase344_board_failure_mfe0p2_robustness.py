"""
Phase344: mfe_lt_0p2_confirm5 robustness validation on additional sessions.
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
from research.phase340_vwap_dev_finetune_evaluation import (
    PROFIT_MISS_SMALL_THRESHOLD,
    _VariantAccum,
    tradeoff_score,
)
from research.phase343_board_failure_mfe_tuning import (
    MFE_COHORTS,
    trade_in_mfe_cohort,
)
from small_paper.board_failure_exit_tuning import (
    BOARD_FAILURE_EXIT_ID,
    BoardFailureTuningVariant,
    VARIANT_MFE_LT_0P2_CONFIRM5,
    phase344_mfe_lt_0p2_confirm5_variant,
)

JST = ZoneInfo("Asia/Tokyo")
VARIANT_ID = VARIANT_MFE_LT_0P2_CONFIRM5

PHASE343_VARIANT_BASELINE: dict[str, Any] = {
    "label": "phase343_mfe_lt_0p2_confirm5",
    "sessions": 3,
    "delta_yen": 24930.0,
    "profit_take_miss_yen_100": -120.0,
    "profit_factor": 0.5465,
    "stop_hit_reduction_count": 3,
    "trigger_count": 8,
}

TRADE_FIELDS = [
    "session_id",
    "day_key",
    "symbol",
    "position_id",
    "variant_id",
    "max_mfe_pct",
    "confirm_ticks",
    "peak_mfe_pct",
    "mfe_bucket",
    "shadow_pnl_yen_100",
    "actual_exit_reason",
    "actual_pnl_yen_100",
    "candidate_vs_actual_delta_yen",
    "no_candidate_trigger",
]


def _load_phase343_variant_baseline(reports_dir: Path) -> dict[str, Any]:
    path = reports_dir / "phase343_board_failure_mfe_tuning_summary.json"
    if not path.is_file():
        return dict(PHASE343_VARIANT_BASELINE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        met = (data.get("variants") or {}).get(VARIANT_ID) or {}
        return {
            "label": "phase343_mfe_lt_0p2_confirm5",
            "sessions": int(data.get("sessions_evaluated") or PHASE343_VARIANT_BASELINE["sessions"]),
            "delta_yen": float(met.get("delta_yen") or PHASE343_VARIANT_BASELINE["delta_yen"]),
            "profit_take_miss_yen_100": float(
                met.get("profit_take_miss_yen_100")
                or PHASE343_VARIANT_BASELINE["profit_take_miss_yen_100"]
            ),
            "profit_factor": met.get("profit_factor") or PHASE343_VARIANT_BASELINE["profit_factor"],
            "stop_hit_reduction_count": int(
                met.get("stop_hit_reduction_count")
                or PHASE343_VARIANT_BASELINE["stop_hit_reduction_count"]
            ),
            "trigger_count": int(met.get("trigger_count") or PHASE343_VARIANT_BASELINE["trigger_count"]),
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return dict(PHASE343_VARIANT_BASELINE)


@dataclass
class _SessionSnapshot:
    session_id: str
    day_key: str
    positions: int
    actual_total_pnl_yen_100: float
    shadow_total_pnl_yen_100: float
    session_delta_yen: float
    cumulative_shadow_pf: Optional[float]
    cumulative_actual_pf: Optional[float]


@dataclass
class Phase344BoardFailureMfe0p2RobustnessAggregator:
    reports_dir: Path
    variant: BoardFailureTuningVariant = field(default_factory=phase344_mfe_lt_0p2_confirm5_variant)
    phase343_baseline: dict[str, Any] = field(default_factory=dict)
    sessions_evaluated: int = 0
    sessions_failed: int = 0
    positions_evaluated: int = 0
    actual_total_pnl_yen_100: float = 0.0
    actual_gross_profit_yen_100: float = 0.0
    actual_gross_loss_yen_100: float = 0.0
    actual_stop_hit_count: int = 0
    accum: _VariantAccum = field(default_factory=_VariantAccum)
    cohort_acc: dict[str, _VariantAccum] = field(default_factory=dict)
    session_snapshots: list[_SessionSnapshot] = field(default_factory=list)
    symbol_session_delta: dict[str, dict[str, float]] = field(default_factory=dict)
    failed_sessions: list[dict[str, Any]] = field(default_factory=list)
    peak_memory_mb: float = 0.0
    wall_runtime_sec: float = 0.0
    parallel_max_workers: int = 1
    parallel_enabled: bool = False

    def __post_init__(self) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        if not self.phase343_baseline:
            self.phase343_baseline = _load_phase343_variant_baseline(self.reports_dir)
        for cohort in MFE_COHORTS:
            self.cohort_acc.setdefault(cohort, _VariantAccum())

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir
            / "phase344_board_failure_mfe0p2_confirm5_robustness_summary.json",
            "sessions": self.reports_dir
            / "phase344_board_failure_mfe0p2_confirm5_robustness_sessions.csv",
            "trades": self.reports_dir
            / "phase344_board_failure_mfe0p2_confirm5_robustness_trades.csv",
            "by_symbol": self.reports_dir
            / "phase344_board_failure_mfe0p2_confirm5_robustness_by_symbol.csv",
            "by_mfe_cohort": self.reports_dir
            / "phase344_board_failure_mfe0p2_confirm5_robustness_by_mfe_cohort.csv",
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

    def actual_pf(self) -> Optional[float]:
        if self.actual_gross_loss_yen_100 <= 0:
            return None if self.actual_gross_profit_yen_100 <= 0 else float("inf")
        return round(self.actual_gross_profit_yen_100 / self.actual_gross_loss_yen_100, 4)

    def ingest_session(
        self,
        *,
        session_meta: Mapping[str, Any],
        trade_rows: Sequence[Mapping[str, Any]],
        push_rows: int,
        runtime_sec: float,
        error: str = "",
        vwap_coverage_pct: Optional[float] = None,
    ) -> None:
        del vwap_coverage_pct
        self.note_memory()
        if error:
            self.sessions_failed += 1
            self.failed_sessions.append({**dict(session_meta), "error": error})
            return

        sid = str(session_meta.get("session_id") or "")
        day_key = str(session_meta.get("day_key") or "")
        seen_positions: dict[str, float] = {}
        session_shadow = 0.0
        enriched: list[dict[str, Any]] = []

        for row in trade_rows:
            if str(row.get("variant_id") or "") != VARIANT_ID:
                continue
            pid = str(row.get("position_id") or "")
            if pid and pid not in seen_positions:
                actual = float(_float(row.get("actual_pnl_yen_100")) or 0.0)
                seen_positions[pid] = actual
                self.positions_evaluated += 1
                self.actual_total_pnl_yen_100 += actual
                if actual > 0:
                    self.actual_gross_profit_yen_100 += actual
                elif actual < 0:
                    self.actual_gross_loss_yen_100 += abs(actual)
                if str(row.get("actual_exit_reason") or "") == "stop_hit":
                    self.actual_stop_hit_count += 1
            enriched.append({**dict(row), "session_id": sid, "day_key": day_key})
            self.accum.ingest_trade(row)
            for cohort in MFE_COHORTS:
                if trade_in_mfe_cohort(row, cohort):
                    self.cohort_acc[cohort].ingest_trade(row)
            session_shadow += float(_float(row.get("shadow_pnl_yen_100")) or 0.0)
            sym = str(row.get("symbol") or "")
            delta = float(_float(row.get("candidate_vs_actual_delta_yen")) or 0.0)
            if sym:
                bucket = self.symbol_session_delta.setdefault(sym, {})
                bucket[sid] = round(bucket.get(sid, 0.0) + delta, 2)

        session_actual = round(sum(seen_positions.values()), 2)
        session_delta = round(session_shadow - session_actual, 2)
        self.accum.ingest_session_delta(session_delta)

        self.session_snapshots.append(
            _SessionSnapshot(
                session_id=sid,
                day_key=day_key,
                positions=len(seen_positions),
                actual_total_pnl_yen_100=session_actual,
                shadow_total_pnl_yen_100=round(session_shadow, 2),
                session_delta_yen=session_delta,
                cumulative_shadow_pf=self.accum.pf(),
                cumulative_actual_pf=self.actual_pf(),
            )
        )

        session_row = {
            **dict(session_meta),
            "positions": len(seen_positions),
            "push_rows": push_rows,
            "runtime_sec": round(runtime_sec, 1),
            "actual_total_pnl_yen_100": session_actual,
            "shadow_total_pnl_yen_100": round(session_shadow, 2),
            "session_delta_yen": session_delta,
            "cumulative_delta_yen": round(
                self.accum.shadow_total_pnl_yen_100 - self.actual_total_pnl_yen_100,
                2,
            ),
        }
        self._append_csv(self.paths()["sessions"], sorted(session_row.keys()), [session_row])
        self._append_csv(self.paths()["trades"], TRADE_FIELDS, enriched)
        self.sessions_evaluated += 1

    def _metrics(self) -> dict[str, Any]:
        acc = self.accum
        actual_total = round(self.actual_total_pnl_yen_100, 2)
        delta = round(acc.shadow_total_pnl_yen_100 - actual_total, 2)
        profit_miss = round(acc.profit_take_miss_yen_100, 2)
        sym_abs = {s: abs(sum(v.values())) for s, v in self.symbol_session_delta.items() if s}
        top_share = _top_share(sym_abs, delta)
        concentrated = top_share is not None and top_share >= CONCENTRATION_THRESHOLD
        cohort_metrics: dict[str, Any] = {}
        for cohort in MFE_COHORTS:
            cacc = self.cohort_acc[cohort]
            pos_n = cacc.trigger_count + cacc.no_trigger_count
            if pos_n <= 0:
                continue
            cohort_metrics[cohort] = {
                "positions": pos_n,
                "shadow_total_pnl_yen_100": round(cacc.shadow_total_pnl_yen_100, 2),
                "shadow_pf": cacc.pf(),
                "trigger_count": cacc.trigger_count,
                "stop_hit_reduction_count": cacc.stop_hit_reduction_count,
                "profit_take_miss_yen_100": round(cacc.profit_take_miss_yen_100, 2),
            }
        return {
            **self.variant.to_dict(),
            "candidate_id": BOARD_FAILURE_EXIT_ID,
            "shadow_total_pnl_yen_100": round(acc.shadow_total_pnl_yen_100, 2),
            "delta_yen": delta,
            "profit_factor": acc.pf(),
            "trigger_count": acc.trigger_count,
            "no_trigger_count": acc.no_trigger_count,
            "improved_trade_count": acc.improved_trade_count,
            "worsened_trade_count": acc.worsened_trade_count,
            "stop_hit_reduction_count": acc.stop_hit_reduction_count,
            "profit_take_miss_yen_100": profit_miss,
            "stop_hit_avoidance_rate": acc.stop_hit_avoidance_rate(),
            "tradeoff_score": tradeoff_score(profit_miss, acc.stop_hit_reduction_count),
            "improved_session_count": acc.improved_session_count,
            "worsened_session_count": acc.worsened_session_count,
            "unchanged_session_count": acc.unchanged_session_count,
            "top_symbol_delta_share": top_share,
            "symbol_concentration": concentrated,
            "mfe_cohort_metrics": cohort_metrics,
        }

    def _pf_trend_fail(self) -> bool:
        snaps = self.session_snapshots
        if len(snaps) < 4:
            return False
        mid = len(snaps) // 2
        first_pf = [s.cumulative_shadow_pf for s in snaps[:mid] if s.cumulative_shadow_pf is not None]
        second_pf = [s.cumulative_shadow_pf for s in snaps[mid:] if s.cumulative_shadow_pf is not None]
        if not first_pf or not second_pf:
            return False
        return float(second_pf[-1]) < float(first_pf[-1]) * 0.9

    def robustness_verdict(self, metrics: Mapping[str, Any]) -> dict[str, Any]:
        actual_pf = self.actual_pf()
        actual_total = self.actual_total_pnl_yen_100
        pf = metrics.get("profit_factor")
        profit_miss = float(metrics.get("profit_take_miss_yen_100") or 0)
        phase343_miss = float(self.phase343_baseline.get("profit_take_miss_yen_100") or 0)
        top_share = metrics.get("top_symbol_delta_share")
        concentrated = bool(metrics.get("symbol_concentration"))

        checks = {
            "total_pnl_improved": float(metrics.get("delta_yen") or 0) > 0,
            "pf_not_worse_than_actual": (
                pf is not None
                and actual_pf is not None
                and pf != float("inf")
                and actual_pf != float("inf")
                and float(pf) >= float(actual_pf)
            ),
            "stop_hit_reduction": int(metrics.get("stop_hit_reduction_count") or 0) > 0,
            "low_profit_take_miss": profit_miss >= PROFIT_MISS_SMALL_THRESHOLD,
            "profit_take_miss_vs_phase343": profit_miss >= phase343_miss,
            "session_stability": int(metrics.get("improved_session_count") or 0)
            >= int(metrics.get("worsened_session_count") or 0),
            "not_symbol_concentrated": not concentrated,
            "top_symbol_share_below_60pct": (
                top_share is not None and float(top_share) < CONCENTRATION_THRESHOLD
            ),
            "pf_stable_over_sessions": not self._pf_trend_fail(),
        }
        fail_reasons: list[str] = []
        if not checks["total_pnl_improved"]:
            fail_reasons.append("total_pnl_not_improved")
        if not checks["pf_not_worse_than_actual"]:
            fail_reasons.append("pf_worse_than_actual")
        if not checks["stop_hit_reduction"]:
            fail_reasons.append("stop_hit_reduction_lost")
        if not checks["low_profit_take_miss"]:
            fail_reasons.append("profit_take_miss_increased")
        if not checks["profit_take_miss_vs_phase343"]:
            fail_reasons.append("profit_take_miss_worse_than_phase343")
        if concentrated or not checks["top_symbol_share_below_60pct"]:
            fail_reasons.append("single_symbol_dependency")
        if not checks["pf_stable_over_sessions"]:
            fail_reasons.append("pf_degrades_over_sessions")
        if not checks["session_stability"]:
            fail_reasons.append("session_instability")

        pass_core = all(checks.values())
        return {
            "robustness_pass": pass_core and not fail_reasons,
            "adopt_ready": pass_core and not fail_reasons,
            "checks": checks,
            "fail_reasons": fail_reasons,
            "actual_total_pnl_yen_100": round(actual_total, 2),
            "actual_pf": actual_pf,
        }

    def by_symbol_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for sym, per_session in sorted(
            self.symbol_session_delta.items(),
            key=lambda x: abs(sum(x[1].values())),
            reverse=True,
        ):
            rows.append(
                {
                    "symbol": sym,
                    "total_delta_yen": round(sum(per_session.values()), 2),
                    "sessions_with_trades": len(per_session),
                    "per_session_delta_json": json.dumps(per_session, ensure_ascii=False),
                }
            )
        return rows

    def build_summary(self) -> dict[str, Any]:
        self.note_memory()
        metrics = self._metrics()
        verdict = self.robustness_verdict(metrics)
        return {
            "phase": 344,
            "title": "board_failure_mfe_lt_0p2_confirm5_robustness",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "target_candidate": BOARD_FAILURE_EXIT_ID,
            "variant_id": VARIANT_ID,
            "phase343_baseline": self.phase343_baseline,
            "sessions_evaluated": self.sessions_evaluated,
            "sessions_failed": self.sessions_failed,
            "positions_evaluated": self.positions_evaluated,
            "actual_total_pnl_yen_100": round(self.actual_total_pnl_yen_100, 2),
            "actual_pf": self.actual_pf(),
            "actual_stop_hit_count": self.actual_stop_hit_count,
            "variant_metrics": metrics,
            "robustness_verdict": verdict,
            "session_snapshots": [
                {
                    "session_id": s.session_id,
                    "day_key": s.day_key,
                    "positions": s.positions,
                    "session_delta_yen": s.session_delta_yen,
                    "cumulative_shadow_pf": s.cumulative_shadow_pf,
                    "cumulative_actual_pf": s.cumulative_actual_pf,
                }
                for s in self.session_snapshots
            ],
            "parallel_enabled": self.parallel_enabled,
            "parallel_max_workers": self.parallel_max_workers,
            "wall_runtime_sec": round(self.wall_runtime_sec, 2),
            "peak_memory_mb": self.peak_memory_mb,
            "failed_sessions": self.failed_sessions,
            "note": "Robustness on sessions beyond Phase343 — shadow only",
        }

    def finalize_outputs(self) -> dict[str, str]:
        paths = self.paths()
        summary = self.build_summary()
        by_symbol = self.by_symbol_rows()
        if by_symbol:
            with paths["by_symbol"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(by_symbol[0].keys()), extrasaction="ignore")
                w.writeheader()
                for row in by_symbol:
                    w.writerow(row)
        cohort_rows: list[dict[str, Any]] = []
        for cohort, cm in (summary.get("variant_metrics") or {}).get("mfe_cohort_metrics", {}).items():
            cohort_rows.append({"cohort": cohort, **cm})
        if cohort_rows:
            with paths["by_mfe_cohort"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(cohort_rows[0].keys()), extrasaction="ignore")
                w.writeheader()
                for row in cohort_rows:
                    w.writerow(row)
        sizes = {k: paths[k].stat().st_size if paths[k].is_file() else 0 for k in paths}
        summary["output_file_sizes_bytes"] = sizes
        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {k: str(v) for k, v in paths.items()}
