"""
Phase346: False-positive guard evaluation for board_failure_exit.
"""

from __future__ import annotations

import csv
import json
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
from small_paper.board_failure_exit_shadow import BOARD_FAILURE_EXIT_ID
from small_paper.board_failure_false_positive_guard import (
    BASE_VARIANT_ID,
    BoardFailureGuardVariant,
    default_phase346_variants,
)

JST = ZoneInfo("Asia/Tokyo")
COHORT_ROBUSTNESS = "phase344_robustness"
COHORT_DEVELOPMENT = "phase343_development"
FOCUS_DAY_KEY = "20260528"

PHASE344_BASELINE: dict[str, Any] = {
    "label": "phase344_mfe_lt_0p2_confirm5",
    "sessions": 5,
    "delta_yen": -13500.0,
    "profit_take_miss_yen_100": -50600.0,
    "profit_factor": 0.56,
    "stop_hit_reduction_count": 7,
    "trigger_count": 16,
    "false_positive_rate": 0.5,
}

TRADE_FIELDS = [
    "session_id",
    "day_key",
    "session_cohort",
    "symbol",
    "position_id",
    "variant_id",
    "entry_cooldown_sec",
    "reclaim_ticks",
    "max_mfe_pct",
    "confirm_ticks",
    "peak_mfe_pct",
    "mfe_bucket",
    "shadow_pnl_yen_100",
    "actual_exit_reason",
    "actual_pnl_yen_100",
    "candidate_vs_actual_delta_yen",
    "no_candidate_trigger",
    "post_shadow_max_up_pct",
    "post_shadow_max_down_pct",
    "forensic_class",
]


def _load_phase344_baseline(reports_dir: Path) -> dict[str, Any]:
    path = reports_dir / "phase344_board_failure_mfe0p2_confirm5_robustness_summary.json"
    if not path.is_file():
        return dict(PHASE344_BASELINE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        met = data.get("variant_metrics") or {}
        return {
            "label": "phase344_mfe_lt_0p2_confirm5",
            "sessions": int(data.get("sessions_evaluated") or PHASE344_BASELINE["sessions"]),
            "delta_yen": float(met.get("delta_yen") or PHASE344_BASELINE["delta_yen"]),
            "profit_take_miss_yen_100": float(
                met.get("profit_take_miss_yen_100")
                or PHASE344_BASELINE["profit_take_miss_yen_100"]
            ),
            "profit_factor": met.get("profit_factor") or PHASE344_BASELINE["profit_factor"],
            "stop_hit_reduction_count": int(
                met.get("stop_hit_reduction_count")
                or PHASE344_BASELINE["stop_hit_reduction_count"]
            ),
            "trigger_count": int(met.get("trigger_count") or PHASE344_BASELINE["trigger_count"]),
            "false_positive_rate": PHASE344_BASELINE["false_positive_rate"],
        }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return dict(PHASE344_BASELINE)


@dataclass
class _ForensicCounters:
    false_positive_count: int = 0
    correct_cut_count: int = 0
    noise_count: int = 0

    def ingest(self, forensic_class: str) -> None:
        if forensic_class == "B_false_positive":
            self.false_positive_count += 1
        elif forensic_class == "A_correct_cut":
            self.correct_cut_count += 1
        elif forensic_class == "C_noise":
            self.noise_count += 1

    def false_positive_rate(self) -> Optional[float]:
        total = self.false_positive_count + self.correct_cut_count + self.noise_count
        if total <= 0:
            return None
        return round(self.false_positive_count / total, 4)


@dataclass
class _CohortActuals:
    actual_total_pnl_yen_100: float = 0.0
    actual_gross_profit_yen_100: float = 0.0
    actual_gross_loss_yen_100: float = 0.0
    actual_stop_hit_count: int = 0
    positions_evaluated: int = 0
    sessions_evaluated: int = 0

    def actual_pf(self) -> Optional[float]:
        if self.actual_gross_loss_yen_100 <= 0:
            return None if self.actual_gross_profit_yen_100 <= 0 else float("inf")
        return round(self.actual_gross_profit_yen_100 / self.actual_gross_loss_yen_100, 4)


@dataclass
class _CohortBundle:
    accum: _VariantAccum = field(default_factory=_VariantAccum)
    forensic: _ForensicCounters = field(default_factory=_ForensicCounters)
    day_delta_yen: dict[str, float] = field(default_factory=dict)


@dataclass
class Phase346BoardFailureFalsePositiveGuardAggregator:
    reports_dir: Path
    variants: tuple[BoardFailureGuardVariant, ...] = field(default_factory=default_phase346_variants)
    phase344_baseline: dict[str, Any] = field(default_factory=dict)
    sessions_evaluated: int = 0
    sessions_failed: int = 0
    positions_evaluated: int = 0
    actual_total_pnl_yen_100: float = 0.0
    actual_gross_profit_yen_100: float = 0.0
    actual_gross_loss_yen_100: float = 0.0
    actual_stop_hit_count: int = 0
    variant_meta: dict[str, BoardFailureGuardVariant] = field(default_factory=dict)
    cohort_variant: dict[str, dict[str, _CohortBundle]] = field(default_factory=dict)
    cohort_actuals: dict[str, _CohortActuals] = field(default_factory=dict)
    failed_sessions: list[dict[str, Any]] = field(default_factory=list)
    peak_memory_mb: float = 0.0
    wall_runtime_sec: float = 0.0
    parallel_max_workers: int = 1
    parallel_enabled: bool = False

    def __post_init__(self) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        if not self.phase344_baseline:
            self.phase344_baseline = _load_phase344_baseline(self.reports_dir)
        for v in self.variants:
            self.variant_meta[v.variant_id] = v
            for cohort in (COHORT_ROBUSTNESS, COHORT_DEVELOPMENT):
                self.cohort_variant.setdefault(cohort, {})
                self.cohort_variant[cohort].setdefault(v.variant_id, _CohortBundle())
                self.cohort_actuals.setdefault(cohort, _CohortActuals())

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase346_board_failure_false_positive_guard_summary.json",
            "sessions": self.reports_dir / "phase346_board_failure_false_positive_guard_sessions.csv",
            "trades": self.reports_dir / "phase346_board_failure_false_positive_guard_trades.csv",
            "by_variant": self.reports_dir / "phase346_board_failure_false_positive_guard_by_variant.csv",
            "focus_0528": self.reports_dir / "phase346_board_failure_false_positive_guard_0528.csv",
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
    ) -> None:
        self.note_memory()
        if error:
            self.sessions_failed += 1
            self.failed_sessions.append({**dict(session_meta), "error": error})
            return

        sid = str(session_meta.get("session_id") or "")
        day_key = str(session_meta.get("day_key") or "")
        cohort = str(session_meta.get("session_cohort") or COHORT_ROBUSTNESS)
        if cohort not in self.cohort_variant:
            cohort = COHORT_ROBUSTNESS

        seen_positions: dict[str, float] = {}
        enriched: list[dict[str, Any]] = []
        session_shadow_by_variant: dict[str, float] = {v.variant_id: 0.0 for v in self.variants}

        for row in trade_rows:
            vid = str(row.get("variant_id") or "")
            if vid not in self.variant_meta:
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
                cact = self.cohort_actuals.setdefault(cohort, _CohortActuals())
                cact.positions_evaluated += 1
                cact.actual_total_pnl_yen_100 += actual
                if actual > 0:
                    cact.actual_gross_profit_yen_100 += actual
                elif actual < 0:
                    cact.actual_gross_loss_yen_100 += abs(actual)
                if str(row.get("actual_exit_reason") or "") == "stop_hit":
                    cact.actual_stop_hit_count += 1

            bundle = self.cohort_variant[cohort][vid]
            bundle.accum.ingest_trade(row)
            bundle.forensic.ingest(str(row.get("forensic_class") or ""))
            delta = float(_float(row.get("candidate_vs_actual_delta_yen")) or 0.0)
            if day_key:
                bundle.day_delta_yen[day_key] = round(
                    bundle.day_delta_yen.get(day_key, 0.0) + delta,
                    2,
                )
            session_shadow_by_variant[vid] = round(
                session_shadow_by_variant.get(vid, 0.0)
                + float(_float(row.get("shadow_pnl_yen_100")) or 0.0),
                2,
            )
            enriched.append(
                {
                    **dict(row),
                    "session_id": sid,
                    "day_key": day_key,
                    "session_cohort": cohort,
                }
            )

        session_actual = round(sum(seen_positions.values()), 2)
        self.cohort_actuals.setdefault(cohort, _CohortActuals()).sessions_evaluated += 1
        for vid in self.variants:
            sh = session_shadow_by_variant.get(vid.variant_id, 0.0)
            self.cohort_variant[cohort][vid.variant_id].accum.ingest_session_delta(
                round(sh - session_actual, 2)
            )

        session_row: dict[str, Any] = {
            **dict(session_meta),
            "session_cohort": cohort,
            "positions": len(seen_positions),
            "push_rows": push_rows,
            "runtime_sec": round(runtime_sec, 1),
            "actual_total_pnl_yen_100": session_actual,
        }
        for vid in self.variants:
            sh = session_shadow_by_variant.get(vid.variant_id, 0.0)
            session_row[f"{vid.variant_id}_shadow_pnl_yen_100"] = sh
            session_row[f"{vid.variant_id}_session_delta_yen"] = round(sh - session_actual, 2)
        self._append_csv(self.paths()["sessions"], sorted(session_row.keys()), [session_row])
        self._append_csv(self.paths()["trades"], TRADE_FIELDS, enriched)
        self.sessions_evaluated += 1

    def _variant_metrics(
        self,
        vid: str,
        bundle: _CohortBundle,
        *,
        actual_total: float,
    ) -> dict[str, Any]:
        acc = bundle.accum
        meta = self.variant_meta[vid]
        delta = round(acc.shadow_total_pnl_yen_100 - actual_total, 2)
        profit_miss = round(acc.profit_take_miss_yen_100, 2)
        sym_abs = {s: abs(v) for s, v in acc.symbol_delta_yen.items() if s}
        top_share = _top_share(sym_abs, delta)
        concentrated = top_share is not None and top_share >= CONCENTRATION_THRESHOLD
        fpr = bundle.forensic.false_positive_rate()
        return {
            **meta.to_dict(),
            "candidate_id": BOARD_FAILURE_EXIT_ID,
            "actual_total_pnl_yen_100": round(actual_total, 2),
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
            "symbol_concentration": concentrated,
            "top_symbol_delta_share": top_share,
            "false_positive_count": bundle.forensic.false_positive_count,
            "correct_cut_count": bundle.forensic.correct_cut_count,
            "noise_count": bundle.forensic.noise_count,
            "false_positive_rate": fpr,
            "session_delta_yen_20260528": bundle.day_delta_yen.get(FOCUS_DAY_KEY),
        }

    def guard_pass_assessment(
        self,
        metrics: Mapping[str, Any],
        *,
        actual_pf: Optional[float],
        baseline: Mapping[str, Any],
    ) -> dict[str, Any]:
        pf = metrics.get("profit_factor")
        profit_miss = float(metrics.get("profit_take_miss_yen_100") or 0)
        baseline_miss = float(baseline.get("profit_take_miss_yen_100") or 0)
        baseline_stop = int(baseline.get("stop_hit_reduction_count") or 0)
        baseline_fpr = float(baseline.get("false_positive_rate") or 1.0)
        baseline_delta = float(baseline.get("delta_yen") or 0)
        day_528 = metrics.get("session_delta_yen_20260528")
        baseline_528 = -34500.0
        fpr = metrics.get("false_positive_rate")

        checks = {
            "profit_take_miss_reduced_vs_phase344": profit_miss > baseline_miss,
            "day_20260528_improved": day_528 is not None and float(day_528) > baseline_528,
            "stop_hit_reduction_preserved": int(metrics.get("stop_hit_reduction_count") or 0)
            >= max(1, baseline_stop - 2),
            "total_pnl_improved": float(metrics.get("delta_yen") or 0) > 0,
            "pf_not_worse_than_actual": (
                pf is not None
                and actual_pf is not None
                and pf != float("inf")
                and actual_pf != float("inf")
                and float(pf) >= float(actual_pf)
            ),
            "pf_improved_vs_phase344": (
                pf is not None
                and baseline.get("profit_factor") is not None
                and float(pf) >= float(baseline["profit_factor"])
            ),
            "false_positive_rate_reduced": (
                fpr is not None and float(fpr) < baseline_fpr
            ),
            "session_stability": int(metrics.get("improved_session_count") or 0)
            >= int(metrics.get("worsened_session_count") or 0),
            "not_symbol_concentrated": not bool(metrics.get("symbol_concentration")),
        }
        fail_reasons: list[str] = []
        if not checks["profit_take_miss_reduced_vs_phase344"]:
            fail_reasons.append("profit_take_miss_not_reduced")
        if not checks["day_20260528_improved"]:
            fail_reasons.append("day_20260528_not_improved")
        if not checks["stop_hit_reduction_preserved"]:
            fail_reasons.append("stop_hit_reduction_lost")
        if not checks["total_pnl_improved"]:
            fail_reasons.append("total_pnl_not_improved")
        if not checks["pf_not_worse_than_actual"]:
            fail_reasons.append("pf_worse_than_actual")
        if not checks["false_positive_rate_reduced"]:
            fail_reasons.append("false_positive_rate_not_reduced")
        if not checks["session_stability"]:
            fail_reasons.append("session_instability")
        if not checks["not_symbol_concentrated"]:
            fail_reasons.append("symbol_concentration")

        guard_pass = all(checks.values())
        return {
            "guard_pass": guard_pass,
            "adopt_ready": guard_pass and not fail_reasons,
            "checks": checks,
            "fail_reasons": fail_reasons,
            "vs_phase344_delta_improvement": round(
                float(metrics.get("delta_yen") or 0) - baseline_delta,
                2,
            ),
        }

    def build_summary(self) -> dict[str, Any]:
        self.note_memory()
        actual_total = round(self.actual_total_pnl_yen_100, 2)
        actual_pf = self.actual_pf()
        baseline = self.phase344_baseline

        cohort_out: dict[str, Any] = {}
        assessments: dict[str, dict[str, Any]] = {}
        for cohort, per_variant in self.cohort_variant.items():
            variants_met: dict[str, Any] = {}
            cohort_assess: dict[str, Any] = {}
            cact = self.cohort_actuals.get(cohort) or _CohortActuals()
            cohort_actual = round(cact.actual_total_pnl_yen_100, 2)
            for vid, bundle in per_variant.items():
                met = self._variant_metrics(vid, bundle, actual_total=cohort_actual)
                variants_met[vid] = met
                if cohort == COHORT_ROBUSTNESS:
                    cohort_assess[vid] = self.guard_pass_assessment(
                        met,
                        actual_pf=cact.actual_pf(),
                        baseline=baseline,
                    )
            cohort_out[cohort] = variants_met
            if cohort_assess:
                assessments = cohort_assess

        robustness = cohort_out.get(COHORT_ROBUSTNESS) or {}
        ranked = sorted(
            robustness.items(),
            key=lambda x: float(x[1].get("tradeoff_score") or 0),
            reverse=True,
        )
        guard_pass_variants = [vid for vid, a in assessments.items() if a.get("guard_pass")]
        best_variant = ranked[0][0] if ranked else None

        return {
            "phase": 346,
            "title": "board_failure_false_positive_guard",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "target_candidate": BOARD_FAILURE_EXIT_ID,
            "base_variant_id": BASE_VARIANT_ID,
            "phase344_baseline": baseline,
            "focus_day_key": FOCUS_DAY_KEY,
            "sessions_evaluated": self.sessions_evaluated,
            "sessions_failed": self.sessions_failed,
            "positions_evaluated": self.positions_evaluated,
            "actual_total_pnl_yen_100": actual_total,
            "actual_pf": actual_pf,
            "actual_stop_hit_count": self.actual_stop_hit_count,
            "cohorts": cohort_out,
            "guard_pass_assessment": assessments,
            "guard_pass_variants": guard_pass_variants,
            "best_variant_by_tradeoff": best_variant,
            "parallel_enabled": self.parallel_enabled,
            "parallel_max_workers": self.parallel_max_workers,
            "wall_runtime_sec": round(self.wall_runtime_sec, 2),
            "peak_memory_mb": self.peak_memory_mb,
            "failed_sessions": self.failed_sessions,
            "note": "Shadow only — false-positive guards; production EXIT unchanged",
        }

    def finalize_outputs(self) -> dict[str, str]:
        paths = self.paths()
        summary = self.build_summary()

        by_variant: list[dict[str, Any]] = []
        focus_rows: list[dict[str, Any]] = []
        for cohort, variants_met in (summary.get("cohorts") or {}).items():
            assess = (summary.get("guard_pass_assessment") or {}) if cohort == COHORT_ROBUSTNESS else {}
            for vid, met in variants_met.items():
                row = {
                    "session_cohort": cohort,
                    **met,
                    **(assess.get(vid) or {}),
                }
                checks = row.pop("checks", {})
                if isinstance(checks, dict):
                    for ck, cv in checks.items():
                        row[f"check_{ck}"] = cv
                by_variant.append(row)
                if met.get("session_delta_yen_20260528") is not None:
                    focus_rows.append(
                        {
                            "variant_id": vid,
                            "session_cohort": cohort,
                            "session_delta_yen_20260528": met.get("session_delta_yen_20260528"),
                            "profit_take_miss_yen_100": met.get("profit_take_miss_yen_100"),
                            "false_positive_rate": met.get("false_positive_rate"),
                            "stop_hit_reduction_count": met.get("stop_hit_reduction_count"),
                            "delta_yen": met.get("delta_yen"),
                        }
                    )

        if by_variant:
            fields = sorted({k for r in by_variant for k in r})
            with paths["by_variant"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                for row in by_variant:
                    w.writerow(row)

        if focus_rows:
            fields = sorted({k for r in focus_rows for k in r})
            with paths["focus_0528"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                for row in focus_rows:
                    w.writerow(row)

        sizes = {k: paths[k].stat().st_size if paths[k].is_file() else 0 for k in paths}
        summary["output_file_sizes_bytes"] = sizes
        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return {k: str(v) for k, v in paths.items()}
